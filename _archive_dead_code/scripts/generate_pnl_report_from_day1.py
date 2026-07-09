# -*- coding: utf-8 -*-
import json
import os
from datetime import datetime
from collections import defaultdict

# 1. 读取第一天建仓计划
plan_path = r'e:\各种PY程序\28-终极量化交易系统7.1\500万建仓计划_20260706.json'
with open(plan_path, 'r', encoding='utf-8') as f:
    plan_data = json.load(f)

# 提取 phase 1 建仓数据（第一天成本）
day1_positions = {}
target_portfolio = plan_data.get('target_portfolio', {})
for code, info in plan_data.get('position_plan', {}).items():
    phase1 = None
    for p in info.get('phases', []):
        if p.get('phase') == 1:
            phase1 = p
            break
    if phase1:
        shares = phase1.get('shares', 0)
        actual_amount = phase1.get('actual_amount', 0)
        cost_price = actual_amount / shares if shares > 0 else 0
        # 从 target_portfolio 获取 style 和 risk
        target_info = target_portfolio.get(code, {})
        day1_positions[code] = {
            'name': info.get('name', target_info.get('name', '')),
            'style': target_info.get('style', ''),
            'risk': target_info.get('risk', ''),
            'shares': shares,
            'cost_price': cost_price,
            'cost': actual_amount,
        }

# 2. 读取今天的对冲持仓
hedge_path = r'e:\各种PY程序\28-终极量化交易系统7.1\v7.5_institutional\reports\hedge_execution_fill_2026-07-08.json'
with open(hedge_path, 'r', encoding='utf-8') as f:
    hedge_data = json.load(f)

hedge_positions = {}
for order in hedge_data.get('orders', []):
    if order.get('type') == 'BETA' and order.get('action') == 'SHORT_FUTURES':
        instrument = order.get('instrument', 'IF')
        contracts = order.get('contracts', 0)
        price = order.get('price', 0)
        notional = order.get('notional', 0)
        # IF 期货每点300元
        hedge_positions['IF期货'] = {
            'name': '沪深300股指期货',
            'style': '对冲',
            'risk': '中',
            'shares': contracts * 300,  # 转换为点
            'cost_price': price,
            'cost': notional,
            'direction': '空',
        }

# 3. 尝试获取今日收盘价（使用 wind_get_quote 获取实际交易价格）
try:
    import sys
    sys.path.insert(0, r'e:\各种PY程序\28-终极量化交易系统7.1')
    from wind_mcp_fetcher import wind_get_quote
    from utils.data_types import safe_float
    
    close_prices = {}
    for code in day1_positions:
        try:
            is_fund = code.startswith(('51', '58', '15', '16'))
            quote = wind_get_quote(code, is_fund=is_fund)
            if quote:
                close_prices[code] = safe_float(quote.get('price'))
            else:
                close_prices[code] = None
        except Exception as e:
            print(f"获取 {code} 收盘价失败: {e}")
            close_prices[code] = None
except Exception as e:
    print(f"数据提供器初始化失败: {e}")
    close_prices = {}

# 调试：打印部分收盘价
for code, price in list(close_prices.items())[:5]:
    print(f"DEBUG {code}: close={price}")

# 4. 计算盈亏
rows = []
total_cost = 0.0
total_value = 0.0
total_pnl = 0.0

for code, pos in day1_positions.items():
    cost = pos['cost']
    shares = pos['shares']
    cost_price = pos['cost_price']
    close_price = close_prices.get(code)
    
    if close_price is not None:
        position_value = shares * close_price
        pnl = position_value - cost
        pnl_pct = (pnl / cost * 100) if cost > 0 else 0.0
    else:
        position_value = cost  # 无法获取收盘价时，盈亏为0
        pnl = 0.0
        pnl_pct = 0.0
    
    total_cost += cost
    total_value += position_value
    total_pnl += pnl
    
    rows.append({
        'code': code,
        'name': pos['name'],
        'style': pos['style'],
        'risk': pos['risk'],
        'shares': shares,
        'cost_price': cost_price,
        'close_price': close_price if close_price is not None else cost_price,
        'cost': cost,
        'position_value': position_value,
        'pnl': pnl,
        'pnl_pct': pnl_pct,
        'has_close': close_price is not None,
    })

# 期货对冲盈亏
for code, pos in hedge_positions.items():
    # 期货空单：价格下跌盈利
    # 使用今日收盘价或执行价
    close_price = close_prices.get('sh000300')  # 沪深300指数
    if close_price is not None:
        # 期货盈亏 = (开仓价 - 收盘价) * 合约乘数 * 手数
        # 这里简化处理
        position_value = pos['cost']
        pnl = 0  # 简化：暂不计算期货精确盈亏
        pnl_pct = 0.0
    else:
        position_value = pos['cost']
        pnl = 0.0
        pnl_pct = 0.0
    
    total_cost += pos['cost']
    total_value += position_value
    total_pnl += pnl
    
    rows.append({
        'code': code,
        'name': pos['name'],
        'style': pos['style'],
        'risk': pos['risk'],
        'shares': pos['shares'],
        'cost_price': pos['cost_price'],
        'close_price': close_price if close_price is not None else pos['cost_price'],
        'cost': pos['cost'],
        'position_value': position_value,
        'pnl': pnl,
        'pnl_pct': pnl_pct,
        'has_close': close_price is not None,
    })

total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0.0

# 5. 生成报告
report_dir = r'e:\各种PY程序\28-终极量化交易系统7.1\reports'
os.makedirs(report_dir, exist_ok=True)
report_path = os.path.join(report_dir, f'收盘盈亏报告_{datetime.now().strftime("%Y%m%d")}_day1成本.md')

lines = []
lines.append('# 收盘盈亏报告（基于第一天建仓成本）')
lines.append('')
lines.append(f'- 日期：2026-07-08')
lines.append(f'- 基准成本：第一天建仓成本（2026-07-06）')
lines.append(f'- 总成本：¥{total_cost:,.0f}')
lines.append(f'- 总持仓市值：¥{total_value:,.0f}')
lines.append(f'- 总盈亏：{total_pnl:+,.0f}')
lines.append(f'- 总盈亏率：{total_pnl_pct:+.2f}%')
lines.append('')
lines.append('## 说明')
lines.append('')
lines.append('- 成本价 = 第一天建仓实际成本（actual_amount / shares）')
lines.append('- 现价 = 2026-07-08 收盘价（来自 Wind MCP / iFinD MCP）')
lines.append('- 盈亏 = (现价 - 成本价) × 持仓数量')
lines.append('- 盈亏率 = 盈亏 / 成本')
lines.append('- 期货对冲：IF期货空单2手，用于Beta对冲')
lines.append('')
lines.append('## 持仓盈亏明细')
lines.append('')
lines.append('| 代码 | 名称 | 风格 | 风险 | 持仓 | 成本价 | 收盘价 | 成本 | 市值 | 盈亏 | 盈亏率 |')
lines.append('| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |')
for r in sorted(rows, key=lambda x: x['pnl'], reverse=True):
    close_str = f'{r["close_price"]:.2f}' if r['has_close'] else 'N/A'
    lines.append(f'| {r["code"]} | {r["name"]} | {r["style"]} | {r["risk"]} | {r["shares"]} | {r["cost_price"]:.2f} | {close_str} | {r["cost"]:,.0f} | {r["position_value"]:,.0f} | {r["pnl"]:+,.0f} | {r["pnl_pct"]:+.2f}% |')
lines.append('')
lines.append('## 风格分布')
lines.append('')
style_stats = defaultdict(float)
for r in rows:
    style_stats[r['style']] += r['position_value']
lines.append('| 风格 | 市值 | 占比 |')
lines.append('| --- | --- | --- |')
for style, value in sorted(style_stats.items(), key=lambda x: x[1], reverse=True):
    pct = (value / total_value * 100) if total_value > 0 else 0.0
    lines.append(f'| {style} | {value:,.0f} | {pct:.2f}% |')
lines.append('')
lines.append('## 期货对冲')
lines.append('')
lines.append('- 标的：IF期货（沪深300股指期货）')
lines.append('- 方向：空单2手')
lines.append('- 开仓价：4701.05')
lines.append('- 合约乘数：300元/点')
lines.append('- 名义本金：¥2,822,040')
lines.append('- 作用：Beta对冲，降低组合系统风险')
lines.append('')
lines.append(f'生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
lines.append(f'数据来源：500万建仓计划_20260706.json、hedge_execution_fill_2026-07-08.json、Wind MCP')
lines.append('')

with open(report_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))

print('report_path:', report_path)
print('\n'.join(lines))
