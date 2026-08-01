# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"E:\各种PY程序\15_每日工作流")

import akshare as ak

print("=== 测试 akshare 港口库存 ===")
try:
    # 尝试获取港口库存数据
    df = ak.futures_portal_inventory(symbol="动力煤")
    print(f"成功获取数据，形状: {df.shape}")
    print(df.head())
except Exception as e:
    print(f"获取失败: {e}")

print("\n=== 测试 akshare 碳市场 ===")
try:
    # 尝试获取碳市场数据
    df = ak.carbon_cncaa_carbon_asindex()
    print(f"成功获取数据，形状: {df.shape}")
    print(df.head())
except Exception as e:
    print(f"获取失败: {e}")

print("\n=== 测试 akshare 动力煤期货 ===")
try:
    # 尝试获取动力煤期货价格
    df = ak.futures_zh_daily_sina(symbol="ZC0")
    print(f"成功获取数据，形状: {df.shape}")
    print(df.head())
except Exception as e:
    print(f"获取失败: {e}")

print("\n=== 测试 akshare 沪铜期货 ===")
try:
    # 尝试获取沪铜期货价格
    df = ak.futures_zh_daily_sina(symbol="CU0")
    print(f"成功获取数据，形状: {df.shape}")
    print(df.head())
except Exception as e:
    print(f"获取失败: {e}")
