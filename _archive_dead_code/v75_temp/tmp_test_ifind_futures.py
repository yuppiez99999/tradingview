import sys
sys.path.insert(0, r'e:\各种PY程序\28-终极量化交易系统7.1')
from utils.ifind_futures_quotes import fetch_futures_quotes
import json

symbols = ['IC2609.CFE', 'IF2609.CFE', 'AU2608.SHF', 'CU2608.SHF', 'M2609.DCE', 'C2609.DCE']
quotes = fetch_futures_quotes(symbols)
print('count=', len(quotes))
print(json.dumps(quotes, ensure_ascii=False, indent=2))
