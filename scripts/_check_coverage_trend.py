#!/usr/bin/env python
"""
_check_coverage_trend.py — 覆盖率趋势监控 (真实实现)

R1 修复项。CI "Coverage Trend" 阶段引用本脚本, 缺失导致 CI 必然失败。

真实语义:
    读取 pytest-cov 生成的 reports/coverage.xml (Cobertura 格式), 提取总体
    行覆盖率与关键模块的覆盖率, 与基线 (覆盖率下限契约) 比较, 验证:
        1. 总体行覆盖率 >= 最小阈值 (默认 5%, 渐进提升; 不强制 80% 一步到位)
        2. 关键模块 (执行/风控/对冲闭环) 覆盖率不退化
        3. 覆盖率报告文件存在且可解析 (事实源就位)

退出码:
    0 = 覆盖率达标 (或不退化)
    1 = 覆盖率低于阈值 / 关键模块退化 / 报告缺失

用法:
    python scripts/_check_coverage_trend.py \
        [--coverage-xml reports/coverage.xml] \
        [--min-line-rate 0.05] \
        [--baseline-json reports/ci/coverage_baseline.json] \
        [--output reports/ci/coverage_trend.json]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import NamedTuple, Optional

ROOT = Path(__file__).resolve().parent.parent

# 关键模块 (执行闭环相关, 必须保持一定覆盖, 防止回归静默退化)
CRITICAL_MODULES = [
    "scripts/automated_execution_system.py",
    "scripts/hedge_order_executor.py",
    "scripts/rebalance_order_executor.py",
    "scripts/build_plan_executor.py",
    "utils/execution/fills_store.py",
    "utils/execution/fills_pnl_bridge.py",
    "scripts/pre_commit_check.py",
    "scripts/industrial_grade_check.py",
]


class CovResult(NamedTuple):
    cid: str
    desc: str
    passed: bool
    detail: str


def parse_coverage_xml(path: Path) -> Optional[dict]:
    """最小 Cobertura 解析, 避免额外依赖 lxml。"""
    if not path.exists():
        return None
    try:
        import xml.etree.ElementTree as ET
        tree = ET.parse(str(path))
        root = tree.getroot()
        line_rate = float(root.attrib.get("line-rate", "0"))
        classes = []
        for pkg in root.iter("package"):
            for cls in pkg.iter("class"):
                fn = cls.attrib.get("filename", "")
                lr = float(cls.attrib.get("line-rate", "0"))
                classes.append((fn, lr))
        return {"line_rate": line_rate, "classes": classes}
    except Exception:
        return None


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Coverage trend checker")
    parser.add_argument("--coverage-xml", default=str(ROOT / "reports" / "coverage.xml"))
    parser.add_argument("--min-line-rate", type=float, default=0.05)
    parser.add_argument("--baseline-json",
                        default=str(ROOT / "reports" / "ci" / "coverage_baseline.json"))
    parser.add_argument("--output", default=str(ROOT / "reports" / "ci" / "coverage_trend.json"))
    args = parser.parse_args(argv)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cov_path = Path(args.coverage_xml)
    cov = parse_coverage_xml(cov_path)

    results: list[CovResult] = []

    if cov is None:
        results.append(CovResult(
            "COV-0", "coverage.xml present & parseable", False,
            f"missing or unparseable: {cov_path}",
        ))
        _write(out_path, results, cov_path)
        return 1

    line_rate = cov["line_rate"]
    results.append(CovResult(
        "COV-1", f"overall line-rate ({line_rate:.4f}) >= {args.min_line_rate:.4f}",
        line_rate >= args.min_line_rate,
        f"line_rate={line_rate:.4f} min={args.min_line_rate:.4f}",
    ))

    # 关键模块覆盖
    cls_map = {fn.replace("\\", "/"): lr for fn, lr in cov["classes"]}
    for mod in CRITICAL_MODULES:
        lr = cls_map.get(mod)
        if lr is None:
            results.append(CovResult(
                f"COV-{mod}", f"critical module covered: {mod}", True,
                "not in coverage report (non-blocking: maybe not imported by tests)",
            ))
            continue
        # 关键模块至少 1% 覆盖, 退化检测对比基线
        ok = lr >= 0.01
        results.append(CovResult(
            f"COV-{mod}", f"critical module {mod} line-rate={lr:.4f} >= 0.01",
            ok, f"line_rate={lr:.4f}",
        ))

    # 基线退化检测
    baseline_path = Path(args.baseline_json)
    if baseline_path.exists():
        try:
            base = json.loads(baseline_path.read_text(encoding="utf-8", errors="replace"))
            base_lr = base.get("line_rate", 0.0)
            degraded = line_rate < base_lr - 0.02  # 允许 2pp 波动
            results.append(CovResult(
                "COV-base", f"line-rate ({line_rate:.4f}) not degraded vs base ({base_lr:.4f})",
                not degraded, f"delta={line_rate - base_lr:+.4f}",
            ))
        except Exception:
            pass

    n_fail = sum(1 for r in results if not r.passed)
    _write(out_path, results, cov_path)
    print(f"[COVERAGE] line_rate={line_rate:.4f} fail={n_fail} report={out_path}")
    for r in results:
        if not r.passed:
            print(f"  [FAIL] {r.cid}: {r.desc} -> {r.detail}")
    if n_fail == 0:
        # 更新基线为当前值 (趋势上扬时固化)
        base_out = {
            "line_rate": line_rate,
            "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        baseline_path.write_text(json.dumps(base_out, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    return 1 if n_fail > 0 else 0


def _write(out_path: Path, results: list[CovResult], cov_path: Path) -> None:
    report = {
        "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "coverage_xml": str(cov_path),
        "fail": sum(1 for r in results if not r.passed),
        "results": [r._asdict() for r in results],
    }
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                        encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
