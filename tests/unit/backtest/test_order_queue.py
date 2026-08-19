"""OrderQueue 单元测试 — 8 用例覆盖 6 状态转移 + 不可变性 + 边界。

状态机覆盖:
    NOT_REPORTED → ALL_TRADED         (mark_filled)
    NOT_REPORTED → PART_TRADED        (mark_partial, 未累计满)
    PART_TRADED → ALL_TRADED          (mark_partial, 累计满自动转)
    NOT_REPORTED → CANCELLED          (cancel)
    NOT_REPORTED → REJECTED           (mark_rejected)

不可变性:
    - enqueue 后入参 order 对象不被修改
    - cancel/mark_filled/mark_partial/mark_rejected 产出新对象,原对象不变

边界:
    - 重复 order_id 抛 ValueError
    - 撤单已终止订单返回 False
    - mark_filled/mark_partial/mark_rejected 对不存在 order_id 抛 KeyError
"""
from __future__ import annotations

import pytest

from utils.backtest.order_queue import OrderQueue, OrderState
from utils.wt_structs import OrderData

# ============================================================
# 用例 1: FIFO 入队/出队
# ============================================================

def test_enqueue_and_pop_next_fifo(sample_buy_order: OrderData, sample_sell_order: OrderData) -> None:
    """入队后 pop_next 按 FIFO 顺序返回。"""
    # Arrange
    queue = OrderQueue()

    # Act
    queue.enqueue(sample_buy_order)
    queue.enqueue(sample_sell_order)

    # Assert
    assert queue.active_count == 2
    assert queue.pending_count == 2
    first = queue.pop_next()
    second = queue.pop_next()
    third = queue.pop_next()

    assert first is sample_buy_order
    assert second is sample_sell_order
    assert third is None
    assert queue.active_count == 2  # pop_next 不改 _active,只弹 _pending


# ============================================================
# 用例 2: 重复 order_id 抛 ValueError
# ============================================================

def test_enqueue_duplicate_raises(sample_buy_order: OrderData) -> None:
    """重复 order_id 抛 ValueError。"""
    # Arrange
    queue = OrderQueue()
    queue.enqueue(sample_buy_order)
    duplicate = OrderData(
        order_id=sample_buy_order.order_id,  # 同 ID
        code="000001.SZ",
        exchange="SZSE",
        direction="SELL",
    )

    # Act + Assert
    with pytest.raises(ValueError, match="重复 order_id"):
        queue.enqueue(duplicate)
    assert queue.active_count == 1


# ============================================================
# 用例 3: 撤单活动订单 → CANCELLED 新对象
# ============================================================

def test_cancel_active_order_returns_true(sample_buy_order: OrderData) -> None:
    """撤单活动订单返回 True,产出 CANCELLED 新对象,原对象不变。"""
    # Arrange
    queue = OrderQueue()
    queue.enqueue(sample_buy_order)
    original_status = sample_buy_order.status

    # Act
    result = queue.cancel(sample_buy_order.order_id)

    # Assert
    assert result is True
    assert queue.active_count == 0
    assert queue.cancelled_count == 1
    # 不可变: 原对象 status 不变
    assert sample_buy_order.status == original_status
    # cancelled 中是 NEW 对象
    cancelled = queue.get_order(sample_buy_order.order_id)
    assert cancelled is not None
    assert cancelled.status == OrderState.CANCELLED
    assert cancelled is not sample_buy_order  # 新对象


# ============================================================
# 用例 4: 撤单已终止订单返回 False
# ============================================================

def test_cancel_already_filled_returns_false(sample_buy_order: OrderData) -> None:
    """撤单已成交订单返回 False。"""
    # Arrange
    queue = OrderQueue()
    queue.enqueue(sample_buy_order)
    queue.mark_filled(sample_buy_order.order_id, fill_price=1800.0, fill_volume=100.0)

    # Act
    result = queue.cancel(sample_buy_order.order_id)

    # Assert
    assert result is False
    assert queue.cancelled_count == 0
    assert queue.completed_count == 1  # 仍为已成交


# ============================================================
# 用例 5: mark_filled → ALL_TRADED
# ============================================================

def test_mark_filled_transitions_to_all_traded(sample_buy_order: OrderData) -> None:
    """mark_filled 转 ALL_TRADED,traded_volume=volume。"""
    # Arrange
    queue = OrderQueue()
    queue.enqueue(sample_buy_order)

    # Act
    queue.mark_filled(sample_buy_order.order_id, fill_price=1800.0, fill_volume=100.0)

    # Assert
    assert queue.active_count == 0
    assert queue.completed_count == 1
    filled = queue.get_order(sample_buy_order.order_id)
    assert filled is not None
    assert filled.status == OrderState.ALL_TRADED
    assert filled.traded_volume == sample_buy_order.volume
    # 不可变: 原对象 status 不变
    assert sample_buy_order.status == "NOT_REPORTED"


# ============================================================
# 用例 6: mark_partial 累加 traded_volume → PART_TRADED
# ============================================================

def test_mark_partial_accumulates_traded_volume(large_buy_order: OrderData) -> None:
    """mark_partial 累加 traded_volume,未满 volume 时状态为 PART_TRADED。"""
    # Arrange
    queue = OrderQueue()
    queue.enqueue(large_buy_order)  # volume=1000

    # Act: 分两次部分成交,累计 300
    queue.mark_partial(large_buy_order.order_id, fill_price=4.0, fill_volume=200.0)
    queue.mark_partial(large_buy_order.order_id, fill_price=4.01, fill_volume=100.0)

    # Assert
    assert queue.active_count == 1  # 仍活动
    partial = queue.get_order(large_buy_order.order_id)
    assert partial is not None
    assert partial.status == OrderState.PART_TRADED
    assert partial.traded_volume == 300.0
    # 不可变: 原对象 traded_volume 不变
    assert large_buy_order.traded_volume == 0.0


# ============================================================
# 用例 7: mark_partial 累计满 → 自动转 ALL_TRADED
# ============================================================

def test_mark_partial_auto_transitions_to_all_traded(large_buy_order: OrderData) -> None:
    """mark_partial 累计 traded_volume >= volume 时自动转 ALL_TRADED。"""
    # Arrange
    queue = OrderQueue()
    queue.enqueue(large_buy_order)  # volume=1000

    # Act: 分两次,第二次超过剩余量(700+400=1100>1000)
    queue.mark_partial(large_buy_order.order_id, fill_price=4.0, fill_volume=700.0)
    queue.mark_partial(large_buy_order.order_id, fill_price=4.01, fill_volume=400.0)

    # Assert
    assert queue.active_count == 0  # 已转终态
    assert queue.completed_count == 1
    filled = queue.get_order(large_buy_order.order_id)
    assert filled is not None
    assert filled.status == OrderState.ALL_TRADED
    assert filled.traded_volume == 1000.0  # 钳制到 volume


# ============================================================
# 用例 8: 不可变性 — 入参对象不被修改
# ============================================================

def test_immutability_no_inplace_modification(sample_buy_order: OrderData) -> None:
    """所有状态变更操作不修改入参 OrderData 对象。"""
    # Arrange
    queue = OrderQueue()
    queue.enqueue(sample_buy_order)
    # 记录原始字段值
    orig_status = sample_buy_order.status
    orig_traded = sample_buy_order.traded_volume

    # Act: 执行所有状态变更操作(用拷贝避免污染)
    import dataclasses
    dataclasses.replace(sample_buy_order)
    queue.cancel(sample_buy_order.order_id)

    # 再次入队一个新订单测试 mark_filled
    new_order = dataclasses.replace(sample_buy_order, order_id="test-immut-002")
    queue.enqueue(new_order)
    queue.mark_filled(new_order.order_id, fill_price=1800.0, fill_volume=100.0)

    # Assert: 原对象字段未被修改
    assert sample_buy_order.status == orig_status
    assert sample_buy_order.traded_volume == orig_traded
    assert new_order.status == "NOT_REPORTED"  # 入队时未改
    assert new_order.traded_volume == 0.0  # mark_filled 未改入参

    # 但队列内的对象状态正确
    filled = queue.get_order(new_order.order_id)
    assert filled is not None
    assert filled.status == OrderState.ALL_TRADED
    assert filled.traded_volume == 100.0
    assert filled is not new_order  # 是新对象


# ============================================================
# 补充用例: mark_rejected 状态转移
# ============================================================

def test_mark_rejected_transitions_to_rejected(sample_buy_order: OrderData) -> None:
    """mark_rejected 转 REJECTED 终态。"""
    # Arrange
    queue = OrderQueue()
    queue.enqueue(sample_buy_order)

    # Act
    queue.mark_rejected(sample_buy_order.order_id, reason="PRICE_LIMIT")

    # Assert
    assert queue.active_count == 0
    assert queue.completed_count == 1  # rejected 也算 completed
    rejected = queue.get_order(sample_buy_order.order_id)
    assert rejected is not None
    assert rejected.status == OrderState.REJECTED
    # 不可变: 原对象不变
    assert sample_buy_order.status == "NOT_REPORTED"


# ============================================================
# 补充用例: KeyError 边界
# ============================================================

def test_mark_filled_nonexistent_raises() -> None:
    """mark_filled 对不存在 order_id 抛 KeyError。"""
    queue = OrderQueue()
    with pytest.raises(KeyError, match="订单不在活动队列"):
        queue.mark_filled("nonexistent", fill_price=100.0, fill_volume=10.0)


def test_mark_partial_nonexistent_raises() -> None:
    """mark_partial 对不存在 order_id 抛 KeyError。"""
    queue = OrderQueue()
    with pytest.raises(KeyError, match="订单不在活动队列"):
        queue.mark_partial("nonexistent", fill_price=100.0, fill_volume=10.0)


def test_mark_rejected_nonexistent_raises() -> None:
    """mark_rejected 对不存在 order_id 抛 KeyError。"""
    queue = OrderQueue()
    with pytest.raises(KeyError, match="订单不在活动队列"):
        queue.mark_rejected("nonexistent", reason="TEST")
