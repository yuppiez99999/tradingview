"""回填 ECL EventStore — CTX-A1 T4.

从 decisions.jsonl + observation_alert_*.json 回填历史事件到 EventStore.

设计 (spec_CTX-A1.md §2.4):
    - 逐行读 decisions.jsonl, 按映射规则写入, schema_version=1, ts 用原始 timestamp 不重排
    - 同日 strategy_eval 的 market_context: 向前查最近一条 regime_shift_suggestion 的 indicators (≤24h)
    - 幂等: (ts, event_type, subject) 唯一索引去重, 脚本可重跑
    - observation_alert → data_gap_alert; vol_regime_weights_*.json 跳过

用法:
    python scripts/backfill_ecl_events.py [--dry-run] [--db-path PATH]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

from utils.infra.ecl.event_store import EventStore
from utils.infra.ecl.sinks import _map_record_to_event

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DECISIONS = _PROJECT_ROOT / "reports" / "evolution" / "decisions.jsonl"
_DEFAULT_ALERTS_DIR = _PROJECT_ROOT / "reports" / "evolution"
_DEFAULT_DB = _PROJECT_ROOT / "data" / "ecl" / "ecl_events.db"


def _parse_iso(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def backfill_decisions(
    store: EventStore,
    decisions_path: Path,
    dry_run: bool = False,
) -> dict[str, int]:
    """从 decisions.jsonl 回填事件.

    Returns:
        统计 {total, parsed, inserted, skipped_dup, parse_fail}.
    """
    stats = {"total": 0, "parsed": 0, "inserted": 0, "skipped_dup": 0, "parse_fail": 0}
    if not decisions_path.exists():
        logger.warning("decisions.jsonl 不存在: %s", decisions_path)
        return stats

    regime_events: list[dict] = []

    with decisions_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            stats["total"] += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                stats["parse_fail"] += 1
                continue
            stats["parsed"] += 1

            ts = record.get("timestamp", "")
            event_type, subject, payload = _map_record_to_event(record)

            if event_type == "regime_shift_suggestion":
                regime_events.append({"ts": ts, "payload": payload})

            if event_type == "strategy_eval":
                market_ctx = _join_market_context(ts, regime_events)
                if market_ctx:
                    payload["market_context"] = market_ctx

            before = store.count()
            if not dry_run:
                store.append(event_type, subject, payload, ts=ts, schema_version=1)
                after = store.count()
                if after > before:
                    stats["inserted"] += 1
                else:
                    stats["skipped_dup"] += 1
            else:
                stats["inserted"] += 1

    return stats


def _join_market_context(
    ts: str, regime_events: list[dict], max_hours: int = 24
) -> dict | None:
    """向前查最近一条 regime_shift_suggestion 的 indicators (≤24h 有效)."""
    target = _parse_iso(ts)
    if target is None:
        return None
    cutoff = target - timedelta(hours=max_hours)
    best: dict | None = None
    best_ts: datetime | None = None
    for ev in regime_events:
        ev_ts = _parse_iso(ev["ts"])
        if ev_ts is None:
            continue
        if ev_ts > target:
            continue
        if ev_ts < cutoff:
            continue
        if best_ts is None or ev_ts > best_ts:
            best = ev["payload"]
            best_ts = ev_ts
    if best is None:
        return None
    return {
        "vix": best.get("indicators", {}).get("vix"),
        "realized_vol": best.get("indicators", {}).get("realized_vol"),
        "current_drawdown": best.get("indicators", {}).get("current_drawdown"),
        "regime_label": best.get("regime", {}).get("label"),
    }


def backfill_observation_alerts(
    store: EventStore,
    alerts_dir: Path,
    dry_run: bool = False,
) -> dict[str, int]:
    """从 observation_alert_*.json 回填 data_gap_alert 事件.

    Returns:
        统计 {total, inserted, skipped_dup, parse_fail}.
    """
    stats = {"total": 0, "inserted": 0, "skipped_dup": 0, "parse_fail": 0}
    pattern = "observation_alert_*.json"
    for alert_file in sorted(alerts_dir.glob(pattern)):
        stats["total"] += 1
        try:
            with alert_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            stats["parse_fail"] += 1
            continue

        date_str = alert_file.stem.replace("observation_alert_", "")
        ts = f"{date_str}T00:00:00"
        subject = "feeder"
        payload = {
            "alert_type": data.get("type", "observation_data_missing"),
            "source_file": alert_file.name,
            "details": data,
        }

        before = store.count()
        if not dry_run:
            store.append("data_gap_alert", subject, payload, ts=ts, schema_version=1)
            after = store.count()
            if after > before:
                stats["inserted"] += 1
            else:
                stats["skipped_dup"] += 1
        else:
            stats["inserted"] += 1

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="回填 ECL EventStore")
    parser.add_argument("--dry-run", action="store_true", help="仅统计不写入")
    parser.add_argument("--db-path", type=Path, default=_DEFAULT_DB)
    parser.add_argument("--decisions", type=Path, default=_DEFAULT_DECISIONS)
    parser.add_argument("--alerts-dir", type=Path, default=_DEFAULT_ALERTS_DIR)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    store = EventStore(args.db_path)
    logger.info("EventStore: %s (现有 %d 条)", args.db_path, store.count())

    dec_stats = backfill_decisions(store, args.decisions, dry_run=args.dry_run)
    logger.info("decisions.jsonl 回填: %s", dec_stats)

    alert_stats = backfill_observation_alerts(
        store, args.alerts_dir, dry_run=args.dry_run
    )
    logger.info("observation_alert 回填: %s", alert_stats)

    final = store.stats()
    logger.info("最终统计: total=%d, by_type=%s", final["total"], final["by_type"])

    return 0


if __name__ == "__main__":
    sys.exit(main())
