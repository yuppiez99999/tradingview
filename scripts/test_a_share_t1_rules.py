"""W6.4.2 SimTradeLab A 股 T+1 模拟验证脚本。

测试 T+1 持仓追踪器 + 涨跌停规则, 确保:
    1. 当日买入不可当日卖出 (T+1)
    2. 次交易日可卖出
    3. T+0 ETF (511/513/518) 不受 T+1 限制
    4. FIFO lot 消费
    5. 涨跌停价格检查 (10cm vs 20cm)

运行:
    python scripts/test_a_share_t1_rules.py
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.backtest.a_share_rules import (
    AShareTradingRules,
    T1PositionTracker,
    bar_to_date,
    filter_order_t1,
)
from utils.wt_structs import BarData, OrderData


def test_t1_basic() -> bool:
    """测试 1: T+1 基本规则 — 当日买入不可卖, 次日可卖。"""
    tracker = T1PositionTracker()
    today = date(2024, 6, 3)

    # Day 1: 买入 1000 股
    tracker.add_lot("600519.SH", 1000.0, today, 1800.0)

    # Day 1: 尝试卖出 → 0 可卖 (T+1)
    avail_d1 = tracker.available_volume("600519.SH", today)
    assert avail_d1 == 0.0, f"T+1 当日可卖应为 0, 实际 {avail_d1}"

    # Day 2: 可卖 1000 股
    tomorrow = today + timedelta(days=1)
    avail_d2 = tracker.available_volume("600519.SH", tomorrow)
    assert avail_d2 == 1000.0, f"T+1 次日可卖应为 1000, 实际 {avail_d2}"

    print("[PASS] 测试 1: T+1 基本规则 (当日买入不可卖, 次日可卖)")
    return True


def test_t0_etf_bypass() -> bool:
    """测试 2: T+0 ETF (511 债券) 不受 T+1 限制。"""
    tracker = T1PositionTracker()
    today = date(2024, 6, 3)

    # 买入 T+0 ETF (511010 = 债券 ETF)
    tracker.add_lot("511010.SH", 500.0, today, 100.0)

    # 当天可卖 (T+0)
    avail = tracker.available_volume("511010.SH", today)
    assert avail == 500.0, f"T+0 ETF 当日可卖应为 500, 实际 {avail}"

    # 当天卖出
    consumed, _ = tracker.consume("511010.SH", 500.0, today)
    assert consumed == 500.0, f"T+0 ETF 当日卖出应为 500, 实际 {consumed}"

    print("[PASS] 测试 2: T+0 ETF (511010) 不受 T+1 限制")
    return True


def test_fifo_consumption() -> bool:
    """测试 3: FIFO lot 消费 — 先买的先卖。"""
    tracker = T1PositionTracker()
    day1 = date(2024, 6, 3)
    day2 = date(2024, 6, 4)
    day3 = date(2024, 6, 5)

    # Day 1: 买入 300 股 @10
    tracker.add_lot("600519.SH", 300.0, day1, 10.0)
    # Day 2: 买入 200 股 @12
    tracker.add_lot("600519.SH", 200.0, day2, 12.0)

    # Day 2: 可卖 = Day1 lot (300), Day2 lot 不可卖 (T+1)
    avail = tracker.available_volume("600519.SH", day2)
    assert avail == 300.0, f"Day2 可卖应为 300, 实际 {avail}"

    # Day 3: 可卖 = 300 + 200 = 500
    avail = tracker.available_volume("600519.SH", day3)
    assert avail == 500.0, f"Day3 可卖应为 500, 实际 {avail}"

    # Day 3: 卖出 400 股 → FIFO 消费 Day1 lot 300 + Day2 lot 100
    consumed, val = tracker.consume("600519.SH", 400.0, day3)
    assert consumed == 400.0, f"应消费 400, 实际 {consumed}"
    # 加权成本: 300*10 + 100*12 = 4200
    assert abs(val - 4200.0) < 0.01, f"加权成本应为 4200, 实际 {val}"

    # 剩余: Day2 lot 100 股
    remaining = tracker.total_volume("600519.SH")
    assert remaining == 100.0, f"剩余应为 100, 实际 {remaining}"

    print("[PASS] 测试 3: FIFO lot 消费 (先买先卖, 加权成本正确)")
    return True


def test_filter_order() -> bool:
    """测试 4: 订单过滤器 — BUY 通过, SELL T+1 违规拦截/部分放行。"""
    rules = AShareTradingRules(enable_t1=True, enable_price_limit=False)
    today = date(2024, 6, 3)
    tomorrow = today + timedelta(days=1)

    # 买入 1000 股
    buy_order = OrderData(
        order_id="b1",
        code="600519.SH",
        exchange="SSE",
        direction="BUY",
        offset="OPEN",
        order_type="MARKET",
        price=1800.0,
        volume=1000.0,
    )
    result = filter_order_t1(rules, buy_order, today)
    assert result.passed and result.adjusted_volume == 1000.0

    # 模拟买入成交
    rules.on_buy_fill("600519.SH", 1000.0, 1800.0, today)

    # Day1 卖出 → T+1 拦截
    sell_order = OrderData(
        order_id="s1",
        code="600519.SH",
        exchange="SSE",
        direction="SELL",
        offset="CLOSE",
        order_type="MARKET",
        price=1850.0,
        volume=1000.0,
    )
    result = filter_order_t1(rules, sell_order, today)
    assert not result.passed, f"Day1 卖出应被 T+1 拦截, 实际 passed={result.passed}"
    assert "T+1" in result.reason

    # Day2 卖出 → 通过
    result = filter_order_t1(rules, sell_order, tomorrow)
    assert result.passed and result.adjusted_volume == 1000.0

    print("[PASS] 测试 4: 订单过滤器 (BUY 通过, SELL T+1 拦截, 次日放行)")
    return True


def test_price_limit() -> bool:
    """测试 5: 涨跌停价格检查 — 10cm vs 20cm。"""
    rules = AShareTradingRules(enable_t1=False, enable_price_limit=True)

    # 10cm 股票 (600519 茅台)
    pct_10 = rules.get_price_limit_pct("600519.SH")
    assert abs(pct_10 - 0.10) < 0.01, f"10cm 涨跌停应为 10%, 实际 {pct_10:.2%}"

    # 20cm 股票 (688001 华兴源创, 科创板)
    pct_20 = rules.get_price_limit_pct("688001.SH")
    assert abs(pct_20 - 0.20) < 0.01, f"20cm 涨跌停应为 20%, 实际 {pct_20:.2%}"

    # 涨停价检查
    ok, _ = rules.check_price_limit("600519.SH", 1980.0, 1800.0)  # +10%
    assert ok, "10% 涨停价应通过"

    rejected, reason = rules.check_price_limit("600519.SH", 2000.0, 1800.0)  # +11.1%
    assert not rejected, "超 10% 涨停应被拒"
    assert "涨停" in reason

    print("[PASS] 测试 5: 涨跌停价格检查 (10cm=10%, 20cm=20%)")
    return True


def test_bar_to_date() -> bool:
    """测试 6: BarData.date → date 转换。"""
    bar = BarData(
        code="600519.SH",
        exchange="SSE",
        period="1d",
        open=1800,
        high=1850,
        low=1790,
        close=1840,
        volume=10000,
        date=20240603,
        time=0,
    )
    d = bar_to_date(bar)
    assert d == date(2024, 6, 3), f"日期应为 2024-06-03, 实际 {d}"

    print("[PASS] 测试 6: bar_to_date 转换正确")
    return True


def main() -> int:
    print("=" * 70)
    print("W6.4.2 SimTradeLab A 股 T+1 模拟验证")
    print("=" * 70)

    tests = [
        test_t1_basic,
        test_t0_etf_bypass,
        test_fifo_consumption,
        test_filter_order,
        test_price_limit,
        test_bar_to_date,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            if test():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"[FAIL] {test.__name__}: {e}")
            failed += 1

    print(f"\n{'=' * 70}")
    print(f"结果: {passed} PASS / {failed} FAIL / {len(tests)} 总计")

    if failed == 0:
        print("✅ 全部通过 — T+1 模拟规则正确")
        return 0
    print("❌ 存在失败 — 需排查")
    return 1


if __name__ == "__main__":
    sys.exit(main())
