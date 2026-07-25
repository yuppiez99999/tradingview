# -*- coding: utf-8 -*-
"""分析 V9 回测最终结果 (修复月度收益解析)"""
import re
import statistics
import json
from pathlib import Path

log_file = Path(r'e:\各种PY程序\28-终极量化交易系统8.4\v9_backtest.log')
with open(log_file, 'r', encoding='utf-8') as f:
    content = f.read()

print('=' * 70)
print('V9 Regime-Specific 回测最终结果分析 (修复解析)')
print('=' * 70)

# 关键指标
annual = re.search(r'年化收益: ([\d.\-]+)', content)
max_dd = re.search(r'最大回撤: ([\d.\-]+)', content)
win_rate = re.search(r'胜率: ([\d.\-]+)', content)
months = re.search(r'月数: (\d+)', content)

print('\n=== 关键指标 ===')
if annual: print(f'  年化收益: {annual.group(1)}')
if max_dd: print(f'  最大回撤: {max_dd.group(1)}')
if win_rate: print(f'  胜率: {win_rate.group(1)}')
if months: print(f'  月数: {months.group(1)}')

# 月度收益 - 只提取数值型 (过滤标的代码)
all_matches = re.findall(r'(\d{4}-\d{2}-\d{2}): ([\d.\-]+)', content)
monthly = [(date, float(ret)) for date, ret in all_matches if abs(float(ret)) < 10]  # 收益率应在 [-1, 1]

if monthly:
    print(f'\n=== 月度收益 ({len(monthly)} 个月) ===')
    for date, ret in monthly:
        print(f'  {date}: {ret:.4f}')

    returns = [ret for _, ret in monthly]

    # 窗口1: 2023-07 ~ 2024-09 (前 15 个月)
    window1 = returns[:15] if len(returns) >= 15 else returns
    # 窗口2: 2024-10 ~ 2025-12 (剩余月份)
    window2 = returns[15:] if len(returns) > 15 else []

    print(f'\n=== 窗口分析 ===')
    print(f'窗口1 ({len(window1)} 个月, 2023-07 ~ 2024-09):')
    if window1:
        w1_mean = statistics.mean(window1)
        w1_std = statistics.stdev(window1) if len(window1) > 1 else 0
        w1_sharpe = w1_mean / w1_std * (12**0.5) if w1_std > 0 else 0
        w1_annual = (1 + w1_mean) ** 12 - 1
        print(f'  平均月收益: {w1_mean:.4f}')
        print(f'  月收益标准差: {w1_std:.4f}')
        print(f'  年化收益: {w1_annual:.4f}')
        print(f'  年化 Sharpe: {w1_sharpe:.4f}')

    print(f'窗口2 ({len(window2)} 个月, 2024-10 ~ 2025-12):')
    if window2:
        w2_mean = statistics.mean(window2)
        w2_std = statistics.stdev(window2) if len(window2) > 1 else 0
        w2_sharpe = w2_mean / w2_std * (12**0.5) if w2_std > 0 else 0
        w2_annual = (1 + w2_mean) ** 12 - 1
        print(f'  平均月收益: {w2_mean:.4f}')
        print(f'  月收益标准差: {w2_std:.4f}')
        print(f'  年化收益: {w2_annual:.4f}')
        print(f'  年化 Sharpe: {w2_sharpe:.4f}')

    # Walk-Forward Sharpe CV
    if window1 and window2 and len(window1) > 1 and len(window2) > 1:
        w1_sharpe_annual = statistics.mean(window1) / statistics.stdev(window1) * (12**0.5)
        w2_sharpe_annual = statistics.mean(window2) / statistics.stdev(window2) * (12**0.5)
        sharpes = [w1_sharpe_annual, w2_sharpe_annual]
        sharpe_mean = statistics.mean(sharpes)
        sharpe_std = statistics.stdev(sharpes) if len(sharpes) > 1 else 0
        sharpe_cv = sharpe_std / abs(sharpe_mean) if abs(sharpe_mean) > 0 else float('inf')
        print(f'\n=== Walk-Forward Sharpe CV ===')
        print(f'  窗口1 Sharpe: {w1_sharpe_annual:.4f}')
        print(f'  窗口2 Sharpe: {w2_sharpe_annual:.4f}')
        print(f'  Sharpe 均值: {sharpe_mean:.4f}')
        print(f'  Sharpe 标准差: {sharpe_std:.4f}')
        print(f'  Sharpe CV: {sharpe_cv:.4f}')
        if sharpe_cv < 0.5:
            print(f'  ✓ 达成目标 Sharpe CV < 0.5!')
        else:
            print(f'  ✗ 未达成目标 Sharpe CV < 0.5 (当前 {sharpe_cv:.4f})')

        print(f'\n=== 与历史版本对比 ===')
        print(f'  V6.2: Sharpe CV = 0.55, 年化 = 14.35%, 回撤 = 7.90%')
        print(f'  V7.2: Sharpe CV = 0.71')
        print(f'  V9:   Sharpe CV = {sharpe_cv:.4f}, 年化 = {annual.group(1) if annual else "N/A"}, 回撤 = {max_dd.group(1) if max_dd else "N/A"}')

    # 关键月份分析
    print(f'\n=== 关键月份分析 ===')
    for date, ret in monthly:
        if date in ['2024-06-03', '2024-02-01', '2024-09-02', '2025-08-01', '2025-09-01']:
            print(f'  {date}: {ret:.4f}')

# 检查结果 JSON 文件
print(f'\n=== 检查结果 JSON 文件 ===')
result_files = list(Path(r'e:\各种PY程序\28-终极量化交易系统8.4\output\validation_reports').glob('v9_regime_specific_backtest_*.json'))
if result_files:
    latest = max(result_files, key=lambda p: p.stat().st_mtime)
    print(f'最新结果文件: {latest}')
    with open(latest, 'r', encoding='utf-8') as f:
        result = json.load(f)
    print(f'  annual_return: {result.get("annual_return", "N/A")}')
    print(f'  max_drawdown: {result.get("max_drawdown", "N/A")}')
    print(f'  win_rate: {result.get("win_rate", "N/A")}')
    print(f'  months: {result.get("months", "N/A")}')
    records = result.get('records', [])
    print(f'  records 数: {len(records)}')
    if records:
        print(f'  首条记录: {records[0].get("date", "N/A")}')
        print(f'  末条记录: {records[-1].get("date", "N/A")}')
