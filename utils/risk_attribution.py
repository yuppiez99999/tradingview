# -*- coding: utf-8 -*-
"""
风险归因面板

输出组合当前的风险来源分解，便于理解：
- 行业/风格集中度风险
- 个股特异性风险
- ETF 暴露风险
- 对冲工具剩余风险
"""

from __future__ import annotations

from typing import Dict, List


def load_positions(path: str):
    import json
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    positions = []
    for item in data.get('positions', {}).values():
        positions.append({
            'code': item.get('code', ''),
            'name': item.get('name', ''),
            'amount': float(item.get('amount', 0.0)),
            'style': item.get('style', ''),
            'sector': item.get('sector', ''),
            'type': item.get('type', 'STOCK'),
        })
    return positions


def print_attribution(positions_path: str = r'e:\各种PY程序\28-终极量化交易系统7.1\config\positions.json'):
    positions = load_positions(positions_path)
    if not positions:
        print('无持仓数据')
        return

    total = sum(p['amount'] for p in positions)
    if total <= 0:
        print('总持仓金额为 0')
        return

    print('组合风险归因')
    print(f"总持仓金额: ¥{total:,.0f}")

    sector: Dict[str, float] = {}
    style: Dict[str, float] = {}
    type_map: Dict[str, float] = {}
    for p in positions:
        sector[p.get('sector', '其他')] = sector.get(p.get('sector', '其他'), 0.0) + p['amount']
        style[p.get('style', '其他')] = style.get(p.get('style', '其他'), 0.0) + p['amount']
        type_map[p.get('type', 'STOCK')] = type_map.get(p.get('type', 'STOCK'), 0.0) + p['amount']

    print('行业分布:')
    for k, v in sorted(sector.items(), key=lambda x: x[1], reverse=True):
        print(f"  {k}: ¥{v:,.0f} ({v/total*100:.2f}%)")

    print('风格分布:')
    for k, v in sorted(style.items(), key=lambda x: x[1], reverse=True):
        print(f"  {k}: ¥{v:,.0f} ({v/total*100:.2f}%)")

    print('资产类型分布:')
    for k, v in sorted(type_map.items(), key=lambda x: x[1], reverse=True):
        print(f"  {k}: ¥{v:,.0f} ({v/total*100:.2f}%)")


if __name__ == '__main__':
    print_attribution()
