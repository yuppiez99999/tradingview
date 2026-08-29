"""T05: SimulatedBroker Almgren-Chriss 滑点模型单元测试.

验证点:
    1. 固定滑点 fallback (无 ADV 时)
    2. Almgren-Chriss 平方根模型 (有 ADV + 波动率时)
    3. 滑点随参与率单调递增
    4. 滑点随波动率单调递增
    5. 滑点上下限保护
    6. set_market_context 集成
    7. wait_fill 实际成交价格反映滑点
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# BUG 修复: 原代码在模块级把 ms_strategy 插入 sys.path 最前面, 会污染后续测试的 src 模块解析
# (broker_adapters 的 fallback `from src.bridges.broker_adapter import ...` 会错误绑定到 ms_strategy/src).
# 改用 pytest 的 conftest.py 统一管理 sys.path, 模块级只做最小化的导入路径补充.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
# ms_strategy 路径插入到末尾 (而非开头), 避免覆盖 v8.3_institutional/src 的优先级
_ms_path = str(PROJECT_ROOT / "ms_strategy")
if _ms_path not in sys.path:
    sys.path.append(_ms_path)

from ms_strategy.src.execution.broker_api import SimulatedBroker  # noqa: E402


class TestSlippageFallback:
    """1. 固定滑点 fallback (无 ADV 时)."""

    def test_no_adv_returns_fixed_slippage(self):
        """未设置 ADV 时, 回退到固定 slippage_bps."""
        broker = SimulatedBroker(slippage_bps=3.0)
        broker._prices["TEST"] = 10.0
        # 未调用 set_price 或 set_market_context, ADV=0
        slip_bps = broker._compute_slippage_bps("TEST", qty=100)
        assert slip_bps == 3.0, f"无 ADV 时应返回固定滑点 3.0, 实际 {slip_bps}"

    def test_zero_adv_returns_fixed_slippage(self):
        """ADV=0 时, 回退到固定滑点."""
        broker = SimulatedBroker(slippage_bps=2.5)
        broker.set_price("TEST", 10.0, volume=0, volatility=0.02)
        slip_bps = broker._compute_slippage_bps("TEST", qty=100)
        assert slip_bps == 2.5

    def test_zero_volatility_returns_fixed_slippage(self):
        """波动率=0 时, 回退到固定滑点."""
        broker = SimulatedBroker(slippage_bps=2.0)
        broker.set_price("TEST", 10.0, volume=100000, volatility=0.0)
        slip_bps = broker._compute_slippage_bps("TEST", qty=100)
        assert slip_bps == 2.0


class TestAlmgrenChrissModel:
    """2. Almgren-Chriss 平方根模型 (有 ADV + 波动率时)."""

    def test_small_order_low_slippage(self):
        """小订单 (低参与率) 应该有较低的滑点."""
        broker = SimulatedBroker(slippage_bps=2.0, slippage_coef=0.142)
        # ADV=1,000,000, 订单=1000, 参与率=0.1%
        broker.set_market_context("TEST", price=10.0, adv=1_000_000, volatility=0.02)
        slip_bps = broker._compute_slippage_bps("TEST", qty=1000)
        # η × σ × √(0.001) × 1.0 × 10000 = 0.142 × 0.02 × 0.0316 × 1.0 × 10000 ≈ 0.897 bps
        assert 0.5 < slip_bps < 1.5, f"小订单滑点应在 0.5-1.5 bps, 实际 {slip_bps}"

    def test_large_order_high_slippage(self):
        """大订单 (高参与率) 应该有较高的滑点."""
        broker = SimulatedBroker(slippage_bps=2.0, slippage_coef=0.142)
        # ADV=100,000, 订单=10,000, 参与率=10%
        broker.set_market_context("TEST", price=10.0, adv=100_000, volatility=0.02)
        slip_bps = broker._compute_slippage_bps("TEST", qty=10000)
        # η × σ × √(0.1) × 1.0 × 10000 = 0.142 × 0.02 × 0.316 × 1.0 × 10000 ≈ 8.97 bps
        assert 5.0 < slip_bps < 15.0, f"大订单滑点应在 5-15 bps, 实际 {slip_bps}"

    def test_high_volatility_amplifies_slippage(self):
        """高波动率应该放大滑点 (vol_scaling = vol/0.02)."""
        broker = SimulatedBroker(slippage_bps=2.0, slippage_coef=0.142)
        # 同样参与率, 但波动率 4% (2x 基准)
        broker.set_market_context("TEST", price=10.0, adv=100_000, volatility=0.04)
        slip_bps_high_vol = broker._compute_slippage_bps("TEST", qty=10000)

        broker.set_market_context("TEST", price=10.0, adv=100_000, volatility=0.02)
        slip_bps_normal_vol = broker._compute_slippage_bps("TEST", qty=10000)

        # 高波动率应该使滑点放大 ~4x (vol^2, 因为 vol×vol_scaling = vol×(vol/0.02))
        ratio = slip_bps_high_vol / slip_bps_normal_vol
        assert 3.0 < ratio < 5.0, f"高波动率应放大滑点 3-5x, 实际 {ratio:.2f}x"


class TestMonotonicity:
    """3. 滑点随参与率单调递增."""

    def test_slippage_increases_with_qty(self):
        """订单越大, 滑点越高 (固定 ADV)."""
        broker = SimulatedBroker(slippage_bps=2.0, slippage_coef=0.142)
        broker.set_market_context("TEST", price=10.0, adv=100_000, volatility=0.02)

        slips = []
        for qty in [100, 500, 1000, 5000, 10000]:
            slip = broker._compute_slippage_bps("TEST", qty=qty)
            slips.append(slip)

        # 验证单调递增
        for i in range(1, len(slips)):
            assert (
                slips[i] > slips[i - 1]
            ), f"qty 增大但滑点未递增: slips[{i-1}]={slips[i-1]}, slips[{i}]={slips[i]}"

    def test_slippage_decreases_with_adv(self):
        """ADV 越大 (流动性越好), 滑点越低 (固定 qty)."""
        broker = SimulatedBroker(slippage_bps=2.0, slippage_coef=0.142)

        slips = []
        for adv in [10_000, 50_000, 100_000, 500_000, 1_000_000]:
            broker.set_market_context("TEST", price=10.0, adv=adv, volatility=0.02)
            slip = broker._compute_slippage_bps("TEST", qty=1000)
            slips.append(slip)

        # 验证单调递减
        for i in range(1, len(slips)):
            assert (
                slips[i] < slips[i - 1]
            ), f"ADV 增大但滑点未递减: slips[{i-1}]={slips[i-1]}, slips[{i}]={slips[i]}"


class TestSlippageBounds:
    """5. 滑点上下限保护."""

    def test_slippage_upper_bound(self):
        """极端参与率 (100%) 应触发上限保护 (100 bps)."""
        broker = SimulatedBroker(slippage_bps=2.0, slippage_coef=0.142)
        # qty = ADV, 参与率 100%, 但数学公式算出来不会超过 100 bps
        broker.set_market_context("TEST", price=10.0, adv=1000, volatility=0.05)
        slip_bps = broker._compute_slippage_bps("TEST", qty=1000)
        assert slip_bps <= 100.0, f"滑点应 <= 100 bps, 实际 {slip_bps}"

    def test_slippage_upper_bound_extreme(self):
        """极端情况: qty > ADV 10x, 仍受 100 bps 上限保护."""
        broker = SimulatedBroker(slippage_bps=2.0, slippage_coef=0.142)
        broker.set_market_context("TEST", price=10.0, adv=1000, volatility=0.1)
        slip_bps = broker._compute_slippage_bps("TEST", qty=10000)
        assert slip_bps == 100.0, f"极端滑点应被限制为 100 bps, 实际 {slip_bps}"

    def test_slippage_lower_bound(self):
        """极小订单应触发下限保护 (固定 slippage_bps × 10%)."""
        broker = SimulatedBroker(slippage_bps=5.0, slippage_coef=0.142)
        # 极小订单, 数学公式算出的滑点可能低于 0.5 bps
        broker.set_market_context("TEST", price=10.0, adv=10_000_000, volatility=0.01)
        slip_bps = broker._compute_slippage_bps("TEST", qty=10)
        # 下限 = 5.0 × 0.1 = 0.5 bps
        assert slip_bps >= 0.5, f"滑点应 >= 0.5 bps (下限保护), 实际 {slip_bps}"


class TestSetMarketContext:
    """6. set_market_context 集成."""

    def test_set_market_context_stores_all_data(self):
        broker = SimulatedBroker()
        broker.set_market_context("AAPL", price=150.0, adv=5_000_000, volatility=0.025)

        assert broker._prices["AAPL"] == 150.0
        assert broker._volumes["AAPL"] == 5_000_000
        assert broker._volatilities["AAPL"] == 0.025

    def test_set_price_with_volatility(self):
        """set_price 的 volatility 参数应被存储."""
        broker = SimulatedBroker()
        broker.set_price("TEST", price=20.0, volume=200_000, volatility=0.03)

        assert broker._volatilities["TEST"] == 0.03

    def test_set_price_default_volatility(self):
        """set_price 未传 volatility 时应默认 0.02."""
        broker = SimulatedBroker()
        broker.set_price("TEST", price=20.0, volume=200_000)

        assert broker._volatilities["TEST"] == 0.02


class TestWaitFillIntegration:
    """7. wait_fill 实际成交价格反映滑点."""

    def test_buy_fill_price_includes_slippage(self):
        """买入成交价 = 价格 × (1 + slip)."""
        broker = SimulatedBroker(slippage_bps=10.0)  # 10 bps = 0.1%
        broker.set_market_context("TEST", price=100.0, adv=1_000_000, volatility=0.02)
        # 使用较大的订单触发明显滑点
        order = broker.place("TEST", qty=50000, side="BUY")
        result = broker.wait_fill(order)

        assert result is not None
        fill_price = result["price"]
        # 买入价应高于基准价 100.0
        assert fill_price > 100.0, f"买入价应 > 100.0 (含滑点), 实际 {fill_price}"

    def test_sell_fill_price_includes_slippage(self):
        """卖出成交价 = 价格 × (1 - slip)."""
        broker = SimulatedBroker(slippage_bps=10.0)
        broker.set_market_context("TEST", price=100.0, adv=1_000_000, volatility=0.02)
        order = broker.place("TEST", qty=50000, side="SELL")
        result = broker.wait_fill(order)

        assert result is not None
        fill_price = result["price"]
        # 卖出价应低于基准价 100.0
        assert fill_price < 100.0, f"卖出价应 < 100.0 (含滑点), 实际 {fill_price}"

    def test_fallback_slippage_when_no_context(self):
        """未设置 market_context 时, 使用固定 slippage_bps."""
        broker = SimulatedBroker(slippage_bps=5.0)
        # 只设置价格, 不设置 ADV/volatility
        broker._prices["TEST"] = 100.0
        order = broker.place("TEST", qty=100, side="BUY")
        result = broker.wait_fill(order)

        assert result is not None
        # 5 bps = 0.05%, 买入价 = 100 × 1.0005 = 100.05
        expected = 100.0 * 1.0005
        assert (
            abs(result["price"] - expected) < 0.001
        ), f"固定滑点成交价应为 {expected}, 实际 {result['price']}"


class TestBackwardCompatibility:
    """向后兼容性测试."""

    def test_default_constructor_unchanged(self):
        """默认构造函数参数不变, 旧代码可正常使用."""
        broker = SimulatedBroker()
        assert broker.slippage_bps == 2.0
        assert broker.slippage_coef == 0.142  # 新参数有默认值
        assert broker.capital == 5_000_000
        assert broker.commission_stock == 0.00025

    def test_old_set_price_signature_compatible(self):
        """旧版 set_price(symbol, price, volume) 仍可调用."""
        broker = SimulatedBroker()
        # 不传 volatility, 应该使用默认值
        broker.set_price("TEST", 10.0, 100000)
        assert broker._prices["TEST"] == 10.0
        assert broker._volumes["TEST"] == 100000
        assert broker._volatilities["TEST"] == 0.02  # 默认值


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
