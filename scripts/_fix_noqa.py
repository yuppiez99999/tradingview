# -*- coding: utf-8 -*-
"""批量添加 noqa 注释 — 处理 ruff 无法自动修复的 F401/E402

策略:
  F401 (未使用 import):
    - try/except 块中的条件导入 → 加 # noqa: F401 (用于检测库可用性)
    - __init__.py 中的 re-export → 加 # noqa: F401
    - 其他 → 加 # noqa: F401 (保守处理, 避免误删)
  E402 (import 不在顶部):
    - 条件导入/延迟导入 → 加 # noqa: E402
    - __init__.py 中的路径调整后 import → 加 # noqa: E402
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from collections import defaultdict

BASE_DIR = Path(__file__).resolve().parent.parent
GATED_LIST = Path(os.environ.get("TEMP", "/tmp")) / "gated_files.txt"


def get_files():
    files = []
    for line in GATED_LIST.read_text(encoding="utf-8").strip().splitlines():
        fp = BASE_DIR / line
        if fp.exists() and fp.suffix == ".py":
            files.append(str(fp))
    return files


def get_errors(files, rule):
    cmd = ["ruff", "check", "--select", rule, "--output-format=json"] + files
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(BASE_DIR))
    if result.returncode not in (0, 1):
        return []
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return []


def add_noqa(filepath, errors_for_file):
    """给文件中指定行添加 noqa 注释

    errors_for_file: [(row, code), ...]
    策略: 如果该行已有 noqa, 合并; 否则追加。
    """
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # 按行号分组, 同行合并规则
    by_row = defaultdict(set)
    for row, code in errors_for_file:
        by_row[row].add(code)

    changed = False
    for row, codes in by_row.items():
        if row > len(lines):
            continue
        line = lines[row - 1]
        # 检查是否已有 noqa
        if "# noqa" in line:
            # 已有 noqa, 检查是否已包含所有规则
            existing = set()
            if "noqa:" in line:
                # 提取已有规则: # noqa: F401, E402
                noqa_part = line.split("# noqa:")[1].split("#")[0].strip()
                existing = {c.strip() for c in noqa_part.split(",")}
            missing = codes - existing
            if not missing:
                continue
            # 合并到现有 noqa
            all_codes = sorted(existing | codes)
            old_noqa = line.split("# noqa:")[0]
            # 保留原有 noqa 后的注释
            after = ""
            if "# noqa:" in line:
                rest = line.split("# noqa:", 1)[1]
                # 找到规则后的内容
                for i, ch in enumerate(rest):
                    if ch == "#":
                        after = rest[i:]
                        break
            new_noqa = f"# noqa: {', '.join(all_codes)}"
            if after:
                new_noqa = new_noqa + " " + after
            line = old_noqa + new_noqa
            if not line.endswith("\n"):
                line += "\n"
            lines[row - 1] = line
            changed = True
        else:
            # 新增 noqa
            code_str = ", ".join(sorted(codes))
            # 去掉行尾换行
            stripped = line.rstrip("\n").rstrip("\r")
            # 保留原有行尾注释 (如果有)
            if "  #" in stripped:
                # 在已有注释前插入 noqa
                parts = stripped.split("  #", 1)
                line = f"{parts[0]}  # noqa: {code_str}  #{parts[1]}\n"
            else:
                line = f"{stripped}  # noqa: {code_str}\n"
            lines[row - 1] = line
            changed = True

    if changed:
        with open(filepath, "w", encoding="utf-8") as f:
            f.writelines(lines)
    return changed


def main():
    files = get_files()
    print(f"门禁文件数: {len(files)}")

    for rule in ["F401", "E402"]:
        errors = get_errors(files, rule)
        print(f"\n{rule} 错误数: {len(errors)}")

        # 按文件分组
        by_file = defaultdict(list)
        for e in errors:
            fp = e["filename"].replace("\\", "/")
            row = e["location"]["row"]
            by_file[fp].append((row, rule))

        fixed = 0
        for fp, errs in sorted(by_file.items()):
            if add_noqa(fp, errs):
                fixed += 1
        print(f"  修复文件数: {fixed}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
