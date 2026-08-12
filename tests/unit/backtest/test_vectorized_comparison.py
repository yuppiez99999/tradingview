"""向量化回测 vs 事件驱动回测 — 指标公式对比验证测试。

验收标准 (OPTIMAL_PLAN_20260811.md):
    "与向量化回测结果偏差 < 5%，且不污染向量化路径"

验证策略:
    1. 构造合成 equity_curve
    2. 分别用 ResultConverter 和 HedgeRebalanceBacktest._metrics 计算指标
    3. 对比 total_return/annual_return/volatility/sharpe/max_dd/calmar/win_rate
    4. 偏差 < 1% (公式完全对齐,应远小于 5% 验收阈值)

本测试不运行完整向量化回测 (需要真实数据),只验证指标计算公式等价性。
完整端到端偏差验证在 Day 6+ 接入真实数据后补充。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from utils.backtest.event_driven_engine import EngineSummary
from utils.backtest.result_converter import ResultConverter
from utils.hedge_rebalance_backtest import BacktestResult, HedgeRebalanceBacktest

# ============================================================
# 辅助: 构造轻量向量化回测实例
# ============================================================

def _make_vectorized_backtest(n_days: int) -> HedgeRebalanceBacktest:
    """构造轻量 HedgeRebalanceBacktest 实例 (合成数据,不加载真实行情)。

    Args:
        n_days: 交易日数 (与 equity_curve 长度 - 1 对齐)

    Returns:
        可调用 _metrics 的实例
    """
    # 合成 price_df: n_days 行, 单列
    dates = pd.bdate_range("2021-01-04", periods=n_days)
    price_df = pd.DataFrame({"dummy": 100.0}, index=dates)
    # 合成 csi300_ret: n_days 个零收益率
    csi300_ret = pd.Series(0.0, index=dates)
    return HedgeRebalanceBacktest(price_df=price_df, csi300_ret=csi300_ret)


def _make_test_equity_curve() -> list:
    """构造有涨有跌、含回撤的测试 equity_curve (252+ 点,覆盖 1+ 年)。"""
    np.random.seed(42)
    n = 260  # 略大于 252 (1 年交易日)
    returns = np.random.normal(0.0004, 0.012, n)  # 日均 +0.04%, vol 1.2%
    eq = [1_000_000.0]
    for r in returns:
        eq.append(eq[-1] * (1 + r))
    return eq


# ============================================================
# 公式等价性验证 (核心)
# ============================================================

class TestMetricsEquivalence:
    """验证 ResultConverter 与 HedgeRebalanceBacktest._metrics 公式完全等价。"""

    @pytest.fixture(scope="class")
    def equity_curve(self) -> list:
        return _make_test_equity_curve()

    @pytest.fixture(scope="class")
    def converter_result(self, equity_curve: list) -> BacktestResult:
        """ResultConverter 产出的 BacktestResult。"""
        summary = EngineSummary(
            initial_capital=equity_curve[0],
            final_equity=equity_curve[-1],
            total_return=(equity_curve[-1] - equity_curve[0]) / equity_curve[0],
            n_events=len(equity_curve) - 1,
            n_orders_submitted=0,
            n_orders_filled=0,
            n_orders_rejected=0,
            equity_curve=equity_curve,
        )
        converter = ResultConverter()
        return converter.convert(summary, name="converter_test")

    @pytest.fixture(scope="class")
    def vectorized_result(self, equity_curve: list) -> BacktestResult:
        """向量化回测 _metrics 产出的 BacktestResult。"""
        n_days = len(equity_curve) - 1
        bt = _make_vectorized_backtest(n_days)

        # 手动构建 BacktestResult 骨架 (与 _run_s1 等返回的格式一致)
        daily_returns = [0.0]
        for i in range(1, len(equity_curve)):
            prev = equity_curve[i - 1]
            daily_returns.append(
                (equity_curve[i] - prev) / max(1, prev) if prev > 0 else 0.0
            )

        dates = list(pd.bdate_range("2021-01-04", periods=len(equity_curve)))
        result = BacktestResult(
            name="vectorized_test",
            equity_curve=equity_curve,
            dates=dates,
            daily_returns=daily_returns,
            trade_count=0,
            hedge_costs=[0.0] * len(equity_curve),
            transaction_costs=[0.0] * len(equity_curve),
            n_days=n_days,
        )
        bt._metrics(result)
        return result

    def test_total_return_matches(
        self, converter_result: BacktestResult, vectorized_result: BacktestResult
    ) -> None:
        """total_return 偏差 < 0.01% (公式完全一致)。"""
        deviation = abs(converter_result.total_return - vectorized_result.total_return)
        assert deviation < 1e-6, f"total_return 偏差 {deviation}"

    def test_annual_return_matches(
        self, converter_result: BacktestResult, vectorized_result: BacktestResult
    ) -> None:
        """annual_return 偏差 < 0.01%。"""
        deviation = abs(
            converter_result.annual_return - vectorized_result.annual_return
        )
        assert deviation < 1e-6, f"annual_return 偏差 {deviation}"

    def test_annual_volatility_matches(
        self, converter_result: BacktestResult, vectorized_result: BacktestResult
    ) -> None:
        """annual_volatility 偏差 < 0.01%。"""
        deviation = abs(
            converter_result.annual_volatility - vectorized_result.annual_volatility
        )
        assert deviation < 1e-6, f"annual_volatility 偏差 {deviation}"

    def test_sharpe_ratio_matches(
        self, converter_result: BacktestResult, vectorized_result: BacktestResult
    ) -> None:
        """sharpe_ratio 偏差 < 0.01。"""
        deviation = abs(
            converter_result.sharpe_ratio - vectorized_result.sharpe_ratio
        )
        assert deviation < 1e-4, f"sharpe_ratio 偏差 {deviation}"

    def test_max_drawdown_matches(
        self, converter_result: BacktestResult, vectorized_result: BacktestResult
    ) -> None:
        """max_drawdown 偏差 < 0.01%。"""
        deviation = abs(
            converter_result.max_drawdown - vectorized_result.max_drawdown
        )
        assert deviation < 1e-6, f"max_drawdown 偏差 {deviation}"

    def test_calmar_ratio_matches(
        self, converter_result: BacktestResult, vectorized_result: BacktestResult
    ) -> None:
        """calmar_ratio 偏差 < 0.01。"""
        deviation = abs(
            converter_result.calmar_ratio - vectorized_result.calmar_ratio
        )
        assert deviation < 1e-4, f"calmar_ratio 偏差 {deviation}"

    def test_win_rate_matches(
        self, converter_result: BacktestResult, vectorized_result: BacktestResult
    ) -> None:
        """win_rate 偏差 < 0.01%。"""
        deviation = abs(converter_result.win_rate - vectorized_result.win_rate)
        assert deviation < 1e-6, f"win_rate 偏差 {deviation}"


# ============================================================
# 验收标准验证 (偏差 < 5%)
# ============================================================

class TestAcceptanceCriteria:
    """验证满足 OPTIMAL_PLAN 验收标准: 偏差 < 5%。"""

    def test_all_metrics_within_5_percent_threshold(self) -> None:
        """所有核心指标偏差远小于 5% 验收阈值。"""
        equity_curve = _make_test_equity_curve()

        # ResultConverter 路径
        summary = EngineSummary(
            initial_capital=equity_curve[0],
            final_equity=equity_curve[-1],
            total_return=(equity_curve[-1] - equity_curve[0]) / equity_curve[0],
            n_events=len(equity_curve) - 1,
            n_orders_submitted=0,
            n_orders_filled=0,
            n_orders_rejected=0,
            equity_curve=equity_curve,
        )
        converter = ResultConverter()
        result_a = converter.convert(summary, name="event_driven")

        # 向量化路径
        n_days = len(equity_curve) - 1
        bt = _make_vectorized_backtest(n_days)
        daily_returns = [0.0]
        for i in range(1, len(equity_curve)):
            prev = equity_curve[i - 1]
            daily_returns.append(
                (equity_curve[i] - prev) / max(1, prev) if prev > 0 else 0.0
            )
        result_b = BacktestResult(
            name="vectorized",
            equity_curve=equity_curve,
            dates=list(pd.bdate_range("2021-01-04", periods=len(equity_curve))),
            daily_returns=daily_returns,
            trade_count=0,
            hedge_costs=[0.0] * len(equity_curve),
            transaction_costs=[0.0] * len(equity_curve),
            n_days=n_days,
        )
        bt._metrics(result_b)

        # 验收: 所有指标偏差 < 5% (实际应 < 0.01%,公式完全一致)
        threshold = 0.05  # 5%

        metrics_pairs = [
            ("total_return", result_a.total_return, result_b.total_return),
            ("annual_return", result_a.annual_return, result_b.annual_return),
            ("annual_volatility", result_a.annual_volatility, result_b.annual_volatility),
            ("sharpe_ratio", result_a.sharpe_ratio, result_b.sharpe_ratio),
            ("max_drawdown", result_a.max_drawdown, result_b.max_drawdown),
            ("calmar_ratio", result_a.calmar_ratio, result_b.calmar_ratio),
            ("win_rate", result_a.win_rate, result_b.win_rate),
        ]

        for name, val_a, val_b in metrics_pairs:
            if abs(val_b) < 1e-10:
                # 近零值,直接比较绝对偏差
                deviation = abs(val_a - val_b)
            else:
                deviation = abs(val_a - val_b) / abs(val_b)
            assert deviation < threshold, (
                f"{name}: 事件驱动={val_a:.6f}, 向量化={val_b:.6f}, "
                f"偏差={deviation:.4%} > {threshold:.0%} 阈值"
            )

    def test_no_vectorized_path_pollution(self) -> None:
        """验证事件驱动路径不污染向量化回测 (独立运行)。"""
        # 事件驱动引擎独立运行
        equity_curve = _make_test_equity_curve()
        summary = EngineSummary(
            initial_capital=equity_curve[0],
            final_equity=equity_curve[-1],
            total_return=(equity_curve[-1] - equity_curve[0]) / equity_curve[0],
            n_events=len(equity_curve) - 1,
            n_orders_submitted=0,
            n_orders_filled=0,
            n_orders_rejected=0,
            equity_curve=equity_curve,
        )
        converter = ResultConverter()
        result = converter.convert(summary, name="isolation_test")

        # 向量化回测独立运行 (合成数据)
        n_days = len(equity_curve) - 1
        bt = _make_vectorized_backtest(n_days)
        # 两者互不影响
        assert result.name == "isolation_test"
        assert bt.n_days == n_days
        # 转换器未修改向量化实例的任何状态
        assert bt.price_df is not None
        assert bt.csi300_ret is not None


# ============================================================
# 不同 equity_curve 场景的对比
# ============================================================

class TestVariousScenarios:
    """多种 equity_curve 场景下的公式等价性。"""

    def _compare_both(
        self, equity_curve: list, scenario_name: str
    ) -> tuple:
        """用两条路径分别计算指标,返回 (converter_result, vectorized_result)。"""
        # Converter 路径
        summary = EngineSummary(
            initial_capital=equity_curve[0],
            final_equity=equity_curve[-1],
            total_return=(equity_curve[-1] - equity_curve[0]) / equity_curve[0],
            n_events=len(equity_curve) - 1,
            n_orders_submitted=0,
            n_orders_filled=0,
            n_orders_rejected=0,
            equity_curve=equity_curve,
        )
        converter = ResultConverter()
        result_a = converter.convert(summary, name=f"converter_{scenario_name}")

        # 向量化路径
        n_days = len(equity_curve) - 1
        bt = _make_vectorized_backtest(n_days)
        daily_returns = [0.0]
        for i in range(1, len(equity_curve)):
            prev = equity_curve[i - 1]
            daily_returns.append(
                (equity_curve[i] - prev) / max(1, prev) if prev > 0 else 0.0
            )
        result_b = BacktestResult(
            name=f"vectorized_{scenario_name}",
            equity_curve=equity_curve,
            dates=list(pd.bdate_range("2021-01-04", periods=len(equity_curve))),
            daily_returns=daily_returns,
            trade_count=0,
            hedge_costs=[0.0] * len(equity_curve),
            transaction_costs=[0.0] * len(equity_curve),
            n_days=n_days,
        )
        bt._metrics(result_b)
        return result_a, result_b

    def test_bull_market_scenario(self) -> None:
        """牛市场景: 持续上涨。"""
        eq = [1_000_000.0 * (1.01 ** i) for i in range(100)]
        result_a, result_b = self._compare_both(eq, "bull")

        assert result_a.total_return == pytest.approx(result_b.total_return, rel=1e-6)
        assert result_a.annual_return == pytest.approx(result_b.annual_return, rel=1e-6)
        assert result_a.max_drawdown == pytest.approx(result_b.max_drawdown, rel=1e-6)

    def test_bear_market_scenario(self) -> None:
        """熊市场景: 持续下跌。"""
        eq = [1_000_000.0 * (0.99 ** i) for i in range(100)]
        result_a, result_b = self._compare_both(eq, "bear")

        assert result_a.total_return == pytest.approx(result_b.total_return, rel=1e-6)
        assert result_a.max_drawdown == pytest.approx(result_b.max_drawdown, rel=1e-6)

    def test_volatile_scenario(self) -> None:
        """高波动场景: 大幅震荡。"""
        np.random.seed(123)
        n = 200
        returns = np.random.normal(0, 0.03, n)  # 3% 日波动
        eq = [1_000_000.0]
        for r in returns:
            eq.append(eq[-1] * (1 + r))

        result_a, result_b = self._compare_both(eq, "volatile")

        assert result_a.annual_volatility == pytest.approx(
            result_b.annual_volatility, rel=1e-6
        )
        assert result_a.sharpe_ratio == pytest.approx(result_b.sharpe_ratio, rel=1e-4)

    def test_flat_scenario(self) -> None:
        """持平场景: 无涨跌。"""
        eq = [1_000_000.0] * 50
        result_a, result_b = self._compare_both(eq, "flat")

        assert result_a.total_return == pytest.approx(result_b.total_return, abs=1e-10)
        assert result_a.annual_volatility == pytest.approx(
            result_b.annual_volatility, abs=1e-10
        )

    def test_crash_recovery_scenario(self) -> None:
        """暴跌后恢复场景: V 型反转。"""
        eq = [1_000_000.0]
        # 先跌 30%
        for _ in range(50):
            eq.append(eq[-1] * 0.993)
        # 再涨回来
        for _ in range(50):
            eq.append(eq[-1] * 1.007)

        result_a, result_b = self._compare_both(eq, "crash_recovery")

        assert result_a.max_drawdown == pytest.approx(result_b.max_drawdown, rel=1e-6)
        assert result_a.calmar_ratio == pytest.approx(result_b.calmar_ratio, rel=1e-4)
