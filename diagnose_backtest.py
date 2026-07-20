# -*- coding: utf-8 -*-
"""诊断 backtest_runner 收益计算"""
from datetime import datetime
import pandas as pd
from utils.data_provider import MarketDataProvider

provider = MarketDataProvider()
for date_str in ["2024-01-01", "2024-02-01", "2024-03-01", "2024-06-03"]:
    df = provider.get_historical_data("600519", period="2y")
    if df is None or df.empty:
        print(f"{date_str}: df is empty")
        continue
    compare_date = pd.Timestamp(date_str).normalize()
    future = df[df.index > compare_date]
    print(f"{date_str}: df range={df.index.min()} ~ {df.index.max()}, len={len(df)}, future_len={len(future)}")
    if len(future) >= 22:
        print(f"  iloc[0]={future.iloc[0].name} close={future.iloc[0]['close']}")
        print(f"  iloc[21]={future.iloc[21].name} close={future.iloc[21]['close']}")
        print(f"  return={float(future.iloc[21]['close'] / future.iloc[0]['close'] - 1):.4f}")
    else:
        print(f"  future too short")
