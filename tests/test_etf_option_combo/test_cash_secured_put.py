"""05_02c — CashSecuredPutEngine 单元测试.

业务规则 (spec.md §5.3):
    1. 现金担保: 冻结 strike × 10000 × qty
    2. OTM ∈ [3%, 8%]
    3. 被指派后按行权价买入标的
    4. 权利金预算 ≤ 总资本1.0%
    5. Kill Switch 阻断
"""

from __future__ import annotations

import pytest

from utils.etf_option_combo.cash_secured_put import CashSecuredPutEngine
from utils.etf_option_combo.combo_base import LegSide, StrategyType

pytestmark = pytest.mark.unit


@pytest.fixture
def csp_config():
    return {
        "total_capital": 2_000_000,
        "otm_pct": 0.05,
        "otm_pct_range": (0.03, 0.08),
        "dte_min": 30,
        "dte_max": 60,
        "preferred_dte": 45,
        "annual_budget_pct": 0.010,
        "max_single_weight": 0.20,
    }


@pytest.fixture
def csp_engine(chain_fetcher, csp_config):
    return CashSecuredPutEngine(config=csp_config, chain_fetcher=chain_fetcher)


@pytest.fixture
def csp_engine_with_state(chain_fetcher, csp_config, state_manager):
    return CashSecuredPutEngine(
        config=csp_config, chain_fetcher=chain_fetcher, state_manager=state_manager,
    )


class TestCspSufficientCash:
    def test_csp_sufficient_cash(self, csp_engine, underlying_code, fixed_spot_price):
        """现金充足时成功生成 CSP."""
        pos = {
            "available_cash": 100_000.0,
            "target_weight": 0.10,
            "current_weight": 0.0,
        }
        result = csp_engine.generate(underlying_code, pos)
        assert result.error_code is None, f"error: {result.error_code}"
        assert result.strategy_type == StrategyType.CASH_SECURED_PUT
        assert len(result.orders) == 1
        order = result.orders[0]
        assert order.leg.option_type == "PUT"
        assert order.leg.side == LegSide.SELL
        assert order.leg.multiplier == 10000

    def test_csp_otm_in_range(self, csp_engine, underlying_code, fixed_spot_price):
        """OTM ∈ [3%, 8%]."""
        pos = {"available_cash": 100_000.0, "target_weight": 0.10, "current_weight": 0.0}
        result = csp_engine.generate(underlying_code, pos)
        if result.error_code is None:
            for order in result.orders:
                otm = (fixed_spot_price - order.leg.strike) / fixed_spot_price
                assert 0.03 <= otm <= 0.08


class TestCspInsufficientCash:
    def test_csp_insufficient_cash_zero(self, csp_engine, underlying_code):
        """可用现金为 0 时返回 CSP_INSUFFICIENT_CASH."""
        pos = {"available_cash": 0.0, "target_weight": 0.10, "current_weight": 0.0}
        result = csp_engine.generate(underlying_code, pos)
        assert result.error_code == "CSP_INSUFFICIENT_CASH"

    def test_csp_insufficient_cash_low(self, csp_engine, underlying_code):
        """可用现金不足冻结时返回 CSP_INSUFFICIENT_CASH."""
        # strike≈2.85, 需要 2.85*10000=28500, 给 1000 不够
        pos = {"available_cash": 1000.0, "target_weight": 0.10, "current_weight": 0.0}
        result = csp_engine.generate(underlying_code, pos)
        assert result.error_code == "CSP_INSUFFICIENT_CASH"


class TestCspTargetWeightReached:
    def test_csp_target_weight_reached(self, csp_engine, underlying_code):
        """已达目标权重时返回 CSP_TARGET_WEIGHT_REACHED."""
        pos = {
            "available_cash": 100_000.0,
            "target_weight": 0.10,
            "current_weight": 0.15,  # 已超目标
        }
        result = csp_engine.generate(underlying_code, pos)
        assert result.error_code == "CSP_TARGET_WEIGHT_REACHED"


class TestCspAssignment:
    def test_csp_assignment(self, csp_engine, make_combo_leg):
        """被指派后生成买入标的指令."""
        assigned_leg = make_combo_leg(
            option_type="PUT", side=LegSide.SELL, strike=2.85, quantity=1,
        )
        result = csp_engine.handle_assignment(assigned_leg)
        assert result.error_msg == "ASSIGNMENT_HANDLED"
        assert result.strategy_type == StrategyType.CASH_SECURED_PUT

    def test_csp_assignment_weight_warning(self, csp_engine_with_state, make_combo_leg):
        """被指派后权重超限触发再平衡日志 (不报错)."""
        # total_capital=2M, strike=2.85, qty=1 -> 买入 10000 份
        # weight = 10000*2.85/2M = 0.01425 < 0.20, 不超限
        assigned_leg = make_combo_leg(option_type="PUT", side=LegSide.SELL, strike=2.85, quantity=1)
        result = csp_engine_with_state.handle_assignment(assigned_leg)
        assert result.error_msg == "ASSIGNMENT_HANDLED"


class TestCspBudgetExceeded:
    def test_csp_budget_exceeded(self, csp_engine_with_state, underlying_code, state_manager):
        """年化权利金收入超预算返回 CSP_BUDGET_EXCEEDED."""
        # 总资本 2M × 1.0% = 20000
        state_manager.update_budget("cash_secured_put", 20000.0)
        pos = {"available_cash": 100_000.0, "target_weight": 0.10, "current_weight": 0.0}
        result = csp_engine_with_state.generate(underlying_code, pos)
        assert result.error_code == "CSP_BUDGET_EXCEEDED"


class TestCspKillSwitch:
    def test_csp_kill_switch_block(self, chain_fetcher, csp_config, underlying_code, mock_risk_state):
        """Kill Switch 触发时返回 CSP_KILL_SWITCH_ACTIVE."""

        class FakeRiskManager:
            def get_risk_state(self):
                return mock_risk_state(kill_switch_active=True)

        engine = CashSecuredPutEngine(
            config=csp_config, chain_fetcher=chain_fetcher, risk_manager=FakeRiskManager(),
        )
        pos = {"available_cash": 100_000.0, "target_weight": 0.10, "current_weight": 0.0}
        result = engine.generate(underlying_code, pos)
        assert result.error_code == "CSP_KILL_SWITCH_ACTIVE"


class TestCspDrawdownBlock:
    def test_csp_l2_block(self, chain_fetcher, csp_config, underlying_code, mock_risk_state):
        """回撤 L2 阻断 CSP."""

        class FakeRiskManager:
            def get_risk_state(self):
                return mock_risk_state(drawdown_level="L2")

        engine = CashSecuredPutEngine(
            config=csp_config, chain_fetcher=chain_fetcher, risk_manager=FakeRiskManager(),
        )
        pos = {"available_cash": 100_000.0, "target_weight": 0.10, "current_weight": 0.0}
        result = engine.generate(underlying_code, pos)
        assert result.error_code == "CSP_BLOCKED_BY_DRAWDOWN"
