# -*- coding: utf-8 -*-
"""
计算 V9 回测的 DSR max_pass (Bailey & Lopez de Prado 2014, 2017 标准公式)
=========================================================================
2026-07-25 顶级对冲基金审计 P0-2/P0-3 修复:
  - P0-2: 修正 DSR 公式 (原代码混用 H0/H1 分布, SR_0 缩放错误)
  - P0-3: Sharpe CV 改用 12 月滚动序列 (原代码仅 2 个数据点, 统计意义为零)

标准 DSR 公式 (Bailey & Lopez de Prado 2014):
  σ(SR) = sqrt((1/(T-1)) * (1 - skew*SR + (kurt/4)*SR^2))   [H1 分布, 含 skew/kurt 校正]
  SR_0  = sqrt(2*ln(n_trials)) * σ(SR)                        [H0 下的期望最大 Sharpe]
  DSR   = (SR - SR_0) / σ(SR) = SR/σ(SR) - sqrt(2*ln(n_trials))

原代码错误: dsr = (SR - sqrt(2*ln(n)) * sqrt(1/(T-1))) / σ(SR)
  - 分子用 sqrt(1/(T-1)) [H0 的 σ(SR)] 缩放 SR_0
  - 分母用 σ(SR) [H1 的 σ(SR), 含 skew/kurt]
  - H0/H1 混用导致系统性低估 DSR (正偏度时)
"""
import json
import math
import statistics
from pathlib import Path
from scipy import stats

# 加载 V9 回测结果
result_file = Path(r'e:\各种PY程序\28-终极量化交易系统8.4\output\validation_reports\v9_regime_specific_backtest_20260725_114943.json')
with open(result_file, 'r', encoding='utf-8') as f:
    result = json.load(f)

records = result.get('records', [])
returns = [float(r.get('portfolio_return', 0)) for r in records if r.get('portfolio_return') is not None]

n = len(returns)
mean_ret = statistics.mean(returns)
std_ret = statistics.stdev(returns)
sharpe_annual = (mean_ret / std_ret) * math.sqrt(12) if std_ret > 0 else 0
sharpe_monthly = sharpe_annual / math.sqrt(12)  # = mean_ret / std_ret

skewness = float(stats.skew(returns))
kurtosis = float(stats.kurtosis(returns, fisher=True))  # Fisher: 正态=0

print('=' * 70)
print('V9 DSR max_pass 计算 (Bailey & Lopez de Prado 2014 标准公式)')
print('=' * 70)
print(f'  样本数 T = {n} 月')
print(f'  年化 Sharpe = {sharpe_annual:.4f}')
print(f'  月度 Sharpe = {sharpe_monthly:.4f}')
print(f'  偏度 = {skewness:.4f}')
print(f'  峰度 (Fisher) = {kurtosis:.4f}')


def compute_dsr_correct(sharpe_monthly, T, skewness, kurtosis, n_trials):
    """计算 DSR (Bailey & Lopez de Prado 2014 标准公式)

    DSR = (SR - SR_0 * σ(SR)) / σ(SR) = SR/σ(SR) - sqrt(2*ln(n))

    其中:
      σ(SR) = sqrt((1/(T-1)) * (1 - skew*SR + (kurt/4)*SR^2))  [H1 分布]
      SR_0  = sqrt(2*ln(n_trials))                              [H0 下的期望最大 z-score]
    """
    if T <= 1 or n_trials <= 1:
        return 0.0
    # H1 分布下的 σ(SR), 含 skew/kurt 校正
    sr_var = (1.0 / (T - 1)) * (1 - skewness * sharpe_monthly + (kurtosis / 4.0) * sharpe_monthly**2)
    sr_var = max(sr_var, 1e-10)
    sr_std = math.sqrt(sr_var)
    # 标准公式: DSR = SR/σ(SR) - sqrt(2*ln(n))
    dsr = sharpe_monthly / sr_std - math.sqrt(2 * math.log(n_trials))
    return dsr


def compute_dsr_old(sharpe_monthly, T, skewness, kurtosis, n_trials):
    """原代码的错误 DSR 公式 (H0/H1 混用, 保留用于对比)"""
    if T <= 1 or n_trials <= 1:
        return 0.0
    sr_0 = math.sqrt(2 * math.log(n_trials))
    sr_var = (1.0 / (T - 1)) * (1 - skewness * sharpe_monthly + (kurtosis / 4.0) * sharpe_monthly**2)
    sr_var = max(sr_var, 1e-10)
    sr_std = math.sqrt(sr_var)
    # 错误: 用 sqrt(1/(T-1)) [H0] 缩放 SR_0, 分母用 sr_std [H1]
    dsr = (sharpe_monthly - sr_0 * math.sqrt(1.0 / (T - 1))) / sr_std
    return dsr


# 对比新旧公式
print('\n=== DSR 公式对比 (旧 vs 新) ===')
print(f'{"n_trials":>10} {"旧DSR":>10} {"新DSR":>10} {"旧p-value":>12} {"新p-value":>12}')
max_pass_old = 0
max_pass_new = 0
for n_trials in range(1, 21):
    dsr_old = compute_dsr_old(sharpe_monthly, n, skewness, kurtosis, n_trials)
    dsr_new = compute_dsr_correct(sharpe_monthly, n, skewness, kurtosis, n_trials)
    p_old = 1 - stats.norm.cdf(dsr_old)
    p_new = 1 - stats.norm.cdf(dsr_new)
    print(f'{n_trials:>10d} {dsr_old:>+10.4f} {dsr_new:>+10.4f} {p_old:>12.4f} {p_new:>12.4f}')
    if dsr_old > 0:
        max_pass_old = n_trials
    if dsr_new > 0:
        max_pass_new = n_trials

print('\n=== max_pass 对比 ===')
print(f'  旧公式 (H0/H1 混用): max_pass = {max_pass_old}')
print(f'  新公式 (标准 Bailey): max_pass = {max_pass_new}')

# 使用新公式作为最终结果
max_pass = max_pass_new

# P0-3 修复: Sharpe CV 改用 12 月滚动序列
print('\n=== Sharpe CV 计算 (12 月滚动序列, P0-3 修复) ===')
rolling_sharpes = []
rolling_window = 12  # 12 月滚动窗口
for i in range(rolling_window, n + 1):
    window = returns[i - rolling_window:i]
    if len(window) >= 6 and statistics.stdev(window) > 0:  # 至少 6 个月, 避免早期 NaN
        w_mean = statistics.mean(window)
        w_std = statistics.stdev(window)
        w_sharpe = (w_mean / w_std) * math.sqrt(12)  # 年化
        rolling_sharpes.append(w_sharpe)

if len(rolling_sharpes) >= 2:
    sharpe_cv_new = statistics.stdev(rolling_sharpes) / abs(statistics.mean(rolling_sharpes))
    print(f'  滚动窗口数: {len(rolling_sharpes)}')
    print(f'  滚动 Sharpe 序列: {[f"{s:.3f}" for s in rolling_sharpes]}')
    print(f'  Sharpe CV (滚动) = {sharpe_cv_new:.4f}')
else:
    sharpe_cv_new = float('inf')
    print(f'  滚动窗口数不足 ({len(rolling_sharpes)}), 无法计算 CV')

# 旧公式 (2 点 CV) 保留用于对比
window1 = returns[:15]
window2 = returns[15:]
w1_sharpe = (statistics.mean(window1) / statistics.stdev(window1)) * math.sqrt(12)
w2_sharpe = (statistics.mean(window2) / statistics.stdev(window2)) * math.sqrt(12)
sharpe_cv_old = statistics.stdev([w1_sharpe, w2_sharpe]) / abs(statistics.mean([w1_sharpe, w2_sharpe]))
print(f'\n  对比: 旧 CV (2 点) = {sharpe_cv_old:.4f}, 新 CV (滚动) = {sharpe_cv_new:.4f}')

# 综合评估 (用户确认的新标准, 使用修正后的指标)
annual_return = result.get('annual_return', 0)
max_drawdown = result.get('max_drawdown', 0)
win_rate = result.get('win_rate', 0)

# 使用滚动 CV 作为最终结果
sharpe_cv = sharpe_cv_new

print(f'\n{"=" * 70}')
print('综合评估 (修正后标准, 2026-07-25 审计 P0-2/P0-3 修复)')
print(f'{"=" * 70}')

checks = [
    ('Sharpe CV < 1.0 (12月滚动)', sharpe_cv < 1.0, f'{sharpe_cv:.4f} (滚动窗口数={len(rolling_sharpes)})'),
    ('DSR max_pass >= 5 (标准公式)', max_pass >= 5, f'max_pass={max_pass}'),
    ('年化收益 >= 15%', annual_return >= 0.15, f'{annual_return*100:.2f}%'),
    ('最大回撤 <= 10%', max_drawdown <= 0.10, f'{max_drawdown*100:.2f}%'),
    ('胜率 >= 60% (额外)', win_rate >= 0.60, f'{win_rate*100:.2f}%'),
]

all_pass = True
print()
for name, passed, value in checks:
    status = '✓ PASS' if passed else '✗ FAIL'
    print(f'  {status} | {name}: {value}')
    if not passed:
        all_pass = False

# DSR 显著性补充说明
dsr_at_5 = compute_dsr_correct(sharpe_monthly, n, skewness, kurtosis, 5)
p_at_5 = 1 - stats.norm.cdf(dsr_at_5)
dsr_at_8 = compute_dsr_correct(sharpe_monthly, n, skewness, kurtosis, 8)
p_at_8 = 1 - stats.norm.cdf(dsr_at_8)

print('\n=== DSR 统计显著性分析 ===')
print(f'  DSR(n=5) = {dsr_at_5:+.4f}, p-value = {p_at_5:.4f} {"< 0.05 显著" if p_at_5 < 0.05 else ">= 0.05 不显著"}')
print(f'  DSR(n=8) = {dsr_at_8:+.4f}, p-value = {p_at_8:.4f} {"< 0.05 显著" if p_at_8 < 0.05 else ">= 0.05 不显著"}')
print('  注: 实际研究过程测试了 V6/V6.1/V6.2/V7/V7.1/V7.2/V8/V9 共 8+ 版本')
print('  Bailey 标准: DSR > 0.95 (单尾 p<0.05) 才能拒绝"无 alpha"零假设')

print(f'\n{"=" * 70}')
if all_pass:
    print('✓ V9 在修正后标准下全部达标!')
else:
    print('✗ V9 在修正后标准下未全部达标')
    print(f'  关键问题: DSR p-value = {p_at_5:.4f} >= 0.05, 统计上无法拒绝"无 alpha"零假设')
    print(f'  即使 max_pass={max_pass} >= 5, 这只是"DSR>0"的试验数, 不等于统计显著')
print(f'{"=" * 70}')

# 保存
summary = {
    'sharpe_annual': sharpe_annual,
    'sharpe_monthly': sharpe_monthly,
    'skewness': skewness,
    'kurtosis_fisher': kurtosis,
    'max_pass_old_formula': max_pass_old,
    'max_pass_new_formula': max_pass_new,
    'max_pass': max_pass,
    'sharpe_cv_old_2point': sharpe_cv_old,
    'sharpe_cv_new_rolling': sharpe_cv_new,
    'sharpe_cv': sharpe_cv,
    'rolling_sharpes': rolling_sharpes,
    'rolling_window': rolling_window,
    'dsr_at_5': dsr_at_5,
    'p_value_at_5': p_at_5,
    'dsr_at_8': dsr_at_8,
    'p_value_at_8': p_at_8,
    'annual_return': annual_return,
    'max_drawdown': max_drawdown,
    'win_rate': win_rate,
    'n_months': n,
    'all_pass': all_pass,
    'checks': [(name, passed, value) for name, passed, value in checks],
    'formula_fix': 'P0-2: 修正 DSR 公式 H0/H1 混用; P0-3: Sharpe CV 改用 12 月滚动序列',
}

out_file = Path(r'e:\各种PY程序\28-终极量化交易系统8.4\output\validation_reports\v9_dsr_maxpass_20260725.json')
with open(out_file, 'w', encoding='utf-8') as f:
    json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
print(f'\n结果已保存: {out_file}')
