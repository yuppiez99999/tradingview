# -*- coding: utf-8 -*-
import pandas as pd
from utils.data_provider import MarketDataProvider

provider = MarketDataProvider(backtest_mode=True)
SYMBOLS = ["600519", "000858", "601318", "000001", "600036", "601398", "600276", "000063"]

for date in ["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01", "2024-05-01", "2024-06-01"]:
    provider.set_backtest_date(date)
    cutoff = pd.Timestamp(date).normalize()
    print(f"\n===== {date} =====")
    for symbol in SYMBOLS:
        df = provider.get_historical_data(symbol, period='3y')
        if df is None or df.empty or len(df) < 30:
            print(f"  {symbol}: no data")
            continue
        if hasattr(df.index, "tz") and df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        if hasattr(cutoff, "tz") and cutoff.tz is not None:
            cutoff = cutoff.tz_localize(None)
        df = df[df.index <= cutoff]
        close = df['close'].dropna()
        ret_5d = float(close.iloc[-1] / close.iloc[-6] - 1) if len(close) > 5 else 0.0
        ret_20d = float(close.iloc[-1] / close.iloc[-21] - 1) if len(close) > 20 else 0.0
        ret_60d = float(close.iloc[-1] / close.iloc[-61] - 1) if len(close) > 60 else 0.0
        momentum = 0.40 * ret_5d + 0.35 * ret_20d + 0.25 * ret_60d
        strength = max(-1.0, min(1.0, momentum * 15))
        print(f"  {symbol}: ret5d={ret_5d:.4f} ret20d={ret_20d:.4f} ret60d={ret_60d:.4f} mom={momentum:.4f} strength={strength:.4f}")
