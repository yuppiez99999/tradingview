"""批量替换根目录所有 .py 文件中的裸 # type: ignore 为带错误码版本.

策略:
    - importlib 动态加载 -> [misc] / [attr-defined] / [union-attr]
    - dict 索引访问 -> [index]
    - 比较运算 -> [operator]
    - 类型赋值 -> [assignment]
    - Optional 属性访问 -> [union-attr]
    - 其他 -> [misc]

先扫描所有根目录 .py 文件的裸 type: ignore, 显示上下文, 然后批量替换.
"""
from __future__ import annotations

import re
from pathlib import Path

BARE_PATTERN = re.compile(r"#\s*type:\s*ignore\s*$", re.IGNORECASE | re.MULTILINE)

# 根目录 .py 文件 (排除 _ 开头的临时脚本)
ROOT_FILES = [f for f in Path(".").glob("*.py")
              if not f.name.startswith("_") and not f.name.startswith("test_")]


def determine_error_code(line: str, prev_lines: list[str]) -> str:
    """根据行内容和上下文决定错误码."""
    stripped = line.strip()

    # importlib 动态加载
    if "importlib" in stripped or "module_from_spec" in stripped or "exec_module" in stripped:
        if "loader" in stripped:
            return "union-attr"
        if ".exec_module" in stripped:
            return "union-attr"
        return "misc"

    # 动态模块属性访问 (_mod.XXX)
    if re.search(r"_mod\.\w+", stripped):
        return "attr-defined"

    # Optional 属性访问 (xxx.yyy 其中 xxx 可能是 None)
    if "self._" in stripped and "=" in stripped and "import" not in stripped:
        return "union-attr"

    # 比较运算
    if re.search(r"[<>]=?\s*\w", stripped) and ("target_date" in stripped or "last_phase" in stripped
                                                  or "phase_end" in stripped):
        return "operator"

    # dict 索引访问
    if re.search(r'\["[^"]+"\]|\[[\'"][^\']+[\'"]\]|\[\d+\]|\[-\d+\]|\[i\]', stripped):
        return "index"

    # 字典字面量赋值 (positions = {})
    if re.search(r"=\s*\{\s*\}", stripped):
        return "assignment"

    # next() / max() / min() 返回 Optional
    if re.search(r"\bnext\(|\bmax\(|\bmin\(", stripped):
        return "misc"

    # 类型转换
    if re.search(r"\bfloat\(|\bint\(|\bstr\(|\bdict\(|\blist\(", stripped):
        return "misc"

    # 默认
    return "misc"


def process_file(path: Path) -> int:
    """处理单个文件, 返回替换数量."""
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return 0

    lines = source.splitlines(keepends=True)
    changed = 0
    changes = []

    for i, line in enumerate(lines):
        m = BARE_PATTERN.search(line)
        if not m:
            continue
        # 获取前几行作为上下文
        prev = [lines[j].strip() for j in range(max(0, i - 3), i)]
        code = determine_error_code(line, prev)
        old = m.group(0)
        new = f"# type: ignore[{code}]"
        lines[i] = line.replace(old, new)
        changed += 1
        changes.append((i + 1, code, line.strip()[:80]))

    if changed > 0:
        path.write_text("".join(lines), encoding="utf-8")
        print(f"\n=== {path.name}: {changed} 处 ===")
        for ln, code, content in changes:
            print(f"  L{ln}: [{code}] {content}")

    return changed


def main() -> None:
    total = 0
    for f in sorted(ROOT_FILES):
        total += process_file(f)
    print(f"\n{'='*60}")
    print(f"Total replaced: {total}")


if __name__ == "__main__":
    main()
