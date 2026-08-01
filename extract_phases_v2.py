#!/usr/bin/env python3
"""自动化提取 daily_workflow.py 中的各 phase_* 方法到独立模块，自动转换引用。"""

import re
import sys
from pathlib import Path

# Configuration
BASE_DIR = Path(__file__).parent.resolve()
SOURCE_FILE = BASE_DIR / "v8.3_institutional" / "daily_workflow.py"
TARGET_DIR = BASE_DIR / "v8.3_institutional" / "daily_workflow" / "phases"

# Phase mapping - map method names to target filenames
PHASE_METHODS = {
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


def extract_method(source_path: str, method_name: str) -> tuple:
    """Extract a single method from source file. Returns (content, start_line, end_line)."""
    with open(source_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # Find the method definition line pattern
    pattern = rf'^\s*def {method_name}\('

    start_line = None
    for i, line in enumerate(lines):
        if re.search(pattern, line, re.MULTILINE):
            # Verify it's inside the DailyWorkflow class
            j = i - 1
            while j >= 0 and not lines[j].strip().startswith('class DailyWorkflow'):
                j -= 1
            if j >= 0:
                start_line = i
                break

    if start_line is None:
        raise ValueError(f"Cannot find method '{method_name}' in {source_path}")

    # Find the end of the method
    start_indent = len(lines[start_line]) - len(lines[start_line].lstrip())
    end_line = start_line + 1
    while end_line < len(lines):
        stripped = lines[end_line].strip()
        if stripped == '':
            end_line += 1
            continue
        current_indent = len(lines[end_line]) - len(lines[end_line].lstrip())
        # If next line is at or before indent level and starts with def/class/@, stop
        if current_indent <= start_indent and stripped and (stripped.startswith('def ') or stripped.startswith('class ') or stripped.startswith('@')):
            break
        end_line += 1

    content_lines = lines[start_line:end_line]
    content = ''.join(content_lines)

    return content, start_line + 1, end_line


def transform_method(method_name: str, extracted_content: str) -> str:
    """Transform extracted method into a standalone function with workflow parameter."""
    lines = extracted_content.split('\n')
    
    # Step 1: Remove leading whitespace from each line (dedent)
    dedented = []
    for line in lines:
        if line.strip() == '':
            dedented.append('')
        else:
            # Remove common leading indentation (assume first non-empty line sets the indent)
            dedented.append(line)
    
    # Step 2: Find and transform the function signature
    transformed_lines = []
    for line in dedented:
        stripped = line.strip()
        # Match: def phase_check(self, ...): or def phase_check(self) -> ret:
        sig_match = re.match(r'(^\s*)def\s+' + re.escape(method_name) + r'\(([^)]*)\)(\s*:.*$)', line)
        if sig_match:
            indent = sig_match.group(1)
            params_str = sig_match.group(2).strip()
            
            # Replace 'self' with 'workflow' in parameters
            param_parts = [p.strip() for p in params_str.split(',')]
            new_params = []
            for p in param_parts:
                if p.startswith('self'):
                    # Replace self.xxx with workflow.xxx if there's a type annotation
                    if ':' in p:
                        old_var = p.split(':')[0].strip()
                        new_param = f'workflow:{p.split(":")[1].strip()}'
                    else:
                        new_param = 'workflow'
                    new_params.append(new_param)
                elif p.strip():
                    new_params.append(p)
            
            new_sig = f"{indent}def {method_name}({', '.join(new_params)}{sig_match.group(3)}"
            transformed_lines.append(new_sig)
        else:
            transformed_lines.append(line)
    
    # Step 3: Replace all self.xxxx references with workflow.xxxxx inside the function body
    final_lines = []
    in_function_body = False
    
    for i, line in enumerate(transformed_lines):
        stripped = line.strip()
        
        # Check if we're entering a function body (next line after def with indent increase)
        if i > 0 and transformed_lines[i-1].strip().startswith('def '):
            in_function_body = True
        
        if in_function_body and stripped:
            # Replace self. with workflow. only if it's not a docstring or comment
            # Simple heuristic: don't replace inside strings (basic check)
            line = re.sub(r'\bself\.\b', 'workflow.', line)
        
        final_lines.append(line)
    
    return '\n'.join(final_lines)


def format_phase_module(method_name: str, transformed_method: str) -> str:
    """Format the transformed method into a complete standalone module."""
    module_header = f'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase implementation: {method_name}

Extracted from original DailyWorkflow class for modularization.
This module contains the standalone phase function implementing the {method_name} phase.

The function receives a DailyWorkflow instance as its first parameter.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..core.workflow_orchestrator import DailyWorkflow

logger = logging.getLogger(__name__)

'''

    return module_header + transformed_method + '\n\n'


def main():
    """Main entry point."""
    logger.info("开始提取 phase 模块...")
    logger.info(f"源文件: {SOURCE_FILE}")
    logger.info(f"目标目录: {TARGET_DIR}")
    
    # Ensure target directory exists
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    
    # Create __init__.py
    init_content = '''# Phases package entry point.

from .phase_check import phase_check as phase_check_impl
from .phase_calibrate import phase_calibrate as phase_calibrate_impl
from .phase_market import phase_market as phase_market_impl
from .phase_risk import phase_risk as phase_risk_impl
from .phase_hedge import phase_hedge as phase_hedge_impl
from .phase_signal import phase_signal as phase_signal_impl
from .phase_execute import phase_execute as phase_execute_impl
from .phase_report import phase_report as phase_report_impl
from .phase_autolearn import phase_autolearn as phase_autolearn_impl
from .phase_factor_kill import phase_factor_kill as phase_factor_kill_impl
from .phase_shadow import phase_shadow as phase_shadow_impl

__all__ = [
    'phase_check_impl', 'phase_calibrate_impl', 'phase_market_impl',
    'phase_risk_impl', 'phase_hedge_impl', 'phase_signal_impl',
    'phase_execute_impl', 'phase_report_impl', 'phase_autolearn_impl',
    'phase_factor_kill_impl', 'phase_shadow_impl'
]
'''
    (TARGET_DIR / "__init__.py").write_text(init_content)
    logger.info(f"✓ 创建 {TARGET_DIR}/__init__.py")
    
    extracted_count = 0
    for method_name, target_basename in PHASE_METHODS.items():
        try:
            content, start_line, end_line = extract_method(str(SOURCE_FILE), method_name)
            
            # Transform the method
            transformed = transform_method(method_name, content)
            
            # Format into full module
            formatted = format_phase_module(method_name, transformed)
            
            target_file = TARGET_DIR / f"{target_basename}.py"
            target_file.write_text(formatted)
            logger.info(f"✓ 提取 {method_name} ({end_line - start_line} 行) → {target_file.name}")
            extracted_count += 1
            
        except Exception as e:
            logger.info(f"✗ 提取 {method_name} 失败: {e}")
            import traceback
            traceback.print_exc()
            alt_name = method_name.replace("_", "")
            if alt_name != method_name:
                try:
                    content, start_line, end_line = extract_method(str(SOURCE_FILE), alt_name)
                    transformed = transform_method(method_name, content)
                    formatted = format_phase_module(method_name, transformed)
                    target_file = TARGET_DIR / f"{target_basename}.py"
                    target_file.write_text(formatted)
                    logger.info(f"✓ (备用) 提取 {alt_name} → {target_file.name}")
                    extracted_count += 1
                except Exception as e2:
                    logger.info(f"✗ (备用) 也失败: {e2}")
    
    logger.info(f"\n成功提取 {extracted_count}/{len(PHASE_METHODS)} 个 phase 模块")
    
    if extracted_count < len(PHASE_METHODS):
        logger.info("\n警告：部分 phase 未成功提取，请检查原始文件内容")
        return 1
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
