"""
真实除零风险精确扫描器 (AST-based, 多行上下文分析)
排除已有 if 守护、try 守护、空检查的合理代码
"""
import ast
import re
from pathlib import Path
from collections import defaultdict
from datetime import datetime

ROOT = Path(r"E:\各种PY程序\28-终极量化交易系统8.4")

SOURCE_DIRS = ["ai", "ai_decision", "data_pipeline", "lgb_trainer", "ms_strategy",
               "realtime_monitor", "reporting", "tools", "ui", "utils",
               "v8.3_institutional", "config", "configs", "githooks"]

EXCLUDE_FILE_PREFIXES = ("_",)


def is_guarded_by_if(node, func_node):
    """检查 node 是否位于 if 守护块内
    通过检查祖先节点中是否有 if-len 守护 (if xxx: ... )
    """
    parent = getattr(node, "_parent", None)
    while parent is not None and parent is not func_node:
        if isinstance(parent, ast.If):
            # 检查 test 是否是对长度/列表的非零检查
            test = parent.test
            # if x: / if not x: / if len(x) > 0: / if x is not None
            if isinstance(test, ast.BoolOp):
                return True  # 复合条件, 视为有守护
            if _is_len_guard(test):
                return True
            # 简单 if var:
            if isinstance(test, ast.Name):
                return True
            if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
                return True
            # if x is not None / if x is None (不太可靠, 不算守护)
        parent = getattr(parent, "_parent", None)
    return False


def _is_len_guard(node):
    """检查 test 是否是 len(x) > 0 / len(x) >= 1 / len(x) != 0 之类的守护"""
    # if len(x): / if x: 这些都是隐式真值守护
    if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "len":
        return True
    # if len(x) > 0
    if isinstance(node, ast.Compare):
        left = node.left
        if isinstance(left, ast.Call) and getattr(left.func, "id", "") == "len":
            return True
        # x > 0 / x >= 1
        if isinstance(left, ast.Name):
            return True
    return False


class DivZeroVisitor(ast.NodeVisitor):
    def __init__(self, filepath: Path):
        self.filepath = filepath
        self.results = []
        self.func_stack = []
        self.try_stack = []

    def _attach_parents(self, tree):
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                child._parent = node

    def visit_FunctionDef(self, node):
        self.func_stack.append(node)
        self.generic_visit(node)
        self.func_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Try(self, node):
        self.try_stack.append(node)
        self.generic_visit(node)
        self.try_stack.pop()

    def _is_in_try(self):
        return len(self.try_stack) > 0

    def visit_BinOp(self, node):
        # 检查除法: / 或 //
        if isinstance(node.op, (ast.Div, ast.FloorDiv)):
            self._check_division(node)
        self.generic_visit(node)

    def visit_AugAssign(self, node):
        if isinstance(node.op, (ast.Div, ast.FloorDiv)):
            self._check_division(node, aug=True)
        self.generic_visit(node)

    def _check_division(self, node, aug=False):
        """检查单个除法操作的除数是否安全"""
        # 标准除法: BinOp(left, Div, right) - right 是除数
        # AugAssign: target /= value - value 是除数
        divisor = node.value if aug else node.right
        if divisor is None:
            return

        # 安全的除数:
        # 1. 字面量 (非零)
        if isinstance(divisor, ast.Constant):
            if isinstance(divisor.value, (int, float)) and divisor.value != 0:
                return
        # 2. 字面量列表/字典长度: len([1,2,3]) -> 已知
        # 3. 已知非零函数调用 (math.sqrt 等)
        if isinstance(divisor, ast.Call):
            fname = self._get_call_name(divisor)
            if fname in ("math.sqrt", "math.exp", "math.log", "math.fabs",
                         "abs", "max", "min", "round"):
                return

        # 危险除数模式: len(x), sum(x), x.count, x.shape[0], len(...)
        is_len_pattern = False
        risky_pattern = ""

        if isinstance(divisor, ast.Call):
            fname = self._get_call_name(divisor)
            if fname == "len":
                is_len_pattern = True
                risky_pattern = "len(...)"
            elif fname and "count" in fname.lower():
                is_len_pattern = True
                risky_pattern = fname
        elif isinstance(divisor, ast.Attribute):
            if "count" in divisor.attr.lower():
                is_len_pattern = True
                risky_pattern = f".{divisor.attr}"
        elif isinstance(divisor, ast.Subscript):
            # arr.shape[0] / arr.shape[1] 等
            if isinstance(divisor.value, ast.Attribute) and divisor.value.attr == "shape":
                is_len_pattern = True
                risky_pattern = ".shape[?]"

        # 如果不是 len/count/shape 模式, 也可能是简单变量 - 仍然检查
        if not is_len_pattern:
            if isinstance(divisor, ast.Name):
                risky_pattern = f"变量 {divisor.id}"
                is_len_pattern = True  # 待定, 看是否有守护
            elif isinstance(divisor, ast.BinOp):
                # 复合表达式, 如 a - b - 难以判断, 标记为待检查
                risky_pattern = "复合表达式"
                is_len_pattern = True

        if not is_len_pattern:
            return

        # 检查守护
        # 1. 是否在 try 块内
        in_try = self._is_in_try()
        # 2. 是否在 if 守护内
        current_func = self.func_stack[-1] if self.func_stack else None
        guarded = False
        if current_func and is_guarded_by_if(node, current_func):
            guarded = True

        # 如果在 try 内, 视为已处理
        if in_try:
            return

        # 如果有 if 守护, 跳过
        if guarded:
            return

        # 真实风险点!
        try:
            code_line = self._get_source_line(node)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            code_line = "<无法读取>"

        self.results.append({
            "file": str(self.filepath),
            "line": node.lineno,
            "col": node.col_offset + 1,
            "code": code_line.strip()[:120],
            "pattern": risky_pattern,
        })

    def _get_call_name(self, call):
        if isinstance(call.func, ast.Name):
            return call.func.id
        if isinstance(call.func, ast.Attribute):
            parts = []
            cur = call.func
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
            return ".".join(reversed(parts))
        return ""

    def _get_source_line(self, node):
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                lines = f.readlines()
            return lines[node.lineno - 1]
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            return ""


def iter_source_files():
    for d in SOURCE_DIRS:
        dir_path = ROOT / d
        if not dir_path.exists():
            continue
        for root, dirs, files in __import__("os").walk(dir_path):
            dirs[:] = [x for x in dirs if x not in ("__pycache__", "venv", "env")]
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                if fname.startswith(EXCLUDE_FILE_PREFIXES):
                    continue
                yield Path(root) / fname
    # research (排除 references)
    research = ROOT / "research"
    if research.exists():
        for root, dirs, files in __import__("os").walk(research):
            dirs[:] = [x for x in dirs if x not in ("__pycache__", "references", "Vibe-Trading")]
            for fname in files:
                if not fname.endswith(".py") or fname.startswith(EXCLUDE_FILE_PREFIXES):
                    continue
                yield Path(root) / fname
    # scripts 根目录
    scripts = ROOT / "scripts"
    if scripts.exists():
        for fname in os.listdir(scripts):
            full = scripts / fname
            if not fname.endswith(".py") or fname.startswith("_"):
                continue
            if full.is_file():
                yield full


import os


def main():
    print("=" * 70)
    print("终极量化交易系统 v8.4 - 真实除零风险精确扫描 (AST-based)")
    print(f"扫描时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    all_results = []
    files_scanned = 0
    files_with_syntax_error = []

    for fpath in iter_source_files():
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            tree = ast.parse(content, filename=str(fpath))
        except SyntaxError:
            files_with_syntax_error.append(str(fpath))
            continue
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            continue

        files_scanned += 1
        visitor = DivZeroVisitor(fpath)
        visitor._attach_parents(tree)
        visitor.visit(tree)
        all_results.extend(visitor.results)

    print(f"\n扫描: {files_scanned} 文件")
    if files_with_syntax_error:
        print(f"跳过 {len(files_with_syntax_error)} 个语法错误文件 (前 5):")
        for f in files_with_syntax_error[:5]:
            print(f"  - {f}")

    print(f"\n真实除零风险点 (排除 if/try 守护): {len(all_results)} 处\n")

    # 按文件分组
    by_file = defaultdict(list)
    for r in all_results:
        by_file[r["file"]].append(r)

    # 输出
    print("=== 按文件分组 (Top 20) ===")
    for fpath, items in sorted(by_file.items(), key=lambda x: -len(x[1]))[:20]:
        try:
            rel = str(Path(fpath).relative_to(ROOT))
        except ValueError:
            rel = fpath
        print(f"\n[{rel}] ({len(items)} 处):")
        for r in items[:8]:
            print(f"  L{r['line']}  [{r['pattern']}]  {r['code']}")
        if len(items) > 8:
            print(f"  ... 还有 {len(items) - 8} 处")

    # 保存 JSON
    import json
    out = {
        "scan_time": datetime.now().isoformat(),
        "files_scanned": files_scanned,
        "total_risks": len(all_results),
        "by_file": {k: v for k, v in by_file.items()},
    }
    out_path = ROOT / "scripts" / "_div_zero_precise_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\nJSON 已保存: {out_path}")


if __name__ == "__main__":
    main()
