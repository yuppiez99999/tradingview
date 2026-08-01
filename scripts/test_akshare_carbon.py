# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"E:\各种PY程序\15_每日工作流")

import akshare as ak

print("=== 测试 akshare 碳市场数据 ===")
try:
    df = ak.energy_carbon_domestic()
    print(f"energy_carbon_domestic: 形状={df.shape}")
    print(df.head())
    print(df.columns.tolist())
except Exception as e:
    print(f"energy_carbon_domestic 失败: {e}")

print("\n=== 测试 akshare 北京碳市场 ===")
try:
    df = ak.energy_carbon_bj()
    print(f"energy_carbon_bj: 形状={df.shape}")
    print(df.head())
    print(df.columns.tolist())
except Exception as e:
    print(f"energy_carbon_bj 失败: {e}")

print("\n=== 测试 akshare 期货库存 ===")
try:
    df = ak.futures_inventory_em(symbol="动力煤")
    print(f"futures_inventory_em: 形状={df.shape}")
    print(df.head())
    print(df.columns.tolist())
except Exception as e:
    print(f"futures_inventory_em 失败: {e}")

print("\n=== 测试 akshare 99 期货库存 ===")
try:
    df = ak.futures_inventory_99(symbol="动力煤")
    print(f"futures_inventory_99: 形状={df.shape}")
    print(df.head())
    print(df.columns.tolist())
except Exception as e:
    print(f"futures_inventory_99 失败: {e}")
