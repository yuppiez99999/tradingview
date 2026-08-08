"""W1.3a 覆盖率检查 — 使用 AST 精确解析可执行行 + trace 追踪执行行."""
import ast
import sys
import trace
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

TARGET_FILE = _PROJECT_ROOT / "utils" / "alpha" / "shadow_real_data_feeder.py"


def collect_executable_lines(source_path: Path) -> set[int]:
    """用 AST 解析源文件, 收集真正的可执行语句行号.

    排除:
        - 模块/函数/类 docstring (Expr(Str) 在 body 第一个)
        - import 语句 (但保留 from...import, 因为可能调用)
        - 纯 class/def 声明行
        - 空行和注释

    保留:
        - 赋值语句
        - 函数调用
        - return/yield/raise
        - if/for/while/try 块的 body 行
        - 表达式语句
    """
    with open(source_path, encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source, filename=str(source_path))

    executable: set[int] = set()

    def _is_docstring(node: ast.stmt) -> bool:
        """判断语句是否为 docstring."""
        return (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )

    def _walk_body(body: list[ast.stmt], is_module: bool = False) -> None:
        """遍历函数/类/模块 body, 收集可执行行."""
        for i, node in enumerate(body):
            # 跳过 docstring (body 的第一个语句若是字符串字面量)
            if i == 0 and _is_docstring(node):
                continue
            # 跳过纯 import 语句 (不视为业务逻辑)
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            # 跳过纯声明 (class/def 行本身不计, 只计 body)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # 计入 return type annotation 行 (如果有)
                if node.returns is not None:
                    executable.add(node.returns.lineno)
                _walk_body(node.body)
                continue
            if isinstance(node, ast.ClassDef):
                _walk_body(node.body)
                continue
            # if/for/while/try 等: 计入条件行 + body
            if isinstance(node, (ast.If, ast.For, ast.While, ast.Try, ast.With, ast.AsyncFor, ast.AsyncWith)):
                executable.add(node.lineno)
                # 处理所有子 body
                for field in ast.iter_child_nodes(node):
                    if isinstance(field, list):
                        _walk_body([n for n in field if isinstance(n, ast.stmt)])
                continue
            # 其他语句: 计入行号
            executable.add(node.lineno)
            # 递归处理子语句 (如赋值中的表达式)
            for child in ast.walk(node):
                if hasattr(child, "lineno") and isinstance(child, (ast.Call, ast.Attribute, ast.Subscript)):
                    executable.add(child.lineno)

    _walk_body(tree.body, is_module=True)
    return executable


def run_pytest() -> int:
    """运行 pytest (被 trace 包裹)."""
    import pytest  # noqa: PLC0415
    return pytest.main([
        "tests/unit/test_shadow_real_data_feeder.py",
        "-q",
        "--no-header",
        "-p", "no:cacheprovider",
    ])


# Step 1: 用 AST 收集可执行行
executable_lines = collect_executable_lines(TARGET_FILE)
print(f"AST 解析完成: {len(executable_lines)} 个可执行行")

# Step 2: 用 trace 追踪执行
tracer = trace.Trace(
    count=True,
    trace=False,
    countfuncs=False,
    countcallers=False,
    ignoredirs=[sys.prefix, str(Path(sys.prefix).parent)],
)

exit_code = tracer.runfunc(run_pytest)

# Step 3: 生成覆盖率报告
results = tracer.results()
print("\n" + "=" * 70)
print("  覆盖率报告 — utils.alpha.shadow_real_data_feeder")
print("=" * 70)

# 统计已执行行
counts = results.counts
executed: set[int] = set()
for (filename, lineno), count in counts.items():
    # 归一化路径比较 (Windows 路径不区分大小写)
    if "shadow_real_data_feeder" in filename and "test_" not in filename:
        if lineno in executable_lines and count > 0:
            executed.add(lineno)

# 计算覆盖率
total_executable = len(executable_lines)
executed_count = len(executed)
missing = executable_lines - executed

if total_executable > 0:
    coverage_pct = executed_count / total_executable * 100
    print(f"  目标文件: {TARGET_FILE.name}")
    print(f"  可执行行数 (AST): {total_executable}")
    print(f"  已执行行数: {executed_count}")
    print(f"  未执行行数: {total_executable - executed_count}")
    print(f"  覆盖率: {coverage_pct:.2f}%")
    print(f"  目标: 85.00%")
    if coverage_pct >= 85:
        print("  [PASS] 覆盖率达标 ✓")
    else:
        print("  [FAIL] 覆盖率不达标")
        # 列出未覆盖行号
        if missing:
            missing_sorted = sorted(missing)
            print(f"  未覆盖行号 (前 30): {missing_sorted[:30]}")
else:
    print("  [WARN] AST 解析未找到可执行行")

print(f"\n  pytest exit_code: {exit_code}")

# 退出码: 测试通过 + 覆盖率达标
if exit_code == 0 and total_executable > 0 and coverage_pct >= 85:
    sys.exit(0)
else:
    sys.exit(1)
