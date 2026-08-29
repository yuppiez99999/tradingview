"""backtest 模块 2026-08-24 审查回归测试 — 修复 BT-2/BT-6/BT-8

覆盖:
    BT-2/BT-6  market_impact 大单 participation 钳制到 (0,1], 冲击成本不爆炸
    BT-8       market_impact 入口防御负 qty/负 price/负 volatility → 返回 0 非 NaN
"""

from __future__ import annotations

import numpy as np

from ms_strategy.src.backtest.cost_aware_backtest import CostAwareBacktest
from ms_strategy.src.backtest.cost_model import CostConfig, CostModel


# ---- BT-2/BT-6: participation 钳制 ----
class TestMarketImpactClamp:
    def setup_method(self):
        self.cm = CostModel(CostConfig())
        self.cab = CostAwareBacktest()

    def test_large_order_clamped(self):
        """BT-2/BT-6: qty > daily_volume 时 participation 钳到 1, 冲击有限."""
        impact = self.cm.market_impact(
            qty=200_000, daily_volume=100_000, volatility=0.02, price=10.0
        )
        # price*qty = 2,000,000; impact_bps 上限 = 0.142*0.02*sqrt(1)*(0.02/0.02) = 0.00284
        # impact = 0.00284 * 2,000,000 = 5680
        assert 0 < impact <= 5680 * 1.01
        assert np.isfinite(impact)

    def test_normal_order(self):
        """BT-2: 正常参与率 (1%) 冲击合理."""
        impact = self.cm.market_impact(
            qty=1000, daily_volume=100_000, volatility=0.02, price=10.0
        )
        assert 0 < impact < 100  # 10% 内

    def test_cost_aware_simplified_clamp(self):
        """BT-2: CostAwareBacktest 简化版也钳制."""
        costs = self.cab.compute_trade_cost(
            notional=2_000_000,
            side="BUY",
            qty=200_000,
            daily_volume=100_000,
            volatility=0.02,
            price=10.0,
        )
        assert np.isfinite(costs["market_impact"])
        assert costs["market_impact"] > 0


# ---- BT-8: 入口防御 ----
class TestMarketImpactGuard:
    def setup_method(self):
        self.cm = CostModel(CostConfig())

    def test_negative_qty(self):
        """BT-8: 负 qty 返回 0 而非 NaN."""
        assert (
            self.cm.market_impact(
                qty=-1000, daily_volume=100_000, volatility=0.02, price=10.0
            )
            == 0.0
        )

    def test_zero_price(self):
        """BT-8: price<=0 返回 0."""
        assert (
            self.cm.market_impact(
                qty=1000, daily_volume=100_000, volatility=0.02, price=0.0
            )
            == 0.0
        )

    def test_negative_volatility(self):
        """BT-8: 负 volatility 返回 0 而非负冲击."""
        assert (
            self.cm.market_impact(
                qty=1000, daily_volume=100_000, volatility=-0.02, price=10.0
            )
            == 0.0
        )

    def test_zero_daily_volume(self):
        """BT-8: daily_volume<=0 返回 0."""
        assert (
            self.cm.market_impact(qty=1000, daily_volume=0, volatility=0.02, price=10.0)
            == 0.0
        )
