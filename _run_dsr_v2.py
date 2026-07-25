# -*- coding: utf-8 -*-
"""DSR (Deflated Sharpe Ratio) V2 验证"""
import json
import sys
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime
from scipy import stats

# 读取 V2 回测结果
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
n = len(returns)

# 基本指标
ann_return = float((1 + returns.mean()) ** 12 - 1)
ann_vol = float(returns.std() * np.sqrt(12))
sharpe = ann_return / ann_vol if ann_vol > 0 else 0
skew = float(returns.skew())
kurt = float(returns.kurt())

print(f"\n{'='*60}")
print(f"Deflated Sharpe Ratio (DSR) V2")
print(f"{'='*60}")
print(f"Sharpe: {sharpe:.4f}, Skew: {skew:.4f}, Kurt: {kurt:.4f}")
print(f"n_months: {n}")

# DSR 计算
# 参考: Bailey & Lopez de Prado (2014)
# DSR = Phi( (SR - E[max(SR)] * sqrt(1 - skew*SR + (kurt-1)/4 * SR^2)) / sqrt(1 - skew*SR + (kurt-1)/4 * SR^2) )
# 简化版: 用正态近似

# 非iid修正因子 (考虑偏度和峰度)
sr_monthly = sharpe / np.sqrt(12)  # 月度夏普
correction = 1 - skew * sr_monthly + (kurt - 1) / 4 * sr_monthly ** 2
correction = max(correction, 0.1)  # 避免负值

print(f"\nMonthly Sharpe: {sr_monthly:.4f}")
print(f"Non-iid correction: {correction:.4f}")

# DSR for different n_trials
# E[max(SR)] ≈ sqrt(2*ln(n_trials)) * sigma_SR
# sigma_SR ≈ sqrt(1/T * correction)
T = n
sigma_sr = np.sqrt(correction / T)

print(f"\n{'n_trials':>10s} | {'E[max SR]':>12s} | {'DSR':>8s} | {'p-value':>10s} | {'pass':>6s}")
print("-" * 55)

dsr_results = []
for n_trials in [1, 2, 3, 5, 8, 10, 15, 20, 50, 100]:
    if n_trials == 1:
        e_max_sr = 0.0
    else:
        # Expected max of n_trials iid standard normals
        e_max_sr = stats.norm.ppf(1 - 1 / n_trials) * sigma_sr

    # DSR = Phi( (SR - E[max SR]) / sigma_SR )
    if sigma_sr > 0:
        dsr = float(stats.norm.cdf((sr_monthly - e_max_sr) / sigma_sr))
    else:
        dsr = 0.0

    p_value = 1 - dsr
    passed = dsr > 0.95  # 5% significance level

    print(f"{n_trials:10d} | {e_max_sr:12.6f} | {dsr:8.4f} | {p_value:10.6f} | {'PASS' if passed else 'FAIL':>6s}")
    dsr_results.append({
        "n_trials": n_trials,
        "e_max_sr": e_max_sr,
        "dsr": dsr,
        "p_value": p_value,
        "pass": passed,
    })

# 找出最大通过的 n_trials
max_pass = max([d["n_trials"] for d in dsr_results if d["pass"]], default=0)
print(f"\nDSR 最大通过 n_trials: {max_pass} (V1: 5, target: >=10)")

# 保存报告
report = {
    "timestamp": datetime.now().isoformat(),
    "data_source": str(result_file),
    "sharpe": sharpe,
    "skewness": skew,
    "kurtosis": kurt,
    "n_months": n,
    "monthly_sharpe": sr_monthly,
    "non_iid_correction": correction,
    "dsr_results": dsr_results,
    "dsr_max_pass_n_trials": max_pass,
}

out_path = Path("output/validation_reports") / f"dsr_v2_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
print(f"\nDSR报告已保存: {out_path}")
