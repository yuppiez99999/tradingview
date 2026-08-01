# -*- coding: utf-8 -*-
"""分析 ruff 剩余错误分布"""
import json
import os
import subprocess
import sys
from pathlib import Path
from collections import Counter

BASE_DIR = Path(__file__).resolve().parent.parent
gated_list = Path(os.environ.get("TEMP", "/tmp")) / "gated_files.txt"

files = []
for line in gated_list.read_text(encoding="utf-8").strip().splitlines():
    fp = BASE_DIR / line
    if fp.exists() and fp.suffix == ".py":
        files.append(str(fp))

# 获取指定规则的错误
rule = sys.argv[1] if len(sys.argv) > 1 else None
cmd = ["ruff", "check", "--output-format=json"]
if rule:
    cmd += ["--select", rule]
cmd += files

result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(BASE_DIR))
data = json.loads(result.stdout) if result.stdout else []

if rule == "F821":
    names = Counter()
    by_file = Counter()
    for d in data:
        msg = d.get("message", "")
        if "Undefined name" in msg and "`" in msg:
            name = msg.split("`")[1]
            names[name] += 1
        by_file[d["filename"].replace("\\", "/")] += 1
    print("F821 未定义名称 Top 20:")
    for n, c in names.most_common(20):
        print(f"  {c:4}  {n}")
    print(f"--- Total: {sum(names.values())} in {len(by_file)} files ---")
    print("\n按文件:")
    for f, c in by_file.most_common(15):
        print(f"  {c:3}  {f}")
else:
    c = Counter(d["code"] for d in data)
    print(f"剩余错误总数: {sum(c.values())}")
    for k, n in c.most_common(30):
        print(f"  {n:5}  {k}")
