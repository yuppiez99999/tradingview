# -*- coding: utf-8 -*-
"""V4 DSR + Walk-Forward 稳定性验证

对比 V3 结果:
  V3: 年化18.25%, 回撤9.59%, Sharpe 1.236, WF Sharpe CV=0.70, DSR n_trials<=5
  V4: 添加月度止盈机制 (单标的>30%→下月×0.5, 组合>10%→下月×0.8)
"""
import json
import sys
import glob
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

# 添加 validation 模块路径
sys.path.insert(0, str(Path("v8.3_institutional/src/validation").resolve()))

# 自动查找最新的V4回测结果 (按修改时间排序, 取最新)
import os
v4_files = glob.glob("output/validation_reports/lgb_backtest_v4*.json")
if not v4_files:
    print("ERROR: 未找到V4回测结果文件")
    sys.exit(1)
v4_files.sort(key=lambda x: os.path.getmtime(x))
v4_file = Path(v4_files[-1])
print(f"加载V4结果: {v4_file}")

with open(v4_file, "r", encoding="utf-8") as f:
    data = json.load(f)

records = data.get("records", [])
if not records:
    print("ERROR: 无回测记录")
    sys.exit(1)

returns = pd.Series([r["portfolio_return"] for r in records])
n = len(returns)

# 基础指标
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
print("V4 DSR + Walk-Forward 验证 (月度止盈机制)")
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

# === Deflated Sharpe Ratio ===
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

# === Walk-Forward 稳定性 (3个窗口, 每个15个月) ===
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
        "window": i,
        "start": records[start]["date"],
        "end": records[end-1]["date"],
        "n": len(win_returns),
        "ann_ret": win_ann_ret,
        "ann_vol": win_ann_vol,
        "sharpe": win_sharpe,
        "max_dd": win_dd,
        "pos_months": win_pos,
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
print(f"稳定性: {'达标' if stable else '未达标'}")

# === 极端月份依赖 ===
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

# === 止盈统计 ===
print()
print("-" * 70)
print("月度止盈统计")
print("-" * 70)
pt_symbol_months = 0
pt_portfolio_months = 0
pt_applied_months = 0
for r in records:
    pt = r.get("profit_taking", {})
    if pt.get("symbols"):
        pt_symbol_months += 1
    if pt.get("portfolio_factor", 1.0) < 1.0:
        pt_portfolio_months += 1
    if pt.get("applied_this_month"):
        pt_applied_months += 1
print(f"单标的止盈信号触发月数: {pt_symbol_months}")
print(f"组合止盈信号触发月数: {pt_portfolio_months}")
print(f"止盈减仓执行月数: {pt_applied_months}")

# === 回撤熔断统计 ===
print()
print("-" * 70)
print("回撤熔断统计")
print("-" * 70)
dd_stats = {"normal": 0, "warning": 0, "severe": 0}
for r in records:
    level = r.get("drawdown_breaker", {}).get("level", "normal")
    dd_stats[level] = dd_stats.get(level, 0) + 1
print(f"normal: {dd_stats['normal']}, warning: {dd_stats['warning']}, severe: {dd_stats['severe']}")

# === 板块违例统计 ===
sector_violation_months = sum(1 for r in records if r.get("market_regime", {}).get("sector_violations"))
print(f"板块违例月份: {sector_violation_months}")

# === 保存结果 ===
output_path = Path("output/validation_reports")
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
result = {
    "timestamp": datetime.now().isoformat(),
    "data_source": str(v4_file),
    "period": f"{records[0]['date']}~{records[-1]['date']}",
    "n_months": n,
    "annual_return": ann_ret,
    "annual_volatility": ann_vol,
    "max_drawdown": max_dd,
    "sharpe_ratio": sharpe,
    "win_rate": win_rate,
    "skewness": skew,
    "kurtosis": kurt,
    "dsr_results": dsr_results,
    "dsr_max_pass_n_trials": max_pass,
    "walk_forward": {
        "windows": windows,
        "sharpe_cv": sharpe_cv,
        "return_cv": return_cv,
        "stable": stable,
    },
    "extreme_months_removed": {
        "dates": top2_dates,
        "returns": top2_returns,
        "annual_return_without": ann_ret_without,
    },
    "profit_taking_stats": {
        "symbol_signal_months": pt_symbol_months,
        "portfolio_signal_months": pt_portfolio_months,
        "applied_months": pt_applied_months,
    },
    "drawdown_breaker_stats": dd_stats,
    "sector_violation_months": sector_violation_months,
}

out_file = output_path / f"dsr_walkforward_v4_{ts}.json"
with open(out_file, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2, default=str)

# === V4 vs V3 vs V2 对比 ===
print()
print("=" * 70)
print("V4 vs V3 vs V2 对比:")
print(f"{'指标':<20} {'V2(15%)':<14} {'V3(10%)':<14} {'V4(止盈)':<14} {'V4-V3':<10}")
print("-" * 72)
v3_ann = 0.1825
v3_dd = 0.0959
v3_sharpe = 1.236
v3_cv = 0.70
v3_dsr = 5
v3_ext = 0.0958
print(f"{'年化收益':<20} {'18.82%':<14} {'18.25%':<14} {f'{ann_ret*100:.2f}%':<14} {f'{(ann_ret-v3_ann)*100:+.2f}%':<10}")
print(f"{'最大回撤':<20} {'12.81%':<14} {'9.59%':<14} {f'{max_dd*100:.2f}%':<14} {f'{(max_dd-v3_dd)*100:+.2f}%':<10}")
print(f"{'Sharpe':<20} {'1.103':<14} {'1.236':<14} {f'{sharpe:.3f}':<14} {f'{sharpe-v3_sharpe:+.3f}':<10}")
print(f"{'WF Sharpe CV':<20} {'0.80':<14} {'0.70':<14} {f'{sharpe_cv:.2f}':<14} {f'{sharpe_cv-v3_cv:+.2f}':<10}")
print(f"{'DSR max n_trials':<20} {'8':<14} {'5':<14} {f'{max_pass}':<14} {f'{max_pass-v3_dsr:+d}':<10}")
print(f"{'去极端月年化':<20} {'10.67%':<14} {'9.58%':<14} {f'{ann_ret_without*100:.2f}%':<14} {f'{(ann_ret_without-v3_ext)*100:+.2f}%':<10}")
dd_trigger = f"w{dd_stats['warning']}+s{dd_stats['severe']}"
print(f"{'回撤熔断触发':<20} {'w10+s3':<14} {'w11+s0':<14} {dd_trigger:<14}")
print(f"{'止盈触发月数':<20} {'N/A':<14} {'N/A':<14} {f'符号{pt_symbol_months}+组合{pt_portfolio_months}':<14}")
print("=" * 72)
print(f"\n结果已保存: {out_file}")
