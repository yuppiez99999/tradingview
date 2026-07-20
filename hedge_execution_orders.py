# -*- coding: utf-8 -*-
"""
对冲执行单生成器 v2.0
修复：订单去重、动态Beta计算、配置对齐
"""
import sys
import os
import json
from datetime import datetime
from collections import OrderedDict

sys.path.insert(0, r'e:\各种PY程序\28-终极量化交易系统7.1')

STYLE_BETA_MAP = {
    "科技": 1.20,
    "金融": 0.90,
    "宽基": 0.95,
    "新能源": 1.15,
    "医药": 0.85,
    "资源": 1.10,
    "制造": 1.05,
    "顺周期": 1.10,
    "防御": 0.60,
    "国债": 0.10,
    "成长": 1.20,
    "default": 1.00,
}

def load_positions():
    path = r'e:\各种PY程序\28-终极量化交易系统7.1\config\positions.json'
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    positions = {}
    prices = {}
    hedge_positions = data.get('hedge_positions', {})
    for item in data.get('positions', {}).values():
        code = item.get('code')
        qty = item.get('phase1_shares') or item.get('total_shares') or item.get('shares', 0)
        price = item.get('est_price', 0.0)
        if code and qty:
            positions[code] = float(qty)
            prices[code] = float(price)
    return positions, prices, hedge_positions, data

def calc_portfolio_beta(positions_data: dict) -> float:
    total_weighted_beta = 0.0
    total_weight = 0.0
    for item in positions_data.get('positions', {}).values():
        style = item.get('style', 'default')
        weight = item.get('target_weight', 0.0)
        beta = STYLE_BETA_MAP.get(style, STYLE_BETA_MAP['default'])
        total_weighted_beta += weight * beta
        total_weight += weight
    if total_weight > 0:
        return total_weighted_beta / total_weight
    return 0.7

def merge_orders(orders: list) -> list:
    merged = OrderedDict()
    for o in orders:
        key = (o['type'], o.get('instrument', ''), o.get('action', ''))
        if key not in merged:
            merged[key] = o.copy()
        else:
            merged[key]['contracts'] = merged[key].get('contracts', 0) + o.get('contracts', 0)
            merged[key]['premium_budget'] = merged[key].get('premium_budget', 0) + o.get('premium_budget', 0)
            merged[key]['budget_pct'] = max(merged[key].get('budget_pct', 0), o.get('budget_pct', 0))
            if o.get('priority', 'secondary') == 'primary':
                merged[key]['priority'] = 'primary'
    return list(merged.values())

def build_orders(plan: dict, positions: dict, prices: dict, hedge_positions: dict, positions_data: dict) -> dict:
    if hedge_positions is None:
        hedge_positions = {}
    action = plan.get('action', 'NO_HEDGE')
    orders = []
    
    target = 5_000_000.0
    deployed = sum(float(pos) * prices.get(code, 0.0) for code, pos in positions.items())
    
    beta = float(plan.get('portfolio_beta', 0.0) or 0.0)
    if beta < 0.1:
        beta = calc_portfolio_beta(positions_data)
    hedge_pct = float(plan.get('total_hedge_pct', 0.0) or 0.0)
    if hedge_pct == 0.0:
        hedge_pct = 0.4 if beta > 0.5 else 0.2
    hedge_value = hedge_pct * target

    if hedge_pct > 0.05 and beta > 0.3:
        if beta > 1.1:
            option_notional = hedge_value * 0.7
            futures_notional = hedge_value * 0.3
        elif beta > 0.9:
            option_notional = hedge_value * 0.8
            futures_notional = hedge_value * 0.2
        else:
            option_notional = hedge_value * 0.9
            futures_notional = hedge_value * 0.1

        option_multiplier = 10000
        option_hedge_codes = [
            ("510050.SH", "510050 Put", "OTM_5%", 0.5),
            ("510300.SH", "沪深300ETF Put", "OTM_5%", 0.3),
            ("159915.SZ", "创业板ETF Put", "OTM_5%", 0.2),
        ]
        remaining_notional = option_notional
        for code, instrument, strike, weight in option_hedge_codes:
            if remaining_notional <= 0:
                break
            est_price = prices.get(code, 0.0)
            if est_price <= 0:
                continue
            alloc_notional = remaining_notional * weight
            cfg = next((cfg for key, cfg in hedge_positions.items() 
                       if instrument.split()[0] in cfg.get('instrument', '')), None)
            premium_budget = cfg.get('premium_budget', alloc_notional * 0.15) if cfg else alloc_notional * 0.15
            contracts = max(1, int(alloc_notional / (est_price * option_multiplier)))
            
            orders.append({
                'type': 'OPTIONS',
                'action': 'BUY_PROTECTION',
                'instrument': instrument,
                'exchange': 'SSE' if 'SH' in code else 'SZSE',
                'contracts': contracts,
                'strike': strike,
                'premium_budget': round(premium_budget, 2),
                'budget_pct': round(premium_budget / target, 4) if target > 0 else 0.0,
                'reason': f'Beta对冲({beta:.2f})优先期权保护',
                'framework': ['优先期权', 'Beta对冲', '尾部保护'],
                'beta_hedge_pct': round(alloc_notional / hedge_value, 2) if hedge_value > 0 else 0.0,
                'priority': 'primary',
            })
            remaining_notional -= alloc_notional

        if futures_notional > 0:
            futures_cfg = hedge_positions.get('IF_futures', {})
            target_contracts = futures_cfg.get('target_contracts', 1)
            if target_contracts > 0:
                instrument = 'IF'
                multiplier = 300
                fut_price = 3800.0
                notional = multiplier * fut_price
                n = target_contracts
                orders.append({
                    'type': 'FUTURES',
                    'action': 'SELL_SHORT',
                    'instrument': instrument,
                    'exchange': 'CFFEX',
                    'contracts': n,
                    'multiplier': multiplier,
                    'est_price': fut_price,
                    'notional': n * notional,
                    'estimated_cost': n * notional * 0.00013,
                    'budget_pct': round(n * notional / target, 4) if target > 0 else 0.0,
                    'reason': f'Beta期货对冲({beta:.2f})',
                    'framework': ['Beta对冲'],
                    'priority': 'primary',
                })

    option_cfgs = {}
    futures_cfgs = {}
    commodity_futures = {'CU', 'AL', 'LC', 'AU', 'RB', 'I', 'J', 'JM', '焦煤', '焦炭', '铁矿石', '螺纹', '铜', '铝', '碳酸锂', '黄金'}
    
    for key, cfg in hedge_positions.items():
        instrument = cfg.get('instrument', key)
        is_option = 'Put' in instrument or 'Call' in instrument or '期权' in instrument
        is_commodity = any(c in instrument.upper() for c in commodity_futures)
        
        if is_option:
            option_cfgs[key] = cfg
        elif is_commodity:
            if cfg.get('force_futures', False):
                futures_cfgs[key] = cfg
        else:
            futures_cfgs[key] = cfg

    for key, cfg in option_cfgs.items():
        instrument = cfg.get('instrument', key)
        existing = next((o for o in orders if o.get('instrument') == instrument), None)
        if existing:
            existing['contracts'] += cfg.get('target_contracts', 0)
            existing['premium_budget'] += cfg.get('premium_budget', 0)
            existing['reason'] = cfg.get('reason', existing['reason'])
            existing['framework'] = cfg.get('framework', existing['framework'])
        else:
            orders.append({
                'type': 'OPTIONS',
                'action': cfg.get('direction', 'BUY'),
                'instrument': instrument,
                'exchange': cfg.get('exchange', ''),
                'contracts': cfg.get('target_contracts', 0),
                'strike': cfg.get('strike', ''),
                'premium_budget': cfg.get('premium_budget', 0.0),
                'budget_pct': cfg.get('premium_budget', 0.0) / target if target > 0 else 0.0,
                'reason': cfg.get('reason', ''),
                'framework': cfg.get('framework', []),
                'priority': 'primary',
            })

    for key, cfg in futures_cfgs.items():
        instrument = cfg.get('instrument', key)
        if 'IF' in instrument and any(o['instrument'] == 'IF' for o in orders if o['type'] == 'FUTURES'):
            continue
        direction = cfg.get('direction', '')
        target_contracts = cfg.get('target_contracts', 0)
        multiplier = cfg.get('multiplier', 0)
        
        if target_contracts <= 0 or multiplier <= 0:
            continue
        
        is_commodity = any(c in instrument.upper() for c in commodity_futures)
        if is_commodity:
            existing_options = sum(1 for o in orders if o['type'] == 'OPTIONS')
            if existing_options >= 2:
                continue
        
        est_price = prices.get(instrument, 0.0)
        if est_price <= 0:
            if 'IF' in instrument.upper():
                est_price = 3800.0
            elif 'IM' in instrument.upper():
                est_price = 5800.0
            elif 'AU' in instrument.upper():
                est_price = 500.0
            else:
                est_price = 5000.0
        notional = target_contracts * multiplier * est_price
        orders.append({
            'type': 'FUTURES',
            'action': direction,
            'instrument': instrument,
            'exchange': cfg.get('exchange', ''),
            'contracts': target_contracts,
            'multiplier': multiplier,
            'est_price': est_price,
            'notional': notional,
            'estimated_cost': notional * cfg.get('margin_rate', 0.0),
            'budget_pct': notional / target,
            'reason': cfg.get('reason', ''),
            'framework': cfg.get('framework', []),
            'priority': 'secondary' if is_commodity else 'primary',
        })

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

    orders = merge_orders(orders)
    
    return {
        'date': datetime.now().strftime('%Y-%m-%d'),
        'action': action,
        'portfolio_beta': beta,
        'hedge_pct': hedge_pct,
        'orders': orders
    }

def main():
    positions, prices, hedge_positions, positions_data = load_positions()
    
    if len(sys.argv) > 1:
        target_date = sys.argv[1]
        try:
            dt = datetime.strptime(target_date, '%Y-%m-%d')
            today_str = dt.strftime('%Y%m%d')
            today_dash = dt.strftime('%Y-%m-%d')
        except ValueError:
            today_str = datetime.now().strftime('%Y%m%d')
            today_dash = datetime.now().strftime('%Y-%m-%d')
    else:
        today_str = datetime.now().strftime('%Y%m%d')
        today_dash = datetime.now().strftime('%Y-%m-%d')
    
    reports_dir = r'e:\各种PY程序\28-终极量化交易系统7.1\reports'
    plan_path = os.path.join(reports_dir, f'hedge_decision_{today_str}.json')
    plan = {'action': 'HEDGE', 'portfolio_beta': 0.0, 'total_hedge_pct': 0.0}
    
    if os.path.exists(plan_path):
        try:
            with open(plan_path, 'r', encoding='utf-8') as f:
                plan = json.load(f)
        except:
            pass
    else:
        prev_dates = []
        if os.path.exists(reports_dir):
            for f in os.listdir(reports_dir):
                if f.startswith('hedge_decision_') and f.endswith('.json'):
                    fp = os.path.join(reports_dir, f)
                    if os.path.getsize(fp) > 0:
                        prev_dates.append(f)
            prev_dates.sort(reverse=True)
        if prev_dates:
            try:
                with open(os.path.join(reports_dir, prev_dates[0]), 'r', encoding='utf-8') as f:
                    plan = json.load(f)
            except:
                pass
    
    beta_from_positions = calc_portfolio_beta(positions_data)
    print(f"[Beta计算] 旧决策Beta: {plan.get('portfolio_beta', 0.0):.4f}, 当前组合Beta: {beta_from_positions:.4f}")
    plan['portfolio_beta'] = beta_from_positions
    plan['total_hedge_pct'] = 0.4 if beta_from_positions > 0.5 else 0.2
    
    orders = build_orders(plan, positions, prices, hedge_positions, positions_data)
    out_path = os.path.join(reports_dir, f'hedge_execution_orders_{today_str}.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(orders, f, ensure_ascii=False, indent=2)
    
    archive_dir = os.path.join(r'e:\各种PY程序\每日报告归档', today_dash)
    os.makedirs(archive_dir, exist_ok=True)
    archive_path = os.path.join(archive_dir, f'对冲执行单_{today_str}.json')
    with open(archive_path, 'w', encoding='utf-8') as f:
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
        total_premium = 0
        total_notional = 0
        for i, o in enumerate(orders['orders'], 1):
            print(f"[{i}] {o['type']} | {o['action']} | {o.get('instrument')}")
            if 'contracts' in o:
                print(f"    手数/张数: {o['contracts']}")
            if 'notional' in o:
                total_notional += o['notional']
                print(f"    名义价值: {o['notional']:,.0f}")
            if 'amount' in o:
                print(f"    金额: {o['amount']:,.0f}")
            if 'estimated_cost' in o:
                print(f"    预估成本: {o['estimated_cost']:,.0f}")
            if 'premium_budget' in o:
                total_premium += o['premium_budget']
                print(f"    权利金预算: {o['premium_budget']:,.0f}")
            if 'budget_pct' in o:
                print(f"    预算占比: {o['budget_pct']*100:.2f}%")
            if 'reason' in o:
                print(f"    理由: {o['reason']}")
            if 'framework' in o:
                print(f"    框架: {', '.join(o['framework'])}")
            if 'priority' in o:
                print(f"    优先级: {o['priority']}")
            print()
        print(f"合计权利金: ¥{total_premium:,.0f}")
        print(f"合计名义价值: ¥{total_notional:,.0f}")
    else:
        print('今日无执行单')
    print('=' * 70)
    print(f'已保存: {out_path}')
    print(f'已归档: {archive_path}')


if __name__ == '__main__':
    main()