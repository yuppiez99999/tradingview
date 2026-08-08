"""下载 scipy wheel (大文件 ~32MB) 并安装"""
import os, sys, subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
PY = os.path.join(BASE_DIR, 'qlib_env', 'Scripts', 'python.exe')

# Clean proxy env
clean_env = {k: v for k, v in os.environ.items() 
             if 'proxy' not in k.lower()}

import requests
s = requests.Session()
s.trust_env = False
s.timeout = (10, 300)

# 1. Find scipy cp38 wheel URL
print('Finding scipy wheel for Python 3.8...')
r = s.get('https://pypi.org/pypi/scipy/json')
data = r.json()

url = fn = None
for ver in sorted(data['releases'].keys(),
                  key=lambda v: tuple(int(x) for x in v.replace('rc','.').replace('dev','.').split('.')[:3]),
                  reverse=True):
    for f in data['releases'][ver]:
        fname = f['filename']
        if 'cp38' in fname and 'win_amd64' in fname and fname.endswith('.whl'):
            url = f['url']
            fn = fname
            size_mb = f.get('size', 0) // (1024*1024)
            print(f'Found: [{ver}] {fname} ({size_mb}MB)')
            break
    if url:
        break

if not url:
    print('ERROR: No compatible scipy wheel!')
    sys.exit(1)

# 2. Download
whl_path = os.path.join(SCRIPT_DIR, fn)
if os.path.exists(whl_path):
    print(f'Already downloaded: {whl_path}')
else:
    print(f'Downloading {size_mb}MB (may take 30-120s)...')
    dl = s.get(url, stream=True)
    dl.raise_for_status()
    total = int(dl.headers.get('content-length', 0))
    with open(whl_path, 'wb') as f:
        downloaded = 0
        for chunk in dl.iter_content(chunk_size=8192):
            f.write(chunk)
            downloaded += len(chunk)
    print(f'Downloaded: {downloaded//1024//1024}MB')

# 3. Install
print('Installing...')
r = subprocess.run(
    [PY, '-X', 'utf8', '-m', 'pip', 'install', whl_path,
     '--no-deps', '--force-reinstall', '--proxy='],
    capture_output=True, text=True, env=clean_env, timeout=120
)
print(f'RC={r.returncode}')
if r.returncode != 0:
    print(f'STDERR: {r.stderr.strip()[-300:]}')
    
os.unlink(whl_path)

# 4. Verify all
print('\nVerifying all ML packages...')
r = subprocess.run(
    [PY, '-X', 'utf8', '-c', 
     'import scipy.sparse; print("scipy.sparse OK"); '
     'from lightgbm import LGBMRegressor; print("LGBM OK"); '
     'import sklearn; print(f"sklearn {sklearn.__version__} OK"); '
     'import joblib; print(f"joblib {joblib.__version__} OK")'],
    capture_output=True, text=True, env=clean_env, timeout=10
)
if r.returncode == 0:
    print('\n'.join(f'  {l}' for l in r.stdout.strip().split('\n')))
    print('\nALL ML PACKAGES READY!')
else:
    print(f'FAILED: {r.stderr.strip()[:500]}')
