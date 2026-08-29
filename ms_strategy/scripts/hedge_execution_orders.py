"""
对冲执行单生成器
输出可交易的期货/期权/避险资产执行单
"""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, r'e:\各种PY程序\28-终极量化交易系统7.1')

def load_positions():
    path = r'e:\各种PY程序\28-终极量化交易系统7.1\config\positions.json'
    with open(path, encoding='utf-8') as f:
        data = json.load(f)['positions']
    positions = {}
    prices = {}
    for item in data.values():
        code = item.get('code')
        qty = item.get('phase1_shares') or item.get('total_shares') or item.get('shares', 0)
        price = item.get('est_price', 0.0)
        if code and qty:
            positions[code] = float(qty)
            prices[code] = float(price)
    return positions, prices

def build_orders(plan: dict, positions: dict, prices: dict) -> dict:
    action = plan.get('action', 'NO_HEDGE')
    orders = []

    # 估算已用资金
    deployed = sum(qty * prices.get(code, 0.0) for code, qty in positions.items())
    target = 5_000_000.0

    # Beta 对冲
    beta = float(plan.get('portfolio_beta', 0.0) or 0.0)
    hedge_pct = float(plan.get('total_hedge_pct', 0.0) or 0.0)
    hedge_value = hedge_pct * target

    if beta > 1.1:
        instrument = 'IF'
        multiplier = 300
        fut_price = 3800.0
        beta_adj = 1.0
    elif beta > 0.9:
        instrument = 'IC'
        multiplier = 200
        fut_price = 5500.0
        beta_adj = 1.2
    else:
        instrument = 'IM'
        multiplier = 200
        fut_price = 5800.0
        beta_adj = 1.1

    notional = multiplier * fut_price
    n = int(hedge_value / (notional * beta_adj)) if notional * beta_adj > 0 else 0
    if n > 0:
        orders.append({
            'type': 'FUTURES',
            'action': 'SELL_SHORT',
            'instrument': instrument,
            'contracts': n,
            'notional': n * notional,
            'estimated_cost': n * notional * 0.00013,
            'budget_pct': n * notional / target,
        })

    # 避险追加
    defense_assets = {
        'sh600900': ('长江电力', 58_800),
        'sz518880': ('黄金ETF华安', 99_450),
        'sh601088': ('中国神华', 38_500),
    }
    defense_total = sum(v for _, v in defense_assets.values())
    target_defense = deployed * 0.15 if deployed > 0 else 0.0
    gap = max(0.0, target_defense - defense_total)
    if gap > 0:
        orders.append({
            'type': 'SAFE_HAVEN',
            'action': 'BUY',
            'instrument': '518880',
            'name': '黄金ETF华安',
            'amount': round(gap, 0),
            'budget_pct': gap / target,
        })

    # 尾部保护
    orders.append({
        'type': 'OPTIONS',
        'action': 'BUY_PROTECTION',
        'instrument': '沪深300ETF Put',
        'contracts': 1,
        'budget_pct': 0.01,
        'note': '行权价 95-90%'
    })

    # 若引擎返回 NO_HEDGE，但组合结构偏进攻，也给出可执行的保守执行单
    if action == 'NO_HEDGE' and not orders:
        orders.append({
            'type': 'SAFE_HAVEN',
            'action': 'BUY',
            'instrument': '518880',
            'name': '黄金ETF华安',
            'amount': round(target * 0.05, 0),
            'budget_pct': 0.05,
            'note': '结构对冲：增加5%避险底仓'
        })
        orders.append({
            'type': 'OPTIONS',
            'action': 'BUY_PROTECTION',
            'instrument': '沪深300ETF Put',
            'contracts': 1,
            'budget_pct': 0.01,
            'note': '结构对冲：买1张虚值Put'
        })

    return {
        'date': datetime.now().strftime('%Y-%m-%d'),
        'action': action,
        'portfolio_beta': beta,
        'hedge_pct': hedge_pct,
        'orders': orders
    }

def main():
    positions, prices = load_positions()
    plan_path = r'e:\各种PY程序\28-终极量化交易系统7.1\reports\hedge_decision_20260706.json'
    plan = {'action': 'NO_HEDGE', 'portfolio_beta': 0.0759, 'total_hedge_pct': 0.0}
    if os.path.exists(plan_path):
        with open(plan_path, encoding='utf-8') as f:
            plan = json.load(f)

    orders = build_orders(plan, positions, prices)
    out_path = r'e:\各种PY程序\28-终极量化交易系统7.1\reports\hedge_execution_orders_20260706.json'
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(orders, f, ensure_ascii=False, indent=2)

    print('=' * 70)
    print('对冲执行单')
    print('=' * 70)
    print(f"日期: {orders['date']}")
    print(f"动作: {orders['action']}")
    print(f"组合Beta: {orders.get('portfolio_beta', 0.0):.4f}")
    print(f"对冲比例: {orders.get('hedge_pct', 0.0)*100:.2f}%")
    print()
    if orders['orders']:
        for i, o in enumerate(orders['orders'], 1):
            print(f"[{i}] {o['type']} | {o['action']} | {o.get('instrument')}")
            if 'contracts' in o:
                print(f"    手数/张数: {o['contracts']}")
            if 'notional' in o:
                print(f"    名义价值: {o['notional']:,.0f}")
            if 'amount' in o:
                print(f"    金额: {o['amount']:,.0f}")
            if 'estimated_cost' in o:
                print(f"    预估成本: {o['estimated_cost']:,.0f}")
            if 'budget_pct' in o:
                print(f"    预算占比: {o['budget_pct']*100:.2f}%")
            print()
    else:
        print('今日无执行单')
    print('=' * 70)
    print(f'已保存: {out_path}')

if __name__ == '__main__':
    main()
