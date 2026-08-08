"""覆盖率趋势检测脚本 — GAP-3 交付物.

ECC mle-workflow + coding-standards 修复:
    将覆盖率作为 PR 闸门, 防止覆盖率退化.

用法:
    # 在 CI 中 (pytest --cov 之后) 调用
    python scripts/_check_coverage_trend.py

    # 指定 baseline 文件
    python scripts/_check_coverage_trend.py --baseline path/to/baseline.json

    # 更新 baseline (本地产物, 不在 CI 调用)
    python scripts/_check_coverage_trend.py --update-baseline

输出:
    - 当前覆盖率 / 上次覆盖率 / 趋势 (↑/↓) / 模块明细
    - 下降 > 2% 时 exit 1 (CI 阻断)
    - 上升时更新 baseline (CI 不阻断, 仅记录)

退出码:
    0 = 通过 (覆盖率未退化, 或无 baseline 时首次记录)
    1 = 失败 (覆盖率下降 > 2%, 或 coverage.xml 不存在)
"""
from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_COVERAGE_XML = _PROJECT_ROOT / "coverage.xml"
_BASELINE_FILE = _PROJECT_ROOT / "coverage_baseline.json"
_DROP_THRESHOLD = 2.0  # 下降 > 2% 时阻断


def parse_coverage_xml(xml_path: Path = _COVERAGE_XML) -> dict[str, Any] | None:
    """解析 coverage.xml, 提取覆盖率数据.

    Args:
        xml_path: coverage.xml 路径

    Returns:
        {
            "total_line_rate": float,  # 0-1
            "total_branch_rate": float,
            "packages": [
                {"name": "utils.alpha", "line_rate": 0.85, ...},
                ...
            ],
            "timestamp": str,
        }
        或 None (文件不存在/解析失败)
    """
    if not xml_path.exists():
        print(f"[GAP-3] coverage.xml 不存在: {xml_path}", file=sys.stderr)
        return None
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    except ET.ParseError as e:
        print(f"[GAP-3] coverage.xml 解析失败: {e}", file=sys.stderr)
        return None

    # 根 <coverage> 元素的 line-rate / branch-rate 是全局覆盖率
    total_line_rate = float(root.attrib.get("line-rate", "0"))
    total_branch_rate = float(root.attrib.get("branch-rate", "0"))

    # 各 <package> 的覆盖率
    packages = []
    for pkg in root.iter("package"):
        pkg_name = pkg.attrib.get("name", "unknown")
        # package 内的 <metrics> 元素
        metrics = pkg.find("metrics")
        if metrics is not None:
            line_rate = float(metrics.attrib.get("line-rate", "0"))
            branch_rate = float(metrics.attrib.get("branch-rate", "0"))
            statements = int(metrics.attrib.get("statements", "0"))
            covered = int(metrics.attrib.get("covered-statements", "0"))
        else:
            line_rate = float(pkg.attrib.get("line-rate", "0"))
            branch_rate = float(pkg.attrib.get("branch-rate", "0"))
            statements = 0
            covered = 0
        packages.append({
            "name": pkg_name,
            "line_rate": line_rate,
            "branch_rate": branch_rate,
            "statements": statements,
            "covered": covered,
        })

    return {
        "total_line_rate": total_line_rate,
        "total_branch_rate": total_branch_rate,
        "packages": packages,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


def load_baseline(baseline_path: Path = _BASELINE_FILE) -> dict[str, Any] | None:
    """加载上次覆盖率 baseline.

    Args:
        baseline_path: baseline 文件路径

    Returns:
        baseline dict 或 None (不存在)
    """
    if not baseline_path.exists():
        return None
    try:
        with open(baseline_path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[GAP-3] baseline 加载失败: {e}", file=sys.stderr)
        return None


def update_baseline(
    current: dict[str, Any],
    baseline_path: Path = _BASELINE_FILE,
) -> None:
    """更新 baseline 文件 (本地产物, 不在 CI 调用).

    Args:
        current: 当前覆盖率数据
        baseline_path: baseline 文件路径
    """
    try:
        with open(baseline_path, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2, ensure_ascii=False)
        print(f"[GAP-3] baseline 已更新: {baseline_path}")
    except OSError as e:
        print(f"[GAP-3] baseline 更新失败: {e}", file=sys.stderr)


def compare_coverage(
    current: dict[str, Any],
    baseline: dict[str, Any],
    drop_threshold: float = _DROP_THRESHOLD,
) -> dict[str, Any]:
    """对比当前覆盖率与 baseline.

    Args:
        current: 当前覆盖率
        baseline: 基线覆盖率
        drop_threshold: 下降阈值 (百分比, 默认 2.0)

    Returns:
        {
            "current_pct": float,
            "baseline_pct": float,
            "delta_pct": float,
            "trend": "up" | "down" | "stable",
            "passed": bool,
            "module_diffs": [...],
        }
    """
    current_pct = current["total_line_rate"] * 100
    baseline_pct = baseline["total_line_rate"] * 100
    delta_pct = current_pct - baseline_pct

    if delta_pct > 0.5:
        trend = "up"
    elif delta_pct < -0.5:
        trend = "down"
    else:
        trend = "stable"

    # 模块级对比
    baseline_pkgs = {p["name"]: p for p in baseline.get("packages", [])}
    module_diffs = []
    for pkg in current.get("packages", []):
        name = pkg["name"]
        base_pkg = baseline_pkgs.get(name)
        if base_pkg is None:
            continue
        cur_rate = pkg["line_rate"] * 100
        base_rate = base_pkg["line_rate"] * 100
        diff = cur_rate - base_rate
        if abs(diff) > 1.0:  # 只显示变化 > 1% 的模块
            module_diffs.append({
                "name": name,
                "current_pct": round(cur_rate, 2),
                "baseline_pct": round(base_rate, 2),
                "delta_pct": round(diff, 2),
            })
    module_diffs.sort(key=lambda x: x["delta_pct"])

    # 判定: 下降 > 阈值则不通过
    passed = delta_pct >= -drop_threshold

    return {
        "current_pct": round(current_pct, 2),
        "baseline_pct": round(baseline_pct, 2),
        "delta_pct": round(delta_pct, 2),
        "trend": trend,
        "passed": passed,
        "module_diffs": module_diffs,
    }


def main() -> int:
    """主入口.

    Returns:
        0 = 通过, 1 = 失败
    """
    parser = argparse.ArgumentParser(description="GAP-3 覆盖率趋势检测")
    parser.add_argument(
        "--baseline",
        type=str,
        default=str(_BASELINE_FILE),
        help="baseline 文件路径 (默认 coverage_baseline.json)",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="更新 baseline (本地产物, 不在 CI 调用)",
    )
    parser.add_argument(
        "--coverage-xml",
        type=str,
        default=str(_COVERAGE_XML),
        help="coverage.xml 路径",
    )
    parser.add_argument(
        "--drop-threshold",
        type=float,
        default=_DROP_THRESHOLD,
        help=f"下降阈值 (百分比, 默认 {_DROP_THRESHOLD})",
    )
    args = parser.parse_args()

    baseline_path = Path(args.baseline)
    xml_path = Path(args.coverage_xml)

    # 1. 解析当前覆盖率
    current = parse_coverage_xml(xml_path)
    if current is None:
        print("[GAP-3] FAIL: 无法获取当前覆盖率 (coverage.xml 不存在或解析失败)")
        return 1

    current_pct = current["total_line_rate"] * 100
    print(f"[GAP-3] 当前覆盖率: {current_pct:.2f}%")

    # 2. 更新 baseline 模式
    if args.update_baseline:
        update_baseline(current, baseline_path)
        return 0

    # 3. 加载 baseline
    baseline = load_baseline(baseline_path)
    if baseline is None:
        print(f"[GAP-3] baseline 不存在 ({baseline_path}), 首次运行, 记录当前覆盖率作为 baseline")
        print(f"[GAP-3] 提示: 本地运行 `python {sys.argv[0]} --update-baseline` 更新 baseline")
        print("[GAP-3] PASS (无 baseline, 首次记录)")
        return 0

    baseline_pct = baseline["total_line_rate"] * 100
    print(f"[GAP-3] baseline 覆盖率: {baseline_pct:.2f}%")

    # 4. 对比
    result = compare_coverage(current, baseline, args.drop_threshold)
    delta = result["delta_pct"]
    trend = result["trend"]
    arrow = {"up": "↑", "down": "↓", "stable": "="}[trend]
    print(f"[GAP-3] 趋势: {arrow} {delta:+.2f}% (threshold: -{args.drop_threshold}%)")

    # 模块明细
    if result["module_diffs"]:
        print("[GAP-3] 模块变化 (>1%):")
        for m in result["module_diffs"]:
            sign = "+" if m["delta_pct"] >= 0 else ""
            print(f"  {m['name']}: {m['current_pct']:.2f}% (was {m['baseline_pct']:.2f}%, {sign}{m['delta_pct']:.2f}%)")

    # 5. 判定
    if result["passed"]:
        print(f"[GAP-3] PASS: 覆盖率未退化 (delta={delta:+.2f}%)")
        return 0
    else:
        print(f"[GAP-3] FAIL: 覆盖率退化 {abs(delta):.2f}% > 阈值 {args.drop_threshold}%")
        print(f"[GAP-3] 请修复覆盖率退化, 或本地运行 `python {sys.argv[0]} --update-baseline` 更新 baseline (仅当退化是预期的)")
        return 1


if __name__ == "__main__":
    sys.exit(main())
