"""分析月度收益,识别极端月份和弱势窗口"""
import json

with open('output/validation_reports/lgb_backtest_45m_regime_mild_20260724_194324.json', encoding='utf-8') as f:
    data = json.load(f)

print(f"年化: {data['annual_return']:.4f}, 回撤: {data['max_drawdown']:.4f}, 胜率: {data['win_rate']:.2f}")
print(f"月份总数: {len(data['records'])}")
print()
print(f"{'日期':12s} | {'月度收益':>10s} | {'regime':10s} | {'factor':>6s} | {'总权重':>8s}")
print('-' * 65)

records = data['records']
for r in records:
    regime = r.get('market_regime', {}).get('regime', 'n/a')
    factor = r.get('market_regime', {}).get('factor', 'n/a')
    exp = r.get('market_regime', {}).get('exposure_after', 0)
    exp_str = f"{exp:.3f}" if isinstance(exp, (int, float)) else str(exp)
    ret = r['portfolio_return'] * 100
    print(f"{r['date']:12s} | {ret:+9.2f}% | {regime:10s} | {factor!s:>6s} | {exp_str:>8s}")

# 按收益排序找出极端月份
print()
print("=== Top 5 最高收益月份 ===")
sorted_recs = sorted(records, key=lambda x: x['portfolio_return'], reverse=True)
for r in sorted_recs[:5]:
    print(f"  {r['date']}: {r['portfolio_return']*100:+.2f}%")

print()
print("=== Top 5 最低收益月份 ===")
for r in sorted_recs[-5:]:
    print(f"  {r['date']}: {r['portfolio_return']*100:+.2f}%")

# 按 regime 统计
print()
print("=== 各 regime 平均收益 ===")
from collections import defaultdict

regime_stats = defaultdict(list)
for r in records:
    regime = r.get('market_regime', {}).get('regime', 'unknown')
    regime_stats[regime].append(r['portfolio_return'])

for regime, rets in regime_stats.items():
    avg = sum(rets) / len(rets)
    print(f"  {regime:12s}: n={len(rets):2d}, avg={avg*100:+.2f}%, win_rate={sum(1 for x in rets if x>0)/len(rets):.2f}")

# 计算 Walk-Forward 三个窗口的收益构成
print()
print("=== Walk-Forward 窗口收益分布 ===")
windows = [
    ("Window 0 (2022-04~2023-06)", "2022-04-01", "2023-06-01"),
    ("Window 1 (2023-07~2024-09)", "2023-07-01", "2024-09-30"),
    ("Window 2 (2024-10~2025-12)", "2024-10-01", "2025-12-31"),
]
for name, start, end in windows:
    win_recs = [r for r in records if start <= r['date'] <= end]
    if win_recs:
        rets = [r['portfolio_return'] for r in win_recs]
        total = 1.0
        for r in rets:
            total *= (1 + r)
        ann_ret = total ** (12/len(rets)) - 1 if len(rets) > 0 else 0
        print(f"  {name}: n={len(win_recs)}, cumulative={total-1:+.2%}, ann={ann_ret:+.2%}")
        # 找出该窗口极端月份
        sorted_win = sorted(win_recs, key=lambda x: x['portfolio_return'], reverse=True)
        if sorted_win:
            print(f"    最佳: {sorted_win[0]['date']} = {sorted_win[0]['portfolio_return']*100:+.2f}%")
            print(f"    最差: {sorted_win[-1]['date']} = {sorted_win[-1]['portfolio_return']*100:+.2f}%")
