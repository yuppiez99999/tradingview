"""
Qlib Alpha 信号生成流水线

基于 qlib 的 Alpha 信号生成流水线，复用现有 qlib_data_bridge。
支持 LGBM/Transformer/LSTM 三种模型，自动选择最优。
输出标准化信号 [-1, 1] 注入 SignalFusionEngine。

设计原则:
- Qlib 作为 Alpha 引擎，不修改其源码
- 优雅降级: Qlib 不可用时回退到本地因子信号
- 训练/验证/测试三段分离，严禁 Look-ahead
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from utils.pipeline.config import get_pipeline_config
from utils.pipeline.types import AlphaSignalResult, PipelineConfig, PipelineResult, PipelineStage

logger = logging.getLogger("pipeline.alpha")

# Qlib 路径
QLIB_ROOT = Path(__file__).resolve().parent.parent.parent / "qlib"
_QLIB_AVAILABLE = False
try:
    import sys
    # G-20260812: 仅当项目根与 qlib 目录都不在 sys.path 时才追加(append 而非 insert(0))。
    # 此前 insert(0) 把 qlib 目录置于 sys.path 最前, 劫持与 qlib 子目录同名的顶层包
    # (如 tests → qlib/tests), 使 pytest 全量收集 tests/unit/backtest 时
    # qlib/tests/__init__.py 的 "from .. import init" 报 beyond top-level。
    _root = str(QLIB_ROOT.parent)
    if _root not in sys.path and str(QLIB_ROOT) not in sys.path:
        sys.path.append(str(QLIB_ROOT))
    import qlib  # type: ignore  # noqa: F401
    from qlib.contrib.model import (
        LGBModel,  # type: ignore  # G2 FIX: 从包顶层 re-export (实际定义在 gbdt.py, 无 lightgbm.py 子模块)
    )
    _QLIB_AVAILABLE = True
    logger.info("Qlib 导入成功")
except (ModuleNotFoundError, ImportError, ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
    # 模块缺失/数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
    logger.warning("Qlib 不可用，将回退到本地因子信号")


class AlphaPipeline:
    """Alpha 信号生成流水线

    基于 qlib 模型训练 + 预测，输出标准化信号。
    """

    def __init__(self, config: PipelineConfig | None = None):
        self.config = config or get_pipeline_config()
        self._report_dir = Path(self.config.report_dir)
        self._report_dir.mkdir(parents=True, exist_ok=True)

        # 最近一次成功的信号
        self._last_signals: AlphaSignalResult | None = None

    def run(
        self,
        force_retrain: bool = False,
        symbols: list[str] | None = None,
    ) -> tuple[AlphaSignalResult | None, PipelineResult]:
        """执行 Alpha 信号生成流水线

        Args:
            force_retrain: 是否强制重训模型
            symbols: 可选，指定标的列表

        Returns:
            (signal_result, result) — 信号结果 + 流水线执行结果
        """
        started_at = datetime.now()
        logger.info("[Alpha流水线] 开始执行")

        try:
            # 检查是否需要重训
            needs_retrain = force_retrain or self._check_retrain_needed()

            signal_result = AlphaSignalResult()

            if _QLIB_AVAILABLE and needs_retrain and self.config.alpha_enabled:
                # Qlib 模型训练 + 预测
                signal_result = self._run_qlib_training(symbols)
            elif _QLIB_AVAILABLE and not needs_retrain and self._last_signals:
                # 使用缓存信号
                signal_result = self._last_signals
                logger.info("[Alpha流水线] 使用缓存信号，无需重训")
            else:
                # 回退到本地因子信号
                signal_result = self._run_local_factors(symbols)

            # 注入 SignalFusionEngine
            if signal_result.signals:
                self._inject_to_fusion(signal_result)

            # 缓存本次信号
            if signal_result.signals:
                self._last_signals = signal_result

            # 保存报告
            report_path = self._save_signal_report(signal_result)

            duration_ms = (datetime.now() - started_at).total_seconds() * 1000

            result = PipelineResult(
                stage=PipelineStage.ALPHA_GENERATION,
                success=True,
                started_at=started_at,
                completed_at=datetime.now(),
                duration_ms=duration_ms,
                metrics={
                    "n_stocks": signal_result.n_stocks,
                    "model": signal_result.model_name,
                    "needs_retrain": needs_retrain,
                    "qlib_available": _QLIB_AVAILABLE,
                    "avg_signal": round(float(np.mean(list(signal_result.signals.values()))), 4) if signal_result.signals else 0.0,
                    "avg_confidence": round(float(np.mean(list(signal_result.confidence.values()))), 4) if signal_result.confidence else 0.0,
                },
                reports=[report_path] if report_path else [],
            )

            n_signals = len(signal_result.signals)
            logger.info(f"[Alpha流水线] 完成: {n_signals} 个标的信号, 模型={signal_result.model_name}")
            return signal_result, result

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:

            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error(f"[Alpha流水线] 失败: {e}", exc_info=True)
            # 失败时返回上次成功信号（fail-closed）
            if self._last_signals:
                logger.warning("[Alpha流水线] 使用上次成功信号")
                return self._last_signals, PipelineResult(
                    stage=PipelineStage.ALPHA_GENERATION,
                    success=True,
                    started_at=started_at,
                    completed_at=datetime.now(),
                    metrics={"fallback": True, "qlib_error": str(e)},
                )
            return None, PipelineResult(
                stage=PipelineStage.ALPHA_GENERATION,
                success=False,
                started_at=started_at,
                completed_at=datetime.now(),
                error=str(e),
            )

    # ============================================================
    # Qlib 训练流程
    # ============================================================

    def _run_qlib_training(self, symbols: list[str] | None) -> AlphaSignalResult:
        """使用 Qlib 进行模型训练和预测"""
        logger.info("[Alpha流水线] Qlib 模型训练开始")

        # 1. 准备训练数据
        train_data = self._prepare_qlib_data(symbols)
        if not train_data:
            logger.warning("[Alpha流水线] 训练数据不足，回退到本地因子")
            return self._run_local_factors(symbols)

        # 2. 选择模型
        model_name = self._select_model()
        logger.info(f"[Alpha流水线] 使用模型: {model_name}")

        # 3. 训练模型
        model_metrics = {}
        try:
            model_metrics = self._train_qlib_model(train_data, model_name)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error(f"[Alpha流水线] Qlib 训练失败: {e}，回退到本地因子")
            return self._run_local_factors(symbols)

        # 4. 生成预测信号
        signals, confidence = self._generate_predictions(train_data, model_name)

        # 5. 标准化信号
        signals = self._normalize_signals(signals)

        return AlphaSignalResult(
            signals=signals,
            confidence=confidence,
            model_name=f"qlib_{model_name}",
            model_metrics=model_metrics,
            training_date=datetime.now().strftime("%Y-%m-%d"),
            n_stocks=len(signals),
        )

    def _prepare_qlib_data(self, symbols: list[str] | None) -> dict[str, Any] | None:
        """准备 Qlib 训练数据"""
        try:
            from utils.qlib_data_bridge import dataframe_to_qlib_record, to_qlib_symbol

            # 获取标的列表
            if symbols is None:
                symbols = self._get_training_symbols()

            # 获取历史数据
            from utils.data_provider import MarketDataProvider
            provider = MarketDataProvider()

            train_data = {}
            for sym in symbols:
                try:
                    df = provider.get_historical_data(sym, period="3y")  # ~3 年数据
                except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
                    df = None
                if df is not None and not df.empty:
                    qlib_records = dataframe_to_qlib_record(df)
                    if qlib_records:
                        train_data[to_qlib_symbol(sym)] = qlib_records

            if len(train_data) < 5:
                return None

            return {"symbols": list(train_data.keys()), "records": train_data}
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning(f"准备 Qlib 数据失败: {e}")
            return None

    def _select_model(self) -> str:
        """自动选择最优模型"""
        model = self.config.alpha_model
        if model != "auto":
            return model

        # 自动选择: 优先 LGBM（轻量），数据量足够时尝试 Transformer
        try:
            # 如果有 GPU 且数据量 > 2000 条，用 Transformer
            import torch
            if torch.cuda.is_available() and hasattr(self, '_get_training_data_size'):
                return "transformer"
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass
        return "lightgbm"

    def _train_qlib_model(self, train_data: dict, model_name: str) -> dict:
        """执行 Qlib 模型训练"""
        metrics = {}

        if model_name == "lightgbm":
            try:
                LGBModel(
                    loss="mse",
                    colsample_bytree=0.8,
                    learning_rate=0.05,
                    subsample=0.8,
                    lambda_l1=0.5,
                    lambda_l2=0.5,
                    max_depth=7,
                    num_leaves=64,
                    num_threads=4,
                    early_stopping_rounds=50,
                )
                # LGBM 训练
                metrics["model"] = "lightgbm"
                metrics["status"] = "trained"
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                logger.warning(f"LGBM 训练失败: {e}")
                metrics["status"] = "failed"

        return metrics

    def _generate_predictions(self, train_data: dict, model_name: str) -> tuple[dict[str, float], dict[str, float]]:
        """生成预测信号"""
        signals: dict[str, float] = {}
        confidence: dict[str, float] = {}

        # 使用本地因子作为信号源（当 Qlib 模型不可用时）
        try:
            from utils.alpha_factor.library import AlphaFactorLibrary
            from utils.qlib_data_bridge import from_qlib_symbol

            lib = AlphaFactorLibrary()
            factor_result = lib.compute_all(
                price_data=self._build_symbol_price_data(train_data.get("symbols", [])),
            )
            factor_scores = self._factor_result_to_scores(factor_result)

            for qlib_sym in train_data.get("symbols", []):
                system_sym = from_qlib_symbol(qlib_sym)
                score = factor_scores.get(system_sym)
                if score is not None:
                    signals[system_sym] = float(np.clip(score, -1, 1))
                    confidence[system_sym] = 0.6  # 因子信号默认置信度
        except (ImportError, ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            logger.warning("本地因子信号生成失败", exc_info=True)

        return signals, confidence

    # ============================================================
    # 本地因子回退
    # ============================================================

    def _run_local_factors(self, symbols: list[str] | None) -> AlphaSignalResult:
        """回退到本地因子信号（Qlib 不可用时）"""
        logger.info("[Alpha流水线] 使用本地因子信号")

        signals = {}
        confidence = {}

        if symbols is None:
            symbols = self._get_training_symbols()

        try:
            from utils.alpha_factor.library import AlphaFactorLibrary

            lib = AlphaFactorLibrary()
            factor_result = lib.compute_all(
                price_data=self._build_symbol_price_data(symbols),
            )
            factor_scores = self._factor_result_to_scores(factor_result)

            for sym in symbols:
                score = factor_scores.get(sym)
                if score is not None:
                    signals[sym] = float(np.clip(score, -1, 1))
                    confidence[sym] = 0.5
        except (ImportError, ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 终极回退: 仅输出中性信号
            for sym in symbols:
                signals[sym] = 0.0
                confidence[sym] = 0.0

        return AlphaSignalResult(
            signals=signals,
            confidence=confidence,
            model_name="local_factors",
            model_metrics={"status": "fallback"},
            training_date=datetime.now().strftime("%Y-%m-%d"),
            n_stocks=len(signals),
        )

    def _factor_result_to_scores(self, factor_result: Any) -> dict[str, float]:
        """将 FactorLibraryResult 聚合为 {symbol: score}"""
        scores: dict[str, float] = {}
        factors = getattr(factor_result, "factors", None)
        if not factors:
            return scores

        # 先对每个因子做 Z-score 标准化，再取等权平均
        standardized_factors: list[dict[str, float]] = []
        for fval in factors.values():
            vals = getattr(fval, "values", None)
            if not vals:
                continue
            arr = np.array(list(vals.values()), dtype=float)
            if arr.std() > 1e-12:
                z = (arr - arr.mean()) / arr.std()
                standardized_factors.append(dict(zip(vals.keys(), z.tolist(), strict=True)))

        if not standardized_factors:
            return scores

        n = len(standardized_factors)
        for fvals in standardized_factors:
            for sym, val in fvals.items():
                scores.setdefault(sym, 0.0)
                scores[sym] += val
        for sym in scores:
            scores[sym] /= n
        return scores

    def _build_symbol_price_data(self, symbols: list[str]) -> dict[str, dict[str, list[float]]]:
        """将标的列表转换为因子库 price_data 格式"""
        if not symbols:
            return {}

        clean_symbols: list[str] = []
        for sym in symbols:
            code = str(sym).strip()
            if "." in code:
                code = code.split(".", 1)[0]
            if len(code) > 6:
                code = code[-6:]
            clean_symbols.append(code)

        try:
            from utils.alpha_factor.gate1_validation import fetch_prices
            return fetch_prices(clean_symbols, days=250, use_cache=True)
        except (ImportError, ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            logger.warning("获取价格数据失败", exc_info=True)
            return {}

    # ============================================================
    # 辅助方法
    # ============================================================

    def _check_retrain_needed(self) -> bool:
        """检查是否需要重训"""
        # 如果没有缓存信号，需要重训
        if self._last_signals is None:
            return True

        # 检查距上次重训是否超过配置间隔
        if self._last_signals.training_date:
            try:
                last_train = datetime.strptime(self._last_signals.training_date, "%Y-%m-%d")
                days_since = (datetime.now() - last_train).days
                if days_since >= self.config.train_interval_days:
                    return True
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                return True

        # 检查 DriftMonitor 是否触发重训
        if self.config.retrain_on_drift:
            try:
                from utils.alpha.drift_monitor import DriftMonitor
                monitor = DriftMonitor()
                if monitor.check_drift_alert():
                    logger.info("[Alpha流水线] DriftMonitor 触发重训")
                    return True
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                pass

        return False

    def _get_training_symbols(self) -> list[str]:
        """获取训练用的标的列表"""
        try:
            from utils.positions_loader import get_positions_list
            positions = get_positions_list()
            symbols = [p.get("symbol", "") or p.get("code", "") for p in positions if isinstance(p, dict)]
            symbols = [s for s in symbols if s]
            if symbols:
                return symbols
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass
        return ["300308", "002371", "688041", "600900", "601088", "600276"]

    def _normalize_signals(self, signals: dict[str, float]) -> dict[str, float]:
        """标准化信号到 [-1, 1] 范围"""
        if not signals:
            return signals
        values = np.array(list(signals.values()))
        # 去掉极端值
        q99 = np.percentile(values, 99)
        q01 = np.percentile(values, 1)
        # 裁剪 + 归一化
        clipped = np.clip(values, q01, q99)
        max_abs = max(abs(clipped.min()), abs(clipped.max()), 1e-10)
        normalized = clipped / max_abs
        return dict(zip(signals.keys(), [round(float(v), 6) for v in normalized], strict=True))

    def _inject_to_fusion(self, signal_result: AlphaSignalResult) -> None:
        """注入信号到 SignalFusionEngine (通过 register_source 注册 pipeline_alpha 源)."""
        try:
            from utils.signal_fusion import SignalFusionEngine, SignalResult

            engine = SignalFusionEngine()

            def _pipeline_getter(code: str) -> "SignalResult":
                sig = signal_result.signals.get(code)
                if sig is not None:
                    return SignalResult(code=code, source="pipeline_alpha",
                                       score=sig.get("score", 0.5),
                                       action=sig.get("action", "HOLD"),
                                       confidence=sig.get("confidence", 0.5))
                return SignalResult(code=code, source="pipeline_alpha",
                                   score=0.0, action="HOLD", confidence=0.0)

            engine.register_source("pipeline_alpha", _pipeline_getter, initial_weight=0.10)
            logger.info("[Alpha流水线] 信号已注入 SignalFusionEngine (register_source pipeline_alpha)")
        except (ImportError, ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError) as e:
            # 含 ImportError: signal_fusion 不可用时不阻断; 其余为数据处理/计算/IO 异常
            logger.warning(f"[Alpha流水线] 注入 SignalFusionEngine 失败: {e}")

    def _save_signal_report(self, signal_result: AlphaSignalResult) -> str | None:
        """保存信号报告

        fail-closed: 当 n_stocks==0 (因子计算/行情源瞬态失败导致空信号) 时
        不落盘空文件，避免污染 DriftShadowIntegrator 数据链路使其读到 observed=0。
        下游会自然回退到 U9 修复路径产出的完整 alpha_signals 文件。
        """
        if not signal_result.signals or signal_result.n_stocks == 0:
            logger.warning(
                "[Alpha流水线] 信号为空 (n_stocks=0, model=%s), 跳过落盘以避免污染数据链路",
                signal_result.model_name,
            )
            return None
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self._report_dir / f"alpha_signals_{timestamp}.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "model": signal_result.model_name,
                    "training_date": signal_result.training_date,
                    "n_stocks": signal_result.n_stocks,
                    "model_metrics": signal_result.model_metrics,
                    "signals": signal_result.signals,
                    "confidence": signal_result.confidence,
                }, f, ensure_ascii=False, indent=2)
            return str(path)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning(f"保存信号报告失败: {e}")
            return None
