#!/usr/bin/env python3
"""Batch fix DTZ005: datetime.now() → now_bj() in utils/ files."""
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

def get_dtz005_files():
    env = {**__import__("os").environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", "DTZ005", "--output-format=json", "utils/"],
        capture_output=True, cwd=str(PROJECT_ROOT), timeout=60, env=env,
    )
    import json
    data = json.loads(result.stdout.decode("utf-8"))
    from collections import Counter
    c = Counter(d["filename"] for d in data)
    return [(Path(k), v) for k, v in c.most_common()]

def fix_file(filepath: Path) -> tuple[bool, str]:
    content = filepath.read_text(encoding="utf-8")
    original = content

    has_datetime_now = "datetime.now()" in content
    if not has_datetime_now:
        return False, "no datetime.now()"

    content = content.replace("datetime.now()", "now_bj()")

    still_uses_datetime = bool(re.search(r'\bdatetime\b', content))
    has_datetime_import = bool(re.search(r'from\s+datetime\s+import\s+.*\bdatetime\b', content))

    if not still_uses_datetime and has_datetime_import:
        content = re.sub(
            r'from\s+datetime\s+import\s+datetime\s*\n',
            'from utils.datetime_utils import now_bj\n',
            content,
        )
        content = re.sub(
            r'from\s+datetime\s+import\s+datetime,\s*',
            'from utils.datetime_utils import now_bj  # type: ignore\nfrom datetime import ',
            content,
        )
    elif still_uses_datetime and has_datetime_import:
        if "from utils.datetime_utils import now_bj" not in content:
            content = re.sub(
                r'(from\s+datetime\s+import\s+[^\n]+\n)',
                r'\1from utils.datetime_utils import now_bj\n',
                content,
                count=1,
            )
    elif not has_datetime_import:
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if line.startswith("import ") or line.startswith("from "):
                lines.insert(i, "from utils.datetime_utils import now_bj")
                break
        content = "\n".join(lines)

    if content == original:
        return False, "no change"

    filepath.write_text(content, encoding="utf-8")
    return True, "fixed"

def main():
    files = get_dtz005_files()
    print(f"Total: {len(files)} files, {sum(v for _, v in files)} violations")

    batch_size = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    start = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    end = min(start + batch_size, len(files))

    fixed = 0
    skipped = 0
    for filepath, count in files[start:end]:
        rel = filepath.relative_to(PROJECT_ROOT)
        ok, msg = fix_file(filepath)
        if ok:
            fixed += 1
            print(f"  FIXED {rel} ({count} violations)")
        else:
            skipped += 1
            print(f"  SKIP   {rel} ({msg})")

    print(f"\nBatch [{start}:{end}]: {fixed} fixed, {skipped} skipped")

    if fixed > 0:
        env = {**__import__("os").environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--select", "DTZ005", "--statistics", "utils/"],
            capture_output=True, cwd=str(PROJECT_ROOT), timeout=60, env=env,
        )
        print(f"Remaining: {result.stdout.decode('utf-8').strip()}")

if __name__ == "__main__":
    main()
