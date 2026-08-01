#!/usr/bin/env python3
"""Create correct phase modules by extracting and transforming from source."""

import ast
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
SOURCE_FILE = BASE_DIR / "v8.3_institutional" / "daily_workflow.py"
TARGET_DIR = BASE_DIR / "v8.3_institutional" / "daily_workflow" / "phases"

# Map method names to their target filenames in phases/
PHASE_MAPPING = {
    "phase_check": "phase_check",
    "phase_calibrate": "phase_calibrate",
    "phase_market": "phase_market",
    "phase_risk": "phase_risk",
    "phase_hedge": "phase_hedge",
    "phase_quant_neutral": "phase_quant_neutral",
    "phase_cash_management": "phase_cash_management",
    "phase_directional_futures": "phase_directional_futures",
    "phase_signal": "phase_signal",
    "phase_execute": "phase_execute",
    "phase_report": "phase_report",
    "phase_autolearn": "phase_autolearn",
    "phase_factor_kill_switch": "phase_factor_kill",
    "phase_shadow_monitor": "phase_shadow",
}


def extract_method_with_ast(filename: str, method_name: str):
    """Extract a method from a Python file using AST parsing."""
    with open(filename, encoding='utf-8') as f:
        source = f.read()

    tree = ast.parse(source, filename=filename)

    # Find DailyWorkflow class
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "DailyWorkflow":
            # Find the method within this class
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == method_name:
                    return item, source
    return None, source


def _extract_func_source_lines(func_node, source):
    """提取函数源代码行，返回 (func_source_lines, start_line_0based)"""
    lines = source.split('\n')
    start_line = func_node.lineno - 1
    original_line = lines[start_line]
    def_match = re.match(r'(\s*)def\s+' + re.escape(func_node.name) + r'\(', original_line)
    if not def_match:
        def_match = re.match(r'(\s*)' + re.escape(func_node.name) + r'\(', original_line)
    return lines, start_line, def_match


def _transform_function_signature(func_full_lines, func_node):
    """转换函数签名：self → workflow"""
    sig_match = re.match(r'(\s*)(def\s+)(' + re.escape(func_node.name) + r'\()\s*([^)]*)\s*(->[^:]*)?:(.*)', func_full_lines[0])
    if not sig_match:
        return func_full_lines

    indent = sig_match.group(1)
    def_prefix = sig_match.group(2) + sig_match.group(3)
    params_str = sig_match.group(4).strip()
    maybe_return = sig_match.group(5) or ''
    trailing = sig_match.group(6) or ''

    param_parts = [p.strip() for p in params_str.split(',')]
    new_params = []
    has_self = False
    for p in param_parts:
        if p.startswith('self'):
            has_self = True
            if ':' in p:
                var_part = p.split(':')[0].strip()
                type_part = p.split(':')[1].strip()
                new_p = f'workflow:{type_part}'
            else:
                new_p = 'workflow'
            new_params.append(new_p)
        elif p and p != 'self':
            new_params.append(p)

    if not new_params:
        new_params = ['workflow']

    new_sig = f"{indent}{def_prefix}{', '.join(new_params)}{maybe_return}:{trailing}"
    func_full_lines[0] = new_sig
    return func_full_lines


def _replace_self_references(func_full_lines):
    """替换函数体中的 self. 为 workflow."""
    for i in range(1, len(func_full_lines)):
        func_full_lines[i] = func_full_lines[i].replace('self.', 'workflow.')
    return func_full_lines


def _find_func_end_index(func_source_lines):
    """查找函数结束行索引"""
    base_indent = len(func_source_lines[0]) - len(func_source_lines[0].lstrip())
    end_idx = 1
    for i, line in enumerate(func_source_lines[1:], start=1):
        stripped = line.strip()
        if stripped == '':
            continue
        curr_indent = len(line) - len(line.lstrip())
        if curr_indent <= base_indent and (stripped.startswith('def ') or stripped.startswith('class ') or stripped.startswith('@')):
            end_idx = i
            break
    return end_idx


def transform_method_to_function(func_node: ast.FunctionDef, source: str) -> str:
    """Convert an AST method node to a standalone function string with transformed references."""
    lines, start_line, def_match = _extract_func_source_lines(func_node, source)

    if not def_match:
        return None

    # Find function end
    func_source_lines = lines[func_node.lineno-1:]
    end_idx = _find_func_end_index(func_source_lines)
    func_full_lines = func_source_lines[:end_idx]

    # Transform signature: self -> workflow
    func_full_lines = _transform_function_signature(func_full_lines, func_node)

    # Replace self. with workflow.
    func_full_lines = _replace_self_references(func_full_lines)

    return '\n'.join(func_full_lines)


def generate_module_content(method_name: str, source_code: str) -> str:
    """Generate complete module content for a phase."""
    header = f'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase implementation: {method_name}

Extracted from original DailyWorkflow class for modularization.
This module contains the standalone phase function implementing the {method_name} phase.

The function receives a DailyWorkflow instance as its first parameter ("workflow").
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..core.workflow_orchestrator import DailyWorkflow

logger = logging.getLogger(__name__)

'''
    return header


def _find_daily_workflow_class(source):
    """从 AST 中找到 DailyWorkflow 类"""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "DailyWorkflow":
            return node
    return None


def _collect_phase_methods(daily_workflow_class):
    """收集所有 phase_ 方法"""
    phase_funcs = {}
    for item in daily_workflow_class.body:
        if isinstance(item, ast.FunctionDef) and item.name.startswith('phase_'):
            phase_funcs[item.name] = item
    return phase_funcs


def _generate_init_py(phase_funcs):
    """生成 __init__.py 内容"""
    init_lines = ['# Phases package entry point.\n']
    for method_name, target_basename in PHASE_MAPPING.items():
        init_lines.append(f"from .{target_basename} import {method_name} as {method_name}_impl\n")
    init_lines.append('\n__all__ = [\n')
    for m in PHASE_MAPPING.values():
        orig = list(PHASE_MAPPING.keys())[list(PHASE_MAPPING.values()).index(m)]
        init_lines.append(f'    "{orig}_impl",\n')
    init_lines.append(']\n')
    return init_lines


def _fallback_transform(py_func_name, source):
    """备用转换：原始文本提取并简单替换"""
    lines = source.split('\n')
    start_line = 0
    for i, line in enumerate(lines):
        if line.strip().startswith(f'def {py_func_name}'):
            start_line = i
            break

    base_indent = len(lines[start_line]) - len(lines[start_line].lstrip())
    extracted_lines = []
    i = start_line
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped == '':
            extracted_lines.append(lines[i])
            i += 1
            continue
        curr_indent = len(lines[i]) - len(lines[i].lstrip())
        if curr_indent <= base_indent and (lines[i].strip().startswith('def ') and i != start_line) or \
           lines[i].strip().startswith('class ') or lines[i].strip().startswith('@'):
            break
        extracted_lines.append(lines[i])
        i += 1

    raw_transformed = '\n'.join(extracted_lines)
    sig_match = re.match(r'(\s*)(def\s+' + re.escape(py_func_name) + r'\()([^)]*)\)', raw_transformed)
    if sig_match:
        indent = sig_match.group(1)
        new_sig = f"{indent}{sig_match.group(2)}workflow){sig_match.group(3)})"
        raw_transformed = raw_transformed.replace(sig_match.group(0), new_sig, 1)
    raw_transformed = raw_transformed.replace('self.', 'workflow.')
    return raw_transformed


def _process_phase_methods(phase_funcs, source):
    """处理所有 phase 方法，返回成功数"""
    success = 0
    for py_func_name, target_file_name in PHASE_MAPPING.items():
        if py_func_name not in phase_funcs:
            logger.info(f"⚠️ 未在 DailyWorkflow 中找到 {py_func_name}, 跳过")
            continue

        func_node = phase_funcs[py_func_name]
        transformed = transform_method_to_function(func_node, source)

        if transformed is None:
            transformed = _fallback_transform(py_func_name, source)

        module_content = generate_module_content(py_func_name, source) + '\n' + transformed + '\n\n'
        target_file = TARGET_DIR / f"{target_file_name}.py"
        target_file.write_text(module_content)
        logger.info(f"✓ {py_func_name} → {target_file_name}.py ({len(transformed.split(chr(10)))}行)")
        success += 1
    return success


def main():
    logger.info("创建正确的 phase 模块...")
    TARGET_DIR.mkdir(parents=True, exist_ok=True)

    with open(SOURCE_FILE, encoding='utf-8') as f:
        source = f.read()

    daily_workflow_class = _find_daily_workflow_class(source)
    if daily_workflow_class is None:
        logger.info("错误：未找到 DailyWorkflow 类")
        return 1

    logger.info(f"在 AST 中找到 DailyWorkflow 类，包含 {len(daily_workflow_class.body)} 个节点")

    phase_funcs = _collect_phase_methods(daily_workflow_class)
    logger.info(f"找到 {len(phase_funcs)} 个 phase 方法: {list(phase_funcs.keys())}")

    init_lines = _generate_init_py(phase_funcs)
    (TARGET_DIR / "__init__.py").write_text(''.join(init_lines))
    logger.info(f"✓ 创建 {TARGET_DIR}/__init__.py")

    total = len(PHASE_MAPPING)
    success = _process_phase_methods(phase_funcs, source)

    logger.info(f"\n成功创建 {success}/{total} 个 phase 模块")

    if success < total:
        logger.info("\n警告：部分模块创建失败，请检查输出")
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
