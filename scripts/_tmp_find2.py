import re
from pathlib import Path

ROOT = Path("utils")
pat = re.compile(r"(api_key|token|password)\s*=\s*[\"']([^\"']+)[\"']")
holders = {"your_key", "ollama", "changeme", "test", "dummy", ""}
for p in ROOT.rglob("*.py"):
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except Exception:
        continue
    for i, l in enumerate(lines, 1):
        m = pat.search(l)
        if m and m.group(2) not in holders and "os.environ" not in l and "getenv" not in l and "get(" not in l:
            print(f"{p}:{i}: {l.strip()[:110]}")
print("scan done")
