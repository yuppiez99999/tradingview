#!/usr/bin/env python3
"""Analyze daily_workflow.py structure for modularization."""

import ast
import re


def _collect_top_level_definitions(tree):
    """从 AST 收集顶级类和非私有函数"""
    classes = []
    functions = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            classes.append(node.name)
        elif isinstance(node, ast.FunctionDef) and node.decorator_list == []:
            if not node.name.startswith('_'):
                functions.append(node.name)
    return classes, functions


def _find_config_like_lines(source):
    """查找配置/常量声明（简单启发式）"""
    lines = source.split('\n')
    config_like = []
    for i, line in enumerate(lines, 1):
        if re.match(r'^\s*[A-Z_]+[ \t]*=', line):
            if '#' not in line[:line.find('#')] if '#' in line else True:
                config_like.append((i, line.strip()[:80]))
    return config_like


def _find_phases_from_comments(source):
    """从注释中提取 Phase 信息"""
    phase_pattern = re.compile(r'Phase\s*(\d+|[\w\s]+):\s*(.*?)(?=\n\s*\w)', re.MULTILINE | re.IGNORECASE)
    return phase_pattern.findall(source)


def analyze_file(filepath: str):
    print(f"\n{'='*70}")
    print(f"分析文件: {filepath}")
    print('='*70)

    with open(filepath, encoding='utf-8') as f:
        source = f.read()

    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError as e:
        print(f"语法错误: {e}")
        return

    classes, functions = _collect_top_level_definitions(tree)

    print(f"\n▶ 顶级类 ({len(classes)}):")
    for c in classes:
        print(f"  - {c}")

    print(f"\n▶ 公共函数 ({len(functions)}):")
    for i, f in enumerate(functions, 1):
        print(f"  {i:2d}. {f}")

    entry_funcs = [f for f in functions if 'run' in f.lower() or 'main' in f.lower() or 'execute' in f.lower()]
    if entry_funcs:
        print(f"\n▶ 入口函数: {entry_funcs}")

    config_like = _find_config_like_lines(source)
    if config_like:
        print("\n▶ 常量/配置声明 (前20处):")
        for lineno, content in config_like[:20]:
            print(f"  Line {lineno}: {content}")

    phases = _find_phases_from_comments(source)
    if phases:
        print("\n📋 文档中识别到的 Phase:")
        for p in phases[:15]:
            print(f"  - {p[0].strip()}: {p[1].strip()}")


if __name__ == '__main__':
    analyze_file(r"v8.3_institutional/daily_workflow.py")
