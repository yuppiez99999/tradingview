# -*- coding: utf-8 -*-
import os
import sys

# 修复路径
sys.path.insert(0, r"E:\各种PY程序\15_每日工作流")

from morning_market_fetcher import (
    _fetch_sina_coal_price,
    _fetch_ths_coal_price,
    _web_search_fallback_coal_price,
    _fetch_sina_coal_inventory,
    _fetch_ths_coal_inventory,
    _web_search_fallback_coal,
)

print("=== 测试动力煤价格 ===")
print("SINA:", _fetch_sina_coal_price())
print("THS:", _fetch_ths_coal_price())
print("WEB:", _web_search_fallback_coal_price())

print("\n=== 测试港口库存 ===")
for port in ["秦皇岛", "曹妃甸", "黄骅港"]:
    print(f"\n{port}:")
    print("  SINA:", _fetch_sina_coal_inventory(port))
    print("  THS:", _fetch_ths_coal_inventory(port))
    print("  WEB:", _web_search_fallback_coal(port))
