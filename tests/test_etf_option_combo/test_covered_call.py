"""05_02a — CoveredCallEngine 单元测试.

业务规则 (spec.md §5.1):
    1. 现货担保: shares >= 10000, 否则 CC_NO_UNDERLYING
    2. OTM ∈ [2%, 8%]
    3. DTE ∈ [30, 60]
    4. 权利金预算: 年化收入 ≤ 总资本1.5%
    5. 回撤 L2+ 阻断
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from utils.etf_option_combo.combo_base import LegSide, StrategyType
from utils.etf_option_combo.covered_call import CoveredCallEngine


pytestmark = pytest.mark.unit


@pytest.fixture
def cc_engine(chain_fetcher, base_config):
    """CoveredCallEngine 实例."""
    return CoveredCallEngine(
        config=base_config,
        chain_fetcher=chain_fetcher,
        risk_manager=None,
        greek_manager=None,
        state_manager=None,
    )


@pytest.fixture
def cc_engine_with_state(chain_fetcher, base_config, state_manager):
    """带状态管理器的 CoveredCallEngine."""
    return CoveredCallEngine(
        config=base_config,
        chain_fetcher=chain_fetcher,
        risk_manager=None,
        greek_manager=None,
        state_manager=state_manager,
    )


class TestCoveredCallSpotSufficient:
    def test_covered_call_spot_sufficient(self, cc_engine, underlying_code, spot_position_sufficient):
        """现货充足时成功生成备兑看涨."""
        result = cc_engine.generate(underlying_code, spot_position_sufficient)
        assert result.error_code is None
        assert result.strategy_type == StrategyType.COVERED_CALL
        assert len(result.orders) == 1
        order = result.orders[0]
        assert order.leg.option_type == "CALL"
        assert order.leg.side == LegSide.SELL
        assert order.leg.multiplier == 10000
        assert result.net_premium > 0  # 卖出收权利金

    def test_covered_call_otm_in_range(self, cc_engine, underlying_code, spot_position_sufficient, fixed_spot_price):
        """OTM 程度 ∈ [2%, 8%]."""
        result = cc_engine.generate(underlying_code, spot_position_sufficient)
        assert result.error_code is None
        for order in result.orders:
            otm = (order.leg.strike - fixed_spot_price) / fixed_spot_price
            assert 0.02 <= otm <= 0.08, f"OTM {otm:.4f} 越界"


class TestCoveredCallNoUnderlying:
    def test_covered_call_no_underlying(self, cc_engine, underlying_code):
        """现货持仓不足 10000 份时返回 CC_NO_UNDERLYING."""
        result = cc_engine.generate(underlying_code, {"shares": 5000})
        assert result.error_code == "CC_NO_UNDERLYING"
        assert result.orders == ()

    def test_covered_call_zero_shares(self, cc_engine, underlying_code):
        """现货持仓为 0 时返回 CC_NO_UNDERLYING."""
        result = cc_engine.generate(underlying_code, {"shares": 0})
        assert result.error_code == "CC_NO_UNDERLYING"


class TestCoveredCallBudgetExceeded:
    def test_covered_call_budget_exceeded(self, cc_engine_with_state, underlying_code, spot_position_sufficient, state_manager):
        """年化权利金收入超预算时返回 CC_BUDGET_EXCEEDED."""
        # 预填满预算: 总资本 2M × 1.5% = 30000
        state_manager.update_budget("covered_call", 30000.0)
        result = cc_engine_with_state.generate(underlying_code, spot_position_sufficient)
        assert result.error_code == "CC_BUDGET_EXCEEDED"


class TestCoveredCallDrawdownBlock:
    def test_covered_call_l2_block(self, chain_fetcher, base_config, underlying_code, spot_position_sufficient, mock_risk_state):
        """回撤 L2 阻断备兑看涨 (CC_BLOCKED_BY_DRAWDOWN)."""

        class FakeRiskManager:
            def get_risk_state(self):
                return mock_risk_state(drawdown_level="L2")

        engine = CoveredCallEngine(
            config=base_config, chain_fetcher=chain_fetcher,
            risk_manager=FakeRiskManager(),
        )
        result = engine.generate(underlying_code, spot_position_sufficient)
        assert result.error_code == "CC_BLOCKED_BY_DRAWDOWN"

    def test_covered_call_l3_block(self, chain_fetcher, base_config, underlying_code, spot_position_sufficient, mock_risk_state):
        """回撤 L3 阻断备兑看涨."""

        class FakeRiskManager:
            def get_risk_state(self):
                return mock_risk_state(drawdown_level="L3")

        engine = CoveredCallEngine(
            config=base_config, chain_fetcher=chain_fetcher,
            risk_manager=FakeRiskManager(),
        )
        result = engine.generate(underlying_code, spot_position_sufficient)
        assert result.error_code == "CC_BLOCKED_BY_DRAWDOWN"

    def test_covered_call_l0_allow(self, chain_fetcher, base_config, underlying_code, spot_position_sufficient, mock_risk_state):
        """回撤 L0 不阻断."""

        class FakeRiskManager:
            def get_risk_state(self):
                return mock_risk_state(drawdown_level="L0")

        engine = CoveredCallEngine(
            config=base_config, chain_fetcher=chain_fetcher,
            risk_manager=FakeRiskManager(),
        )
        result = engine.generate(underlying_code, spot_position_sufficient)
        assert result.error_code is None


class TestCoveredCallNoOptionData:
    def test_covered_call_no_option_data(self, empty_chain_fetcher, base_config, underlying_code, spot_position_sufficient):
        """期权链为空时返回 NO_OPTION_DATA 或 CC_NO_OPTION_DATA."""
        engine = CoveredCallEngine(
            config=base_config, chain_fetcher=empty_chain_fetcher,
        )
        result = engine.generate(underlying_code, spot_position_sufficient)
        # 现货充足但期权链空 -> NO_OPTION_DATA (chain_fetcher 返回 [])
        assert result.error_code in ("NO_OPTION_DATA", "CC_NO_OPTION_DATA", "NO_SPOT_DATA")


class TestCoveredCallRiskPreCheck:
    def test_covered_call_risk_blocked(self, chain_fetcher, base_config, underlying_code, spot_position_sufficient):
        """风控预检拒绝时返回 RISK_BLOCKED."""

        class BlockingRiskManager:
            def pre_check(self, legs, market_state):
                return {"approved": False, "rejected_reason": "TEST_BLOCK"}

            def get_risk_state(self):
                return {"drawdown_level": "L0", "kill_switch_active": False}

        engine = CoveredCallEngine(
            config=base_config, chain_fetcher=chain_fetcher,
            risk_manager=BlockingRiskManager(),
        )
        result = engine.generate(underlying_code, spot_position_sufficient)
        assert result.error_code == "RISK_BLOCKED"


class TestCoveredCallClose:
    def test_close_returns_empty_orders(self, cc_engine):
        """close() 返回空订单包."""
        result = cc_engine.close(reason="manual")
        assert result.orders == ()
        assert result.error_code is None