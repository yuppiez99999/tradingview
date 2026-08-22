#!/usr/bin/env python
"""
_detect_mock_inflation.py — mock 虚增覆盖率检出器

检出用 mock 替代真实链路虚增 line_rate 的测试。
mock 替代 risk / execution / pipeline 核心链路的测试标记为违规 (边缘 IO mock 不计)。

检测规则:
    1. 测试中 mock.patch 了 risk / execution / pipeline 核心模块的方法
    2. 但测试断言的是被 mock 的行为 (而非真实链路)
    3. 边缘 IO mock (requests / urllib / 文件读写) 不计

用法:
    python scripts/_detect_mock_inflation.py [--test-dir tests/]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# 核心链路模块 (mock 这些会虚增覆盖率)
_CORE_LINK_MODULES = [
    "utils.risk",
    "utils.execution",
    "utils.pipeline",
    "institutional_pipeline_runner",
]

# 边缘 IO mock (不计入违规)
_IO_MOCK_PATTERNS = [
    re.compile(r"(?:mock\.)?patch\s*\(\s*['\"](?:requests|urllib|httpx|aiohttp)", re.IGNORECASE),
    re.compile(r"(?:mock\.)?patch\s*\(\s*['\"](?:open|pathlib|shutil|os\.path)", re.IGNORECASE),
    re.compile(r"(?:mock\.)?patch\s*\(\s*['\"].*\.(?:read|write|load|save|dump)", re.IGNORECASE),
]

# 核心 mock 模式 (匹配 mock.patch 或直接 patch after import)
_MOCK_PATCH_RE = re.compile(r"(?:mock\.)?patch\s*\(\s*['\"]([^'\"]+)['\"]")


def detect_mock_inflation(test_dir: Path) -> list[str]:
    """扫描测试目录, 检出 mock 虚增覆盖率的测试文件.

    Args:
        test_dir: 测试目录路径

    Returns:
        违规测试文件路径清单 (相对路径)
    """
    if not test_dir.exists():
        return []

    violations: list[str] = []
    for py_file in test_dir.rglob("test_*.py"):
        try:
            content = py_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        # 提取所有 mock.patch 目标
        mock_targets = _MOCK_PATCH_RE.findall(content)
        if not mock_targets:
            continue

        for target in mock_targets:
            # 排除边缘 IO mock
            is_io_mock = any(p.search(f"mock.patch('{target}')") for p in _IO_MOCK_PATTERNS)
            if is_io_mock:
                continue

            # 检查是否 mock 了核心链路模块
            for core_mod in _CORE_LINK_MODULES:
                if core_mod in target:
                    try:
                        rel_path = str(py_file.relative_to(_ROOT))
                    except ValueError:
                        rel_path = str(py_file)
                    violation = f"{rel_path}: mock.patch('{target}') 替代核心链路 {core_mod}"
                    if violation not in violations:
                        violations.append(violation)
                    break
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description="mock 虚增覆盖率检出器")
    parser.add_argument("--test-dir", type=Path, default=_ROOT / "tests")
    parser.add_argument("--output", type=Path, default=_ROOT / "reports" / "ci" / "mock_inflation.json")
    args = parser.parse_args()

    violations = detect_mock_inflation(args.test_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"violations": violations, "count": len(violations)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if violations:
        print(f"检出 {len(violations)} 处 mock 虚增覆盖率:")
        for v in violations[:10]:
            print(f"  {v}")
        if len(violations) > 10:
            print(f"  ... 共 {len(violations)} 处")
        return 1
    print("无 mock 虚增覆盖率 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
