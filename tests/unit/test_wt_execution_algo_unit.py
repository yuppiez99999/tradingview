"""wt_execution_algo 单元测试 — WonderTrader 风格执行算法

覆盖:
- _get_adaptive_execution_params: 自适应参数计算
- MinImpactExecutor: 最小冲击拆单
- TWAPExecutor: 时间加权平均
- VWAPExecutor: 成交量加权平均
- OrderExecutor: 统一执行器
- split_order / compare_execution / execute_order_with_algorithm 便捷函数
"""

from __future__ import annotations

from unittest.mock import patch

from utils.wt_execution_algo import (
    MinImpactExecutor,
    OrderExecutor,
    TWAPExecutor,
    VWAPExecutor,
    _get_adaptive_execution_params,
    compare_execution,
    execute_order_with_algorithm,
    split_order,
)

# ============================================================
# _get_adaptive_execution_params
# ============================================================


class TestGetAdaptiveParams:
    """_get_adaptive_execution_params 自适应参数测试"""

    def test_normal_market(self):
        """正常市场: depth_ratio 低, 波动率低"""
        r = _get_adaptive_execution_params(100_000, 10, 1_000_000, 0.02)
        assert r["max_participation_pct"] == 0.15
        assert r["execution_window_minutes"] == 30
        assert r["interval_minutes"] == 5
        assert r["delay_factor"] == 1.0
        assert r["shallow_market"] is False
        assert r["high_volatility"] is False

    def test_shallow_market(self):
        """深度不足: depth_ratio > 0.1"""
        r = _get_adaptive_execution_params(1_000_000, 10, 100_000, 0.02)
        assert r["shallow_market"] is True
        assert r["max_participation_pct"] == 0.08

    def test_high_volatility(self):
        """高波动: volatility > 0.05"""
        r = _get_adaptive_execution_params(100_000, 10, 1_000_000, 0.08)
        assert r["high_volatility"] is True
        assert r["max_participation_pct"] == 0.08

    def test_shallow_and_high_vol(self):
        """深度不足 + 高波动"""
        r = _get_adaptive_execution_params(1_000_000, 10, 100_000, 0.08)
        assert r["shallow_market"] is True
        assert r["high_volatility"] is True
        assert r["execution_window_minutes"] == 60
        assert r["interval_minutes"] == 10
        assert r["delay_factor"] == 1.8

    def test_medium_depth_ratio(self):
        """中等深度: 0.05 < depth_ratio < 0.1"""
        r = _get_adaptive_execution_params(600_000, 10, 1_000_000, 0.02)
        assert r["max_participation_pct"] == 0.10

    def test_zero_avg_daily_volume(self):
        """日均成交量为 0 → 深度不足"""
        r = _get_adaptive_execution_params(100_000, 10, 0, 0.02)
        assert r["shallow_market"] is True

    def test_zero_ref_price(self):
        """参考价为 0 → depth_ratio = 0"""
        r = _get_adaptive_execution_params(100_000, 0, 1_000_000, 0.02)
        assert r["depth_ratio"] == 0.0

    def test_returns_all_keys(self):
        r = _get_adaptive_execution_params(100_000, 10, 1_000_000, 0.02)
        for key in [
            "max_participation_pct",
            "execution_window_minutes",
            "interval_minutes",
            "delay_factor",
            "depth_ratio",
            "shallow_market",
            "high_volatility",
        ]:
            assert key in r


# ============================================================
# MinImpactExecutor
# ============================================================


class TestMinImpactExecutor:
    """MinImpactExecutor 最小冲击拆单测试"""

    def test_init_defaults(self):
        e = MinImpactExecutor()
        assert e.max_participation_pct == 0.15
        assert e.min_order_size == 100

    def test_init_custom(self):
        e = MinImpactExecutor(max_participation_pct=0.2, min_order_size=200)
        assert e.max_participation_pct == 0.2
        assert e.min_order_size == 200

    def test_calculate_zero_amount(self):
        e = MinImpactExecutor()
        assert e.calculate_optimal_splits(0, 100) == []

    def test_calculate_zero_price(self):
        e = MinImpactExecutor()
        assert e.calculate_optimal_splits(100_000, 0) == []

    def test_calculate_negative_amount(self):
        e = MinImpactExecutor()
        assert e.calculate_optimal_splits(-100, 100) == []

    def test_calculate_small_order_single(self):
        """小单 → 单笔执行"""
        e = MinImpactExecutor(min_order_size=100)
        orders = e.calculate_optimal_splits(500, 10)  # 50 股 < 100
        assert len(orders) == 1
        assert orders[0]["type"] == "single"
        assert orders[0]["qty"] == 50

    def test_calculate_large_order_splits(self):
        """大单 → 多笔拆分"""
        e = MinImpactExecutor(min_order_size=100)
        orders = e.calculate_optimal_splits(10_000_000, 10, avg_daily_volume=1_000_000, volatility=0.02)
        assert len(orders) >= 1
        total_qty = sum(o["qty"] for o in orders)
        assert total_qty == 1_000_000  # 10M / 10

    def test_calculate_orders_have_fields(self):
        e = MinImpactExecutor()
        orders = e.calculate_optimal_splits(1_000_000, 10, avg_daily_volume=1_000_000)
        assert len(orders) >= 1
        for o in orders:
            assert "order_idx" in o
            assert "qty" in o
            assert "amount" in o
            assert "delay_minutes" in o

    def test_calculate_cumulative_qty(self):
        e = MinImpactExecutor()
        orders = e.calculate_optimal_splits(1_000_000, 10, avg_daily_volume=1_000_000)
        if len(orders) > 1:
            for o in orders:
                assert "cumulative_qty" in o

    def test_simulate_execution_with_mock_sleep(self):
        """模拟执行 (mock time.sleep 避免等待)"""
        e = MinImpactExecutor()
        orders = e.calculate_optimal_splits(1_000_000, 10, avg_daily_volume=1_000_000)
        with patch("utils.wt_execution_algo.time.sleep"):
            result = e.simulate_execution(orders)
        assert "total_qty" in result
        assert "total_amount" in result
        assert "avg_execution_price" in result
        assert "slippage_total" in result
        assert "num_orders" in result

    def test_simulate_empty_orders(self):
        """空订单 → 不崩溃 (bug 已修复)"""
        e = MinImpactExecutor()
        with patch("utils.wt_execution_algo.time.sleep"):
            result = e.simulate_execution([])
        assert result["total_qty"] == 0
        assert result["num_orders"] == 0
        assert result["slippage_pct"] == 0.0



# ============================================================
# TWAPExecutor
# ============================================================


class TestTWAPExecutor:
    """TWAPExecutor 时间加权平均测试"""

    def test_init_defaults(self):
        e = TWAPExecutor()
        assert e.execution_window_minutes == 30
        assert e.interval_minutes == 5

    def test_init_custom(self):
        e = TWAPExecutor(execution_window_minutes=60, interval_minutes=10)
        assert e.execution_window_minutes == 60
        assert e.interval_minutes == 10

    def test_calculate_splits_zero(self):
        e = TWAPExecutor()
        assert e.calculate_splits(0, 100) == []

    def test_calculate_splits_success(self):
        e = TWAPExecutor()
        orders = e.calculate_splits(1_000_000, 10)
        assert len(orders) >= 1
        total_qty = sum(o["qty"] for o in orders)
        assert total_qty == 100_000

    def test_calculate_optimal_splits_zero(self):
        e = TWAPExecutor()
        assert e.calculate_optimal_splits(0, 100) == []

    def test_calculate_optimal_splits_success(self):
        e = TWAPExecutor()
        orders = e.calculate_optimal_splits(1_000_000, 10, avg_daily_volume=1_000_000)
        assert len(orders) >= 1
        for o in orders:
            assert o["type"] == "twap"

    def test_twap_delay_increments(self):
        """TWAP 延迟应按 interval 递增"""
        e = TWAPExecutor()
        orders = e.calculate_optimal_splits(1_000_000, 10, avg_daily_volume=1_000_000)
        if len(orders) > 1:
            delays = [o["delay_minutes"] for o in orders]
            for i in range(1, len(delays)):
                assert delays[i] >= delays[i - 1]


# ============================================================
# VWAPExecutor
# ============================================================


class TestVWAPExecutor:
    """VWAPExecutor 成交量加权平均测试"""

    def test_init(self):
        e = VWAPExecutor()
        assert e is not None

    def test_get_volume_profile_day(self):
        e = VWAPExecutor()
        profile = e.get_volume_profile("day")
        assert len(profile) > 0
        for delay, weight in profile:
            assert isinstance(delay, int)
            assert isinstance(weight, float)
            assert weight >= 0

    def test_get_volume_profile_night(self):
        e = VWAPExecutor()
        profile = e.get_volume_profile("night")
        assert len(profile) > 0
        assert len(profile) < 20  # 夜盘更短

    def test_get_volume_profile_default_day(self):
        e = VWAPExecutor()
        profile = e.get_volume_profile()
        assert len(profile) > 0

    def test_calculate_splits_zero(self):
        e = VWAPExecutor()
        assert e.calculate_splits(0, 100) == []

    def test_calculate_splits_success(self):
        e = VWAPExecutor()
        orders = e.calculate_splits(10_000_000, 10)
        assert len(orders) >= 1
        total_qty = sum(o["qty"] for o in orders)
        assert total_qty > 0

    def test_calculate_optimal_splits_zero(self):
        e = VWAPExecutor()
        assert e.calculate_optimal_splits(0, 100) == []

    def test_calculate_optimal_splits_success(self):
        e = VWAPExecutor()
        orders = e.calculate_optimal_splits(10_000_000, 10, avg_daily_volume=1_000_000)
        assert len(orders) >= 1
        for o in orders:
            assert o["type"] == "vwap"

    def test_calculate_night_session(self):
        e = VWAPExecutor()
        orders = e.calculate_optimal_splits(10_000_000, 10, session_type="night")
        assert len(orders) >= 1


# ============================================================
# OrderExecutor
# ============================================================


class TestOrderExecutor:
    """OrderExecutor 统一执行器测试"""

    def test_init_min_impact(self):
        e = OrderExecutor(algorithm="min_impact")
        assert e.algorithm == "min_impact"
        assert isinstance(e._executor, MinImpactExecutor)

    def test_init_twap(self):
        e = OrderExecutor(algorithm="twap")
        assert isinstance(e._executor, TWAPExecutor)

    def test_init_vwap(self):
        e = OrderExecutor(algorithm="vwap")
        assert isinstance(e._executor, VWAPExecutor)

    def test_init_unknown_falls_back_to_min_impact(self):
        e = OrderExecutor(algorithm="unknown")
        assert isinstance(e._executor, MinImpactExecutor)

    def test_algorithms_list(self):
        assert "min_impact" in OrderExecutor.ALGORITHMS
        assert "twap" in OrderExecutor.ALGORITHMS
        assert "vwap" in OrderExecutor.ALGORITHMS
        assert "immediate" in OrderExecutor.ALGORITHMS

    def test_split_order_immediate(self):
        e = OrderExecutor(algorithm="immediate")
        orders = e.split_order(100_000, 10)
        assert len(orders) == 1
        assert orders[0]["type"] == "immediate"
        assert orders[0]["qty"] == 10000

    def test_split_order_min_impact(self):
        e = OrderExecutor(algorithm="min_impact")
        orders = e.split_order(1_000_000, 10, avg_daily_volume=1_000_000)
        assert len(orders) >= 1

    def test_split_order_twap(self):
        e = OrderExecutor(algorithm="twap")
        orders = e.split_order(1_000_000, 10)
        assert len(orders) >= 1

    def test_split_order_vwap(self):
        e = OrderExecutor(algorithm="vwap")
        orders = e.split_order(10_000_000, 10)
        assert len(orders) >= 1

    def test_split_order_zero(self):
        e = OrderExecutor(algorithm="min_impact")
        assert e.split_order(0, 10) == []

    def test_simulate_min_impact(self):
        e = OrderExecutor(algorithm="min_impact")
        orders = e.split_order(1_000_000, 10, avg_daily_volume=1_000_000)
        with patch("utils.wt_execution_algo.time.sleep"):
            result = e.simulate(orders)
        assert "total_qty" in result
        assert "total_amount" in result

    def test_simulate_twap(self):
        e = OrderExecutor(algorithm="twap")
        orders = e.split_order(1_000_000, 10)
        result = e.simulate(orders)
        assert "total_qty" in result
        assert result["slippage_total"] == 0

    def test_simulate_empty(self):
        e = OrderExecutor(algorithm="twap")
        result = e.simulate([])
        assert result["total_qty"] == 0

    def test_compare_algorithms(self):
        with patch("utils.wt_execution_algo.time.sleep"):
            results = OrderExecutor.compare_algorithms(1_000_000, 10, 1_000_000)
        assert "min_impact" in results
        assert "twap" in results
        assert "vwap" in results
        assert "immediate" in results
        for algo, r in results.items():
            assert r["algorithm"] == algo
            assert "num_orders" in r
            assert "total_qty" in r


# ============================================================
# 便捷函数
# ============================================================


class TestConvenienceFunctions:
    """split_order / compare_execution / execute_order_with_algorithm 测试"""

    def test_split_order(self):
        orders = split_order(1_000_000, 10, algorithm="min_impact", avg_daily_volume=1_000_000)
        assert len(orders) >= 1

    def test_split_order_twap(self):
        orders = split_order(1_000_000, 10, algorithm="twap")
        assert len(orders) >= 1

    def test_compare_execution(self):
        with patch("utils.wt_execution_algo.time.sleep"):
            results = compare_execution(1_000_000, 10, 1_000_000)
        assert len(results) == 4

    def test_execute_order_with_algorithm(self):
        with patch("utils.wt_execution_algo.time.sleep"):
            result = execute_order_with_algorithm(1_000_000, 10, algorithm="min_impact", avg_daily_volume=1_000_000)
        assert "algorithm" in result
        assert "orders" in result
        assert "simulation" in result
        assert result["algorithm"] == "min_impact"
