"""通过Python subprocess强制pip禁用代理安装包"""
import os, sys, subprocess

# Aggressively strip ALL proxy settings
env = {}
for k, v in os.environ.items():
    if 'proxy' not in k.lower():
        env[k] = v

base_py = os.path.normpath(os.path.join(
    os.path.dirname(os.path.dirname(__file__)), 
    'qlib_env', 'Scripts', 'python.exe'))

pkgs = ['scipy']

for pkg in pkgs:
    print(f'\n=== Installing {pkg} (force no proxy) ===')
    r = subprocess.run(
        [base_py, '-X', 'utf8', '-m', 'pip', 'install', pkg, 
         '--no-cache-dir', '--proxy=""', 
         '-i', 'https://pypi.org/simple',
         '--trusted-host', 'pypi.org', '--trusted-host', 'files.pythonhosted.org'],
        capture_output=True, text=True, env=env, timeout=300
    )
    print(f'RC={r.returncode}')
    if r.stdout.strip():
        print('STDOUT:', r.stdout.strip()[-200:])
    if r.stderr.strip():
        print('STDERR:', r.stderr.strip()[-300:])

# Verify
r = subprocess.run(
    [base_py, '-X', 'utf8', '-c', 
     'import scipy.sparse; print("scipy OK"); '
     'from lightgbm import LGBMRegressor; print("LGBM OK"); '
     'import sklearn; print("sklearn OK")'],
    capture_output=True, text=True, env=env, timeout=10
)
status = 'OK' if r.returncode == 0 else 'FAIL'
print(f'\n{status}: {r.stdout.strip() or r.stderr.strip()[:200]}')
