# -*- coding: utf-8 -*-
"""检查 P1 改进当前状态"""
import re
from pathlib import Path

# === 1. 标的池 ===
print('=== 1. 标的池状态 ===')
su_files = list(Path('.').rglob('symbol_universe.py'))
print('symbol_universe.py:', [str(f) for f in su_files])

if su_files:
    with open(su_files[0], 'r', encoding='utf-8') as f:
        content = f.read()
    codes = re.findall(r'"(\d{6})"', content)
    unique_codes = list(dict.fromkeys(codes))
    print('标的数量:', len(unique_codes))
    if unique_codes:
        print('前10:', unique_codes[:10])
        print('后10:', unique_codes[-10:])

# === 2. Fundamentals ===
print()
print('=== 2. Fundamentals 覆盖率 ===')
fund_dirs = [Path('data_cache/fundamentals'), Path('cache/fundamentals')]
for d in fund_dirs:
    if d.exists():
        files = list(d.glob('*.parquet')) + list(d.glob('*.json'))
        print(f'{d}: {len(files)} 个文件')
        for f in sorted(files)[:3]:
            print(f'  {f.name}')

# 也搜索所有 fundamentals 相关文件
all_fund = list(Path('data_cache').rglob('*fundamental*')) + list(Path('cache').rglob('*fundamental*'))
print(f'所有 fundamentals 相关文件: {len(all_fund)}')

# === 3. 510300 ETF ===
print()
print('=== 3. 510300 ETF 数据 ===')
etf_file = Path('data_cache/historical_510300_5y_base.parquet')
if etf_file.exists():
    import pandas as pd
    df = pd.read_parquet(etf_file)
    print(f'510300 ETF: 存在, {len(df)} 行')
    print(f'  日期: {df.index[0]} ~ {df.index[-1]}')
else:
    print('510300 ETF: 不存在')
    sh = Path('data_cache/historical_sh000300_index.parquet')
    if sh.exists():
        import pandas as pd
        df = pd.read_parquet(sh)
        print(f'  替代 sh000300_index: 存在, {len(df)} 行')
        print(f'  日期: {df.index[0]} ~ {df.index[-1]}')

# === 4. 检查所有 data_cache 中的 historical_*_5y_base.parquet ===
print()
print('=== 4. 已下载的 OHLCV 数据 ===')
ohlcv_files = list(Path('data_cache').glob('historical_*_5y_base.parquet'))
print(f'OHLCV 文件数: {len(ohlcv_files)}')
# 提取标的代码
ohlcv_codes = []
for f in ohlcv_files:
    m = re.search(r'historical_(\d{6})_5y_base', f.name)
    if m:
        ohlcv_codes.append(m.group(1))
    else:
        m2 = re.search(r'historical_(.+?)_5y_base', f.name)
        if m2:
            ohlcv_codes.append(m2.group(1))
print('标的数据文件:', sorted(set(ohlcv_codes))[:20], '...' if len(ohlcv_codes) > 20 else '')
print('总标的数:', len(set(ohlcv_codes)))

# 检查 510300 是否在 OHLCV 文件中
if '510300' in ohlcv_codes:
    print('510300 在 OHLCV 文件中: 是')
else:
    print('510300 在 OHLCV 文件中: 否 (需要回填)')
