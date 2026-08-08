"""下载与 Python 3.8 兼容的 lightgbm 版本"""
import os, sys, traceback, subprocess

os.environ.pop('HTTP_PROXY', None)
os.environ.pop('HTTPS_PROXY', None)
os.environ.pop('http_proxy', None)
os.environ.pop('https_proxy', None)
os.environ['NO_PROXY'] = '*'

try:
    import requests
    s = requests.Session()
    s.trust_env = False

    resp = s.get('https://pypi.org/pypi/lightgbm/json')
    data = resp.json()
    
    # Find versions with py3-none-win_amd64 AND requires_python compatible with 3.8
    compatible = []
    for ver, files in data['releases'].items():
        # Skip pre-releases
        if 'rc' in ver or 'dev' in ver or 'alpha' in ver or 'beta' in ver:
            continue
        # Check requires_python
        requires = data['info'].get('requires_python', '') or '>=3.7'
        # Try to get per-version requires_python from the release data
        for f in files:
            if 'py3-none-win_amd64' in f.get('filename', '') and f['filename'].endswith('.whl'):
                requires = f.get('requires_python', '') or '>=3.7'
                # Check if compatible with 3.8
                # Simple check - skip if requires >=3.9 or >=3.10
                if '>=3.9' in requires or '>3.8' in requires or '>=3.10' in requires:
                    continue
                compatible.append((ver, f, requires))
                break
    
    compatible.sort(key=lambda x: tuple(int(n) for n in x[0].split('.')), reverse=True)
    
    print(f'Compatible with Python 3.8: {len(compatible)}')
    for ver, f, req in compatible[:5]:
        print(f'  v{ver} (requires {req}): {f["filename"]}')

    if not compatible:
        print('No compatible version found via requires_python. Trying known versions...')
        # LightGBM 3.3.5 was the last version known to support Python 3.7-3.10
        # Let's try the full pypi simple page
        resp2 = s.get('https://pypi.org/simple/lightgbm/')
        import re
        all_vers = re.findall(r'>lightgbm-(\d+\.\d+\.\d+)-', resp2.text)
        all_vers = sorted(set(all_vers), key=lambda v: tuple(int(n) for n in v.split('.')), reverse=True)
        # Try versions 4.3.0 downwards
        for try_ver in all_vers:
            if tuple(int(n) for n in try_ver.split('.')) <= (4, 3, 0):
                print(f'Trying v{try_ver}...')
                break
        sys.exit(1)
    
    # Download the latest compatible
    ver, f_info, req = compatible[0]
    url = f_info['url']
    fn = f_info['filename']
    print(f'\nDownloading {fn}')
    dl = s.get(url)
    dl.raise_for_status()
    whl_path = os.path.join(os.path.dirname(__file__), fn)
    with open(whl_path, 'wb') as f:
        f.write(dl.content)
    print(f'Saved: {len(dl.content)//1024}KB')

    # Install
    py = os.path.normpath(os.path.join(os.path.dirname(os.path.dirname(__file__)), 
                                        'qlib_env', 'Scripts', 'python.exe'))
    r = subprocess.run(
        [py, '-X', 'utf8', '-m', 'pip', 'install', whl_path, '--no-deps', '--proxy=""'],
        capture_output=True, text=True, env=os.environ, timeout=60
    )
    print(f'Install RC={r.returncode}')
    for line in (r.stderr + r.stdout).strip().split('\n')[-8:]:
        if line.strip():
            print(f'  {line.strip()}')
    
    os.unlink(whl_path)

    # Verify
    r2 = subprocess.run(
        [py, '-X', 'utf8', '-c', 'import lightgbm; print(f"LGB version: {lightgbm.__version__}"); ' 
                                  'from lightgbm import LGBMRegressor; print("Import OK")'],
        capture_output=True, text=True, env=os.environ, timeout=10
    )
    print(f'\n{r2.stdout.strip()}')
    if r2.stderr.strip() and 'Traceback' in r2.stderr:
        print(f'Error: {r2.stderr.strip()[:500]}')

except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:

    # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
    print(f'Error: {type(e).__name__}: {e}')
    traceback.print_exc()
