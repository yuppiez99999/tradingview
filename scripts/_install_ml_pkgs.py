"""离线安装ML包到 venv site-packages"""
import subprocess, sys, os

env = os.environ.copy()
env.pop('HTTP_PROXY', None)
env.pop('HTTPS_PROXY', None)
env.pop('http_proxy', None)
env.pop('https_proxy', None)
env['NO_PROXY'] = '*'
env['PYTHONIOENCODING'] = 'utf-8'

py = r'e:\各种PY程序\28-终极量化交易系统8.4\qlib_env\Scripts\python.exe'
pkgs = ['lightgbm', 'scikit-learn', 'joblib']

for pkg in pkgs:
    print(f'\n=== installing {pkg} ===')
    result = subprocess.run(
        [py, '-X', 'utf8', '-m', 'pip', 'install', pkg,
         '--no-cache-dir', '--proxy=""',
         '-i', 'https://pypi.tuna.tsinghua.edu.cn/simple',
         '--trusted-host', 'pypi.tuna.tsinghua.edu.cn'],
        capture_output=True, text=True, env=env, timeout=120
    )
    print('STDOUT:', result.stdout[-500:] if result.stdout else '(empty)')
    print('STDERR:', result.stderr[-500:] if result.stderr else '(empty)')
    print('RC:', result.returncode)
