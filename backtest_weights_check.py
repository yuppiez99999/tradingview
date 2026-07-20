# -*- coding: utf-8 -*-
"""只校验回测输入来源的脚本"""
from __future__ import annotations

import json
from pathlib import Path

from backtest_runner import run_backtest

result = run_backtest(["600519", "000858", "601318"], "2024-01-01", "2025-12-31")
out = Path("output/backtest_weights_check.json")
out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(out)
print("annual_return=", result.get("annual_return"))
print("max_drawdown=", result.get("max_drawdown"))
print("win_rate=", result.get("win_rate"))
print("first_weights=", result.get("records", [{}])[0].get("weights"))
