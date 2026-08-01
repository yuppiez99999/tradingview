# -*- coding: utf-8 -*-
"""深入分析 V6.2 Window 1 (2023-07~2024-09) 失败根因

分析内容:
1. Window 1 月度收益分布和 regime 分布
2. LGB 信号在 bear regime 的 IC 表现
3. 反转调整的效果评估
4. 与 Window 0/2 的对比
"""
import json

import numpy as np
import pandas as pd

# 加载 V6.2 结果
with open('output/validation_reports/lgb_backtest_v6_2_profit_taking_20260725_065118.json', 'r', encoding='utf-8') as f:
    v62 = json.load(f)

records = v62['records']

# 定义 3 个窗口
n = len(records)
window_size = n // 3
windows = {
    0: records[:window_size],
    1: records[window_size:2*window_size],
    2: records[2*window_size:],
}

print("=" * 90)
print("V6.2 Window 1 (2023-07~2024-09) 失败根因分析")
print("=" * 90)

# 1. 各窗口基本指标对比
print("\n=== 1. 各窗口基本指标对比 ===")
print(f"{'窗口':<6} {'期间':<28} {'月数':<6} {'年化':<10} {'Sharpe':<10} {'回撤':<10} {'胜率':<10}")
print("-" * 90)
for w_idx, w_records in windows.items():
    returns = pd.Series([r['portfolio_return'] for r in w_records])
    ann_ret = float((1 + returns.mean()) ** 12 - 1)
    ann_vol = float(returns.std() * np.sqrt(12))
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    equity = (1 + returns).cumprod()
    peak = equity.cummax()
    dd = float(((peak - equity) / peak).max())
    win_rate = float((returns > 0).mean())
    period = f"{w_records[0]['date']} ~ {w_records[-1]['date']}"
    print(f"{w_idx:<6} {period:<28} {len(w_records):<6} {ann_ret*100:<10.2f} {sharpe:<10.3f} {dd*100:<10.2f} {win_rate*100:<10.1f}")

# 2. Window 1 月度收益和 regime 分布
print("\n=== 2. Window 1 月度收益和 regime 分布 ===")
print(f"{'日期':<12} {'收益':<10} {'regime':<10} {'factor':<8} {'dd_level':<10} {'dd_factor':<10} {'pt':<6} {'defensive':<30}")
print("-" * 100)
w1_records = windows[1]
for r in w1_records:
    regime = r['market_regime']
    dd = r['drawdown_breaker']
    pt = r['profit_taking']
    defensive = regime.get('defensive', {})
    def_str = f"超跌{defensive.get('n_oversold_increased', 0)}/超涨{defensive.get('n_overbought_reduced', 0)}" if defensive else "无"
    pt_str = "是" if pt.get('applied_this_month') else "否"
    print(f"{r['date']:<12} {r['portfolio_return']*100:<10.2f} {regime.get('regime', 'N/A'):<10} {regime.get('factor', 0):<8.2f} {dd['level']:<10} {dd['factor']:<10.2f} {pt_str:<6} {def_str:<30}")

# 3. Window 1 regime 统计
print("\n=== 3. Window 1 regime 统计 ===")
w1_regimes = [r['market_regime'].get('regime', 'unknown') for r in w1_records]
regime_counts = pd.Series(w1_regimes).value_counts()
print("regime 分布:")
for regime, count in regime_counts.items():
    pct = count / len(w1_records) * 100
    # 计算该 regime 下的平均收益
    regime_returns = [r['portfolio_return'] for r in w1_records if r['market_regime'].get('regime') == regime]
    avg_ret = np.mean(regime_returns) * 100
    print(f"  {regime}: {count}个月 ({pct:.1f}%), 平均月收益={avg_ret:+.2f}%")

# 4. Window 1 反转调整效果
print("\n=== 4. Window 1 反转调整效果 ===")
reversal_months = [r for r in w1_records if r['market_regime'].get('defensive', {}).get('n_oversold_increased', 0) > 0 or r['market_regime'].get('defensive', {}).get('n_overbought_reduced', 0) > 0]
print(f"触发反转调整的月份: {len(reversal_months)}/{len(w1_records)}")
if reversal_months:
    reversal_returns = [r['portfolio_return'] for r in reversal_months]
    non_reversal_returns = [r['portfolio_return'] for r in w1_records if r not in reversal_months]
    print(f"  反转调整月平均收益: {np.mean(reversal_returns)*100:+.2f}%")
    if non_reversal_returns:
        print(f"  非反转调整月平均收益: {np.mean(non_reversal_returns)*100:+.2f}%")

# 5. Window 1 回撤熔断效果
print("\n=== 5. Window 1 回撤熔断效果 ===")
dd_breaker_months = [r for r in w1_records if r['drawdown_breaker']['factor'] < 1.0]
print(f"触发回撤熔断的月份: {len(dd_breaker_months)}/{len(w1_records)}")
if dd_breaker_months:
    dd_returns = [r['portfolio_return'] for r in dd_breaker_months]
    print(f"  熔断月平均收益: {np.mean(dd_returns)*100:+.2f}%")
    for r in dd_breaker_months:
        print(f"    {r['date']}: 收益={r['portfolio_return']*100:+.2f}%, dd={r['drawdown_breaker']['prev_dd']*100:.2f}%, factor={r['drawdown_breaker']['factor']}")

# 6. 对比 Window 0 和 Window 2 的 regime 分布
print("\n=== 6. 各窗口 regime 分布对比 ===")
for w_idx, w_records in windows.items():
    regimes = [r['market_regime'].get('regime', 'unknown') for r in w_records]
    regime_counts = pd.Series(regimes).value_counts()
    print(f"\n窗口 {w_idx} ({w_records[0]['date']} ~ {w_records[-1]['date']}):")
    for regime, count in regime_counts.items():
        pct = count / len(w_records) * 100
        regime_returns = [r['portfolio_return'] for r in w_records if r['market_regime'].get('regime') == regime]
        avg_ret = np.mean(regime_returns) * 100
        print(f"  {regime}: {count}个月 ({pct:.1f}%), 平均月收益={avg_ret:+.2f}%")

# 7. 标的级别分析: Window 1 中哪些标的贡献正/负收益
print("\n=== 7. Window 1 标的级别贡献分析 ===")
symbol_contributions = {}
for r in w1_records:
    weights = r['weights']
    rets = r['returns']
    for sym in weights:
        if sym not in symbol_contributions:
            symbol_contributions[sym] = {'total_return': 0, 'months': 0, 'avg_weight': 0}
        contribution = weights.get(sym, 0) * rets.get(sym, 0)
        symbol_contributions[sym]['total_return'] += contribution
        symbol_contributions[sym]['months'] += 1
        symbol_contributions[sym]['avg_weight'] += weights.get(sym, 0)

print(f"{'标的':<8} {'总贡献':<12} {'平均权重':<12} {'月数':<6}")
print("-" * 40)
sorted_symbols = sorted(symbol_contributions.items(), key=lambda x: x[1]['total_return'])
for sym, data in sorted_symbols:
    avg_w = data['avg_weight'] / data['months'] if data['months'] > 0 else 0
    print(f"{sym:<8} {data['total_return']*100:<12.2f} {avg_w:<12.4f} {data['months']:<6}")
