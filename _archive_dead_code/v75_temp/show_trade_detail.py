# -*- coding: utf-8 -*-
"""展示 2026-07-06 交易明细汇总"""
import json
from pathlib import Path
from collections import defaultdict

# 读取执行结果 JSON
p = Path(r'e:\各种PY程序\28-终极量化交易系统7.1\每日报告归档\2026\07\06\v75_daily_workflow_20260706.json')
state = json.loads(p.read_text(encoding='utf-8'))

fills = state.get('phases', {}).get('execute', {}).get('fills', [])
print(f'总成交笔数: {len(fills)}')
print()

# 按标的+session 汇总
summary = defaultdict(lambda: {
    'qty': 0, 'amount': 0.0, 'fills': 0,
    'name': '', 'style': '', 'risk': '',
    'est_price': 0.0, 'side': '', 'avg_price': 0.0,
})
for f in fills:
    key = (f.get('symbol', ''), f.get('session', ''))
    s = summary[key]
    s['qty'] += f.get('qty', 0)
    s['amount'] += f.get('amount', 0)
    s['fills'] += 1
    s['name'] = f.get('name', '')
    s['style'] = f.get('style', '')
    s['risk'] = f.get('risk', '')
    s['est_price'] = f.get('est_price', 0)
    s['side'] = f.get('side', '')

# 上午批次
print('=' * 90)
print('上午批次 (09:30-10:30)')
print('=' * 90)
print(f"{'#':>3} {'代码':<10} {'名称':<22} {'风格':<8} {'风险':<5} {'方向':<5} {'股数':>7} {'预估价':>8} {'成交均价':>9} {'金额':>10} {'笔数':>4}")
print('-' * 90)
morning_items = sorted([(k, v) for k, v in summary.items() if k[1] == 'morning'],
                       key=lambda x: -x[1]['amount'])
morning_total = 0
for i, ((sym, sess), s) in enumerate(morning_items, 1):
    avg = s['amount'] / s['qty'] if s['qty'] else 0
    print(f"{i:>3} {sym:<10} {s['name']:<22} {s['style']:<8} {s['risk']:<5} "
          f"{s['side']:<5} {s['qty']:>7} {s['est_price']:>8.2f} {avg:>9.4f} "
          f"{s['amount']:>10,.0f} {s['fills']:>4}")
    morning_total += s['amount']
print('-' * 90)
print(f"{'':>3} {'':<10} {'上午合计':<22} {'':<8} {'':<5} {'':<5} {'':>7} {'':>8} {'':>9} {morning_total:>10,.0f}")
print()

# 下午批次
print('=' * 90)
print('下午批次 (14:00-14:30)')
print('=' * 90)
print(f"{'#':>3} {'代码':<10} {'名称':<22} {'风格':<8} {'风险':<5} {'方向':<5} {'股数':>7} {'预估价':>8} {'成交均价':>9} {'金额':>10} {'笔数':>4}")
print('-' * 90)
afternoon_items = sorted([(k, v) for k, v in summary.items() if k[1] == 'afternoon'],
                         key=lambda x: -x[1]['amount'])
afternoon_total = 0
for i, ((sym, sess), s) in enumerate(afternoon_items, 1):
    avg = s['amount'] / s['qty'] if s['qty'] else 0
    print(f"{i:>3} {sym:<10} {s['name']:<22} {s['style']:<8} {s['risk']:<5} "
          f"{s['side']:<5} {s['qty']:>7} {s['est_price']:>8.2f} {avg:>9.4f} "
          f"{s['amount']:>10,.0f} {s['fills']:>4}")
    afternoon_total += s['amount']
print('-' * 90)
print(f"{'':>3} {'':<10} {'下午合计':<22} {'':<8} {'':<5} {'':<5} {'':>7} {'':>8} {'':>9} {afternoon_total:>10,.0f}")
print()

# 单日总计
print('=' * 90)
print(f'单日总计: 上午 {morning_total:,.0f} + 下午 {afternoon_total:,.0f} = {morning_total + afternoon_total:,.0f}')
print(f'预估金额: 1,722,410 元')
print(f'实际成交: {morning_total + afternoon_total:,.0f} 元')
print(f'滑点成本: {1722410 - (morning_total + afternoon_total):,.0f} 元 '
      f'({(1722410 - (morning_total + afternoon_total)) / 1722410 * 100:.4f}%)')
print('=' * 90)
