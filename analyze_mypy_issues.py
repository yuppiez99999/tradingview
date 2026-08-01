#!/usr/bin/env python3
"""Analyze mypy type ignore issues in daily_workflow.py"""

import os

def analyze_file(filepath: str):
    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        return

    lines = open(filepath, encoding='utf-8').readlines()
    total = len(lines)

    # Count type: ignore lines
    type_ignore_lines = []
    for i, line in enumerate(lines, 1):
        if 'type: ignore' in line:
            type_ignore_lines.append((i, line.strip()[:100]))

    print(f"\n文件: {filepath}")
    print(f"总行数: {total:,}")
    print(f"type: ignore 行数: {len(type_ignore_lines)}")
    print("\n前 20 处 type: ignore:")
    for lineno, content in type_ignore_lines[:20]:
        print(f"  Line {lineno}: {content}")
    if len(type_ignore_lines) > 20:
        print(f"  ... and {len(type_ignore_lines) - 20} more")

if __name__ == '__main__':
    daily_workflow = r"v8.3_institutional/daily_workflow.py"
    generate_daily_report = "generate_daily_report.py"
    lgb_enhanced_trainer = "lgb_enhanced_trainer.py"

    analyze_file(daily_workflow)
    analyze_file(generate_daily_report)
    analyze_file(lgb_enhanced_trainer)

    # Check mypy config
    print("\n" + "="*60)
    print("检查 mypy 配置...")
    mypy_ini = ".mypy.ini"
    setup_py = "setup.cfg"
    pyproject_toml = "pyproject.toml"

    for cfg in [myPyIni, setup_py, pyproject_toml]:
        if os.path.exists(cfg):
            print(f"找到配置文件: {cfg}")
            with open(cfg, encoding='utf-8') as f:
                print(f.read()[:500])
        else:
            print(f"未找到配置文件: {cfg}")