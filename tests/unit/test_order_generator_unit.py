"""test_order_generator_unit.py — OrderGenerator 订单生成器单元测试

覆盖要点:
    - Order / OrderBatch dataclass
    - generate 基本流程 (buy/sell)
    - 零权重跳过
    - 无效价格跳过
    - 单笔上限截断
    - 最小交易单位 lot_size 取整
    - 空信号
    - current_positions 调仓
"""
from __future__ import annotations

import pytest

from utils.order_generator import Order, OrderBatch, OrderGenerator


# ============================================================
# Dataclass
# ============================================================


class TestDataclass:
    @pytest.mark.unit
    def test_order_defaults(self):
        o = Order(symbol="600519", side="buy", quantity=100)
        assert o.price == 0.0
        assert o.order_type == "market"
        assert o.reason == ""
        assert o.tags == {}

    @pytest.mark.unit
    def test_order_with_tags(self):
        o = Order(symbol="300750", side="sell", quantity=200, price=50.0, tags={"w": 0.1})
        assert o.price == 50.0
        assert o.tags == {"w": 0.1}

    @pytest.mark.unit
    def test_order_batch_defaults(self):
        b = OrderBatch(batch_id="123", orders=[])
        assert b.total_amount == 0.0
        assert b.created_at == ""
        assert b.dry_run is True


# ============================================================
# generate 基本流程
# ============================================================


class TestGenerate:
    @pytest.mark.unit
    def test_buy_order(self):
        gen = OrderGenerator(lot_size=100)
        batch = gen.generate(
            signals={"600519": 0.1},
            total_capital=1_000_000,
            prices={"600519": 100.0},
        )
        assert len(batch.orders) == 1
        assert batch.orders[0].side == "buy"
        assert batch.orders[0].symbol == "600519"
        assert batch.orders[0].quantity > 0
        assert batch.orders[0].quantity % 100 == 0  # lot_size 对齐

    @pytest.mark.unit
    def test_sell_order(self):
        gen = OrderGenerator(lot_size=100)
        batch = gen.generate(
            signals={"600519": -0.1},
            total_capital=1_000_000,
            prices={"600519": 100.0},
        )
        assert len(batch.orders) == 1
        assert batch.orders[0].side == "sell"

    @pytest.mark.unit
    def test_zero_weight_skipped(self):
        gen = OrderGenerator()
        batch = gen.generate(
            signals={"600519": 0.0},
            prices={"600519": 100.0},
        )
        assert len(batch.orders) == 0

    @pytest.mark.unit
    def test_near_zero_weight_skipped(self):
        """abs(weight) < 1e-6 跳过"""
        gen = OrderGenerator()
        batch = gen.generate(
            signals={"600519": 1e-7},
            prices={"600519": 100.0},
        )
        assert len(batch.orders) == 0

    @pytest.mark.unit
    def test_no_price_skipped(self):
        gen = OrderGenerator()
        batch = gen.generate(
            signals={"600519": 0.1},
            prices={},  # 无价格
        )
        assert len(batch.orders) == 0

    @pytest.mark.unit
    def test_zero_price_skipped(self):
        gen = OrderGenerator()
        batch = gen.generate(
            signals={"600519": 0.1},
            prices={"600519": 0.0},
        )
        assert len(batch.orders) == 0

    @pytest.mark.unit
    def test_empty_signals(self):
        gen = OrderGenerator()
        batch = gen.generate(signals={}, prices={})
        assert len(batch.orders) == 0
        assert batch.total_amount == 0.0

    @pytest.mark.unit
    def test_total_amount_calculation(self):
        gen = OrderGenerator(lot_size=100)
        batch = gen.generate(
            signals={"600519": 0.2},
            total_capital=1_000_000,
            prices={"600519": 50.0},
        )
        expected = sum(o.quantity * o.price for o in batch.orders)
        assert batch.total_amount == pytest.approx(expected)

    @pytest.mark.unit
    def test_dry_run_flag(self):
        gen = OrderGenerator()
        batch = gen.generate(
            signals={"600519": 0.1},
            prices={"600519": 100.0},
        )
        assert batch.dry_run is True


# ============================================================
# 调仓 (current_positions)
# ============================================================


class TestRebalance:
    @pytest.mark.unit
    def test_reduce_position(self):
        """目标权重 < 当前持仓 → 卖出"""
        gen = OrderGenerator(lot_size=100)
        batch = gen.generate(
            signals={"600519": 0.05},
            current_positions={"600519": 100_000},  # 当前 10%
            total_capital=1_000_000,
            prices={"600519": 100.0},
        )
        assert len(batch.orders) == 1
        assert batch.orders[0].side == "sell"

    @pytest.mark.unit
    def test_increase_position(self):
        """目标权重 > 当前持仓 → 买入"""
        gen = OrderGenerator(lot_size=100)
        batch = gen.generate(
            signals={"600519": 0.2},
            current_positions={"600519": 50_000},  # 当前 5%
            total_capital=1_000_000,
            prices={"600519": 100.0},
        )
        assert len(batch.orders) == 1
        assert batch.orders[0].side == "buy"

    @pytest.mark.unit
    def test_no_trade_needed(self):
        """目标权重 ≈ 当前持仓 → 无订单 (quantity 取整为 0)"""
        gen = OrderGenerator(lot_size=100)
        batch = gen.generate(
            signals={"600519": 0.1},
            current_positions={"600519": 100_000},  # 完全匹配
            total_capital=1_000_000,
            prices={"600519": 100.0},
        )
        assert len(batch.orders) == 0


# ============================================================
# 单笔上限
# ============================================================


class TestMaxSingleOrder:
    @pytest.mark.unit
    def test_cap_at_max(self):
        """delta_value > max_single_order_value → 截断"""
        gen = OrderGenerator(lot_size=100, max_single_order_value=100_000)
        batch = gen.generate(
            signals={"600519": 1.0},  # 目标 100 万
            total_capital=1_000_000,
            prices={"600519": 100.0},
        )
        assert len(batch.orders) == 1
        # delta_value 截断到 100_000, quantity = 100_000 / 100 / 100 * 100 = 1000
        assert batch.orders[0].quantity <= 1000

    @pytest.mark.unit
    def test_negative_cap_at_max(self):
        """卖出方向也截断"""
        gen = OrderGenerator(lot_size=100, max_single_order_value=100_000)
        batch = gen.generate(
            signals={"600519": -1.0},
            current_positions={"600519": 1_000_000},
            total_capital=1_000_000,
            prices={"600519": 100.0},
        )
        assert len(batch.orders) == 1
        assert batch.orders[0].side == "sell"
        assert batch.orders[0].quantity <= 1000


# ============================================================
# lot_size 取整
# ============================================================


class TestLotSize:
    @pytest.mark.unit
    def test_quantity_aligned_to_lot(self):
        gen = OrderGenerator(lot_size=100)
        batch = gen.generate(
            signals={"600519": 0.123},  # 非整除
            total_capital=1_000_000,
            prices={"600519": 33.33},
        )
        for o in batch.orders:
            assert o.quantity % 100 == 0

    @pytest.mark.unit
    def test_lot_size_200(self):
        gen = OrderGenerator(lot_size=200)
        batch = gen.generate(
            signals={"600519": 0.3},
            total_capital=1_000_000,
            prices={"600519": 50.0},
        )
        for o in batch.orders:
            assert o.quantity % 200 == 0

    @pytest.mark.unit
    def test_small_delta_no_order(self):
        """delta 太小, 取整后 quantity=0 → 无订单"""
        gen = OrderGenerator(lot_size=100)
        batch = gen.generate(
            signals={"600519": 0.001},  # 目标 1000 元
            total_capital=1_000_000,
            prices={"600519": 100.0},
        )
        # 1000 / 100 / 100 = 0.1 → int(0.1) * 100 = 0
        assert len(batch.orders) == 0


# ============================================================
# 多标的
# ============================================================


class TestMultiSymbol:
    @pytest.mark.unit
    def test_multiple_signals(self):
        gen = OrderGenerator(lot_size=100)
        batch = gen.generate(
            signals={"600519": 0.1, "300750": -0.05, "688981": 0.08},
            total_capital=1_000_000,
            prices={"600519": 100.0, "300750": 200.0, "688981": 50.0},
        )
        symbols = {o.symbol for o in batch.orders}
        assert "600519" in symbols
        assert "300750" in symbols
        assert "688981" in symbols

    @pytest.mark.unit
    def test_mixed_buy_sell(self):
        gen = OrderGenerator(lot_size=100)
        batch = gen.generate(
            signals={"600519": 0.1, "300750": -0.1},
            total_capital=1_000_000,
            prices={"600519": 100.0, "300750": 100.0},
        )
        sides = {o.side for o in batch.orders}
        assert "buy" in sides
        assert "sell" in sides