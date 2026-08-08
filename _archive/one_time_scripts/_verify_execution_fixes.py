"""验证执行模块修复的脚本

验证内容:
1. P0: OrderRouter.route_order 注入 symbol/side 到 order 字典
2. P0: OrderRouter._execute_order 校验 symbol/side 非空
3. P1: ExecutionAlgorithmEngine IS 算法除零保护
4. P1: ExecutionAlgorithmEngine POV 算法除零保护
5. P1: OrderRouter 多线程锁保护
6. P2: _apply_randomization 时间钳制到交易时段
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

# 确保项目根目录在 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
_V83_SRC = _PROJECT_ROOT / "v8.3_institutional" / "src"
if str(_V83_SRC) not in sys.path:
    sys.path.insert(0, str(_V83_SRC))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

PASS_COUNT = 0
FAIL_COUNT = 0


def _check(condition: bool, name: str, detail: str = "") -> None:
    """断言检查"""
    global PASS_COUNT, FAIL_COUNT
    if condition:
        PASS_COUNT += 1
        print(f"  [PASS] {name}")
    else:
        FAIL_COUNT += 1
        print(f"  [FAIL] {name} {detail}")


def test_p0_route_order_injects_symbol_side():
    """P0: route_order 注入 symbol/side"""
    print("\n=== Test 1: P0 route_order 注入 symbol/side ===")
    from utils.execution.automated_execution_system import OrderRouter

    router = OrderRouter()
    # 构造 execution_plan (模拟 ExecutionStrategy.generate_execution_plan 输出)
    execution_plan = {
        'trade_id': 'TRADE_TEST',
        'instrument': '600519',
        'total_size': 1000,
        'total_direction': 'buy',
        'strategy': 'aggressive',
        'num_slices': 1,
        'slices': [{
            'slice_id': 1,
            'size': 1000,
            'direction': 'buy',
            'instrument': '600519',
            'price_type': 'market',
            'priority': 'high',
            'created_at': '2026-07-28T10:00:00',
        }],
        'timeout_seconds': 30,
        'max_retry_attempts': 2,
        'slippage_tolerance': 0.01,
        'execution_style': 'immediate',
        'created_at': '2026-07-28T10:00:00',
    }

    result = router.route_order(execution_plan, 'normal')
    _check(result.get('success') is True, "route_order 返回成功")
    routed = result.get('routed_orders', [])
    _check(len(routed) == 1, "生成 1 个订单", f"实际 {len(routed)}")
    if routed:
        order = routed[0]
        _check(order.get('symbol') == '600519', "order.symbol 注入正确", f"实际 {order.get('symbol')}")
        _check(order.get('side') == 'BUY', "order.side 注入正确 (大写)", f"实际 {order.get('side')}")

    # 测试 SELL 方向
    execution_plan['total_direction'] = 'sell'
    execution_plan['slices'][0]['direction'] = 'sell'
    result2 = router.route_order(execution_plan, 'normal')
    routed2 = result2.get('routed_orders', [])
    if routed2:
        _check(routed2[0].get('side') == 'SELL', "SELL 方向注入正确", f"实际 {routed2[0].get('side')}")


def test_p0_execute_order_validates_symbol():
    """P0: _execute_order 校验 symbol 非空"""
    print("\n=== Test 2: P0 _execute_order 校验 symbol 非空 ===")
    from utils.execution.automated_execution_system import OrderRouter

    router = OrderRouter()  # 模拟模式 (无 smart_router/broker, 无 TRADING_ENV=production)

    # 空 symbol → 拒绝执行
    order_no_symbol = {
        'order_id': 'TEST_001',
        'slice_info': {'size': 100, 'price': 100.0},
        'target_pool': 'normal',
        'symbol': '',
        'side': 'BUY',
    }
    result = router._execute_order(order_no_symbol)
    _check(result.get('success') is False, "空 symbol 拒绝执行")
    _check('symbol' in result.get('error', '').lower() or '空' in result.get('error', ''),
           "错误信息提及 symbol", f"实际: {result.get('error')}")

    # 非法 side → 拒绝执行
    order_bad_side = {
        'order_id': 'TEST_002',
        'slice_info': {'size': 100, 'price': 100.0},
        'target_pool': 'normal',
        'symbol': '600519',
        'side': 'INVALID',
    }
    result2 = router._execute_order(order_bad_side)
    _check(result2.get('success') is False, "非法 side 拒绝执行")

    # 正常订单 → 执行成功 (模拟模式)
    order_ok = {
        'order_id': 'TEST_003',
        'slice_info': {'size': 100, 'price': 100.0},
        'target_pool': 'normal',
        'symbol': '600519',
        'side': 'BUY',
    }
    result3 = router._execute_order(order_ok)
    _check(result3.get('success') is True, "正常订单执行成功", f"实际: {result3}")
    _check(result3.get('is_live') is False, "模拟模式 is_live=False")


def test_p1_is_algo_division_zero_protection():
    """P1: IS 算法除零保护"""
    print("\n=== Test 3: P1 IS 算法除零保护 ===")
    from utils.execution_algorithm_engine import ExecutionAlgorithmEngine, Order

    engine = ExecutionAlgorithmEngine()
    order = Order(
        symbol='600519',
        side='BUY',
        total_shares=10000,
        start_time=pd.Timestamp('2026-07-28 09:30'),
        end_time=pd.Timestamp('2026-07-28 11:30'),
        urgency='HIGH',
    )

    # 极端参数: lam * sigma2 * t * 10 非常大, exp 下溢到 0
    # daily_volatility=10 → sigma2=100, lam=2.5(HIGH), t*10 最大=10
    # exponent = -2.5 * 100 * 1.0 * 10 = -2500, exp(-2500) 下溢到 0
    plan = engine.is_algo(order, daily_volatility=10.0)
    _check(plan is not None, "IS 算法返回非空 plan (未崩溃)")
    _check(not any(np.isnan(c.shares) for c in plan.child_orders),
           "子订单 shares 无 NaN", f"shares: {[c.shares for c in plan.child_orders]}")
    _check(not any(np.isinf(c.shares) for c in plan.child_orders),
           "子订单 shares 无 Inf")
    print(f"    IS 算法 (extreme vol): num_slices={plan.num_slices}, cost_bps={plan.expected_cost_bps:.2f}")


def test_p1_pov_division_zero_protection():
    """P1: POV 算法除零保护 (通过全 0 volume_profile 间接触发)"""
    print("\n=== Test 4: P1 POV 算法除零保护 ===")
    from utils.execution_algorithm_engine import ExecutionAlgorithmEngine, Order

    engine = ExecutionAlgorithmEngine()
    # 覆盖默认曲线为全 0 (模拟用户传入异常曲线)
    engine.default_volume_curve = [0.0] * 24

    order = Order(
        symbol='600519',
        side='BUY',
        total_shares=10000,
        start_time=pd.Timestamp('2026-07-28 09:30'),
        end_time=pd.Timestamp('2026-07-28 11:30'),
    )
    # 全 0 曲线 → total_w=0 → 触发除零保护
    try:
        plan = engine.pov(order, expected_market_volume=1_000_000)
        _check(plan is not None, "POV 算法返回非空 plan (未崩溃)")
        _check(not any(np.isnan(c.shares) for c in plan.child_orders),
               "子订单 shares 无 NaN")
        print(f"    POV 算法 (zero curve): num_slices={plan.num_slices}")
    except Exception as e:
        _check(False, "POV 算法不应抛异常", f"异常: {e}")

    # 恢复默认曲线
    engine.default_volume_curve = engine._default_u_shape_curve()


def test_p1_vwap_with_real_adv():
    """P1: VWAP 算法接受真实 ADV 参数"""
    print("\n=== Test 5: P1 VWAP 算法真实 ADV ===")
    from utils.execution_algorithm_engine import ExecutionAlgorithmEngine, Order

    engine = ExecutionAlgorithmEngine()
    order = Order(
        symbol='600519',
        side='BUY',
        total_shares=10000,
        start_time=pd.Timestamp('2026-07-28 09:30'),
        end_time=pd.Timestamp('2026-07-28 11:30'),
    )

    # 不传 adv (占位估计)
    plan_no_adv = engine.vwap(order)
    # 传真实 adv (大 ADV → 小 impact)
    plan_with_adv = engine.vwap(order, adv=10_000_000)

    _check(plan_no_adv.metadata.get('adv_provided') is False,
           "不传 adv 时 adv_provided=False")
    _check(plan_with_adv.metadata.get('adv_provided') is True,
           "传 adv 时 adv_provided=True")
    _check(plan_with_adv.expected_cost_bps < plan_no_adv.expected_cost_bps,
           "真实 ADV (大) → 更小 impact_bps",
           f"with_adv={plan_with_adv.expected_cost_bps:.2f} vs no_adv={plan_no_adv.expected_cost_bps:.2f}")
    print(f"    VWAP 无 ADV: cost_bps={plan_no_adv.expected_cost_bps:.2f}")
    print(f"    VWAP 有 ADV: cost_bps={plan_with_adv.expected_cost_bps:.2f}")


def test_p1_thread_safety():
    """P1: OrderRouter 多线程锁保护"""
    print("\n=== Test 6: P1 OrderRouter 多线程锁保护 ===")
    from utils.execution.automated_execution_system import OrderRouter

    router = OrderRouter()
    errors = []

    def worker():
        try:
            for _ in range(50):
                # 并发调用 get_router_summary (读操作)
                summary = router.get_router_summary()
                _ = summary.get('total_active_orders', 0)
                # 并发写入 active_orders
                with router._orders_lock:
                    router.active_orders[f'thread_{threading.get_ident()}_{time.time()}'] = {
                        'status': 'pending', 'target_pool': 'normal'
                    }
        except Exception as e:
            errors.append(str(e))

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    _check(len(errors) == 0, "多线程并发无异常", f"错误: {errors[:3]}")
    summary = router.get_router_summary()
    _check(summary['total_active_orders'] > 0, "并发写入后 active_orders 非空")
    print(f"    多线程测试: {summary['total_active_orders']} 个活跃订单")


def test_p2_randomization_clamps_to_trading_hours():
    """P2: _apply_randomization 时间钳制到交易时段"""
    print("\n=== Test 7: P2 _apply_randomization 时间钳制 ===")
    from utils.execution_algorithm_engine import ExecutionAlgorithmEngine

    engine = ExecutionAlgorithmEngine()

    # 构造一个靠近午休的 slot (11:25), 随机化可能漂移到 11:35 (午休)
    slots = [pd.Timestamp('2026-07-28 11:25')]
    shares = [1000.0]

    # 多次运行, 确认所有结果都不在午休时段 (11:30-13:00)
    for _ in range(100):
        randomized = engine._apply_randomization(shares, slots)
        for _, ts in randomized:
            hour_min = ts.hour * 60 + ts.minute
            _check(
                not (11 * 60 + 30 < hour_min < 13 * 60),
                f"时间 {ts.strftime('%H:%M')} 不在午休时段",
                f"hour_min={hour_min}",
            )
            # 只报告第一次失败, 避免刷屏
            if 11 * 60 + 30 < hour_min < 13 * 60:
                break
        else:
            continue
        break

    # 测试 _clamp_to_trading_hours 静态方法
    clamped = ExecutionAlgorithmEngine._clamp_to_trading_hours
    _check(clamped(pd.Timestamp('2026-07-28 11:45')).hour == 13,
           "11:45 钳制到 13:00")
    _check(clamped(pd.Timestamp('2026-07-28 12:30')).hour == 13,
           "12:30 钳制到 13:00")
    _check(clamped(pd.Timestamp('2026-07-28 09:00')).hour == 9,
           "09:00 钳制到 09:30")
    _check(clamped(pd.Timestamp('2026-07-28 09:00')).minute == 30,
           "09:00 钳制到 09:30")
    _check(clamped(pd.Timestamp('2026-07-28 15:30')).hour == 14,
           "15:30 钳制到 14:59")


def main():
    print("=" * 60)
    print("执行模块修复验证脚本")
    print("=" * 60)

    test_p0_route_order_injects_symbol_side()
    test_p0_execute_order_validates_symbol()
    test_p1_is_algo_division_zero_protection()
    test_p1_pov_division_zero_protection()
    test_p1_vwap_with_real_adv()
    test_p1_thread_safety()
    test_p2_randomization_clamps_to_trading_hours()

    print("\n" + "=" * 60)
    print(f"验证结果: {PASS_COUNT} PASS / {FAIL_COUNT} FAIL")
    print("=" * 60)
    return 0 if FAIL_COUNT == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
