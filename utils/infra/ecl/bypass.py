"""EOD 阶段 4.95 ECL 旁路编排 — CTX-A3.

三步编排 (spec_CTX-A3.md §2.2):
    ①对账兜底: decisions.jsonl 当日行 vs ecl_events 当日事件, 差额回补
    ②经验提炼: ExperienceStore.derive_from_events(as_of=当日)
    ③检索记录: query_similar → reports/ecl/retrieval_{date}.json

全部 fail-open: 任何异常不增加 fail_count (旁路性质, 非 EOD 核心阶段).
三 flag 独立控制三步.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from utils.infra.feature_flags import is_enabled

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DEFAULT_DB = _PROJECT_ROOT / "data" / "ecl" / "ecl_events.db"
_DEFAULT_DECISIONS = _PROJECT_ROOT / "reports" / "evolution" / "decisions.jsonl"
_DEFAULT_RETRIEVAL_DIR = _PROJECT_ROOT / "reports" / "ecl"


def _reconcile_events(
    report_date: str, db_path: Path, decisions_path: Path
) -> dict[str, Any]:
    """①对账兜底: decisions.jsonl 当日行 vs ecl_events 当日事件, 差额回补."""
    from utils.infra.ecl.event_store import EventStore
    from utils.infra.ecl.sinks import _map_record_to_event

    store = EventStore(db_path)
    decisions_count = 0
    backfilled = 0

    if decisions_path.exists():
        with decisions_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = record.get("timestamp", "")
                if not ts.startswith(report_date):
                    continue
                decisions_count += 1

                event_type, subject, payload = _map_record_to_event(record)
                before = store.count()
                store.append(event_type, subject, payload, ts=ts, schema_version=1)
                after = store.count()
                if after > before:
                    backfilled += 1

    events_count = store.count()
    return {
        "step": "reconcile",
        "decisions_today": decisions_count,
        "backfilled": backfilled,
        "total_events": events_count,
    }


def _derive_experiences(report_date: str, db_path: Path) -> dict[str, Any]:
    """②经验提炼: ExperienceStore.derive_from_events."""
    from utils.infra.ecl.event_store import EventStore
    from utils.infra.ecl.experience_store import ExperienceStore

    store = EventStore(db_path)
    exp_store = ExperienceStore(db_path, store)
    count = exp_store.derive_from_events(report_date)
    return {"step": "derive", "new_experiences": count}


def _record_retrieval(
    report_date: str, db_path: Path, retrieval_dir: Path
) -> dict[str, Any]:
    """③检索记录: query_similar → reports/ecl/retrieval_{date}.json."""
    from utils.infra.ecl.event_store import EventStore
    from utils.infra.ecl.experience_store import ExperienceStore

    store = EventStore(db_path)
    exp_store = ExperienceStore(db_path, store)

    regime_events = store.replay(event_type="regime_shift_suggestion")
    if not regime_events:
        return {"step": "retrieval", "result_count": 0, "reason": "no_regime_events"}

    latest = regime_events[-1]
    indicators = latest.payload.get("indicators", {})
    regime = latest.payload.get("regime", {})
    context = {
        "regime_label": regime.get("label"),
        "vix": indicators.get("vix"),
        "realized_vol": indicators.get("realized_vol"),
        "current_drawdown": indicators.get("current_drawdown"),
    }

    results = exp_store.query_similar(context, k=5)
    output_path = retrieval_dir / f"retrieval_{report_date}.json"
    exp_store.write_retrieval_report(context, results, output_path)

    return {
        "step": "retrieval",
        "result_count": len(results),
        "output": str(output_path),
    }


def run_ecl_bypass(
    report_date: str,
    db_path: Path | str | None = None,
    decisions_path: Path | str | None = None,
    retrieval_dir: Path | str | None = None,
) -> dict[str, Any]:
    """ECL 旁路主入口 — 三步编排, 三 flag 独立控制.

    Args:
        report_date: 当日日期 (YYYY-MM-DD).
        db_path: EventStore 数据库路径.
        decisions_path: decisions.jsonl 路径.
        retrieval_dir: 检索报告输出目录.

    Returns:
        各步结果 {reconcile, derive, retrieval}.
    """
    db = Path(db_path) if db_path else _DEFAULT_DB
    dec = Path(decisions_path) if decisions_path else _DEFAULT_DECISIONS
    ret = Path(retrieval_dir) if retrieval_dir else _DEFAULT_RETRIEVAL_DIR

    result: dict[str, Any] = {"success": True, "date": report_date}

    if is_enabled("USE_ECL_EVENT_LOG"):
        try:
            result["reconcile"] = _reconcile_events(report_date, db, dec)
        except Exception as e:  # noqa: BLE001 — fail-open
            result["reconcile"] = {
                "step": "reconcile",
                "success": False,
                "error": str(e),
            }
    else:
        result["reconcile"] = {"step": "reconcile", "skipped": "USE_ECL_EVENT_LOG off"}

    if is_enabled("USE_ECL_EXPERIENCE"):
        try:
            result["derive"] = _derive_experiences(report_date, db)
        except Exception as e:  # noqa: BLE001 — fail-open
            result["derive"] = {"step": "derive", "success": False, "error": str(e)}
    else:
        result["derive"] = {"step": "derive", "skipped": "USE_ECL_EXPERIENCE off"}

    if is_enabled("USE_ECL_RETRIEVAL"):
        try:
            result["retrieval"] = _record_retrieval(report_date, db, ret)
        except Exception as e:  # noqa: BLE001 — fail-open
            result["retrieval"] = {
                "step": "retrieval",
                "success": False,
                "error": str(e),
            }
    else:
        result["retrieval"] = {"step": "retrieval", "skipped": "USE_ECL_RETRIEVAL off"}

    return result


def run_phase4_95_ecl_bypass(report_date: str, eod_summary: dict, args: Any) -> bool:
    """EOD 阶段 4.95 入口 — fail-open 旁路.

    在进化编排 (4.9) 之后、归档 (5) 之前执行.
    任何异常不增加 fail_count (旁路性质, 非 EOD 核心阶段).
    """
    if getattr(args, "skip_ecl", False):
        eod_summary["phases"]["phase4_95_ecl_bypass"] = {"skipped": True}
        return True
    try:
        result = run_ecl_bypass(report_date=report_date)
        eod_summary["phases"]["phase4_95_ecl_bypass"] = result
        return True
    except Exception as e:  # noqa: BLE001 — fail-open 旁路
        logger.warning("ECL 旁路异常(不影响主流程): %s", e)
        eod_summary["phases"]["phase4_95_ecl_bypass"] = {
            "success": False,
            "error": str(e),
        }
        return True
