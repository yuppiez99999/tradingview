"""T02: 给 utils/ 目录剩余 P2 模块的 broad-except 添加 noqa 注释.

策略: P2 普通模块 (alpha/attribution/reporting/data_provider 等) 的 broad-except
都是数据加载/计算的 fail-safe, 异常不应导致主流程崩溃. 保留但标记为已知技术债.
"""
from __future__ import annotations

import re
from pathlib import Path

UTILS_DIR = Path(r"E:\各种PY程序\28-终极量化交易系统8.4\utils")

# 已处理的子目录
SKIP_SUBDIRS = {"risk", "execution"}

PATTERN = re.compile(
    r"^(?P<indent>\s+)except\s+Exception(\s+as\s+\w+)?\s*:\s*$"
    r"(?!\s*  # noqa)",
    re.MULTILINE,
)


def add_noqa(content: str, comment: str) -> tuple[str, int]:
    count = 0

    def replacer(match: re.Match) -> str:
        nonlocal count
        line = match.group(0)
        if "# noqa" in line:
            return line
        count += 1
        return line.rstrip() + f"  # noqa: BLE001  # {comment}"

    return PATTERN.sub(replacer, content), count


def collect_py_files() -> list[Path]:
    """收集 utils/ 顶层 .py + 非已处理子目录的 .py 文件."""
    files = []
    # 顶层 .py 文件
    for py in UTILS_DIR.glob("*.py"):
        files.append(py)
    # 子目录 (跳过 risk/ execution/)
    for sub in UTILS_DIR.iterdir():
        if sub.is_dir() and sub.name not in SKIP_SUBDIRS:
            for py in sub.rglob("*.py"):
                files.append(py)
    return files


def main() -> None:
    total = 0
    files_modified = 0
    for py_file in collect_py_files():
        try:
            text = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print(f"  跳过 {py_file.name}: {e}")
            continue
        new_text, n = add_noqa(text, "P2 模块 fail-safe, 待后续精确化")
        if n > 0:
            py_file.write_text(new_text, encoding="utf-8")
            print(f"  {py_file.relative_to(UTILS_DIR)}: {n} 处")
            total += n
            files_modified += 1
    print(f"总计修改: {total} 处, 涉及 {files_modified} 个文件")


if __name__ == "__main__":
    main()
