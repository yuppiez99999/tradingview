#!/usr/bin/env python3
"""自动化提取 daily_workflow.py 中的各 phase_* 方法到独立模块。"""

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
    # Look for: def phase_{name}(self, ...) -> ...:
    pattern = rf'^\s*def {method_name}\('

    start_line = None
    for i, line in enumerate(lines):
        if re.search(pattern, line, re.MULTILINE):
            # Verify it's inside the DailyWorkflow class
            # Check nearby context - find the class definition above
            j = i - 1
            while j >= 0 and not lines[j].strip().startswith('class DailyWorkflow'):
                j -= 1
            if j >= 0:
                start_line = i
                break

    if start_line is None:
        raise ValueError(f"Cannot find method '{method_name}' in {source_path}")

    # Find the end of the method (next line at same indent level or file end)
    # Count indentation levels
    start_indent = len(lines[start_line]) - len(lines[start_line].lstrip())
    end_line = start_line + 1
    while end_line < len(lines):
        stripped = lines[end_line].strip()
        if stripped == '':
            end_line += 1
            continue
        current_indent = len(lines[end_line]) - len(lines[end_line].lstrip())
        # If we hit a line that's not empty and has less or equal indent than the method definition,
        # and it starts with something that could be another method/class/def, we stop
        if current_indent <= start_indent and not stripped.startswith(' ') and \
           (stripped.startswith('def ') or stripped.startswith('class ') or stripped.startswith('@')):
            break
        end_line += 1

    # Now get the content
    content_lines = lines[start_line:end_line]
    content = ''.join(content_lines)

    return content, start_line + 1, end_line


def format_phase_module(method_name: str, extracted_content: str):
    """Format the extracted method into a proper standalone phase module."""
    # Clean up: remove 'self' parameter first parameter, convert to top-level function
    # Extract the method signature line
    lines = extracted_content.split('\n')
    
    # Build module header
    module_header = f'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase implementation: {method_name}

Extracted from original DailyWorkflow class for modularization.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..core.workflow_orchestrator import DailyWorkflow

logger = logging.getLogger(__name__)
'''

    # Parse and transform the method
    new_lines = []
    in_method = False
    skip_next = False
    
    for i, line in enumerate(lines):
        stripped = line.strip()
        
        # Check if this is the method definition line
        if stripped.startswith(f'def {method_name}(self'):
            in_method = True
            # Transform: remove 'self' parameter
            # Use regex to find parameters between parentheses
            import re
            match = re.match(r'(def\s+' + method_name + r'\()(.*)\)', line)
            if match:
                params_part = match.group(2).strip()
                # Remove 'self' if present
                param_parts = [p.strip() for p in params_part.split(',')]
                param_parts = [p for p in param_parts if p != 'self']
                new_params = ', '.join(param_parts)
                new_line = f'{match.group(1)}{new_params})'
                new_lines.append(new_line)
                continue
            else:
                new_lines.append(line.replace('def ' + method_name + '(self,', f'def {method_name}('))
                continue
        
        if in_method:
            # Check if we need to dedent by one level (remove the self->indent shift)
            # The method body originally was indented relative to def, now it stays same level
            # We just keep the line as-is but ensure proper indentation for the module
            
            # Skip continuation lines that are purely continuation (...)
            if stripped.startswith('...'):
                continue
                
            new_lines.append(line)
            
            # Check if this is the end marker (blank line after method body)
            if i > 0 and lines[i-1].strip() == '' and i < len(lines) - 1:
                # Simple heuristic: if next line looks like a new definition
                next_stripped = lines[i+1].strip()
                if next_stripped.startswith('def ') or next_stripped.startswith('class '):
                    break
    
    # Ensure we close properly
    module_body = '\n'.join(new_lines)
    
    # Add docstring and return
    full_module = f"{module_header}\n\n{module_body}\n\n"
    return full_module


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
            formatted = format_phase_module(method_name, content)
            
            target_file = TARGET_DIR / f"{target_basename}.py"
            target_file.write_text(formatted)
            logger.info(f"✓ 提取 {method_name} ({end_line - start_line} 行) → {target_file.name}")
            extracted_count += 1
        except Exception as e:
            logger.info(f"✗ 提取 {method_name} 失败: {e}")
            # Try alternative naming if direct match fails
            alt_name = method_name.replace("_", "")
            if alt_name != method_name:
                try:
                    content, start_line, end_line = extract_method(str(SOURCE_FILE), alt_name)
                    formatted = format_phase_module(method_name, content)
                    target_file = TARGET_DIR / f"{target_basename}.py"
                    target_file.write_text(formatted)
                    logger.info(f"✓ (备用) 提取 {alt_name} → {target_file.name}")
                    extracted_count += 1
                except Exception as e2:
                    logger.info(f"✗ (备用) 也失败: {e2}")
    
    logger.info(f"\n成功提取 {extracted_count}/{len(PHASE_METHODS)} 个 phase 模块")
    
    if extracted_count < len(PHASE_METHODS):
        logger.info("\n警告：部分 phase 未成功提取，请检查原始文件内容并手动完成")
        return 1
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
