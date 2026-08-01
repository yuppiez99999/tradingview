# -*- coding: utf-8 -*-
import sys

sys.path.insert(0, r"E:\各种PY程序\15_每日工作流")

import json

# 加载实际的铜数据
with open(r"E:\各种PY程序\每日报告归档\2026-07-17\morning_market_data_20260717.json", "r", encoding="utf-8") as f:
    data = json.load(f)

copper_data = data.get("copper", {})

for key in ["kline", "inventory", "basis"]:
    item = copper_data.get(key)
    if item is None:
        continue
    data_field = item.get("data", [])
    if not data_field or not isinstance(data_field, list):
        continue
    first = data_field[0]
    columns = first.get("columns", [])
    col_names = [c.get("name", "") if isinstance(c, dict) else str(c) for c in columns]
    print(f"\n=== {key} ===")
    print(f"列名: {col_names}")
    print(f"第一行: {first.get('rows', [[None]])[0]}")
