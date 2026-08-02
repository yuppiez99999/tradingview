"""评估不同止损规则的影响范围, 避免 V7 过度优化"""
import json

import numpy as np
import pandas as pd

# 加载 V6.2 结果
with open('output/validation_reports/lgb_backtest_v6_2_profit_taking_20260725_065118.json', encoding='utf-8') as f:
    v62 = json.load(f)

records = v62['records']

# 1. 评估个股级止损 (个股月收益 < 阈值 → 下月权重 ×因子)
print("=== 1. 个股级止损影响评估 ===")
thresholds = [-0.10, -0.15, -0.20, -0.25]
factors = [0.3, 0.5, 0.0]  # 清仓=0.0, 减仓=0.3/0.5

for threshold in thresholds:
    trigger_count = 0
    trigger_months = set()
    for r in records:
        for _sym, ret in r['returns'].items():
            if ret < threshold:
                trigger_count += 1
                trigger_months.add(r['date'])
    print(f"  阈值 {threshold*100:.0f}%: 触发 {trigger_count} 次, 涉及 {len(trigger_months)} 个月")

# 2. 评估组合级止损 (组合月收益 < 阈值 → 下月仓位 ×因子)
print("\n=== 2. 组合级止损影响评估 ===")
port_thresholds = [-0.02, -0.03, -0.04, -0.05]
for threshold in port_thresholds:
    trigger_months = [r['date'] for r in records if r['portfolio_return'] < threshold]
    print(f"  阈值 {threshold*100:.0f}%: 触发 {len(trigger_months)} 个月 ({trigger_months})")

# 3. 评估现有回撤熔断的触发情况
print("\n=== 3. 现有回撤熔断触发情况 ===")
dd_breaker_months = [r for r in records if r['drawdown_breaker']['factor'] < 1.0]
print(f"  触发月份: {len(dd_breaker_months)}")
for r in dd_breaker_months:
    print(f"    {r['date']}: 收益={r['portfolio_return']*100:+.2f}%, dd={r['drawdown_breaker']['prev_dd']*100:.2f}%, factor={r['drawdown_breaker']['factor']}")

# 4. 评估现有止盈的触发情况
print("\n=== 4. 现有止盈触发情况 ===")
pt_months = [r for r in records if r['profit_taking']['applied_this_month']]
print(f"  触发月份: {len(pt_months)}")
for r in pt_months:
    print(f"    {r['date']}: {r['profit_taking']['applied_this_month']}")

# 5. 模拟 V7 方案: 个股止损(-15%/×0.3) + 组合止损(-3%/×0.5)
print("\n=== 5. V7 方案模拟: 个股止损(-15%/×0.3) + 组合止损(-3%/×0.5) ===")
print("（模拟下月权重调整, 不改变 alpha 信号）")

# 模拟 V7 回测
equity_curve = 1.0
equity_peak = 1.0
prev_month_return = 0.0
v7_returns = []

# 个股止损状态
stock_stop_loss = {}  # {symbol: 减仓因子}
# 组合止损状态
portfolio_stop_factor = 1.0

for _i, r in enumerate(records):
    weights = dict(r['weights'])  # 复制原始权重

    # 应用组合止损 (基于上月信号)
    if portfolio_stop_factor < 1.0:
        weights = {s: w * portfolio_stop_factor for s, w in weights.items()}

    # 应用个股止损 (基于上月信号)
    if stock_stop_loss:
        for sym, factor in stock_stop_loss.items():
            if sym in weights:
                weights[sym] = weights[sym] * factor

    # 应用回撤熔断 (与原逻辑相同)
    current_dd = (equity_peak - equity_curve) / equity_peak if equity_peak > 0 else 0.0
    dd_factor = 1.0
    if prev_month_return < 0 and current_dd >= 0.10:
        dd_factor = 0.4
    elif prev_month_return < 0 and current_dd >= 0.05:
        dd_factor = 0.6
    if dd_factor < 1.0:
        weights = {s: w * dd_factor for s, w in weights.items()}

    # 应用止盈 (与 V6.2 相同)
    # ... (省略, 使用原始 pt_applied)

    # 计算组合收益
    rets = r['returns']
    port_return = float(np.sum([weights.get(s, 0.0) * rets.get(s, 0.0) for s in weights]))

    # 检测止损信号 (供下月使用)
    new_stock_stop = {}
    for sym, ret in rets.items():
        if ret < -0.15:  # 个股止损: -15%
            new_stock_stop[sym] = 0.3
    new_portfolio_stop = 1.0
    if port_return < -0.03:  # 组合止损: -3%
        new_portfolio_stop = 0.5

    stock_stop_loss = new_stock_stop
    portfolio_stop_factor = new_portfolio_stop

    # 更新权益曲线
    equity_curve *= (1 + port_return)
    equity_peak = max(equity_peak, equity_curve)
    prev_month_return = port_return

    v7_returns.append(port_return)

# 计算V7指标
v7_returns = np.array(v7_returns)
v7_ann_ret = float((1 + v7_returns.mean()) ** 12 - 1)
v7_ann_vol = float(v7_returns.std() * np.sqrt(12))
v7_sharpe = v7_ann_ret / v7_ann_vol if v7_ann_vol > 0 else 0
v7_equity = np.cumprod(1 + v7_returns)
v7_peak = np.maximum.accumulate(v7_equity)
v7_max_dd = float(np.max((v7_peak - v7_equity) / v7_peak))

# Walk-Forward
n = len(v7_returns)
ws = n // 3
v7_sharpes = []
for i in range(3):
    start = i * ws
    end = (i + 1) * ws if i < 2 else n
    win_rets = v7_returns[start:end]
    win_ann = float((1 + win_rets.mean()) ** 12 - 1)
    win_vol = float(win_rets.std() * np.sqrt(12))
    win_sharpe = win_ann / win_vol if win_vol > 0 else 0
    v7_sharpes.append(win_sharpe)
    print(f"  V7 窗口{i}: 年化={win_ann*100:.2f}%, Sharpe={win_sharpe:.3f}")

v7_sharpe_cv = float(np.std(v7_sharpes) / (np.mean(v7_sharpes) + 1e-9))

print("\n  V7 总指标:")
print(f"    年化: {v7_ann_ret*100:.2f}% (V6.2: 14.35%)")
print(f"    Sharpe: {v7_sharpe:.3f} (V6.2: 1.092)")
print(f"    最大回撤: {v7_max_dd*100:.2f}% (V6.2: 7.90%)")
print(f"    Sharpe CV: {v7_sharpe_cv:.4f} (V6.2: 0.55, 目标<0.5)")
print(f"    峰度: {float(pd.Series(v7_returns).kurt()):.2f} (V6.2: 6.86)")

# 去极端月
sorted_rets = np.sort(v7_returns)[::-1]
returns_without_top2 = np.delete(v7_returns, np.argsort(v7_returns)[-2:])
v7_ann_without = float((1 + returns_without_top2.mean()) ** 12 - 1)
print(f"    去极端月年化: {v7_ann_without*100:.2f}% (V6.2: 6.48%, 目标>=8%)")

