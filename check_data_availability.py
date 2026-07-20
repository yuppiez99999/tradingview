# -*- coding: utf-8 -*-
"""检查回测数据可用性"""
from datetime import datetime
import pandas as pd
from utils.data_provider import MarketDataProvider

provider = MarketDataProvider()
symbols = ["600519", "000858", "601318", "000001", "600036", "601398", "600276", "000063", "002594", "300750", "600900", "601899", "000338", "600585", "002142"]

for symbol in symbols:
    df = provider.get_historical_data(symbol, period="2y")
    if df is not None and not df.empty:
        print(f"{symbol}: {df.index.min().strftime('%Y-%m-%d')} ~ {df.index.max().strftime('%Y-%m-%d')}, len={len(df)}")
    else:
        print(f"{symbol}: 无数据")
