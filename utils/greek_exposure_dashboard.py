# -*- coding: utf-8 -*-
"""
Greeks 暴露监控面板

输出组合当前的 Greeks 暴露，并给出再平衡建议。
"""

from __future__ import annotations

from typing import Dict, Optional


def load_positions(path: str):
    import json
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    positions = {}
    prices = {}
    for item in data.get('positions', {}).values():
        code = item.get('code')
        qty = item.get('phase1_shares') or item.get('total_shares') or item.get('shares', 0)
        price = item.get('est_price', 0.0)
        if code and qty:
            positions[code] = {
                'shares': float(qty),
                'est_price': float(price),
                'beta': float(item.get('beta', 1.0)),
                'delta': float(item.get('delta', 1.0)),
                'gamma': float(item.get('gamma', 0.0)),
                'theta': float(item.get('theta', 0.0)),
                'vega': float(item.get('vega', 0.0)),
            }
            prices[code] = float(price)
    return positions, prices


def print_dashboard(positions_path: str = r'e:\各种PY程序\28-终极量化交易系统7.1\config\positions.json'):
    positions, prices = load_positions(positions_path)
    try:
        from utils.greek_hedge_manager import GreekHedgeManager
        manager = GreekHedgeManager(target_delta=0.0, max_vega=50000.0, max_theta_burn=-5000.0)
        exposure = manager.calc_portfolio_greeks(positions, prices)
        signal = manager.rebalance_signal(exposure)
        print('组合 Greeks 暴露监控')
        print(f"Delta: {exposure.delta:,.2f}")
        print(f"Gamma: {exposure.gamma:,.2f}")
        print(f"Theta: {exposure.theta:,.2f}")
        print(f"Vega: {exposure.vega:,.2f}")
        print('再平衡信号:')
        print(f"  Delta: {'需要再平衡' if signal['delta_rebalance'] else '正常'}")
        print(f"  Gamma: {'需要再平衡' if signal['gamma_rebalance'] else '正常'}")
        print(f"  Vega: {'需要再平衡' if signal['vega_rebalance'] else '正常'}")
        print(f"  Theta: {'需要再平衡' if signal['theta_rebalance'] else '正常'}")
        print(f"  综合: {'需要再平衡' if signal['need_rebalance'] else '正常'}")
    except Exception as e:
        print(f'Greeks 计算失败: {e}')


if __name__ == '__main__':
    print_dashboard()
