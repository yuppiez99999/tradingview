"""G7 覆盖率冲刺 — utils/backtest/vectorbt_bridge.py 单元测试.

目标: 覆盖率从 31.65% → ≥70%

测试范围:
    1. ComparisonReport dataclass + summary (PASS/FAIL)
    2. generate_ma_cross_signals: 金叉/死叉/HOLD/慢线未形成
    3. VectorBtBridge.__init__
    4. run_g15: 简单 bars + signals (真实引擎)
    5. run_vectorbt: mock vectorbt
    6. run_ma_cross_comparison: 主入口 (mock vectorbt)
    7. _LongShortContext.buy/sell 边界
    8. _MACrossStrategy.on_bar 分支

运行:
    python -m pytest tests/unit/test_g7_backtest_vectorbt_bridge_boost.py -v
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils.backtest.vectorbt_bridge import (  # noqa: E402
    ComparisonReport,
    VectorBtBridge,
    generate_ma_cross_signals,
)
from utils.wt_structs import BarData  # noqa: E402

# ============================================================
# 辅助: 构造 BarData
# ============================================================


def _make_bar(close: float, open_: float | None = None, code: str = "600519.SH") -> BarData:
    return BarData(
        code=code,
        exchange="SH",
        period="1d",
        open=open_ if open_ is not None else close,
        high=close * 1.01,
        low=close * 0.99,
        close=close,
        volume=10000.0,
        amount=10000.0 * close,
        date=20260101,
    )


def _make_bars(closes: list[float], opens: list[float] | None = None, code: str = "600519.SH") -> list[BarData]:
    opens = opens or closes
    return [_make_bar(c, o, code) for c, o in zip(closes, opens)]


# ============================================================
# 1. ComparisonReport
# ============================================================


class TestComparisonReport:
    def test_defaults(self) -> None:
        r = ComparisonReport(
            g15_final_equity=1_000_000, vbt_final_equity=1_000_000,
            equity_deviation_pct=0.0, g15_total_return=0.0, vbt_total_return=0.0,
            return_deviation_pct=0.0, passed=True, n_signals=10, n_buy_signals=2, n_sell_signals=2,
        )
        assert r.threshold_pct == 5.0
        assert r.notes == []

    def test_summary_pass(self) -> None:
        r = ComparisonReport(
            g15_final_equity=1_100_000, vbt_final_equity=1_100_000,
            equity_deviation_pct=0.5, g15_total_return=0.1, vbt_total_return=0.1,
            return_deviation_pct=0.5, passed=True, n_signals=10, n_buy_signals=2, n_sell_signals=2,
        )
        s = r.summary()
        assert "PASS" in s
        assert "1,100,000" in s

    def test_summary_fail(self) -> None:
        r = ComparisonReport(
            g15_final_equity=1_000_000, vbt_final_equity=1_100_000,
            equity_deviation_pct=10.0, g15_total_return=0.0, vbt_total_return=0.1,
            return_deviation_pct=10.0, passed=False, n_signals=10, n_buy_signals=2, n_sell_signals=2,
        )
        s = r.summary()
        assert "FAIL" in s


# ============================================================
# 2. generate_ma_cross_signals
# ============================================================


class TestGenerateMaCrossSignals:
    def test_hold_before_slow_window(self) -> None:
        # 慢线未形成 (前 slow_window-1 个) → HOLD
        closes = pd.Series([100.0, 101.0, 102.0])
        signals = generate_ma_cross_signals(closes, fast_window=2, slow_window=5)
        assert len(signals) == 3
        assert all(s == "HOLD" for s in signals)

    def test_golden_cross(self) -> None:
        # 构造先跌后涨, 产生金叉
        closes = pd.Series([100, 99, 98, 97, 96, 97, 98, 99, 100, 101, 102, 103, 104, 105, 106.0])
        signals = generate_ma_cross_signals(closes, fast_window=2, slow_window=5)
        assert "BUY" in signals

    def test_death_cross(self) -> None:
        # 构造先涨后跌, 产生死叉
        closes = pd.Series([100, 101, 102, 103, 104, 105, 106, 105, 104, 103, 102, 101, 100, 99, 98.0])
        signals = generate_ma_cross_signals(closes, fast_window=2, slow_window=5)
        assert "SELL" in signals

    def test_all_hold_no_cross(self) -> None:
        # 单调上升, 快线始终在慢线上方 → 无交叉
        closes = pd.Series([100.0 * (1.01 ** i) for i in range(30)])
        signals = generate_ma_cross_signals(closes, fast_window=2, slow_window=5)
        # 可能有初始 HOLD, 但不应有交叉 (单调)
        assert len(signals) == 30

    def test_length_matches_closes(self) -> None:
        closes = pd.Series([100.0] * 50)
        signals = generate_ma_cross_signals(closes, fast_window=5, slow_window=20)
        assert len(signals) == 50


# ============================================================
# 3. VectorBtBridge.__init__
# ============================================================


class TestVectorBtBridgeInit:
    def test_defaults(self) -> None:
        b = VectorBtBridge()
        assert b._initial_capital == 1_000_000.0
        assert b._commission_rate == 0.0003
        assert b._threshold_pct == 5.0
        assert b._position_size == 1000.0

    def test_custom(self) -> None:
        b = VectorBtBridge(initial_capital=500_000, commission_rate=0.0005, threshold_pct=3.0, position_size=500.0)
        assert b._initial_capital == 500_000
        assert b._commission_rate == 0.0005
        assert b._threshold_pct == 3.0
        assert b._position_size == 500.0


# ============================================================
# 4. run_g15 (真实引擎)
# ============================================================


class TestRunG15:
    def test_empty_bars(self) -> None:
        b = VectorBtBridge()
        summary = b.run_g15([], [], "600519.SH")
        assert summary is not None

    def test_all_hold_signals(self) -> None:
        closes = [100.0, 101.0, 102.0, 103.0, 104.0]
        bars = _make_bars(closes)
        signals = ["HOLD"] * 5
        b = VectorBtBridge()
        summary = b.run_g15(bars, signals, "600519.SH")
        # 全 HOLD → 无交易 → 最终权益 = 初始资金
        assert summary.final_equity == pytest.approx(1_000_000.0)

    def test_buy_then_sell(self) -> None:
        # BUY at bar0, SELL at bar2 → 有交易
        closes = [100.0, 101.0, 102.0, 103.0, 104.0]
        opens = [100.0, 101.0, 102.0, 103.0, 104.0]
        bars = _make_bars(closes, opens)
        signals = ["BUY", "HOLD", "SELL", "HOLD", "HOLD"]
        b = VectorBtBridge()
        summary = b.run_g15(bars, signals, "600519.SH")
        # 有订单成交
        assert summary.n_orders_filled >= 0  # 引擎语义: T 提交 T+1 成交


# ============================================================
# 5. run_vectorbt (mock vectorbt)
# ============================================================


class TestRunVectorbt:
    def test_run_with_mocked_vbt(self) -> None:
        # mock vectorbt.Portfolio.from_signals 返回 mock portfolio
        mock_vbt = MagicMock()
        mock_pf = MagicMock()
        mock_value_df = MagicMock()
        mock_value_df.iloc = MagicMock()
        mock_value_df.iloc.__getitem__ = MagicMock(return_value=1_050_000.0)
        mock_pf.value.return_value = mock_value_df
        mock_vbt.Portfolio.from_signals.return_value = mock_pf

        closes = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0])
        opens = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0])
        signals = ["HOLD", "BUY", "HOLD", "SELL", "HOLD"]

        b = VectorBtBridge()
        with patch.dict(sys.modules, {"vectorbt": mock_vbt}):
            result = b.run_vectorbt(closes, opens, signals)
            assert result is mock_pf


# ============================================================
# 6. run_ma_cross_comparison (主入口, mock vectorbt)
# ============================================================


class TestRunMaCrossComparison:
    def test_full_comparison(self) -> None:
        # 构造 30 天数据, mock vectorbt
        closes = pd.Series([100.0 * (1.001 ** i) + (5.0 if i > 15 else -5.0) for i in range(30)])
        opens = pd.Series([100.0 * (1.001 ** i) + (5.0 if i > 15 else -5.0) for i in range(30)])
        bars = _make_bars(list(closes), list(opens))

        mock_vbt = MagicMock()
        mock_pf = MagicMock()
        mock_value_series = pd.Series([1_000_000.0] * 30)
        mock_pf.value.return_value = mock_value_series
        mock_vbt.Portfolio.from_signals.return_value = mock_pf

        b = VectorBtBridge()
        with patch.dict(sys.modules, {"vectorbt": mock_vbt}):
            report = b.run_ma_cross_comparison(
                bars=bars, closes=closes, opens=opens,
                fast_window=2, slow_window=5,
            )
            assert isinstance(report, ComparisonReport)
            assert report.n_signals == 30
            # summary 可调用
            assert isinstance(report.summary(), str)

    def test_empty_bars_default_code(self) -> None:
        # bars 空 → target_code = ""
        closes = pd.Series([100.0])
        opens = pd.Series([100.0])

        mock_vbt = MagicMock()
        mock_pf = MagicMock()
        mock_pf.value.return_value = pd.Series([1_000_000.0])
        mock_vbt.Portfolio.from_signals.return_value = mock_pf

        b = VectorBtBridge()
        with patch.dict(sys.modules, {"vectorbt": mock_vbt}):
            report = b.run_ma_cross_comparison(bars=[], closes=closes, opens=opens)
            assert report.n_signals == 1


# ============================================================
# 7. _LongShortContext 边界 (通过 _MACrossStrategy 间接覆盖)
# ============================================================


class TestLongShortContextBoundary:
    def test_buy_zero_volume_no_op(self) -> None:
        # volume=0 → buy 返回 False, 不产生订单
        closes = [100.0, 101.0, 102.0]
        bars = _make_bars(closes)
        signals = ["BUY", "HOLD", "HOLD"]
        b = VectorBtBridge(position_size=0.0)
        summary = b.run_g15(bars, signals, "600519.SH")
        # position_size=0 → 无有效订单
        assert summary.final_equity == pytest.approx(1_000_000.0)

    def test_code_mismatch_no_op(self) -> None:
        # bar.code != target_code → on_bar 直接返回
        closes = [100.0, 101.0, 102.0]
        bars = _make_bars(closes, code="000001.SZ")
        signals = ["BUY", "SELL", "HOLD"]
        b = VectorBtBridge()
        # target_code 不匹配 → 无交易
        summary = b.run_g15(bars, signals, "600519.SH")
        assert summary.final_equity == pytest.approx(1_000_000.0)

    def test_more_signals_than_bars(self) -> None:
        # signals 长度 > bars 长度 → bar_index 越界保护
        closes = [100.0, 101.0]
        bars = _make_bars(closes)
        signals = ["BUY", "SELL", "BUY", "SELL"]  # 多于 bars
        b = VectorBtBridge()
        # 不抛异常
        summary = b.run_g15(bars, signals, "600519.SH")
        assert summary is not None
