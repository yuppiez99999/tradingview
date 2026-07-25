# -*- coding: utf-8 -*-
"""分析窗口1 (2023-07~2024-09) 月度表现, 找出Alpha信号问题"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# 加载V4.1回测结果
v4_file = Path("output/validation_reports/lgb_backtest_v4_1_tuned_20260724_211027.json")
with open(v4_file, "r", encoding="utf-8") as f:
    data = json.load(f)

records = data.get("records", [])

# 窗口1: 2023-07~2024-09
window1_start = "2023-07"
window1_end = "2024-09"

print("=" * 80)
print("窗口1 (2023-07~2024-09) 月度详细分析")
print("=" * 80)
print(f"{'日期':<12} {'组合收益':>10} {'regime':>10} {'factor':>8} {'dd_breaker':>12} {'pt':>6} {'top3标的收益':>30}")
print("-" * 100)

window1_records = []
for r in records:
    date_str = r["date"]
    if window1_start <= date_str <= window1_end:
        window1_records.append(r)
        port_ret = r["portfolio_return"]
        regime = r.get("market_regime", {}).get("regime", "n/a")
        factor = r.get("market_regime", {}).get("factor", "n/a")
        dd_level = r.get("drawdown_breaker", {}).get("level", "n/a")
        pt = r.get("profit_taking", {})
        pt_applied = "yes" if pt.get("applied_this_month") else "no"

        # 找top3标的收益
        rets = r.get("returns", {})
        sorted_rets = sorted(rets.items(), key=lambda x: x[1], reverse=True)[:3]
        top3_str = " | ".join([f"{s}:{v*100:+.1f}%" for s, v in sorted_rets])

        print(f"{date_str:<12} {port_ret*100:>+9.2f}% {regime:>10} {str(factor):>8} {dd_level:>12} {pt_applied:>6} {top3_str:>30}")

# 统计
returns = pd.Series([r["portfolio_return"] for r in window1_records])
print(f"\n窗口1统计:")
print(f"  月数: {len(window1_records)}")
print(f"  平均月收益: {returns.mean()*100:.2f}%")
print(f"  年化收益: {((1+returns.mean())**12-1)*100:.2f}%")
print(f"  正月数: {(returns>0).sum()}/{len(returns)}")
print(f"  负月数: {(returns<0).sum()}/{len(returns)}")
print(f"  最大月收益: {returns.max()*100:.2f}%")
print(f"  最小月收益: {returns.min()*100:.2f}%")

# 分析regime分布
print(f"\nRegime分布:")
regimes = [r.get("market_regime", {}).get("regime", "n/a") for r in window1_records]
for reg in set(regimes):
    count = regimes.count(reg)
    print(f"  {reg}: {count}月 ({count/len(regimes)*100:.0f}%)")

# 分析dd_breaker触发
print(f"\n回撤熔断触发:")
dd_levels = [r.get("drawdown_breaker", {}).get("level", "n/a") for r in window1_records]
for level in set(dd_levels):
    count = dd_levels.count(level)
    print(f"  {level}: {count}月")

# 分析每个标的在窗口1的表现
print(f"\n{'='*80}")
print("窗口1 各标的收益贡献分析")
print(f"{'='*80}")
symbol_returns = {}
for r in window1_records:
    weights = r.get("weights", {})
    rets = r.get("returns", {})
    for sym in rets:
        w = weights.get(sym, 0.0)
        ret = rets.get(sym, 0.0)
        if sym not in symbol_returns:
            symbol_returns[sym] = {"total_return": 0.0, "months": 0, "positive": 0, "negative": 0}
        symbol_returns[sym]["total_return"] += w * ret
        symbol_returns[sym]["months"] += 1
        if ret > 0:
            symbol_returns[sym]["positive"] += 1
        elif ret < 0:
            symbol_returns[sym]["negative"] += 1

# 按总收益贡献排序
sorted_symbols = sorted(symbol_returns.items(), key=lambda x: x[1]["total_return"], reverse=True)
print(f"\n{'标的':<10} {'总贡献':>10} {'月数':>6} {'正月':>6} {'负月':>6} {'胜率':>8}")
print("-" * 50)
for sym, info in sorted_symbols:
    win_rate = info["positive"] / info["months"] if info["months"] > 0 else 0
    print(f"{sym:<10} {info['total_return']*100:>+9.2f}% {info['months']:>6} {info['positive']:>6} {info['negative']:>6} {win_rate*100:>7.1f}%")
