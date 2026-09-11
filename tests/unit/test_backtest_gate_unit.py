"""Backtest Gate 单元测试.

被测模块: utils/pipeline/backtest_gate.py
覆盖目标: >=80%
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.pipeline.backtest_gate import BacktestGate  # noqa: E402
from utils.pipeline.types import (  # noqa: E402
    AlphaSignalResult,
    BacktestGateResult,
    PipelineConfig,
    PipelineStage,
)


def _make_gate(tmp_path) -> BacktestGate:
    config = PipelineConfig(report_dir=str(tmp_path))
    return BacktestGate(config=config)


class TestFinalJudgment:
    def test_all_pass(self, tmp_path):
        gate = _make_gate(tmp_path)
        result = BacktestGateResult(
            ic=0.05, dsr=1.5, walk_forward_passed=True, stress_test_passed=True
        )
        judged = gate._final_judgment(result)
        assert judged.passed is True

    def test_ic_fail(self, tmp_path):
        gate = _make_gate(tmp_path)
        result = BacktestGateResult(
            ic=0.01, dsr=1.5, walk_forward_passed=True, stress_test_passed=True
        )
        judged = gate._final_judgment(result)
        assert judged.passed is False
        assert "IC" in judged.rejection_reason

    def test_dsr_fail(self, tmp_path):
        # 2026-09-10 min_dsr 口径迁移 (1.0→0.5) 后, dsr=0.5 恰在阈值上不再 FAIL;
        # 用 0.4 (< min_dsr=0.5) 表达 "DSR 不足" 语义 (pre-existing 测试未跟上口径)
        gate = _make_gate(tmp_path)
        result = BacktestGateResult(
            ic=0.05, dsr=0.4, walk_forward_passed=True, stress_test_passed=True
        )
        judged = gate._final_judgment(result)
        assert judged.passed is False
        assert "DSR" in judged.rejection_reason

    def test_walk_forward_fail(self, tmp_path):
        gate = _make_gate(tmp_path)
        result = BacktestGateResult(
            ic=0.05, dsr=1.5, walk_forward_passed=False, stress_test_passed=True
        )
        judged = gate._final_judgment(result)
        assert judged.passed is False
        assert "Walk-Forward" in judged.rejection_reason

    def test_stress_test_fail(self, tmp_path):
        gate = _make_gate(tmp_path)
        result = BacktestGateResult(
            ic=0.05, dsr=1.5, walk_forward_passed=True, stress_test_passed=False
        )
        judged = gate._final_judgment(result)
        assert judged.passed is False
        assert "压力测试" in judged.rejection_reason

    def test_all_fail(self, tmp_path):
        gate = _make_gate(tmp_path)
        result = BacktestGateResult(
            ic=0.01, dsr=0.5, walk_forward_passed=False, stress_test_passed=False
        )
        judged = gate._final_judgment(result)
        assert judged.passed is False
        assert ";" in judged.rejection_reason


class TestExtractCloseSeries:
    def test_none_df(self):
        assert BacktestGate._extract_close_series(None, "A") is None

    def test_empty_df(self):
        assert BacktestGate._extract_close_series(pd.DataFrame(), "A") is None

    def test_no_close_column(self):
        df = pd.DataFrame({"open": [1, 2, 3]})
        assert BacktestGate._extract_close_series(df, "A") is None

    def test_with_close_column(self):
        df = pd.DataFrame(
            {
                "close": [10.0, 11.0, 12.0],
                "date": ["2026-08-01", "2026-08-02", "2026-08-03"],
            }
        )
        series = BacktestGate._extract_close_series(df, "A")
        assert series is not None
        assert len(series) == 3

    def test_with_index_as_date(self):
        df = pd.DataFrame(
            {"close": [10.0, 11.0, 12.0]},
            index=pd.to_datetime(["2026-08-01", "2026-08-02", "2026-08-03"]),
        )
        series = BacktestGate._extract_close_series(df, "A")
        assert series is not None
        assert len(series) == 3

    def test_single_row_returns_none(self):
        df = pd.DataFrame({"close": [10.0], "date": ["2026-08-01"]})
        assert BacktestGate._extract_close_series(df, "A") is None

    def test_chinese_column_names(self):
        df = pd.DataFrame(
            {
                "收盘": [10.0, 11.0],
                "日期": ["2026-08-01", "2026-08-02"],
            }
        )
        series = BacktestGate._extract_close_series(df, "A")
        assert series is not None


class TestSignalDate:
    def test_with_training_date_iso(self):
        signal = AlphaSignalResult(training_date="2026-08-01")
        result = BacktestGate._signal_date(signal, {})
        assert result is not None
        assert result.year == 2026

    def test_with_training_date_compact(self):
        signal = AlphaSignalResult(training_date="20260801")
        result = BacktestGate._signal_date(signal, {})
        assert result is not None

    def test_with_training_date_slash(self):
        signal = AlphaSignalResult(training_date="2026/08/01")
        result = BacktestGate._signal_date(signal, {})
        assert result is not None

    def test_no_training_date_with_prices(self):
        signal = AlphaSignalResult()
        prices = {
            "A": pd.Series(
                [1, 2, 3],
                index=pd.to_datetime(["2026-08-01", "2026-08-02", "2026-08-03"]),
            )
        }
        result = BacktestGate._signal_date(signal, prices)
        assert result is not None

    def test_no_training_date_no_prices(self):
        signal = AlphaSignalResult()
        assert BacktestGate._signal_date(signal, {}) is None


class TestForwardReturns:
    def test_no_signal_date(self, tmp_path):
        gate = _make_gate(tmp_path)
        signal = AlphaSignalResult()
        result = gate._forward_returns(signal, {}, horizon=5)
        assert len(result) == 0

    def test_normal_calculation(self, tmp_path):
        gate = _make_gate(tmp_path)
        signal = AlphaSignalResult(training_date="2026-08-01")
        prices = {
            "A": pd.Series(
                [10.0, 11.0, 12.0, 13.0, 14.0, 15.0],
                index=pd.to_datetime(
                    [
                        "2026-07-28",
                        "2026-07-29",
                        "2026-07-30",
                        "2026-07-31",
                        "2026-08-01",
                        "2026-08-02",
                    ]
                ),
            ),
        }
        result = gate._forward_returns(signal, prices, horizon=1)
        assert isinstance(result, pd.Series)


class TestPortfolioReturns:
    def test_empty_signals(self, tmp_path):
        gate = _make_gate(tmp_path)
        signal = AlphaSignalResult()
        result = gate._portfolio_returns(signal, {})
        assert len(result) == 0

    def test_normal_calculation(self, tmp_path):
        gate = _make_gate(tmp_path)
        signal = AlphaSignalResult(
            signals={"A": 1.0, "B": 0.5},
            training_date="2026-08-03",
        )
        prices = {
            "A": pd.Series(
                [10.0, 11.0, 12.0],
                index=pd.to_datetime(["2026-08-01", "2026-08-02", "2026-08-03"]),
            ),
            "B": pd.Series(
                [20.0, 21.0, 22.0],
                index=pd.to_datetime(["2026-08-01", "2026-08-02", "2026-08-03"]),
            ),
        }
        result = gate._portfolio_returns(signal, prices, window_days=10)
        assert isinstance(result, pd.Series)

    def test_negative_weights_clipped(self, tmp_path):
        gate = _make_gate(tmp_path)
        signal = AlphaSignalResult(
            signals={"A": -1.0, "B": 1.0},
            training_date="2026-08-03",
        )
        prices = {
            "A": pd.Series(
                [10.0, 11.0, 12.0],
                index=pd.to_datetime(["2026-08-01", "2026-08-02", "2026-08-03"]),
            ),
            "B": pd.Series(
                [20.0, 21.0, 22.0],
                index=pd.to_datetime(["2026-08-01", "2026-08-02", "2026-08-03"]),
            ),
        }
        result = gate._portfolio_returns(signal, prices, window_days=10)
        assert isinstance(result, pd.Series)


class TestMakeResult:
    def test_make_result(self, tmp_path):
        gate = _make_gate(tmp_path)
        started = datetime(2026, 8, 14, 10, 0)
        gate_result = BacktestGateResult(passed=True, ic=0.05)
        result = gate._make_result(started, gate_result)
        assert result.stage == PipelineStage.BACKTEST_GATE
        assert result.success is True


class TestPortfolioReturnsLookaheadGuard:
    """P1-2: 样本外序列必须排除信号日 (训练日) 当日已实现收益."""

    def test_training_day_return_excluded(self, tmp_path):
        gate = _make_gate(tmp_path)
        signal = AlphaSignalResult(
            signals={"A": 1.0},
            training_date="2026-08-03",  # 训练日 +9.78% 已实现收益
        )
        prices = {
            "A": pd.Series(
                [10.0, 10.0, 10.0, 10.978],
                index=pd.to_datetime(
                    ["2026-07-31", "2026-08-01", "2026-08-02", "2026-08-03"]
                ),
            ),
        }
        result = gate._portfolio_returns(signal, prices, window_days=None)
        # 训练日 2026-08-03 的已实现收益 (+9.78%) 不得进入样本外序列
        assert "2026-08-03" not in [d.strftime("%Y-%m-%d") for d in result.index]
        assert "2026-08-02" in [d.strftime("%Y-%m-%d") for d in result.index]
        # 序列内不应出现 0.0978 级别的收益
        assert not (result.abs() > 0.05).any()
