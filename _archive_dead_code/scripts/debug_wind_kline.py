import sys
sys.path.insert(0, r'e:\各种PY程序\28-终极量化交易系统7.1')
from wind_mcp_fetcher import wind_get_kline, _get_wind_api_key, _call_wind
import json

symbol = '510300'
windcode = '510300.SH'
print(f'WIND_API_KEY: {_get_wind_api_key()[:8]}...' if _get_wind_api_key() else 'None')

# 直接调用 wind_get_kline
items = wind_get_kline(windcode, days=10, is_fund=True)
print(f'wind_get_kline result type: {type(items)}')
if items is None:
    print('result is None')
elif isinstance(items, list):
    print(f'items length: {len(items)}')
    if items:
        print(f'first item keys: {list(items[0].keys())}')
        print(f'first item: {json.dumps(items[0], ensure_ascii=False)[:500]}')
else:
    print(f'result: {items}')

# 也直接调用 _call_wind 看原始返回
print('\n=== raw _call_wind ===')
res = _call_wind('fund_data', 'get_fund_kline', {"windcode": windcode, "begin_date": "20250601", "end_date": "20250706"})
print(f'ok: {res.get("ok")}')
print(f'error: {res.get("error")}')
if res.get('data'):
    data = res['data']
    content = (((data.get('result') or {}).get('content') or [{}])[0].get('text') or '')
    print(f'content[:300]: {content[:300]}')
