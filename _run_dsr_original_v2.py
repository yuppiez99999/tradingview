# -*- coding: utf-8 -*-
"""使用原始DSR模块验证V2结果"""
import sys, json
import numpy as np, pandas as pd
sys.path.insert(0, 'v8.3_institutional/src/validation')
from deflated_sharpe import deflated_sharpe_ratio

with open('output/validation_reports/lgb_backtest_v2_optimized_20260724_205445.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
returns = pd.Series([r['portfolio_return'] for r in data['records']])

print("V2 DSR (original module):")
print(f"{'n_trials':>10} | {'DSR':>8} | {'p-value':>10} | {'E[max SR]':>12} | {'pass':>6}")
print("-" * 60)
max_pass = 0
for n_trials in [1, 3, 5, 8, 10, 20, 50, 100]:
    result = deflated_sharpe_ratio(returns, n_trials=n_trials)
    if result.is_pass:
        max_pass = n_trials
    status = "PASS" if result.is_pass else "FAIL"
    print(f"{n_trials:10d} | {result.deflated_sharpe_ratio:8.4f} | {result.p_value:10.6f} | {result.e_max_sr:12.6f} | {status:>6}")
print(f"\nMax pass n_trials: {max_pass} (V1 was 5, target >=10)")
print(f"Sharpe: {result.sharpe_ratio:.4f}")
