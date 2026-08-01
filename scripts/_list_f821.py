# -*- coding: utf-8 -*-
"""列出所有 F821 错误的详细信息"""
import json
import os
import subprocess
from collections import defaultdict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
GATED_LIST = Path(os.environ.get("TEMP", "/tmp")) / "gated_files.txt"

files = []
for line in GATED_LIST.read_text(encoding="utf-8").strip().splitlines():
    fp = BASE_DIR / line
    if fp.exists() and fp.suffix == ".py":
        files.append(str(fp))

cmd = ["ruff", "check", "--select", "F821", "--output-format=json"] + files
result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(BASE_DIR))
data = json.loads(result.stdout) if result.stdout else []

by_file = defaultdict(list)
for d in data:
    fp = d["filename"].replace("\\", "/")
    row = d["location"]["row"]
    msg = d.get("message", "")
    name = msg.split("`")[1] if "`" in msg else msg
    by_file[fp].append((row, name))

for fp, errs in sorted(by_file.items()):
    short = fp.replace("E:/各种PY程序/28-终极量化交易系统8.4/", "")
    names = sorted(set(n for _, n in errs))
    print(f"{short} ({len(errs)}): {', '.join(names)}")
