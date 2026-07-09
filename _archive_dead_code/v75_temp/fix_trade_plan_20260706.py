# -*- coding: utf-8 -*-
"""修复 trade_plan_20260706.json：移除3个被剔除标的，并重算订单统计"""
import json
from pathlib import Path

ROOT = Path(r"e:\各种PY程序\28-终极量化交易系统7.1")
PLAN = ROOT / "v7.5_institutional" / "trade_plans" / "trade_plan_20260706.json"
REMOVE_CODES = {"sz000792", "sh601318", "sz000858"}

with open(PLAN, "r", encoding="utf-8") as f:
    plan = json.load(f)

plan["phase"]["asset_count"] = 25

plan["execution_plan"]["morning_orders"] = [
    o for o in plan["execution_plan"].get("morning_orders", []) if o.get("code") not in REMOVE_CODES
]
plan["execution_plan"]["afternoon_orders"] = [
    o for o in plan["execution_plan"].get("afternoon_orders", []) if o.get("code") not in REMOVE_CODES
]

plan["execution_plan"]["total_orders"] = len(plan["execution_plan"]["morning_orders"]) + len(plan["execution_plan"]["afternoon_orders"])
plan["execution_plan"]["morning_total"] = round(sum(o.get("est_amount", 0) for o in plan["execution_plan"]["morning_orders"]), 2)
plan["execution_plan"]["afternoon_total"] = round(sum(o.get("est_amount", 0) for o in plan["execution_plan"]["afternoon_orders"]), 2)
plan["execution_plan"]["grand_total"] = round(plan["execution_plan"]["morning_total"] + plan["execution_plan"]["afternoon_total"], 2)

for item in plan.get("phase_roadmap", []):
    item["asset_count"] = 25

with open(PLAN, "w", encoding="utf-8") as f:
    json.dump(plan, f, ensure_ascii=False, indent=2)

print(f"[OK] morning={len(plan['execution_plan']['morning_orders'])}, afternoon={len(plan['execution_plan']['afternoon_orders'])}, total={plan['execution_plan']['total_orders']}")
print(f"[OK] morning_total={plan['execution_plan']['morning_total']}, afternoon_total={plan['execution_plan']['afternoon_total']}, grand_total={plan['execution_plan']['grand_total']}")
