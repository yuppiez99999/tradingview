#!/usr/bin/env python3
"""Claude Code Hook 质量检查器 (AST-based)。

本脚本服务于 Wave 0 开发工作流自动化, 既是 Claude Code PreToolUse/PostToolUse hook
的执行体, 也可独立运行检查单个文件或目录。

检查项 (按严重度):
  P0 安全风险:
    - eval() / exec() 调用 (代码注入)
    - os.system() / subprocess.run(..., shell=True) (命令注入)
    - pickle.load / pickle.loads 无 SHA256 守护 (反序列化攻击)
  P1 代码质量:
    - 裸 except: (必须指定异常类型)
    - except Exception / BaseException 既未记录也未 raise (静默吞异常)

调用模式:
  1. Claude Code Hook (从 stdin 读取 JSON):
       python scripts/_claude_hook_quality_check.py --stdin
  2. 独立检查单个文件:
       python scripts/_claude_hook_quality_check.py --file path/to/file.py
  3. 扫描目录 (默认 v8.3_institutional/src):
       python scripts/_claude_hook_quality_check.py [dir1] [dir2] ...

退出码:
  0 = 通过 (或非 .py 文件, hook 放行)
  1 = 发现 P0/P1 违规 (hook 阻止操作)
  2 = 脚本自身错误 (hook 放行, 不阻断业务)

设计参考: scripts/check_exception_policy.py (异常规范化检查器)
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

# ============================================================
# 常量定义
# ============================================================

BROAD_EXCEPT_NAMES = {"Exception", "BaseException"}

# P0 危险调用模式 (函数名 -> 风险描述)
P0_DANGEROUS_CALLS: dict[str, str] = {
    "eval": "eval() 调用存在代码注入风险 (P0)",
    "exec": "exec() 调用存在代码注入风险 (P0)",
}

# P0 os.system / subprocess shell=True 模式
P0_OS_SYSTEM = "os.system() 调用存在命令注入风险 (P0)"
P0_SHELL_TRUE = "subprocess shell=True 存在命令注入风险 (P0)"

# pickle 反序列化 (若无 SHA256 守护则报 P0)
P0_PICKLE_LOAD = "pickle.load/loads 无 SHA256 守护, 存在反序列化攻击风险 (P0)"

# 白名单: 受信源 + 已有防护的文件不报 P0
# 参考 cairn/LOG.md 2026-08-03 条目: auto_retrain_scheduler.py:517 已有三层防护
PICKLE_WHITELIST = {
    "auto_retrain_scheduler.py",
}


# ============================================================
# AST 检查器
# ============================================================


class QualityChecker(ast.NodeVisitor):
    """AST 遍历器, 收集 P0/P1 违规。

    Attributes:
        violations: 违规列表, 每项为 (lineno, severity, message)
    """

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.violations: list[tuple[int, str, str]] = []
        # 跟踪 pickle.load 是否在 try 块内有 SHA256 校验 (简化判断)
        self._has_sha256_guard = False

    def _add(self, node: ast.AST, severity: str, msg: str) -> None:
        self.violations.append((getattr(node, "lineno", 0), severity, msg))

    def visit_Call(self, node: ast.Call) -> None:
        """检查函数调用: eval/exec/os.system/pickle.load/subprocess shell=True."""
        func = node.func

        # eval() / exec() — 直接调用形式
        if isinstance(func, ast.Name) and func.id in P0_DANGEROUS_CALLS:
            self._add(node, "P0", P0_DANGEROUS_CALLS[func.id])

        # os.system() / os.popen() — 属性访问形式
        if isinstance(func, ast.Attribute):
            if func.attr in ("system", "popen"):
                # 判断是否 os 模块
                if isinstance(func.value, ast.Name) and func.value.id == "os":
                    self._add(node, "P0", P0_OS_SYSTEM)

            # pickle.load / pickle.loads
            if func.attr in ("load", "loads"):
                if isinstance(func.value, ast.Name) and func.value.id == "pickle":
                    # 白名单文件跳过 (已有 SHA256 + 大小限制 + 异常处理三层防护)
                    if Path(self.filename).name not in PICKLE_WHITELIST:
                        self._add(node, "P0", P0_PICKLE_LOAD)

            # subprocess.run/call/Popen + shell=True
            if func.attr in ("run", "call", "Popen", "check_call", "check_output"):
                for kw in node.keywords:
                    if kw.arg == "shell":
                        if isinstance(kw.value, ast.Constant) and kw.value.value is True:
                            self._add(node, "P0", P0_SHELL_TRUE)

        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        """检查 except 异常处理: 裸 except / 宽泛 Exception 静默吞."""
        # 裸 except:
        if node.type is None:
            self._add(node, "P1", "裸 except: (禁止, 必须指定异常类型)")
            self.generic_visit(node)
            return

        # 识别宽泛异常 Exception / BaseException
        is_broad = False
        if isinstance(node.type, ast.Name) and node.type.id in BROAD_EXCEPT_NAMES:
            is_broad = True
        elif isinstance(node.type, ast.Tuple):
            for elt in node.type.elts:
                if isinstance(elt, ast.Name) and elt.id in BROAD_EXCEPT_NAMES:
                    is_broad = True

        if is_broad:
            # 检查处理体是否有 raise 或 logging
            if not self._has_raise_or_logging(node):
                self._add(
                    node,
                    "P1",
                    "宽泛 except 既未记录异常也未重新抛出 (静默吞异常)",
                )

        self.generic_visit(node)

    @staticmethod
    def _has_raise_or_logging(handler: ast.ExceptHandler) -> bool:
        """检查 except 处理体是否包含 raise 或 logging 调用."""
        for child in ast.walk(handler):
            if isinstance(child, ast.Raise):
                return True
            if isinstance(child, ast.Call):
                func = child.func
                if isinstance(func, ast.Attribute):
                    if func.attr in {"error", "warning", "exception", "critical", "debug", "info"}:
                        return True
                if isinstance(func, ast.Name) and func.id == "print":
                    return True
            if isinstance(child, ast.Attribute) and child.attr in {"print_exc", "print_exception"}:
                return True
        return False


# ============================================================
# 文件检查入口
# ============================================================


def check_file(path: str) -> list[tuple[int, str, str]]:
    """检查单个 Python 文件, 返回违规列表.

    Args:
        path: 文件路径

    Returns:
        违规列表, 每项为 (lineno, severity, message); 空列表表示通过
    """
    try:
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
    except OSError:
        return []

    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return [(exc.lineno or 0, "P1", f"语法错误: {exc}")]

    checker = QualityChecker(path)
    checker.visit(tree)
    return checker.violations


def check_path(target: str) -> list[tuple[str, int, str, str]]:
    """检查文件或目录, 返回带文件名的违规列表.

    Args:
        target: 文件或目录路径

    Returns:
        违规列表, 每项为 (filepath, lineno, severity, message)
    """
    results: list[tuple[str, int, str, str]] = []
    if os.path.isfile(target):
        if target.endswith(".py"):
            for lineno, sev, msg in check_file(target):
                results.append((target, lineno, sev, msg))
    elif os.path.isdir(target):
        for root, _dirs, names in os.walk(target):
            for name in names:
                if name.endswith(".py"):
                    fpath = os.path.join(root, name)
                    for lineno, sev, msg in check_file(fpath):
                        results.append((fpath, lineno, sev, msg))
    return results


# ============================================================
# Claude Code Hook stdin 处理
# ============================================================


def handle_stdin_hook() -> int:
    """从 stdin 读取 Claude Code hook JSON, 检查目标文件.

    Claude Code PreToolUse/PostToolUse hook 输入格式:
        {
            "session_id": "...",
            "tool_name": "Edit|Write|MultiEdit",
            "tool_input": {"file_path": "/path/to/file"}
        }

    Returns:
        0 = 通过 (非 .py 文件或检查无违规)
        1 = 发现违规, 阻止操作
        2 = 脚本错误, 放行 (不阻断业务)
    """
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            # 无 stdin 输入, 视为独立模式, 放行
            return 0
        data: dict[str, Any] = json.loads(raw)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[hook] stdin 解析失败, 放行: {exc}", file=sys.stderr)
        return 2

    tool_input = data.get("tool_input", {})
    file_path = tool_input.get("file_path") or tool_input.get("path")

    if not file_path:
        # 无文件路径信息 (如 Bash 工具), 放行
        return 0

    # 非 .py 文件放行
    if not str(file_path).endswith(".py"):
        return 0

    # 文件不存在 (新建文件场景), 放行
    if not os.path.isfile(file_path):
        return 0

    violations = check_file(file_path)
    if not violations:
        return 0

    # 输出违规到 stderr (Claude Code 会展示给用户)
    print(f"\n[质量检查] {file_path} 发现 {len(violations)} 处违规:", file=sys.stderr)
    for lineno, sev, msg in violations:
        print(f"  {sev} L{lineno}: {msg}", file=sys.stderr)
    print("\n提示: 修复违规后重试, 或使用 Feature Flag 控制的新功能替代.", file=sys.stderr)
    return 1


# ============================================================
# 主入口
# ============================================================


def main(argv: list[str]) -> int:
    """主入口: 解析参数, 路由到对应模式.

    Args:
        argv: 命令行参数 (argv[0] 为脚本名)

    Returns:
        0 = 通过, 1 = 发现违规, 2 = 脚本错误
    """
    parser = argparse.ArgumentParser(
        description="Claude Code Hook 质量检查器 (P0 安全 + P1 异常规范)",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="从 stdin 读取 Claude Code hook JSON (PreToolUse/PostToolUse 模式)",
    )
    parser.add_argument("--file", type=str, help="检查单个文件")
    parser.add_argument(
        "dirs",
        nargs="*",
        help="扫描目录 (默认 v8.3_institutional/src)",
    )
    args = parser.parse_args(argv[1:])

    # 模式 1: stdin hook
    if args.stdin:
        return handle_stdin_hook()

    # 模式 2: 单文件
    if args.file:
        violations = check_file(args.file)
        if not violations:
            print(f"✓ {args.file} 通过质量检查")
            return 0
        for lineno, sev, msg in violations:
            print(f"{args.file}:{lineno}: {sev} {msg}", file=sys.stderr)
        print(f"\n发现 {len(violations)} 处违规.", file=sys.stderr)
        return 1

    # 模式 3: 目录扫描
    targets = args.dirs if args.dirs else ["v8.3_institutional/src"]
    total = 0
    p0_count = 0
    for target in targets:
        for fpath, lineno, sev, msg in check_path(target):
            print(f"{fpath}:{lineno}: {sev} {msg}")
            total += 1
            if sev == "P0":
                p0_count += 1

    if total == 0:
        print(f"✓ 质量检查通过 (扫描 {len(targets)} 个目标)")
        return 0

    print(
        f"\n发现 {total} 处违规 (P0: {p0_count}, P1: {total - p0_count})",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
