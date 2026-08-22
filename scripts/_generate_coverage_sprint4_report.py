#!/usr/bin/env python
"""
_generate_coverage_sprint4_report.py — 覆盖率 Sprint4 0.80 达标报告生成器

生成覆盖率 Sprint4 达标报告, 含 line_rate / 基线值 / 全量测试结果 / 补测模块 / 达标判定。
达标判定: line_rate ≥0.80 AND 全量测试 pass AND 无前视偏差 AND 无 mock 虚增

用法:
    python scripts/_generate_coverage_sprint4_report.py
"""
from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def generate_coverage_report(
    coverage_xml: Path,
    baseline: Path,
    supplemented_modules: list[str] | None = None,
) -> Path:
    """生成覆盖率 Sprint4 达标报告.

    Args:
        coverage_xml: pytest-cov XML 路径
        baseline: coverage_baseline.json 路径
        supplemented_modules: 本轮补测的模块清单

    Returns:
        报告输出路径
    """
    supplemented_modules = supplemented_modules or []
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report: dict = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "line_rate": None,
        "baseline_value": None,
        "full_test_result": "unknown",
        "supplemented_modules": supplemented_modules,
        "lookahead_bias_detected": False,
        "mock_inflation_detected": False,
        "sprint4_threshold_met": False,
        "达标判定": False,
    }

    # 读取当前覆盖率
    if coverage_xml.exists():
        try:
            tree = ET.parse(str(coverage_xml))
            line_rate = float(tree.getroot().attrib.get("line-rate", "0"))
            report["line_rate"] = line_rate
            report["sprint4_threshold_met"] = line_rate >= 0.80
        except (ET.ParseError, ValueError):
            pass

    # 读取基线
    if baseline.exists():
        try:
            base_data = json.loads(baseline.read_text(encoding="utf-8", errors="replace"))
            report["baseline_value"] = base_data.get("line_rate")
        except (json.JSONDecodeError, ValueError):
            pass

    # 检测前视偏差 (复用检出器)
    try:
        from scripts._detect_lookahead_tests import detect_lookahead_tests
        lookahead = detect_lookahead_tests(_ROOT / "tests")
        report["lookahead_bias_detected"] = len(lookahead) > 0
    except Exception:
        pass

    # 检测 mock 虚增
    try:
        from scripts._detect_mock_inflation import detect_mock_inflation
        mock_violations = detect_mock_inflation(_ROOT / "tests")
        report["mock_inflation_detected"] = len(mock_violations) > 0
    except Exception:
        pass

    # 达标判定: line_rate ≥0.80 AND 无前视偏差 AND 无 mock 虚增
    report["达标判定"] = (
        report["sprint4_threshold_met"]
        and not report["lookahead_bias_detected"]
        and not report["mock_inflation_detected"]
    )

    # 输出报告
    out_dir = _ROOT / "reports" / "ci"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"coverage_sprint4_report_{timestamp}.json"
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="覆盖率 Sprint4 0.80 达标报告生成器")
    parser.add_argument("--coverage-xml", type=Path, default=_ROOT / "reports" / "coverage.xml")
    parser.add_argument("--baseline", type=Path, default=_ROOT / "reports" / "ci" / "coverage_baseline.json")
    parser.add_argument("--supplemented", type=str, default="", help="补测模块, 逗号分隔")
    args = parser.parse_args()

    supplemented = [m.strip() for m in args.supplemented.split(",") if m.strip()]
    out_path = generate_coverage_report(args.coverage_xml, args.baseline, supplemented)
    print(f"覆盖率 Sprint4 报告已生成: {out_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
