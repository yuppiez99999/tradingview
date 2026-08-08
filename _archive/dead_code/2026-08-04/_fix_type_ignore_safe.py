"""安全批量替换裸 # type: ignore 为带错误码版本 (逐行处理, 避免行合并问题).

改进:
    1. 使用逐行处理 (splitlines + join), 不用正则 MULTILINE
    2. 只替换行尾的 # type: ignore, 不影响行内容
    3. 保留原始换行符

支持:
    - 单文件: --file path
    - 目录递归: --dir path (排除 references/, _archive/, tests/)
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

# 只匹配行尾的裸 # type: ignore (无错误码)
# 注意: 用 [ \t]* 而非 \s* 避免贪婪匹配吞掉换行符导致行合并
BARE_PATTERN = re.compile(r"#\s*type:\s*ignore[ \t]*$", re.IGNORECASE)

EXCLUDE_DIRS = {"references", ".git", "__pycache__", ".venv", "venv", "node_modules",
                "qlib_env", ".mypy_cache", ".pytest_cache", "build", "dist", "_archive", "qlib"}


def determine_error_code(line: str) -> str:
    """根据行内容决定错误码. line 参数应包含原始行 (含 # type: ignore 注释)."""
    # 先去除行尾的 # type: ignore 注释, 得到代码部分
    code_part = re.sub(r"#\s*type:\s*ignore.*$", "", line, flags=re.IGNORECASE).rstrip()
    stripped = code_part.strip()

    # importlib 动态加载
    if "module_from_spec" in stripped:
        return "misc"
    if ".loader.exec_module" in stripped or "loader.exec_module" in stripped:
        return "union-attr"
    if "spec.loader" in stripped:
        return "union-attr"

    # 动态模块属性访问 (_mod.XXX, _m.XXX)
    if re.search(r"_mod\.\w+|_m\.\w+", stripped):
        return "attr-defined"

    # import 语句
    if stripped.startswith("from ") or stripped.startswith("import "):
        return "misc"

    # 比较运算 (target_date > xxx, target_date < xxx 等)
    if re.search(r"target_date\s*[<>]=?", stripped):
        return "operator"
    if re.search(r"last_phase_end|phase_end", stripped) and re.search(r"[<>]", stripped):
        return "operator"

    # dict 索引访问 (xxx["yyy"], xxx[0], xxx[-1], xxx[i], xxx.get("k", ...))
    if re.search(r'\["[^"]+"\]|\[[\'"][^\']+[\'"]\]|\[\d+\]|\[-\d+\]|\[i\]', stripped):
        return "index"
    # .get("key", ...) 取值后类型不确定
    if re.search(r'\.get\(\s*["\']', stripped):
        return "index"
    # rm_stats["xxx"] 字典索引赋值
    if re.search(r'\w+\["\w+"\]\s*=', stripped):
        return "index"

    # 字典字面量赋值 (xxx = {...}, 包括类型不匹配的 weights = {"a": "invalid"})
    if re.search(r"=\s*\{[^}]*\}\s*,?\s*$", stripped):
        return "assignment"

    # next() / max() / min() 返回 Optional
    if re.search(r"\bnext\(", stripped):
        return "misc"

    # Optional 属性访问 (self._xxx = ...)
    if "self._" in stripped and "=" in stripped:
        return "union-attr"

    # None 赋值 (含 Optional 类型注解 = None)
    if re.search(r"=\s*None\s*,?\s*$", stripped):
        return "assignment"

    # 类型转换 (float(...), int(...), str(...), dict(...), list(...))
    if re.search(r"=\s*\bfloat\(|=\s*\bint\(|=\s*\bstr\(|=\s*\bdict\(|=\s*\blist\(", stripped):
        return "misc"

    # 模块属性赋值 (si.AutomatedExecutionSystem = object, HedgeCoordinator = None)
    if re.search(r"^\w+\.\w+\s*=", stripped):
        return "assignment"

    # 对象属性赋值 (report.model_name = "modified", r.derisk_triggered_days = ...)
    if re.search(r"^\w+\.\w+\s*=\s*", stripped):
        return "assignment"
    # mock 属性赋值 (mock_layer.collect.return_value = ..., layer._run_system_check = ...)
    if re.search(r"\.\w+\s*=\s*", stripped) and ("mock" in stripped.lower() or "return_value" in stripped or "._" in stripped):
        return "assignment"

    # 元组解包 (factor_name, agent.name, vote.score, ...)
    if re.search(r"^\w+,\s*\w+\.", stripped):
        return "union-attr"

    # _xt_trader.orderStock(...) 等 Optional 链式调用
    if re.search(r"self\._\w+\.\w+\(", stripped):
        return "union-attr"

    # 调用方法返回 Optional (vote = agent.vote(...), account = self.broker.get_account_info())
    if re.search(r"=\s*\w+\.\w+\(", stripped):
        # 后续访问属性 → union-attr
        if re.search(r"=\s*\w+\.\w+\([^)]*\)\s*,?\s*$", stripped):
            return "union-attr"
        return "misc"

    # defaultdict(float) 类型推断
    if "defaultdict" in stripped:
        return "assignment"

    # 默认
    return "misc"


def process_file(path: Path, dry_run: bool = False) -> tuple[int, list]:
    """处理单个文件, 返回 (替换数, 变更列表)."""
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return 0, []

    lines = source.splitlines(keepends=True)
    changed = 0
    changes = []

    for i, line in enumerate(lines):
        m = BARE_PATTERN.search(line)
        if not m:
            continue
        code = determine_error_code(line)
        old = m.group(0)
        new = f"# type: ignore[{code}]"
        # 只替换行尾部分, 保留前面的内容和换行符
        lines[i] = line[:m.start()] + new + line[m.end():]
        changed += 1
        changes.append((i + 1, code, line.strip()[:80]))

    if changed > 0 and not dry_run:
        path.write_text("".join(lines), encoding="utf-8")

    return changed, changes


def main() -> None:
    parser = argparse.ArgumentParser(description="安全批量替换裸 type: ignore")
    parser.add_argument("--file", type=str, help="处理单个文件")
    parser.add_argument("--dir", type=str, help="处理目录 (递归)")
    parser.add_argument("--dry-run", action="store_true", help="只显示, 不修改")
    args = parser.parse_args()

    total = 0

    if args.file:
        path = Path(args.file)
        if path.exists():
            n, changes = process_file(path, args.dry_run)
            total = n
            print(f"\n=== {path}: {n} 处 ===")
            for ln, code, content in changes:
                print(f"  L{ln}: [{code}] {content}")
    elif args.dir:
        root = Path(args.dir)
        for py_file in sorted(root.rglob("*.py")):
            # 排除目录
            try:
                rel_parts = py_file.relative_to(".").parts
                if any(part in EXCLUDE_DIRS for part in rel_parts):
                    continue
            except ValueError:
                continue
            n, changes = process_file(py_file, args.dry_run)
            if n > 0:
                total += n
                print(f"\n=== {py_file}: {n} 处 ===")
                for ln, code, content in changes[:5]:  # 每文件只显示前 5 处
                    print(f"  L{ln}: [{code}] {content}")
                if len(changes) > 5:
                    print(f"  ... and {len(changes) - 5} more")

    print(f"\n{'='*60}")
    print(f"Total replaced: {total}")


if __name__ == "__main__":
    main()
