# -*- coding: utf-8 -*-
import json
import os
from datetime import datetime
from collections import defaultdict

# 读取执行日志
log_path = r'e:\各种PY程序\28-终极量化交易系统7.1\每日报告归档\2026\07\08\v75_daily_workflow_20260708.json'
with open(log_path, 'r', encoding='utf-8') as f:
    data = json.load(f)

fills = data.get('phases', {}).get('execute', {}).get('fills', [])

# 聚合持仓
positions = {}
for fill in fills:
    symbol = fill['symbol']
    if symbol not in positions:
        positions[symbol] = {
            'name': fill['name'],
            'style': fill['style'],
            'risk': fill['risk'],
            'shares': 0,
            'amount': 0.0,
            'price': fill['price']
        }
    positions[symbol]['shares'] += fill['qty']
    positions[symbol]['amount'] += fill['amount']

# 计算汇总
total_value = 0.0
total_cost = 0.0
style_stats = defaultdict(float)
rows = []

for symbol, pos in positions.items():
    shares = pos['shares']
    cost = pos['amount']
    price = pos['price']
    cost_price = cost / shares if shares > 0 else 0.0
    position_value = shares * price
    pnl = position_value - cost
    pnl_pct = (pnl / cost * 100) if cost > 0 else 0.0
    
    total_value += position_value
    total_cost += cost
    style_stats[pos['style']] += position_value
    
    rows.append({
        'code': symbol,
        'name': pos['name'],
        'style': pos['style'],
        'risk': pos['risk'],
        'shares': shares,
        'price': price,
        'cost_price': cost_price,
        'cost': cost,
        'position_value': position_value,
        'pnl': pnl,
        'pnl_pct': pnl_pct,
    })

total_pnl = total_value - total_cost
total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0.0

# 生成报告
report_dir = r'e:\各种PY程序\28-终极量化交易系统7.1\reports'
os.makedirs(report_dir, exist_ok=True)
report_path = os.path.join(report_dir, f'收盘报告_{datetime.now().strftime("%Y%m%d")}_plan.md')

lines = []
lines.append('# 收盘报告（计划成本版）')
lines.append('')
lines.append(f'- 日期：2026-07-08')
lines.append(f'- 阶段：第一阶段-底仓建立（第3天）')
lines.append(f'- 总资金：{total_cost:,.0f}')
lines.append(f'- 总持仓市值：{total_value:,.0f}')
lines.append(f'- 总盈亏：{total_pnl:+,.0f}')
lines.append(f'- 总盈亏率：{total_pnl_pct:+.2f}%')
lines.append('')
lines.append('## 说明')
lines.append('')
lines.append('- 成本价 = phase1_amount / phase1_shares（基于今日执行成交价）')
lines.append('- 现价 = 计划成本价（当日建仓，假设收盘价=成本价）')
lines.append('- 盈亏率 = (市值 - 成本) / 成本')
lines.append('- 若当天刚建仓，盈亏率应接近 0%')
lines.append('- 数据来源：v75_daily_workflow_20260708.json 执行日志')
lines.append('')
lines.append('## 持仓明细')
lines.append('')
lines.append('| 代码 | 名称 | 风格 | 风险 | 持仓 | 现价 | 成本价 | 成本 | 市值 | 盈亏 | 盈亏率 |')
lines.append('| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |')
for r in sorted(rows, key=lambda x: x['position_value'], reverse=True):
    lines.append(f'| {r["code"]} | {r["name"]} | {r["style"]} | {r["risk"]} | {r["shares"]} | {r["price"]:.2f} | {r["cost_price"]:.2f} | {r["cost"]:,.0f} | {r["position_value"]:,.0f} | {r["pnl"]:+,.0f} | {r["pnl_pct"]:+.2f}% |')
lines.append('')
lines.append('## 风格分布')
lines.append('')
lines.append('| 风格 | 市值 | 占比 |')
lines.append('| --- | --- | --- |')
for style, value in sorted(style_stats.items(), key=lambda x: x[1], reverse=True):
    pct = (value / total_value * 100) if total_value > 0 else 0.0
    lines.append(f'| {style} | {value:,.0f} | {pct:.2f}% |')
lines.append('')
lines.append('## 今日执行摘要')
lines.append('')
lines.append(f'- 成交笔数：25笔')
lines.append(f'- 成交金额：¥{total_cost:,.0f}')
lines.append(f'- 覆盖标的：{len(positions)}个')
lines.append(f'- 对冲执行：IF期货空单2手@4703.4')
lines.append('')
lines.append(f'生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
lines.append(f'数据来源：v75_daily_workflow_20260708.json')
lines.append(f'成本口径：基于今日执行成交价')
lines.append('')

with open(report_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))

print('report_path:', report_path)
print('\n'.join(lines))
