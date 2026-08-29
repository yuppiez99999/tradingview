#!/usr/bin/env python
"""
_detect_lookahead_tests.py — 前视偏差测试检出器

扫描测试套件, 检出用未来数据断言过去的测试 (前视偏差 lookahead bias)。
复用 Honest Validation 检测逻辑, 违规测试文件路径清单返回。

检测规则:
    1. 测试中引用了 date > now() 的数据 (未来日期)
    2. 测试中用 shift(-n) (负向 shift 取未来数据)
    3. 测试中用 future / tomorrow / next_day 命名变量但断言过去行为

用法:
    python scripts/_detect_lookahead_tests.py [--test-dir tests/]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# 前视偏差模式 (正则)
_LOOKAHEAD_PATTERNS = [
    re.compile(r"\.shift\s*\(\s*-\s*\d+\s*\)"),  # df.shift(-n) 负向 shift
    re.compile(r"future\w*\s*[:=]"),  # future_xxx = ...
    re.compile(r"tomorrow\s*[:=]"),
    re.compile(r"next_day\s*[:=]"),
    re.compile(r"date\s*>\s*(?:now|today|datetime\.now)"),  # date > now()
    re.compile(r"pd\.Timestamp\.now\s*\(\s*\)\s*\+\s*pd\.DateOffset"),  # now + offset
]

# 豁免: Honest Validation 测试本身是检测前视偏差的, 不算违规
_EXEMPT_FILES = {
    "test_g7_backtest_honest_validation_boost.py",
    "test_lookahead_bias_detection.py",
}


def detect_lookahead_tests(test_dir: Path) -> list[str]:
    """扫描测试目录, 检出前视偏差测试文件.

    Args:
        test_dir: 测试目录路径 (如 tests/)

    Returns:
        违规测试文件路径清单 (相对路径)
    """
    if not test_dir.exists():
        return []

    violations: list[str] = []
    for py_file in test_dir.rglob("test_*.py"):
        if py_file.name in _EXEMPT_FILES:
            continue
        try:
            content = py_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for line_no, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for pattern in _LOOKAHEAD_PATTERNS:
                if pattern.search(line):
                    try:
                        rel_path = str(py_file.relative_to(_ROOT))
                    except ValueError:
                        rel_path = str(py_file)
                    violations.append(f"{rel_path}:{line_no}")
                    break  # 同一行只记一次
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description="前视偏差测试检出器")
    parser.add_argument("--test-dir", type=Path, default=_ROOT / "tests")
    parser.add_argument(
        "--output", type=Path, default=_ROOT / "reports" / "ci" / "lookahead_tests.json"
    )
    args = parser.parse_args()

    violations = detect_lookahead_tests(args.test_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {"violations": violations, "count": len(violations)},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    if violations:
        print(f"检出 {len(violations)} 处前视偏差测试 (ERR_COV_LOOKAHEAD_BIAS):")
        for v in violations[:10]:
            print(f"  {v}")
        if len(violations) > 10:
            print(f"  ... 共 {len(violations)} 处")
        return 1
    print("无前视偏差测试 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
