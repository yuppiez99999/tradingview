# -*- coding: utf-8 -*-
"""读取 2024 pipeline_backtest.json 并计算汇总指标"""
import json
from pathlib import Path

base = Path(r"E:\各种PY程序\28-终极量化交易系统7.1\output\institutional_pipeline")
files = sorted(base.glob("2024-*-01/pipeline_backtest.json"))

records = []
for f in files:
    data = json.loads(f.read_text(encoding="utf-8"))
    report_date = data.get("report_date", f.parent.name)
    pd = data.get("steps", {}).get("portfolio_decision", {})
    weights = pd.get("target_weights", {})
    expected_return = pd.get("expected_return")
    expected_risk = pd.get("expected_risk")
    records.append({
        "date": report_date,
        "weights": weights,
        "expected_return": expected_return,
        "expected_risk": expected_risk,
    })

print(f"已生成回测月份数: {len(records)}")
for r in records:
    print(f"{r['date']}: return={r['expected_return']}, risk={r['expected_risk']}, weights={r['weights']}")

# 汇总
if records:
    print("\n汇总:")
    print(f"  月份数: {len(records)}")
    print(f"  日期范围: {records[0]['date']} ~ {records[-1]['date']}")
    active = [r for r in records if r['weights']]
    if active:
        print(f"  有权重月份数: {len(active)}")
        all_weights = {}
        for r in active:
            for sym, w in r['weights'].items():
                all_weights[sym] = all_weights.get(sym, 0) + w
        print(f"  累计权重分布: {all_weights}")
