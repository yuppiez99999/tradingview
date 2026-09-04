"""05_02d — VerticalSpreadEngine 单元测试.

业务规则 (spec.md §5.4):
    1. 同到期日
    2. 行权价顺序: 借方认购买低卖高, 借方认沽买高卖低
    3. 价差宽度 ∈ [0.05, 0.30]
    4. 不得替代现货底仓
"""

from __future__ import annotations

from datetime import date

import pytest

from utils.etf_option_combo.combo_base import LegSide, StrategyType
from utils.etf_option_combo.vertical_spread import VerticalSpreadEngine


pytestmark = pytest.mark.unit


@pytest.fixture
def vs_config():
    return {
        "total_capital": 2_000_000,
        "spread_width_min": 0.05,
        "spread_width_max": 0.30,
        "max_loss_budget_pct": 0.005,
        "min_volume": 0,  # 合成链 volume=1000, 设 0 放宽
        "dte_min": 30,
        "dte_max": 60,
        "preferred_dte": 45,
    }


@pytest.fixture
def vs_engine(chain_fetcher, vs_config):
    return VerticalSpreadEngine(config=vs_config, chain_fetcher=chain_fetcher)


class TestVerticalSpreadSuccess:
    def test_vs_debit_call_success(self, vs_engine, underlying_code, fixed_spot_price):
        """借方认购价差成功生成."""
        pos = {"spread_type": "debit_call", "replace_spot": False}
        result = vs_engine.generate(underlying_code, pos)
        assert result.error_code is None, f"error: {result.error_code}"
        assert result.strategy_type == StrategyType.VERTICAL_SPREAD
        assert len(result.orders) == 2
        long_leg = result.orders[0].leg
        short_leg = result.orders[1].leg
        assert long_leg.side == LegSide.BUY
        assert short_leg.side == LegSide.SELL
        assert long_leg.expiry == short_leg.expiry  # 同到期日

    def test_vs_strike_order_debit_call(self, vs_engine, underlying_code, fixed_spot_price):
        """借方认购: 买低卖高."""
        pos = {"spread_type": "debit_call", "replace_spot": False}
        result = vs_engine.generate(underlying_code, pos)
        if result.error_code is None:
            long_leg = result.orders[0].leg
            short_leg = result.orders[1].leg
            assert long_leg.strike < short_leg.strike

    def test_vs_debit_put_success(self, vs_engine, underlying_code):
        """借方认沽价差成功生成."""
        pos = {"spread_type": "debit_put", "replace_spot": False}
        result = vs_engine.generate(underlying_code, pos)
        assert result.error_code is None, f"error: {result.error_code}"


class TestVerticalSpreadWidthValidation:
    def test_vs_width_too_narrow(self, chain_fetcher, fixed_spot_price, make_combo_leg):
        """价差宽度 < 0.05 返回 VS_WIDTH_TOO_NARROW."""
        cfg = {"spread_width_min": 0.05, "spread_width_max": 0.30, "total_capital": 2_000_000}
        engine = VerticalSpreadEngine(config=cfg, chain_fetcher=chain_fetcher)
        expiry = date(2026, 9, 23)
        long_leg = make_combo_leg(option_type="CALL", side=LegSide.BUY, strike=3.10, premium=0.05, expiry=expiry)
        short_leg = make_combo_leg(option_type="CALL", side=LegSide.SELL, strike=3.12, premium=0.04, expiry=expiry)
        # width = 0.02 < 0.05
        error = engine._validate_business_rules((long_leg, short_leg), fixed_spot_price)
        assert error == "VS_WIDTH_TOO_NARROW"

    def test_vs_width_too_wide(self, chain_fetcher, fixed_spot_price, make_combo_leg):
        """价差宽度 > 0.30 返回 VS_WIDTH_TOO_WIDE."""
        cfg = {"spread_width_min": 0.05, "spread_width_max": 0.30, "total_capital": 2_000_000}
        engine = VerticalSpreadEngine(config=cfg, chain_fetcher=chain_fetcher)
        expiry = date(2026, 9, 23)
        long_leg = make_combo_leg(option_type="CALL", side=LegSide.BUY, strike=2.70, premium=0.30, expiry=expiry)
        short_leg = make_combo_leg(option_type="CALL", side=LegSide.SELL, strike=3.20, premium=0.05, expiry=expiry)
        # width = 0.50 > 0.30
        error = engine._validate_business_rules((long_leg, short_leg), fixed_spot_price)
        assert error == "VS_WIDTH_TOO_WIDE"


class TestVerticalSpreadExpiryMismatch:
    def test_vs_expiry_mismatch(self, chain_fetcher, fixed_spot_price, make_combo_leg):
        """两腿到期日不同返回 VS_EXPIRY_MISMATCH."""
        cfg = {"spread_width_min": 0.05, "spread_width_max": 0.30, "total_capital": 2_000_000}
        engine = VerticalSpreadEngine(config=cfg, chain_fetcher=chain_fetcher)
        long_leg = make_combo_leg(option_type="CALL", side=LegSide.BUY, strike=3.00, premium=0.10, expiry=date(2026, 9, 23))
        short_leg = make_combo_leg(option_type="CALL", side=LegSide.SELL, strike=3.10, premium=0.05, expiry=date(2026, 10, 28))
        error = engine._validate_business_rules((long_leg, short_leg), fixed_spot_price)
        assert error == "VS_EXPIRY_MISMATCH"


class TestVerticalSpreadReplaceSpot:
    def test_vs_replace_spot_rejected(self, vs_engine, underlying_code):
        """replace_spot=True 返回 VS_CANNOT_REPLACE_SPOT."""
        pos = {"spread_type": "debit_call", "replace_spot": True}
        result = vs_engine.generate(underlying_code, pos)
        assert result.error_code == "VS_CANNOT_REPLACE_SPOT"


class TestVerticalSpreadInvalidType:
    def test_vs_invalid_spread_type(self, vs_engine, underlying_code):
        """无效 spread_type 返回 VS_INVALID_SPREAD_TYPE."""
        pos = {"spread_type": "invalid_type", "replace_spot": False}
        result = vs_engine.generate(underlying_code, pos)
        assert result.error_code == "VS_INVALID_SPREAD_TYPE"


class TestVerticalSpreadLegCount:
    def test_vs_leg_count_mismatch(self, chain_fetcher, fixed_spot_price, make_combo_leg):
        """腿数 ≠ 2 返回 VS_LEG_COUNT_MISMATCH."""
        cfg = {"total_capital": 2_000_000}
        engine = VerticalSpreadEngine(config=cfg, chain_fetcher=chain_fetcher)
        leg = make_combo_leg()
        error = engine._validate_business_rules((leg,), fixed_spot_price)
        assert error == "VS_LEG_COUNT_MISMATCH"


class TestVerticalSpreadTypeMismatch:
    def test_vs_type_mismatch(self, chain_fetcher, fixed_spot_price, make_combo_leg):
        """两腿类型不同返回 VS_TYPE_MISMATCH."""
        cfg = {"spread_width_min": 0.05, "spread_width_max": 0.30, "total_capital": 2_000_000}
        engine = VerticalSpreadEngine(config=cfg, chain_fetcher=chain_fetcher)
        expiry = date(2026, 9, 23)
        long_leg = make_combo_leg(option_type="CALL", side=LegSide.BUY, strike=3.00, premium=0.10, expiry=expiry)
        short_leg = make_combo_leg(option_type="PUT", side=LegSide.SELL, strike=3.10, premium=0.05, expiry=expiry)
        error = engine._validate_business_rules((long_leg, short_leg), fixed_spot_price)
        assert error == "VS_TYPE_MISMATCH"


class TestVerticalSpreadWrongSide:
    def test_vs_wrong_side(self, chain_fetcher, fixed_spot_price, make_combo_leg):
        """腿方向错误返回 VS_WRONG_SIDE."""
        cfg = {"spread_width_min": 0.05, "spread_width_max": 0.30, "total_capital": 2_000_000}
        engine = VerticalSpreadEngine(config=cfg, chain_fetcher=chain_fetcher)
        expiry = date(2026, 9, 23)
        # 两腿都是 BUY
        long_leg = make_combo_leg(option_type="CALL", side=LegSide.BUY, strike=3.00, premium=0.10, expiry=expiry)
        short_leg = make_combo_leg(option_type="CALL", side=LegSide.BUY, strike=3.10, premium=0.05, expiry=expiry)
        error = engine._validate_business_rules((long_leg, short_leg), fixed_spot_price)
        assert error == "VS_WRONG_SIDE"