# -*- coding: utf-8 -*-
"""批量更新trade_orders_20260706.json，补全28标的"""
import json
from pathlib import Path

BASE = Path(r"e:\各种PY程序\28-终极量化交易系统7.1")
ORDERS_FILE = BASE / "reports" / "trade_orders_20260706.json"

with open(ORDERS_FILE, "r", encoding="utf-8") as f:
    data = json.load(f)

new_symbols = [
    {"code": "sh688981", "name": "中芯国际", "session": "morning", "shares": 1000, "est_price": 142.93, "style": "科技", "risk": "高", "note": "上午批次 09:30-10:30"},
    {"code": "sh688981", "name": "中芯国际", "session": "afternoon", "shares": 1000, "est_price": 142.93, "style": "科技", "risk": "高", "note": "下午批次 14:00-14:30"},
    {"code": "sh603019", "name": "中科曙光", "session": "morning", "shares": 1000, "est_price": 94.42, "style": "科技", "risk": "高", "note": "上午批次 09:30-10:30"},
    {"code": "sh603019", "name": "中科曙光", "session": "afternoon", "shares": 1000, "est_price": 94.42, "style": "科技", "risk": "高", "note": "下午批次 14:00-14:30"},
    {"code": "sh600219", "name": "南山铝业", "session": "morning", "shares": 35700, "est_price": 4.19, "style": "制造", "risk": "中", "note": "上午批次 09:30-10:30"},
    {"code": "sh600219", "name": "南山铝业", "session": "afternoon", "shares": 35700, "est_price": 4.19, "style": "制造", "risk": "中", "note": "下午批次 14:00-14:30"},
    {"code": "sh600019", "name": "宝钢股份", "session": "morning", "shares": 26700, "est_price": 5.61, "style": "制造", "risk": "中", "note": "上午批次 09:30-10:30"},
    {"code": "sh600019", "name": "宝钢股份", "session": "afternoon", "shares": 26700, "est_price": 5.61, "style": "制造", "risk": "中", "note": "下午批次 14:00-14:30"},
    {"code": "sz000792", "name": "盐湖股份", "session": "morning", "shares": 5100, "est_price": 29.41, "style": "资源", "risk": "高", "note": "上午批次 09:30-10:30"},
    {"code": "sz000792", "name": "盐湖股份", "session": "afternoon", "shares": 5100, "est_price": 29.41, "style": "资源", "risk": "高", "note": "下午批次 14:00-14:30"},
    {"code": "sh601318", "name": "中国平安", "session": "morning", "shares": 2000, "est_price": 48.96, "style": "金融", "risk": "中", "note": "上午批次 09:30-10:30"},
    {"code": "sh601318", "name": "中国平安", "session": "afternoon", "shares": 2000, "est_price": 48.96, "style": "金融", "risk": "中", "note": "下午批次 14:00-14:30"},
    {"code": "sz000858", "name": "五粮液", "session": "morning", "shares": 2000, "est_price": 73.21, "style": "消费", "risk": "中", "note": "上午批次 09:30-10:30"},
    {"code": "sz000858", "name": "五粮液", "session": "afternoon", "shares": 2000, "est_price": 73.21, "style": "消费", "risk": "中", "note": "下午批次 14:00-14:30"},
]

max_priority = max(o["priority"] for o in data["morning_orders"] + data["afternoon_orders"])

for sym in new_symbols:
    max_priority += 1
    order = {
        "priority": max_priority,
        "code": sym["code"],
        "name": sym["name"],
        "session": sym["session"],
        "shares": sym["shares"],
        "est_price": sym["est_price"],
        "limit_price": round(sym["est_price"] * 1.008, 2),
        "est_amount": round(sym["shares"] * sym["est_price"], 2),
        "side": "BUY",
        "order_type": "LIMIT",
        "style": sym["style"],
        "risk": sym["risk"],
        "note": sym["note"],
        "technical_alpha": 1.0,
    }
    if sym["session"] == "morning":
        data["morning_orders"].append(order)
    else:
        data["afternoon_orders"].append(order)

with open(ORDERS_FILE, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=4)

print(f"[OK] trade_orders_20260706.json 已更新为28标的")
print(f"  morning_orders: {len(data['morning_orders'])}")
print(f"  afternoon_orders: {len(data['afternoon_orders'])}")
