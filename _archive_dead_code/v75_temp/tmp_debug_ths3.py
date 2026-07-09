import sys
sys.path.insert(0, r'e:\各种PY程序\28-终极量化交易系统7.1')
from utils.ifind_futures_quotes import _get_ifind_token, _build_ths_command
import json

try:
    import requests
except Exception:
    print('requests not installed')
    sys.exit(0)

token = _get_ifind_token()
cmd = _build_ths_command(['IC2609.CFE', 'IF2609.CFE'], ['latest', 'volume', 'open', 'high', 'low', 'prevClose'])
encoded_cmd = __import__('urllib.parse', fromlist=['quote']).quote(cmd)
candidates = [
    "https://api.51ifind.com/api/command",
    "https://api.51ifind.com/ths/command",
    "https://open.51ifind.com/api/command",
]

for base in candidates:
    url = base
    print('\nURL:', url)
    try:
        resp = requests.post(url, data={
            'token': token,
            'cmd': cmd,
            'type': 'text',
        }, headers={
            'Referer': 'https://www.51ifind.com',
            'User-Agent': 'Mozilla/5.0',
        }, timeout=10, proxies={'http': None, 'https': None}, verify=False)
        print('status:', resp.status_code)
        print('body:', resp.text[:500])
    except Exception as e:
        print('exception:', type(e).__name__, e)
