"""模拟 V7 方案: 个股波动率调整 + 更激进止盈

V7 设计逻辑:
  问题1: Window 1 (2024-06) 688017/300308 高权重+大跌 → 需要降低高波动股权重
  问题2: Window 2 (2025-08/09) 极端收益导致峰度高 → 需要更激进止盈
  问题3: Sharpe CV=0.55, 需要 Window 1 Sharpe↑ + Window 2 Sharpe↓

V7 方案:
  1. 个股波动率调整: 20日波动率 > 5% → 权重 ×0.7 (降低高波动股暴露)
  2. 单标的止盈: 月收益 > 40% → 下月权重 ×0.4 (更激进, 原 >50%→×0.6)
  3. 组合止盈: 保持 12%/×0.80 (V6.2 设置)
  4. 保持反转调整和回撤熔断不变
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

# 加载 V6.2 结果
with open('output/validation_reports/lgb_backtest_v6_2_profit_taking_20260725_065118.json', encoding='utf-8') as f:
    v62 = json.load(f)

records = v62['records']

# 读取个股 20 日波动率
def get_symbol_vol_20d(symbol, date_str):
    """获取个股在指定日期的 20 日波动率"""
    sym_file = Path(f"data_cache/historical_{symbol}_5y_base.parquet")
    if not sym_file.exists():
        return None
    try:
        df = pd.read_parquet(sym_file)
        if hasattr(df.index, 'tz') and df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        df = df.sort_index()
        cutoff = pd.Timestamp(date_str).normalize()
        df = df[df.index <= cutoff]
        if len(df) < 22:
            return None
        daily_rets = df['close'].pct_change().tail(20)
        return float(daily_rets.std())
    except Exception:
        return None

# 模拟 V7 回测
print("=== V7 模拟: 个股波动率调整(>5%→×0.7) + 止盈(>40%→×0.4) ===")

equity_curve = 1.0
equity_peak = 1.0
prev_month_return = 0.0
v7_returns = []
v7_details = []

# 止盈状态
profit_taking_symbols = {}  # {symbol: 减仓因子}
portfolio_pt_factor = 1.0

for r in records:
    date_str = r['date']
    weights = dict(r['weights'])

    # 应用组合止盈 (V6.2: 12%/×0.80)
    if portfolio_pt_factor < 1.0:
        weights = {s: w * portfolio_pt_factor for s, w in weights.items()}

    # 应用单标的止盈 (V7: >40%→×0.4, V6.2: >50%→×0.6)
    if profit_taking_symbols:
        for sym, factor in profit_taking_symbols.items():
            if sym in weights:
                weights[sym] = weights[sym] * factor

    # V7 新增: 个股波动率调整 (>5% → ×0.7)
    vol_adjustments = {}
    for sym in list(weights.keys()):
        if weights[sym] > 0.005:  # 只调整权重>0.5%的标的
            vol_20d = get_symbol_vol_20d(sym, date_str)
            if vol_20d is not None and vol_20d > 0.05:  # 20日波动率 > 5%
                vol_adjustments[sym] = 0.7
                weights[sym] = weights[sym] * 0.7

    # 应用回撤熔断 (与 V6.2 相同)
    current_dd = (equity_peak - equity_curve) / equity_peak if equity_peak > 0 else 0.0
    dd_factor = 1.0
    dd_level = "normal"
    if prev_month_return < 0 and current_dd >= 0.10:
        dd_factor = 0.4
        dd_level = "severe"
    elif prev_month_return < 0 and current_dd >= 0.05:
        dd_factor = 0.6
        dd_level = "warning"
    if dd_factor < 1.0:
        weights = {s: w * dd_factor for s, w in weights.items()}

    # 计算组合收益
    rets = r['returns']
    port_return = float(np.sum([weights.get(s, 0.0) * rets.get(s, 0.0) for s in weights]))

    # 检测止盈信号 (供下月使用)
    new_pt_symbols = {}
    for sym, ret in rets.items():
        if ret > 0.40:  # V7: 单标的月收益 > 40% (V6.2: >50%)
            new_pt_symbols[sym] = 0.4  # V7: ×0.4 (V6.2: ×0.6)
    new_portfolio_pt = 1.0
    if port_return > 0.12:  # V7: 保持 12% 阈值
        new_portfolio_pt = 0.80

    profit_taking_symbols = new_pt_symbols
    portfolio_pt_factor = new_portfolio_pt

    # 更新权益曲线
    equity_curve *= (1 + port_return)
    equity_peak = max(equity_peak, equity_curve)
    prev_month_return = port_return

    v7_returns.append(port_return)
    v7_details.append({
        'date': date_str,
        'return': port_return,
        'vol_adjusted': len(vol_adjustments),
        'dd_level': dd_level,
        'pt_symbols': len(new_pt_symbols),
    })

# 计算V7指标
v7_returns = np.array(v7_returns)
v7_ann_ret = float((1 + v7_returns.mean()) ** 12 - 1)
v7_ann_vol = float(v7_returns.std() * np.sqrt(12))
v7_sharpe = v7_ann_ret / v7_ann_vol if v7_ann_vol > 0 else 0
v7_equity = np.cumprod(1 + v7_returns)
v7_peak = np.maximum.accumulate(v7_equity)
v7_max_dd = float(np.max((v7_peak - v7_equity) / v7_peak))
v7_kurt = float(pd.Series(v7_returns).kurt())
v7_skew = float(pd.Series(v7_returns).skew())

# Walk-Forward
n = len(v7_returns)
ws = n // 3
v7_sharpes = []
v7_window_details = []
for i in range(3):
    start = i * ws
    end = (i + 1) * ws if i < 2 else n
    win_rets = v7_returns[start:end]
    win_ann = float((1 + win_rets.mean()) ** 12 - 1)
    win_vol = float(win_rets.std() * np.sqrt(12))
    win_sharpe = win_ann / win_vol if win_vol > 0 else 0
    v7_sharpes.append(win_sharpe)
    v7_window_details.append({
        'window': i,
        'start': records[start]['date'],
        'end': records[end-1]['date'],
        'ann_ret': win_ann,
        'sharpe': win_sharpe,
    })

v7_sharpe_cv = float(np.std(v7_sharpes) / (np.mean(v7_sharpes) + 1e-9))

# 去极端月
sorted_idx = np.argsort(v7_returns)
returns_without_top2 = np.delete(v7_returns, sorted_idx[-2:])
v7_ann_without = float((1 + returns_without_top2.mean()) ** 12 - 1)

# 输出结果
print()
print("=== V7 Walk-Forward 窗口指标 ===")
for w in v7_window_details:
    print(f"  窗口{w['window']}: {w['start']} ~ {w['end']} | 年化={w['ann_ret']*100:.2f}% Sharpe={w['sharpe']:.3f}")

print()
print("=== V7 vs V6.2 总指标对比 ===")
print(f"{'指标':<18} {'V6.2':<15} {'V7':<15} {'差异':<15}")
print("-" * 65)
print(f"{'年化收益':<18} {'14.35%':<15} {f'{v7_ann_ret*100:.2f}%':<15} {f'{(v7_ann_ret-0.1435)*100:+.2f}%':<15}")
print(f"{'最大回撤':<18} {'7.90%':<15} {f'{v7_max_dd*100:.2f}%':<15} {f'{(v7_max_dd-0.079)*100:+.2f}%':<15}")
print(f"{'Sharpe':<18} {'1.092':<15} {f'{v7_sharpe:.3f}':<15} {f'{v7_sharpe-1.092:+.3f}':<15}")
print(f"{'峰度':<18} {'6.86':<15} {f'{v7_kurt:.2f}':<15} {f'{v7_kurt-6.86:+.2f}':<15}")
print(f"{'偏度':<18} {'1.98':<15} {f'{v7_skew:.2f}':<15} {f'{v7_skew-1.98:+.2f}':<15}")
print(f"{'WF Sharpe CV':<18} {'0.55':<15} {f'{v7_sharpe_cv:.4f}':<15} {f'{v7_sharpe_cv-0.55:+.4f}':<15}")
print(f"{'去极端月年化':<18} {'6.48%':<15} {f'{v7_ann_without*100:.2f}%':<15} {f'{(v7_ann_without-0.0648)*100:+.2f}%':<15}")

# V6.2 窗口 Sharpe 对比
print()
print("=== 窗口 Sharpe 对比 ===")
print(f"{'窗口':<10} {'V6.2 Sharpe':<15} {'V7 Sharpe':<15} {'差异':<15}")
v62_sharpes = [1.078, 0.330, 1.725]
for i, (w, v62_s) in enumerate(zip(v7_window_details, v62_sharpes)):
    v7_s = w['sharpe']
    print(f"  {i:<8} {v62_s:<15.3f} {v7_s:<15.3f} {v7_s-v62_s:+.3f}")

print()
print("=== V7 验收检查 ===")
checks = [
    ("WF Sharpe CV < 0.5", v7_sharpe_cv < 0.5, f"{v7_sharpe_cv:.4f}"),
    ("年化收益 >= 8%", v7_ann_ret >= 0.08, f"{v7_ann_ret*100:.2f}%"),
    ("去极端月年化 >= 8%", v7_ann_without >= 0.08, f"{v7_ann_without*100:.2f}%"),
    ("最大回撤 <= 15%", v7_max_dd <= 0.15, f"{v7_max_dd*100:.2f}%"),
]
all_pass = True
for name, ok, val in checks:
    status = "✅ PASS" if ok else "❌ FAIL"
    print(f"  {status}  {name}: {val}")
    if not ok:
        all_pass = False

# 波动率调整触发统计
print()
print("=== V7 波动率调整触发统计 ===")
vol_trigger_months = [d for d in v7_details if d['vol_adjusted'] > 0]
print(f"触发月份: {len(vol_trigger_months)}/{len(v7_details)}")
for d in vol_trigger_months[:10]:
    print(f"  {d['date']}: 调整{d['vol_adjusted']}个标的, 收益={v7_returns[v7_details.index(d)]*100:+.2f}%")
