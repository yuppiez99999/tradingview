"""对比 V6 vs V6.1 vs V6.2 月度收益, 找出 V6.2 退化原因"""
import json
from pathlib import Path

# 加载 V6.2 结果
with open('output/validation_reports/lgb_backtest_v6_2_profit_taking_20260725_065118.json', encoding='utf-8') as f:
    v62 = json.load(f)

# 加载 V6.1 结果
v61_files = sorted(Path('output/validation_reports').glob('lgb_backtest_v6_1*.json'), key=lambda p: p.stat().st_mtime)
with open(v61_files[-1], encoding='utf-8') as f:
    v61 = json.load(f)

# 加载 V6 结果
v6_files = sorted(Path('output/validation_reports').glob('lgb_backtest_v6_alpha_quality*.json'), key=lambda p: p.stat().st_mtime)
with open(v6_files[-1], encoding='utf-8') as f:
    v6 = json.load(f)

# 对比月度收益
print("日期         V6          V6.1        V6.2        V6.2-V6     V6.2-V6.1")
print("-" * 75)
for i, r in enumerate(v62['records']):
    date = r['date']
    v6_ret = v6['records'][i]['portfolio_return'] if i < len(v6['records']) else None
    v61_ret = v61['records'][i]['portfolio_return'] if i < len(v61['records']) else None
    v62_ret = r['portfolio_return']
    diff_v6 = (v62_ret - v6_ret) * 100 if v6_ret is not None else None
    diff_v61 = (v62_ret - v61_ret) * 100 if v61_ret is not None else None
    v6_str = f"{v6_ret*100:>7.2f}%" if v6_ret is not None else "    N/A"
    v61_str = f"{v61_ret*100:>7.2f}%" if v61_ret is not None else "    N/A"
    v62_str = f"{v62_ret*100:>7.2f}%"
    d6_str = f"{diff_v6:>+7.2f}%" if diff_v6 is not None else "    N/A"
    d61_str = f"{diff_v61:>+7.2f}%" if diff_v61 is not None else "    N/A"
    print(f"{date:<12} {v6_str}  {v61_str}  {v62_str}  {d6_str}  {d61_str}")

print()
print("=" * 75)
print("V6.2 止盈应用详情 (pt_applied):")
print("-" * 75)
for r in v62['records']:
    pt = r.get('profit_taking', {})
    applied = pt.get('applied_this_month', [])
    if applied:
        print(f"{r['date']}: {applied}")

print()
print("=" * 75)
print("V6 止盈应用详情 (pt_applied):")
print("-" * 75)
for r in v6['records']:
    pt = r.get('profit_taking', {})
    applied = pt.get('applied_this_month', [])
    if applied:
        print(f"{r['date']}: {applied}")

print()
print("=" * 75)
print("V6.1 止盈应用详情 (pt_applied):")
print("-" * 75)
for r in v61['records']:
    pt = r.get('profit_taking', {})
    applied = pt.get('applied_this_month', [])
    if applied:
        print(f"{r['date']}: {applied}")
