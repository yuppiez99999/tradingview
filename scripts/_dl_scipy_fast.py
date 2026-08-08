"""从清华镜像快速下载 scipy (cp38 win_amd64)"""
import os, sys, subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
PY = os.path.join(BASE_DIR, 'qlib_env', 'Scripts', 'python.exe')

clean_env = {k: v for k, v in os.environ.items() 
             if 'proxy' not in k.lower()}

# Direct Tsinghua mirror URLs (faster in China)
# scipy 1.10.1 cp38 win_amd64
URLS = [
    'https://pypi.tuna.tsinghua.edu.cn/packages/3a/ee/c07e31e8099ea3bccd5f2242f1101e93ba0d768982920a97fb1c5dac4f52/scipy-1.10.1-cp38-cp38-win_amd64.whl',
    'https://pypi.org/pypi/scipy/1.10.1/json',  # fallback: get URL from JSON  
]

import requests
s = requests.Session()
s.trust_env = False

# Try direct URL first
for url in URLS:
    if '/json' in url:
        # Get actual URL from JSON
        r = s.get(url)
        data = r.json()
        for f in data['urls']:
            if 'cp38' in f['filename'] and 'win_amd64' in f['filename']:
                url = f['url']
                break
    break

print(f'Downloading from: {url[:80]}...')
try:
    dl = s.get(url, stream=True)
    dl.raise_for_status()
    
    fn = 'scipy-1.10.1-cp38-cp38-win_amd64.whl'
    whl_path = os.path.join(SCRIPT_DIR, fn)
    
    with open(whl_path, 'wb') as f:
        for chunk in dl.iter_content(65536):
            f.write(chunk)
    
    size = os.path.getsize(whl_path)
    print(f'Downloaded {size//1024//1024}MB')
    
    # Install
    r = subprocess.run(
        [PY, '-X', 'utf8', '-m', 'pip', 'install', whl_path,
         '--no-deps', '--force-reinstall', '--proxy='],
        capture_output=True, text=True, env=clean_env, timeout=120
    )
    print(f'RC={r.returncode}')
    os.unlink(whl_path)
    
    # Verify
    r = subprocess.run(
        [PY, '-X', 'utf8', '-c', 
         'import scipy.sparse; print("scipy OK"); '
         'from lightgbm import LGBMRegressor; print("LGBM OK"); '
         'import sklearn; print("sklearn OK")'],
        capture_output=True, text=True, env=clean_env, timeout=10
    )
    print(r.stdout.strip() or r.stderr.strip()[:200])
    
except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
    
    # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
    print(f'Error: {e}')
