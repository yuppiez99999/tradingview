"""tca_post_trade_attribution 单元测试 — TCA 执行后归因引擎."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from utils.tca_post_trade_attribution import (
    DEFAULT_ESTIMATE_VS_ACTUAL_TOLERANCE_BPS,
    EstimateVsActual,
    FillRecord,
    PnLAttribution,
    PostTradeAttribution,
    PostTradeAttributionError,
    create_default_attribution,
    create_no_save_attribution,
)


class TestFillRecord:
    def test_basic(self):
        fr = FillRecord(symbol="600276", side="BUY", shares=1000, price=10.5)
        assert fr.symbol == "600276"
        assert fr.side == "BUY"
        assert fr.shares == 1000
        assert fr.price == 10.5
        assert fr.timestamp != ""
        assert fr.broker == ""

    def test_custom_timestamp(self):
        fr = FillRecord(symbol="A", side="SELL", shares=100, price=20.0, timestamp="2026-01-01T10:00:00")
        assert fr.timestamp == "2026-01-01T10:00:00"


class TestEstimateVsActual:
    def test_basic(self):
        eva = EstimateVsActual(
            symbol="A", side="BUY", estimated_cost_bps=5.0, actual_cost_bps=7.0,
            deviation_bps=2.0, estimated_amount=100.0, actual_amount=140.0,
            deviation_amount=40.0, within_tolerance=False, tolerance_bps=5.0,
        )
        assert eva.symbol == "A"
        assert eva.timestamp != ""

    def test_to_dict(self):
        eva = EstimateVsActual(
            symbol="A", side="BUY", estimated_cost_bps=5.0, actual_cost_bps=7.0,
            deviation_bps=2.0, estimated_amount=100.0, actual_amount=140.0,
            deviation_amount=40.0, within_tolerance=False, tolerance_bps=5.0,
        )
        d = eva.to_dict()
        assert d["symbol"] == "A"
        assert d["actual_cost_bps"] == 7.0


class TestPnLAttribution:
    def test_basic(self):
        pnl = PnLAttribution(
            symbol="A", side="BUY", shares=100, decision_price=10.0, avg_exec_price=10.05,
            alpha_pnl=0.0, execution_pnl=-50.0, risk_pnl=0.0, total_pnl=-50.0,
            alpha_bps=0.0, execution_bps=-47.6, risk_bps=0.0, notional=1005.0,
        )
        assert pnl.timestamp != ""

    def test_to_dict(self):
        pnl = PnLAttribution(
            symbol="A", side="BUY", shares=100, decision_price=10.0, avg_exec_price=10.05,
            alpha_pnl=0.0, execution_pnl=-50.0, risk_pnl=0.0, total_pnl=-50.0,
            alpha_bps=0.0, execution_bps=-47.6, risk_bps=0.0, notional=1005.0,
        )
        d = pnl.to_dict()
        assert d["symbol"] == "A"


class TestPostTradeAttributionInit:
    def test_default(self, tmp_path):
        pta = PostTradeAttribution(attribution_dir=tmp_path, save_to_file=False)
        assert pta.tolerance_bps == DEFAULT_ESTIMATE_VS_ACTUAL_TOLERANCE_BPS
        assert pta.save_to_file is False

    def test_custom(self, tmp_path):
        pta = PostTradeAttribution(tolerance_bps=10.0, attribution_dir=tmp_path, save_to_file=True)
        assert pta.tolerance_bps == 10.0
        assert pta.save_to_file is True
        assert tmp_path.exists()


class TestRecord:
    def test_basic(self, tmp_path):
        pta = PostTradeAttribution(attribution_dir=tmp_path, save_to_file=False)
        fill = FillRecord(symbol="600276", side="BUY", shares=1000, price=10.5)
        pta.record(fill)
        assert len(pta._records["600276"]) == 1

    def test_with_estimate(self, tmp_path):
        pta = PostTradeAttribution(attribution_dir=tmp_path, save_to_file=False)
        fill = FillRecord(symbol="A", side="BUY", shares=100, price=10.0)
        estimate = MagicMock()
        pta.record(fill, estimate=estimate)
        assert pta._records["A"][-1] == (fill, estimate)

    def test_invalid_fill(self, tmp_path):
        pta = PostTradeAttribution(attribution_dir=tmp_path, save_to_file=False)
        with pytest.raises(PostTradeAttributionError):
            pta.record(fill="not a fill")

    def test_save_to_file(self, tmp_path):
        pta = PostTradeAttribution(attribution_dir=tmp_path, save_to_file=True)
        fill = FillRecord(symbol="A", side="BUY", shares=100, price=10.0)
        pta.record(fill)
        files = list(tmp_path.glob("fills_*.jsonl"))
        assert len(files) == 1


class TestCompareEstimateVsActual:
    def test_no_records(self, tmp_path):
        pta = PostTradeAttribution(attribution_dir=tmp_path, save_to_file=False)
        assert pta.compare_estimate_vs_actual("NONEXIST") is None

    def test_no_estimate(self, tmp_path):
        pta = PostTradeAttribution(attribution_dir=tmp_path, save_to_file=False)
        fill = FillRecord(symbol="A", side="BUY", shares=100, price=10.0)
        pta.record(fill, estimate=None)
        assert pta.compare_estimate_vs_actual("A") is None

    def test_basic_buy(self, tmp_path):
        pta = PostTradeAttribution(attribution_dir=tmp_path, save_to_file=False)
        fill = FillRecord(symbol="A", side="BUY", shares=100, price=10.05)
        estimate = MagicMock()
        estimate.estimated_cost_bps = 3.0
        estimate.estimated_cost_amount = 30.0
        estimate.notional = 1000.0
        pta.record(fill, estimate=estimate)
        result = pta.compare_estimate_vs_actual("A", decision_price=10.0)
        assert result is not None
        assert result.symbol == "A"
        assert result.actual_cost_bps > 0
        assert result.estimated_cost_bps == 3.0

    def test_within_tolerance(self, tmp_path):
        pta = PostTradeAttribution(tolerance_bps=100.0, attribution_dir=tmp_path, save_to_file=False)
        fill = FillRecord(symbol="A", side="BUY", shares=100, price=10.0)
        estimate = MagicMock()
        estimate.estimated_cost_bps = 0.0
        estimate.estimated_cost_amount = 0.0
        estimate.notional = 1000.0
        pta.record(fill, estimate=estimate)
        result = pta.compare_estimate_vs_actual("A", decision_price=10.0)
        assert result.within_tolerance is True

    def test_sell_side(self, tmp_path):
        pta = PostTradeAttribution(attribution_dir=tmp_path, save_to_file=False)
        fill = FillRecord(symbol="A", side="SELL", shares=100, price=9.95)
        estimate = MagicMock()
        estimate.estimated_cost_bps = 5.0
        estimate.estimated_cost_amount = 50.0
        estimate.notional = 1000.0
        pta.record(fill, estimate=estimate)
        result = pta.compare_estimate_vs_actual("A", decision_price=10.0)
        assert result is not None
        assert result.side == "SELL"


class TestAttributePnl:
    def test_buy_basic(self, tmp_path):
        pta = PostTradeAttribution(attribution_dir=tmp_path, save_to_file=False)
        result = pta.attribute_pnl(
            symbol="A", decision_price=10.0, avg_exec_price=10.05,
            shares=1000, side="BUY",
        )
        assert result.symbol == "A"
        assert result.alpha_pnl == 0.0
        assert result.execution_pnl == pytest.approx(-50.0)
        assert result.risk_pnl == 0.0
        assert result.total_pnl == pytest.approx(-50.0)

    def test_sell_basic(self, tmp_path):
        pta = PostTradeAttribution(attribution_dir=tmp_path, save_to_file=False)
        result = pta.attribute_pnl(
            symbol="A", decision_price=10.0, avg_exec_price=10.05,
            shares=1000, side="SELL",
        )
        assert result.execution_pnl == pytest.approx(50.0)

    def test_with_estimated_entry(self, tmp_path):
        pta = PostTradeAttribution(attribution_dir=tmp_path, save_to_file=False)
        result = pta.attribute_pnl(
            symbol="A", decision_price=10.0, avg_exec_price=10.05,
            shares=1000, side="BUY", estimated_entry_price=10.02,
        )
        assert result.alpha_pnl == pytest.approx(-20.0)
        assert result.execution_pnl == pytest.approx(-30.0)

    def test_with_risk_pnl(self, tmp_path):
        pta = PostTradeAttribution(attribution_dir=tmp_path, save_to_file=False)
        result = pta.attribute_pnl(
            symbol="A", decision_price=10.0, avg_exec_price=10.0,
            shares=1000, side="BUY",
            hedge_pnl=-200, stop_loss_pnl=-50, position_adjust_pnl=30,
        )
        assert result.risk_pnl == pytest.approx(-220.0)

    def test_invalid_decision_price(self, tmp_path):
        pta = PostTradeAttribution(attribution_dir=tmp_path, save_to_file=False)
        with pytest.raises(PostTradeAttributionError):
            pta.attribute_pnl(symbol="A", decision_price=0, avg_exec_price=10, shares=100, side="BUY")

    def test_invalid_shares(self, tmp_path):
        pta = PostTradeAttribution(attribution_dir=tmp_path, save_to_file=False)
        with pytest.raises(PostTradeAttributionError):
            pta.attribute_pnl(symbol="A", decision_price=10, avg_exec_price=10, shares=0, side="BUY")

    def test_invalid_side(self, tmp_path):
        pta = PostTradeAttribution(attribution_dir=tmp_path, save_to_file=False)
        with pytest.raises(PostTradeAttributionError):
            pta.attribute_pnl(symbol="A", decision_price=10, avg_exec_price=10, shares=100, side="HOLD")

    def test_bps_calculation(self, tmp_path):
        pta = PostTradeAttribution(attribution_dir=tmp_path, save_to_file=False)
        result = pta.attribute_pnl(
            symbol="A", decision_price=10.0, avg_exec_price=10.0,
            shares=1000, side="BUY", hedge_pnl=-100,
        )
        assert result.notional == pytest.approx(10000.0)
        assert result.risk_bps == pytest.approx(-100.0)


class TestCalibrate:
    def test_no_data(self, tmp_path):
        pta = PostTradeAttribution(attribution_dir=tmp_path, save_to_file=False)
        assert pta.calibrate() is None

    def test_no_estimator(self, tmp_path):
        pta = PostTradeAttribution(attribution_dir=tmp_path, save_to_file=False)
        fill = FillRecord(symbol="A", side="BUY", shares=100, price=10.05)
        estimate = MagicMock()
        estimate.estimated_cost_bps = 3.0
        estimate.estimated_cost_amount = 30.0
        estimate.notional = 1000.0
        pta.record(fill, estimate=estimate)
        pta.compare_estimate_vs_actual("A", decision_price=10.0)
        assert pta.calibrate() is None

    def test_with_estimator(self, tmp_path):
        pta = PostTradeAttribution(attribution_dir=tmp_path, save_to_file=False)
        fill = FillRecord(symbol="A", side="BUY", shares=100, price=10.05)
        estimate = MagicMock()
        estimate.estimated_cost_bps = 3.0
        estimate.estimated_cost_amount = 30.0
        estimate.notional = 1000.0
        pta.record(fill, estimate=estimate)
        pta.compare_estimate_vs_actual("A", decision_price=10.0)
        estimator = MagicMock()
        estimator.calibrate_threshold.return_value = 15.0
        estimator.cost_threshold_bps = 10.0
        result = pta.calibrate(pre_trade_estimator=estimator)
        assert result == 15.0

    def test_estimator_missing_method(self, tmp_path):
        pta = PostTradeAttribution(attribution_dir=tmp_path, save_to_file=False)
        fill = FillRecord(symbol="A", side="BUY", shares=100, price=10.05)
        estimate = MagicMock()
        estimate.estimated_cost_bps = 3.0
        estimate.estimated_cost_amount = 30.0
        estimate.notional = 1000.0
        pta.record(fill, estimate=estimate)
        pta.compare_estimate_vs_actual("A", decision_price=10.0)
        estimator = MagicMock(spec=[])
        with pytest.raises(PostTradeAttributionError):
            pta.calibrate(pre_trade_estimator=estimator)


class TestSummarize:
    def test_empty(self, tmp_path):
        pta = PostTradeAttribution(attribution_dir=tmp_path, save_to_file=False)
        s = pta.summarize()
        assert s["total_pnl"] == 0.0
        assert s["n_fills"] == 0
        assert s["n_comparisons"] == 0

    def test_with_data(self, tmp_path):
        pta = PostTradeAttribution(attribution_dir=tmp_path, save_to_file=False)
        pta.attribute_pnl(symbol="A", decision_price=10.0, avg_exec_price=10.05, shares=1000, side="BUY")
        pta.attribute_pnl(symbol="B", decision_price=20.0, avg_exec_price=20.0, shares=500, side="SELL", hedge_pnl=-100)
        s = pta.summarize()
        assert s["n_fills"] == 0
        assert "A" in s["per_symbol"]
        assert "B" in s["per_symbol"]


class TestHistoryQueries:
    def test_get_pnl_history(self, tmp_path):
        pta = PostTradeAttribution(attribution_dir=tmp_path, save_to_file=False)
        pta.attribute_pnl(symbol="A", decision_price=10.0, avg_exec_price=10.05, shares=100, side="BUY")
        assert len(pta.get_pnl_history("A")) == 1
        assert len(pta.get_pnl_history()) == 1
        assert len(pta.get_pnl_history("NONEXIST")) == 0

    def test_get_fills(self, tmp_path):
        pta = PostTradeAttribution(attribution_dir=tmp_path, save_to_file=False)
        fill = FillRecord(symbol="A", side="BUY", shares=100, price=10.0)
        pta.record(fill)
        assert len(pta.get_fills("A")) == 1
        assert len(pta.get_fills()) == 1

    def test_get_comparison_history(self, tmp_path):
        pta = PostTradeAttribution(attribution_dir=tmp_path, save_to_file=False)
        assert pta.get_comparison_history() == []


class TestFactoryFunctions:
    def test_create_default(self):
        pta = create_default_attribution()
        assert pta.save_to_file is True

    def test_create_no_save(self):
        pta = create_no_save_attribution()
        assert pta.save_to_file is False
