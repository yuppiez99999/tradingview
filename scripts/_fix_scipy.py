"""重新安装 scipy"""
import os, subprocess, sys

os.environ.pop('HTTP_PROXY', None)
os.environ.pop('HTTPS_PROXY', None)
os.environ.pop('http_proxy', None)
os.environ.pop('https_proxy', None)
os.environ['NO_PROXY'] = '*'

import requests
s = requests.Session()
s.trust_env = False

resp = s.get('https://pypi.org/pypi/scipy/json')
data = resp.json()

# Find latest cp38-win_amd64
found = False
for ver in sorted(data['releases'].keys(),
                  key=lambda v: tuple(int(x) for x in v.replace('rc','.').replace('dev','.').split('.')[:3]),
                  reverse=True):
    for f in data['releases'][ver]:
        fn = f['filename']
        if 'cp38' in fn and 'win_amd64' in fn and fn.endswith('.whl'):
            url = f['url']
            print(f'Found: [{ver}] {fn}')
            dl = s.get(url)
            dl.raise_for_status()
            whl = os.path.join(os.path.dirname(__file__), fn)
            with open(whl, 'wb') as fh:
                fh.write(dl.content)
            print(f'Saved: {len(dl.content)//1024}KB')
            found = True
            break
    if found:
        break

if not found:
    # Try pip
    print('No cp38 wheel found, trying pip...')
    sys.exit(1)

# Force reinstall
py = os.path.normpath(os.path.join(os.path.dirname(os.path.dirname(__file__)), 
                                    'qlib_env', 'Scripts', 'python.exe'))
r = subprocess.run(
    [py, '-X', 'utf8', '-m', 'pip', 'install', '--force-reinstall', whl, '--no-deps', '--proxy=""'],
    capture_output=True, text=True, env=os.environ, timeout=120
)
print(f'RC={r.returncode}')
os.unlink(whl)

# Verify
r2 = subprocess.run(
    [py, '-X', 'utf8', '-c', 'import scipy.sparse; print("scipy.sparse OK")'],
    capture_output=True, text=True, env=os.environ
)
print(r2.stdout.strip() or r2.stderr.strip()[:200])
