#!/usr/bin/env python3

# 读取 daily_workflow.py 的前 300 行
file_path = 'v8.3_institutional/daily_workflow.py'
with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

print(f'总行数: {len(lines)}')
print('\n--- 前 150 行 ---')
for i, line in enumerate(lines[:150], 1):
    print(f'{i:4d}: {line.rstrip()}')

print('\n--- 搜索 all "def" top-level 函数 ---')
for i, line in enumerate(lines, 1):
    stripped = line.strip()
    if stripped.startswith('def ') and not stripped.startswith('def _'):
        # 检查这个函数是否在类内部
        # 简单的启发式方法：如果上一行有 'class ' 或缩进量不为 0，则跳过
        prev_line = lines[i-2] if i > 1 else ''
        if prev_line.strip().startswith('class '):
            continue
        if i > 1 and lines[i-2].strip():  # 非空前导行可能有缩进判断
            pass
        print(f'Line {i}: {stripped[:100]}...')

print('\n--- 搜索 Class 定义 ---')
for i, line in enumerate(lines, 1):
    stripped = line.strip()
    if stripped.startswith('class '):
        print(f'Line {i}: {stripped}')
