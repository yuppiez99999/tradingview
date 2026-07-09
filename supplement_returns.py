# -*- coding: utf-8 -*-
"""
历史收益率数据补充器
用于 v7.5 对冲引擎的真实 Beta / 相关性计算
"""
import sys
import os
import json
import pandas as pd
from datetime import datetime, timedelta

sys.path.insert(0, r'e:\各种PY程序\28-终极量化交易系统7.1')

from utils.data_provider import get_historical_data, MarketDataProvider

# 读取持仓
positions_path = r'e:\各种PY程序\28-终极量化交易系统7.1\config\positions.json'
with open(positions_path, 'r', encoding='utf-8') as f:
    positions_data = json.load(f)['positions']

symbols = []
symbol_map = {}
for key, item in positions_data.items():
    code = item.get('code')
    if code:
        symbols.append(code)
        symbol_map[code] = item.get('name', code)

print(f'需要获取历史数据的标的数量: {len(symbols)}')
print(f'标的列表: {", ".join(symbols[:10])}...')
print()

# 获取历史数据
provider = MarketDataProvider()
period = '1y'  # 1年历史数据，约252个交易日

returns_data = {}
market_symbol = '510300'  # 用沪深300ETF作为市场基准

print('开始获取历史数据...')
print('-' * 60)

success_count = 0
fail_count = 0

for symbol in symbols:
    try:
        df = provider.get_historical_data(symbol, period)
        if df is not None and not df.empty and 'close' in df.columns:
            # 计算日收益率
            df['return'] = df['close'].pct_change()
            returns_data[symbol] = df['return'].dropna()
            success_count += 1
            print(f'[成功] {symbol_map.get(symbol, symbol):12s} - {len(df)} 条数据')
        else:
            fail_count += 1
            print(f'[失败] {symbol_map.get(symbol, symbol):12s} - 无数据')
    except Exception as e:
        fail_count += 1
        print(f'[错误] {symbol_map.get(symbol, symbol):12s} - {e}')

print()
print('-' * 60)
print(f'获取完成: 成功 {success_count}, 失败 {fail_count}')
print()

# 获取市场基准收益率
print('获取市场基准收益率...')
market_df = provider.get_historical_data(market_symbol, period)
market_returns = None
if market_df is not None and not market_df.empty and 'close' in market_df.columns:
    market_returns = market_df['close'].pct_change().dropna()
    print(f'市场基准 {market_symbol}: {len(market_df)} 条数据')
else:
    print(f'警告: 无法获取市场基准 {market_symbol} 的数据')

print()

# 转换为DataFrame格式（用于对冲引擎）
if returns_data:
    returns_df = pd.DataFrame(returns_data)
    print(f'收益率DataFrame形状: {returns_df.shape}')
    print(f'日期范围: {returns_df.index[0]} ~ {returns_df.index[-1]}')
    print(f'列名: {list(returns_df.columns)}')
    print()
    
    # 保存到文件
    output_dir = r'e:\各种PY程序\28-终极量化交易系统7.1\config'
    os.makedirs(output_dir, exist_ok=True)
    
    returns_path = os.path.join(output_dir, 'returns_history.json')
    returns_df.to_json(returns_path, orient='split', date_format='iso')
    print(f'已保存收益率数据到: {returns_path}')
    
    # 保存市场收益率
    if market_returns is not None:
        market_path = os.path.join(output_dir, 'market_returns.json')
        market_returns.to_json(market_path, orient='split', date_format='iso')
        print(f'已保存市场收益率到: {market_path}')
    
    # 计算并显示一些统计信息
    print()
    print('-' * 60)
    print('收益率统计 (年化)')
    print('-' * 60)
    
    mean_returns = returns_df.mean() * 252
    std_returns = returns_df.std() * (252 ** 0.5)

    print(f'{"标的":12s} {"年化收益":>10s} {"年化波动":>10s} {"夏普":>8s}')
    print('-' * 60)
    for col in returns_df.columns:
        ret = mean_returns.get(col, 0)
        vol = std_returns.get(col, 0)
        ret = 0.0 if pd.isna(ret) else float(ret)
        vol = 0.0 if pd.isna(vol) else float(vol)
        sharpe = ret / vol if vol > 1e-6 else 0.0
        print(f'{symbol_map.get(col, col):12s} {ret:>10.2%} {vol:>10.2%} {sharpe:>8.2f}')
    
    print()
    print('下一步: 将 returns_df 和 market_returns 传入 HedgeCoordinator.coordinate()')
    print('        即可获得真实 Beta / 相关性 / 对冲手数')
else:
    print('错误: 没有获取到任何有效数据')
    print('请检查:')
    print('  1. 网络连接是否正常')
    print('  2. 数据源配置是否正确 (Wind/iFinD/AKShare/新浪)')
    print('  3. 标的代码格式是否正确')
