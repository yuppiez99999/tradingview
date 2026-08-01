#!/usr/bin/env python3
"""Analyze daily_workflow.py structure for modularization."""

import ast
import re

def analyze_file(filepath: str):
    print(f"\n{'='*70}")
    print(f"分析文件: {filepath}")
    print('='*70)
    with open(filepath, 'r', encoding='utf-8') as f:
        source = f.read()

    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError as e:
        print(f"语法错误: {e}")
        return

    # 收集所有顶级类和函数
    classes = []
    functions = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            classes.append(node.name)
        elif isinstance(node, ast.FunctionDef) and node.decorator_list == []:
            # 排除以 _ 开头的私有函数
            if not node.name.startswith('_'):
                functions.append(node.name)

    print(f"\n▶ 顶级类 ({len(classes)}):")
    for c in classes:
        print(f"  - {c}")

    print(f"\n▶ 公共函数 ({len(functions)}):")
    for i, f in enumerate(functions, 1):
        print(f"  {i:2d}. {f}")

    # 查找 main / run / execute 等入口函数
    entry_funcs = [f for f in functions if 'run' in f.lower() or 'main' in f.lower() or 'execute' in f.lower()]
    if entry_funcs:
        print(f"\n▶ 入口函数: {entry_funcs}")

    # 查找关键配置/常量
    lines = source.split('\n')
    config_like = []
    for i, line in enumerate(lines, 1):
        # 寻找大写字母定义的配置或常量（简单启发式）
        if re.match(r'^\s*[A-Z_]+[ \t]*=', line):
            # 避开注释行
            if '#' not in line[:line.find('#')] if '#' in line else True:
                config_like.append((i, line.strip()[:80]))

    if config_like:
        print(f"\n▶ 常量/配置声明 (前20处):")
        for lineno, content in config_like[:20]:
            print(f"  Line {lineno}: {content}")

    # 尝试识别主要 phases（从注释中提取）
    phase_pattern = re.compile(r'Phase\s*(\d+|[\w\s]+):\s*(.*?)(?=\n\s*\w)', re.MULTILINE | re.IGNORECASE)
    phases = phase_pattern.findall(source)
    if phases:
        print(f"\n📋 文档中识别到的 Phase:")
        for p in phases[:15]:
            print(f"  - {p[0].strip()}: {p[1].strip()}")

if __name__ == '__main__':
    analyze_file(r"v8.3_institutional/daily_workflow.py")