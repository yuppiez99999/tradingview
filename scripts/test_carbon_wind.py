# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"E:\各种PY程序\15_每日工作流")

from morning_market_fetcher import _exec_wind_analytics, _is_wind_success

r = _exec_wind_analytics("全国碳排放权交易市场CEA最新收盘价、CEA最新成交量、CCER挂牌价")
print(f"成功: {_is_wind_success(r)}")
print(f"错误: {r.get('error', 'N/A')}")
print(f"来源: {r.get('source', 'N/A')}")

if r.get('data'):
    data = r['data']
    print(f"\n数据类型: {type(data)}")
    if isinstance(data, list):
        print(f"数据项数: {len(data)}")
        for i, item in enumerate(data):
            print(f"\n--- 第{i}项 ---")
            print(f"列名: {[c.get('name', '') for c in item.get('columns', [])]}")
            print(f"行数: {len(item.get('rows', []))}")
            if item.get('rows'):
                print(f"第一行: {item['rows'][0]}")
                if len(item.get('rows', [])) > 1:
                    print(f"第二行: {item['rows'][1]}")
