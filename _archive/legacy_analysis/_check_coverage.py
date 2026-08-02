"""检查标的池覆盖情况"""
import re
from pathlib import Path

with open('cache/symbol_universe.py', encoding='utf-8') as f:
    content = f.read()

codes = re.findall(r'"(\d{6}_[SZSH]+)"', content)
unique_codes = list(dict.fromkeys(codes))
print('标的池总数:', len(unique_codes))

pure_codes = [c.split('_')[0] for c in unique_codes]
print('纯代码数:', len(set(pure_codes)))

# 已下载 OHLCV
ohlcv_files = list(Path('data_cache').glob('historical_*_5y_base.parquet'))
ohlcv_codes = set()
for f in ohlcv_files:
    m = re.search(r'historical_(\d{6})_5y_base', f.name)
    if m:
        ohlcv_codes.add(m.group(1))

print('\n已下载 OHLCV:', len(ohlcv_codes), '个标的')

missing = sorted([c for c in set(pure_codes) if c not in ohlcv_codes])
print('缺失 OHLCV:', len(missing), '个标的')
if missing:
    print('缺失列表 (前30):', missing[:30])
    if len(missing) > 30:
        print('  ... 共', len(missing), '个')

# Fundamentals
fund_files = list(Path('cache/fundamentals').glob('*_latest.json'))
fund_codes = set()
for f in fund_files:
    m = re.match(r'(\d{6})_', f.name)
    if m:
        fund_codes.add(m.group(1))

print('\nFundamentals:', len(fund_codes), '个标的')
missing_fund = sorted([c for c in set(pure_codes) if c not in fund_codes])
print('缺失 Fundamentals:', len(missing_fund), '个标的')
if missing_fund:
    print('缺失列表:', missing_fund)
