# -*- coding: utf-8 -*-
"""
45 个月回测结果的 DSR + Walk-Forward 稳定性验证

注意: 回测为月度收益 (45 个点), 年化因子 = 12 (非 252)
- 年化夏普 = mean_monthly * 12 / (std_monthly * sqrt(12))
- DSR 的 e_max_sr 也用 12 年化
"""
import sys
import json
import math
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / "v8.3_institutional" / "src"))

# 读取 regime scaling 版本的回测结果 (优先 mild 温和因子版)
result_files = sorted(Path("output/validation_reports").glob("lgb_backtest_45m_regime_mild_*.json"))
if not result_files:
    result_files = sorted(Path("output/validation_reports").glob("lgb_backtest_45m_regime_*.json"))
if not result_files:
    print("❌ 未找到修复后的回测结果文件")
    sys.exit(1)
result_file = result_files[-1]
data = json.loads(result_file.read_text(encoding="utf-8"))
records = data["records"]
monthly_returns = [r["portfolio_return"] for r in records]
n = len(monthly_returns)

print("=" * 70)
print(f"DSR + Walk-Forward 验证 (月度收益, {n} 个月)")
print(f"数据源: {result_file.name}")
print("=" * 70)
print(f"回测期: {data['period']}")
print(f"年化收益: {data['annual_return']*100:.2f}%")
print(f"最大回撤: {data['max_drawdown']*100:.2f}%")
print(f"胜率: {data['win_rate']*100:.2f}%")
print()

# ============================================================
# 1. 基础统计 (月度 -> 年化)
# ============================================================
mean_m = sum(monthly_returns) / n
var_m = sum((r - mean_m) ** 2 for r in monthly_returns) / (n - 1)
std_m = math.sqrt(var_m)
# 年化 (因子 12)
ann_return = (1 + mean_m) ** 12 - 1
ann_vol = std_m * math.sqrt(12)
rf_annual = 0.03
sharpe = (ann_return - rf_annual) / ann_vol if ann_vol > 0 else 0.0

print("=" * 70)
print("1. 基础统计 (月度 -> 年化, 因子=12)")
print("=" * 70)
print(f"  月均收益: {mean_m*100:.2f}%")
print(f"  月度波动: {std_m*100:.2f}%")
print(f"  年化收益: {ann_return*100:.2f}%")
print(f"  年化波动: {ann_vol*100:.2f}%")
print(f"  夏普比率: {sharpe:.3f}  (无风险利率 {rf_annual*100}%)")
print()

# ============================================================
# 2. Deflated Sharpe Ratio (月度版)
# ============================================================
# 偏度/峰度
m3 = sum((r - mean_m) ** 3 for r in monthly_returns) / n
skewness = m3 / (std_m ** 3) if std_m > 0 else 0.0
m4 = sum((r - mean_m) ** 4 for r in monthly_returns) / n
kurtosis = m4 / (std_m ** 4) - 3.0 if std_m > 0 else 0.0

def _norm_ppf(p):
    if p <= 0: return -10.0
    if p >= 1: return 10.0
    q = min(p, 1.0 - p)
    if q < 1e-16: return 10.0 if p > 0.5 else -10.0
    t = math.sqrt(-2.0 * math.log(q))
    c0, c1, c2 = 2.515517, 0.802853, 0.010328
    d1, d2, d3 = 1.432788, 0.189269, 0.001308
    z = t - (c0 + c1*t + c2*t*t) / (1.0 + d1*t + d2*t*t + d3*t*t*t)
    return -z if p < 0.5 else z

def _norm_cdf(z):
    if z < -8: return 0.0
    if z > 8: return 1.0
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))

gamma_euler = 0.5772156649015329

print("=" * 70)
print("2. Deflated Sharpe Ratio (月度, 年化因子=12)")
print("=" * 70)
print(f"  偏度: {skewness:.3f}")
print(f"  超额峰度: {kurtosis:.3f}")
print()

# 不同 n_trials 下的 DSR
print(f"  {'n_trials':<10} {'E[max_SR]':>10} {'DSR':>8} {'p-value':>10} {'通过>=0.95':>12}")
print(f"  {'-'*55}")
dsr_results = []
for n_trials in [1, 5, 10, 20, 50, 100, 200]:
    # E[max{SR}] 月度版: Var[SR] ≈ (1 + skew^2/4) / T, T = n (月数)
    T = max(n, 12)
    var_sr = (1.0 + skewness ** 2 / 4.0) / T
    std_sr = math.sqrt(max(var_sr, 1e-10))
    p_n = 1.0 - 1.0 / n_trials if n_trials > 1 else 0.5
    p_ne = 1.0 - 1.0 / (n_trials * math.e) if n_trials > 1 else 0.5
    z_n = _norm_ppf(p_n)
    z_ne = _norm_ppf(p_ne)
    e_max_monthly = std_sr * ((1.0 - gamma_euler) * z_n + gamma_euler * z_ne)
    e_max_ann = e_max_monthly * math.sqrt(12)  # 月度 -> 年化

    # SE of SR
    se_sr = math.sqrt((1.0 + 0.5 * sharpe**2 - skewness * sharpe * math.sqrt(1/12)
                       + (kurtosis / 4.0) * sharpe**2) / (T * 12))
    if se_sr < 1e-15:
        z_score = 10.0 if sharpe > e_max_ann else -10.0
    else:
        z_score = (sharpe - e_max_ann) / se_sr
    p_value = _norm_cdf(-z_score)
    dsr = 1.0 - p_value
    is_pass = dsr >= 0.95
    dsr_results.append((n_trials, e_max_ann, dsr, p_value, is_pass))
    print(f"  {n_trials:<10} {e_max_ann:>10.3f} {dsr:>8.4f} {p_value:>10.4f} {'✅' if is_pass else '❌':>12}")

print()
# 找到通过的最大 n_trials
max_pass = max([nt for nt, _, _, _, p in dsr_results if p], default=0)
print(f"  结论: DSR 在 n_trials≤{max_pass} 时通过 (夏普 {sharpe:.3f} 显著优于噪音)")
if max_pass >= 20:
    print("  ✅ 策略经得起 20+ 次试验的多重检验校正")
elif max_pass >= 5:
    print("  ⚠️ 策略仅经得起少量试验, 存在过拟合风险")
else:
    print("  ❌ 策略可能是过拟合产物")
print()

# ============================================================
# 3. Walk-Forward 稳定性检验 (多窗口)
# ============================================================
print("=" * 70)
print("3. Walk-Forward 稳定性检验 (3 窗口, 每窗口 15 个月)")
print("=" * 70)
n_windows = 3
window_size = n // n_windows
windows = []
for w in range(n_windows):
    start = w * window_size
    end = min(start + window_size, n)
    wr = monthly_returns[start:end]
    if len(wr) < 6:
        continue
    w_mean = sum(wr) / len(wr)
    w_var = sum((r - w_mean) ** 2 for r in wr) / max(len(wr) - 1, 1)
    w_std = math.sqrt(w_var)
    w_ann_ret = (1 + w_mean) ** 12 - 1
    w_ann_vol = w_std * math.sqrt(12)
    w_sharpe = (w_ann_ret - rf_annual) / w_ann_vol if w_ann_vol > 0 else 0.0
    # 窗口内最大回撤
    eq = 1.0
    pk = 1.0
    w_max_dd = 0.0
    for r in wr:
        eq *= (1 + r)
        pk = max(pk, eq)
        dd = (pk - eq) / pk
        w_max_dd = max(w_max_dd, dd)
    pos = sum(1 for r in wr if r > 0)
    windows.append({
        "window": w, "start": records[start]["date"], "end": records[end-1]["date"],
        "n": len(wr), "ann_ret": w_ann_ret, "ann_vol": w_ann_vol, "sharpe": w_sharpe,
        "max_dd": w_max_dd, "pos_months": pos
    })

print(f"  {'窗口':<6} {'时段':<28} {'年化':>8} {'波动':>8} {'夏普':>8} {'回撤':>8} {'正月':>6}")
for w in windows:
    print(f"  W{w['window']:<5} {w['start'][:10]}~{w['end'][:10]:<16} {w['ann_ret']*100:>7.2f}% {w['ann_vol']*100:>7.2f}% {w['sharpe']:>8.3f} {w['max_dd']*100:>7.2f}% {w['pos_months']}/{w['n']}")
print()

sharpes = [w["sharpe"] for w in windows]
returns = [w["ann_ret"] for w in windows]
mean_sharpe = sum(sharpes) / len(sharpes)
std_sharpe = math.sqrt(sum((s - mean_sharpe)**2 for s in sharpes) / (len(sharpes) - 1))
sharpe_cv = abs(std_sharpe / mean_sharpe) if mean_sharpe != 0 else float('inf')
mean_return = sum(returns) / len(returns)
std_return = math.sqrt(sum((r - mean_return)**2 for r in returns) / (len(returns) - 1))
return_cv = abs(std_return / mean_return) if mean_return != 0 else float('inf')

print(f"  夏普变异系数: {sharpe_cv:.2f}  (阈值<1.0 {'✅' if sharpe_cv < 1.0 else '❌'})")
print(f"  收益变异系数: {return_cv:.2f}  (阈值<0.5 {'✅' if return_cv < 0.5 else '❌'})")
all_pos = all(r > 0 for r in returns)
print(f"  所有窗口收益为正: {'✅' if all_pos else '❌'} ({[f'{r*100:.1f}%' for r in returns]})")
stable = sharpe_cv < 1.0 and return_cv < 0.5 and all_pos
print(f"  Walk-Forward 稳定性: {'✅ 稳定' if stable else '❌ 不稳定'}")
print()

# ============================================================
# 4. 极端月份分析
# ============================================================
print("=" * 70)
print("4. 极端月份分析")
print("=" * 70)
sorted_rets = sorted(enumerate(monthly_returns), key=lambda x: x[1])
print("  收益最低的 5 个月:")
for idx, ret in sorted_rets[:5]:
    print(f"    {records[idx]['date']}: {ret*100:+.2f}%")
print("  收益最高的 5 个月:")
for idx, ret in sorted_rets[-5:][::-1]:
    print(f"    {records[idx]['date']}: {ret*100:+.2f}%")

# 剔除极端月份后的表现
top2_dates = {records[idx]['date'] for idx, _ in sorted_rets[-2:]}
mask = [i for i in range(n) if records[i]['date'] not in top2_dates]
filtered = [monthly_returns[i] for i in mask]
f_mean = sum(filtered) / len(filtered)
f_ann = (1 + f_mean) ** 12 - 1
print(f"\n  剔除最高 2 个月 ({sorted(top2_dates)}) 后:")
print(f"    剩余 {len(filtered)} 个月, 年化 {f_ann*100:.2f}% (原 {ann_return*100:.2f}%)")

# ============================================================
# 5. 总结
# ============================================================
print()
print("=" * 70)
print("5. 总结")
print("=" * 70)
print(f"  年化收益: {ann_return*100:.2f}%  (目标 >= 8% {'✅' if ann_return >= 0.08 else '❌'})")
print(f"  最大回撤: {data['max_drawdown']*100:.2f}%  (目标 <= 15% {'✅' if data['max_drawdown'] <= 0.15 else '❌'})")
print(f"  夏普比率: {sharpe:.3f}")
print(f"  DSR 通过: n_trials ≤ {max_pass}")
print(f"  Walk-Forward: {'稳定' if stable else '不稳定'} (夏普CV={sharpe_cv:.2f}, 收益CV={return_cv:.2f})")
print(f"  极端月份依赖: 剔除最高2月后年化 {f_ann*100:.2f}% (下降 {(ann_return-f_ann)*100:.2f}pp)")

# 保存报告
report = {
    "timestamp": datetime.now().isoformat(),
    "data_source": str(result_file),
    "period": data["period"],
    "n_months": n,
    "annual_return": ann_return,
    "annual_volatility": ann_vol,
    "max_drawdown": data["max_drawdown"],
    "sharpe_ratio": sharpe,
    "skewness": skewness,
    "kurtosis": kurtosis,
    "dsr_results": [{"n_trials": nt, "e_max_sr": em, "dsr": d, "p_value": p, "pass": ip} for nt, em, d, p, ip in dsr_results],
    "dsr_max_pass_n_trials": max_pass,
    "walk_forward": {
        "windows": windows,
        "sharpe_cv": sharpe_cv,
        "return_cv": return_cv,
        "stable": stable,
    },
    "extreme_months_removed": {
        "dates": sorted(top2_dates),
        "annual_return_without": f_ann,
    },
}
report_file = Path("output/validation_reports") / f"dsr_walkforward_45m_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print(f"\n报告已保存: {report_file}")
