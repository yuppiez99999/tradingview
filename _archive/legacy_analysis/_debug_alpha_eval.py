"""诊断: 为什么 _real_alpha_evaluation 返回 mock"""
import sys

sys.path.insert(0, '.')

import pandas as pd

from utils.data_provider import MarketDataProvider

dp = MarketDataProvider(backtest_mode=True)
dp.set_backtest_date('2025-06-01')
cutoff = pd.Timestamp('2025-06-01').normalize()

for sym in ['600519', '000858', '601318']:
    print(f"\n=== {sym} ===")
    df = dp.get_historical_data(sym, period='3y')
    if df is None or df.empty:
        print("  df is None/empty")
        continue
    print(f"  raw rows: {len(df)}, cols: {list(df.columns)}")
    if hasattr(df.index, 'tz') and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df = df[df.index <= cutoff]
    print(f"  truncated rows: {len(df)}, last: {df.index[-1]}")

    if 'close' not in df.columns:
        print("  NO 'close' column!")
        continue
    s = df['close'].dropna()
    print(f"  close series len: {len(s)}")

    if len(s) < 120:
        print(f"  TOO SHORT: {len(s)} < 120")
        continue

    factor = s.pct_change(60).shift(1)
    fwd = s.pct_change(20).shift(-20)
    joined = pd.concat([factor, fwd], axis=1).dropna()
    joined.columns = ['factor', 'fwd']
    print(f"  joined rows after dropna: {len(joined)}")
    if len(joined) > 0:
        ic = float(joined['factor'].corr(joined['fwd']))
        print(f"  IC: {ic}")
        print(f"  factor std: {joined['factor'].std()}")
        ic_ir = ic / (joined['factor'].std() + 1e-9)
        print(f"  IC_IR: {ic_ir}")
    else:
        print("  joined is EMPTY - checking NaN pattern")
        print(f"  factor NaN: {factor.isna().sum()}/{len(factor)}")
        print(f"  fwd NaN: {fwd.isna().sum()}/{len(fwd)}")
        print(f"  factor head: {factor.head(3).tolist()}")
        print(f"  factor tail: {factor.tail(3).tolist()}")
        print(f"  fwd head: {fwd.head(3).tolist()}")
        print(f"  fwd tail: {fwd.tail(3).tolist()}")
