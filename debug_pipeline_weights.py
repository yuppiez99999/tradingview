# -*- coding: utf-8 -*-
"""快速验证：pipeline 输出的月度权重是否动态变化（只跑 pipeline，不跑完整回测）"""
import json
from datetime import datetime
from institutional_pipeline_runner import PipelineContext, InstitutionalPipelineRunner

SYMBOLS = ["600519", "000858", "601318", "000001", "600036", "601398", "600276", "000063"]
DATES = ["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01", "2024-05-01", "2024-06-01"]

results = []
for date in DATES:
    ctx = PipelineContext(
        mode="backtest",
        symbols=SYMBOLS,
        report_date=date,
    )
    runner = InstitutionalPipelineRunner(ctx)
    result = runner.run()
    weights = result.get("steps", {}).get("portfolio_decision", {}).get("target_weights", {}) or {}
    signals = result.get("steps", {}).get("signal_fusion", [])
    status = result.get("status", "unknown")
    nonzero_signals = {s.get("symbol"): s.get("strength") for s in signals if s.get("strength", 0) != 0}
    results.append({
        "date": date,
        "status": status,
        "nonzero_signals": nonzero_signals,
        "weights": weights,
    })
    print(f"{date} | status={status} | nonzero_signals={nonzero_signals}")
    print(f"        weights={weights}")
    print()

with open("output/debug_pipeline_weights.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print("结果已保存到 output/debug_pipeline_weights.json")
