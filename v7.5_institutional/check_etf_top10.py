# -*- coding: utf-8 -*-
"""对比ETF资金净流入TOP10与今日交易计划"""
import json

# 图片中的ETF资金净流入TOP10
etf_top10 = [
    {"name": "华夏上证科创板50成份ETF", "code": "588000"},
    {"name": "华泰柏瑞沪深300ETF", "code": "510300"},
    {"name": "易方达创业板ETF", "code": "159915"},
    {"name": "南方中证1000ETF", "code": "512100"},
    {"name": "银华日利", "code": "511880"},
    {"name": "南方中证500ETF", "code": "510500"},
    {"name": "易方达沪深300ETF", "code": "510310"},
    {"name": "博时可转债ETF", "code": "511380"},
    {"name": "嘉实上证科创板芯片ETF", "code": "588200"},
    {"name": "国泰中证半导体材料设备主题ETF", "code": "159516"},
]

# 读取今日交易计划
plan_path = r'e:\各种PY程序\28-终极量化交易系统7.1\v7.5_institutional\trade_plans\trade_plan_20260721.json'
with open(plan_path, 'r', encoding='utf-8') as f:
    plan = json.load(f)

# 提取计划中所有标的代码
plan_codes = set()
plan_names = {}

# 股票订单
for s in plan.get('execution_plan', {}).get('stock_orders', []):
    code = str(s.get('code', '')).zfill(6)
    plan_codes.add(code)
    plan_names[code] = s.get('name', '')

# 期货订单
for f in plan.get('execution_plan', {}).get('futures_orders', []):
    code = str(f.get('code', '')).zfill(6)
    plan_codes.add(code)
    plan_names[code] = f.get('name', '')

# 期权订单
for o in plan.get('execution_plan', {}).get('options_orders', []):
    code = str(o.get('code', '')).zfill(6)
    plan_codes.add(code)
    plan_names[code] = o.get('name', '')

# 对比
print("=" * 80)
print("ETF资金净流入TOP10 vs 今日交易计划")
print("=" * 80)

in_plan = []
not_in_plan = []

for etf in etf_top10:
    code = etf['code'].zfill(6)
    if code in plan_codes:
        in_plan.append(etf)
        print(f"[OK] {etf['name']} ({code}) - 已在计划中")
    else:
        not_in_plan.append(etf)
        print(f"[NO] {etf['name']} ({code}) - 不在计划中")

print("\n" + "=" * 80)
print(f"统计: {len(in_plan)}/10 已在计划中, {len(not_in_plan)}/10 不在计划中")
print("=" * 80)

if not_in_plan:
    print("\n建议添加到计划的标的:")
    for etf in not_in_plan:
        print(f"  - {etf['name']} ({etf['code']})")

# 检查计划中额外的标的
print("\n计划中额外标的（不在TOP10中）:")
plan_etfs = [c for c in plan_codes if c.startswith(('51', '15', '58'))]
for code in sorted(plan_etfs):
    if code not in [e['code'].zfill(6) for e in etf_top10]:
        print(f"  - {plan_names.get(code, code)} ({code})")
