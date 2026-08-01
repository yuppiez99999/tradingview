# -*- coding: utf-8 -*-
"""对比 V6 vs V6.2 在 2024-09 的权重, 找出反转调整逻辑差异"""
import json
from pathlib import Path

# 加载 V6.2 结果
with open('output/validation_reports/lgb_backtest_v6_2_profit_taking_20260725_065118.json', 'r', encoding='utf-8') as f:
    v62 = json.load(f)

# 加载 V6 结果
v6_files = sorted(Path('output/validation_reports').glob('lgb_backtest_v6_alpha_quality*.json'), key=lambda p: p.stat().st_mtime)
with open(v6_files[-1], 'r', encoding='utf-8') as f:
    v6 = json.load(f)

# 对比 2024-09-02 的权重
target_date = '2024-09-02'
print(f"=== {target_date} 权重对比 ===")
print(f"{'标的':<10} {'V6 权重':<12} {'V6.2 权重':<12} {'差异':<12}")
print("-" * 50)
v6_weights = None
v62_weights = None
for r in v6['records']:
    if r['date'] == target_date:
        v6_weights = r['weights']
        break
for r in v62['records']:
    if r['date'] == target_date:
        v62_weights = r['weights']
        break

if v6_weights and v62_weights:
    all_symbols = sorted(set(list(v6_weights.keys()) + list(v62_weights.keys())))
    for sym in all_symbols:
        v6_w = v6_weights.get(sym, 0)
        v62_w = v62_weights.get(sym, 0)
        diff = v62_w - v6_w
        print(f"{sym:<10} {v6_w:<12.6f} {v62_w:<12.6f} {diff:<+12.6f}")

    print()
    print(f"V6 总权重: {sum(v6_weights.values()):.6f}")
    print(f"V6.2 总权重: {sum(v62_weights.values()):.6f}")

# 对比 2024-09-02 的标的收益
print()
print(f"=== {target_date} 标的收益对比 ===")
v6_rets = None
v62_rets = None
for r in v6['records']:
    if r['date'] == target_date:
        v6_rets = r['returns']
        break
for r in v62['records']:
    if r['date'] == target_date:
        v62_rets = r['returns']
        break

if v6_rets and v62_rets:
    print(f"{'标的':<10} {'V6 收益':<12} {'V6.2 收益':<12}")
    print("-" * 35)
    for sym in sorted(set(list(v6_rets.keys()) + list(v62_rets.keys()))):
        v6_r = v6_rets.get(sym, 0)
        v62_r = v62_rets.get(sym, 0)
        if abs(v6_r - v62_r) > 0.0001:
            print(f"{sym:<10} {v6_r:<12.6f} {v62_r:<12.6f}  ← 差异")
        else:
            print(f"{sym:<10} {v6_r:<12.6f} {v62_r:<12.6f}")

# 检查 V6 的 defensive 字段中的 symbol_rets_20d
print()
print("=== V6 的 defensive 字段 (2024-09-02) ===")
for r in v6['records']:
    if r['date'] == target_date:
        defensive = r['market_regime'].get('defensive', {})
        print(f"defensive keys: {list(defensive.keys())}")
        if 'symbol_rets_20d' in defensive:
            symbol_rets = defensive['symbol_rets_20d']
            print("\n20日收益率 (用于反转调整):")
            for sym, ret in sorted(symbol_rets.items()):
                flag = ""
                if ret < -0.10:
                    flag = " ← 超跌加仓×1.3"
                elif ret > 0.10:
                    flag = " ← 超涨减仓×0.7"
                print(f"  {sym}: {ret*100:+.2f}%{flag}")
        break

# 检查 V6.2 的 defensive 字段
print()
print("=== V6.2 的 defensive 字段 (2024-09-02) ===")
for r in v62['records']:
    if r['date'] == target_date:
        defensive = r['market_regime'].get('defensive', {})
        print(f"defensive keys: {list(defensive.keys())}")
        print(f"defensive: {defensive}")
        break
