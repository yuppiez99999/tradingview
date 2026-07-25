# -*- coding: utf-8 -*-
"""V7.2 重算: 基于 V7.1 记录, 对 bull regime 月份应用 5% 权重上限

V7.2 方案: bull regime 下 max_weight 10%->5%
重算逻辑:
  1. 加载 V7.1 完整记录 (45 个月)
  2. 对每个 bull regime 月份:
     a. 将 >5% 的权重截断至 5%
     b. 归一化保持总暴露度 (与 backtest_runner 一致)
     c. 重新计算组合收益 = sum(weight_i * return_i)
  3. 非 bull regime 月份保持不变
  4. 重新计算 Sharpe CV, DSR, 极端月份

优势: 避免 LGB 训练崩溃, 直接验证 V7.2 效果
"""
import json
import sys
import glob
import os
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path("v8.3_institutional/src/validation").resolve()))

# === 加载 V7.1 结果 ===
v71_files = glob.glob("output/validation_reports/lgb_backtest_v71_signal_penalty*.json")
v71_files.sort(key=lambda x: os.path.getmtime(x))
v71_file = Path(v71_files[-1])
print(f"加载 V7.1 结果: {v71_file}")

with open(v71_file, "r", encoding="utf-8") as f:
    v71_data = json.load(f)

records = v71_data.get("records", [])
n = len(records)
print(f"V7.1 记录数: {n} ({records[0]['date']} ~ {records[-1]['date']})")


# === V7.2 重算函数 ===
def recompute_v72(weights: dict, returns: dict, max_weight: float = 0.05) -> tuple:
    """V7.2: 截断权重至 max_weight, 归一化, 重算组合收益

    Returns: (new_weights, new_portfolio_return)
    """
    # Step 1: 截断至 max_weight
    clamped = {}
    violations = []
    for sym, w in weights.items():
        if w > max_weight:
            clamped[sym] = max_weight
            violations.append({"symbol": sym, "old": w, "new": max_weight})
        else:
            clamped[sym] = w

    # Step 2: 归一化保持总暴露度 (与 backtest_runner enforce_hard_constraints 后归一化一致)
    total_old = sum(weights.values())
    total_new = sum(clamped.values())
    if total_new > 0 and total_new < total_old:
        # 归一化: 将截断后的权重按比例放大至原始总暴露度
        scale = total_old / total_new
        clamped = {s: w * scale for s, w in clamped.items()}
        # 二次截断确保硬约束
        for sym, w in clamped.items():
            if w > max_weight:
                clamped[sym] = max_weight
        # 再次归一化 (简化: 不迭代, 与 backtest_runner 二次截断一致)
        total_final = sum(clamped.values())
        if total_final > 0 and total_final < total_old:
            scale2 = total_old / total_final
            clamped = {s: min(w * scale2, max_weight) for s, w in clamped.items()}

    # Step 3: 重算组合收益
    new_return = sum(clamped.get(s, 0) * returns.get(s, 0) for s in weights)
    return clamped, new_return, violations


# === 遍历所有记录, 应用 V7.2 ===
v72_records = []
v72_changes = []
for rec in records:
    regime = rec.get("market_regime", {}).get("regime", "unknown")
    weights = rec.get("weights", {})
    returns = rec.get("returns", {})
    old_return = rec.get("portfolio_return", 0)

    if regime == "bull" and weights:
        # V7.2: bull regime 应用 5% 权重上限
        new_weights, new_return, violations = recompute_v72(weights, returns, max_weight=0.05)
        new_rec = dict(rec)
        new_rec["weights"] = new_weights
        new_rec["portfolio_return"] = new_return
        new_rec["v72_applied"] = True
        new_rec["v72_violations"] = violations
        v72_records.append(new_rec)
        if violations:
            v72_changes.append({
                "date": rec["date"],
                "regime": regime,
                "old_return": old_return,
                "new_return": new_return,
                "delta": new_return - old_return,
                "n_violations": len(violations),
                "violations": [{"symbol": v["symbol"], "old": round(v["old"], 4), "new": round(v["new"], 4)} for v in violations],
            })
    else:
        # 非 bull regime: 保持不变
        new_rec = dict(rec)
        new_rec["v72_applied"] = False
        v72_records.append(new_rec)

# === 打印 V7.2 变更摘要 ===
print(f"\n{'='*70}")
print(f"V7.2 变更摘要: {len(v72_changes)} 个 bull regime 月份被修改")
print(f"{'='*70}")
for ch in v72_changes:
    print(f"{ch['date']}: ret {ch['old_return']*100:+.2f}% -> {ch['new_return']*100:+.2f}% "
          f"(Δ{ch['delta']*100:+.2f}%, {ch['n_violations']} stocks clamped)")
    for v in ch["violations"][:5]:
        print(f"    {v['symbol']}: {v['old']*100:.1f}% -> {v['new']*100:.1f}%")

# === 重新计算 V7.2 指标 ===
returns_series = pd.Series([r["portfolio_return"] for r in v72_records])

ann_ret = float((1 + returns_series.mean()) ** 12 - 1)
ann_vol = float(returns_series.std() * np.sqrt(12))
sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
equity = (1 + returns_series).cumprod()
peak = equity.cummax()
dd_series = (peak - equity) / peak
max_dd = float(dd_series.max())
win_rate = float((returns_series > 0).mean())
skew = float(returns_series.skew())
kurt = float(returns_series.kurt())

print(f"\n{'='*70}")
print(f"V7.2 重算结果 (bull regime max_weight 5%)")
print(f"{'='*70}")
print(f"回测期: {records[0]['date']} ~ {records[-1]['date']} ({n} 个月)")
print(f"年化收益: {ann_ret:.4f} ({ann_ret*100:.2f}%)  [V7.1: 14.23%, V6.2: 14.35%]")
print(f"年化波动: {ann_vol:.4f} ({ann_vol*100:.2f}%)  [V7.1: 12.83%, V6.2: 12.85%]")
print(f"Sharpe:   {sharpe:.4f}  [V7.1: 1.110, V6.2: 1.092]")
print(f"最大回撤: {max_dd:.4f} ({max_dd*100:.2f}%)  [V7.1: 8.43%, V6.2: 7.90%]")
print(f"胜率:     {win_rate:.4f} ({win_rate*100:.2f}%)  [V7.1: 55.56%, V6.2: 57.78%]")
print(f"偏度:     {skew:.4f}  [V7.1: 1.43, V6.2: 1.31]")
print(f"峰度:     {kurt:.4f}  [V7.1: 4.82, V6.2: 6.86]")

# === DSR ===
print(f"\n{'-'*70}")
print("Deflated Sharpe Ratio (DSR)")
print(f"{'-'*70}")
try:
    from deflated_sharpe import deflated_sharpe_ratio
    print(f"{'n_trials':>10} | {'DSR':>8} | {'p-value':>10} | {'E[max SR]':>12} | {'pass':>6}")
    print("-" * 60)
    dsr_results = []
    max_pass = 0
    for n_trials in [1, 3, 5, 8, 10, 20, 50, 100]:
        result = deflated_sharpe_ratio(returns_series, n_trials=n_trials)
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
    print(f"\nDSR 最大通过 n_trials: {max_pass} (目标 >=5, V7.1: 3, V6.2: 3)")
except Exception as e:
    print(f"DSR 计算失败: {e}")
    dsr_results = []
    max_pass = 0

# === Walk-Forward ===
print(f"\n{'-'*70}")
print("Walk-Forward 稳定性 (3个窗口)")
print(f"{'-'*70}")
window_size = n // 3
windows = []
for i in range(3):
    start = i * window_size
    end = (i + 1) * window_size if i < 2 else n
    win_returns = returns_series.iloc[start:end]
    win_equity = (1 + win_returns).cumprod()
    win_peak = win_equity.cummax()
    win_dd = float(((win_peak - win_equity) / win_peak).max())
    win_ann_ret = float((1 + win_returns.mean()) ** 12 - 1)
    win_ann_vol = float(win_returns.std() * np.sqrt(12))
    win_sharpe = win_ann_ret / win_ann_vol if win_ann_vol > 0 else 0
    win_pos = int((win_returns > 0).sum())
    windows.append({
        "window": i, "start": v72_records[start]["date"], "end": v72_records[end-1]["date"],
        "n": len(win_returns), "ann_ret": win_ann_ret, "ann_vol": win_ann_vol,
        "sharpe": win_sharpe, "max_dd": win_dd, "pos_months": win_pos,
    })
    print(f"窗口{i}: {v72_records[start]['date']} ~ {v72_records[end-1]['date']} "
          f"| 年化={win_ann_ret*100:6.2f}% 波动={win_ann_vol*100:5.2f}% "
          f"Sharpe={win_sharpe:5.3f} 回撤={win_dd*100:5.2f}% 胜月={win_pos}/{len(win_returns)}")

sharpes = [w["sharpe"] for w in windows]
sharpe_cv = float(np.std(sharpes) / (np.mean(sharpes) + 1e-9)) if np.mean(sharpes) > 0 else 0
stable = sharpe_cv < 0.5
print(f"\nSharpe CV: {sharpe_cv:.4f} (目标 <0.5, V7.1: 0.74, V6.2: 0.55, V6[bug]: 0.46, {'达标' if stable else '未达标'})")

# === 2024-06 验证 ===
print(f"\n{'-'*70}")
print("V7.2 关键验证: 2024-06 月 (bull regime 崩盘月)")
print(f"{'-'*70}")
jun = [r for r in v72_records if r["date"].startswith("2024-06")][0]
jun_old = [r for r in records if r["date"].startswith("2024-06")][0]
print(f"2024-06 组合收益: {jun['portfolio_return']*100:.2f}%  [V7.1: {jun_old['portfolio_return']*100:.2f}%]")
if jun.get("v72_violations"):
    print(f"被截断股票:")
    for v in jun["v72_violations"]:
        print(f"  {v['symbol']}: {v['old']*100:.1f}% -> {v['new']*100:.1f}%")

# === 极端月份 ===
print(f"\n{'-'*70}")
print("极端月份依赖分析")
print(f"{'-'*70}")
sorted_rets = returns_series.sort_values(ascending=False)
top2_idx = sorted_rets.iloc[:2].index
top2_dates = [v72_records[i]["date"] for i in top2_idx]
top2_returns = sorted_rets.iloc[:2].tolist()
returns_without_top2 = returns_series.drop(top2_idx)
ann_ret_without = float((1 + returns_without_top2.mean()) ** 12 - 1)
print(f"最高2个月份: {top2_dates} = {[f'{r*100:.2f}%' for r in top2_returns]}")
print(f"去除最高2月后年化: {ann_ret_without*100:.2f}% (目标 >=8%, V7.1: 6.90%, V6.2: 6.91%)")

# === 保存 ===
output_path = Path("output/validation_reports")
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
result = {
    "timestamp": datetime.now().isoformat(),
    "method": "recompute_from_v71_records",
    "data_source": str(v71_file),
    "period": f"{records[0]['date']}~{records[-1]['date']}",
    "n_months": n,
    "annual_return": ann_ret, "annual_volatility": ann_vol,
    "max_drawdown": max_dd, "sharpe_ratio": sharpe,
    "win_rate": win_rate, "skewness": skew, "kurtosis": kurt,
    "dsr_results": dsr_results, "dsr_max_pass_n_trials": max_pass,
    "walk_forward": {"windows": windows, "sharpe_cv": sharpe_cv, "stable": stable},
    "extreme_months_removed": {
        "dates": top2_dates, "returns": top2_returns,
        "annual_return_without": ann_ret_without,
    },
    "v72_changes": v72_changes,
    "comparison": {
        "V6.2": {"sharpe_cv": 0.55, "dsr_max_pass": 3, "ann_ret": 0.1435, "sharpe": 1.092, "max_dd": 0.079},
        "V7.1": {"sharpe_cv": 0.74, "dsr_max_pass": 3, "ann_ret": 0.1423, "sharpe": 1.110, "max_dd": 0.084},
        "V7.2": {"sharpe_cv": sharpe_cv, "dsr_max_pass": max_pass, "ann_ret": ann_ret, "sharpe": sharpe, "max_dd": max_dd},
    },
}
result_file = output_path / f"dsr_walkforward_v72_recompute_{ts}.json"
with open(result_file, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2, default=str)
print(f"\n结果已保存: {result_file}")
