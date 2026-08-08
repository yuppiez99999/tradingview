"""Download scipy 1.10.1 cp38 win_amd64 wheel and install it."""
import os, sys, shutil, subprocess, json, tempfile, time
from urllib import request

SCIPY_VER = "1.10.1"
PY_VER = "cp38"
PLAT = "win_amd64"
KEY = f"scipy-{SCIPY_VER}-{PY_VER}-{PY_VER}-{PLAT}"  # scipy uses cp38-cp38-win_amd64

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def main():
    log("Fetching scipy release info from PyPI...")
    with request.urlopen(f"https://pypi.org/pypi/scipy/{SCIPY_VER}/json") as resp:
        data = json.loads(resp.read().decode())
    
    whl_url = None
    for u in data['urls']:
        if u['python_version'] == PY_VER and PLAT in u['filename'] and u['filename'].endswith('.whl'):
            whl_url = u['url']
            log(f"Found: {u['filename']} ({u['size']:,} bytes)")
            break
    
    if not whl_url:
        # try generic py3 wheel
        for u in data['urls']:
            if 'py3' in u['filename'] and PLAT in u['filename'] and u['filename'].endswith('.whl'):
                whl_url = u['url']
                log(f"Found (py3): {u['filename']} ({u['size']:,} bytes)")
                break
    
    if not whl_url:
        log("No matching wheel found!")
        log(f"Available: {[u['filename'] for u in data['urls']]}")
        return 1
    
    whl_name = whl_url.split('/')[-1]
    whl_path = os.path.join(tempfile.gettempdir(), whl_name)
    
    # Delete old corrupted scipy first
    venv_sp = os.path.join(os.path.dirname(sys.executable), 'Lib', 'site-packages')
    for name in os.listdir(venv_sp):
        if name.startswith('scipy') and (os.path.isdir(os.path.join(venv_sp, name)) or name.endswith('.pth')):
            full = os.path.join(venv_sp, name)
            log(f"Removing old: {full}")
            if os.path.isdir(full):
                shutil.rmtree(full, ignore_errors=True)
            else:
                os.remove(full)
    
    log(f"Downloading {whl_name} ({int(data['urls'][0]['size'])/1e6:.1f} MB)...")
    def progress(count, block_size, total_size):
        pct = min(count * block_size / total_size * 100, 100)
        sys.stdout.write(f"\r  {pct:.0f}% ({count*block_size/1e6:.1f}/{total_size/1e6:.1f} MB)")
        sys.stdout.flush()
    
    request.urlretrieve(whl_url, whl_path, progress)
    print()
    log(f"Downloaded to {whl_path}")
    
    log("Installing...")
    result = subprocess.run(
        [sys.executable, '-m', 'pip', 'install', whl_path, '--no-deps', '--force-reinstall',
         '--proxy=', '--no-cache-dir'],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        log(f"Install failed:\n{result.stderr[:2000]}")
        return 1
    log(f"Install OK: {result.stdout.strip()}")
    
    # Verify
    result2 = subprocess.run(
        [sys.executable, '-c', 'import scipy; import scipy.linalg; print(f"scipy {scipy.__version__} OK")'],
        capture_output=True, text=True
    )
    if result2.returncode != 0:
        log(f"Verify failed:\n{result2.stderr[:1000]}")
        return 1
    log("scipy verified OK!")
    
    # Cleanup
    os.remove(whl_path)
    return 0

if __name__ == '__main__':
    sys.exit(main())
