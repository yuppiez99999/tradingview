import sys
sys.path.insert(0, r'e:\各种PY程序\28-终极量化交易系统7.1\skills\ifind-finance-data')
from call import list_tools
import json

servers = ['stock', 'fund', 'edb', 'news', 'bond', 'global_stock', 'index']
for server in servers:
    try:
        res = list_tools(server)
        if res.get('ok'):
            tools = res.get('data', {}).get('result', {}).get('tools', [])
            print(f'\n=== {server} ({len(tools)} tools) ===')
            for t in tools:
                if isinstance(t, dict):
                    print(f"  - {t.get('name')}: {t.get('description', '')[:100]}")
    except Exception as e:
        print(f'{server}: ERROR - {e}')
