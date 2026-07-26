# -*- coding: utf-8 -*-
"""调试 trade_plan 字段"""
import json
from pathlib import Path

p = Path('v8.3_institutional/trade_plans/trade_plan_20260727.json')
d = json.load(open(p, encoding='utf-8'))

ph = d.get('phase', {})
ep = d.get('execution_plan', {})
rg = d.get('risk_guard', {})
he = d.get('hedge_execution', {})
foh = d.get('futures_options_hedge', {})

print('=== phase ===')
for k, v in ph.items():
    print(f'  {k}: {v}')

print('\n=== execution_plan (资金相关) ===')
for k in ['day_capital', 'original_day_capital', 'total_amount', 'total_orders',
          'morning_orders_count', 'afternoon_orders_count', 'options_orders_count']:
    print(f'  {k}: {ep.get(k)}')

print('\n=== risk_guard (关键字段) ===')
for k in ['vol_scale', 'vol_action', 'vol_scale_executed_summary', 'drawdown_level', 'drawdown_action']:
    v = rg.get(k)
    if isinstance(v, dict):
        print(f'  {k}:')
        for kk, vv in v.items():
            print(f'    {kk}: {vv}')
    else:
        print(f'  {k}: {v}')

print('\n=== hedge_execution ===')
for k in ['execution_status', 'futures_orders', 'options_orders', 'execution_notes']:
    v = he.get(k)
    if isinstance(v, list):
        print(f'  {k}: list len={len(v)}')
        for i, item in enumerate(v[:3]):
            print(f'    [{i}]: {item}')
    else:
        print(f'  {k}: {v}')

print('\n=== futures_options_hedge ===')
for k in ['loaded', 'orders_count', 'execution_status', 'orders']:
    v = foh.get(k)
    if isinstance(v, list):
        print(f'  {k}: list len={len(v)}')
    else:
        print(f'  {k}: {v}')

# 检查 budget_cut_reason
print('\n=== budget_cut_reason ===')
print(f'  phase.budget_cut_reason: {ph.get("budget_cut_reason")}')
print(f'  risk_guard.budget_cut_reason: {rg.get("budget_cut_reason")}')
