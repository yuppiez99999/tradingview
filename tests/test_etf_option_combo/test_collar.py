"""05_02b — CollarEngine 单元测试.

业务规则 (spec.md §5.2):
    1. 三腿: 现货 + 买OTM Put + 卖OTM Call
    2. 零成本: |Put.premium - Call.premium| ≤ 0.5% × spot
    3. 保护带宽度: (Call.strike - Put.strike) / spot ≥ 10%
    4. Put OTM ≤ 10%
    5. 回撤 L3 阻断
"""

from __future__ import annotations

import pytest

from utils.etf_option_combo.combo_base import LegSide, StrategyType
from utils.etf_option_combo.collar import CollarEngine


pytestmark = pytest.mark.unit


@pytest.fixture
def collar_config():
    return {
        "total_capital": 2_000_000,
        "put_otm_pct": 0.05,
        "call_otm_pct": 0.05,
        "protection_band_min": 0.10,
        "put_otm_max": 0.10,
        "max_net_cost_pct": 0.005,
        "dte_min": 30,
        "dte_max": 60,
        "preferred_dte": 45,
    }


@pytest.fixture
def collar_engine(chain_fetcher, collar_config):
    return CollarEngine(config=collar_config, chain_fetcher=chain_fetcher)


class TestCollarZeroCost:
    def test_collar_zero_cost(self, collar_engine, underlying_code, spot_position_sufficient, fixed_spot_price):
        """成功构建零/低成本领口."""
        result = collar_engine.generate(underlying_code, spot_position_sufficient)
        assert result.error_code is None, f"error: {result.error_code} - {result.error_msg}"
        assert result.strategy_type == StrategyType.COLLAR
        # 应有 Put(买) + Call(卖) 两腿
        assert len(result.orders) >= 1
        legs = [o.leg for o in result.orders]
        call_legs = [l for l in legs if l.option_type == "CALL" and l.side == LegSide.SELL]
        assert len(call_legs) == 1, "应有 1 条卖出 Call 腿"

    def test_collar_protection_band(self, collar_engine, underlying_code, spot_position_sufficient, fixed_spot_price):
        """保护带宽度 ≥ 10%."""
        result = collar_engine.generate(underlying_code, spot_position_sufficient)
        if result.error_code is not None:
            pytest.skip(f"合成链无法构建领口: {result.error_code}")
        legs = [o.leg for o in result.orders]
        put_leg = next((l for l in legs if l.option_type == "PUT"), None)
        call_leg = next((l for l in legs if l.option_type == "CALL"), None)
        if put_leg and call_leg:
            band = (call_leg.strike - put_leg.strike) / fixed_spot_price
            assert band >= 0.10 - 1e-6, f"保护带 {band:.4f} < 0.10"


class TestCollarNoUnderlying:
    def test_collar_no_underlying(self, collar_engine, underlying_code):
        """现货不足时返回 COLLAR_NO_UNDERLYING."""
        result = collar_engine.generate(underlying_code, {"shares": 5000})
        assert result.error_code == "COLLAR_NO_UNDERLYING"


class TestCollarBandTooNarrow:
    def test_collar_band_too_narrow_validation(self, chain_fetcher, underlying_code, fixed_spot_price, make_combo_leg):
        """校验保护带过窄返回 COLLAR_BAND_TOO_NARROW."""
        from datetime import date
        cfg = {"protection_band_min": 0.10, "put_otm_max": 0.10, "max_net_cost_pct": 0.005}
        engine = CollarEngine(config=cfg, chain_fetcher=chain_fetcher)
        # 直接构造腿并调用校验
        put_leg = make_combo_leg(
            option_type="PUT", side=LegSide.BUY, strike=2.97, premium=0.03,
        )
        call_leg = make_combo_leg(
            option_type="CALL", side=LegSide.SELL, strike=3.00, premium=0.03,
        )
        # band = (3.00 - 2.97) / 3.00 = 0.01 < 0.10
        error = engine._validate_business_rules((put_leg, call_leg), fixed_spot_price)
        assert error == "COLLAR_BAND_TOO_NARROW"


class TestCollarPutOtmExceed:
    def test_collar_put_otm_too_deep(self, chain_fetcher, fixed_spot_price, make_combo_leg):
        """Put OTM > 10% 返回 COLLAR_PUT_OTM_TOO_DEEP."""
        cfg = {"protection_band_min": 0.10, "put_otm_max": 0.10, "max_net_cost_pct": 0.005}
        engine = CollarEngine(config=cfg, chain_fetcher=chain_fetcher)
        # spot=3.0, put strike=2.60 -> otm = (3.0-2.60)/3.0 = 0.133 > 0.10
        put_leg = make_combo_leg(option_type="PUT", side=LegSide.BUY, strike=2.60, premium=0.01)
        call_leg = make_combo_leg(option_type="CALL", side=LegSide.SELL, strike=3.30, premium=0.01)
        error = engine._validate_business_rules((put_leg, call_leg), fixed_spot_price)
        assert error == "COLLAR_PUT_OTM_TOO_DEEP"


class TestCollarCostTooHigh:
    def test_collar_cost_too_high(self, chain_fetcher, fixed_spot_price, make_combo_leg):
        """净成本超限返回 COLLAR_COST_TOO_HIGH."""
        cfg = {"protection_band_min": 0.10, "put_otm_max": 0.10, "max_net_cost_pct": 0.005}
        engine = CollarEngine(config=cfg, chain_fetcher=chain_fetcher)
        # put premium=0.10, call premium=0.01 -> net_cost=0.09 > 0.005*3.0=0.015
        put_leg = make_combo_leg(option_type="PUT", side=LegSide.BUY, strike=2.70, premium=0.10)
        call_leg = make_combo_leg(option_type="CALL", side=LegSide.SELL, strike=3.30, premium=0.01)
        error = engine._validate_business_rules((put_leg, call_leg), fixed_spot_price)
        assert error == "COLLAR_COST_TOO_HIGH"


class TestCollarExistingPut:
    def test_collar_existing_put(self, chain_fetcher, collar_config, underlying_code, spot_position_sufficient):
        """已有认沽保护时仅新增 Call 腿."""

        class FakeProtectivePutEngine:
            state = {"active_protections": {"510050.SH": {"strike": 2.85}}}

        engine = CollarEngine(
            config=collar_config, chain_fetcher=chain_fetcher,
            protective_put_engine=FakeProtectivePutEngine(),
        )
        result = engine.generate(underlying_code, spot_position_sufficient)
        if result.error_code is None:
            legs = [o.leg for o in result.orders]
            put_legs = [l for l in legs if l.option_type == "PUT"]
            # 已有保护, 不新增 Put
            assert len(put_legs) == 0


class TestCollarL3Block:
    def test_collar_l3_block(self, chain_fetcher, collar_config, underlying_code, spot_position_sufficient, mock_risk_state):
        """回撤 L3 阻断领口."""

        class FakeRiskManager:
            def get_risk_state(self):
                return mock_risk_state(drawdown_level="L3")

        engine = CollarEngine(
            config=collar_config, chain_fetcher=chain_fetcher,
            risk_manager=FakeRiskManager(),
        )
        result = engine.generate(underlying_code, spot_position_sufficient)
        assert result.error_code == "COLLAR_BLOCKED_BY_DRAWDOWN"

    def test_collar_l2_allow(self, chain_fetcher, collar_config, underlying_code, spot_position_sufficient, mock_risk_state):
        """回撤 L2 不阻断领口 (仅 L3 阻断)."""

        class FakeRiskManager:
            def get_risk_state(self):
                return mock_risk_state(drawdown_level="L2")

        engine = CollarEngine(
            config=collar_config, chain_fetcher=chain_fetcher,
            risk_manager=FakeRiskManager(),
        )
        result = engine.generate(underlying_code, spot_position_sufficient)
        # L2 不阻断 collar
        assert result.error_code != "COLLAR_BLOCKED_BY_DRAWDOWN"


class TestCollarMissingCall:
    def test_collar_missing_call(self, chain_fetcher, fixed_spot_price, make_combo_leg):
        """缺少 Call 腿返回 COLLAR_MISSING_CALL."""
        cfg = {"protection_band_min": 0.10, "put_otm_max": 0.10, "max_net_cost_pct": 0.005}
        engine = CollarEngine(config=cfg, chain_fetcher=chain_fetcher)
        put_leg = make_combo_leg(option_type="PUT", side=LegSide.BUY, strike=2.85, premium=0.03)
        error = engine._validate_business_rules((put_leg,), fixed_spot_price)
        assert error == "COLLAR_MISSING_CALL"