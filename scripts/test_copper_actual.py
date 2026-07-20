# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"E:\各种PY程序\15_每日工作流")

import json
from morning_market_fetcher import _format_wind_result

# 加载实际的铜数据
with open(r"E:\各种PY程序\每日报告归档\2026-07-17\morning_market_data_20260717.json", "r", encoding="utf-8") as f:
    data = json.load(f)

copper_data = data.get("copper", {})

print("=== 检查铜数据结构 ===")
print(f"copper_data keys: {copper_data.keys()}")

for key in ["kline", "inventory", "basis"]:
    item = copper_data.get(key)
    print(f"\n{key}:")
    if item is None:
        print("  None")
        continue
    print(f"  type: {type(item)}")
    print(f"  keys: {item.keys() if isinstance(item, dict) else 'N/A'}")
    print(f"  source: {item.get('source', 'N/A')}")
    print(f"  error: {item.get('error', 'N/A')}")
    
    data_field = item.get("data")
    print(f"  data type: {type(data_field)}")
    if isinstance(data_field, list):
        print(f"  data length: {len(data_field)}")
        if data_field:
            print(f"  first item keys: {data_field[0].keys() if isinstance(data_field[0], dict) else 'N/A'}")
            rows = data_field[0].get("rows", [])
            print(f"  rows length: {len(rows)}")
            if rows:
                print(f"  first row: {rows[0]}")
    elif isinstance(data_field, dict):
        print(f"  data keys: {data_field.keys()}")
    
    result = _format_wind_result(item, key)
    print(f"  _format_wind_result: {result}")
