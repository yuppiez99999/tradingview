# -*- coding: utf-8 -*-
"""
手动补入 512100、510500 到 2026-07-21 交易计划
"""
import json
from pathlib import Path

PLAN_FILE = Path(r"e:\各种PY程序\28-终极量化交易系统7.1\v7.5_institutional\trade_plans\trade_plan_20260721.json")

NEW_ORDERS = [
    {
        "priority": 33,
        "code": "512100",
        "name": "中证1000ETF南方",
        "session": "morning",
        "shares": 1600,
        "est_price": 2.65,
        "limit_price": 2.673,
        "est_amount": 4240.0,
        "side": "BUY",
        "order_type": "LIMIT",
        "style": "宽基",
        "risk": "中",
        "note": "上午批次 09:30-10:30 (阶段1 第12天) — ETF资金净流入20亿信号补入",
        "technical_alpha": 1.0,
    },
    {
        "priority": 35,
        "code": "510500",
        "name": "中证500ETF南方",
        "session": "morning",
        "shares": 700,
        "est_price": 6.2,
        "limit_price": 6.255,
        "est_amount": 4340.0,
        "side": "BUY",
        "order_type": "LIMIT",
        "style": "宽基",
        "risk": "中",
        "note": "上午批次 09:30-10:30 (阶段1 第12天) — ETF资金净流入18.5亿信号补入",
        "technical_alpha": 1.0,
    },
]

with open(PLAN_FILE, "r", encoding="utf-8") as f:
    plan = json.load(f)

# 注入 morning_orders
morning = plan.get("execution_plan", {}).get("morning_orders", [])
for order in NEW_ORDERS:
    if not any(o.get("code") == order["code"] for o in morning):
        morning.append(order)
plan["execution_plan"]["morning_orders"] = morning

# 同步更新统计
plan["execution_plan"]["stock_orders_count"] = len(morning) + len(plan.get("execution_plan", {}).get("afternoon_orders", []))
plan["execution_plan"]["total_amount"] = sum(o.get("est_amount", 0) for o in morning) + sum(o.get("est_amount", 0) for o in plan.get("execution_plan", {}).get("afternoon_orders", []))

with open(PLAN_FILE, "w", encoding="utf-8") as f:
    json.dump(plan, f, ensure_ascii=False, indent=2)

print(f"已补入 {len(NEW_ORDERS)} 笔订单到 7/21 计划")
for o in NEW_ORDERS:
    print(f"  {o['code']} {o['name']} {o['shares']}股 @ {o['est_price']} = {o['est_amount']}")
print(f"更新后上午订单: {len(morning)} 笔")
print(f"更新后现货总额: {plan['execution_plan']['total_amount']}")
