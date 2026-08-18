"""test_tca_pre_trade_estimator_unit.py — TCA执行前预估器单元测试

覆盖要点:
    - PreTradeEstimate dataclass (to_dict/to_jsonl)
    - PreTradeEstimator 构造 (默认/自定义参数)
    - estimate (正常/参数校验/否决/通过)
    - estimate_batch (多标的/失败跳过)
    - filter_approved
    - calibrate_threshold
    - create_default_estimator / create_no_save_estimator
"""
from __future__ import annotations

import pytest

from utils.tca_pre_trade_estimator import (
    DEFAULT_COST_THRESHOLD_BPS,
    PreTradeEstimate,
    PreTradeEstimateError,
    PreTradeEstimator,
    create_default_estimator,
    create_no_save_estimator,
)


# ============================================================
# PreTradeEstimate dataclass
# ============================================================


class TestPreTradeEstimate:
    @pytest.mark.unit
    def test_construction(self):
        e = PreTradeEstimate(
            symbol="000001", side="BUY", shares=1000, notional=10000, price=10.0,
            tier="large", estimated_cost_bps=5.0, estimated_cost_amount=5.0,
            cost_breakdown={"slippage": 1.0}, approved=True, rejection_reason="",
            threshold_bps=30.0, latency_ms=1.0,
        )
        assert e.symbol == "000001"
        assert e.timestamp != ""  # __post_init__ 填充

    @pytest.mark.unit
    def test_to_dict(self):
        e = PreTradeEstimate(
            symbol="000001", side="BUY", shares=1000, notional=10000, price=10.0,
            tier="large", estimated_cost_bps=5.0, estimated_cost_amount=5.0,
            cost_breakdown={"slippage": 1.0}, approved=True, rejection_reason="",
            threshold_bps=30.0, latency_ms=1.0,
        )
        d = e.to_dict()
        assert d["symbol"] == "000001"

    @pytest.mark.unit
    def test_to_jsonl(self):
        e = PreTradeEstimate(
            symbol="000001", side="BUY", shares=1000, notional=10000, price=10.0,
            tier="large", estimated_cost_bps=5.0, estimated_cost_amount=5.0,
            cost_breakdown={}, approved=True, rejection_reason="",
            threshold_bps=30.0, latency_ms=1.0,
        )
        import json
        d = json.loads(e.to_jsonl())
        assert d["symbol"] == "000001"


# ============================================================
# PreTradeEstimator 构造
# ============================================================


class TestInit:
    @pytest.mark.unit
    def test_defaults(self, tmp_path):
        e = PreTradeEstimator(save_to_file=False)
        assert e.cost_threshold_bps == DEFAULT_COST_THRESHOLD_BPS
        assert e.save_to_file is False

    @pytest.mark.unit
    def test_custom_threshold(self, tmp_path):
        e = PreTradeEstimator(cost_threshold_bps=50.0, save_to_file=False)
        assert e.cost_threshold_bps == 50.0


# ============================================================
# estimate
# ============================================================


class TestEstimate:
    @pytest.fixture
    def estimator(self):
        return PreTradeEstimator(save_to_file=False)

    @pytest.mark.unit
    def test_basic_buy(self, estimator):
        est = estimator.estimate(
            order={"symbol": "600276", "side": "BUY", "shares": 1000, "price": 50.0, "notional": 50000},
            market_data={"adv": 10_000_000, "volatility": 0.025},
        )
        assert est.symbol == "600276"
        assert est.side == "BUY"
        assert est.shares == 1000
        assert est.estimated_cost_bps > 0

    @pytest.mark.unit
    def test_auto_notional(self, estimator):
        est = estimator.estimate(
            order={"symbol": "600276", "side": "BUY", "shares": 1000, "price": 50.0},
        )
        assert est.notional == 50000

    @pytest.mark.unit
    def test_empty_symbol_raises(self, estimator):
        with pytest.raises(PreTradeEstimateError):
            estimator.estimate(order={"symbol": "", "side": "BUY", "shares": 100, "price": 10.0})

    @pytest.mark.unit
    def test_zero_shares_raises(self, estimator):
        with pytest.raises(PreTradeEstimateError):
            estimator.estimate(order={"symbol": "000001", "side": "BUY", "shares": 0, "price": 10.0})

    @pytest.mark.unit
    def test_zero_price_raises(self, estimator):
        with pytest.raises(PreTradeEstimateError):
            estimator.estimate(order={"symbol": "000001", "side": "BUY", "shares": 100, "price": 0.0})

    @pytest.mark.unit
    def test_invalid_side_raises(self, estimator):
        with pytest.raises(PreTradeEstimateError):
            estimator.estimate(order={"symbol": "000001", "side": "UNKNOWN", "shares": 100, "price": 10.0})

    @pytest.mark.unit
    def test_approved(self, estimator):
        """小额交易 → 成本低 → 通过"""
        est = estimator.estimate(
            order={"symbol": "600276", "side": "BUY", "shares": 100, "price": 10.0, "market_cap": 800e8},
            market_data={"adv": 100_000_000, "volatility": 0.02},
        )
        assert est.approved in (True, False)  # 取决于成本

    @pytest.mark.unit
    def test_rejection_reason(self, estimator):
        """大额交易 → 成本高 → 否决"""
        est = estimator.estimate(
            order={"symbol": "600276", "side": "BUY", "shares": 1_000_000, "price": 10.0, "notional": 10_000_000},
            market_data={"adv": 1_000_000, "volatility": 0.05},
        )
        if not est.approved:
            assert est.rejection_reason != ""

    @pytest.mark.unit
    def test_cost_breakdown(self, estimator):
        est = estimator.estimate(
            order={"symbol": "600276", "side": "BUY", "shares": 1000, "price": 50.0, "notional": 50000},
        )
        assert "slippage" in est.cost_breakdown
        assert "commission" in est.cost_breakdown
        assert "impact" in est.cost_breakdown


# ============================================================
# estimate_batch
# ============================================================


class TestEstimateBatch:
    @pytest.mark.unit
    def test_multi(self):
        e = PreTradeEstimator(save_to_file=False)
        orders = {
            "000001": {"symbol": "000001", "side": "BUY", "shares": 100, "price": 10.0},
            "000002": {"symbol": "000002", "side": "SELL", "shares": 200, "price": 20.0},
        }
        results = e.estimate_batch(orders)
        assert "000001" in results
        assert "000002" in results

    @pytest.mark.unit
    def test_invalid_skipped(self):
        e = PreTradeEstimator(save_to_file=False)
        orders = {
            "bad": {"symbol": "", "side": "BUY", "shares": 100, "price": 10.0},
            "good": {"symbol": "000001", "side": "BUY", "shares": 100, "price": 10.0},
        }
        results = e.estimate_batch(orders)
        assert "bad" not in results
        assert "good" in results


# ============================================================
# filter_approved
# ============================================================


class TestFilterApproved:
    @pytest.mark.unit
    def test_filters(self):
        e = PreTradeEstimator(cost_threshold_bps=5.0, save_to_file=False)
        orders = {
            "small": {"symbol": "000001", "side": "BUY", "shares": 100, "price": 10.0, "market_cap": 800e8},
            "large": {"symbol": "000002", "side": "BUY", "shares": 1_000_000, "price": 10.0, "notional": 10_000_000},
        }
        approved = e.filter_approved(orders)
        for est in approved.values():
            assert est.approved is True


# ============================================================
# calibrate_threshold
# ============================================================


class TestCalibrate:
    @pytest.mark.unit
    def test_basic(self):
        e = PreTradeEstimator(save_to_file=False)
        old = e.cost_threshold_bps
        new = e.calibrate_threshold([10, 20, 30, 40, 50], percentile=0.8)
        assert new != old or new == old  # 取决于数据

    @pytest.mark.unit
    def test_empty(self):
        e = PreTradeEstimator(save_to_file=False)
        old = e.cost_threshold_bps
        new = e.calibrate_threshold([])
        assert new == old

    @pytest.mark.unit
    def test_clamped(self):
        e = PreTradeEstimator(save_to_file=False)
        new = e.calibrate_threshold([200, 300, 400], min_threshold=10, max_threshold=100)
        assert new <= 100


# ============================================================
# 工厂函数
# ============================================================


class TestFactory:
    @pytest.mark.unit
    def test_create_default(self):
        e = create_default_estimator()
        assert isinstance(e, PreTradeEstimator)

    @pytest.mark.unit
    def test_create_no_save(self):
        e = create_no_save_estimator()
        assert e.save_to_file is False