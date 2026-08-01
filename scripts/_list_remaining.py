# -*- coding: utf-8 -*-
"""列出所有剩余 ruff 错误"""
import json
import os
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
GATED_LIST = Path(os.environ.get("TEMP", "/tmp")) / "gated_files.txt"

files = []
for line in GATED_LIST.read_text(encoding="utf-8").strip().splitlines():
    fp = BASE_DIR / line
    if fp.exists() and fp.suffix == ".py":
        files.append(str(fp))

cmd = ["ruff", "check", "--output-format=json"] + files
result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(BASE_DIR))
data = json.loads(result.stdout) if result.stdout else []

for d in sorted(data, key=lambda x: (x["code"], x["filename"])):
    fp = d["filename"].replace("\\", "/").replace("E:/各种PY程序/28-终极量化交易系统8.4/", "")
    row = d["location"]["row"]
    code = d["code"]
    msg = d["message"].split("(")[0].strip()
    print(f"{code:8} {fp}:{row}  {msg}")

print(f"\n--- Total: {len(data)} errors ---")
