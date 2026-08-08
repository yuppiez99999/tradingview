"""pip install scipy — 无代理+清华镜像+大超时"""
import subprocess, os, sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(BASE_DIR, 'qlib_env', 'Scripts', 'python.exe')

env = os.environ.copy()
for k in list(env.keys()):
    if 'proxy' in k.lower():
        del env[k]

pkg = 'scipy==1.10.1'
print(f'Installing {pkg} (allow 5min timeout)...')
sys.stdout.flush()

r = subprocess.run(
    [PY, '-X', 'utf8', '-m', 'pip', 'install', pkg,
     '--no-cache-dir',
     '--index-url', 'https://pypi.tuna.tsinghua.edu.cn/simple',
     '--trusted-host', 'pypi.tuna.tsinghua.edu.cn',
     '--no-build-isolation',
     '--only-binary', ':all:'],
    capture_output=False,  # show progress live
    env=env,
    timeout=300  # 5 minutes for 32MB download
)

print(f'\nRC={r.returncode}')

# Verify
r2 = subprocess.run(
    [PY, '-X', 'utf8', '-c', 
     'import scipy; print(f"scipy {scipy.__version__}"); '
     'import scipy.sparse; print("sparse OK"); '
     'from lightgbm import LGBMRegressor; print("LGBM OK")'],
    capture_output=True, text=True, env=env, timeout=10
)
print('\n'.join(l for l in r2.stdout.strip().split('\n') if l))
if r2.stderr.strip():
    print('ERRORS:', r2.stderr.strip()[:300])
