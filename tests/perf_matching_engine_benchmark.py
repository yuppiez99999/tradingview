"""W6.3.4 MatchingEngine 性能基准测试

验证 Rust POC 前置评估结论 — 实测三档场景下 match() 单次/总耗时。

场景:
  A. 日线级: 10 股 × 250 天 = 2,500 事件 (BAR 模式, orders/event=10)
  B. 分钟级: 10 股 × 240 分钟 × 250 天 = 600,000 事件 (BAR 模式, orders/event=10)
  C. TICK 级: 1 股 × 4 tick/s × 60s = 240 事件 (TICK 模式, orders/event=10)

输出:
  - 每场景 match() 调用次数 / 总耗时 / 单次平均耗时
  - cProfile top 20 热点函数
  - ROI 判定 (Rust 预估加速 10x, 阈值: 分钟级 ≥5min 才推荐 POC)
"""
from __future__ import annotations

import cProfile
import io
import pstats
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from utils.backtest.matching_engine import MatchingEngine
from utils.wt_structs import BarData, OrderData, TickData


def make_bar(code: str, day_idx: int) -> BarData:
    """构造测试 BarData (日线)."""
    base = 10.0 + (day_idx % 10) * 0.1
    return BarData(
        code=code,
        exchange="SSE",
        period="1d",
        open=base,
        high=base + 0.2,
        low=base - 0.2,
        close=base + 0.05,
        volume=1_000_000.0,
        amount=10_000_000.0,
        date=20260101 + day_idx,
    )


def make_tick(code: str, ts: float) -> TickData:
    """构造测试 TickData (五档)."""
    base = 10.0
    return TickData(
        code=code,
        exchange="SSE",
        price=base,
        open=base,
        high=base + 0.1,
        low=base - 0.1,
        pre_close=base,
        volume=100_000.0,
        amount=1_000_000.0,
        bid_prices=[base - 0.01, base - 0.02, base - 0.03, base - 0.04, base - 0.05],
        ask_prices=[base + 0.01, base + 0.02, base + 0.03, base + 0.04, base + 0.05],
        bid_volumes=[1000.0, 2000.0, 1500.0, 800.0, 500.0],
        ask_volumes=[1000.0, 2000.0, 1500.0, 800.0, 500.0],
        timestamp=ts,
    )


def make_orders(code: str, n: int, day_idx: int) -> list[OrderData]:
    """构造 n 个测试订单 (混合 LIMIT/MARKET, BUY/SELL)."""
    orders = []
    for i in range(n):
        direction = "BUY" if i % 2 == 0 else "SELL"
        order_type = "LIMIT" if i % 3 != 0 else "MARKET"
        price = 10.0 + (day_idx % 10) * 0.1 + (0.05 if direction == "BUY" else -0.05)
        orders.append(OrderData(
            order_id=f"ORD_{day_idx}_{i}",
            code=code,
            exchange="SSE",
            direction=direction,
            order_type=order_type,
            price=price,
            volume=100.0 * (i + 1),
        ))
    return orders


def run_scenario(name: str, n_events: int, n_orders_per_event: int, mode: str = "BAR"):
    """运行一档场景,返回 (总耗时, 调用次数, 单次平均μs)."""
    codes = [f"60000{i}.SH" for i in range(min(10, n_orders_per_event))]
    engine = MatchingEngine(mode=mode, allow_partial_fill=True, max_participation_rate=0.10)

    # 预构造数据 (避免数据构造计入耗时)
    events_data = []
    for ev_idx in range(n_events):
        code = codes[ev_idx % len(codes)]
        if mode == "BAR":
            events_data.append((code, make_bar(code, ev_idx), make_orders(code, n_orders_per_event, ev_idx)))
        else:
            events_data.append((code, make_tick(code, ev_idx * 0.25), make_orders(code, n_orders_per_event, ev_idx)))

    # 预热 (JIT/缓存)
    if mode == "BAR":
        engine.match(events_data[0][2], events_data[0][1])
    else:
        engine.match(events_data[0][2], events_data[0][1])

    # 实测
    start = time.perf_counter()
    total_calls = 0
    for _code, event, orders in events_data:
        engine.match(orders, event)
        total_calls += 1
    elapsed = time.perf_counter() - start

    avg_us = (elapsed / total_calls) * 1_000_000 if total_calls > 0 else 0
    return elapsed, total_calls, avg_us


def run_cprofile_scenario(n_events: int, n_orders_per_event: int, mode: str = "BAR"):
    """cProfile 分析单场景,返回 top 20 热点函数文本."""
    codes = [f"60000{i}.SH" for i in range(min(10, n_orders_per_event))]
    engine = MatchingEngine(mode=mode, allow_partial_fill=True, max_participation_rate=0.10)

    events_data = []
    for ev_idx in range(n_events):
        code = codes[ev_idx % len(codes)]
        if mode == "BAR":
            events_data.append((code, make_bar(code, ev_idx), make_orders(code, n_orders_per_event, ev_idx)))
        else:
            events_data.append((code, make_tick(code, ev_idx * 0.25), make_orders(code, n_orders_per_event, ev_idx)))

    profiler = cProfile.Profile()
    profiler.enable()
    for _code, event, orders in events_data:
        engine.match(orders, event)
    profiler.disable()

    s = io.StringIO()
    ps = pstats.Stats(profiler, stream=s).sort_stats("cumulative")
    ps.print_stats(20)
    return s.getvalue()


def main():
    print("=" * 80)
    print("W6.3.4 MatchingEngine 性能基准测试")
    print("=" * 80)
    print()

    # ---- 场景 A: 日线级 ----
    print("【场景 A】日线级: 2,500 事件 × 10 orders/event (BAR 模式)")
    elapsed_a, calls_a, avg_us_a = run_scenario("A", n_events=2500, n_orders_per_event=10, mode="BAR")
    print(f"  总耗时: {elapsed_a:.3f}s")
    print(f"  match() 调用次数: {calls_a}")
    print(f"  单次平均: {avg_us_a:.1f}μs ({avg_us_a/1000:.3f}ms)")
    print(f"  Rust 预估 (10x): {elapsed_a/10:.3f}s, 节省 {elapsed_a*0.9:.3f}s")
    print()

    # ---- 场景 B: 分钟级 ----
    print("【场景 B】分钟级: 600,000 事件 × 10 orders/event (BAR 模式)")
    print("  (cProfile 仅跑 60,000 事件采样, 实测跑全量)")
    elapsed_b_full, calls_b, avg_us_b = run_scenario("B", n_events=600_000, n_orders_per_event=10, mode="BAR")
    print(f"  总耗时: {elapsed_b_full:.3f}s ({elapsed_b_full/60:.2f}min)")
    print(f"  match() 调用次数: {calls_b}")
    print(f"  单次平均: {avg_us_b:.1f}μs ({avg_us_b/1000:.3f}ms)")
    print(f"  Rust 预估 (10x): {elapsed_b_full/10:.3f}s, 节省 {elapsed_b_full*0.9:.3f}s ({elapsed_b_full*0.9/60:.2f}min)")
    print()

    # ---- 场景 C: TICK 级 ----
    print("【场景 C】TICK 级: 240 事件 × 10 orders/event (TICK 模式, 五档撮合)")
    elapsed_c, calls_c, avg_us_c = run_scenario("C", n_events=240, n_orders_per_event=10, mode="TICK")
    print(f"  总耗时: {elapsed_c:.6f}s ({elapsed_c*1000:.2f}ms)")
    print(f"  match() 调用次数: {calls_c}")
    print(f"  单次平均: {avg_us_c:.1f}μs ({avg_us_c/1000:.3f}ms)")
    print(f"  Rust 预估 (10x): {elapsed_c/10:.6f}s, 节省 {elapsed_c*0.9:.6f}s")
    print()

    # ---- cProfile 热点分析 (场景 A 采样) ----
    print("=" * 80)
    print("cProfile 热点分析 (场景 A: 2,500 事件采样)")
    print("=" * 80)
    profile_output = run_cprofile_scenario(n_events=2500, n_orders_per_event=10, mode="BAR")
    print(profile_output)

    # ---- ROI 判定 ----
    print("=" * 80)
    print("ROI 判定")
    print("=" * 80)
    print()
    print("  日线级 (2,500 事件):")
    print(f"    实测总耗时: {elapsed_a:.3f}s")
    print(f"    Rust 预估节省: {elapsed_a*0.9:.3f}s")
    rust_threshold_a = 5.0  # 5s 阈值
    print(f"    阈值 (≥5s 才推荐): {'✅ 达标' if elapsed_a >= rust_threshold_a else '❌ 未达标'} → {'⚠️ 跳过' if elapsed_a < rust_threshold_a else '✅ POC'}")
    print()
    print("  分钟级 (600,000 事件):")
    print(f"    实测总耗时: {elapsed_b_full:.3f}s ({elapsed_b_full/60:.2f}min)")
    print(f"    Rust 预估节省: {elapsed_b_full*0.9:.3f}s ({elapsed_b_full*0.9/60:.2f}min)")
    rust_threshold_b = 300.0  # 5min 阈值
    print(f"    阈值 (≥5min 才推荐): {'✅ 达标' if elapsed_b_full >= rust_threshold_b else '❌ 未达标'} → {'✅ POC' if elapsed_b_full >= rust_threshold_b else '⚠️ 跳过'}")
    print()
    print("  TICK 级 (240 事件):")
    print(f"    实测总耗时: {elapsed_c:.6f}s")
    print(f"    Rust 预估节省: {elapsed_c*0.9:.6f}s")
    rust_threshold_c = 1.0  # 1s 阈值
    print(f"    阈值 (≥1s 才推荐): {'✅ 达标' if elapsed_c >= rust_threshold_c else '❌ 未达标'} → {'⚠️ 跳过' if elapsed_c < rust_threshold_c else '✅ POC'}")
    print()

    # ---- 总结 ----
    print("=" * 80)
    print("总结")
    print("=" * 80)
    overall_recommend_poc = elapsed_b_full >= rust_threshold_b
    print("  前置评估结论: 跳过 (分钟级预估 40min)")
    print(f"  实测分钟级: {elapsed_b_full/60:.2f}min")
    print(f"  修正幅度: {abs(elapsed_b_full/60 - 40) / 40 * 100:.1f}% {'高估' if elapsed_b_full/60 < 40 else '低估'}")
    print(f"  最终建议: {'✅ 推荐 Rust POC (分钟级达标)' if overall_recommend_poc else '⚠️ 维持跳过 (分钟级未达标, FFI 回调开销会进一步抵消收益)'}")
    print()
    print("  详细报告: tests/perf_matching_engine_benchmark.py")
    print("  决策记录: cairn/nautilus-trader-study.md §4.3")


if __name__ == "__main__":
    main()
