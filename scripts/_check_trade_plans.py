#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""快速检查7月所有trade_plan订单数"""
import json
from pathlib import Path

root = Path("e:/各种PY程序/28-终极量化交易系统8.4/v8.3_institutional/trade_plans")
files = sorted(root.glob("trade_plan_202607*.json"))

print(f"共 {len(files)} 个 trade_plan 文件\n")
print(f"{'日期':<12} {'orders':<8} {'capital':<10} {'symbols':<5} {'first_3_symbols'}")
print("-" * 60)

for p in files:
    try:
        with open(p, "r", encoding="utf-8") as f:
            j = json.load(f)
        ep = j.get("execution_plan", {})
        mo = ep.get("morning_orders", [])
        ao = ep.get("afternoon_orders", [])
        all_orders = mo + ao
        dc = ep.get("day_capital", 0)
        syms = []
        for o in all_orders:
            if o.get("code"):
                syms.append(o["code"])
        date = p.stem.replace("trade_plan_", "")
        date_fmt = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
        first3 = ",".join(syms[:3]) if syms else "无"
        print(f"{date_fmt:<12} {len(all_orders):<8} {dc:<10} {len(syms):<5} {first3}")
    except Exception as e:
        print(f"{p.name}: 解析失败 {e}")
