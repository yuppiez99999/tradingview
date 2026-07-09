import sys
sys.path.insert(0, r'e:\各种PY程序\28-终极量化交易系统7.1\skills\ifind-finance-data')
from call import list_tools, call
import json

# Check all available tools more carefully
for server in ['stock', 'index']:
    res = list_tools(server)
    if res.get('ok'):
        tools = res.get("data", {}).get("result", {}).get("tools", [])
        print(f'\n=== {server} tools ===')
        for t in tools:
            if isinstance(t, dict):
                print(f"  {t.get('name')}: {t.get('description', '')[:200]}")

# Try stock_highfreq_quotes with futures-like symbols
print('\n=== Try stock_highfreq_quotes with futures symbols ===')
try:
    res = call('stock', 'stock_highfreq_quotes', {
        'symbols': 'IC2609.CFE',
        'indicators': 'latest;volume;open;high;low;prevClose',
        'data_mode': 'real_time',
        'interval': 1,
    })
    print('ok=', res.get('ok'))
    if res.get('ok'):
        data = res.get('data') or {}
        result = data.get('result') or {}
        content = result.get('content') or [{}]
        text = content[0].get('text', '') if content else ''
        print('text=', text[:500])
    else:
        print('error=', res.get('error'))
except Exception as e:
    print('exception=', e)
