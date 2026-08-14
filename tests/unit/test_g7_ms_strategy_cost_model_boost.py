"""G7 boost: ms_strategy/src/backtest/cost_model.py 单元测试.

覆盖 CostConfig / CostModel / AlmgrenChrissCost 的全部公开接口,
包括佣金/印花税/过户费/市场冲击/融资成本/综合成本的核心路径与边界分支.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "ms_strategy"))

from ms_strategy.src.backtest.cost_model import (  # noqa: E402
    AlmgrenChrissCost,
    CostConfig,
    CostModel,
)

# ============================================================
# 1. CostConfig 默认值
# ============================================================


class TestCostConfig:
    def test_default_values(self):
        cfg = CostConfig()
        assert cfg.commission_stock == pytest.approx(0.00025)
        assert cfg.commission_futures == pytest.approx(0.000023)
        assert cfg.commission_options == 5.0
        assert cfg.stamp_duty == pytest.approx(0.001)
        assert cfg.transfer_fee == pytest.approx(0.00002)
        assert cfg.slippage_coef == pytest.approx(0.142)
        assert cfg.volatility_scaling is True
        assert cfg.margin_long == pytest.approx(0.06)
        assert cfg.margin_short == pytest.approx(0.06)
        assert cfg.repo == pytest.approx(0.018)

    def test_custom_values(self):
        cfg = CostConfig(commission_stock=0.001, stamp_duty=0.002)
        assert cfg.commission_stock == pytest.approx(0.001)
        assert cfg.stamp_duty == pytest.approx(0.002)


# ============================================================
# 2. CostModel.commission
# ============================================================


class TestCommission:
    def test_stock_buy(self):
        cm = CostModel()
        notional = 100_000.0
        cost = cm.commission(notional, "stock", "BUY")
        # 佣金 + 过户费, 无印花税
        expected = notional * 0.00025 + notional * 0.00002
        assert cost == pytest.approx(expected)

    def test_stock_sell(self):
        cm = CostModel()
        notional = 100_000.0
        cost = cm.commission(notional, "stock", "SELL")
        # 佣金 + 印花税 + 过户费
        expected = notional * 0.00025 + notional * 0.001 + notional * 0.00002
        assert cost == pytest.approx(expected)

    def test_futures(self):
        cm = CostModel()
        notional = 200_000.0
        cost = cm.commission(notional, "futures", "BUY")
        assert cost == pytest.approx(notional * 0.000023)

    def test_options(self):
        cm = CostModel()
        cost = cm.commission(0.0, "options", "BUY")
        assert cost == pytest.approx(5.0)

    def test_unknown_asset_type_returns_zero(self):
        cm = CostModel()
        assert cm.commission(100_000.0, "crypto", "BUY") == 0.0

    def test_zero_notional(self):
        cm = CostModel()
        assert cm.commission(0.0, "stock", "BUY") == 0.0


# ============================================================
# 3. CostModel.market_impact
# ============================================================


class TestMarketImpact:
    def test_zero_daily_volume_returns_zero(self):
        cm = CostModel()
        impact = cm.market_impact(qty=100, daily_volume=0, volatility=0.02, price=10.0)
        assert impact == 0.0

    def test_negative_daily_volume_returns_zero(self):
        cm = CostModel()
        impact = cm.market_impact(qty=100, daily_volume=-1, volatility=0.02, price=10.0)
        assert impact == 0.0

    def test_volatility_scaling_on(self):
        cfg = CostConfig(volatility_scaling=True)
        cm = CostModel(cfg)
        impact = cm.market_impact(qty=100, daily_volume=100_000, volatility=0.02, price=10.0)
        assert impact > 0
        # 验证公式: slippage_coef * vol * sqrt(qty/vol) * (vol/0.02) * price * qty
        participation = 100 / 100_000
        expected_bps = 0.142 * 0.02 * np.sqrt(participation) * (0.02 / 0.02)
        expected = expected_bps * 10.0 * 100
        assert impact == pytest.approx(expected)

    def test_volatility_scaling_off(self):
        cfg = CostConfig(volatility_scaling=False)
        cm = CostModel(cfg)
        impact = cm.market_impact(qty=100, daily_volume=100_000, volatility=0.02, price=10.0)
        participation = 100 / 100_000
        expected_bps = 0.142 * 0.02 * np.sqrt(participation)
        expected = expected_bps * 10.0 * 100
        assert impact == pytest.approx(expected)

    def test_large_participation(self):
        cm = CostModel()
        impact = cm.market_impact(qty=50_000, daily_volume=100_000, volatility=0.03, price=20.0)
        assert impact > 0


# ============================================================
# 4. CostModel.total_cost
# ============================================================


class TestTotalCost:
    def test_stock_buy_total(self):
        cm = CostModel()
        result = cm.total_cost(qty=100, price=10.0, daily_volume=100_000,
                               volatility=0.02, asset_type="stock", side="BUY")
        assert result["notional"] == pytest.approx(1000.0)
        assert result["commission"] > 0
        assert result["market_impact"] >= 0
        assert result["total_cost"] == pytest.approx(result["commission"] + result["market_impact"])
        assert result["bps"] == pytest.approx(result["total_cost"] / 1000.0)

    def test_zero_notional_bps_zero(self):
        cm = CostModel()
        result = cm.total_cost(qty=0, price=10.0, daily_volume=100_000,
                               volatility=0.02, asset_type="stock", side="BUY")
        assert result["notional"] == 0.0
        assert result["bps"] == 0.0
        assert result["total_cost"] == 0.0

    def test_keys_present(self):
        cm = CostModel()
        result = cm.total_cost(qty=100, price=10.0, daily_volume=100_000,
                               volatility=0.02, asset_type="stock", side="SELL")
        for key in ("commission", "market_impact", "total_cost", "bps", "notional"):
            assert key in result


# ============================================================
# 5. CostModel.financing_cost
# ============================================================


class TestFinancingCost:
    def test_long(self):
        cm = CostModel()
        cost = cm.financing_cost(notional=1_000_000, days=30, position_type="long")
        expected = 1_000_000 * 0.06 * (30 / 365)
        assert cost == pytest.approx(expected)

    def test_short(self):
        cm = CostModel()
        cost = cm.financing_cost(notional=1_000_000, days=30, position_type="short")
        expected = 1_000_000 * 0.06 * (30 / 365)
        assert cost == pytest.approx(expected)

    def test_repo(self):
        cm = CostModel()
        cost = cm.financing_cost(notional=1_000_000, days=30, position_type="repo")
        expected = 1_000_000 * 0.018 * (30 / 365)
        assert cost == pytest.approx(expected)

    def test_default_position_type_is_long(self):
        cm = CostModel()
        cost_default = cm.financing_cost(notional=1_000_000, days=30)
        cost_long = cm.financing_cost(notional=1_000_000, days=30, position_type="long")
        assert cost_default == pytest.approx(cost_long)

    def test_zero_days(self):
        cm = CostModel()
        assert cm.financing_cost(notional=1_000_000, days=0) == 0.0


# ============================================================
# 6. CostModel.trade_cost
# ============================================================


class TestTradeCost:
    def test_stock_buy(self):
        cm = CostModel()
        result = cm.trade_cost(symbol="000001", qty=100, price=10.0,
                               side="BUY", asset_type="stock", adv=100_000)
        assert result["symbol"] == "000001"
        assert result["side"] == "BUY"
        assert result["qty"] == 100
        assert result["price"] == pytest.approx(10.0)
        assert result["notional"] == pytest.approx(1000.0)
        assert result["stamp_duty"] == 0.0
        assert result["commission"] > 0
        assert result["slippage"] >= 0
        assert result["total"] == pytest.approx(result["commission"] + result["slippage"])

    def test_stock_sell_has_stamp_duty(self):
        cm = CostModel()
        result = cm.trade_cost(symbol="000001", qty=100, price=10.0,
                               side="SELL", asset_type="stock", adv=100_000)
        assert result["stamp_duty"] == pytest.approx(1000.0 * 0.001)
        # commission 应已扣除印花税
        assert result["commission"] > 0

    def test_futures(self):
        cm = CostModel()
        result = cm.trade_cost(symbol="IF2406", qty=1, price=4000.0,
                               side="BUY", asset_type="futures", adv=10_000)
        assert result["stamp_duty"] == 0.0
        assert result["commission"] > 0

    def test_options(self):
        cm = CostModel()
        result = cm.trade_cost(symbol="10004", qty=1, price=0.05,
                               side="BUY", asset_type="options", adv=1000)
        assert result["commission"] == pytest.approx(5.0)

    def test_adv_zero_uses_default_volume(self):
        cm = CostModel()
        result = cm.trade_cost(symbol="X", qty=100, price=10.0, adv=0)
        # adv=0 时 daily_volume = max(qty*100, 1) = 10000
        assert result["slippage"] >= 0

    def test_negative_qty_abs(self):
        cm = CostModel()
        result = cm.trade_cost(symbol="X", qty=-100, price=10.0, side="BUY")
        assert result["qty"] == 100

    def test_zero_qty(self):
        cm = CostModel()
        result = cm.trade_cost(symbol="X", qty=0, price=10.0, side="BUY")
        assert result["notional"] == 0.0
        assert result["bps"] == 0.0

    def test_lowercase_sell(self):
        cm = CostModel()
        result = cm.trade_cost(symbol="X", qty=100, price=10.0, side="sell")
        assert result["stamp_duty"] > 0


# ============================================================
# 7. AlmgrenChrissCost
# ============================================================


class TestAlmgrenChrissCost:
    def test_init_defaults(self):
        ac = AlmgrenChrissCost()
        assert ac.permanent_impact == pytest.approx(0.1)
        assert ac.temporary_impact == pytest.approx(0.15)

    def test_total_cost_with_permanent_and_temporary(self):
        ac = AlmgrenChrissCost(permanent_impact=0.1, temporary_impact=0.15)
        result = ac.total_cost(qty=100, price=10.0, daily_volume=100_000,
                               volatility=0.02, asset_type="stock", side="BUY",
                               time_horizon=1)
        assert "permanent_impact" in result
        assert "temporary_impact" in result
        assert result["permanent_impact"] > 0
        assert result["temporary_impact"] > 0
        assert result["total_cost"] > result["commission"] + result["market_impact"]

    def test_total_cost_zero_daily_volume(self):
        ac = AlmgrenChrissCost()
        result = ac.total_cost(qty=100, price=10.0, daily_volume=0,
                               volatility=0.02, asset_type="stock", side="BUY")
        # daily_volume=0 → participation=0 → permanent/temp=0
        assert result["permanent_impact"] == 0.0
        assert result["temporary_impact"] == 0.0

    def test_total_cost_zero_notional_bps_zero(self):
        ac = AlmgrenChrissCost()
        result = ac.total_cost(qty=0, price=10.0, daily_volume=100_000,
                               volatility=0.02, asset_type="stock", side="BUY")
        assert result["notional"] == 0.0
        assert result["bps"] == 0.0

    def test_time_horizon_affects_temporary(self):
        ac = AlmgrenChrissCost(temporary_impact=0.15)
        r1 = ac.total_cost(qty=100, price=10.0, daily_volume=100_000,
                           volatility=0.02, time_horizon=1)
        r4 = ac.total_cost(qty=100, price=10.0, daily_volume=100_000,
                           volatility=0.02, time_horizon=4)
        # 时间跨度越大, 临时冲击越小
        assert r1["temporary_impact"] > r4["temporary_impact"]


# ============================================================
# 8. CostModel 默认构造
# ============================================================


class TestCostModelInit:
    def test_default_config(self):
        cm = CostModel()
        assert cm.cfg.commission_stock == pytest.approx(0.00025)

    def test_none_config_uses_default(self):
        cm = CostModel(None)
        assert cm.cfg.commission_stock == pytest.approx(0.00025)

    def test_custom_config(self):
        cfg = CostConfig(commission_stock=0.0005)
        cm = CostModel(cfg)
        assert cm.cfg.commission_stock == pytest.approx(0.0005)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
