#!/usr/bin/env python
"""
_find_uncovered_p02_branches.py — P0-P2 链路未覆盖分支识别器

差驱识别 coverage.xml 中 risk / execution / pipeline 链路的未覆盖分支,
按优先级排序输出待补测分支清单, 供补测任务消费。

优先级排序: risk > execution > pipeline (P0-P2 链路)
排除规则: 易测边缘分支 (纯 IO / 日志 / __repr__ 等) 不计入

用法:
    python scripts/_find_uncovered_p02_branches.py [--coverage-xml reports/coverage.xml]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from xml.etree.ElementTree import ParseError

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from utils.safe_xml import safe_xml_parse  # noqa: E402  (bandit B314: 统一加固解析入口)

# P0-P2 链路优先级映射 (数字越小优先级越高)
# 匹配 package name (如 "risk") 或 filename 路径 (如 "utils/risk")
_PRIORITY_MAP = {
    "risk": "P0",
    "src.risk": "P0",
    "utils/risk": "P0",
    "execution": "P1",
    "src.execution": "P1",
    "utils/execution": "P1",
    "pipeline": "P2",
    "src.pipeline": "P2",
    "utils/pipeline": "P2",
    "industrial_pipeline_runner": "P1",
}

# 排除的易测边缘分支 (不计入待补测)
_EXCLUDE_PATTERNS = ("__repr__", "__str__", "__hash__", "_log", "logger", "__init__")


@dataclass(frozen=True)
class UncoveredBranch:
    """未覆盖分支描述."""

    module: str
    file: str
    line_start: int
    line_end: int
    branch_type: str
    priority: str

    def to_dict(self) -> dict:
        return asdict(self)


def _classify_priority(filepath: str) -> str:
    """根据文件路径或 package name 分类优先级, 返回空字符串表示不匹配."""
    for prefix, prio in _PRIORITY_MAP.items():
        if prefix in filepath:
            return prio
    return ""


def _is_excluded(line_content: str) -> bool:
    """判断是否为易测边缘分支 (排除)."""
    return any(pat in line_content for pat in _EXCLUDE_PATTERNS)


def find_uncovered_p02_branches(coverage_xml: Path) -> list[UncoveredBranch]:
    """从 coverage.xml 提取 P0-P2 链路未覆盖分支.

    Args:
        coverage_xml: pytest-cov 生成的 Cobertura XML 路径

    Returns:
        按优先级排序的未覆盖分支清单 (risk > execution > pipeline)
    """
    if not coverage_xml.exists():
        return []

    try:
        tree = safe_xml_parse(coverage_xml)  # 加固解析入口 (bandit B314)
    except ParseError:
        return []

    root = tree.getroot()
    branches: list[UncoveredBranch] = []

    for pkg_elem in root.iter("package"):
        pkg_name = pkg_elem.attrib.get("name", "")
        for cls_elem in pkg_elem.iter("class"):
            filename = cls_elem.attrib.get("filename", "")
            # 用 package name + filename 组合分类优先级
            priority = _classify_priority(pkg_name) or _classify_priority(filename)
            if not priority:
                continue  # 仅 P0-P2 链路

            # 解析 <line> 标签中 branch=true 且 missing-branches > 0 的行
            for line_elem in cls_elem.iter("line"):
                is_branch = line_elem.attrib.get("branch", "false") == "true"
                if not is_branch:
                    continue
                missing = line_elem.attrib.get("missing-branches", "0")
                try:
                    missing_count = int(missing.split(",")[0]) if missing != "0" else 0
                except ValueError:
                    missing_count = 0
                if missing_count == 0:
                    continue

                line_no = int(line_elem.attrib.get("number", "0"))
                line_content = line_elem.attrib.get("source", "")
                if _is_excluded(line_content):
                    continue

                branches.append(
                    UncoveredBranch(
                        module=pkg_name or filename,
                        file=filename,
                        line_start=line_no,
                        line_end=line_no,
                        branch_type="branch",
                        priority=priority,
                    )
                )

    # 按优先级排序 (P0 < P1 < P2)
    priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    branches.sort(
        key=lambda b: (priority_order.get(b.priority, 9), b.file, b.line_start)
    )
    return branches


def main() -> int:
    parser = argparse.ArgumentParser(description="P0-P2 未覆盖分支识别器")
    parser.add_argument(
        "--coverage-xml", type=Path, default=_ROOT / "reports" / "coverage.xml"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_ROOT / "reports" / "ci" / "uncovered_p02_branches.json",
    )
    args = parser.parse_args()

    branches = find_uncovered_p02_branches(args.coverage_xml)
    if not branches:
        print(f"无未覆盖分支 (或 {args.coverage_xml.name} 缺失)")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps([b.to_dict() for b in branches], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"发现 {len(branches)} 个未覆盖分支 (P0-P2), 输出到 {args.output.name}")
    by_prio: dict[str, int] = {}
    for b in branches:
        by_prio[b.priority] = by_prio.get(b.priority, 0) + 1
    for prio in sorted(by_prio):
        print(f"  {prio}: {by_prio[prio]} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
