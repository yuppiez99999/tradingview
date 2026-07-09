# -*- coding: utf-8 -*-
"""
调试 Beta 计算
"""
import sys
import os
import json
import pandas as pd

sys.path.insert(0, r'e:\各种PY程序\28-终极量化交易系统7.1')

from utils.data_provider import MarketDataProvider

# 读取持仓
positions_path = r'e:\各种PY程序\28-终极量化交易系统7.1\config\positions.json'
with open(positions_path, 'r', encoding='utf-8') as f:
    positions_data = json.load(f)['positions']

positions = {}
prices = {}
for key, item in positions_data.items():
    code = item.get('code')
    qty = item.get('phase1_shares') or item.get('total_shares') or item.get('shares', 0)
    price = item.get('est_price', 0.0)
    if code and qty:
        positions[code] = float(qty)
        prices[code] = float(price)

print(f'positions keys: {list(positions.keys())[:5]}...')
print(f'prices keys: {list(prices.keys())[:5]}...')
print()

# 读取收益率数据
returns_path = r'e:\各种PY程序\28-终极量化交易系统7.1\config\returns_history.json'
market_path = r'e:\各种PY程序\28-终极量化交易系统7.1\config\market_returns.json'

returns = pd.read_json(returns_path, orient='split')
market_returns = pd.read_json(market_path, orient='split', typ='series')

print(f'returns columns: {list(returns.columns)[:5]}...')
print(f'returns index: {returns.index[:3]}...')
print(f'market_returns index: {market_returns.index[:3]}...')
print()

# 检查匹配情况
matched = [s for s in positions if s in prices and s in returns.columns]
unmatched = [s for s in positions if s not in returns.columns]

print(f'matched positions: {len(matched)}/{len(positions)}')
print(f'unmatched positions: {unmatched}')
print()

# 计算 market_value
market_value = {s: positions.get(s, 0) * prices.get(s, 0)
                for s in positions if s in prices and s in returns.columns}
total = sum(market_value.values())
print(f'total market value: {total:,.0f}')
print(f'matched value: {sum(market_value.values()):,.0f}')
print()

if total > 0:
    # 手动计算 portfolio beta
    sys.path.insert(0, r'e:\各种PY程序\28-终极量化交易系统7.1\v7.5_institutional\src')
    from hedging.beta_hedger import BetaHedger
    hedger = BetaHedger()
    
    beta_port = 0.0
    for symbol, value in market_value.items():
        w = value / total
        b = hedger.ewma_beta(returns[symbol], market_returns)
        print(f'{symbol}: weight={w:.4f}, beta={b:.4f}')
        beta_port += w * b
    
    print(f'\nportfolio beta: {beta_port:.4f}')
else:
    print('ERROR: total market value is 0 or negative')
    print('This means prices or positions are empty/mismatched')
