"""LatencyModel 单元测试 — 覆盖 Fixed/Random/Queue 三种延迟模型。

覆盖矩阵:
    FixedLatency:   零延迟、非零延迟、负数输入保护
    RandomLatency:  可复现性、边界值、多实例独立、不污染全局状态
    QueueLatency:   基础延迟、max cap、pending_count=0、大量积压
    LatencyModel:   ABC 不可直接实例化
"""
from __future__ import annotations

import random

import pytest

from utils.backtest.latency_model import (
    FixedLatency,
    LatencyModel,
    QueueLatency,
    RandomLatency,
)

# ============================================================
# LatencyModel ABC 测试
# ============================================================

def test_latency_model_is_abstract() -> None:
    """LatencyModel 是抽象类,不能直接实例化。"""
    with pytest.raises(TypeError):
        LatencyModel()  # type: ignore[call-arg]


def test_all_models_are_latency_model() -> None:
    """所有具体模型都是 LatencyModel 的子类。"""
    assert isinstance(FixedLatency(0), LatencyModel)
    assert isinstance(RandomLatency(1, 3), LatencyModel)
    assert isinstance(QueueLatency(1, 0.5, 10), LatencyModel)


# ============================================================
# FixedLatency 测试 (3 用例)
# ============================================================

def test_fixed_latency_zero_delay(sample_buy_order) -> None:
    """FixedLatency(0): 返回 0 延迟,用于与向量化对比验证。"""
    model = FixedLatency(latency_ticks=0)
    result = model.calculate_latency(sample_buy_order)
    assert result == 0
    assert isinstance(result, int)


def test_fixed_latency_nonzero_delay(sample_buy_order) -> None:
    """FixedLatency(5): 返回固定 5 延迟。"""
    model = FixedLatency(latency_ticks=5)
    result = model.calculate_latency(sample_buy_order, pending_count=10)
    assert result == 5  # 忽略 pending_count


def test_fixed_latency_negative_protection() -> None:
    """FixedLatency(-1): 负数输入被保护为 0。"""
    model = FixedLatency(latency_ticks=-1)
    assert model.latency_ticks == 0
    result = model.calculate_latency(None)  # type: ignore[arg-type]
    assert result == 0


def test_fixed_latency_ignores_all_params(sample_buy_order) -> None:
    """FixedLatency 忽略所有其他参数,总是返回固定值。"""
    model = FixedLatency(latency_ticks=7)
    # 传入各种参数,结果都应该是 7
    assert model.calculate_latency(sample_buy_order) == 7
    assert model.calculate_latency(sample_buy_order, pending_count=100) == 7
    assert model.calculate_latency(sample_buy_order, pending_count=9999) == 7


# ============================================================
# RandomLatency 测试 (5 用例)
# ============================================================

def test_random_latency_reproducibility(sample_buy_order) -> None:
    """相同 seed 产生相同结果(可复现)。"""
    model1 = RandomLatency(min_ticks=1, max_ticks=5, seed=42)
    model2 = RandomLatency(min_ticks=1, max_ticks=5, seed=42)

    # 连续调用,结果应完全一致
    for _ in range(100):
        r1 = model1.calculate_latency(sample_buy_order)
        r2 = model2.calculate_latency(sample_buy_order)
        assert r1 == r2


def test_random_latency_bounds(sample_buy_order) -> None:
    """返回值在 [min_ticks, max_ticks] 范围内。"""
    model = RandomLatency(min_ticks=2, max_ticks=10, seed=123)

    for _ in range(1000):
        result = model.calculate_latency(sample_buy_order)
        assert 2 <= result <= 10


def test_random_latency_different_seeds_give_different_patterns(
    sample_buy_order,
) -> None:
    """不同 seed 产生不同的随机序列(至少前几个不同)。"""
    model_a = RandomLatency(1, 100, seed=1)
    model_b = RandomLatency(1, 100, seed=999)

    seq_a = [model_a.calculate_latency(sample_buy_order) for _ in range(10)]
    seq_b = [model_b.calculate_latency(sample_buy_order) for _ in range(10)]

    # 不同 seed 至少有一个结果不同
    assert seq_a != seq_b


def test_random_latency_does_not_pollute_global_state() -> None:
    """RandomLatency 使用 per-instance RNG,不污染全局 random 状态。"""
    # 保存全局状态,获取"预期"的第一个随机数
    state_before = random.getstate()
    expected_value = random.random()

    # 恢复状态,创建 RandomLatency 实例并调用(不应影响全局)
    random.setstate(state_before)
    model = RandomLatency(0, 100, seed=42)
    _ = [model.calculate_latency(None) for _ in range(50)]  # type: ignore[arg-type]

    # 调用后获取实际的第一个随机数
    actual_value = random.random()

    # 验证: RandomLatency 调用不影响全局 RNG 序列
    assert expected_value == actual_value


def test_random_latency_zero_min() -> None:
    """min_ticks=0 允许返回 0 延迟。"""
    # 种子 0 可能产生 0
    for seed in range(1000):
        model = RandomLatency(min_ticks=0, max_ticks=1, seed=seed)
        result = model.calculate_latency(None)  # type: ignore[arg-type]
        if result == 0:
            break
    else:
        pytest.fail("无法找到返回 0 的 seed,边界逻辑可能有问题")


def test_random_latency_single_value_range() -> None:
    """min=max 时,总是返回该值。"""
    model = RandomLatency(min_ticks=5, max_ticks=5, seed=42)
    for _ in range(50):
        result = model.calculate_latency(None)  # type: ignore[arg-type]
        assert result == 5


# ============================================================
# QueueLatency 测试 (6 用例)
# ============================================================

def test_queue_latency_no_pending_orders(sample_buy_order) -> None:
    """pending_count=0 时,返回 base_ticks。"""
    model = QueueLatency(base_ticks=1, per_pending_order_ticks=0.5, max_ticks=10)
    result = model.calculate_latency(sample_buy_order, pending_count=0)
    assert result == 1


def test_queue_latency_scales_with_pending(sample_buy_order) -> None:
    """pending_count 增加时,延迟相应增加。"""
    model = QueueLatency(base_ticks=1, per_pending_order_ticks=1.0, max_ticks=20)

    # 基础: pending=0 → 1
    assert model.calculate_latency(sample_buy_order, 0) == 1
    # pending=5 → 1 + 1.0*5 = 6
    assert model.calculate_latency(sample_buy_order, 5) == 6
    # pending=10 → 1 + 1.0*10 = 11
    assert model.calculate_latency(sample_buy_order, 10) == 11


def test_queue_latency_capped_at_max(sample_buy_order) -> None:
    """延迟被限制在 max_ticks 上限。"""
    model = QueueLatency(base_ticks=1, per_pending_order_ticks=5.0, max_ticks=10)

    # pending=100 → 1 + 5*100 = 501,但 max=10,应被截断
    result = model.calculate_latency(sample_buy_order, pending_count=100)
    assert result == 10


def test_queue_latency_never_below_base(sample_buy_order) -> None:
    """延迟永远不低于 base_ticks。"""
    model = QueueLatency(base_ticks=5, per_pending_order_ticks=0.1, max_ticks=10)

    # 即使 pending_count=0,也应返回 base_ticks
    result = model.calculate_latency(sample_buy_order, pending_count=0)
    assert result >= 5


def test_queue_latency_fractional_per_order(sample_buy_order) -> None:
    """per_pending_order_ticks 为小数时,使用 int() 截断。"""
    model = QueueLatency(base_ticks=0, per_pending_order_ticks=0.3, max_ticks=10)

    # pending=1 → 0 + 0.3*1 = 0.3, int → 0,但 base=0,应返回 0
    assert model.calculate_latency(sample_buy_order, 1) == 0
    # pending=4 → 0 + 0.3*4 = 1.2, int → 1
    assert model.calculate_latency(sample_buy_order, 4) == 1
    # pending=5 → 0 + 0.3*5 = 1.5, int → 1
    assert model.calculate_latency(sample_buy_order, 5) == 1
    # pending=7 → 0 + 0.3*7 = 2.1, int → 2
    assert model.calculate_latency(sample_buy_order, 7) == 2


def test_queue_latency_zero_per_pending(sample_buy_order) -> None:
    """per_pending_order_ticks=0 时,总是返回 base_ticks。"""
    model = QueueLatency(base_ticks=2, per_pending_order_ticks=0.0, max_ticks=10)

    for count in range(100):
        result = model.calculate_latency(sample_buy_order, pending_count=count)
        assert result == 2


def test_queue_latency_max_equals_base(sample_buy_order) -> None:
    """max_ticks=base_ticks 时,总是返回 base_ticks。"""
    model = QueueLatency(base_ticks=3, per_pending_order_ticks=10.0, max_ticks=3)

    for count in range(100):
        result = model.calculate_latency(sample_buy_order, pending_count=count)
        assert result == 3


def test_queue_latency_default_values(sample_buy_order) -> None:
    """默认参数: base=1, per=0.5, max=10。"""
    model = QueueLatency()
    assert model.calculate_latency(sample_buy_order, 0) == 1
    assert model.calculate_latency(sample_buy_order, 2) == 2  # 1 + 0.5*2 = 2
    assert model.calculate_latency(sample_buy_order, 20) == 10  # capped at max


# ============================================================
# 可选参数兼容性测试
# ============================================================

def test_models_accept_none_order() -> None:
    """所有模型接受 None 作为 order 参数(接口兼容性)。"""
    models = [
        FixedLatency(0),
        RandomLatency(1, 3),
        QueueLatency(1, 0.5, 10),
    ]
    for model in models:
        result = model.calculate_latency(None)  # type: ignore[arg-type]
        assert isinstance(result, int)
        assert result >= 0


def test_models_with_market_event_param(sample_bar, sample_buy_order) -> None:
    """market_event 参数被接受但当前不影响结果。"""
    model = FixedLatency(latency_ticks=2)
    result = model.calculate_latency(sample_buy_order, market_event=sample_bar)
    assert result == 2  # market_event 当前不影响结果
