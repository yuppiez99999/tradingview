"""批量下载并安装所有ML包 (绕过代理)"""
import os, sys, traceback, subprocess

os.environ.pop('HTTP_PROXY', None)
os.environ.pop('HTTPS_PROXY', None)
os.environ.pop('http_proxy', None)
os.environ.pop('https_proxy', None)
os.environ['NO_PROXY'] = '*'

def parse_ver(v):
    parts = v.replace('rc','.').replace('dev','.').split('.')
    nums = []
    for p in parts:
        try: nums.append(int(p))
        except (ValueError, TypeError): break  # 非数字版本片段 (如 'rc1','dev') 跳过
    return tuple(nums)

def get_latest_wheel_url(pkg_name, platform_hint='win_amd64'):
    """从pypi JSON API获取最新兼容wheel的URL"""
    import requests
    s = requests.Session()
    s.trust_env = False
    resp = s.get(f'https://pypi.org/pypi/{pkg_name}/json')
    data = resp.json()
    
    # py3-none: platform-independent
    # cp38: CPython 3.8 specific
    for pattern in [f'py3-none-{platform_hint}', f'cp38-{platform_hint}', f'py3-none-any', f'cp38-none-{platform_hint}']:
        for ver in sorted(data['releases'].keys(), key=parse_ver, reverse=True):
            for f in data['releases'][ver]:
                fn = f['filename']
                if '.whl' not in fn:
                    continue
                if 'rc' in fn or 'dev' in fn or 'alpha' in fn:
                    continue
                if (pattern.endswith('any') and 'any.whl' in fn) or (pattern in fn):
                    # Check requires_python
                    req = f.get('requires_python', '>=3.7')
                    if '>=3.9' in req or '>3.8' in req or '>=3.10' in req:
                        continue
                    return ver, f['filename'], f['url']
    return None, None, None

def install_wheel(filename, url):
    """下载并安装单个wheel"""
    import requests
    # Check if already installed
    s = requests.Session()
    s.trust_env = False
    
    print(f'  Downloading {filename}...')
    dl = s.get(url)
    dl.raise_for_status()
    
    save_path = os.path.join(os.path.dirname(__file__), filename)
    with open(save_path, 'wb') as f:
        f.write(dl.content)
    print(f'  Saved: {len(dl.content)//1024}KB')
    
    py = os.path.normpath(os.path.join(
        os.path.dirname(os.path.dirname(__file__)), 
        'qlib_env', 'Scripts', 'python.exe'))
    
    r = subprocess.run(
        [py, '-X', 'utf8', '-m', 'pip', 'install', save_path, '--no-deps', '--proxy=""'],
        capture_output=True, text=True, env=os.environ, timeout=60
    )
    os.unlink(save_path)
    
    if r.returncode != 0:
        print(f'  INSTALL FAILED: {r.stderr.strip()[-200:]}')
        return False
    print(f'  Installed OK')
    return True

# Main
pkgs = [
    'scipy',
    'scikit-learn',
    'joblib',
]

base_py = os.path.normpath(os.path.join(
    os.path.dirname(os.path.dirname(__file__)), 
    'qlib_env', 'Scripts', 'python.exe'))

for pkg_name in pkgs:
    print(f'\n=== {pkg_name} ===')
    # Check if already importable
    r = subprocess.run(
        [base_py, '-X', 'utf8', '-c', f'import {pkg_name}; print("{pkg_name} OK:", getattr({pkg_name}, "__version__", ""))'],
        capture_output=True, text=True, env=os.environ, timeout=10
    )
    if r.returncode == 0 and pkg_name in r.stdout:
        print(f'  Already installed: {r.stdout.strip()}')
        continue
    
    ver, fn, url = get_latest_wheel_url(pkg_name)
    if fn:
        print(f'  Found: [{ver}] {fn}')
        if install_wheel(fn, url):
            pass
        else:
            print(f'  Failed to install {pkg_name}')
    else:
        print(f'  No compatible wheel found for {pkg_name}')
        # Try pip install with fallback
        print(f'  Trying pip install...')
        r = subprocess.run(
            [base_py, '-X', 'utf8', '-m', 'pip', 'install', pkg_name, '--no-cache-dir', '--proxy=""'],
            capture_output=True, text=True, env=os.environ, timeout=120
        )
        print(f'  RC={r.returncode}')

# Final verification
print('\n=== Final Verification ===')
for pkg in ['lightgbm', 'scipy', 'sklearn', 'joblib']:
    r = subprocess.run(
        [base_py, '-X', 'utf8', '-c', f'import {pkg}; print("{pkg}:", getattr({pkg}, "__version__", "OK"))'],
        capture_output=True, text=True, env=os.environ, timeout=10
    )
    print(f'  {r.stdout.strip() or r.stderr.strip()[:80]}')
