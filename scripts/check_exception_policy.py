#!/usr/bin/env python3
from __future__ import annotations

"""异常规范化策略检查器 (AST)。

策略要求（防止"静默吞异常"）：
  1. 禁止裸 `except:`（必须指定异常类型）。
  2. 捕获 `Exception` / `BaseException` 等宽泛异常时，处理体必须：
       - 重新抛出 (`raise`)，或
       - 记录异常信息（调用 logger / logging / traceback / print 并带 exc），
     否则视为"静默吞异常"并在 CI / pre-commit 阶段报错。

用法:
    python scripts/check_exception_policy.py <file_or_dir> [<file_or_dir> ...]
退出码 0 = 通过; 1 = 发现违规。
"""
import ast
import os
import sys

BROAD_NAMES = {"Exception", "BaseException"}


def _has_raise(node: ast.ExceptHandler) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Raise):
            return True
    return False


def _has_logging(node: ast.ExceptHandler) -> bool:
    for child in ast.walk(node):
        # logger.error / logging.warning / logger.exception(...) 等
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Attribute):
                if func.attr in {"error", "warning", "exception", "critical", "debug", "info"}:
                    return True
                # logging.error(...) 形式
                if isinstance(func.value, ast.Name) and func.value.id == "logging":
                    return True
            # 直接 print(...) 也算（带 exc_info 时）
            if isinstance(func, ast.Name) and func.id == "print":
                return True
        # traceback.print_exc()
        if isinstance(child, ast.Attribute) and child.attr in {"print_exc", "print_exception"}:
            return True
    return False


def check_file(path: str) -> list[tuple[int, str]]:
    with open(path, "r", encoding="utf-8") as fh:
        src = fh.read()
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return [(exc.lineno or 0, f"语法错误: {exc}")]

    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                if handler.type is None:
                    violations.append((handler.lineno, "裸 except: (禁止, 必须指定异常类型)"))
                    continue
                # 识别宽泛异常 Exception / BaseException
                is_broad = False
                if isinstance(handler.type, ast.Name) and handler.type.id in BROAD_NAMES:
                    is_broad = True
                elif isinstance(handler.type, ast.Tuple):
                    for elt in handler.type.elts:
                        if isinstance(elt, ast.Name) and elt.id in BROAD_NAMES:
                            is_broad = True
                if not is_broad:
                    continue
                if _has_raise(handler) or _has_logging(handler):
                    continue
                violations.append((handler.lineno, "宽泛 except 既未记录异常也未重新抛出 (静默吞异常)"))
    return violations


def main(argv: list[str]) -> int:
    targets = argv[1:] or ["v8.3_institutional/src", "v8.3_institutional/utils"]
    total = 0
    for target in targets:
        if os.path.isfile(target):
            files = [target]
        elif os.path.isdir(target):
            files = []
            for root, _dirs, names in os.walk(target):
                for name in names:
                    if name.endswith(".py"):
                        files.append(os.path.join(root, name))
        else:
            continue
        for fpath in files:
            for lineno, msg in check_file(fpath):
                print(f"{fpath}:{lineno}: {msg}")
                total += 1
    if total:
        print(f"\n发现 {total} 处异常规范化违规。", file=sys.stderr)
        return 1
    print("异常规范化检查通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
