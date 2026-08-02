"""V7.1 DSR + Walk-Forward 稳定性验证 (信号后处理: bull regime 高波动股惩罚)"""
import glob
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path("v8.3_institutional/src/validation").resolve()))

# 自动查找最新的V7-Model回测结果
v7_files = glob.glob("output/validation_reports/lgb_backtest_v71_signal_penalty*.json")
if not v7_files:
    print("ERROR: 未找到V7-Model回测结果文件")
    sys.exit(1)
v7_files.sort(key=lambda x: os.path.getmtime(x))
v7_file = Path(v7_files[-1])
print(f"加载V7-Model结果: {v7_file}")

with open(v7_file, encoding="utf-8") as f:
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
print("V7.1 DSR + Walk-Forward 验证 (信号后处理)")
print("=" * 70)
print(f"回测期: {records[0]['date']} ~ {records[-1]['date']} ({n} 个月)")
print(f"年化收益: {ann_ret:.4f} ({ann_ret*100:.2f}%)")
print(f"年化波动: {ann_vol:.4f} ({ann_vol*100:.2f}%)")
print(f"Sharpe:   {sharpe:.4f}")
print(f"最大回撤: {max_dd:.4f} ({max_dd*100:.2f}%)")
print(f"胜率:     {win_rate:.4f} ({win_rate*100:.2f}%)")
print(f"偏度:     {skew:.4f}")
print(f"峰度:     {kurt:.4f}  (V6.2: 6.86, V6: 4.99)")
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
    print(f"\nDSR 最大通过 n_trials: {max_pass} (目标 >=5, V6.2: 3)")
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
sharpe_cv = float(np.std(sharpes) / (np.mean(sharpes) + 1e-9)) if np.mean(sharpes) > 0 else 0
stable = sharpe_cv < 0.5
print(f"\nSharpe CV: {sharpe_cv:.4f} (目标 <0.5, V6.2: 0.55, V6: 0.46[bug], {'达标' if stable else '未达标'})")

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

# 保存
output_path = Path("output/validation_reports")
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
result = {
    "timestamp": datetime.now().isoformat(),
    "data_source": str(v7_file),
    "period": f"{records[0]['date']}~{records[-1]['date']}",
    "n_months": n,
    "annual_return": ann_ret, "annual_volatility": ann_vol,
    "max_drawdown": max_dd, "sharpe_ratio": sharpe,
    "win_rate": win_rate, "skewness": skew, "kurtosis": kurt,
    "dsr_results": dsr_results, "dsr_max_pass_n_trials": max_pass,
    "walk_forward": {"windows": windows, "sharpe_cv": sharpe_cv, "stable": stable},
    "extreme_months_removed": {"dates": top2_dates, "returns": top2_returns, "annual_return_without": ann_ret_without},
}
out_file = output_path / f"dsr_walkforward_v71_{ts}.json"
with open(out_file, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2, default=str)

# 对比
print()
print("=" * 70)
print("V7.1 vs V7-Model vs V6.2 对比:")
print(f"{'指标':<18} {'V6.2':<10} {'V7-Model':<10} {'V7.1':<10} {'V71-V62':<10} {'V71-V7M':<10}")
print("-" * 78)
print(f"{'年化收益':<18} {'14.35%':<10} {'14.50%':<10} {f'{ann_ret*100:.2f}%':<10} {f'{(ann_ret-0.1435)*100:+.2f}%':<10} {f'{(ann_ret-0.1450)*100:+.2f}%':<10}")
print(f"{'最大回撤':<18} {'7.90%':<10} {'10.44%':<10} {f'{max_dd*100:.2f}%':<10} {f'{(max_dd-0.0790)*100:+.2f}%':<10} {f'{(max_dd-0.1044)*100:+.2f}%':<10}")
print(f"{'Sharpe':<18} {'1.092':<10} {'1.023':<10} {f'{sharpe:.3f}':<10} {f'{sharpe-1.092:+.3f}':<10} {f'{sharpe-1.023:+.3f}':<10}")
print(f"{'峰度':<18} {'6.86':<10} {'2.48':<10} {f'{kurt:.2f}':<10} {f'{kurt-6.86:+.2f}':<10} {f'{kurt-2.48:+.2f}':<10}")
print(f"{'偏度':<18} {'-':<10} {'1.02':<10} {f'{skew:.2f}':<10} {'-':<10} {f'{skew-1.02:+.2f}':<10}")
print(f"{'WF Sharpe CV':<18} {'0.55':<10} {'0.74':<10} {f'{sharpe_cv:.2f}':<10} {f'{sharpe_cv-0.55:+.2f}':<10} {f'{sharpe_cv-0.74:+.2f}':<10}")
print(f"{'DSR n_trials':<18} {'3':<10} {'5':<10} {f'{max_pass}':<10} {f'{max_pass-3:+d}':<10} {f'{max_pass-5:+d}':<10}")
print(f"{'去极端月年化':<18} {'6.48%':<10} {'7.17%':<10} {f'{ann_ret_without*100:.2f}%':<10} {f'{(ann_ret_without-0.0648)*100:+.2f}%':<10} {f'{(ann_ret_without-0.0717)*100:+.2f}%':<10}")
w1_sharpe = windows[1]["sharpe"]
w2_sharpe = windows[2]["sharpe"]
print(f"{'Window1 Sharpe':<18} {'0.330':<10} {'0.300':<10} {w1_sharpe:.3f}{'':<5} {w1_sharpe-0.330:+.3f}{'':<5} {w1_sharpe-0.300:+.3f}")
print(f"{'Window2 Sharpe':<18} {'1.725':<10} {'2.008':<10} {w2_sharpe:.3f}{'':<5} {w2_sharpe-1.725:+.3f}{'':<5} {w2_sharpe-2.008:+.3f}")
print("=" * 70)

# 验收检查
print()
print("=" * 70)
print("V7.1 验收检查:")
print("-" * 70)
checks = [
    ("WF Sharpe CV < 0.5", sharpe_cv < 0.5, f"{sharpe_cv:.4f}"),
    ("年化收益 >= 8%", ann_ret >= 0.08, f"{ann_ret*100:.2f}%"),
    ("去极端月年化 >= 8%", ann_ret_without >= 0.08, f"{ann_ret_without*100:.2f}%"),
    ("最大回撤 <= 15%", max_dd <= 0.15, f"{max_dd*100:.2f}%"),
    ("DSR n_trials >= 5", max_pass >= 5, f"{max_pass}"),
    ("Window1 Sharpe >= 0.5", windows[1]["sharpe"] >= 0.5, f"{windows[1]['sharpe']:.3f}"),
]
all_pass = True
for name, ok, val in checks:
    status = "✅ PASS" if ok else "❌ FAIL"
    print(f"  {status}  {name}: {val}")
    if not ok:
        all_pass = False
print()
if all_pass:
    print("🎉 V7.1 全部验收通过! 信号后处理成功解决 bull regime 信号失效")
else:
    print("⚠️  V7.1 部分验收未通过")
print("=" * 70)
print(f"\n结果已保存: {out_file}")
