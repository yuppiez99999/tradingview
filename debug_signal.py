# -*- coding: utf-8 -*-
"""诊断信号：打印单月的 raw momentum 和 strength"""
import pandas as pd
from utils.data_provider import MarketDataProvider

provider = MarketDataProvider(backtest_mode=True)
provider.set_backtest_date("2024-01-01")

SYMBOLS = ["600519", "000858", "601318", "000001", "600036", "601398", "600276", "000063"]

for symbol in SYMBOLS:
    df = provider.get_historical_data(symbol, period='3y')
    if df is None or df.empty or len(df) < 30:
        print(f"{symbol}: no data")
        continue
    cutoff = pd.Timestamp("2024-01-01").normalize()
    if hasattr(df.index, "tz") and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    if hasattr(cutoff, "tz") and cutoff.tz is not None:
        cutoff = cutoff.tz_localize(None)
    df = df[df.index <= cutoff]
    close = df['close'].dropna()
    print(f"{symbol}: len={len(close)}, last={close.iloc[-1]}, last6={close.iloc[-6] if len(close) > 5 else 'N/A'}, ret5d={close.iloc[-1] / close.iloc[-6] - 1 if len(close) > 5 else 0.0}")
