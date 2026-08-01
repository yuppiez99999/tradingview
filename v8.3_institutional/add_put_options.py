"""添加 Put 期权到今日交易计划"""
import json

plan_path = r'e:\各种PY程序\28-终极量化交易系统7.1\v7.5_institutional\trade_plans\trade_plan_20260721.json'
with open(plan_path, encoding='utf-8') as f:
    plan = json.load(f)

existing = plan.get('execution_plan', {}).get('options_orders', [])
print(f"当前期权订单: {len(existing)} 笔")
for o in existing:
    print(f"  {o['direction']} {o['code']} {o['contracts']}张 权利金={o['est_premium_total']:.2f}")

put_orders = [
    {"code": "510050", "name": "510050_Put", "direction": "BUY_PUT", "underlying": "510050",
     "contracts": 20, "est_premium_total": 300000, "session": "morning",
     "note": "尾部风险保护: 上证50认沽 OTM_5%"},
    {"code": "588080", "name": "588080_Put", "direction": "BUY_PUT", "underlying": "588080",
     "contracts": 10, "est_premium_total": 120000, "session": "morning",
     "note": "尾部风险保护: 科创50认沽 OTM_5%"},
    {"code": "159915", "name": "159915_Put", "direction": "BUY_PUT", "underlying": "159915",
     "contracts": 10, "est_premium_total": 100000, "session": "morning",
     "note": "尾部风险保护: 创业板认沽 OTM_5%"},
    {"code": "510300", "name": "510300_Put", "direction": "BUY_PUT", "underlying": "510300",
     "contracts": 5, "est_premium_total": 40000, "session": "morning",
     "note": "尾部风险保护: 沪深300认沽 OTM_5%"},
]

existing.extend(put_orders)
plan['execution_plan']['options_orders'] = existing
plan['execution_plan']['options_orders_count'] = len(existing)
plan['execution_plan']['options_total_premium'] = sum(o['est_premium_total'] for o in existing)

with open(plan_path, 'w', encoding='utf-8') as f:
    json.dump(plan, f, ensure_ascii=False, indent=2)

print(f"\n更新后期权订单: {len(existing)} 笔")
print(f"总权利金/预算: {plan['execution_plan']['options_total_premium']:.2f}")
for o in existing:
    print(f"  {o['direction']} {o['code']} {o['contracts']}张 权利金={o['est_premium_total']:.2f}")
