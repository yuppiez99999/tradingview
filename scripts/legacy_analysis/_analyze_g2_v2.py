# -*- coding: utf-8 -*-
"""分析第六批次v2 G2 详情"""
import json
from pathlib import Path

state_path = Path(r"e:\各种PY程序\28-终极量化交易系统8.4\research\vibe_trading_factor_analysis\reports\vibe_trading\sixth_batch_20260725_120832\pipeline_state.json")

with open(state_path, "r", encoding="utf-8") as f:
    state = json.load(f)

print(f"=== Batch: {state.get('batch_id')} ===")
print(f"Total: {state.get('total_candidates')} | G1: {state.get('g1_passed')} | G2: {state.get('g2_passed')} | Deferred: {state.get('deferred_fundamentals')}")

print("\n=== 各因子 G2 详情（按 IC_IR 排序）===")
factors = state.get("factors", [])
g2_data = []
for f in factors:
    name = f.get("factor_name")
    state_str = f.get("state")
    g1 = f.get("g1_orthogonality") or {}
    g2 = f.get("g2_ic_stability") or {}

    if g2:
        g2_data.append({
            "name": name,
            "state": state_str,
            "g1_pass": g1.get("passed"),
            "g2_pass": g2.get("passed"),
            "ic": g2.get("ic", 0),
            "ic_ir": g2.get("ic_ir_estimated", 0),
            "ic_mean": g2.get("ic_mean", 0),
            "ic_std": g2.get("ic_std", 0),
            "ic_decay": g2.get("ic_decay_estimated", 0),
            "n_days": g2.get("n_days", 0),
        })

# 按 |IC_IR| 降序
g2_data.sort(key=lambda x: -abs(x["ic_ir"]))

print(f"\n{'因子':35s} {'G1':5s} {'G2':5s} {'IC':>8s} {'IC_IR':>8s} {'IC_std':>8s} {'n_days':>7s} {'decay':>7s}")
print("-" * 100)
for d in g2_data:
    g1_mark = "✓" if d["g1_pass"] else "✗"
    g2_mark = "✓" if d["g2_pass"] else "✗"
    print(f"{d['name']:35s} {g1_mark:5s} {g2_mark:5s} {d['ic']:>8.4f} {d['ic_ir']:>8.4f} {d['ic_std']:>8.4f} {d['n_days']:>7d} {d['ic_decay']:>7.3f}")

# 统计 IC 分布
import numpy as np

ics = [d["ic"] for d in g2_data]
ic_irs = [d["ic_ir"] for d in g2_data]
print("\n=== 统计 ===")
print(f"IC  均值={np.mean(ics):+.4f} | 中位数={np.median(ics):+.4f} | 范围 [{min(ics):+.4f}, {max(ics):+.4f}]")
print(f"IC_IR 均值={np.mean(ic_irs):+.4f} | 中位数={np.median(ic_irs):+.4f} | 范围 [{min(ic_irs):+.4f}, {max(ic_irs):+.4f}]")
print(f"|IC_IR| >= 0.3 的因子数: {sum(1 for x in ic_irs if abs(x) >= 0.3)}")
print(f"|IC_IR| >= 0.2 的因子数: {sum(1 for x in ic_irs if abs(x) >= 0.2)}")
print(f"|IC_IR| >= 0.1 的因子数: {sum(1 for x in ic_irs if abs(x) >= 0.1)}")
