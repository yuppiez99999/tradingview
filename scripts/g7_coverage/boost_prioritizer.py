"""G7 补测优先级排序器 — 按 spec §5.3.1 规则1 三级优先级排序。

P1（line-rate=0%）→ P2（<50%）→ P3（50-80%），同级内按链路位置排序。
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from utils.datetime_utils import now_bj

PROJECT_ROOT = Path(__file__).resolve().parents[2]
import sys as _sys

if str(PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(PROJECT_ROOT))

from scripts.g7_coverage.coverage_inventory import (
    CHAIN_ORDER,
    CoverageInventory,
    ModuleCoverageRecord,
)


@dataclass
class BoostTask:
    module_path: str
    priority: int
    priority_bucket: str
    chain_stage: str
    chain_position: int
    test_file_path: str
    line_rate_before: float
    target_line_rate: float
    testability_refactor: str = "none"
    status: str = "pending"

    @property
    def task_id(self) -> str:
        return f"P{self.priority}_{Path(self.module_path).stem}"


def _target_rate(bucket: str) -> float:
    if bucket == "P1_zero":
        return 0.85
    if bucket == "P2_low":
        return 0.70
    if bucket == "P3_mid":
        return 0.85
    return 0.85


def _test_file_path(module_path: str) -> str:
    stem = Path(module_path).stem
    if "ms_strategy" in module_path:
        prefix = "test_g7_ms_strategy_"
    elif "alpha_factor" in module_path:
        prefix = "test_g7_alpha_factor_"
    elif "backtest" in module_path:
        prefix = "test_g7_backtest_"
    else:
        prefix = "test_g7_"
    return f"tests/unit/{prefix}{stem}_boost.py"


class BoostPrioritizer:
    @staticmethod
    def prioritize(
        records: Sequence[ModuleCoverageRecord] | None = None,
        chain_order: Sequence[str] | None = None,
    ) -> list[BoostTask]:
        if records is None:
            records = CoverageInventory.scan()
        order = list(chain_order) if chain_order else CHAIN_ORDER
        sorted_records = sorted(
            records,
            key=lambda r: (
                _priority_rank(r.priority_bucket),
                order.index(r.chain_stage) if r.chain_stage in order else len(order),
                r.module_path,
            ),
        )
        tasks: list[BoostTask] = []
        for r in sorted_records:
            if r.priority_bucket == "P4_covered":
                continue
            tasks.append(
                BoostTask(
                    module_path=r.module_path,
                    priority=_priority_rank(r.priority_bucket),
                    priority_bucket=r.priority_bucket,
                    chain_stage=r.chain_stage,
                    chain_position=r.chain_position,
                    test_file_path=_test_file_path(r.module_path),
                    line_rate_before=r.line_rate_before,
                    target_line_rate=_target_rate(r.priority_bucket),
                )
            )
        return tasks

    @staticmethod
    def prioritize_and_archive(
        output_dir: str | None = None,
    ) -> str:
        tasks = BoostPrioritizer.prioritize()
        out_dir = Path(output_dir) if output_dir else PROJECT_ROOT / "reports" / "ci"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = now_bj().strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"g7_boost_queue_{ts}.json"
        payload = {
            "generated_at": now_bj().isoformat(),
            "total_tasks": len(tasks),
            "by_priority": _count_by_priority(tasks),
            "tasks": [asdict(t) for t in tasks],
        }
        out_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return str(out_path)


def _priority_rank(bucket: str) -> int:
    return {"P1_zero": 1, "P2_low": 2, "P3_mid": 3, "P4_covered": 4}.get(bucket, 5)


def _count_by_priority(tasks: Sequence[BoostTask]) -> dict:
    counts: dict = {}
    for t in tasks:
        key = f"P{t.priority}"
        counts[key] = counts.get(key, 0) + 1
    return counts


def main() -> int:
    start = time.time()
    out_path = BoostPrioritizer.prioritize_and_archive()
    elapsed = time.time() - start
    tasks = BoostPrioritizer.prioritize()
    print(f"排序完成: {len(tasks)} 个补测任务, 耗时 {elapsed:.2f}s")
    print(f"归档至: {out_path}")
    by_p = _count_by_priority(tasks)
    print(f"优先级统计: {by_p}")
    print("\nP1 队列 (0% 模块):")
    for t in tasks:
        if t.priority == 1:
            print(f"  {t.module_path}  [{t.chain_stage}]  → {t.test_file_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
