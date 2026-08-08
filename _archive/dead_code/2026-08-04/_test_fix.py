"""测试修复脚本逻辑。"""
from __future__ import annotations

import re
from pathlib import Path

MERGED_PATTERN = re.compile(r"(#\s*type:\s*ignore\[[^\]]+\])([ \t]{2,})(\S)")


def fix_merged_lines(text):
    """修复行合并问题，返回 (修复后的文本, 拆分次数)."""
    total_split = 0
    while True:
        m = MERGED_PATTERN.search(text)
        if not m:
            break
        line_start = text.rfind("\n", 0, m.start()) + 1
        line_prefix = text[line_start:m.start()]
        indent_match = re.match(r"^(\s*)", line_prefix)
        indent = indent_match.group(1) if indent_match else ""
        replacement = m.group(1) + "\n" + indent + m.group(3)
        text = text[:m.start()] + replacement + text[m.end():]
        total_split += 1
    return text, total_split


if __name__ == "__main__":
    p = Path("research/vibe_trading_factor_analysis/committee/factor_committee.py")
    text = p.read_text(encoding="utf-8")
    fixed, n = fix_merged_lines(text)
    print("Split count:", n)
    lines = fixed.split("\n")
    for i, line in enumerate(lines[430:450], start=431):
        print("L{}: {}".format(i, line))
