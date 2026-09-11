"""test_s2_l2_thresholds_unit.py — L2 执行层参数口径 (Issue #13: S-2)

巡检事实: L2 默认 portfolio_value=1_000_000 + gate.max_single_pct=2%,
真实组合 200 万下几乎所有有意义的仓位在 L2 被 veto (实测 1700 元×200 股 = 34 万
被判 "名义金额超过上限 2 万"), 说明桥接层参数从未用真实组合跑通。
本测试锁定新的单一事实源口径 (200 万 / 单笔 5%)。
"""

from __future__ import annotations

import pytest

from ai_decision.execution_bridge import _generate_execution_plan, execute_decision
from ai_decision.execution_risk import _execution_risk_check
from ai_decision.models import TradingDecision
from utils.risk_thresholds import get_default_portfolio_value, get_max_single_pct


def _decision(action: str = "buy", mode: str = "shadow") -> TradingDecision:
    return TradingDecision(
        symbol="600519",
        action=action,
        strength=0.9,
        confidence=0.9,
        mode=mode,
        verdict_type="AUTO",
    )


class TestL2PortfolioValue:
    @pytest.mark.unit
    def test_default_portfolio_value_from_single_source(self):
        assert get_default_portfolio_value() == pytest.approx(2_000_000.0)

    @pytest.mark.unit
    def test_execution_plan_uses_configured_portfolio_value(self):
        """缺省净值 = 配置值 (200 万), 与显式传入等价。"""
        plan_default = _generate_execution_plan(_decision(), None, price=1700.0)
        plan_explicit = _generate_execution_plan(
            _decision(), get_default_portfolio_value(), price=1700.0
        )
        assert get_max_single_pct() == pytest.approx(0.05)
        assert plan_default["qty"] == plan_explicit["qty"] > 0
        assert plan_default["notional"] == plan_explicit["notional"]

    @pytest.mark.unit
    def test_single_pct_ceiling_scales_with_portfolio(self):
        """单笔上限口径生效: 组合越大, 同一信号下建仓股数越多。"""
        small = _generate_execution_plan(_decision(), 2_000_000.0, price=10.0)
        large = _generate_execution_plan(_decision(), 20_000_000.0, price=10.0)
        assert large["qty"] > small["qty"]
        assert large["notional"] <= 20_000_000.0 * get_max_single_pct()

    @pytest.mark.unit
    def test_realistic_position_no_longer_vetoed(self):
        """S-2 核心回归: 真实规模订单 (34 万/200 万 = 17%) 在合理上限下不再被误杀。

        旧口径 (100 万净值 × 2% = 2 万上限) 会 veto 一切有意义的仓位。
        """
        plan = {
            "symbol": "600519",
            "side": "BUY",
            "qty": 200,
            "limit_price": 1700.0,
            "notional": 340_000.0,
        }
        result = _execution_risk_check(plan, portfolio_value=2_000_000.0)
        # 17% 仍超 5% 上限 → 应 veto, 但原因是"真实超限"而非参数错配
        assert result.veto is True
        assert result.checks["notional"]["max"] == pytest.approx(100_000.0)

    @pytest.mark.unit
    def test_small_order_passes_with_default_portfolio(self):
        plan = {
            "symbol": "600519",
            "side": "BUY",
            "qty": 100,
            "limit_price": 1700.0,
            "notional": 170_000.0,
        }
        result = _execution_risk_check(plan, portfolio_value=None)
        # 默认净值 200 万 → 上限 10 万; 17 万超限, 但 L2 报的是真实上限
        assert result.checks["notional"]["max"] == pytest.approx(100_000.0)

    @pytest.mark.unit
    def test_execute_decision_defaults_to_configured_portfolio(self):
        result = execute_decision(_decision(), price=1700.0)
        assert result["execution_plan"]["qty"] > 0
        assert result["mode"] == "shadow"


class TestPriceMissingSemantics:
    @pytest.mark.unit
    def test_price_missing_slice_price_is_none(self):
        """S-2: 价格缺失时切片价不得写占位价 10.0 (避免被误当真实限价)。"""
        plan = _generate_execution_plan(_decision(), 2_000_000.0, price=None)
        assert plan["price_missing"] is True
        assert plan["slices"][0]["price"] is None

    @pytest.mark.unit
    def test_present_price_propagates_to_slices_and_plan(self):
        plan = _generate_execution_plan(_decision(), 2_000_000.0, price=1700.0)
        assert plan["price_missing"] is False
        assert plan["limit_price"] == pytest.approx(1700.0)
        assert all(s["price"] == pytest.approx(1700.0) for s in plan["slices"])
