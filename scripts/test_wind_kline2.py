# -*- coding: utf-8 -*-
import sys

sys.path.insert(0, r"E:\各种PY程序\15_每日工作流")

from morning_market_fetcher import _exec_wind_kline, _is_wind_success

print("=== 测试 _exec_wind_kline 沪铜期货 ===")
r = _exec_wind_kline("CU.SHF", days=5)
print(f"成功: {_is_wind_success(r)}")
print(f"错误: {r.get('error', 'N/A')}")
print(f"来源: {r.get('source', 'N/A')}")

if r.get('data'):
    data = r['data']
    print(f"\n数据 keys: {data.keys() if isinstance(data, dict) else 'list'}")
    if isinstance(data, dict):
        for k, v in data.items():
            val_str = str(v)
            print(f"  {k}: {val_str[:200]}")
    elif isinstance(data, list):
        print(f"数据项数: {len(data)}")
        for i, item in enumerate(data):
            print(f"\n--- 第{i}项 ---")
            print(f"keys: {item.keys() if isinstance(item, dict) else type(item)}")
            if isinstance(item, dict):
                for k2, v2 in item.items():
                    print(f"  {k2}: {str(v2)[:200]}")
