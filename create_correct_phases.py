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
    with open(filename, 'r', encoding='utf-8') as f:
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


def transform_method_to_function(func_node: ast.FunctionDef, source: str) -> str:
    """Convert an AST method node to a standalone function string with transformed references."""
    # Get the source lines of this function
    lines = source.split('\n')
    
    # Calculate function's start and end line numbers (AST uses 0-based internally but line numbers are 1-based)
    start_line = func_node.lineno - 1  # convert to 0-based
    
    # We need to extract just the function definition and its body
    # First, get the indentation of the def statement
    original_line = lines[start_line]
    def_match = re.match(r'(\s*)def\s+' + re.escape(func_node.name) + r'\(', original_line)
    if not def_match:
        # Try to match without capturing params up to parenthesis
        def_match = re.match(r'(\s*)' + re.escape(func_node.name) + r'\(', original_line)
    
    if def_match:
        indent = def_match.group(1)
        # Remove 'self' from parameters and change to 'workflow'
        # We'll do simple text replacement after getting full source
        
        # Find where this function ends - look for next statement at same indent
        # This is complex with AST, so instead let's use a simpler approach:
        # Use ast.get_source_segment to get the function source segment
        pass
    
    # Simpler approach: use tokenize to get exact text ranges
    import tokenize
    
    try:
        tokenizer = tokenize.StringIO(source).tokenize()
        # Find the tokens for this function
        func_start = None
        func_end = None
        for tok in tokenizer:
            if tok.type == tokenize.NAME and tok.string == func_node.name and func_start is None:
                # Check if this is the right occurrence (within DailyWorkflow)
                # Hard to verify with tokenize alone, so we'll just trust it's the first one
                func_start = tok.start[0]  # line number (1-based)
                break
        
        if func_start is None:
            return None
        
        # Now find matching end - we need to track indentation depth
        # This is getting complex... let's use a different approach
        pass
    except Exception as e:
        logger.warning(f"Unexpected error in create_correct_phases.py", exc_info=True)
    
    # Fallback: use the simpler line-by-line extraction with manual dedent
    # We know the function starts at func_node.lineno
    func_source_lines = lines[func_node.lineno-1:]
    
    # Find where the function ends (next line with less or equal indent that isn't empty/continuation)
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
        # Also check if we hit a comment-only line that indicates end... skip for now
    
    func_full_lines = func_source_lines[:end_idx]
    func_text = '\n'.join(func_full_lines)
    
    # Transform: change self parameter to workflow, replace self. references
    # First, parse the function signature
    sig_match = re.match(r'(\s*)(def\s+)(' + re.escape(func_node.name) + r'\()\s*([^)]*)\s*(->[^:]*)?:(.*)', func_full_lines[0])
    if sig_match:
        indent = sig_match.group(1)
        def_prefix = sig_match.group(2) + sig_match.group(3)  # "def phase_check("
        params_str = sig_match.group(4).strip()
        maybe_return = sig_match.group(5) or ''
        trailing = sig_match.group(6) or ''
        
        # Split params, remove any 'self' parameter, replace with 'workflow'
        param_parts = [p.strip() for p in params_str.split(',')]
        new_params = []
        has_self = False
        for p in param_parts:
            if p.startswith('self'):
                has_self = True
                # If there's a type annotation like "self: Optional[DailyWorkflow] = None",
                # replace with "workflow: Optional[DailyWorkflow] = None"
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
        
        # Now replace all self.xxx references in the function body with workflow.xxx
        # But carefully: don't replace inside strings or comments if possible
        # Simple regex substitution with caution
        for i in range(1, len(func_full_lines)):
            line = func_full_lines[i]
            # Replace 'self.' with 'workflow.' but only outside of strings/comments
            # Very basic approach: do a global replace for now (safe since all these are attribute accesses)
            # Actually we should check if it's inside a string literal... too complex
            # For this refactor, all self. references are legitimate attribute accesses
            func_full_lines[i] = line.replace('self.', 'workflow.')
        
        # Also need to handle cases where 'self' appears without dot (rare but possible in some code patterns)
        # Typically not needed for this codebase
        
        return '\n'.join(func_full_lines)
    
    return None


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


def main():
    logger.info("创建正确的 phase 模块...")
    
    # Ensure target directory exists
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    
    # Read source
    with open(SOURCE_FILE, 'r', encoding='utf-8') as f:
        source = f.read()
    
    # Use AST to find methods cleanly
    tree = ast.parse(source)
    
    # Find DailyWorkflow class
    daily_workflow_class = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "DailyWorkflow":
            daily_workflow_class = node
            break
    
    if daily_workflow_class is None:
        logger.info("错误：未找到 DailyWorkflow 类")
        return 1
    
    logger.info(f"在 AST 中找到 DailyWorkflow 类，包含 {len(daily_workflow_class.body)} 个节点")
    
    # Collect all phase methods
    phase_funcs = {}
    for item in daily_workflow_class.body:
        if isinstance(item, ast.FunctionDef) and item.name.startswith('phase_'):
            phase_funcs[item.name] = item
    
    logger.info(f"找到 {len(phase_funcs)} 个 phase 方法: {list(phase_funcs.keys())}")
    
    # Create __init__.py first
    init_lines = ['# Phases package entry point.\n']
    for method_name, target_basename in PHASE_MAPPING.items():
        init_lines.append(f"from .{target_basename} import {method_name} as {method_name}_impl\n")
    init_lines.append('\n__all__ = [\n')
    for m in PHASE_MAPPING.values():
        orig = list(PHASE_MAPPING.keys())[list(PHASE_MAPPING.values()).index(m)]
        init_lines.append(f'    "{orig}_impl",\n')
    init_lines.append(']\n')
    (TARGET_DIR / "__init__.py").write_text(''.join(init_lines))
    logger.info(f"✓ 创建 {TARGET_DIR}/__init__.py")
    
    # Process each phase method
    total = len(PHASE_MAPPING)
    success = 0
    
    for py_func_name, target_file_name in PHASE_MAPPING.items():
        if py_func_name not in phase_funcs:
            logger.info(f"⚠️ 未在 DailyWorkflow 中找到 {py_func_name}, 跳过")
            continue
        
        func_node = phase_funcs[py_func_name]
        
        # Transform the function to standalone
        transformed = transform_method_to_function(func_node, source)
        
        if transformed is None:
            logger.info(f"✗ 无法转换 {py_func_name}, 尝试备用方案")
            # Fallback: extract raw text and do simple replacement
            # Find the line number
            start_line = func_node.lineno
            # Get indented lines until we find something at class level
            lines = source.split('\n')
            base_indent = len(lines[start_line-1]) - len(lines[start_line-1].lstrip())
            extracted_lines = []
            i = start_line - 1
            while i < len(lines):
                stripped = lines[i].strip()
                if stripped == '':
                    extracted_lines.append(lines[i])
                    i += 1
                    continue
                curr_indent = len(lines[i]) - len(lines[i].lstrip())
                if curr_indent <= base_indent and (lines[i].strip().startswith('def ') and i != start_line-1) or \
                   lines[i].strip().startswith('class ') or lines[i].strip().startswith('@'):
                    break
                extracted_lines.append(lines[i])
                i += 1
            
            raw_transformed = '\n'.join(extracted_lines)
            # Basic transform: change self parameter name
            sig_match = re.match(r'(\s*)(def\s+' + re.escape(py_func_name) + r'\()([^)]*)\)', raw_transformed)
            if sig_match:
                indent = sig_match.group(1)
                new_sig = f"{indent}{sig_match.group(2)}workflow){sig_match.group(3)})"
                raw_transformed = raw_transformed.replace(sig_match.group(0), new_sig, 1)
            # Replace self. with workflow.
            raw_transformed = raw_transformed.replace('self.', 'workflow.')
            transformed = raw_transformed
        
        # Generate complete module
        module_content = generate_module_content(py_func_name, source) + '\n' + transformed + '\n\n'
        
        target_file = TARGET_DIR / f"{target_file_name}.py"
        target_file.write_text(module_content)
        logger.info(f"✓ {py_func_name} → {target_file_name}.py ({len(transformed.split(chr(10)))}行)")
        success += 1
    
    logger.info(f"\n成功创建 {success}/{total} 个 phase 模块")
    
    if success < total:
        logger.info("\n警告：部分模块创建失败，请检查输出")
        return 1
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
