# -*- coding: utf-8 -*-
"""诊断回测：打印单月信号与权重"""
import json
import logging
from datetime import datetime

import numpy as np
import pandas as pd

from institutional_pipeline_runner import InstitutionalPipelineRunner, PipelineContext

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("debug_backtest")

SYMBOLS = ["600519", "000858", "601318", "000001", "600036", "601398", "600276", "000063"]


def debug_month(report_date: str):
    ctx = PipelineContext(
        mode="backtest",
        symbols=SYMBOLS,
        report_date=report_date,
    )
    runner = InstitutionalPipelineRunner(ctx)
    result = runner.run()

    steps = result.get("steps", {})
    fusion = steps.get("signal_fusion", [])
    portfolio = steps.get("portfolio_decision", {})

    print(f"\n===== {report_date} =====")
    print("Fusion signals:")
    for s in fusion:
        print(f"  {s.get('symbol')}: strength={s.get('strength')}, confidence={s.get('confidence')}, sources={s.get('sources')}")
    print("Portfolio weights:")
    print(f"  {portfolio.get('target_weights')}")
    print("Portfolio meta:")
    print(f"  {portfolio.get('meta')}")


if __name__ == "__main__":
    for d in ["2024-01-01", "2024-03-01", "2024-06-01", "2024-09-01", "2024-12-01", "2025-03-01", "2025-06-01", "2025-09-01", "2025-12-01"]:
        debug_month(d)
