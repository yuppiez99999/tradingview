"""test_execution_router_unit.py — 执行路由引擎单元测试

覆盖要点:
    - ExecutionPlan (dataclass 默认值 / to_dict round 4 位)
    - ExecutionReview (__post_init__ 自动 timestamp / 显式 timestamp 保留)
    - ExecutionRouter.route (high→IS / medium→VWAP / low→TWAP / signal=None / market_state=None)
    - ExecutionRouter._urgency (high/medium/low 边界)
    - ExecutionRouter._select_algorithm (IS/VWAP/TWAP 边界)
    - ExecutionRouter._estimate_slippage (IS/VWAP/TWAP 公式)
    - ExecutionRouter._duration / _slices
    - ExecutionRouter.review (within/超限/零价格/BUG-E1 actual_slippage 从 executed 读取)
    - ExecutionRouter.route_with_tca (flag 关闭/启用 approved/启用 rejected/启用 异常)
    - ExecutionRouter._save_review (落盘 jsonl)
"""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from utils.execution_router import ExecutionPlan, ExecutionReview, ExecutionRouter

# ============================================================
# ExecutionPlan
# ============================================================


class TestExecutionPlan:
    @pytest.mark.unit
    def test_defaults(self):
        plan = ExecutionPlan(symbol="000001", algorithm="TWAP", urgency="low")
        assert plan.estimated_slippage_bps == 0.0
        assert plan.estimated_duration_minutes == 60
        assert plan.slices == 1
        assert plan.meta == {}

    @pytest.mark.unit
    def test_to_dict_rounds_slippage(self):
        plan = ExecutionPlan(
            symbol="000001",
            algorithm="IS",
            urgency="high",
            estimated_slippage_bps=3.14159265,
            estimated_duration_minutes=15,
            slices=3,
            meta={"k": "v"},
        )
        d = plan.to_dict()
        assert d["symbol"] == "000001"
        assert d["algorithm"] == "IS"
        assert d["urgency"] == "high"
        assert d["estimated_slippage_bps"] == 3.1416  # round 4 位
        assert d["estimated_duration_minutes"] == 15
        assert d["slices"] == 3
        assert d["meta"] == {"k": "v"}

    @pytest.mark.unit
    def test_meta_default_factory_independent(self):
        """meta 默认 dict 不共享"""
        p1 = ExecutionPlan(symbol="A", algorithm="TWAP", urgency="low")
        p2 = ExecutionPlan(symbol="B", algorithm="TWAP", urgency="low")
        p1.meta["x"] = 1
        assert "x" not in p2.meta


# ============================================================
# ExecutionReview
# ============================================================


class TestExecutionReview:
    @pytest.mark.unit
    def test_post_init_auto_timestamp(self):
        review = ExecutionReview(
            symbol="000001",
            planned_price=10.0,
            executed_price=10.01,
            planned_slippage_bps=1.0,
            actual_slippage_bps=2.0,
            shortfall_bps=10.0,
            within_tolerance=True,
        )
        assert review.timestamp != ""
        # 验证格式 YYYY-MM-DD HH:MM:SS
        datetime.strptime(review.timestamp, "%Y-%m-%d %H:%M:%S")

    @pytest.mark.unit
    def test_explicit_timestamp_preserved(self):
        review = ExecutionReview(
            symbol="000001",
            planned_price=10.0,
            executed_price=10.0,
            planned_slippage_bps=0.0,
            actual_slippage_bps=0.0,
            shortfall_bps=0.0,
            within_tolerance=True,
            timestamp="2026-08-17 10:00:00",
        )
        assert review.timestamp == "2026-08-17 10:00:00"


# ============================================================
# ExecutionRouter._urgency
# ============================================================


class TestUrgency:
    @pytest.mark.unit
    def test_high_confidence_and_strength(self):
        router = ExecutionRouter(review_save_dir="reports/execution_test")
        assert router._urgency(0.8, 0.6, 100_000) == "high"

    @pytest.mark.unit
    def test_high_negative_strength(self):
        """abs(strength) >= 0.5 也算 high"""
        router = ExecutionRouter(review_save_dir="reports/execution_test")
        assert router._urgency(0.7, -0.5, 100_000) == "high"

    @pytest.mark.unit
    def test_medium_confidence_and_large_notional(self):
        router = ExecutionRouter(review_save_dir="reports/execution_test")
        assert router._urgency(0.5, 0.1, 200_000) == "medium"

    @pytest.mark.unit
    def test_low_default(self):
        router = ExecutionRouter(review_save_dir="reports/execution_test")
        assert router._urgency(0.3, 0.1, 50_000) == "low"

    @pytest.mark.unit
    def test_boundary_confidence_07(self):
        """confidence=0.7, strength=0.5 恰好 high"""
        router = ExecutionRouter(review_save_dir="reports/execution_test")
        assert router._urgency(0.7, 0.5, 100_000) == "high"

    @pytest.mark.unit
    def test_boundary_confidence_05_notional_200k(self):
        """confidence=0.5, notional=200_000 恰好 medium"""
        router = ExecutionRouter(review_save_dir="reports/execution_test")
        assert router._urgency(0.5, 0.1, 200_000) == "medium"


# ============================================================
# ExecutionRouter._select_algorithm
# ============================================================


class TestSelectAlgorithm:
    @pytest.mark.unit
    def test_high_urgency_is(self):
        router = ExecutionRouter(review_save_dir="reports/execution_test")
        assert router._select_algorithm("high", 100_000, 0.01) == "IS"

    @pytest.mark.unit
    def test_large_notional_is(self):
        """notional >= 500_000 → IS (即使 urgency=low)"""
        router = ExecutionRouter(review_save_dir="reports/execution_test")
        assert router._select_algorithm("low", 500_000, 0.01) == "IS"

    @pytest.mark.unit
    def test_medium_notional_vwap(self):
        """notional >= 100_000 → VWAP"""
        router = ExecutionRouter(review_save_dir="reports/execution_test")
        assert router._select_algorithm("low", 100_000, 0.01) == "VWAP"

    @pytest.mark.unit
    def test_high_vol_vwap(self):
        """vol >= 0.03 → VWAP"""
        router = ExecutionRouter(review_save_dir="reports/execution_test")
        assert router._select_algorithm("low", 50_000, 0.03) == "VWAP"

    @pytest.mark.unit
    def test_low_all_twap(self):
        router = ExecutionRouter(review_save_dir="reports/execution_test")
        assert router._select_algorithm("low", 50_000, 0.01) == "TWAP"


# ============================================================
# ExecutionRouter._estimate_slippage
# ============================================================


class TestEstimateSlippage:
    @pytest.mark.unit
    def test_is_formula(self):
        """IS: base * vol * 10000 * 1.2, base=max(notional/1M, 0.1)"""
        router = ExecutionRouter(review_save_dir="reports/execution_test")
        # notional=1_000_000 → base=1.0, vol=0.02 → 1.0*0.02*10000*1.2=240
        assert router._estimate_slippage("IS", 1_000_000, 0.02) == pytest.approx(240.0)

    @pytest.mark.unit
    def test_vwap_formula(self):
        router = ExecutionRouter(review_save_dir="reports/execution_test")
        # base=1.0, vol=0.02 → 1.0*0.02*10000=200
        assert router._estimate_slippage("VWAP", 1_000_000, 0.02) == pytest.approx(
            200.0
        )

    @pytest.mark.unit
    def test_twap_formula(self):
        router = ExecutionRouter(review_save_dir="reports/execution_test")
        # base=1.0, vol=0.02 → 1.0*0.02*10000*0.6=120
        assert router._estimate_slippage("TWAP", 1_000_000, 0.02) == pytest.approx(
            120.0
        )

    @pytest.mark.unit
    def test_small_notional_base_floor(self):
        """notional < 100_000 → base=0.1 (下限)"""
        router = ExecutionRouter(review_save_dir="reports/execution_test")
        # notional=10_000 → base=max(0.01, 0.1)=0.1, vol=0.02 → 0.1*0.02*10000=20
        assert router._estimate_slippage("VWAP", 10_000, 0.02) == pytest.approx(20.0)


# ============================================================
# ExecutionRouter._duration / _slices
# ============================================================


class TestDurationSlices:
    @pytest.mark.unit
    def test_duration_is(self):
        router = ExecutionRouter(review_save_dir="reports/execution_test")
        assert router._duration("IS", 1_000_000) == 15

    @pytest.mark.unit
    def test_duration_vwap(self):
        router = ExecutionRouter(review_save_dir="reports/execution_test")
        assert router._duration("VWAP", 1_000_000) == 90

    @pytest.mark.unit
    def test_duration_twap(self):
        router = ExecutionRouter(review_save_dir="reports/execution_test")
        assert router._duration("TWAP", 1_000_000) == 240

    @pytest.mark.unit
    def test_slices_is(self):
        router = ExecutionRouter(review_save_dir="reports/execution_test")
        assert router._slices("IS") == 3

    @pytest.mark.unit
    def test_slices_vwap(self):
        router = ExecutionRouter(review_save_dir="reports/execution_test")
        assert router._slices("VWAP") == 12

    @pytest.mark.unit
    def test_slices_twap(self):
        router = ExecutionRouter(review_save_dir="reports/execution_test")
        assert router._slices("TWAP") == 24


# ============================================================
# ExecutionRouter.route
# ============================================================


class TestRoute:
    @pytest.mark.unit
    def test_high_urgency_routes_to_is(self):
        router = ExecutionRouter(review_save_dir="reports/execution_test")
        order = {
            "symbol": "000001",
            "quantity": 1000,
            "side": "buy",
            "notional": 200_000,
        }
        signal = {"strength": 0.6, "confidence": 0.8}
        market_state = {"volatility": 0.02, "liquidity": 1.0, "spread": 0.001}

        plan = router.route(order, signal, market_state)
        assert plan.symbol == "000001"
        assert plan.algorithm == "IS"
        assert plan.urgency == "high"
        assert plan.estimated_duration_minutes == 15
        assert plan.slices == 3
        assert plan.meta["confidence"] == 0.8
        assert plan.meta["strength"] == 0.6
        assert plan.meta["notional"] == 200_000
        assert plan.meta["volatility"] == 0.02

    @pytest.mark.unit
    def test_low_urgency_small_notional_twap(self):
        router = ExecutionRouter(review_save_dir="reports/execution_test")
        order = {"symbol": "000002", "notional": 50_000}
        signal = {"strength": 0.1, "confidence": 0.3}
        market_state = {"volatility": 0.01}

        plan = router.route(order, signal, market_state)
        assert plan.algorithm == "TWAP"
        assert plan.urgency == "low"
        assert plan.estimated_duration_minutes == 240
        assert plan.slices == 24

    @pytest.mark.unit
    def test_medium_urgency_vwap(self):
        router = ExecutionRouter(review_save_dir="reports/execution_test")
        order = {"symbol": "600000", "notional": 200_000}
        signal = {"strength": 0.1, "confidence": 0.5}
        market_state = {"volatility": 0.01}

        plan = router.route(order, signal, market_state)
        assert plan.algorithm == "VWAP"
        assert plan.urgency == "medium"

    @pytest.mark.unit
    def test_signal_none_defaults(self):
        """signal=None → confidence=0.5, strength=0.0"""
        router = ExecutionRouter(review_save_dir="reports/execution_test")
        order = {"symbol": "000001", "notional": 50_000}

        plan = router.route(order, None, None)
        assert plan.meta["confidence"] == 0.5
        assert plan.meta["strength"] == 0.0
        assert plan.meta["volatility"] == 0.02  # 默认 vol

    @pytest.mark.unit
    def test_market_state_none_default_vol(self):
        router = ExecutionRouter(review_save_dir="reports/execution_test")
        order = {"symbol": "000001", "notional": 50_000}
        signal = {"confidence": 0.3, "strength": 0.0}

        plan = router.route(order, signal, None)
        assert plan.meta["volatility"] == 0.02

    @pytest.mark.unit
    def test_large_notional_500k_is(self):
        """notional >= 500_000 → IS (即使 urgency=low)"""
        router = ExecutionRouter(review_save_dir="reports/execution_test")
        order = {"symbol": "000001", "notional": 500_000}
        signal = {"confidence": 0.3, "strength": 0.0}

        plan = router.route(order, signal, {"volatility": 0.01})
        assert plan.algorithm == "IS"


# ============================================================
# ExecutionRouter.review
# ============================================================


class TestReview:
    @pytest.mark.unit
    def test_within_tolerance(self, tmp_path):
        router = ExecutionRouter(review_save_dir=str(tmp_path))
        planned = {"symbol": "000001", "price": 10.0, "slippage_bps": 1.0}
        executed = {"symbol": "000001", "price": 10.005, "slippage_bps": 2.0}

        review = router.review(planned, executed)
        # shortfall = (10.005 - 10.0) / 10.0 * 10000 = 5 bps
        assert review.shortfall_bps == pytest.approx(5.0)
        assert review.within_tolerance is True  # 5 <= 8
        assert review.planned_price == 10.0
        assert review.executed_price == 10.005
        assert review.actual_slippage_bps == 2.0  # BUG-E1: 从 executed 读取

    @pytest.mark.unit
    def test_outside_tolerance(self, tmp_path):
        router = ExecutionRouter(
            review_save_dir=str(tmp_path), shortfall_tolerance_bps=3.0
        )
        planned = {"symbol": "000001", "price": 10.0, "slippage_bps": 0.0}
        executed = {"symbol": "000001", "price": 10.01, "slippage_bps": 0.0}

        review = router.review(planned, executed)
        # shortfall = (10.01 - 10.0) / 10.0 * 10000 = 10 bps
        assert review.shortfall_bps == pytest.approx(10.0)
        assert review.within_tolerance is False  # 10 > 3

    @pytest.mark.unit
    def test_zero_planned_price(self, tmp_path):
        """planned_price=0 → shortfall=0.0 (避免除零)"""
        router = ExecutionRouter(review_save_dir=str(tmp_path))
        planned = {"symbol": "000001", "price": 0.0, "slippage_bps": 0.0}
        executed = {"symbol": "000001", "price": 10.0, "slippage_bps": 0.0}

        review = router.review(planned, executed)
        assert review.shortfall_bps == 0.0
        assert review.within_tolerance is True

    @pytest.mark.unit
    def test_negative_shortfall(self, tmp_path):
        """executed < planned → 负 shortfall"""
        router = ExecutionRouter(review_save_dir=str(tmp_path))
        planned = {"symbol": "000001", "price": 10.0, "slippage_bps": 0.0}
        executed = {"symbol": "000001", "price": 9.99, "slippage_bps": 0.0}

        review = router.review(planned, executed)
        # shortfall = (9.99 - 10.0) / 10.0 * 10000 = -10 bps
        assert review.shortfall_bps == pytest.approx(-10.0)
        assert review.within_tolerance is False  # abs(-10) > 8

    @pytest.mark.unit
    def test_bug_e1_actual_slippage_from_executed(self, tmp_path):
        """BUG-E1 回归: actual_slippage 必须从 executed 读取, 不是 planned"""
        router = ExecutionRouter(review_save_dir=str(tmp_path))
        planned = {"symbol": "000001", "price": 10.0, "slippage_bps": 99.0}
        executed = {"symbol": "000001", "price": 10.0, "slippage_bps": 5.0}

        review = router.review(planned, executed)
        assert review.actual_slippage_bps == 5.0  # 从 executed
        assert review.planned_slippage_bps == 99.0  # 从 planned

    @pytest.mark.unit
    def test_review_saves_to_file(self, tmp_path):
        router = ExecutionRouter(review_save_dir=str(tmp_path))
        planned = {"symbol": "000001", "price": 10.0, "slippage_bps": 0.0}
        executed = {"symbol": "000001", "price": 10.0, "slippage_bps": 0.0}

        router.review(planned, executed)
        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) == 1
        row = json.loads(files[0].read_text(encoding="utf-8").strip())
        assert row["symbol"] == "000001"


# ============================================================
# ExecutionRouter.route_with_tca
# ============================================================


class TestRouteWithTCA:
    @pytest.mark.unit
    def test_flag_disabled_returns_none(self, monkeypatch, tmp_path):
        """USE_TCA_PRE_TRADE_ESTIMATE=False → (plan, None)"""
        import utils.execution_router as er_mod

        monkeypatch.setattr(er_mod, "_tca_pre_trade_enabled", lambda: False)

        router = ExecutionRouter(review_save_dir=str(tmp_path))
        order = {"symbol": "000001", "notional": 200_000}
        signal = {"confidence": 0.8, "strength": 0.6}

        plan, estimate = router.route_with_tca(order, signal, {"volatility": 0.02})
        assert estimate is None
        assert plan.meta["tca_enabled"] is False

    @pytest.mark.unit
    def test_flag_enabled_approved(self, monkeypatch, tmp_path):
        """flag 启用, estimator 返回 approved=True"""
        import utils.execution_router as er_mod

        monkeypatch.setattr(er_mod, "_tca_pre_trade_enabled", lambda: True)

        mock_estimator = MagicMock()
        mock_estimate = MagicMock()
        mock_estimate.estimated_cost_bps = 10.0
        mock_estimate.approved = True
        mock_estimate.rejection_reason = ""
        mock_estimator.estimate.return_value = mock_estimate

        router = ExecutionRouter(
            review_save_dir=str(tmp_path), tca_estimator=mock_estimator
        )
        order = {"symbol": "000001", "notional": 200_000}
        signal = {"confidence": 0.8, "strength": 0.6}

        plan, estimate = router.route_with_tca(
            order, signal, {"volatility": 0.02, "adv": 1_000_000}
        )
        assert estimate is mock_estimate
        assert plan.meta["tca_enabled"] is True
        assert plan.meta["tca_cost_bps"] == 10.0
        assert plan.meta["tca_approved"] is True
        assert "tca_rejected" not in plan.meta

    @pytest.mark.unit
    def test_flag_enabled_rejected(self, monkeypatch, tmp_path):
        """flag 启用, estimator 返回 approved=False → tca_rejected"""
        import utils.execution_router as er_mod

        monkeypatch.setattr(er_mod, "_tca_pre_trade_enabled", lambda: True)

        mock_estimator = MagicMock()
        mock_estimate = MagicMock()
        mock_estimate.estimated_cost_bps = 50.0
        mock_estimate.approved = False
        mock_estimate.rejection_reason = "cost exceeds threshold"
        mock_estimator.estimate.return_value = mock_estimate

        router = ExecutionRouter(
            review_save_dir=str(tmp_path), tca_estimator=mock_estimator
        )
        order = {"symbol": "000001", "notional": 200_000}
        signal = {"confidence": 0.8, "strength": 0.6}

        plan, estimate = router.route_with_tca(order, signal, {"volatility": 0.02})
        assert plan.meta["tca_rejected"] is True
        assert plan.meta["tca_rejection_reason"] == "cost exceeds threshold"
        assert plan.meta["tca_approved"] is False

    @pytest.mark.unit
    def test_flag_enabled_estimator_exception(self, monkeypatch, tmp_path):
        """flag 启用, estimator 抛异常 → fail-safe (plan, None)"""
        import utils.execution_router as er_mod

        monkeypatch.setattr(er_mod, "_tca_pre_trade_enabled", lambda: True)

        mock_estimator = MagicMock()
        mock_estimator.estimate.side_effect = RuntimeError("estimator down")

        router = ExecutionRouter(
            review_save_dir=str(tmp_path), tca_estimator=mock_estimator
        )
        order = {"symbol": "000001", "notional": 200_000}
        signal = {"confidence": 0.8, "strength": 0.6}

        plan, estimate = router.route_with_tca(order, signal, {"volatility": 0.02})
        assert estimate is None
        assert "tca_error" in plan.meta
        assert "estimator down" in plan.meta["tca_error"]

    @pytest.mark.unit
    def test_flag_enabled_market_state_none(self, monkeypatch, tmp_path):
        """flag 启用, market_state=None → 用默认值构造 market_data"""
        import utils.execution_router as er_mod

        monkeypatch.setattr(er_mod, "_tca_pre_trade_enabled", lambda: True)

        mock_estimator = MagicMock()
        mock_estimate = MagicMock()
        mock_estimate.estimated_cost_bps = 5.0
        mock_estimate.approved = True
        mock_estimate.rejection_reason = ""
        mock_estimator.estimate.return_value = mock_estimate

        router = ExecutionRouter(
            review_save_dir=str(tmp_path), tca_estimator=mock_estimator
        )
        order = {"symbol": "000001", "notional": 200_000}

        plan, estimate = router.route_with_tca(order, None, None)
        assert estimate is mock_estimate
        # 验证 estimator 收到 market_data
        call_args = mock_estimator.estimate.call_args
        market_data = call_args[0][1]
        assert market_data["adv"] == 0.0
        assert market_data["volatility"] == 0.02
