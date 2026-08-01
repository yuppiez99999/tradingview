# -*- coding: utf-8 -*-
"""DSR + Walk-Forward 验证 V2 - 读取优化后的回测结果"""
import json
import sys
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

# 读取 V2 优化版回测结果
result_files = sorted(Path("output/validation_reports").glob("lgb_backtest_v2_optimized_*.json"))
if not result_files:
    print("ERROR: No V2 backtest result found")
    sys.exit(1)

result_file = result_files[-1]
print(f"Reading: {result_file}")

with open(result_file, "r", encoding="utf-8") as f:
    data = json.load(f)

records = data["records"]
returns = pd.Series([r["portfolio_return"] for r in records])

# 基本指标
annual_return = float((1 + returns.mean()) ** 12 - 1)
annual_vol = float(returns.std() * np.sqrt(12))
sharpe = annual_return / annual_vol if annual_vol > 0 else 0
equity = (1 + returns).cumprod()
peak = equity.cummax()
dd_series = (peak - equity) / peak
max_dd = float(dd_series.max())
win_rate = float((returns > 0).mean())
skewness = float(returns.skew())
kurtosis = float(returns.kurt())

print(f"\n{'='*60}")
print("V2 优化版回测结果")
print(f"{'='*60}")
print(f"回测期: {data['period']}")
print(f"月度数: {len(records)}")
print(f"年化收益: {annual_return:.4f} ({annual_return*100:.2f}%)")
print(f"年化波动: {annual_vol:.4f} ({annual_vol*100:.2f}%)")
print(f"夏普比率: {sharpe:.4f}")
print(f"最大回撤: {max_dd:.4f} ({max_dd*100:.2f}%)")
print(f"胜率: {win_rate:.4f} ({win_rate*100:.1f}%)")
print(f"偏度: {skewness:.4f}")
print(f"峰度: {kurtosis:.4f}")

# Walk-Forward 稳定性 (3个窗口, 每个15个月)
print(f"\n{'='*60}")
print("Walk-Forward 稳定性")
print(f"{'='*60}")
n = len(records)
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
    print(f"Window {i} ({records[start]['date']} ~ {records[end-1]['date']}): "
          f"ann={win_ann_ret*100:+.2f}%, sharpe={win_sharpe:.2f}, dd={win_dd*100:.2f}%, pos={win_pos}/{len(win_returns)}")

sharpe_cv = float(np.std([w["sharpe"] for w in windows]) / (abs(np.mean([w["sharpe"] for w in windows])) + 1e-9))
return_cv = float(np.std([w["ann_ret"] for w in windows]) / (abs(np.mean([w["ann_ret"] for w in windows])) + 1e-9))
stable = sharpe_cv < 0.5
print(f"\nSharpe CV: {sharpe_cv:.4f} ({'STABLE' if stable else 'UNSTABLE'}, target <0.5)")
print(f"Return CV: {return_cv:.4f}")

# 极端月份分析
print(f"\n{'='*60}")
print("极端月份依赖分析")
print(f"{'='*60}")
sorted_returns = returns.sort_values(ascending=False)
top2_dates = [records[i]["date"] for i in sorted_returns.index[:2]]
top2_values = sorted_returns.iloc[:2].tolist()
print(f"Top 2 月份: {top2_dates[0]}={top2_values[0]*100:+.2f}%, {top2_dates[1]}={top2_values[1]*100:+.2f}%")
# 剔除 top 2 后的年化
mask = ~returns.index.isin(sorted_returns.index[:2])
returns_without_top2 = returns[mask]
ann_without_top2 = float((1 + returns_without_top2.mean()) ** 12 - 1)
print(f"剔除 Top 2 后年化: {ann_without_top2*100:.2f}% (target >=8%)")

# 回撤熔断器统计
print(f"\n{'='*60}")
print("回撤熔断器统计")
print(f"{'='*60}")
breaker_counts = {"normal": 0, "warning": 0, "severe": 0}
for r in records:
    level = r.get("drawdown_breaker", {}).get("level", "normal")
    breaker_counts[level] = breaker_counts.get(level, 0) + 1
print(f"Normal: {breaker_counts['normal']} 个月")
print(f"Warning (dd>5%): {breaker_counts['warning']} 个月")
print(f"Severe (dd>10%): {breaker_counts['severe']} 个月")

# 板块约束违例统计
print(f"\n{'='*60}")
print("板块约束违例统计")
print(f"{'='*60}")
violation_count = 0
for r in records:
    violations = r.get("market_regime", {}).get("sector_violations", [])
    if violations:
        violation_count += 1
        print(f"  {r['date']}: {violations}")
print(f"总违例月份数: {violation_count}")

# 保存完整验证报告
report = {
    "timestamp": datetime.now().isoformat(),
    "data_source": str(result_file),
    "period": data["period"],
    "n_months": len(records),
    "annual_return": annual_return,
    "annual_volatility": annual_vol,
    "max_drawdown": max_dd,
    "sharpe_ratio": sharpe,
    "skewness": skewness,
    "kurtosis": kurtosis,
    "win_rate": win_rate,
    "walk_forward": {
        "windows": windows,
        "sharpe_cv": sharpe_cv,
        "return_cv": return_cv,
        "stable": stable,
    },
    "extreme_months_removed": {
        "dates": top2_dates,
        "annual_return_without": ann_without_top2,
    },
    "drawdown_breaker_stats": breaker_counts,
    "sector_violation_months": violation_count,
}

out_path = Path("output/validation_reports") / f"dsr_walkforward_v2_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
print(f"\n验证报告已保存: {out_path}")

# 验收判定
print(f"\n{'='*60}")
print("验收判定")
print(f"{'='*60}")
checks = [
    ("年化收益 >= 8%", annual_return >= 0.08, f"{annual_return*100:.2f}%"),
    ("最大回撤 <= 15%", max_dd <= 0.15, f"{max_dd*100:.2f}%"),
    ("Walk-Forward CV < 0.5", sharpe_cv < 0.5, f"{sharpe_cv:.4f}"),
    ("剔除Top2后年化 >= 8%", ann_without_top2 >= 0.08, f"{ann_without_top2*100:.2f}%"),
]
all_pass = True
for name, passed, value in checks:
    status = "PASS" if passed else "FAIL"
    if not passed:
        all_pass = False
    print(f"  [{status}] {name}: {value}")

print(f"\n总结: {'ALL PASS' if all_pass else 'SOME FAILED'}")
