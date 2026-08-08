"""Vibe-Trading 回测引擎桥接器.

模块整合 8.4 — GitHub 热门项目集成 §3.2
Flag: USE_VIBE_BACKTEST_BRIDGE (默认 False, 双签启用)

设计原则:
    1. 只读: 不修改生产持仓, 仅做回测分析
    2. 隔离: 回测在当前进程, 但失败不阻断主流程 (返回降级结果)
    3. 审计: 所有回测结果写入 reports/vibe_backtest/
    4. Flag 透传 (HC-1): USE_VIBE_BACKTEST_BRIDGE=False 时返回空结果
    5. 成本模型: 包含佣金/滑点/印花税 (与生产一致)
    6. 不可变性: 所有返回均为新对象

API:
    from utils.alpha.vibe_backtest_bridge import VibeBacktestBridge, VibeBacktestConfig

    bridge = VibeBacktestBridge(VibeBacktestConfig())
    result = bridge.run_backtest(
        factor_ids=["alpha101_001", "alpha101_002"],
        symbols=["600519.SH", "000001.SZ"],
        start_date="2024-01-01",
        end_date="2024-06-30",
    )
    comparison = bridge.compare_with_baseline(result, baseline_dict)

注意:
    - 本模块为轻量级回测, 使用 Vibe 因子值生成多空信号, 不依赖 Vibe-Trading 完整回测引擎
    - 适用于因子有效性快速验证, 非生产级回测 (生产级用 wt_backtest_engine)
"""

from __future__ import annotations

import json
import logging
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from utils.infra.feature_flags import is_enabled

if TYPE_CHECKING:
    from utils.vibe_trading_adapter import VibeFactorAdapter

logger = logging.getLogger("vibe_backtest_bridge")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_BACKTEST_DIR = _PROJECT_ROOT / "reports" / "vibe_backtest"
_BACKTEST_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# 配置数据类
# ============================================================
@dataclass
class VibeBacktestConfig:
    """Vibe 回测配置.

    Attributes:
        initial_capital: 初始资金 (元)
        commission_bps: 佣金 (万分之, 双边)
        slippage_bps: 滑点 (万分之, 单边)
        stamp_duty_bps: 印花税 (万分之, 卖出单边)
        benchmark: 基准代码
        top_quantile: 多头分位 (0.2 = 前 20%)
        bottom_quantile: 空头分位 (0.2 = 后 20%)
        rebalance_days: 调仓周期 (天)
        audit_enabled: 是否写审计日志
    """

    initial_capital: float = 1_000_000.0
    commission_bps: float = 3.0
    slippage_bps: float = 5.0
    stamp_duty_bps: float = 10.0
    benchmark: str = "510300.SH"
    top_quantile: float = 0.2
    bottom_quantile: float = 0.2
    rebalance_days: int = 5
    audit_enabled: bool = True


# ============================================================
# 回测结果数据类
# ============================================================
@dataclass
class VibeBacktestResult:
    """Vibe 回测结果.

    Attributes:
        status: 状态 (success/failed/disabled)
        factor_ids: 回测的因子列表
        symbols: 回测标的列表
        date_range: 日期范围 (start, end)
        total_return: 总收益率
        annual_return: 年化收益率
        max_drawdown: 最大回撤
        sharpe_ratio: 夏普比率
        win_rate: 胜率
        n_trades: 交易次数
        daily_returns: 每日收益率序列 (Dict[date, return])
        error: 失败原因 (status=failed 时)
    """

    status: str = "disabled"
    factor_ids: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    date_range: tuple[str, str] = ("", "")
    total_return: float = 0.0
    annual_return: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    win_rate: float = 0.0
    n_trades: int = 0
    daily_returns: dict[str, float] = field(default_factory=dict)
    cost_total: float = 0.0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ============================================================
# Vibe 回测桥接器
# ============================================================
class VibeBacktestBridge:
    """Vibe-Trading 回测引擎桥接器.

    安全契约:
        - 只读: 不修改生产持仓
        - 隔离: 失败返回降级结果, 不阻断主流程
        - 审计: 结果写入 reports/vibe_backtest/
    """

    def __init__(self, config: VibeBacktestConfig | None = None) -> None:
        self.config = config or VibeBacktestConfig()
        self._adapter: VibeFactorAdapter | None = None

    @property
    def adapter(self) -> VibeFactorAdapter:
        """延迟获取 VibeFactorAdapter 单例 (避免循环依赖)."""
        if self._adapter is None:
            from utils.vibe_trading_adapter import get_vibe_adapter

            self._adapter = get_vibe_adapter()
        return self._adapter

    # ============================================================
    # 回测主入口
    # ============================================================
    def run_backtest(
        self,
        factor_ids: list[str],
        symbols: list[str],
        start_date: str,
        end_date: str,
        price_data: dict[str, pd.DataFrame] | None = None,
    ) -> VibeBacktestResult:
        """运行 Vibe 因子回测.

        Args:
            factor_ids: Vibe 因子 ID 列表 (如 ["alpha101_001", ...])
            symbols: 标的代码列表
            start_date: 回测开始日期 (YYYY-MM-DD)
            end_date: 回测结束日期 (YYYY-MM-DD)
            price_data: {symbol: DataFrame} 历史价格数据 (None=调用方未提供, 返回 disabled)

        Returns:
            VibeBacktestResult 回测结果
        """
        if not is_enabled("USE_VIBE_BACKTEST_BRIDGE"):
            logger.debug("USE_VIBE_BACKTEST_BRIDGE=False, 跳过回测")
            return VibeBacktestResult(
                status="disabled",
                factor_ids=factor_ids,
                symbols=symbols,
                date_range=(start_date, end_date),
                error="feature flag disabled",
            )

        if not factor_ids or not symbols:
            return VibeBacktestResult(
                status="failed",
                factor_ids=factor_ids,
                symbols=symbols,
                date_range=(start_date, end_date),
                error="factor_ids 或 symbols 为空",
            )

        if price_data is None or not price_data:
            return VibeBacktestResult(
                status="failed",
                factor_ids=factor_ids,
                symbols=symbols,
                date_range=(start_date, end_date),
                error="price_data 未提供, 无法回测",
            )

        try:
            result = self._execute_backtest(factor_ids, symbols, start_date, end_date, price_data)
            if self.config.audit_enabled and result.status == "success":
                self._write_audit(result)
            return result
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # noqa: BLE001  # 回测 fail-safe, 不阻断主流程
            logger.error("Vibe 回测失败: %s", e)
            logger.debug(traceback.format_exc())
            return VibeBacktestResult(
                status="failed",
                factor_ids=factor_ids,
                symbols=symbols,
                date_range=(start_date, end_date),
                error=f"{type(e).__name__}: {e}",
            )

    def _execute_backtest(
        self,
        factor_ids: list[str],
        symbols: list[str],
        start_date: str,
        end_date: str,
        price_data: dict[str, pd.DataFrame],
    ) -> VibeBacktestResult:
        """执行回测核心逻辑."""
        # 1. 计算每个标的每个日期的因子综合得分
        factor_scores = self._compute_factor_scores(factor_ids, symbols, price_data)
        if not factor_scores:
            return VibeBacktestResult(
                status="failed",
                factor_ids=factor_ids,
                symbols=symbols,
                date_range=(start_date, end_date),
                error="因子得分计算失败 (无有效数据)",
            )

        # 2. 生成多空信号 (横截面分组)
        signals = self._generate_signals(factor_scores, symbols)

        # 3. 回测引擎 (简化: 等权多空组合)
        daily_returns, cost_total, n_trades = self._backtest_engine(signals, price_data)

        if not daily_returns:
            return VibeBacktestResult(
                status="failed",
                factor_ids=factor_ids,
                symbols=symbols,
                date_range=(start_date, end_date),
                error="回测引擎未产生任何收益数据",
            )

        # 4. 计算性能指标
        metrics = self._compute_metrics(daily_returns)

        return VibeBacktestResult(
            status="success",
            factor_ids=factor_ids,
            symbols=symbols,
            date_range=(start_date, end_date),
            total_return=metrics["total_return"],
            annual_return=metrics["annual_return"],
            max_drawdown=metrics["max_drawdown"],
            sharpe_ratio=metrics["sharpe_ratio"],
            win_rate=metrics["win_rate"],
            n_trades=n_trades,
            daily_returns={k: round(v, 6) for k, v in daily_returns.items()},
            cost_total=round(cost_total, 2),
        )

    # ============================================================
    # 因子得分计算
    # ============================================================
    def _compute_factor_scores(
        self,
        factor_ids: list[str],
        symbols: list[str],
        price_data: dict[str, pd.DataFrame],
    ) -> dict[str, dict[str, float]]:
        """计算每个日期每个标的的因子综合得分.

        Returns:
            {date_str: {symbol: score}}
        """
        scores: dict[str, dict[str, float]] = {}

        for symbol in symbols:
            df = price_data.get(symbol)
            if df is None or df.empty:
                continue
            try:
                result = self.adapter.compute_single_stock(df, factor_ids=factor_ids)
                if not result.values:
                    continue
                # 综合得分 = 因子值均值 (简单等权)
                composite = float(np.mean(list(result.values.values())))
                for date in df.index:
                    date_str = str(date.date()) if hasattr(date, "date") else str(date)
                    scores.setdefault(date_str, {})[symbol] = composite
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # noqa: BLE001  # 因子计算 fail-safe
                logger.warning("[%s] 因子计算失败: %s", symbol, e)
                continue

        return scores

    # ============================================================
    # 多空信号生成
    # ============================================================
    def _generate_signals(
        self,
        factor_scores: dict[str, dict[str, float]],
        symbols: list[str],
    ) -> dict[str, dict[str, float]]:
        """根据因子得分生成多空信号 (横截面分组).

        Returns:
            {date_str: {symbol: weight}} 正=多头, 负=空头
        """
        signals: dict[str, dict[str, float]] = {}
        n_symbols = len(symbols)

        for date_str, symbol_scores in factor_scores.items():
            if len(symbol_scores) < 2:
                continue

            sorted_items = sorted(symbol_scores.items(), key=lambda x: x[1])
            n_top = max(1, int(n_symbols * self.config.top_quantile))
            n_bottom = max(1, int(n_symbols * self.config.bottom_quantile))

            long_symbols = [s for s, _ in sorted_items[-n_top:]]
            short_symbols = [s for s, _ in sorted_items[:n_bottom]]

            # 等权分配 (多头 +1/n, 空头 -1/n)
            long_weight = 1.0 / max(n_top, 1)
            short_weight = -1.0 / max(n_bottom, 1)

            day_signals: dict[str, float] = {}
            for s in long_symbols:
                day_signals[s] = long_weight
            for s in short_symbols:
                day_signals[s] = short_weight
            signals[date_str] = day_signals

        return signals

    # ============================================================
    # 回测引擎 (简化)
    # ============================================================
    def _backtest_engine(
        self,
        signals: dict[str, dict[str, float]],
        price_data: dict[str, pd.DataFrame],
    ) -> tuple[dict[str, float], float, int]:
        """简化回测引擎: 基于信号计算每日收益.

        成本模型: 佣金 (双边) + 滑点 (单边) + 印花税 (卖出)
        """
        daily_returns: dict[str, float] = {}
        total_cost = 0.0
        n_trades = 0
        prev_signals: dict[str, float] = {}

        sorted_dates = sorted(signals.keys())
        for date_str in sorted_dates:
            day_signals = signals[date_str]
            day_return = 0.0

            for symbol, weight in day_signals.items():
                df = price_data.get(symbol)
                if df is None or df.empty:
                    continue
                # 获取当日收益率
                ret = self._get_daily_return(df, date_str)
                if ret is None:
                    continue
                day_return += weight * ret

                # 计算换手成本 (与前一交易日信号差异)
                prev_weight = prev_signals.get(symbol, 0.0)
                turnover = abs(weight - prev_weight)
                if turnover > 1e-6:
                    cost_bps = self.config.commission_bps + self.config.slippage_bps
                    if weight < prev_weight:  # 卖出
                        cost_bps += self.config.stamp_duty_bps
                    total_cost += turnover * abs(weight) * self.config.initial_capital * cost_bps / 10000
                    n_trades += 1

            daily_returns[date_str] = day_return
            prev_signals = day_signals.copy()

        return daily_returns, total_cost, n_trades

    @staticmethod
    def _get_daily_return(df: pd.DataFrame, date_str: str) -> float | None:
        """获取 DataFrame 中指定日期的收益率."""
        try:
            if "close" not in df.columns:
                return None
            idx = df.index
            if isinstance(idx, pd.DatetimeIndex):
                target_date = pd.to_datetime(date_str)
                if target_date not in idx:
                    return None
                pos = idx.get_loc(target_date)
                if pos == 0:
                    return 0.0
                prev_close = float(df["close"].iloc[pos - 1])
                curr_close = float(df["close"].iloc[pos])
                if prev_close <= 0:
                    return 0.0
                return (curr_close - prev_close) / prev_close
            return None
        except (KeyError, ValueError, IndexError):
            return None

    # ============================================================
    # 性能指标计算
    # ============================================================
    def _compute_metrics(self, daily_returns: dict[str, float]) -> dict[str, float]:
        """计算回测性能指标."""
        returns = list(daily_returns.values())
        if not returns:
            return {
                "total_return": 0.0,
                "annual_return": 0.0,
                "max_drawdown": 0.0,
                "sharpe_ratio": 0.0,
                "win_rate": 0.0,
            }

        returns_arr = np.array(returns, dtype=float)
        total_return = float(np.sum(returns_arr))
        n_days = len(returns_arr)
        annual_return = total_return * (252.0 / max(n_days, 1))

        # 最大回撤
        cumulative = np.cumsum(returns_arr)
        running_max = np.maximum.accumulate(cumulative)
        drawdowns = cumulative - running_max
        max_drawdown = float(np.min(drawdowns)) if len(drawdowns) > 0 else 0.0

        # 夏普比率 (假设无风险利率 0, 日化)
        std = float(np.std(returns_arr, ddof=1)) if n_days > 1 else 0.0
        sharpe = float(np.mean(returns_arr) / std * np.sqrt(252)) if std > 0 else 0.0

        # 胜率
        win_rate = float(np.mean(returns_arr > 0))

        return {
            "total_return": round(total_return, 6),
            "annual_return": round(annual_return, 6),
            "max_drawdown": round(max_drawdown, 6),
            "sharpe_ratio": round(sharpe, 4),
            "win_rate": round(win_rate, 4),
        }

    # ============================================================
    # Baseline 对比
    # ============================================================
    def compare_with_baseline(
        self,
        vibe_result: VibeBacktestResult,
        baseline: dict[str, Any],
    ) -> dict[str, float]:
        """对比 Vibe 回测结果与 baseline.

        Args:
            vibe_result: Vibe 回测结果
            baseline: baseline 指标字典 (如 {"total_return": 0.1, "sharpe_ratio": 1.2, ...})

        Returns:
            对比字典 (差异 + 偏差率)
        """
        if vibe_result.status != "success":
            return {"status": "vibe_not_success", "deviation_pct": float("inf")}

        metrics = ["total_return", "annual_return", "max_drawdown", "sharpe_ratio", "win_rate"]
        comparison: dict[str, float] = {}

        for metric in metrics:
            vibe_val = getattr(vibe_result, metric, 0.0)
            base_val = float(baseline.get(metric, 0.0))
            diff = vibe_val - base_val
            deviation_pct = abs(diff) / max(abs(base_val), 1e-8) * 100
            comparison[f"vibe_{metric}"] = round(vibe_val, 6)
            comparison[f"baseline_{metric}"] = round(base_val, 6)
            comparison[f"diff_{metric}"] = round(diff, 6)
            comparison[f"deviation_pct_{metric}"] = round(deviation_pct, 2)

        # 总偏差 (用于 5% 阈值判断)
        total_deviation = (
            sum(
                abs(getattr(vibe_result, m, 0.0) - float(baseline.get(m, 0.0)))
                / max(abs(float(baseline.get(m, 0.0))), 1e-8)
                for m in metrics
            )
            / len(metrics)
            * 100
        )
        comparison["total_deviation_pct"] = round(total_deviation, 2)
        comparison["within_threshold"] = 1.0 if total_deviation < 5.0 else 0.0

        return comparison

    # ============================================================
    # 审计
    # ============================================================
    def _write_audit(self, result: VibeBacktestResult) -> None:
        """写回测审计日志 (JSON 格式)."""
        try:
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            audit_file = _BACKTEST_DIR / f"backtest_{timestamp}.json"
            with open(audit_file, "w", encoding="utf-8") as f:
                json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.warning("写回测审计日志失败: %s", e)

    # ============================================================
    # 健康检查
    # ============================================================
    def get_health(self) -> dict[str, Any]:
        """返回桥接器健康状态."""
        try:
            adapter_health = self.adapter.health
            return {
                "flag_enabled": is_enabled("USE_VIBE_BACKTEST_BRIDGE"),
                "adapter_loaded": adapter_health.get("loaded", 0),
                "adapter_failed": adapter_health.get("failed", 0),
                "benchmark": self.config.benchmark,
            }
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # noqa: BLE001  # 健康检查 fail-safe
            return {"flag_enabled": is_enabled("USE_VIBE_BACKTEST_BRIDGE"), "error": str(e)}
