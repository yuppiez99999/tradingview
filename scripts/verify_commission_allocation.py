"""验证 ResultConverter 手续费分配逻辑 — 复杂模拟数据。

数据设计:
    - 253 个权益曲线点 (252 交易日), 可复现随机种子
    - 22 笔 ALL_TRADED 成交 — 分布在 event_index 1,2,5,7,10,15,22,30,45,60,
      80,100,120,150,180,200,220,240,250 (跨全季度)
    - 8 笔 REJECTED 拒单 — 分布在 event_index 3,8,18,35,55,90,130,210
    - 5 笔无 event_index 的旧格式成交 (向后兼容, 归入 Day 0)
    - 验证: 每笔手续费 = price * volume * commission_rate, 按 event_index 精确分配
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np

from utils.backtest import EngineSummary, ResultConverter
from utils.backtest.result_converter import DEFAULT_COMMISSION_RATE

# ============================================================
# 1. 构造 252 天权益曲线
# ============================================================
RNG = np.random.default_rng(20240811)  # 固定种子
N_POINTS = 253
INITIAL_CAPITAL = 1_000_000.0

daily_rets = 0.0003 + 0.010 * RNG.standard_normal(N_POINTS - 1)
equity_curve = [INITIAL_CAPITAL]
for r in daily_rets:
    equity_curve.append(equity_curve[-1] * (1 + r))

FINAL_EQUITY = equity_curve[-1]
ENGINE_RETURN = (FINAL_EQUITY - INITIAL_CAPITAL) / INITIAL_CAPITAL

# ============================================================
# 2. 构造交易记录
# ============================================================
trade_records: list[dict] = []
EXPECTED_COSTS: dict[int, float] = {}  # event_index -> expected commission

def add_trade(order_id, code, direction, offset, price, volume, event_index, status="ALL_TRADED"):
    trade = {
        "order_id": order_id,
        "code": code,
        "direction": direction,
        "offset": offset,
        "status": status,
    }
    if status == "ALL_TRADED":
        trade["price"] = price
        trade["volume"] = volume
        trade["event_index"] = event_index
        commission = price * volume * DEFAULT_COMMISSION_RATE
        EXPECTED_COSTS[event_index] = EXPECTED_COSTS.get(event_index, 0.0) + commission
    else:
        trade["reason"] = "test_reason"
        trade["event_index"] = event_index
    trade_records.append(trade)

STOCKS = ["600519.SH", "000001.SZ", "300750.SZ", "688981.SH", "601899.SH", "600900.SH",
          "601318.SH", "600036.SH", "000858.SZ"]

# ── 跨不同日期成交 (22 笔) ──
# Day 1-10: 密集交易
add_trade("T01", "600519.SH", "BUY", "OPEN",  1800.00, 50,  event_index=1)
add_trade("T02", "000001.SZ", "SELL", "CLOSE",  10.50, 2000, event_index=1)  # 同日第 2 笔
add_trade("T03", "300750.SZ", "BUY", "OPEN",  220.00, 300,  event_index=2)
add_trade("T04", "688981.SH", "SELL", "OPEN",  15.80, 500,  event_index=5)
add_trade("T05", "601899.SH", "BUY", "CLOSE",   8.20, 1000, event_index=5)  # 同日第 2 笔
add_trade("T06", "600900.SH", "BUY", "OPEN",  14.50, 800,  event_index=7)
add_trade("T07", "601318.SH", "SELL", "OPEN",  48.00, 150,  event_index=10)

# Day 15-30: 中期交易
add_trade("T08", "000858.SZ", "BUY", "OPEN",   75.00, 200,  event_index=15)
add_trade("T09", "600036.SH", "SELL", "CLOSE",  42.00, 400,  event_index=22)
add_trade("T10", "300750.SZ", "BUY", "OPEN",  250.00, 100,  event_index=30)

# Day 45-80: 稀疏交易
add_trade("T11", "600519.SH", "SELL", "CLOSE", 1850.00, 20,  event_index=45)
add_trade("T12", "601899.SH", "BUY", "OPEN",    9.10, 1500, event_index=60)
add_trade("T13", "000001.SZ", "BUY", "OPEN",   11.20, 800,  event_index=60)  # 同日第 2 笔
add_trade("T14", "600900.SH", "SELL", "OPEN",  15.80, 600,  event_index=80)

# Day 100-250: 后半段交易
add_trade("T15", "688981.SH", "BUY", "OPEN",  18.00, 300,  event_index=100)
add_trade("T16", "601318.SH", "BUY", "CLOSE",  52.50, 100,  event_index=120)
add_trade("T17", "000858.SZ", "SELL", "CLOSE",  85.00, 250,  event_index=150)
add_trade("T18", "300750.SZ", "SELL", "OPEN", 280.00, 150,  event_index=180)
add_trade("T19", "600036.SH", "BUY", "OPEN",  45.00, 500,  event_index=200)
add_trade("T20", "600519.SH", "SELL", "OPEN",1920.00, 15,   event_index=220)
add_trade("T21", "601899.SH", "BUY", "CLOSE",  10.00, 2000, event_index=240)
add_trade("T22", "000001.SZ", "BUY", "OPEN",  12.50, 1200, event_index=250)

# ── 8 笔拒单 (不产生手续费) ──
add_trade("R01", "600519.SH", "BUY",  "OPEN", 1800.00, 100, event_index=3,  status="REJECTED")
add_trade("R02", "000001.SZ", "SELL", "OPEN",   10.50, 500, event_index=8,  status="REJECTED")
add_trade("R03", "300750.SZ", "BUY",  "OPEN",  220.00, 200, event_index=18, status="REJECTED")
add_trade("R04", "688981.SH", "SELL", "OPEN",   15.80, 800, event_index=35, status="REJECTED")
add_trade("R05", "601899.SH", "BUY",  "OPEN",    8.20, 3000,event_index=55, status="REJECTED")
add_trade("R06", "600900.SH", "SELL", "OPEN",   14.50, 1500,event_index=90, status="REJECTED")
add_trade("R07", "601318.SH", "BUY",  "CLOSE",  48.00, 400, event_index=130,status="REJECTED")
add_trade("R08", "000858.SZ", "SELL", "CLOSE",  75.00, 600, event_index=210,status="REJECTED")

# ── 5 笔旧格式成交 (无 event_index, 归入 Day 0) ──
old_format_commission = 0.0
for i, (price, vol) in enumerate([(50.0, 200), (30.0, 500), (15.0, 1000), (8.0, 2000), (3.0, 5000)]):
    trade = {
        "order_id": f"OLD{i:02d}",
        "code": STOCKS[i % len(STOCKS)],
        "direction": "BUY",
        "offset": "OPEN",
        "status": "ALL_TRADED",
        "price": price,
        "volume": vol,
        # 注意: 没有 event_index 字段
    }
    trade_records.append(trade)
    old_format_commission += price * vol * DEFAULT_COMMISSION_RATE
# 旧格式全部归入 Day 0
EXPECTED_COSTS[0] = EXPECTED_COSTS.get(0, 0.0) + old_format_commission

print("=" * 76)
print("【复杂模拟数据概览】")
print("-" * 76)
print(f"  权益曲线点数:      {N_POINTS} (含初始点, 252 交易日)")
print(f"  区间:              {equity_curve[0]:,.0f} → {equity_curve[-1]:,.0f}")
print(f"  总收益率:          {ENGINE_RETURN:.6%}")
print(f"  ALL_TRADED 笔数:   {sum(1 for t in trade_records if t['status'] == 'ALL_TRADED')} (含 5 笔旧格式)")
print(f"  REJECTED 笔数:     {sum(1 for t in trade_records if t['status'] == 'REJECTED')}")
print(f"  总交易记录数:       {len(trade_records)}")
print("\n  按日分配手续费预期 (event_index → commission):")
for idx in sorted(EXPECTED_COSTS.keys()):
    print(f"    Day {idx:>3d}:  ¥{EXPECTED_COSTS[idx]:>12.6f}")
print(f"  {'':>3s} 合计:  ¥{sum(EXPECTED_COSTS.values()):>12.6f}")

# ============================================================
# 3. 构造 EngineSummary 并运行 ResultConverter
# ============================================================
summary = EngineSummary(
    initial_capital=INITIAL_CAPITAL,
    final_equity=FINAL_EQUITY,
    total_return=ENGINE_RETURN,
    n_events=N_POINTS - 1,
    n_orders_submitted=len(trade_records),
    n_orders_filled=sum(1 for t in trade_records if t["status"] == "ALL_TRADED"),
    n_orders_rejected=sum(1 for t in trade_records if t["status"] == "REJECTED"),
    equity_curve=equity_curve,
    trade_records=trade_records,
)

converter = ResultConverter()
result = converter.convert(summary, name="complex_commission_test")

# ============================================================
# 4. 逐笔验证: 每日手续费
# ============================================================
print("\n" + "=" * 76)
print("【手续费分配验证 — 逐日对比】")
print("-" * 76)
print(f"  {'Day':>4s}  {'预期手续费':>14s}  {'实际手续费':>14s}  {'状态':>6s}  {'说明'}")
print(f"  {'----':>4s}  {'--------------':>14s}  {'--------------':>14s}  {'------':>6s}  {'----'}")

all_ok = True
for day in sorted(set(list(EXPECTED_COSTS.keys()) + [i for i in range(N_POINTS)])):
    expected = EXPECTED_COSTS.get(day, 0.0)
    actual = result.transaction_costs[day]
    if expected > 0 or actual > 0:
        match = "✓" if abs(expected - actual) < 1e-9 else "✗ FAIL"
        if abs(expected - actual) >= 1e-9:
            all_ok = False
        note = ""
        if day == 0 and old_format_commission > 0:
            note = f"(含 {old_format_commission:.4f} 旧格式)"
        print(f"  {day:>4d}  ¥{expected:>14.6f}  ¥{actual:>14.6f}  {match:>6s}  {note}")

print("\n  未在列表中的日期 (手续费为 0): ", end="")
zero_days = [i for i in range(N_POINTS) if EXPECTED_COSTS.get(i, 0.0) == 0.0 and result.transaction_costs[i] != 0.0]
if zero_days:
    print(f"⚠️  异常: Day {zero_days}")
    all_ok = False
else:
    print("无异常 ✓")

# ============================================================
# 5. 汇总验证
# ============================================================
total_expected = sum(EXPECTED_COSTS.values())
total_actual = result.total_transaction_cost

print("\n" + "=" * 76)
print("【汇总验证】")
print("-" * 76)
print(f"  预期总手续费:  ¥{total_expected:>14.6f}")
print(f"  实际总手续费:  ¥{total_actual:>14.6f}")
diff = abs(total_expected - total_actual)
pct_diff = (diff / max(total_expected, 1e-12)) * 100
print(f"  绝对偏差:     ¥{diff:>14.6f}")
print(f"  相对偏差:     {pct_diff:>14.8f}%")

# 验证
assert diff < 1e-9, f"总手续费偏差过大: {diff}"
print(f"  验证:         {'✅ PASS' if diff < 1e-9 else '❌ FAIL'}")

# 拒单验证: 8 笔拒单不应产生任何手续费
rejected_costs = []
for t in trade_records:
    if t["status"] == "REJECTED":
        ei = t.get("event_index", 0)
        rejected_costs.append((t["order_id"], ei))

# 验证: 每个有拒单的日期, 当天手续费仅来自 ALL_TRADED (不含 REJECTED)
for _oid, ei in rejected_costs:
    manual_at_day = sum(
        t["price"] * t["volume"] * DEFAULT_COMMISSION_RATE
        for t in trade_records
        if t["status"] == "ALL_TRADED" and t.get("event_index", 0) == ei
    )
    assert abs(result.transaction_costs[ei] - manual_at_day) < 1e-9, \
        f"Day {ei} 手续费不符 (含拒单): {result.transaction_costs[ei]} vs {manual_at_day}"
print("  拒单排除验证:  ✅ PASS (8 笔 REJECTED 未计入手续费)")

# 旧格式兼容性
assert abs(result.transaction_costs[0] - (EXPECTED_COSTS.get(0, 0.0))) < 1e-9, \
    "Day 0 手续费不符 (旧格式归入失败)"
print("  旧格式兼容:   ✅ PASS (5 笔无 event_index 归入 Day 0)")

# trade_count 验证
n_filled = sum(1 for t in trade_records if t["status"] == "ALL_TRADED")
assert result.trade_count == n_filled, f"trade_count: {result.trade_count} vs {n_filled}"
print(f"  成交数验证:   ✅ PASS ({result.trade_count} 笔 ALL_TRADED)")

# ============================================================
# 6. 核心指标不受手续费影响 (验证指标公式正确性)
# ============================================================
print("\n" + "=" * 76)
print("【核心指标验证】")
print("-" * 76)
eq_arr = np.array(equity_curve)
expected_total_return = (eq_arr[-1] - eq_arr[0]) / eq_arr[0]
assert abs(result.total_return - expected_total_return) < 1e-9, "total_return 偏差"
print(f"  total_return:   {result.total_return:.8%}  ✅")

n_years = result.n_days / 252
expected_annual = (1 + expected_total_return) ** (1 / max(n_years, 0.5)) - 1
assert abs(result.annual_return - expected_annual) < 1e-9, "annual_return 偏差"
print(f"  annual_return:  {result.annual_return:.8%}  ✅")

rets = np.array(result.daily_returns)
expected_vol = float(np.std(rets) * np.sqrt(252))
assert abs(result.annual_volatility - expected_vol) < 1e-9, "annual_volatility 偏差"
print(f"  annual_vol:     {result.annual_volatility:.8%}  ✅")

# ============================================================
# 7. Yearly Stats
# ============================================================
print("\n" + "=" * 76)
print("【Yearly Stats】")
print("-" * 76)
print(f"  yearly_stats 条数:  {len(result.yearly_stats)}")
for ys in result.yearly_stats:
    print(f"  Year {ys['year']}: ret={ys['return']:.4%}  vol={ys['volatility']:.4%}  "
          f"maxdd={ys['max_drawdown']:.4%}  csi300={ys['csi300_return']:.4%}  "
          f"type={ys['market_type']}")

assert len(result.yearly_stats) >= 1

# ============================================================
# 8. 最终总结
# ============================================================
print("\n" + "=" * 76)
if all_ok:
    print("✅ 全部验证通过! 手续费分配逻辑正确:")
    print(f"    - {n_filled} 笔 ALL_TRADED 按 event_index 精确分配到对应日期")
    print("    - 8 笔 REJECTED 拒单未计入手续费")
    print("    - 5 笔旧格式成交正确归入 Day 0")
    print(f"    - 总手续费 ¥{total_actual:.4f} 与手动计算完全一致")
    print("    - 核心指标 (return/vol/sharpe) 不受手续费影响")
else:
    print("❌ 验证失败, 手续费分配存在偏差")
    sys.exit(1)
print("=" * 76)
