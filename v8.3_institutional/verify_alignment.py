#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证已修正文件与宏观点评模块
"""

import json
from pathlib import Path

BASE_DIR = Path(r"e:\各种PY程序\28-终极量化交易系统7.1")
TRADE_PLAN = BASE_DIR / r"v7.5_institutional\trade_plans\trade_plan_20260706.json"
BUILD_PLAN = BASE_DIR / "500万建仓计划_20260706.json"
POSITIONS = BASE_DIR / "config" / "positions.json"
WF = BASE_DIR / r"v7.5_institutional\daily_workflow.py"
MM = BASE_DIR / r"v7.5_institutional\src\macro\macro_policy_scoring.py"

print("=== 交易计划 ===")
data = json.loads(TRADE_PLAN.read_text(encoding="utf-8"))
print('capital=', data.get('capital'))
print('stock_etf_capital=', data.get('stock_etf_capital'))
print('hedge_capital=', data.get('hedge_capital'))
print('phase=', data.get('phase', {}).get('capital_ratio'), data.get('phase', {}).get('phase_capital'), data.get('phase', {}).get('day_capital'))
print('asset_count=', data.get('phase', {}).get('asset_count'))
print('total_orders=', data.get('execution_plan', {}).get('total_orders'))
print('grand_total=', data.get('execution_plan', {}).get('grand_total'))
print('phase_roadmap=', [(p['phase'], p['capital_ratio'], p['capital_amount']) for p in data.get('phase_roadmap', [])])

print("\n=== 建仓计划 ===")
bdata = json.loads(BUILD_PLAN.read_text(encoding="utf-8"))
print('metadata stock=', bdata.get('metadata', {}).get('stock_etf_capital'))
print('metadata hedge=', bdata.get('metadata', {}).get('hedge_capital'))
print('target_total=', sum(v.get('target_amount', 0) for v in bdata.get('target_portfolio', {}).values()))

print("\n=== 持仓 ===")
pdata = json.loads(POSITIONS.read_text(encoding="utf-8"))
positions = pdata.get('positions', pdata if isinstance(pdata, list) else [])
print('positions=', len(positions))

print("\n=== Workflow 残留 28/400/100 标记 ===")
text = WF.read_text(encoding="utf-8")
for token in ['28 标的', '400 万 (80%)', '100 万 (20%)', '22.5% / P2 20%']:
    print(token, 'exists=', token in text)

print("\n=== 宏观点评模块存在 ===")
print('macro module exists=', MM.exists())
