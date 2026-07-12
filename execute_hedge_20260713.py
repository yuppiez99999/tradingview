# -*- coding: utf-8 -*-
"""
2026-07-13 对冲执行单自动执行
"""
import os
import json
from datetime import datetime

PROJECT_ROOT = r'e:\各种PY程序\28-终极量化交易系统7.1'
ORDERS_PATH = os.path.join(PROJECT_ROOT, 'reports', 'hedge_execution_orders_20260713.json')
POSITIONS_PATH = os.path.join(PROJECT_ROOT, 'config', 'positions.json')
REPORT_PATH = os.path.join(PROJECT_ROOT, 'reports', 'hedge_execution_report_20260713.md')

def load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def build_markdown(orders: dict, execution: list) -> str:
    lines = []
    lines.append('# 2026-07-13 对冲执行报告')
    lines.append('')
    lines.append(f"- 日期: {orders['date']}")
    lines.append(f"- 动作: {orders['action']}")
    lines.append(f"- 组合Beta: {orders.get('portfolio_beta', 0.0):.4f}")
    lines.append(f"- 对冲比例: {orders.get('hedge_pct', 0.0)*100:.2f}%")
    lines.append('')
    lines.append('## 执行结果')
    lines.append('')
    if not execution:
        lines.append('今日无实际执行。')
        return '\n'.join(lines)

    total_notional = 0.0
    total_cost = 0.0
    for i, item in enumerate(execution, 1):
        o = item['order']
        status = item['status']
        lines.append(f"### [{i}] {o.get('type')} | {o.get('action')} | {o.get('instrument')}")
        lines.append(f"- 状态: {status}")
        if 'contracts' in o:
            lines.append(f"- 手数/张数: {o['contracts']}")
        if 'notional' in o:
            n = float(o['notional'])
            lines.append(f"- 名义价值: {n:,.0f}")
            total_notional += n
        if 'estimated_cost' in o:
            c = float(o['estimated_cost'])
            lines.append(f"- 预估成本: {c:,.0f}")
            total_cost += c
        if 'premium_budget' in o:
            p = float(o['premium_budget'])
            lines.append(f"- 期权权利金预算: {p:,.0f}")
        if 'reason' in o:
            lines.append(f"- 理由: {o['reason']}")
        if 'framework' in o:
            lines.append(f"- 框架: {', '.join(o['framework'])}")
        lines.append('')

    lines.append('## 汇总')
    lines.append('')
    lines.append(f"- 执行笔数: {len(execution)}")
    lines.append(f"- 总名义价值: {total_notional:,.0f}")
    lines.append(f"- 总预估成本: {total_cost:,.0f}")
    lines.append(f"- 报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append('')
    return '\n'.join(lines)

def main():
    orders = load_json(ORDERS_PATH)
    positions = load_json(POSITIONS_PATH)
    hedge_positions = positions.get('hedge_positions', {})

    execution = []
    for o in orders.get('orders', []):
        instrument = o.get('instrument')
        key = None
        for k, cfg in hedge_positions.items():
            if cfg.get('instrument') == instrument or k == instrument:
                key = k
                break

        if key and key in hedge_positions:
            hedge_positions[key]['executed_date'] = orders['date']
            hedge_positions[key]['executed_status'] = 'simulated'
            hedge_positions[key]['executed_qty'] = o.get('contracts') or o.get('target_contracts') or 0
            if 'notional' in o:
                hedge_positions[key]['executed_notional'] = float(o['notional'])
            if 'estimated_cost' in o:
                hedge_positions[key]['executed_cost'] = float(o['estimated_cost'])
            if 'premium_budget' in o:
                hedge_positions[key]['executed_premium_budget'] = float(o['premium_budget'])
            execution.append({'order': o, 'status': 'simulated'})
        else:
            execution.append({'order': o, 'status': 'skipped'})

    positions['hedge_positions'] = hedge_positions
    save_json(POSITIONS_PATH, positions)
    markdown = build_markdown(orders, execution)
    with open(REPORT_PATH, 'w', encoding='utf-8') as f:
        f.write(markdown)

    print('=' * 70)
    print('2026-07-13 对冲执行报告')
    print('=' * 70)
    print(markdown)
    print('=' * 70)
    print(f"已更新持仓: {POSITIONS_PATH}")
    print(f"已保存报告: {REPORT_PATH}")

if __name__ == '__main__':
    main()
