# -*- coding: utf-8 -*-
"""V6 DSR + Walk-Forward 稳定性验证

V6核心改进: 均值回归特征 + 5日前向收益标签 (提升震荡市Alpha信号质量)

对比基准:
  V5.1: 年化15.57%, 回撤9.60%, Sharpe 1.176, WF Sharpe CV=0.67, DSR n_trials<=8
  V4.1: 年化15.71%, 回撤9.27%, Sharpe 1.191, WF Sharpe CV=0.66, DSR n_trials<=8
  V3:   年化18.25%, 回撤9.59%, Sharpe 1.236, WF Sharpe CV=0.70, DSR n_trials<=5

V6目标:
  - WF Sharpe CV < 0.5 (核心目标, V5.1=0.67)
  - 去极端月年化 >= 8%
  - DSR n_trials >= 10
  - 最大回撤 <= 15%
"""
import glob
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path("v8.3_institutional/src/validation").resolve()))

# 自动查找最新的V6回测结果
v6_files = glob.glob("output/validation_reports/lgb_backtest_v6*.json")
if not v6_files:
    print("ERROR: 未找到V6回测结果文件")
    sys.exit(1)
v6_files.sort(key=lambda x: os.path.getmtime(x))
v6_file = Path(v6_files[-1])
print(f"加载V6结果: {v6_file}")

with open(v6_file, "r", encoding="utf-8") as f:
    data = json.load(f)

records = data.get("records", [])
if not records:
    print("ERROR: 无回测记录")
    sys.exit(1)

returns = pd.Series([r["portfolio_return"] for r in records])
n = len(returns)

ann_ret = float((1 + returns.mean()) ** 12 - 1)
ann_vol = float(returns.std() * np.sqrt(12))
sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
equity = (1 + returns).cumprod()
peak = equity.cummax()
dd_series = (peak - equity) / peak
max_dd = float(dd_series.max())
win_rate = float((returns > 0).mean())
skew = float(returns.skew())
kurt = float(returns.kurt())

print("=" * 70)
print("V6 DSR + Walk-Forward 验证 (均值回归特征 + 5日前向收益标签)")
print("=" * 70)
print(f"回测期: {records[0]['date']} ~ {records[-1]['date']} ({n} 个月)")
print(f"年化收益: {ann_ret:.4f} ({ann_ret*100:.2f}%)")
print(f"年化波动: {ann_vol:.4f} ({ann_vol*100:.2f}%)")
print(f"Sharpe:   {sharpe:.4f}")
print(f"最大回撤: {max_dd:.4f} ({max_dd*100:.2f}%)")
print(f"胜率:     {win_rate:.4f} ({win_rate*100:.2f}%)")
print(f"偏度:     {skew:.4f}")
print(f"峰度:     {kurt:.4f}")
print()

# DSR
print("-" * 70)
print("Deflated Sharpe Ratio (DSR)")
print("-" * 70)
try:
    from deflated_sharpe import deflated_sharpe_ratio
    print(f"{'n_trials':>10} | {'DSR':>8} | {'p-value':>10} | {'E[max SR]':>12} | {'pass':>6}")
    print("-" * 60)
    dsr_results = []
    max_pass = 0
    for n_trials in [1, 3, 5, 8, 10, 20, 50, 100]:
        result = deflated_sharpe_ratio(returns, n_trials=n_trials)
        dsr_results.append({
            "n_trials": n_trials,
            "e_max_sr": float(result.e_max_sr),
            "dsr": float(result.deflated_sharpe_ratio),
            "p_value": float(result.p_value),
            "pass": bool(result.is_pass),
        })
        if result.is_pass:
            max_pass = n_trials
        status = "PASS" if result.is_pass else "FAIL"
        print(f"{n_trials:10d} | {result.deflated_sharpe_ratio:8.4f} | {result.p_value:10.6f} | {result.e_max_sr:12.6f} | {status:>6}")
    print(f"\nDSR 最大通过 n_trials: {max_pass} (目标 >=10)")
except Exception as e:
    print(f"DSR 计算失败: {e}")
    dsr_results = []
    max_pass = 0

# Walk-Forward
print()
print("-" * 70)
print("Walk-Forward 稳定性 (3个窗口)")
print("-" * 70)
window_size = n // 3
windows = []
for i in range(3):
    start = i * window_size
    end = (i + 1) * window_size if i < 2 else n
    win_returns = returns.iloc[start:end]
    win_equity = (1 + win_returns).cumprod()
    win_peak = win_equity.cummax()
    win_dd = float(((win_peak - win_equity) / win_peak).max())
    win_ann_ret = float((1 + win_returns.mean()) ** 12 - 1)
    win_ann_vol = float(win_returns.std() * np.sqrt(12))
    win_sharpe = win_ann_ret / win_ann_vol if win_ann_vol > 0 else 0
    win_pos = int((win_returns > 0).sum())
    windows.append({
        "window": i, "start": records[start]["date"], "end": records[end-1]["date"],
        "n": len(win_returns), "ann_ret": win_ann_ret, "ann_vol": win_ann_vol,
        "sharpe": win_sharpe, "max_dd": win_dd, "pos_months": win_pos,
    })
    print(f"窗口{i}: {records[start]['date']} ~ {records[end-1]['date']} "
          f"| 年化={win_ann_ret*100:6.2f}% 波动={win_ann_vol*100:5.2f}% "
          f"Sharpe={win_sharpe:5.3f} 回撤={win_dd*100:5.2f}% 胜月={win_pos}/{len(win_returns)}")

sharpes = [w["sharpe"] for w in windows]
ret_anns = [w["ann_ret"] for w in windows]
sharpe_cv = float(np.std(sharpes) / (np.mean(sharpes) + 1e-9)) if np.mean(sharpes) > 0 else 0
return_cv = float(np.std(ret_anns) / (np.mean(ret_anns) + 1e-9)) if np.mean(ret_anns) > 0 else 0
stable = sharpe_cv < 0.5
print(f"\nSharpe CV: {sharpe_cv:.4f} (目标 <0.5, {'达标' if stable else '未达标'})")
print(f"Return CV: {return_cv:.4f}")

# 极端月份
print()
print("-" * 70)
print("极端月份依赖分析")
print("-" * 70)
sorted_rets = returns.sort_values(ascending=False)
top2_idx = sorted_rets.iloc[:2].index
top2_dates = [records[i]["date"] for i in top2_idx]
top2_returns = sorted_rets.iloc[:2].tolist()
returns_without_top2 = returns.drop(top2_idx)
ann_ret_without = float((1 + returns_without_top2.mean()) ** 12 - 1)
print(f"最高2个月份: {top2_dates} = {[f'{r*100:.2f}%' for r in top2_returns]}")
print(f"去除最高2月后年化: {ann_ret_without*100:.2f}% (目标 >=8%)")

# 防御性调整统计
print()
print("-" * 70)
print("防御性调整统计")
print("-" * 70)
defensive_months = 0
for r in records:
    reg_info = r.get("market_regime", {})
    def_info = reg_info.get("defensive", {})
    if def_info:
        defensive_months += 1
print(f"防御性调整触发月数: {defensive_months}")

# 止盈统计
pt_symbol_months = sum(1 for r in records if r.get("profit_taking", {}).get("symbols"))
pt_portfolio_months = sum(1 for r in records if r.get("profit_taking", {}).get("portfolio_factor", 1.0) < 1.0)
print(f"止盈触发: 符号{pt_symbol_months}月 + 组合{pt_portfolio_months}月")

# 回撤熔断
dd_stats = {"normal": 0, "warning": 0, "severe": 0}
for r in records:
    level = r.get("drawdown_breaker", {}).get("level", "normal")
    dd_stats[level] = dd_stats.get(level, 0) + 1
print(f"回撤熔断: normal={dd_stats['normal']}, warning={dd_stats['warning']}, severe={dd_stats['severe']}")

# 保存
output_path = Path("output/validation_reports")
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
result = {
    "timestamp": datetime.now().isoformat(),
    "data_source": str(v6_file),
    "period": f"{records[0]['date']}~{records[-1]['date']}",
    "n_months": n,
    "annual_return": ann_ret, "annual_volatility": ann_vol,
    "max_drawdown": max_dd, "sharpe_ratio": sharpe,
    "win_rate": win_rate, "skewness": skew, "kurtosis": kurt,
    "dsr_results": dsr_results, "dsr_max_pass_n_trials": max_pass,
    "walk_forward": {"windows": windows, "sharpe_cv": sharpe_cv, "return_cv": return_cv, "stable": stable},
    "extreme_months_removed": {"dates": top2_dates, "returns": top2_returns, "annual_return_without": ann_ret_without},
    "defensive_months": defensive_months,
    "profit_taking_stats": {"symbol_months": pt_symbol_months, "portfolio_months": pt_portfolio_months},
    "drawdown_breaker_stats": dd_stats,
}
out_file = output_path / f"dsr_walkforward_v6_{ts}.json"
with open(out_file, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2, default=str)

# 对比
print()
print("=" * 70)
print("V6 vs V5.1 vs V4.1 vs V3 vs V2 对比:")
print(f"{'指标':<18} {'V2(15%)':<12} {'V3(10%)':<12} {'V4.1(止盈)':<12} {'V5.1(防御)':<12} {'V6(Alpha)':<12} {'V6-V5.1':<10}")
print("-" * 90)
print(f"{'年化收益':<18} {'18.82%':<12} {'18.25%':<12} {'15.71%':<12} {'15.57%':<12} {f'{ann_ret*100:.2f}%':<12} {f'{(ann_ret-0.1557)*100:+.2f}%':<10}")
print(f"{'最大回撤':<18} {'12.81%':<12} {'9.59%':<12} {'9.27%':<12} {'9.60%':<12} {f'{max_dd*100:.2f}%':<12} {f'{(max_dd-0.0960)*100:+.2f}%':<10}")
print(f"{'Sharpe':<18} {'1.103':<12} {'1.236':<12} {'1.191':<12} {'1.176':<12} {f'{sharpe:.3f}':<12} {f'{sharpe-1.176:+.3f}':<10}")
print(f"{'WF Sharpe CV':<18} {'0.80':<12} {'0.70':<12} {'0.66':<12} {'0.67':<12} {f'{sharpe_cv:.2f}':<12} {f'{sharpe_cv-0.67:+.2f}':<10}")
print(f"{'DSR n_trials':<18} {'8':<12} {'5':<12} {'8':<12} {'8':<12} {f'{max_pass}':<12} {f'{max_pass-8:+d}':<10}")
print(f"{'去极端月年化':<18} {'10.67%':<12} {'9.58%':<12} {'8.79%':<12} {'8.65%':<12} {f'{ann_ret_without*100:.2f}%':<12} {f'{(ann_ret_without-0.0865)*100:+.2f}%':<10}")
print("=" * 90)

# 验收检查
print()
print("=" * 70)
print("V6 验收检查:")
print("-" * 70)
checks = [
    ("WF Sharpe CV < 0.5", sharpe_cv < 0.5, f"{sharpe_cv:.4f}"),
    ("年化收益 >= 8%", ann_ret >= 0.08, f"{ann_ret*100:.2f}%"),
    ("去极端月年化 >= 8%", ann_ret_without >= 0.08, f"{ann_ret_without*100:.2f}%"),
    ("最大回撤 <= 15%", max_dd <= 0.15, f"{max_dd*100:.2f}%"),
    ("DSR n_trials >= 10", max_pass >= 10, f"{max_pass}"),
]
all_pass = True
for name, ok, val in checks:
    status = "✅ PASS" if ok else "❌ FAIL"
    print(f"  {status}  {name}: {val}")
    if not ok:
        all_pass = False
print()
if all_pass:
    print("🎉 V6 全部验收通过!")
else:
    print("⚠️  V6 部分验收未通过, 需要进一步优化")
print("=" * 70)
print(f"\n结果已保存: {out_file}")
