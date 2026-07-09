import sys, json
from pathlib import Path
import pandas as pd

sys.path.insert(0, r'e:\各种PY程序\28-终极量化交易系统7.1')
from utils.data_provider import MarketDataProvider

base = Path(r'e:\各种PY程序\28-终极量化交易系统7.1\config')
positions_path = base / 'positions.json'
with open(positions_path, 'r', encoding='utf-8') as f:
    positions_data = json.load(f)['positions']

symbols = [item.get('code') for item in positions_data.values() if item.get('code')]
print(f'标的数量: {len(symbols)}')
print(f'前5个标的: {symbols[:5]}')

provider = MarketDataProvider()
for symbol in symbols[:3]:
    df = provider.get_historical_data(symbol, '1y')
    print(f'\n=== {symbol} ===')
    print(f'shape: {df.shape}')
    print(f'index type: {df.index.dtype}')
    print(f'columns: {list(df.columns)}')
    print(f'head:\n{df.head(3)}')
    print(f'tail:\n{df.tail(3)}')
    if 'close' in df.columns:
        r = df['close'].pct_change().dropna()
        print(f'returns shape: {r.shape}')
        print(f'returns head: {r.head(3).tolist()}')
        print(f'returns tail: {r.tail(3).tolist()}')

# 构造 returns_df 并检查
returns_data = {}
for symbol in symbols:
    df = provider.get_historical_data(symbol, '1y')
    if df is not None and not df.empty and 'close' in df.columns:
        returns_data[symbol] = df['close'].pct_change().dropna()

if returns_data:
    returns_df = pd.DataFrame(returns_data)
    print(f'\n=== returns_df ===')
    print(f'shape: {returns_df.shape}')
    print(f'columns: {list(returns_df.columns)}')
    print(f'index length: {len(returns_df.index)}')
    print(f'head:\n{returns_df.head(3)}')
    print(f'tail:\n{returns_df.tail(3)}')
