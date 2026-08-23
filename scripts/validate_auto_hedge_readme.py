"""README 校验脚本。"""
import os
import sys

path = "utils/auto_hedge_rebalance/README.md"
size = os.path.getsize(path)
with open(path, "r", encoding="utf-8") as f:
    lines = f.readlines()
line_count = len(lines)

in_block = False
block_lines = 0
max_block = 0
for line in lines:
    stripped = line.strip()
    if stripped.startswith("```"):
        if in_block:
            max_block = max(max_block, block_lines)
            in_block = False
            block_lines = 0
        else:
            in_block = True
    elif in_block:
        block_lines += 1

import re

has_html = False
html_pattern = re.compile(r"<\s*[a-zA-Z][^>]*>")
for line in lines:
    s = line.strip()
    if s.startswith("<!--"):
        continue
    if html_pattern.search(s):
        has_html = True
        break

has_secret = any(
    ("API_KEY=" in line or "TOKEN=" in line or "password=" in line.lower())
    and "${" not in line
    for line in lines
)

checks = [
    ("文件大小", f"{size/1024:.1f}KB", size <= 50 * 1024),
    ("总行数", str(line_count), line_count <= 600),
    ("最大代码块", f"{max_block}行", max_block <= 20),
    ("HTML标签", "有" if has_html else "无", not has_html),
    ("敏感凭证", "有" if has_secret else "无", not has_secret),
]

all_pass = True
for name, value, ok in checks:
    status = "通过" if ok else "失败"
    if not ok:
        all_pass = False
    print(f"{name}: {value} -> {status}")

print(f"\n校验结果: {'全部通过' if all_pass else '存在失败项'}")
sys.exit(0 if all_pass else 1)