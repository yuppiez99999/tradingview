#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量修复 ruff B007 错误 (未使用循环变量 → _变量) — 文本解析版"""
import re, os, subprocess, sys

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(project_root)

# 运行 ruff 获取 B007 错误
result = subprocess.run(
    ['python', '-m', 'ruff', 'check', '.', '--select', 'B007',
     '--exclude', 'qlib_env', '--exclude', 'research/references',
     '--exclude', 'ms_strategy', '--exclude', 'qlib', '--exclude', '_archive_dead_code',
     '--no-cache'],
    capture_output=True, text=True, cwd=project_root
)

# 解析文本输出: B007 错误格式为:
# "B007 Loop control variable `var` not used within loop body"
# "   --> file:line:col"
pattern_err = re.compile(r'B007.*?`(\w+)`.*?\n\s*-->\s*(.+?):(\d+):', re.DOTALL)
matches = pattern_err.findall(result.stdout)

print(f'Found {len(matches)} B007 errors')

# 按文件分组 (matches 是 (var_name, file, line) 元组列表)
by_file = {}
for var_name, fname, line_num in matches:
    fname = fname.replace('\\', os.sep).replace('/', os.sep)
    # 处理 ruff 输出的相对路径
    if not os.path.isabs(fname):
        fname = os.path.join(project_root, fname)
    by_file.setdefault(fname, []).append((int(line_num), var_name))

total_fixed = 0
for fname, fixes in by_file.items():
    if not os.path.exists(fname):
        print(f'  SKIP (not found): {fname}')
        continue
    with open(fname, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    changed = False
    for line_num, old_var in fixes:
        idx = line_num - 1
        if idx >= len(lines):
            continue
        line = lines[idx]
        # 替换 for 循环中的变量名
        # 模式1: for var,  或 for var  或 for var:
        pat1 = r'(for\s+)' + re.escape(old_var) + r'(\s*[,:\s])'
        new_line = re.sub(pat1, r'\g<1>_' + old_var + r'\g<2>', line, count=1)
        # 模式2: , var, 或 , var: (多变量 for)
        if new_line == line:
            pat2 = r'(,\s*)' + re.escape(old_var) + r'(\s*[,:\s])'
            new_line = re.sub(pat2, r'\g<1>_' + old_var + r'\g<2>', line, count=1)
        if new_line != line:
            lines[idx] = new_line
            changed = True
            total_fixed += 1
            print(f'  Fixed: {os.path.relpath(fname, project_root)}:{line_num} {old_var} -> _{old_var}')

    if changed:
        with open(fname, 'w', encoding='utf-8') as f:
            f.writelines(lines)

print(f'Done. Fixed {total_fixed} errors in {len(by_file)} files.')
