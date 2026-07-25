# -*- coding: utf-8 -*-
"""检查真实 A 股历史数据结构"""
import json
from pathlib import Path

import pandas as pd

PROJECT = Path(r"e:\各种PY程序\28-终极量化交易系统8.4")

print("=" * 70)
print("真实 A 股历史数据结构检查")
print("=" * 70)

# 1. 检查 510300.json
etf_file = PROJECT / "data" / "etf_fallback" / "510300.json"
print(f"\n[1] 510300 ETF JSON: {etf_file}")
print(f"    大小: {etf_file.stat().st_size} bytes")
with open(etf_file, "r", encoding="utf-8") as f:
    etf_data = json.load(f)
print(f"    类型: {type(etf_data).__name__}")
if isinstance(etf_data, dict):
    print(f"    顶层 keys: {list(etf_data.keys())[:10]}")
    # 打印第一条记录
    for k, v in etf_data.items():
        if isinstance(v, list) and v:
            print(f"    {k}[0]: {v[0]}")
            print(f"    {k}长度: {len(v)}")
            break
        elif isinstance(v, dict):
            print(f"    {k} keys: {list(v.keys())[:10]}")
        else:
            print(f"    {k}: {str(v)[:100]}")
elif isinstance(etf_data, list):
    print(f"    列表长度: {len(etf_data)}")
    print(f"    第一条: {etf_data[0] if etf_data else 'empty'}")

# 2. 检查 parquet 文件
parquet_file = PROJECT / "cache" / "ohlcv" / "000333_SZ_2y.parquet"
print(f"\n[2] 个股 parquet: {parquet_file.name}")
df = pd.read_parquet(parquet_file)
print(f"    形状: {df.shape}")
print(f"    列名: {list(df.columns)}")
print(f"    索引: {df.index.name}, dtype={df.index.dtype}")
print(f"    日期范围: {df.index.min()} -> {df.index.max()}")
print(f"    前 3 行:\n{df.head(3)}")
print(f"    末 3 行:\n{df.tail(3)}")

# 3. 列出所有 parquet 标的
ohlcv_dir = PROJECT / "cache" / "ohlcv"
symbols = sorted([f.stem.replace("_2y", "") for f in ohlcv_dir.glob("*_2y.parquet")])
print(f"\n[3] 所有标的 ({len(symbols)} 个):")
for i, s in enumerate(symbols):
    print(f"    {i+1:>2}. {s}", end="" if (i+1) % 5 else "\n")
print()

# 4. 检查是否有现成的数据加载器
print(f"\n[4] 数据加载器接口检查")
import sys
sys.path.insert(0, str(PROJECT))
try:
    from utils.data_provider import MarketDataProvider
    print(f"    ✓ utils.data_provider.MarketDataProvider 可导入")
    import inspect
    sig = inspect.signature(MarketDataProvider.__init__) if hasattr(MarketDataProvider, "__init__") else "N/A"
    print(f"    __init__ 签名: {sig}")
    methods = [m for m in dir(MarketDataProvider) if not m.startswith("_")][:10]
    print(f"    公开方法: {methods}")
except Exception as e:
    print(f"    ✗ 导入失败: {e}")

# 5. 检查 alpha_factor_library 接口
print(f"\n[5] AlphaFactorLibrary 接口检查")
try:
    from utils.alpha_factor_library import AlphaFactorLibrary, FactorValue, FactorLibraryResult
    print(f"    ✓ AlphaFactorLibrary 可导入")
    import inspect
    sig = inspect.signature(AlphaFactorLibrary.compute_all)
    print(f"    compute_all 签名: {sig}")
    # FactorValue 字段
    if hasattr(FactorValue, "__dataclass_fields__"):
        print(f"    FactorValue 字段: {list(FactorValue.__dataclass_fields__.keys())}")
    else:
        print(f"    FactorValue 不是 dataclass, attrs: {[a for a in dir(FactorValue) if not a.startswith('_')][:10]}")
except Exception as e:
    print(f"    ✗ 导入失败: {e}")

print("\n" + "=" * 70)
