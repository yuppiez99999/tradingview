import sys
sys.path.insert(0, r'e:\各种PY程序\28-终极量化交易系统7.1')
from utils.ifind_futures_quotes import _get_ifind_token, _build_ths_command
import json
import urllib.parse
import urllib.request
import urllib.error

token = _get_ifind_token()
cmd = _build_ths_command(['IC2609.CFE', 'IF2609.CFE'], ['latest', 'volume', 'open', 'high', 'low', 'prevClose'])
encoded_cmd = urllib.parse.quote(cmd)
candidates = [
    "https://api.51ifind.com/api/command?token=" + urllib.parse.quote(token, safe="") + "&cmd=" + encoded_cmd + "&type=text",
    "https://api.51ifind.com/ths/command?token=" + urllib.parse.quote(token, safe="") + "&cmd=" + encoded_cmd,
    "https://open.51ifind.com/api/command?token=" + urllib.parse.quote(token, safe="") + "&cmd=" + encoded_cmd,
]

for url in candidates:
    print('\nURL:', url[:120])
    req = urllib.request.Request(url)
    req.add_header("Referer", "https://www.51ifind.com")
    req.add_header("User-Agent", "Mozilla/5.0")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        print('status:', resp.status)
        print('raw:', raw[:500])
    except urllib.error.HTTPError as e:
        print('http error:', e.code, e.reason)
        print('body:', e.read().decode('utf-8', errors='replace')[:500])
    except Exception as e:
        print('exception:', type(e).__name__, e)
