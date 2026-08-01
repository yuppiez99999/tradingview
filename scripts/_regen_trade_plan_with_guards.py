# -*- coding: utf-8 -*-
"""
重生成 trade_plan_20260727.json 并应用 7-Guard 链
执行流程:
  1. generate_daily_trade_plan.py 生成基础 trade_plan (已完成)
  2. risk_guard_integrator.run_all_guards("2026-07-27") 应用 7 Guard + 一致性校验
"""
import sys
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from utils.risk_guard_integrator import RiskGuardIntegrator

print("=" * 70)
print("应用 7-Guard 链到 trade_plan_20260727.json")
print("=" * 70)

# 加载最新生成的 trade_plan
plan_path = BASE / "v8.3_institutional" / "trade_plans" / "trade_plan_20260727.json"
with open(plan_path, "r", encoding="utf-8") as f:
    before_plan = json.load(f)

print("\n=== Guard 前状态 ===")
ms = before_plan.get("market_state", {})
print(f"  market_state.circuit_level: {ms.get('circuit_level')}")
print(f"  market_state.build_allowed: {ms.get('build_allowed')}")
print(f"  market_state.spot_build_allowed: {ms.get('spot_build_allowed')}")
print(f"  hedge_capital (顶层): {before_plan.get('hedge_capital'):,}")
print(f"  stock_etf_capital (顶层): {before_plan.get('stock_etf_capital'):,}")

# 运行 7-Guard 链
integrator = RiskGuardIntegrator(report_date="2026-07-25")  # 用最近一个交易日的 pnl report
updated_plan = integrator.run_all_guards("2026-07-27")

# 重新加载并校验
with open(plan_path, "r", encoding="utf-8") as f:
    after_plan = json.load(f)

print("\n=== Guard 后状态 ===")
ms = after_plan.get("market_state", {})
print(f"  market_state.circuit_level: {ms.get('circuit_level')}")
print(f"  market_state.build_allowed: {ms.get('build_allowed')}")
print(f"  market_state.spot_build_allowed: {ms.get('spot_build_allowed')}")
print(f"  market_state.liquidity_crisis: {ms.get('liquidity_crisis')}")

he = after_plan.get("hedge_execution", {})
cs = he.get("cost_summary", {})
print(f"\n  hedge_execution.execution_status: {he.get('execution_status')}")
print(f"  hedge_execution.cost_summary.within_budget: {cs.get('within_budget')}")
print(f"  hedge_execution.cost_summary.hedge_mode: {cs.get('hedge_mode')}")
print(f"  hedge_execution.cost_summary.total_cost: ¥{cs.get('total_cost'):,}")
print(f"  hedge_execution.cost_summary.budget_threshold: ¥{cs.get('budget_threshold'):,}")

fo = he.get("futures_orders", [])
oo = he.get("options_orders", [])
print(f"\n  futures_orders 数量: {len(fo)}")
print(f"  options_orders 数量: {len(oo)}")
for o in oo:
    print(f"    {o.get('instrument'):25s} ¥{o.get('premium_budget'):>10,} | status={o.get('status')}")

rg = after_plan.get("risk_guard", {})
print(f"\n  risk_guard.hedge_action: {rg.get('hedge_action')}")

# === 字段一致性校验 ===
print("\n=== 字段一致性校验 ===")
errors = []

# 1. 顶层 hedge_capital 与 portfolio.yaml 一致
if after_plan.get("hedge_capital") != 1_000_000:
    errors.append(f"顶层 hedge_capital={after_plan.get('hedge_capital')} 应为 1,000,000")
else:
    print("  ✅ 顶层 hedge_capital = 1,000,000 (与 portfolio.yaml 一致)")

# 2. 顶层 stock_etf_capital 与 portfolio.yaml 一致
if after_plan.get("stock_etf_capital") != 4_000_000:
    errors.append(f"顶层 stock_etf_capital={after_plan.get('stock_etf_capital')} 应为 4,000,000")
else:
    print("  ✅ 顶层 stock_etf_capital = 4,000,000 (与 portfolio.yaml 一致)")

# 3. circuit_level 与 build_allowed 一致性
cl = ms.get("circuit_level", "NORMAL").upper()
ba = ms.get("build_allowed")
sba = ms.get("spot_build_allowed")
if cl == "CRITICAL" and (ba != False or sba != False):
    errors.append(f"circuit_level=CRITICAL 时 build_allowed={ba}, spot_build_allowed={sba} 必须为 False")
elif cl == "WARNING" and sba != False:
    errors.append(f"circuit_level=WARNING 时 spot_build_allowed={sba} 必须为 False")
else:
    print(f"  ✅ circuit_level={cl} 与 build_allowed={ba} / spot_build_allowed={sba} 一致")

# 4. hedge_execution 字段一致性
if he:
    if not he.get("execution_status"):
        errors.append("hedge_execution 缺少 execution_status 字段")
    elif cs.get("within_budget") == False and he.get("execution_status") != "CANCELLED":
        errors.append(f"within_budget=false 时 execution_status={he.get('execution_status')} 应为 CANCELLED")
    elif cs.get("within_budget") == True and he.get("execution_status") != "PENDING":
        errors.append(f"within_budget=true 时 execution_status={he.get('execution_status')} 应为 PENDING")
    else:
        print(f"  ✅ execution_status={he.get('execution_status')} 与 within_budget={cs.get('within_budget')} 一致")

    # 5. 期货订单与 hedge_mode 一致性
    if cs.get("hedge_mode") == "OPTIONS_ONLY" and len(fo) > 0:
        errors.append(f"hedge_mode=OPTIONS_ONLY 但仍生成 {len(fo)} 笔期货订单")
    elif cs.get("hedge_mode") == "OPTIONS_ONLY" and len(fo) == 0:
        print("  ✅ hedge_mode=OPTIONS_ONLY, futures_orders=0 (一致)")

    # 6. 订单 status 一致性
    all_orders = fo + oo
    expected_status = "CANCELLED_OVER_BUDGET" if not cs.get("within_budget") else "PENDING"
    for o in all_orders:
        if o.get("status") != expected_status:
            errors.append(f"订单 {o.get('instrument')} status={o.get('status')} 应为 {expected_status}")
    if all_orders and not any(e.startswith("订单") for e in errors):
        print(f"  ✅ 所有 {len(all_orders)} 笔订单 status={expected_status} 一致")

# 7. futures_options_hedge 字段同步
foh = after_plan.get("futures_options_hedge", {})
if foh:
    if foh.get("hedge_mode") != cs.get("hedge_mode"):
        errors.append(f"futures_options_hedge.hedge_mode={foh.get('hedge_mode')} 与 cost_summary.hedge_mode={cs.get('hedge_mode')} 不一致")
    else:
        print(f"  ✅ futures_options_hedge.hedge_mode 与 hedge_execution.cost_summary.hedge_mode 一致 ({foh.get('hedge_mode')})")

# 8. hedge_fund_overlays.v77_notes 与 market_state 一致
hfo = after_plan.get("hedge_fund_overlays", {})
notes = hfo.get("v77_notes", {})
if notes:
    if notes.get("build_allowed") != ms.get("build_allowed"):
        errors.append(f"v77_notes.build_allowed={notes.get('build_allowed')} 与 market_state.build_allowed={ms.get('build_allowed')} 不一致")
    else:
        print(f"  ✅ v77_notes.build_allowed 与 market_state.build_allowed 一致 ({notes.get('build_allowed')})")

# === 总结 ===
print("\n" + "=" * 70)
if errors:
    print(f"❌ 发现 {len(errors)} 个字段一致性问题:")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    print("✅ trade_plan_20260727.json 字段一致性全部通过 (P0-01 修复完成)")
print("=" * 70)
