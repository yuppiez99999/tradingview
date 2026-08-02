"""T02: 给 broker_adapters.py 的 broad-except 添加 noqa 注释.

策略: broker API 边界的 broad-except 是合理的 fail-safe 模式
(不同券商 SDK 异常类型不可预知), 保留但标记为已知技术债.
"""
from __future__ import annotations

import re
from pathlib import Path

FILE = Path(r"E:\各种PY程序\28-终极量化交易系统8.4\utils\execution\broker_adapters.py")

# 匹配 except Exception [as xxx]: 但不含 noqa 注释
# 已有 noqa 的不重复添加
PATTERN = re.compile(
    r"^(?P<indent>\s+)except\s+Exception(\s+as\s+\w+)?\s*:\s*$"
    r"(?!\s*  # noqa)",
    re.MULTILINE,
)


def add_noqa(content: str) -> tuple[str, int]:
    """给 except Exception: 行添加 # noqa: BLE001 注释.

    Returns:
        (新内容, 修改行数)
    """
    count = 0

    def replacer(match: re.Match) -> str:
        nonlocal count
        line = match.group(0)
        # 已有 noqa 则跳过
        if "# noqa" in line:
            return line
        count += 1
        # 去掉末尾换行, 添加注释
        return line.rstrip() + "  # noqa: BLE001  # broker API 边界, fail-safe"

    new_content = PATTERN.sub(replacer, content)
    return new_content, count


def main() -> None:
    text = FILE.read_text(encoding="utf-8")
    new_text, n = add_noqa(text)
    if n == 0:
        print("无需修改 (已全部标记)")
        return
    FILE.write_text(new_text, encoding="utf-8")
    print(f"已修改 {n} 处 broad-except, 文件: {FILE.name}")


if __name__ == "__main__":
    main()
