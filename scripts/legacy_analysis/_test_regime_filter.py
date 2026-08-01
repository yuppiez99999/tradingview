# -*- coding: utf-8 -*-
"""快速验证三层过滤对 2022-07 和 2024-12 崩盘月的捕捉效果"""
from pathlib import Path

import pandas as pd

proxy = "510300"
ma_period = 60
slope_window = 5
vol_lookback = 20
vol_high_threshold = 0.015
mom_lookback = 20
mom_crash = -0.05
mom_severe = -0.10

base_file = Path("data_cache") / f"historical_{proxy}_5y_base.parquet"
df = pd.read_parquet(base_file)
if hasattr(df.index, "tz") and df.index.tz is not None:
    df.index = df.index.tz_localize(None)
df = df.sort_index()

# 测试关键月份
test_dates = [
    "2022-07-01", "2022-08-01", "2024-12-02",  # 崩盘月
    "2025-08-01", "2025-09-01",  # 极端盈利月
    "2023-03-01", "2024-09-02",  # 正常上涨月
]

print(f"{'日期':12s} | {'close':>8s} | {'MA60':>8s} | {'base':>6s} | {'vol_20d':>8s} | {'vol_ovr':>6s} | {'mom_20d':>8s} | {'mom_ovr':>6s} | {'final':>6s} | {'regime':>8s}")
print("-" * 100)

for date_str in test_dates:
    cutoff = pd.Timestamp(date_str).normalize()
    df_cut = df[df.index <= cutoff]
    if len(df_cut) < ma_period + slope_window:
        print(f"{date_str}: 数据不足")
        continue

    close = df_cut["close"]
    ma = close.rolling(ma_period).mean()
    latest_close = float(close.iloc[-1])
    latest_ma = float(ma.iloc[-1])
    ma_slope = float(ma.iloc[-1] - ma.iloc[-1 - slope_window])
    ma_rising = ma_slope > 0
    above_ma = latest_close > latest_ma

    # 第一层: MA60
    if above_ma and ma_rising:
        regime, base_factor = "bull", 1.0
    elif above_ma and not ma_rising:
        regime, base_factor = "choppy", 0.8
    elif not above_ma and ma_rising:
        regime, base_factor = "rebound", 0.6
    else:
        regime, base_factor = "bear", 0.5

    # 第二层: 波动率
    daily_rets = close.pct_change()
    recent_vol = float(daily_rets.tail(vol_lookback).std())
    vol_override = 0.8 if recent_vol > vol_high_threshold else 1.0

    # 第三层: 动量
    mom_20d = float(close.iloc[-1] / close.iloc[-1 - mom_lookback] - 1)
    if mom_20d < mom_severe:
        mom_override = 0.4
    elif mom_20d < mom_crash:
        mom_override = 0.6
    else:
        mom_override = 1.0

    # 综合
    factor = max(base_factor * vol_override * mom_override, 0.3)

    print(f"{date_str:12s} | {latest_close:8.3f} | {latest_ma:8.3f} | {base_factor:6.2f} | {recent_vol*100:7.3f}% | {vol_override:6.2f} | {mom_20d*100:+7.2f}% | {mom_override:6.2f} | {factor:6.2f} | {regime:8s}")
