"""批量修复 B007: 循环变量未使用 → 重命名为 _前缀

用法: python scripts/_fix_b007.py
读取 ruff 的 B007 JSON 输出, 逐个把未使用的循环变量加 _ 前缀。
"""
import json
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
os_sep = "\\" if sys.platform == "win32" else "/"


def get_b007_errors(files):
    """运行 ruff 获取所有 B007 错误"""
    cmd = ["ruff", "check", "--select", "B007", "--output-format=json"] + files
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(BASE_DIR))
    if result.returncode not in (0, 1):
        print(f"ruff 失败: {result.stderr[:500]}")
        return []
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return []


def fix_file(filepath, errors):
    """修复单个文件的 B007 错误

    策略: 读取行内容, 在指定列位置找到标识符, 改为 _前缀。
    同一行可能有多个 B007 (如 for a, b, c in ...), 按列倒序处理避免偏移。
    """
    with open(filepath, encoding="utf-8") as f:
        lines = f.readlines()

    # 按行分组, 同行按列倒序
    by_line = defaultdict(list)
    for e in errors:
        row = e["location"]["row"]
        col = e["location"]["column"]
        by_line[row].append(col)

    changed = False
    for row, cols in by_line.items():
        if row > len(lines):
            continue
        line = lines[row - 1]
        for col in sorted(cols, reverse=True):
            # col 是 1-based, 转为 0-based
            idx = col - 1
            if idx >= len(line):
                continue
            # 从 col 位置向前找标识符起始 (字母/下划线)
            start = idx
            while start > 0 and (line[start - 1].isalnum() or line[start - 1] == "_"):
                start -= 1
            # 从 col 位置向后找标识符结束
            end = idx
            while end < len(line) and (line[end].isalnum() or line[end] == "_"):
                end += 1
            var_name = line[start:end]
            if not var_name or var_name.startswith("_"):
                continue  # 已是 _ 前缀或空
            # 替换为 _前缀
            new_name = "_" + var_name
            line = line[:start] + new_name + line[end:]
            changed = True
        lines[row - 1] = line

    if changed:
        with open(filepath, "w", encoding="utf-8") as f:
            f.writelines(lines)
    return changed


def main():
    # 读取门禁文件列表
    gated_list = Path(os.environ.get("TEMP", "/tmp")) / "gated_files.txt"
    if not gated_list.exists():
        print("gated_files.txt 不存在, 请先生成")
        return 1

    files = []
    for line in gated_list.read_text(encoding="utf-8").strip().splitlines():
        fp = BASE_DIR / line
        if fp.exists() and fp.suffix == ".py":
            files.append(str(fp))

    print(f"门禁文件数: {len(files)}")

    errors = get_b007_errors(files)
    print(f"B007 错误数: {len(errors)}")

    # 按文件分组
    by_file = defaultdict(list)
    for e in errors:
        fp = e["filename"].replace("\\", "/")
        by_file[fp].append(e)

    fixed_count = 0
    for fp, errs in sorted(by_file.items()):
        if fix_file(fp, errs):
            fixed_count += 1

    print(f"修复文件数: {fixed_count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
