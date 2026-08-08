"""深度子类分析: SYS_PATH 真实调用 vs 字符串字面量, TYPE_IGNORE 错误码分布."""
from __future__ import annotations

import ast
import collections
import pathlib
import re

ROOT = pathlib.Path(".")
EXCLUDE_DIRS = {"references", ".git", "__pycache__", ".venv", "venv", "node_modules",
                "qlib_env", ".mypy_cache", ".pytest_cache", "build", "dist"}

SYS_PATH_PATTERN = re.compile(r"sys\.path\.(?:insert|append)\s*\(")
TYPE_IGNORE_PATTERN = re.compile(r"#\s*type:\s*ignore(?:\[([^\]]+)\])?", re.IGNORECASE)


def is_excluded(path: pathlib.Path) -> bool:
    try:
        rel_parts = path.relative_to(ROOT).parts
    except ValueError:
        return True
    for part in rel_parts:
        if part in EXCLUDE_DIRS:
            return True
    return False


def main() -> None:
    # === SYS_PATH AST 分析 (区分真实调用 vs 字符串字面量) ===
    sys_path_real = 0
    sys_path_string_literal = 0
    sys_path_real_locations: list[tuple[str, int]] = []
    sys_path_patterns: dict[str, int] = collections.Counter()  # 按插入的路径模式分类

    # === TYPE_IGNORE 错误码分析 ===
    type_ignore_with_code = collections.Counter()  # 有错误码
    type_ignore_no_code = 0  # 无错误码
    type_ignore_by_category = collections.Counter()  # 错误码大类

    files_with_sys_path = set()
    files_with_type_ignore = set()

    for py_file in pathlib.Path(".").rglob("*.py"):
        if is_excluded(py_file):
            continue
        try:
            source = py_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        rel = str(py_file)

        # AST 解析区分真实 sys.path 调用
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # 检查是否是 sys.path.insert/append
            func = node.func
            if isinstance(func, ast.Attribute):
                if (isinstance(func.value, ast.Attribute)
                    and isinstance(func.value.value, ast.Name)
                    and func.value.value.id == "sys"
                    and func.value.attr == "path"
                    and func.attr in ("insert", "append")):
                    sys_path_real += 1
                    files_with_sys_path.add(rel)
                    if len(sys_path_real_locations) < 20:
                        sys_path_real_locations.append((rel, node.lineno))
                    # 分析插入的路径模式
                    if len(node.args) >= 2:
                        arg = node.args[1] if func.attr == "insert" else node.args[0]
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            sys_path_patterns["string_literal"] += 1
                        elif isinstance(arg, ast.Name):
                            sys_path_patterns[f"var:{arg.id}"] += 1
                        elif isinstance(arg, ast.Call):
                            if isinstance(arg.func, ast.Attribute):
                                sys_path_patterns[f"call:{arg.func.attr}"] += 1
                            else:
                                sys_path_patterns["call:other"] += 1
                        elif isinstance(arg, ast.BinOp):
                            sys_path_patterns["binop:str_concat"] += 1
                        else:
                            sys_path_patterns[f"other:{type(arg).__name__}"] += 1

        # 正则匹配字符串字面量 (排除 AST 已识别的真实调用)
        for m in SYS_PATH_PATTERN.finditer(source):
            ln = m.start()
            # 检查是否在字符串字面量中 (简单启发: 该行前后有引号)
            line_start = source.rfind("\n", 0, ln) + 1
            line_end = source.find("\n", ln)
            if line_end == -1:
                line_end = len(source)
            line = source[line_start:line_end]
            # 如果该行以引号开头或包含 re.escape/re.compile 等, 可能是字符串
            stripped = line.lstrip()
            if (stripped.startswith(('"', "'"))
                or 'sys.path.insert' in source[:ln].count('"') * '"' and False  # 简化
                or re.search(r'["\'].*sys\.path\.(?:insert|append).*["\']', line)):
                # 进一步检查: 是否在 AST 已识别的行号中
                real_lines = {loc[1] for loc in sys_path_real_locations}
                current_ln = source[: ln].count("\n") + 1
                if current_ln not in real_lines:
                    sys_path_string_literal += 1

        # TYPE_IGNORE 错误码分析
        for m in TYPE_IGNORE_PATTERN.finditer(source):
            code = m.group(1)
            if code:
                type_ignore_with_code[code] += 1
                # 错误码大类
                for c in code.split(","):
                    c = c.strip()
                    if c:
                        # 取第一个词作为大类
                        category = c.split("-")[0] if "-" in c else c
                        type_ignore_by_category[category] += 1
            else:
                type_ignore_no_code += 1
            files_with_type_ignore.add(rel)

    print("=== SYS_PATH 分析 ===")
    print(f"Real sys.path calls (AST): {sys_path_real}")
    print(f"String literal matches: {sys_path_string_literal}")
    print(f"Files with real sys.path: {len(files_with_sys_path)}")
    print(f"\nTop 20 sys.path real locations:")
    for f, ln in sys_path_real_locations[:20]:
        print(f"  {f}:{ln}")
    print(f"\nSys.path argument patterns:")
    for pat, c in sys_path_patterns.most_common():
        print(f"  {pat}: {c}")

    print("\n" + "=" * 60)
    print("=== TYPE_IGNORE 分析 ===")
    total = sum(type_ignore_with_code.values()) + type_ignore_no_code
    print(f"Total: {total}")
    print(f"With error code: {sum(type_ignore_with_code.values())}")
    print(f"Without error code (bare): {type_ignore_no_code}")
    print(f"Files affected: {len(files_with_type_ignore)}")
    print(f"\nTop 20 error codes:")
    for code, c in type_ignore_with_code.most_common(20):
        print(f"  {c:3d}  {code}")
    print(f"\nError code categories (first segment):")
    for cat, c in type_ignore_by_category.most_common(15):
        print(f"  {cat}: {c}")

    # 按目录分布
    print("\n" + "=" * 60)
    print("=== 按一级目录分布 ===")
    dir_sys_path = collections.Counter()
    dir_type_ignore = collections.Counter()
    for f in files_with_sys_path:
        parts = pathlib.Path(f).parts
        top = parts[0] if parts else "(root)"
        dir_sys_path[top] += 1
    for f in files_with_type_ignore:
        parts = pathlib.Path(f).parts
        top = parts[0] if parts else "(root)"
        dir_type_ignore[top] += 1

    print("SYS_PATH by top-level dir:")
    for d, c in dir_sys_path.most_common():
        print(f"  {c:3d}  {d}")
    print("\nTYPE_IGNORE by top-level dir:")
    for d, c in dir_type_ignore.most_common():
        print(f"  {c:3d}  {d}")


if __name__ == "__main__":
    main()
