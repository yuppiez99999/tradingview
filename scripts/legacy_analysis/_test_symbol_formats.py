# -*- coding: utf-8 -*-
"""诊断: 测试不同代码格式和周期对 get_historical_data 的影响"""
import sys

sys.path.insert(0, '.')

from utils.data_provider import get_historical_data

tests = [
    ("688041", "1y"),      # 纯代码 1y
    ("688041", "2y"),      # 纯代码 2y
    ("688041.SH", "1y"),   # 带后缀 1y
    ("688041.SH", "2y"),   # 带后缀 2y
    ("002371", "1y"),      # 纯代码 1y
    ("002371.SZ", "1y"),   # 带后缀 1y
    ("600519", "2y"),      # 已知可用的纯代码 2y
    ("600519.SH", "2y"),   # 已知可用的带后缀 2y
]

for sym, period in tests:
    try:
        df = get_historical_data(sym, period)
        if df is None or df.empty:
            print(f"  {sym:12s} {period:3s}: EMPTY")
        else:
            print(f"  {sym:12s} {period:3s}: rows={len(df)} last={df.index[-1].date()}")
    except Exception as e:
        print(f"  {sym:12s} {period:3s}: ERROR {type(e).__name__}: {e}")
