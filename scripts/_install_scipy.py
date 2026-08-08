"""安装 scipy — 从基座Python wheel"""
import os, sys, subprocess, shutil

os.environ.pop('HTTP_PROXY', None)
os.environ.pop('HTTPS_PROXY', None)
os.environ.pop('http_proxy', None)
os.environ.pop('https_proxy', None)
os.environ['NO_PROXY'] = '*'

base_py = os.path.normpath(os.path.join(
    os.path.dirname(os.path.dirname(__file__)), 
    'qlib_env', 'Scripts', 'python.exe'))

# 1. Check if scipy already works
r = subprocess.run(
    [base_py, '-X', 'utf8', '-c', 'import scipy.sparse; print("scipy OK")'],
    capture_output=True, text=True, env=os.environ, timeout=10
)
if r.returncode == 0:
    print('scipy already working!')
    sys.exit(0)

print('scipy broken, reinstalling...')

# 2. Find scipy wheel
src = r'C:\Program Files\Python38\Lib\site-packages\scipy-1.10.1-cp38-cp38-win_amd64.whl'
if not os.path.exists(src):
    # Search for any scipy wheel
    import glob
    candidates = glob.glob(r'C:\Program Files\Python38\Lib\site-packages\scipy*.whl')
    if candidates:
        src = candidates[0]
        print(f'Using: {src}')
    else:
        print('No scipy wheel found in base Python!')
        sys.exit(1)

# 3. Copy to temp (keep original filename!)
whl_name = os.path.basename(src)
dst = os.path.join(os.environ.get('TEMP', 'C:\\Temp'), whl_name)
shutil.copy2(src, dst)
print(f'Copied to {dst}')

# 4. Install
r = subprocess.run(
    [base_py, '-X', 'utf8', '-m', 'pip', 'install', dst, 
     '--no-deps', '--force-reinstall', '--proxy='],
    capture_output=True, text=True, env=os.environ, timeout=120
)
print(f'RC={r.returncode}')
if r.returncode != 0:
    print('STDERR:', r.stderr[-300:])
    # Try without proxy flag
    r2 = subprocess.run(
        [base_py, '-X', 'utf8', '-m', 'pip', 'install', dst, '--no-deps', '--force-reinstall'],
        capture_output=True, text=True, env=os.environ, timeout=120
    )
    print(f'RC2={r2.returncode}')
    print('STDERR:', r2.stderr[-300:])

# 5. Cleanup
try:
    os.unlink(dst)
except OSError:  # 临时文件清理失败 (文件占用/权限/不存在) 忽略
    pass

# 6. Verify
r = subprocess.run(
    [base_py, '-X', 'utf8', '-c', 'import scipy.sparse; print("scipy.sparse OK"); from lightgbm import LGBMRegressor; print("LGBM OK"); import sklearn; print("sklearn OK")'],
    capture_output=True, text=True, env=os.environ, timeout=10
)
print('\nVerification:')
for line in r.stdout.strip().split('\n'):
    print(f'  {line}')
if r.stderr.strip():
    stderr_lines = r.stderr.strip().split('\n')
    for line in stderr_lines[:3]:
        print(f'  ERR: {line}')
