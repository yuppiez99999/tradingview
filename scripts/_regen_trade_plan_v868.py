# -*- coding: utf-8 -*-
"""
v8.6.8 实盘就绪度 — 重新生成 trade_plan 并应用 7-Guard 链
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "v8.3_institutional"))
sys.path.insert(0, str(BASE / "utils"))

# Step 1: 重新生成基础 trade_plan
print("=" * 72)
print("Step 1: 重新生成 trade_plan_20260727.json")
print("=" * 72)
try:
    from generate_daily_trade_plan import generate_trade_plan
    plan = generate_trade_plan("2026-07-27", 5_000_000)

    import json
    plan_path = BASE / "v8.3_institutional" / "trade_plans" / "trade_plan_20260727.json"
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    print(f"✅ 已生成: {plan_path}")
    print(f"   stock_etf_capital = {plan['stock_etf_capital']:,}")
    print(f"   hedge_capital = {plan['hedge_capital']:,}")
    print(f"   phase.phase_capital = {plan['phase']['phase_capital']:,}")
    print(f"   phase.daily_capital = {plan['phase']['daily_capital']}")
    print(f"   hedge_config.layers.layer1_futures.action = {plan['hedge_config']['layers']['layer1_futures']['action']}")
    print(f"   metadata.version = {plan['metadata']['version']}")
except Exception as e:
    import traceback
    print(f"❌ 生成 trade_plan 失败: {e}")
    traceback.print_exc()
    sys.exit(1)

# Step 2: 应用 7-Guard 链
print("\n" + "=" * 72)
print("Step 2: 应用 7-Guard 风控守卫链")
print("=" * 72)
try:
    from risk_guard_integrator import RiskGuardIntegrator
    integrator = RiskGuardIntegrator(report_date="2026-07-25")
    updated_plan = integrator.run_all_guards(next_trade_date="2026-07-27")

    print("\n✅ 7-Guard 完成")
    ms = updated_plan.get('market_state', {})
    print(f"   circuit_level = {ms.get('circuit_level')}")
    print(f"   build_allowed = {ms.get('build_allowed')}")
    print(f"   spot_build_allowed = {ms.get('spot_build_allowed')}")

    rg = updated_plan.get('risk_guard', {})
    print(f"   vol_scale = {rg.get('vol_scale')}")
    print(f"   vol_action = {rg.get('vol_action')}")
    print(f"   hedge_action = {rg.get('hedge_action')}")

    he = updated_plan.get('hedge_execution', {})
    print(f"   hedge_execution.execution_status = {he.get('execution_status')}")
    print(f"   hedge_execution.futures_orders count = {len(he.get('futures_orders', []))}")
    print(f"   hedge_execution.options_orders count = {len(he.get('options_orders', []))}")

    foh = updated_plan.get('futures_options_hedge', {})
    print(f"   futures_options_hedge.loaded = {foh.get('loaded')}")
    print(f"   futures_options_hedge.orders_count = {foh.get('orders_count')}")

    ep = updated_plan.get('execution_plan', {})
    print(f"   execution_plan.total_orders = {ep.get('total_orders')}")
    print(f"   execution_plan.total_amount = {ep.get('total_amount')}")
    print(f"   execution_plan.day_capital = {ep.get('day_capital')}")
    print(f"   execution_plan.options_orders_count = {ep.get('options_orders_count')}")
except Exception as e:
    import traceback
    print(f"❌ 7-Guard 应用失败: {e}")
    traceback.print_exc()
    sys.exit(1)

print("\n" + "=" * 72)
print("✅ trade_plan 重生成 + 7-Guard 应用 完成")
print("=" * 72)
