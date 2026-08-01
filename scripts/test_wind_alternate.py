# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"E:\各种PY程序\15_每日工作流")

from morning_market_fetcher import (
    _exec_wind_analytics, _exec_wind_economic,
    _fetch_sina_coal_inventory, _fetch_ths_coal_inventory, _web_search_fallback_coal,
    _is_wind_success,
)

print("=== 测试 Wind MCP 港口库存 ===")
# 尝试使用 economic_data 查询港口库存
r = _exec_wind_economic("S0243027,S0243028,S0243029")  # 假设的港口库存指标ID
print(f"economic_data 结果: 成功={_is_wind_success(r)}")
if r.get('data'):
    print(f"  数据: {str(r['data'])[:300]}")

# 尝试使用 analytics_data 查询
r2 = _exec_wind_analytics("秦皇岛港动力煤库存、曹妃甸港动力煤库存、黄骅港动力煤库存")
print(f"analytics_data 结果: 成功={_is_wind_success(r2)}")
if r2.get('data'):
    print(f"  数据: {str(r2['data'])[:300]}")

print("\n=== 测试 Wind MCP 碳市场 ===")
r3 = _exec_wind_analytics("全国碳排放权交易市场CEA最新收盘价、CEA最新成交量、CCER挂牌价")
print(f"CEA/CCER 结果: 成功={_is_wind_success(r3)}")
if r3.get('data'):
    print(f"  数据: {str(r3['data'])[:300]}")

r4 = _exec_wind_economic("S0243027")  # 假设的CEA指标ID
print(f"CEA economic_data 结果: 成功={_is_wind_success(r4)}")
if r4.get('data'):
    print(f"  数据: {str(r4['data'])[:300]}")

print("\n=== 测试 新浪/同花顺/全网 港口库存 ===")
for port in ["秦皇岛", "曹妃甸", "黄骅港"]:
    print(f"\n{port}:")
    r = _fetch_sina_coal_inventory(port)
    print(f"  新浪: {r.get('source', 'N/A')} - {r.get('error', 'OK')}")
    if r.get('data'):
        print(f"    数据: {r['data']}")
    
    r = _fetch_ths_coal_inventory(port)
    print(f"  同花顺: {r.get('source', 'N/A')} - {r.get('error', 'OK')}")
    if r.get('data'):
        print(f"    数据: {r['data']}")
    
    r = _web_search_fallback_coal(port)
    print(f"  全网: {r.get('source', 'N/A')} - {r.get('error', 'OK')}")
    if r.get('data'):
        print(f"    数据: {r['data']}")
