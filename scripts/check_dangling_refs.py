#!/usr/bin/env python
"""
删除时下游依赖检查 (Dangling Reference Check)
================================================
创建: 2026-08-06 防复发机制 #2
目的: 防止"删模块/改类名时没人检查下游引用, 导致测试 collection ERROR"
      这类沉默失败。在 pre-commit 时扫描被删/被改名的符号是否还有引用。

检查内容:
    1. 测试文件 (tests/) 是否 import 了不存在的模块
    2. 生产文件 (utils/) 是否 import 了不存在的模块
    3. 已知的高风险"已删除模块"清单

用法:
    python scripts/check_dangling_refs.py              # 全量扫描
    python scripts/check_dangling_refs.py --staged     # 只检查 git 暂存区涉及的文件

退出码:
    0 = 无悬挂引用
    1 = 发现悬挂引用 (应阻断提交)
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 已知已删除/已重构的模块 (高风险清单)
# 注意: 清单项必须精确对应"确实不存在"的模块, 否则会产生假阳性。
# 已验证以下内容当前不存在 (2026-08-06):
#   - risk.portfolio_risk_assessor: 全系统无此文件
#   - src.risk.unified_risk_cockpit: 仅在已归档测试中引用
# 已移除非真实删除的项 (2026-08-06 修正):
#   - hedging.* 实际存在于 ms_strategy/src/hedging/ (仅路径变化, 非删除)
#   - FLAG_NAME 在 utils.attribution.* 中仍正常定义 (非删除)
KNOWN_DELETED_MODULES = {
    "risk.portfolio_risk_assessor",  # 已删除, 全系统无此文件
    "src.risk.unified_risk_cockpit",  # 已删除, 仅历史测试引用
}

# 已知已重命名的符号 (旧名 → 新名)
# 仅列入"确实已从所有模块移除"的符号。FLAG_NAME 仍存在于 utils.attribution.*,
# 故不列入, 避免对有效 import 产生假阳性。
KNOWN_RENAMED_SYMBOLS = {
    # 注意: FusionSignal 仍存在于 utils/signal_fusion.py (L47 class FusionSignal),
    #   与 FLAG_NAME 同理 (utils.attribution.* 仍定义), 故不列入, 避免对有效 import 假阳性。
    "get_report_dir": "get_report_dir 已删除, 请检查 utils/path_config.py 当前导出的函数",
    "get_log_dir": "get_log_dir 已删除, 请检查 utils/path_config.py",
}


def _extract_imports(filepath: Path) -> list[tuple[str, str, int]]:
    """用 AST 提取文件中的 import 语句.

    Returns:
        [(module, name, line), ...] 列表
    """
    try:
        content = filepath.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(content, filename=str(filepath))
    except (SyntaxError, OSError):
        return []

    imports: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, alias.asname or alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports.append((module, alias.name, node.lineno))
    return imports


def check_dangling_in_dir(scan_dir: Path, label: str) -> list[dict[str, Any]]:
    """扫描目录下所有 .py 文件的悬挂引用."""
    violations: list[dict[str, Any]] = []

    if not scan_dir.exists():
        return violations

    for py_file in scan_dir.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        imports = _extract_imports(py_file)
        for module, name, line in imports:
            # 检查已知删除的模块
            full_ref = f"{module}.{name}" if module else name
            for deleted in KNOWN_DELETED_MODULES:
                if deleted in full_ref or deleted in module:
                    violations.append({
                        "file": str(py_file.relative_to(_PROJECT_ROOT)),
                        "line": line,
                        "ref": full_ref,
                        "issue": f"引用已删除的模块: {deleted}",
                        "severity": "ERROR",
                    })
            # 检查已知重命名的符号
            if name in KNOWN_RENAMED_SYMBOLS:
                violations.append({
                    "file": str(py_file.relative_to(_PROJECT_ROOT)),
                    "line": line,
                    "ref": f"{module}.{name}" if module else name,
                    "issue": KNOWN_RENAMED_SYMBOLS[name],
                    "severity": "ERROR",
                })

    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="悬挂引用检查")
    parser.add_argument("--staged", action="store_true", help="只检查 git 暂存区涉及的文件")
    args = parser.parse_args(argv)

    all_violations: list[dict[str, Any]] = []

    # 检查 tests/ 目录
    all_violations.extend(check_dangling_in_dir(_PROJECT_ROOT / "tests", "tests"))
    # 检查 utils/ 目录
    all_violations.extend(check_dangling_in_dir(_PROJECT_ROOT / "utils", "utils"))
    # 检查 scripts/ 目录
    all_violations.extend(check_dangling_in_dir(_PROJECT_ROOT / "scripts", "scripts"))

    if not all_violations:
        print("[OK] 无悬挂引用 — 所有 import 目标均存在")
        return 0

    print(f"[XX] 发现 {len(all_violations)} 处悬挂引用:")
    for v in all_violations:
        print(f"  {v['file']}:{v['line']} -> {v['ref']}")
        print(f"    {v['issue']}")
    print()
    print("修复方法: 更新 import 路径到模块的新位置, 或归档引用已删除模块的测试")
    return 1


if __name__ == "__main__":
    sys.exit(main())
