"""
数据清洗流水线 (Data Cleaning Pipeline)

复用现有 DataQualityMonitor + DataGate，新增多源交叉验证。
输出: DataQualityReport — 标的质量评分 0-100，异常标记，缺失统计

设计原则:
- 复用现有基础设施，不重复造轮子
- 多源价格偏离 > 1% 告警
- 数据质量评分 < 阈值 → 阻断后续流水线
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np

from utils.pipeline.config import get_pipeline_config
from utils.pipeline.types import DataQualityReport, PipelineConfig, PipelineResult, PipelineStage

logger = logging.getLogger("pipeline.data_cleaning")

# 尝试导入现有模块（优雅降级）
DataQualityMonitor: Optional[type]
try:
    from utils.data_quality_monitor import DataQualityMonitor as _DQM_impl

    DataQualityMonitor = _DQM_impl
    _HAS_QUALITY_MONITOR = True
except ImportError:
    DataQualityMonitor = None
    _HAS_QUALITY_MONITOR = False

DataGate: Optional[type]
DataGateResult: Optional[type]
try:
    from utils.data_gate import DataGate as _DG_impl
    from utils.data_gate import DataGateResult as _DGR_impl

    DataGate = _DG_impl
    DataGateResult = _DGR_impl
    _HAS_DATA_GATE = True
except ImportError:
    DataGate = None
    DataGateResult = None
    _HAS_DATA_GATE = False


class DataCleaningPipeline:
    """数据清洗流水线

    执行多源数据交叉验证、异常值检测、缺失值填充，
    输出数据质量报告，低质量数据阻断后续处理。
    """

    def __init__(self, config: PipelineConfig | None = None):
        self.config = config or get_pipeline_config()
        self._quality_monitor = DataQualityMonitor() if _HAS_QUALITY_MONITOR else None
        self._data_gate = DataGate() if _HAS_DATA_GATE else None
        self._report_dir = Path(self.config.report_dir)
        self._report_dir.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        market_data: dict[str, Any] | None = None,
        symbols: list[str] | None = None,
        save_report: bool = True,
    ) -> tuple[list[DataQualityReport], PipelineResult]:
        """执行数据清洗流水线

        Args:
            market_data: 可选，市场数据 (dict[symbol, OHLCV])
            symbols: 可选，需要检查的标的列表
            save_report: 是否保存报告到文件

        Returns:
            (reports, result) — 数据质量报告列表 + 流水线执行结果
        """
        started_at = datetime.now()
        logger.info("[数据清洗] 开始执行")

        try:
            if market_data is None:
                market_data = self._load_market_data(symbols)

            if not market_data:
                logger.warning("[数据清洗] 无数据输入")
                return [], PipelineResult(
                    stage=PipelineStage.DATA_CLEANING,
                    success=True,
                    started_at=started_at,
                    completed_at=datetime.now(),
                    metrics={"n_symbols": 0, "n_cleaned": 0},
                )

            # 步骤 1: 多源交叉验证
            if self.config.multi_source_check:
                multi_source_result = self._validate_multi_source(market_data)
            else:
                multi_source_result = {}

            # 步骤 2: 异常值检测 (Z-score + IQR + MAD)
            outlier_result = self._detect_outliers(market_data)

            # 步骤 3: 缺失值填充
            gap_result = self._fill_gaps(market_data)

            # 步骤 4: DataGate 质量门控
            gate_result = self._check_gate(market_data)

            # 生成报告
            reports = []
            total_symbols = len(market_data)
            passed_count = 0

            for symbol in market_data:
                qs = self._calc_quality_score(
                    symbol, multi_source_result, outlier_result, gap_result, gate_result
                )
                report = DataQualityReport(
                    symbol=symbol,
                    quality_score=qs["score"],
                    outlier_flags=outlier_result.get(symbol, []),
                    missing_fields=gap_result.get(symbol, {}).get("missing", []),
                    gap_days=gap_result.get(symbol, {}).get("gap_days", 0),
                    multi_source_deviation_pct=multi_source_result.get(symbol, {}).get("deviation_pct", 0.0),
                    passed=qs["score"] >= self.config.min_quality_score,
                    meta=qs["meta"],
                )
                reports.append(report)
                if report.passed:
                    passed_count += 1

            # 保存报告
            report_paths = []
            if save_report:
                report_paths = self._save_reports(reports)

            duration_ms = (datetime.now() - started_at).total_seconds() * 1000

            result = PipelineResult(
                stage=PipelineStage.DATA_CLEANING,
                success=True,
                started_at=started_at,
                completed_at=datetime.now(),
                duration_ms=duration_ms,
                metrics={
                    "n_symbols": total_symbols,
                    "n_passed": passed_count,
                    "n_failed": total_symbols - passed_count,
                    "avg_quality_score": round(sum(r.quality_score for r in reports) / max(len(reports), 1), 2),
                },
                reports=report_paths,
            )

            logger.info(
                f"[数据清洗] 完成: {passed_count}/{total_symbols} 通过, "
                f"平均质量评分 {result.metrics['avg_quality_score']}"
            )
            return reports, result

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:

            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error(f"[数据清洗] 失败: {e}", exc_info=True)
            return [], PipelineResult(
                stage=PipelineStage.DATA_CLEANING,
                success=False,
                started_at=started_at,
                completed_at=datetime.now(),
                error=str(e),
            )

    # ============================================================
    # 内部方法
    # ============================================================

    def _load_market_data(self, symbols: list[str] | None) -> dict[str, Any]:
        """加载市场数据（复用 DataProvider）"""
        data = {}
        try:
            from utils.data_provider import DataProvider
            provider = DataProvider()
            target_symbols = symbols or self._get_default_symbols()
            for sym in target_symbols:
                try:
                    snapshot = provider.get_realtime_snapshot(sym)
                    if snapshot:
                        data[sym] = snapshot
                except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
                    # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                    continue
        except ImportError:
            logger.warning("[数据清洗] DataProvider 不可用，使用空数据")
        return data

    def _get_default_symbols(self) -> list[str]:
        """获取默认监控标的列表"""
        try:
            from utils.positions_loader import load_positions
            positions = load_positions()
            return [p.get("symbol", "") for p in positions if p.get("symbol")]
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            return ["300308", "002371", "688041", "600900", "601088", "600276", "600519"]

    def _validate_multi_source(self, data: dict) -> dict[str, dict]:
        """多源数据交叉验证

        比较 Wind MCP vs 通达信 vs AKShare vs 新浪 多源价格，计算偏离度。
        """
        result = {}
        for symbol in data:
            try:
                prices = self._fetch_multi_source_prices(symbol)
                if len(prices) < 2:
                    result[symbol] = {"deviation_pct": 0.0, "n_sources": len(prices)}
                    continue
                mean_price = np.mean(list(prices.values()))
                max_dev = max(abs(p - mean_price) / mean_price for p in prices.values())
                result[symbol] = {
                    "deviation_pct": round(max_dev * 100, 4),
                    "n_sources": len(prices),
                    "prices": prices,
                }
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                result[symbol] = {"deviation_pct": 0.0, "n_sources": 0}
        return result

    # 交叉校验数据源: (源标识, MarketDataProvider 上的取数方法名)
    # 说明: 各源均为 MarketDataProvider 实际实现的独立取数通道, 返回 dict 含 index_price 字段。
    _CROSS_CHECK_SOURCES = (
        ("wind_mcp", "_try_wind_mcp_realtime"),
        ("tdx", "_try_tdx_realtime"),
        ("akshare", "_try_akshare_realtime"),
        ("sina", "_try_sina_http_realtime"),
    )

    def _fetch_multi_source_prices(self, symbol: str) -> dict[str, float]:
        """从多数据源获取同一标的的价格 (用于交叉校验)

        逐源独立调用, 单源失败不影响其余源, 便于识别源间偏离。
        """
        prices: dict[str, float] = {}
        try:
            from utils.data_provider import MarketDataProvider
            provider = MarketDataProvider()
        except (ImportError, ValueError, TypeError, AttributeError, RuntimeError, OSError):
            # 数据提供器不可用: 无法交叉校验, 返回空 (上层记 n_sources=0)
            return prices

        for source_name, method_name in self._CROSS_CHECK_SOURCES:
            fetch = getattr(provider, method_name, None)
            if fetch is None:
                continue
            try:
                payload = fetch(symbol)
                if not payload:
                    continue
                price = float(payload.get("index_price", 0) or 0)
                if price > 0:
                    prices[source_name] = price
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
                # 单源取数失败: 格式/类型/字段/属性/运行时/网络/超时, 跳过该源继续校验其余源
                continue
        return prices

    def _detect_outliers(self, data: dict) -> dict[str, list[str]]:
        """异常值检测: Z-score + IQR + MAD 三重检测"""
        flags: dict[str, list[str]] = {}
        for symbol, snapshot in data.items():
            symbol_flags = []
            price = self._extract_price(snapshot)
            if price is None:
                continue

            # 收集历史价格用于检测
            history = self._get_price_history(symbol)
            if not history:
                continue

            prices = np.array(history)
            if len(prices) < 10:
                continue

            # Z-score 检测
            z_score = abs(price - np.mean(prices)) / max(np.std(prices), 1e-10)
            if z_score > self.config.outlier_z_threshold:
                symbol_flags.append(f"z_score={z_score:.2f}")

            # IQR 检测
            q1, q3 = np.percentile(prices, [25, 75])
            iqr = q3 - q1
            if price < q1 - 1.5 * iqr or price > q3 + 1.5 * iqr:
                symbol_flags.append("iqr_outlier")

            # MAD 检测
            median = np.median(prices)
            mad = np.median(np.abs(prices - median))
            if mad > 0:
                mad_score = abs(price - median) / mad
                if mad_score > 3.0:
                    symbol_flags.append(f"mad_score={mad_score:.2f}")

            if symbol_flags:
                flags[symbol] = symbol_flags

        return flags

    def _fill_gaps(self, data: dict) -> dict[str, dict]:
        """缺失值检测与填充"""
        result = {}
        for symbol in data:
            try:
                history = self._get_price_history(symbol)
                if not history:
                    continue

                # 检测缺失值
                prices = np.array(history)
                nan_count = int(np.sum(np.isnan(prices)))
                gap_days = self._detect_gaps(prices)

                result[symbol] = {
                    "missing": [f"nan_count={nan_count}"] if nan_count > 0 else [],
                    "gap_days": gap_days,
                    "n_nan": nan_count,
                }
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                continue
        return result

    def _detect_gaps(self, prices: np.ndarray) -> int:
        """检测连续缺失天数"""
        if len(prices) == 0:
            return 0
        is_nan = np.isnan(prices)
        max_gap = 0
        current = 0
        for v in is_nan:
            if v:
                current += 1
                max_gap = max(max_gap, current)
            else:
                current = 0
        return max_gap

    def _check_gate(self, data: dict) -> dict[str, dict]:
        """DataGate 质量门控"""
        result = {}
        if self._data_gate is None:
            return result
        for symbol, snapshot in data.items():
            try:
                gate_result = self._data_gate.check_and_gate(symbol, snapshot)
                result[symbol] = {
                    "allowed": gate_result.allowed,
                    "quality_score": gate_result.quality_score,
                    "reasons": gate_result.reasons,
                }
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                continue
        return result

    def _calc_quality_score(
        self,
        symbol: str,
        multi_source: dict,
        outlier: dict,
        gap: dict,
        gate: dict,
    ) -> dict:
        """计算综合质量评分 0-100"""
        score = 100.0
        meta = {}

        # 多源偏离扣分
        ms = multi_source.get(symbol, {})
        dev_pct = ms.get("deviation_pct", 0.0)
        if dev_pct > 1.0:
            penalty = min(dev_pct * 5, 30)  # 偏离 1% 扣 5 分，最多扣 30
            score -= penalty
            meta["multi_source_penalty"] = round(penalty, 2)

        # 异常值扣分
        n_outliers = len(outlier.get(symbol, []))
        if n_outliers > 0:
            penalty = n_outliers * 10
            score -= penalty
            meta["outlier_penalty"] = penalty

        # 缺失值扣分
        gap_info = gap.get(symbol, {})
        gap_days = gap_info.get("gap_days", 0)
        if gap_days > 0:
            penalty = min(gap_days * 5, 20)
            score -= penalty
            meta["gap_penalty"] = round(penalty, 2)

        # DataGate 扣分
        gt = gate.get(symbol, {})
        if not gt.get("allowed", True):
            score -= 30
            meta["gate_penalty"] = 30

        return {"score": max(0, round(score, 2)), "meta": meta}

    def _extract_price(self, snapshot: Any) -> float | None:
        """从快照中提取价格"""
        if isinstance(snapshot, dict):
            for key in ("price", "current", "close", "last_price"):
                if key in snapshot:
                    return float(snapshot[key])
        return None

    def _get_price_history(self, symbol: str, days: int = 60) -> list[float]:
        """获取历史价格序列"""
        try:
            from utils.data_provider import DataProvider
            provider = DataProvider()
            df = provider.get_history(symbol, days=days)
            if df is not None and not df.empty:
                col = "close" if "close" in df.columns else (df.columns[-1] if len(df.columns) > 0 else None)
                if col:
                    return df[col].dropna().tolist()
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass
        return []

    def _save_reports(self, reports: list[DataQualityReport]) -> list[str]:
        """保存数据质量报告到文件"""
        paths = []
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # JSON 报告
        json_path = self._report_dir / f"data_quality_{timestamp}.json"
        try:
            import json
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(
                    [r.__dict__ for r in reports],
                    f,
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            paths.append(str(json_path))
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning(f"保存 JSON 报告失败: {e}")

        # CSV 汇总
        csv_path = self._report_dir / f"data_quality_{timestamp}.csv"
        try:
            import csv
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["symbol", "quality_score", "passed", "outlier_flags", "gap_days", "deviation_pct"])
                for r in reports:
                    writer.writerow([
                        r.symbol, r.quality_score, r.passed,
                        ";".join(r.outlier_flags), r.gap_days,
                        r.multi_source_deviation_pct,
                    ])
            paths.append(str(csv_path))
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass

        return paths
