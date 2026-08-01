# -*- coding: utf-8 -*-
"""临时测试: 验证 data_provider 能否返回真实历史数据"""
import sys
sys.path.insert(0, '.')

from utils.data_provider import MarketDataProvider

dp = MarketDataProvider(backtest_mode=True)
dp.set_backtest_date('2026-06-01')

test_syms = ['600519', '000858', '510300', '600276', '002371']
for sym in test_syms:
    try:
        df = dp.get_historical_data(sym, period='1y')
        if df is None or df.empty:
            print(f'{sym}: EMPTY')
        else:
            cols = list(df.columns)[:8]
            last = df.index[-1]
            src = df.attrs.get('source', '?')
            print(f'{sym}: rows={len(df)} cols={cols} last={last} src={src}')
    except Exception as e:
        print(f'{sym}: ERROR {type(e).__name__}: {e}')
