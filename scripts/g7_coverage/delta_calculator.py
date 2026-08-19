"""G7 覆盖率增量核算器 — 对比补测前后 coverage.xml。

输出 DeltaReport（含 overall_delta_pp、by_stage、by_module、by_bucket）。
"""
from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
import sys as _sys

if str(PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(PROJECT_ROOT))

from scripts.g7_coverage.coverage_inventory import (
    CHAIN_ORDER,
    P0_CHAIN_SPEC,
)


@dataclass
class DeltaReport:
    overall_line_rate_before: float
    overall_line_rate_after: float
    overall_delta_pp: float
    by_stage: dict[str, dict[str, float]] = field(default_factory=dict)
    by_module: list[dict[str, object]] = field(default_factory=list)
    by_bucket: dict[str, dict[str, float]] = field(default_factory=dict)


def _root_line_rate(xml_path: Path) -> float:
    if not xml_path.exists():
        return 0.0
    root = ET.parse(str(xml_path)).getroot()
    return float(root.get("line-rate", "0"))


def _module_line_rate(xml_path: Path, module_path: str) -> float:
    if not xml_path.exists():
        return 0.0
    root = ET.parse(str(xml_path)).getroot()
    import os
    basename = os.path.basename(module_path)
    for cls in root.iter("class"):
        filename = cls.get("filename", "")
        if os.path.basename(filename) == basename or filename == module_path:
            return float(cls.get("line-rate", "0"))
    return 0.0


class DeltaCalculator:
    @staticmethod
    def calculate(
        before_xml_path: Optional[str] = None,
        after_xml_path: Optional[str] = None,
        p0_module_spec: Optional[dict[str, list[str]]] = None,
    ) -> DeltaReport:
        before = Path(before_xml_path) if before_xml_path else PROJECT_ROOT / "reports" / "coverage.xml"
        after = Path(after_xml_path) if after_xml_path else PROJECT_ROOT / "reports" / "coverage.xml"
        spec = p0_module_spec or P0_CHAIN_SPEC
        lr_before = _root_line_rate(before)
        lr_after = _root_line_rate(after)
        delta_pp = (lr_after - lr_before) * 100.0
        by_stage: dict[str, dict[str, float]] = {}
        for stage in CHAIN_ORDER:
            modules = spec.get(stage, [])
            stage_before = sum(_module_line_rate(before, m) for m in modules) / max(len(modules), 1)
            stage_after = sum(_module_line_rate(after, m) for m in modules) / max(len(modules), 1)
            by_stage[stage] = {
                "before": round(stage_before, 4),
                "after": round(stage_after, 4),
                "delta_pp": round((stage_after - stage_before) * 100, 2),
            }
        by_module: list[dict[str, object]] = []
        for stage, modules in spec.items():
            for mod in modules:
                mb = _module_line_rate(before, mod)
                ma = _module_line_rate(after, mod)
                by_module.append(
                    {
                        "module_path": mod,
                        "chain_stage": stage,
                        "before": round(mb, 4),
                        "after": round(ma, 4),
                        "delta_pp": round((ma - mb) * 100, 2),
                    }
                )
        buckets = {"P1_zero": (0.0, 0.0), "P2_low": (0.0, 0.0), "P3_mid": (0.0, 0.0), "P4_covered": (0.0, 0.0)}
        bucket_counts: dict[str, int] = {k: 0 for k in buckets}
        for mod_entry in by_module:
            b = mod_entry["before"]
            if b == 0.0:
                bk = "P1_zero"
            elif b < 0.50:
                bk = "P2_low"
            elif b < 0.80:
                bk = "P3_mid"
            else:
                bk = "P4_covered"
            buckets[bk] = (buckets[bk][0] + b, buckets[bk][1] + mod_entry["after"])
            bucket_counts[bk] += 1
        by_bucket: dict[str, dict[str, float]] = {}
        for bk, (sb, sa) in buckets.items():
            cnt = max(bucket_counts[bk], 1)
            by_bucket[bk] = {
                "count": bucket_counts[bk],
                "before_avg": round(sb / cnt, 4),
                "after_avg": round(sa / cnt, 4),
                "delta_pp": round((sa / cnt - sb / cnt) * 100, 2),
            }
        return DeltaReport(
            overall_line_rate_before=round(lr_before, 4),
            overall_line_rate_after=round(lr_after, 4),
            overall_delta_pp=round(delta_pp, 2),
            by_stage=by_stage,
            by_module=by_module,
            by_bucket=by_bucket,
        )

    @staticmethod
    def calculate_and_archive(
        before_xml_path: Optional[str] = None,
        after_xml_path: Optional[str] = None,
        output_dir: Optional[str] = None,
    ) -> str:
        report = DeltaCalculator.calculate(before_xml_path=before_xml_path, after_xml_path=after_xml_path)
        out_dir = Path(output_dir) if output_dir else PROJECT_ROOT / "reports" / "ci"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"g7_coverage_delta_{ts}.json"
        payload = {"generated_at": datetime.now().isoformat(), **asdict(report)}
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return str(out_path)


def main() -> int:
    out_path = DeltaCalculator.calculate_and_archive()
    report = DeltaCalculator.calculate()
    print(f"增量核算完成, 归档至: {out_path}")
    print(f"整体 line-rate: {report.overall_line_rate_before} → {report.overall_line_rate_after} (Δ={report.overall_delta_pp}pp)")
    print("\n按链路段:")
    for stage, vals in report.by_stage.items():
        print(f"  {stage}: {vals['before']} → {vals['after']} (Δ={vals['delta_pp']}pp)")
    print("\n按优先级桶:")
    for bk, vals in report.by_bucket.items():
        print(f"  {bk}: count={vals['count']}, avg {vals['before_avg']} → {vals['after_avg']} (Δ={vals['delta_pp']}pp)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
