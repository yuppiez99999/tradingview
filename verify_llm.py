import os
import sys
from pathlib import Path

BASE = Path(r'e:\各种PY程序\28-终极量化交易系统7.1\v7.5_institutional')
sys.path.insert(0, str(BASE / 'src'))
_PROJECT_ROOT = BASE.parent
_ENV_PATH = _PROJECT_ROOT / '.env'
print('ENV_PATH=', _ENV_PATH)
print('EXISTS=', _ENV_PATH.exists())
if _ENV_PATH.exists():
    with open(_ENV_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            line=line.strip()
            if line and not line.startswith('#') and '=' in line:
                k,_,v=line.partition('=')
                print('LOAD=', k.strip(), '=>', v.strip().strip("\"'")[:10]+'...')

sys.path.insert(0, str(_PROJECT_ROOT / '15_每日工作流'))
import llm_client as llm
print('doubao_key=', bool(llm.VOLCENGINE_API_KEY))
print('test=', llm.test_connection())
