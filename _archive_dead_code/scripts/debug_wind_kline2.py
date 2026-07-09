import sys, json
sys.path.insert(0, r'e:\各种PY程序\28-终极量化交易系统7.1')
from wind_mcp_fetcher import _call_wind, _get_wind_api_key, wind_get_kline

print('WIND_API_KEY:', _get_wind_api_key()[:8] + '...' if _get_wind_api_key() else 'None')

# 直接调用 _call_wind 看原始返回
res = _call_wind('fund_data', 'get_fund_kline', {"windcode": "510300.SH", "begin_date": "20250601", "end_date": "20250706"})
print('ok:', res.get('ok'))
print('error:', res.get('error'))
print('keys:', list(res.keys()))
print('data keys:', list(res.get('data', {}).keys()) if isinstance(res.get('data'), dict) else type(res.get('data')))
if isinstance(res.get('data'), dict):
    inner = res['data']
    content = (((inner.get('result') or {}).get('content') or [{}])[0].get('text') or '')
    print('content repr:', repr(content[:500]))
    if content:
        try:
            parsed = json.loads(content)
            print('parsed keys:', list(parsed.keys()) if isinstance(parsed, dict) else type(parsed))
            if isinstance(parsed, dict):
                print('data keys:', list(parsed.get('data', {}).keys()) if isinstance(parsed.get('data'), dict) else type(parsed.get('data')))
                print('result keys:', list(parsed.get('result', {}).keys()) if isinstance(parsed.get('result'), dict) else type(parsed.get('result')))
        except Exception as e:
            print('json parse error:', e)

# 也测 stock_data
print('\n=== stock_data ===')
res2 = _call_wind('stock_data', 'get_stock_kline', {"windcode": "300750.SZ", "begin_date": "20250601", "end_date": "20250706"})
print('ok:', res2.get('ok'))
print('error:', res2.get('error'))
if isinstance(res2.get('data'), dict):
    inner = res2['data']
    content = (((inner.get('result') or {}).get('content') or [{}])[0].get('text') or '')
    print('content repr:', repr(content[:500]))
