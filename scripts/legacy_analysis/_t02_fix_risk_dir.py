# -*- coding: utf-8 -*-
"""T02: 给 utils/risk/ 目录的 broad-except 添加 noqa 注释.

策略: risk 模块的 broad-except 都是 pub/sub 隔离或决策回调的 fail-safe,
异常不应影响总线或其他订阅者. 保留但标记为已知技术债.
"""
from __future__ import annotations

import re
from pathlib import Path

RISK_DIR = Path(r"E:\各种PY程序\28-终极量化交易系统8.4\utils\risk")

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


def main() -> None:
    total = 0
    for py_file in RISK_DIR.glob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        new_text, n = add_noqa(text, "risk pub/sub 隔离, fail-safe")
        if n > 0:
            py_file.write_text(new_text, encoding="utf-8")
            print(f"  {py_file.name}: {n} 处")
            total += n
    print(f"总计修改: {total} 处")


if __name__ == "__main__":
    main()
