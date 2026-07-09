#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
精确修正 trade_plan_20260706.json 的 phase 与资金字段，使其与 2026 交易计划对齐。
"""

import json
import os

BASE_DIR = r"e:\各种PY程序\28-终极量化交易系统7.1"
TRADE_PLAN_PATH = os.path.join(BASE_DIR, "v7.5_institutional", "trade_plans", "trade_plan_20260706.json")

TOTAL_CAPITAL = 5_000_000
EQUITY_CAPITAL = 3_000_000
HEDGE_CAPITAL = 2_000_000

PHASE_CONFIGS = [
    {"phase": 1, "capital_ratio": 0.35, "duration_days": 10},
    {"phase": 2, "capital_ratio": 0.30, "duration_days": 15},
    {"phase": 3, "capital_ratio": 0.20, "duration_days": 15},
    {"phase": 4, "capital_ratio": 0.15, "duration_days": 20},
]

with open(TRADE_PLAN_PATH, "r", encoding="utf-8") as f:
    data = json.load(f)

phase_number = data.get("phase", {}).get("phase_number", 1)
phase_cfg = next((p for p in PHASE_CONFIGS if p["phase"] == phase_number), PHASE_CONFIGS[0])

phase_capital = round(TOTAL_CAPITAL * phase_cfg["capital_ratio"], 2)
day_capital = round(phase_capital / phase_cfg["duration_days"], 2)

data["capital"] = TOTAL_CAPITAL
data["stock_etf_capital"] = EQUITY_CAPITAL
data["hedge_capital"] = HEDGE_CAPITAL

data["phase"]["capital_ratio"] = phase_cfg["capital_ratio"]
data["phase"]["phase_capital"] = phase_capital
data["phase"]["day_capital"] = day_capital

with open(TRADE_PLAN_PATH, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"已精确修正 phase: P{phase_number} capital_ratio={phase_cfg['capital_ratio']}, "
      f"phase_capital={phase_capital:,}, day_capital={day_capital:,}")
