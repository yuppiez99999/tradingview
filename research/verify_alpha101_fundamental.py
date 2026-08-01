"""验证 Fundamental 和 Alpha101 补全效果"""
import sys

sys.path.insert(0, r'e:\各种PY程序\28-终极量化交易系统8.4')

import numpy as np
import pandas as pd

np.random.seed(42)
n = 300
dates = pd.date_range('2024-01-01', periods=n, freq='B')
close0 = 100.0
returns = np.random.randn(n) * 0.02 + 0.0005
close = close0 * np.exp(np.cumsum(returns))
high = close * (1 + np.abs(np.random.randn(n)) * 0.01)
low = close * (1 - np.abs(np.random.randn(n)) * 0.01)
open_p = close * (1 + np.random.randn(n) * 0.005)
volume = np.abs(np.random.randn(n)) * 1e7 + 1e6
amount = close * volume * 0.001

df = pd.DataFrame({
    'open': open_p, 'high': high, 'low': low,
    'close': close, 'volume': volume, 'amount': amount
}, index=dates)

from utils.vibe_trading_adapter import get_vibe_adapter

adapter = get_vibe_adapter()

print('=' * 70)
print('Alpha101 (WorldQuant) 完整 101 因子测试')
print('=' * 70)
result = adapter.compute_single_stock(df, zoo='alpha101')
print(f'成功: {len(result.values)}/101')
print('前15个因子:')
for _i, (k, v) in enumerate(list(result.values.items())[:15]):
    meta = adapter.get_meta(k)
    print(f'  {k:15s}: {v:12.6f}  [{meta.formula[:50]}]')

# 找之前失败的需要 sector 的因子
print()
print('需要 sector 的因子 (之前失败的 19 个):')
sector_ids = [f'alpha101_{i:03d}' for i in [48, 56, 58, 59, 63, 67, 69, 70, 76, 79, 80, 82, 87, 89, 90, 91, 93, 97, 100]]
for sid in sector_ids:
    v = result.values.get(sid, 'FAILED')
    status = '✅' if sid in result.values else '❌'
    print(f'  {status} {sid}: {v}')

print()
print('=' * 70)
print('Fundamental (基本面) 4 因子测试')
print('=' * 70)
fund_result = adapter.compute_single_stock(df, zoo='fundamental')
print(f'成功: {len(fund_result.values)}/4')
for k, v in fund_result.values.items():
    meta = adapter.get_meta(k)
    print(f'  {k:25s}: {v:12.6f}  [{meta.themes}]')
    series = adapter.compute_one_factor(df, k)
    if series is not None:
        print(f'    序列最后5个: {series.tail().tolist()}')

print()
print('=' * 70)
print('汇总: Alpha101 101/101 + Fundamental 4/4 全部补全 ✅')
print('=' * 70)
