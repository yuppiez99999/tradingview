"""批量替换 build_plan_executor.py 中的裸 # type: ignore 为 # type: ignore[index].

策略:
    - dict 索引访问 (plan_data["xxx"], pc["start"] 等) -> [index]
    - 比较运算 (target_date > last_phase_end 等) -> [operator]
    - 其他保持 [misc]

先扫描确认所有行, 然后逐行替换.
"""
from __future__ import annotations

import re
from pathlib import Path

TARGET = Path("build_plan_executor.py")
BARE_PATTERN = re.compile(r"#\s*type:\s*ignore\s*$", re.IGNORECASE | re.MULTILINE)

# 上下文关键词 -> 错误码映射
OPERATOR_KEYWORDS = ["target_date >", "target_date <", "target_date <=",
                     "target_date >=", "target_date ==",
                     "> last_phase_end", "< last_phase_end",
                     "> phase_end", "< phase_end"]


def determine_error_code(line: str) -> str:
    """根据行内容决定错误码."""
    stripped = line.strip()
    # 比较运算
    for kw in OPERATOR_KEYWORDS:
        if kw in stripped:
            return "operator"
    # dict 索引访问
    if '["' in stripped or "['" in stripped or "[0]" in stripped or "[-1]" in stripped or "[i]" in stripped:
        return "index"
    # 默认
    return "misc"


def main() -> None:
    source = TARGET.read_text(encoding="utf-8")
    lines = source.splitlines(keepends=True)
    changed = 0

    for i, line in enumerate(lines):
        m = BARE_PATTERN.search(line)
        if not m:
            continue
        code = determine_error_code(line)
        old = m.group(0)
        new = f"# type: ignore[{code}]"
        lines[i] = line.replace(old, new)
        changed += 1
        print(f"  L{i+1}: -> [{code}]  {line.strip()[:80]}")

    TARGET.write_text("".join(lines), encoding="utf-8")
    print(f"\nTotal replaced: {changed}")


if __name__ == "__main__":
    main()
