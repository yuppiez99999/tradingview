# -*- coding: utf-8 -*-
import json
import os
import sys
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wind_mcp_fetcher import wind_get_quote, wind_get_batch_quotes

positions_path = r'e:\各种PY程序\28-终极量化交易系统7.1\config\positions.json'
with open(positions_path, 'r', encoding='utf-8') as f:
    data = json.load(f)

positions = data.get('positions', {})

wind_codes = {}
for key, item in positions.items():
    code = item.get('code', key)
    if code.startswith('51') or code.startswith('58'):
        wind_code = f"{code}.SH"
        is_fund = True
    elif code.startswith(('15', '16')):
        wind_code = f"{code}.SZ"
        is_fund = True
    elif code.startswith('6'):
        wind_code = f"{code}.SH"
        is_fund = False
    elif code.startswith(('0', '3')):
        wind_code = f"{code}.SZ"
        is_fund = False
    else:
        continue
    wind_codes[code] = {'wind_code': wind_code, 'is_fund': is_fund, 'item': item}

print(f'Fetching Wind MCP quotes for {len(wind_codes)} positions...')
results = {}
for code, info in wind_codes.items():
    q = wind_get_quote(info['wind_code'], is_fund=info['is_fund'])
    results[code] = q
    print(f"{code} {info['item'].get('name', '')}: {q.get('price') if q else None}")

meta = data.get('meta', {})
total_value = 0.0
total_cost = float(meta.get('day_capital', 0.0))
style_stats = defaultdict(float)
rows = []

for key, item in positions.items():
    code = item.get('code', key)
    name = item.get('name', '')
    style = item.get('style', '其他')
    shares = int(item.get('phase1_shares') or item.get('total_shares') or item.get('shares', 0))
    cost = float(item.get('phase1_amount', 0.0))
    cost_price = (cost / shares) if shares > 0 else 0.0

    q = results.get(code)
    price = float(q['price']) if q and q.get('price') is not None else 0.0
    source = q.get('source', 'wind_mcp') if q else 'wind_mcp_failed'

    position_value = shares * price
    pnl = position_value - cost
    pnl_pct = (pnl / cost * 100) if cost > 0 else 0.0
    total_value += position_value
    style_stats[style] += position_value
    rows.append({
        'code': code,
        'name': name,
        'style': style,
        'shares': shares,
        'price': price,
        'cost_price': cost_price,
        'cost': cost,
        'position_value': position_value,
        'pnl': pnl,
        'pnl_pct': pnl_pct,
        'source': source,
        'last_update': datetime.now().isoformat(),
    })

total_pnl = total_value - total_cost
total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0.0

report_dir = r'e:\各种PY程序\28-终极量化交易系统7.1\reports'
os.makedirs(report_dir, exist_ok=True)
report_path = os.path.join(report_dir, f'收盘报告_计划成本_{datetime.now().strftime("%Y%m%d")}.md')

lines = []
lines.append('# 收盘报告（计划成本版）')
lines.append('')
lines.append(f'- 日期：{meta.get("date", datetime.now().strftime("%Y-%m-%d"))}')
lines.append(f'- 阶段：{meta.get("phase", "")}')
lines.append(f'- 总资金：{total_cost:,.0f}')
lines.append(f'- 总持仓市值：{total_value:,.0f}')
lines.append(f'- 总盈亏：{total_pnl:+,.0f}')
lines.append(f'- 总盈亏率：{total_pnl_pct:+.2f}%')
lines.append('')
lines.append('## 说明')
lines.append('')
lines.append('- 成本价 = phase1_amount / phase1_shares')
lines.append('- 现价 = Wind MCP 实时/收盘价')
lines.append('- 盈亏率 = (市值 - 成本) / 成本')
lines.append('- 若当天刚建仓，盈亏率应接近 0%')
lines.append('')
lines.append('## 持仓明细')
lines.append('')
lines.append('| 代码 | 名称 | 风格 | 持仓 | 现价 | 成本价 | 成本 | 市值 | 盈亏 | 盈亏率 | 来源 | 更新时间 |')
lines.append('| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |')
for r in sorted(rows, key=lambda x: x['position_value'], reverse=True):
    lines.append(f'| {r["code"]} | {r["name"]} | {r["style"]} | {r["shares"]} | {r["price"]:.2f} | {r["cost_price"]:.2f} | {r["cost"]:,.0f} | {r["position_value"]:,.0f} | {r["pnl"]:+,.0f} | {r["pnl_pct"]:+.2f}% | {r["source"]} | {r["last_update"]} |')
lines.append('')
lines.append('## 风格分布')
lines.append('')
lines.append('| 风格 | 市值 | 占比 |')
lines.append('| --- | --- | --- |')
for style, value in sorted(style_stats.items(), key=lambda x: x[1], reverse=True):
    pct = (value / total_value * 100) if total_value > 0 else 0.0
    lines.append(f'| {style} | {value:,.0f} | {pct:.2f}% |')
lines.append('')
lines.append(f'生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
lines.append(f'数据来源：Wind MCP')
lines.append(f'成本口径：plan_cost = phase1_amount / phase1_shares')
lines.append('')

with open(report_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))

print('\nreport_path:', report_path)
print('\n'.join(lines))