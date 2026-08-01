# -*- coding: utf-8 -*-
"""
手动生成 trade_plan_20260728.json (用于验证自动生成逻辑)
"""
import json
import sys
from pathlib import Path

BASE = Path(r"E:\各种PY程序\28-终极量化交易系统8.4")
sys.path.insert(0, str(BASE / "v8.3_institutional"))

print("=" * 72)
print("生成 trade_plan_20260728.json (验证自动生成逻辑)")
print("=" * 72)

try:
    from generate_daily_trade_plan import generate_trade_plan
    plan = generate_trade_plan("2026-07-28", 5_000_000)

    plan_path = BASE / "v8.3_institutional" / "trade_plans" / "trade_plan_20260728.json"
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)

    print(f"✅ 已生成: {plan_path}")
    print(f"   metadata.version: {plan['metadata']['version']}")
    print(f"   capital: {plan.get('capital', 0):,}")
    print(f"   stock_etf_capital: {plan.get('stock_etf_capital', 0):,}")
    print(f"   hedge_capital: {plan.get('hedge_capital', 0):,}")
    print(f"   phase.phase_capital: {plan['phase']['phase_capital']:,}")
    print(f"   phase.daily_capital: {plan['phase']['daily_capital']}")
    print(f"   phase.capital_ratio: {plan['phase']['capital_ratio']}")
    print(f"   hedge_config.layers.layer1_futures.action: {plan['hedge_config']['layers']['layer1_futures']['action']}")
    print(f"   execution_plan.total_orders: {plan['execution_plan'].get('total_orders', 0)}")
    print(f"   execution_plan.morning_orders count: {len(plan['execution_plan'].get('morning_orders', []))}")
    print(f"   execution_plan.options_orders count: {len(plan['execution_plan'].get('options_orders', []))}")
except Exception as e:
    import traceback
    print(f"❌ 生成失败: {e}")
    traceback.print_exc()
    sys.exit(1)
