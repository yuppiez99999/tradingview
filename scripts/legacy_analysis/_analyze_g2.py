# -*- coding: utf-8 -*-
"""分析第六批次 G2 失败的根因"""
import json
from pathlib import Path

state_path = Path(r"e:\各种PY程序\28-终极量化交易系统8.4\research\vibe_trading_factor_analysis\reports\vibe_trading\sixth_batch_20260725_120601\pipeline_state.json")

with open(state_path, "r", encoding="utf-8") as f:
    state = json.load(f)

print(f"=== Batch: {state.get('batch_id')} ===")
print(f"Total: {state.get('total_candidates')} | G1: {state.get('g1_passed')} | G2: {state.get('g2_passed')} | Deferred: {state.get('deferred_fundamentals')}")

print("\n=== 各因子 G2 详情 ===")
for f in state.get("factors", []):
    name = f.get("factor_name")
    state_str = f.get("state")
    g1 = f.get("g1_orthogonality") or {}
    g2 = f.get("g2_ic_stability") or {}
    fail_reasons = f.get("fail_reasons") or []

    print(f"\n[{name}] state={state_str}")
    print(f"  G1 passed={g1.get('passed')} | max_abs_corr={g1.get('max_abs_corr')}")
    print(f"  G2 passed={g2.get('passed')}")
    print(f"  G2 fields: {g2}")
    print(f"  Fail reasons: {fail_reasons}")
