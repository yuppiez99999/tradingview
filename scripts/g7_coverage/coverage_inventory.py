"""G7 覆盖率盘点脚本 — 解析 coverage.xml 提取 P0 模块 line-rate。

实现 CoverageInventory.scan(coverage_xml_path, coveragerc_path, p0_module_spec) 接口，
按 spec §5.1.1 规则2 筛选 P0 模块（主路径 ∧ 未omit ∧ <80%）。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]

P0_CHAIN_SPEC: Dict[str, List[str]] = {
    "data_collection": [
        "ms_strategy/src/data/qmt_data_feed.py",
        "utils/data_provider.py",
        "utils/akshare_data_source.py",
    ],
    "factor_calculation": [
        "utils/alpha_factor/base.py",
        "utils/alpha_factor/fundamental.py",
        "utils/alpha_factor/technical.py",
        "utils/alpha_factor/price_volume.py",
        "utils/alpha_factor/library.py",
        "utils/alpha_factor/expectation.py",
        "utils/alpha_factor/gate1_validation.py",
        "utils/alpha_factor/graph.py",
        "utils/alpha_factor_library.py",
    ],
    "signal_generation": [
        "ms_strategy/src/alpha/signal_fusion.py",
        "ms_strategy/src/alpha/signal_generator.py",
    ],
    "risk_control": [
        "utils/risk_constraints.py",

        "utils/risk_metrics.py",
    ],
    "order_placement": [
        "ms_strategy/src/execution/smart_order_router.py",
        "ms_strategy/src/execution/qmt_broker.py",
        "ms_strategy/src/execution/broker_api.py",
        "ms_strategy/src/execution/algo_engine.py",
        "ms_strategy/src/execution/ntp_sync.py",
        "ms_strategy/src/execution/post_execution_review.py",
        "ms_strategy/scripts/hedge_execution_orders.py",
        "ms_strategy/scripts/live_scheduler.py",
        "ms_strategy/scripts/automated_execution_system.py",
        "utils/cost_model.py",
        "utils/qmt_broker.py",
        "utils/execution_algo_engine.py",
        "utils/smart_order_router.py",
        "utils/transaction_cost_model.py",
    ],
    "reconciliation": [],
    "backtest": [
        "ms_strategy/src/backtest/combinatorial_purged_cv.py",
        "ms_strategy/src/backtest/cost_model.py",
        "ms_strategy/src/backtest/metrics.py",
        "ms_strategy/src/backtest/cost_aware_backtest.py",
        "ms_strategy/src/backtest/scenario_lib.py",
        "ms_strategy/src/backtest/noise_injection_test.py",
        "ms_strategy/src/backtest/walk_forward.py",
        "utils/backtest/vectorbt_bridge.py",
        "utils/backtest/honest_validation.py",
        "utils/backtest/a_share_rules.py",
        "utils/backtest/deflated_sharpe.py",
    ],
}

CHAIN_ORDER = list(P0_CHAIN_SPEC.keys())


class CoverageReportMissingError(FileNotFoundError):
    pass


class CoverageReportParseError(ValueError):
    pass


@dataclass
class ModuleCoverageRecord:
    module_path: str
    chain_stage: str
    chain_position: int
    in_coverage_xml: bool
    line_rate_before: float
    lines_valid: int = 0
    lines_covered: int = 0
    priority_bucket: str = field(default="")

    def __post_init__(self) -> None:
        if self.line_rate_before == 0.0:
            self.priority_bucket = "P1_zero"
        elif self.line_rate_before < 0.50:
            self.priority_bucket = "P2_low"
        elif self.line_rate_before < 0.80:
            self.priority_bucket = "P3_mid"
        else:
            self.priority_bucket = "P4_covered"


def _load_omit_patterns(coveragerc_path: Path) -> List[str]:
    patterns: List[str] = []
    if not coveragerc_path.exists():
        return patterns
    in_omit = False
    for line in coveragerc_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("omit"):
            in_omit = True
            continue
        if in_omit:
            if stripped and not stripped.startswith("#") and "=" not in stripped:
                if stripped.startswith("["):
                    in_omit = False
                    continue
                patterns.append(stripped)
            elif stripped.startswith("["):
                in_omit = False
    return patterns


def _is_omitted(module_path: str, patterns: Sequence[str]) -> bool:
    for pat in patterns:
        normalized = pat.replace("*/", "").replace("*", "")
        if normalized and normalized in module_path:
            return True
    return False


def _parse_coverage_xml(xml_path: Path) -> Dict[str, Dict[str, object]]:
    if not xml_path.exists():
        raise CoverageReportMissingError(f"coverage.xml 不存在: {xml_path}")
    try:
        tree = ET.parse(str(xml_path))
    except ET.ParseError as exc:
        raise CoverageReportParseError(f"coverage.xml 解析失败: {exc}") from exc
    root = tree.getroot()
    file_map: Dict[str, Dict[str, object]] = {}
    for cls in root.iter("class"):
        filename = cls.get("filename")
        if not filename:
            continue
        line_rate = float(cls.get("line-rate", "0"))
        lines_valid = 0
        lines_covered = 0
        for line in cls.iter("line"):
            lines_valid += 1
            if line.get("hits", "0") != "0":
                lines_covered += 1
        file_map[filename] = {
            "line_rate": line_rate,
            "lines_valid": lines_valid,
            "lines_covered": lines_covered,
        }
    return file_map


def _match_module(module_path: str, file_map: Dict[str, Dict[str, object]]) -> Optional[Dict[str, object]]:
    if module_path in file_map:
        return file_map[module_path]
    basename = os.path.basename(module_path)
    for key, val in file_map.items():
        if os.path.basename(key) == basename:
            return val
    return None


class CoverageInventory:
    @staticmethod
    def scan(
        coverage_xml_path: Optional[str] = None,
        coveragerc_path: Optional[str] = None,
        p0_module_spec: Optional[Dict[str, List[str]]] = None,
    ) -> List[ModuleCoverageRecord]:
        xml_path = Path(coverage_xml_path) if coverage_xml_path else PROJECT_ROOT / "reports" / "coverage.xml"
        rc_path = Path(coveragerc_path) if coveragerc_path else PROJECT_ROOT / ".coveragerc"
        spec = p0_module_spec or P0_CHAIN_SPEC
        omit_patterns = _load_omit_patterns(rc_path)
        file_map = _parse_coverage_xml(xml_path)
        records: List[ModuleCoverageRecord] = []
        for stage_idx, stage in enumerate(CHAIN_ORDER):
            modules = spec.get(stage, [])
            for mod in modules:
                if _is_omitted(mod, omit_patterns):
                    continue
                matched = _match_module(mod, file_map)
                if matched is not None:
                    records.append(
                        ModuleCoverageRecord(
                            module_path=mod,
                            chain_stage=stage,
                            chain_position=stage_idx,
                            in_coverage_xml=True,
                            line_rate_before=float(matched["line_rate"]),
                            lines_valid=int(matched["lines_valid"]),
                            lines_covered=int(matched["lines_covered"]),
                        )
                    )
                else:
                    records.append(
                        ModuleCoverageRecord(
                            module_path=mod,
                            chain_stage=stage,
                            chain_position=stage_idx,
                            in_coverage_xml=False,
                            line_rate_before=0.0,
                        )
                    )
        records.sort(key=lambda r: r.line_rate_before)
        return records

    @staticmethod
    def scan_and_archive(
        output_dir: Optional[str] = None,
        coverage_xml_path: Optional[str] = None,
    ) -> str:
        records = CoverageInventory.scan(coverage_xml_path=coverage_xml_path)
        out_dir = Path(output_dir) if output_dir else PROJECT_ROOT / "reports" / "ci"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"g7_coverage_inventory_{ts}.json"
        payload = {
            "generated_at": datetime.now().isoformat(),
            "coverage_xml_root_line_rate": _root_line_rate(coverage_xml_path),
            "total_p0_modules": len(records),
            "by_bucket": _count_by_bucket(records),
            "records": [asdict(r) for r in records],
        }
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return str(out_path)


def _root_line_rate(coverage_xml_path: Optional[str]) -> Optional[float]:
    xml_path = Path(coverage_xml_path) if coverage_xml_path else PROJECT_ROOT / "reports" / "coverage.xml"
    if not xml_path.exists():
        return None
    root = ET.parse(str(xml_path)).getroot()
    return float(root.get("line-rate", "0"))


def _count_by_bucket(records: Sequence[ModuleCoverageRecord]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for r in records:
        counts[r.priority_bucket] = counts.get(r.priority_bucket, 0) + 1
    return counts


def main() -> int:
    start = time.time()
    out_path = CoverageInventory.scan_and_archive()
    elapsed = time.time() - start
    records = CoverageInventory.scan()
    print(f"盘点完成: {len(records)} 个 P0 模块, 耗时 {elapsed:.2f}s")
    print(f"归档至: {out_path}")
    by_bucket = _count_by_bucket(records)
    print(f"分桶统计: {by_bucket}")
    zero_modules = [r for r in records if r.line_rate_before == 0.0]
    print(f"0% 模块 ({len(zero_modules)} 个):")
    for r in zero_modules:
        print(f"  {r.module_path}  [{r.chain_stage}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())