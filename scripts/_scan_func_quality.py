#!/usr/bin/env python
"""函数质量扫描器 (Function Quality Scanner).

实现 refactoring-standards.md §8 三轴问题阈值扫描:
    1. 函数长度 > 80 行 (扣装饰器行数)
    2. 圈复杂度 > 15 (if/for/while/except/with/assert/comprehension +1, BoolOp +len-1)
    3. 参数数 > 5 (扣除 self/cls)

严重度分类:
    Strong         — 三项都超标 (最高优先重构)
    Worth exploring — 两项超标
    Speculative    — 一项超标

目标: 全项目 Strong 函数 = 0 (2026-08-03 首次达成, 本脚本用于持续监控)

用法:
    python scripts/_scan_func_quality.py                    # 扫描全项目
    python scripts/_scan_func_quality.py utils/             # 扫描指定目录
    python scripts/_scan_func_quality.py --json             # JSON 输出
    python scripts/_scan_func_quality.py --target-dir utils/ # 显式指定目录

退出码:
    0 = 无 Strong 函数
    1 = 有 Strong 函数 (建议重构)
    2 = 扫描异常

参考:
    - cairn/refactoring-standards.md §8 三轴问题阈值
    - 创建于 2026-08-12 (W7.4.4 daily_workflow 拆分收尾)
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

# ============================================================
# 阈值常量 (与 refactoring-standards.md §8 对齐)
# ============================================================

MAX_FUNCTION_LINES = 80
MAX_CYCLOMATIC_COMPLEXITY = 15
MAX_PARAMS = 5

# 扫描排除目录 (第三方/生成/缓存)
EXCLUDE_DIRS = {
    "__pycache__", ".git", "node_modules", ".tox", ".eggs",
    "qlib", "vnpy", "site-packages", "dist", "build",
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
}


# ============================================================
# 数据类
# ============================================================


@dataclass
class FuncMetrics:
    """单个函数的三轴指标."""

    file: str
    func_name: str
    line_start: int
    line_end: int
    length: int              # 函数长度 (扣装饰器)
    cyclomatic_complexity: int  # 圈复杂度
    params_count: int        # 参数数 (扣 self/cls)
    severity: str = ""       # Strong / Worth exploring / Speculative / OK

    def to_dict(self) -> dict:
        return asdict(self)


# ============================================================
# AST 访问器: 计算三轴指标
# ============================================================


class FuncMetricsVisitor(ast.NodeVisitor):
    """AST 访问器: 遍历所有函数定义, 计算三轴指标."""

    def __init__(self, filepath: str) -> None:
        self.filepath = filepath
        self.metrics: list[FuncMetrics] = []

    def _compute_cyclomatic_complexity(self, node: ast.AST) -> int:
        """计算圈复杂度.

        规则 (refactoring-standards.md §8):
            if/for/while/except/with/assert/comprehension +1
            BoolOp +len-1
        """
        complexity = 1  # 基础复杂度

        for child in ast.walk(node):
            if isinstance(child, (ast.If, ast.For, ast.While, ast.ExceptHandler,
                                  ast.With, ast.Assert)) or isinstance(child, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                complexity += 1
            elif isinstance(child, ast.BoolOp):
                complexity += len(child.values) - 1

        return complexity

    def _count_params(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
        """计算参数数 (扣 self/cls)."""
        args = node.args
        all_args = list(args.args) + list(args.kwonlyargs) + list(args.posonlyargs)
        if args.vararg:
            all_args.append(args.vararg)
        if args.kwarg:
            all_args.append(args.kwarg)

        # 扣除 self/cls (第一个参数)
        if all_args and all_args[0].arg in ("self", "cls"):
            all_args = all_args[1:]

        return len(all_args)

    def _compute_length(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
        """计算函数长度 (扣装饰器行数)."""
        if node.decorator_list:
            # 从最后一个装饰器之后开始计算
            start_line = node.decorator_list[-1].end_lineno + 1
        else:
            start_line = node.lineno
        end_line = node.end_lineno or node.lineno
        return max(0, end_line - start_line + 1)

    def _classify_severity(self, length: int, complexity: int, params: int) -> str:
        """分类严重度."""
        overflows = 0
        if length > MAX_FUNCTION_LINES:
            overflows += 1
        if complexity > MAX_CYCLOMATIC_COMPLEXITY:
            overflows += 1
        if params > MAX_PARAMS:
            overflows += 1

        if overflows == 3:
            return "Strong"
        elif overflows == 2:
            return "Worth exploring"
        elif overflows == 1:
            return "Speculative"
        return "OK"

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """处理函数定义."""
        length = self._compute_length(node)
        complexity = self._compute_cyclomatic_complexity(node)
        params = self._count_params(node)
        severity = self._classify_severity(length, complexity, params)

        self.metrics.append(FuncMetrics(
            file=self.filepath,
            func_name=node.name,
            line_start=node.lineno,
            line_end=node.end_lineno or node.lineno,
            length=length,
            cyclomatic_complexity=complexity,
            params_count=params,
            severity=severity,
        ))

        # 递归访问函数体内的嵌套函数
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)


# ============================================================
# 扫描器
# ============================================================


def scan_file(filepath: Path) -> list[FuncMetrics]:
    """扫描单个 Python 文件, 返回函数指标列表."""
    try:
        source = filepath.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(filepath))
    except (SyntaxError, ValueError, OSError):
        return []

    visitor = FuncMetricsVisitor(str(filepath))
    visitor.visit(tree)
    return visitor.metrics


def scan_directory(
    target_dir: Path,
    exclude_dirs: set[str] | None = None,
) -> list[FuncMetrics]:
    """扫描目录下所有 Python 文件."""
    excludes = exclude_dirs or EXCLUDE_DIRS
    all_metrics: list[FuncMetrics] = []

    for py_file in target_dir.rglob("*.py"):
        # 排除目录检查
        if any(part in excludes for part in py_file.parts):
            continue
        all_metrics.extend(scan_file(py_file))

    return all_metrics


def scan_project(root: Path) -> list[FuncMetrics]:
    """扫描项目根目录下的关键目录 (utils/, v8.3_institutional/, ai_decision/, scripts/)."""
    key_dirs = ["utils", "v8.3_institutional", "ai_decision", "scripts", "tests"]
    all_metrics: list[FuncMetrics] = []

    for dir_name in key_dirs:
        target = root / dir_name
        if target.is_dir():
            all_metrics.extend(scan_directory(target))

    return all_metrics


# ============================================================
# 报告生成
# ============================================================


def generate_report(metrics: list[FuncMetrics]) -> dict:
    """生成汇总报告."""
    total = len(metrics)
    strong = [m for m in metrics if m.severity == "Strong"]
    worth = [m for m in metrics if m.severity == "Worth exploring"]
    speculative = [m for m in metrics if m.severity == "Speculative"]
    ok = [m for m in metrics if m.severity == "OK"]

    return {
        "total_functions": total,
        "strong_count": len(strong),
        "worth_exploring_count": len(worth),
        "speculative_count": len(speculative),
        "ok_count": len(ok),
        "strong_functions": [m.to_dict() for m in strong],
        "worth_exploring_functions": [m.to_dict() for m in worth],
        "thresholds": {
            "max_function_lines": MAX_FUNCTION_LINES,
            "max_cyclomatic_complexity": MAX_CYCLOMATIC_COMPLEXITY,
            "max_params": MAX_PARAMS,
        },
    }


def print_text_report(report: dict) -> None:
    """打印文本格式报告."""
    print("=" * 60)
    print("函数质量扫描报告 (Function Quality Scan)")
    print("=" * 60)
    print(f"阈值: 长度>{MAX_FUNCTION_LINES}行 / 圈复杂度>{MAX_CYCLOMATIC_COMPLEXITY} / 参数>{MAX_PARAMS}")
    print(f"总函数数: {report['total_functions']}")
    print(f"  Strong (三项超标):          {report['strong_count']}")
    print(f"  Worth exploring (两项超标): {report['worth_exploring_count']}")
    print(f"  Speculative (一项超标):     {report['speculative_count']}")
    print(f"  OK (全达标):                {report['ok_count']}")
    print("-" * 60)

    if report["strong_functions"]:
        print("\n[Strong 函数清单] (最高优先重构)")
        for m in report["strong_functions"]:
            print(f"  {m['file']}:{m['line_start']}-{m['line_end']} {m['func_name']}"
                  f" (长度={m['length']} / CC={m['cyclomatic_complexity']} / 参数={m['params_count']})")

    if report["worth_exploring_functions"]:
        print(f"\n[Worth exploring 函数清单] (共 {len(report['worth_exploring_functions'])} 个, 仅显示前 10)")
        for m in report["worth_exploring_functions"][:10]:
            print(f"  {m['file']}:{m['line_start']} {m['func_name']}"
                  f" (长度={m['length']} / CC={m['cyclomatic_complexity']} / 参数={m['params_count']})")

    print("\n" + "=" * 60)
    if report["strong_count"] == 0:
        print("✓ 无 Strong 函数 (目标达成)")
    else:
        print(f"✗ 有 {report['strong_count']} 个 Strong 函数, 建议重构")


# ============================================================
# 主入口
# ============================================================


def main() -> int:
    parser = argparse.ArgumentParser(description="函数质量扫描器 (三轴阈值)")
    parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help="扫描目标 (文件/目录, 默认扫描项目关键目录)",
    )
    parser.add_argument(
        "--target-dir",
        default=None,
        help="显式指定扫描目录",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="JSON 格式输出",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent

    # 确定扫描目标
    if args.target_dir:
        target = Path(args.target_dir)
        if not target.is_absolute():
            target = project_root / target
    elif args.target:
        target = Path(args.target)
        if not target.is_absolute():
            target = project_root / target
    else:
        target = project_root

    # 扫描
    if target.is_file():
        metrics = scan_file(target)
    elif target.is_dir():
        # 如果是项目根目录, 只扫描关键目录
        if target == project_root:
            metrics = scan_project(project_root)
        else:
            metrics = scan_directory(target)
    else:
        print(f"错误: 目标不存在: {target}", file=sys.stderr)
        return 2

    # 生成报告
    report = generate_report(metrics)

    # 输出
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_text_report(report)

    # 退出码: 有 Strong 函数则返回 1
    return 1 if report["strong_count"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
