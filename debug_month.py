# -*- coding: utf-8 -*-
"""单月 debug：验证信号融合后的权重是否真的随月份变化"""
import json
from datetime import datetime
from institutional_pipeline_runner import PipelineContext, InstitutionalPipelineRunner

SYMBOLS = ["600519", "000858", "601318", "000001", "600036", "601398", "600276", "000063"]
DATES = ["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01", "2024-05-01", "2024-06-01"]

for date in DATES:
    ctx = PipelineContext(
        mode="backtest",
        symbols=SYMBOLS,
        report_date=date,
    )
    runner = InstitutionalPipelineRunner(ctx)
    result = runner.run()
    weights = result.get("steps", {}).get("portfolio_decision", {}).get("target_weights", {}) or {}
    status = result.get("status", "unknown")
    print(f"{date} | status={status} | weights={weights}")
