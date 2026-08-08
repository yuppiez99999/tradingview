#!/usr/bin/env python
"""检查 trade_plan 字段完整性"""
import json
from pathlib import Path

plan_path = Path("v8.3_institutional/trade_plans/trade_plan_20260727.json")
with open(plan_path, encoding="utf-8") as f:
    data = json.load(f)

print("=== trade_plan_20260727.json 字段检查 ===")
print(f"top-level keys: {list(data.keys())}")
print()

# 资金配置
print("=== 资金配置 ===")
print(f"stock_etf_capital: {data.get('stock_etf_capital', 'MISSING')}")
print(f"hedge_capital: {data.get('hedge_capital', 'MISSING')}")
print(f"capital: {data.get('capital', 'MISSING')}")
print()

# risk_guard
rg = data.get("risk_guard", {})
print(f"=== risk_guard (keys: {list(rg.keys())}) ===")
og = rg.get("overnight_gap", {})
print(f"overnight_gap: {og}")
print(f"vol_scale: {rg.get('vol_scale', 'MISSING')}")
print()

# futures_options_hedge
foh = data.get("futures_options_hedge", {})
print(f"=== futures_options_hedge (keys: {list(foh.keys()) if isinstance(foh, dict) else type(foh)}) ===")
print(f"loaded: {foh.get('loaded', 'MISSING') if isinstance(foh, dict) else 'NOT_DICT'}")
print()

# execution_plan
ep = data.get("execution_plan", {})
print(f"=== execution_plan (keys: {list(ep.keys()) if isinstance(ep, dict) else type(ep)}) ===")
print(f"execution_status: {data.get('execution_status', 'MISSING')}")
print(f"day_capital: {ep.get('day_capital', 'MISSING') if isinstance(ep, dict) else 'NOT_DICT'}")
