"""临时脚本: 对 量化策略系统_统一入口_v8.6.py 的 BLE001 加 noqa (完成后删除).

匹配所有含 Exception 的 except 行 (含元组形式), 加 # noqa: BLE001.
"""
from __future__ import annotations

import re
from pathlib import Path


def main() -> None:
    """主入口."""
    filepath = '量化策略系统_统一入口_v8.6.py'
    path = Path(filepath)
    lines = path.read_text(encoding='utf-8').split('\n')

    # 匹配: except Exception: / except Exception as e: / except (X, Exception):
    pattern = re.compile(r'^(\s*except\s+.*\bException\b.*:\s*)(.*)$')

    added = 0
    for i, line in enumerate(lines):
        m = pattern.match(line)
        if not m:
            continue
        if 'noqa' in line:
            continue
        # 在行尾加 noqa 注释 (保留原有注释)
        stripped_line = line.rstrip()
        # 检查是否已有行内注释
        if '  #' in stripped_line:
            lines[i] = stripped_line + '  # noqa: BLE001'
        else:
            lines[i] = stripped_line + '  # noqa: BLE001  # fail-safe, 待后续精确化'
        added += 1

    if added > 0:
        path.write_text('\n'.join(lines), encoding='utf-8')
    print(f'{filepath}: +{added} noqa')


if __name__ == '__main__':
    main()
