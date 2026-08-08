"""临时脚本: 分析 量化策略系统_统一入口_v8.6.py 结构 (完成后删除)."""
from __future__ import annotations

import re

f = '量化策略系统_统一入口_v8.6.py'
with open(f, 'r', encoding='utf-8') as fh:
    content = fh.read()

has_logging_import = 'import logging' in content
has_logger = bool(re.search(r'logger\s*=\s*logging\.getLogger', content))
print_count = len(re.findall(r'\bprint\s*\(', content))
empty_print = len(re.findall(r'\bprint\s*\(\s*\)', content))
fstring_print = len(re.findall(r"\bprint\s*\(\s*f['\"]", content))

# 多行 import 检测
lines = content.split('\n')
multi_line_imports = 0
for i, line in enumerate(lines):
    if re.match(r'^\s*from\s+\S+\s+import\s*\($', line):
        multi_line_imports += 1

print(f'文件: {f}')
print(f'  总行数: {len(lines)}')
print(f'  print count: {print_count}')
print(f'  empty print: {empty_print}')
print(f'  f-string print: {fstring_print}')
print(f'  has import logging: {has_logging_import}')
print(f'  has logger def: {has_logger}')
print(f'  multi-line imports (from x import open-paren): {multi_line_imports}')

# 前 10 个 print 样例
print('  样例 (前 10):')
for m in list(re.finditer(r'\bprint\s*\([^)]*\)', content))[:10]:
    line_num = content[:m.start()].count('\n') + 1
    print(f'    L{line_num}: {m.group()[:120]}')

# 检查是否有 logger 已定义但未使用, 或其他特殊情况
print()
print('=== 前 40 行 ===')
for i, line in enumerate(lines[:40], 1):
    print(f'  {i:3d}: {line}')
