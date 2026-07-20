# -*- coding: utf-8 -*-
"""诊断IC为NaN的原因"""
import json
import numpy as np
import pandas as pd

report_path = r"e:\各种PY程序\28-终极量化交易系统7.1\reports\qlib_v3_train_20260711_165756.json"

with open(report_path, "r", encoding="utf-8") as f:
    report = json.load(f)

print("报告摘要:")
print(f"  日均 IC: {report['mean_daily_ic']}")
print(f"  IC IR: {report['ic_ir']}")

signals = report["stock_signals"]
latest_signals = [s["latest_signal"] for s in signals]
avg_signals = [s.get("qlib_avg_signal", s.get("avg_signal", 0)) for s in signals]

print(f"\n最新信号统计:")
print(f"  均值: {np.mean(latest_signals):.6f}")
print(f"  标准差: {np.std(latest_signals):.6f}")
print(f"  最小值: {np.min(latest_signals):.6f}")
print(f"  最大值: {np.max(latest_signals):.6f}")
print(f"  变异系数: {np.std(latest_signals)/np.abs(np.mean(latest_signals)):.4f}" if np.mean(latest_signals) != 0 else "  变异系数: N/A (均值为0)")

print(f"\n信号排名分布:")
ranks = [s["rank"] for s in signals]
print(f"  最小排名: {min(ranks)}")
print(f"  最大排名: {max(ranks)}")

print(f"\n信号方向:")
long = sum(1 for s in signals if s["direction"] == "看多")
short = sum(1 for s in signals if s["direction"] == "看空")
print(f"  看多: {long}, 看空: {short}")

print(f"\n信号绝对值排序:")
sorted_signals = sorted(signals, key=lambda x: abs(x["latest_signal"]), reverse=True)
for s in sorted_signals[:10]:
    print(f"  {s['code']} {s['name']:<10} 信号: {s['latest_signal']:>+.4f} 排名: {s['rank']}/{s['total']}")