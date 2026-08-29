"""EventStore 单测 — CTX-A1 T2.

覆盖: 不可变、全序、as_of 边界、compensation 视图级覆盖、幂等、单条<10ms 基准.

依据: docs/集成记录/Wave10/CTX-A/spec_CTX-A1.md §4 验收标准
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from pathlib import Path

import pytest

from utils.infra.ecl.event_store import EventStore


@pytest.fixture()
def store(tmp_path: Path) -> EventStore:
    return EventStore(tmp_path / "test_ecl.db")


class TestEventStoreSchema:
    """T1: 表结构 + pragma + 唯一索引."""

    def test_schema_created_idempotent(self, tmp_path: Path) -> None:
        db = tmp_path / "ecl.db"
        s1 = EventStore(db)
        s2 = EventStore(db)
        assert s1.count() == 0
        assert s2.count() == 0

    def test_wal_journal_mode(self, store: EventStore) -> None:
        with sqlite3.connect(str(store.db_path)) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"

    def test_busy_timeout_set(self, store: EventStore) -> None:
        with sqlite3.connect(str(store.db_path)) as conn:
            timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert timeout == 5000

    def test_unique_index_exists(self, store: EventStore) -> None:
        with sqlite3.connect(str(store.db_path)) as conn:
            idxs = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_ecl_events_unique'"
            ).fetchall()
        assert len(idxs) == 1

    def test_no_update_delete_code_paths(self) -> None:
        """R1: 不可变性 — event_store.py 无 UPDATE/DELETE 代码路径."""
        import utils.infra.ecl.event_store as mod

        source = Path(mod.__file__).read_text(encoding="utf-8")
        lines = source.splitlines()
        violations: list[str] = []
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "UPDATE" in stripped.upper() and "ecl_events" in stripped.upper():
                violations.append(f"line {i}: {line}")
            if "DELETE" in stripped.upper() and "ecl_events" in stripped.upper():
                violations.append(f"line {i}: {line}")
        assert violations == [], f"发现 UPDATE/DELETE 代码路径: {violations}"


class TestEventStoreAppend:
    """T2: append API."""

    def test_append_returns_event_id(self, store: EventStore) -> None:
        eid = store.append("decision", "portfolio", {"action": "buy"})
        assert isinstance(eid, int)
        assert eid > 0

    def test_append_monotonic_id(self, store: EventStore) -> None:
        id1 = store.append("decision", "portfolio", {"a": 1}, ts="2026-01-01T10:00:00")
        id2 = store.append("decision", "portfolio", {"a": 2}, ts="2026-01-01T11:00:00")
        assert id2 > id1

    def test_append_idempotent_same_ts_type_subject(self, store: EventStore) -> None:
        ts = "2026-01-01T10:00:00"
        id1 = store.append("decision", "portfolio", {"a": 1}, ts=ts)
        id2 = store.append("decision", "portfolio", {"a": 2}, ts=ts)
        assert id1 == id2
        assert store.count() == 1

    def test_append_different_subject_not_dedup(self, store: EventStore) -> None:
        ts = "2026-01-01T10:00:00"
        id1 = store.append("decision", "portfolio", {"a": 1}, ts=ts)
        id2 = store.append("decision", "ticker_600519", {"a": 1}, ts=ts)
        assert id1 != id2
        assert store.count() == 2

    def test_append_ts_defaults_to_now(self, store: EventStore) -> None:
        before = time.time()
        eid = store.append("decision", "portfolio", {"x": 1})
        after = time.time()
        ev = store.get_by_id(eid)
        assert ev is not None
        assert "T" in ev.ts
        # 默认 ts 必须落在本次调用窗口内（防硬编码时间戳 / 时区错位）
        ts_epoch = datetime.fromisoformat(ev.ts).timestamp()
        assert before - 1.0 <= ts_epoch <= after + 1.0

    def test_append_payload_json_roundtrip(self, store: EventStore) -> None:
        payload = {
            "vix": 21.3,
            "weights": {"stock": 0.6, "bond": 0.4},
            "list": [1, 2, 3],
        }
        eid = store.append(
            "regime_shift_suggestion", "portfolio", payload, ts="2026-01-01T10:00:00"
        )
        ev = store.get_by_id(eid)
        assert ev is not None
        assert ev.payload == payload

    def test_append_under_10ms(self, store: EventStore) -> None:
        """R3: 单条写入 <10ms 基准."""
        elapsed: list[float] = []
        for i in range(100):
            t0 = time.perf_counter()
            store.append(
                "decision", "portfolio", {"i": i}, ts=f"2026-01-01T10:{i:02d}:00"
            )
            elapsed.append((time.perf_counter() - t0) * 1000)
        avg_ms = sum(elapsed) / len(elapsed)
        assert avg_ms < 10.0, f"平均写入 {avg_ms:.2f}ms >= 10ms"


class TestEventStoreReplay:
    """T2: replay API."""

    def test_replay_returns_all_when_no_filter(self, store: EventStore) -> None:
        store.append("decision", "portfolio", {"a": 1}, ts="2026-01-01T10:00:00")
        store.append("decision", "portfolio", {"a": 2}, ts="2026-01-01T11:00:00")
        events = store.replay()
        assert len(events) == 2

    def test_replay_ordered_by_id(self, store: EventStore) -> None:
        store.append("decision", "portfolio", {"a": 1}, ts="2026-01-01T10:00:00")
        store.append("decision", "portfolio", {"a": 2}, ts="2026-01-01T11:00:00")
        store.append("decision", "portfolio", {"a": 3}, ts="2026-01-01T12:00:00")
        events = store.replay()
        ids = [e.id for e in events]
        assert ids == sorted(ids)

    def test_replay_as_of_inclusive_boundary(self, store: EventStore) -> None:
        store.append("decision", "portfolio", {"a": 1}, ts="2026-01-01T10:00:00")
        store.append("decision", "portfolio", {"a": 2}, ts="2026-01-01T11:00:00")
        store.append("decision", "portfolio", {"a": 3}, ts="2026-01-01T12:00:00")
        events = store.replay(as_of="2026-01-01T11:00:00")
        assert len(events) == 2
        assert events[0].payload["a"] == 1
        assert events[1].payload["a"] == 2

    def test_replay_filter_event_type(self, store: EventStore) -> None:
        store.append("decision", "portfolio", {"a": 1}, ts="2026-01-01T10:00:00")
        store.append("drift_alert", "feeder", {"b": 2}, ts="2026-01-01T11:00:00")
        events = store.replay(event_type="drift_alert")
        assert len(events) == 1
        assert events[0].event_type == "drift_alert"

    def test_replay_filter_subject(self, store: EventStore) -> None:
        store.append("decision", "portfolio", {"a": 1}, ts="2026-01-01T10:00:00")
        store.append("decision", "ticker_600519", {"a": 2}, ts="2026-01-01T11:00:00")
        events = store.replay(subject="ticker_600519")
        assert len(events) == 1
        assert events[0].subject == "ticker_600519"


class TestEventStoreCompensation:
    """T2: compensation 视图级覆盖 (评审点②已确认)."""

    def test_append_compensation_returns_id(self, store: EventStore) -> None:
        eid = store.append("decision", "portfolio", {"a": 1}, ts="2026-01-01T10:00:00")
        cid = store.append_compensation(eid, "data error")
        assert cid > eid
        assert cid > 0

    def test_compensation_physical_row_never_deleted(self, store: EventStore) -> None:
        eid = store.append("decision", "portfolio", {"a": 1}, ts="2026-01-01T10:00:00")
        store.append_compensation(eid, "data error", ts="2026-01-01T12:00:00")
        ev = store.get_by_id(eid)
        assert ev is not None
        assert ev.payload == {"a": 1}

    def test_replay_compensation_view_level_override(self, store: EventStore) -> None:
        eid = store.append("decision", "portfolio", {"a": 1}, ts="2026-01-01T10:00:00")
        store.append("decision", "portfolio", {"a": 2}, ts="2026-01-01T11:00:00")
        store.append_compensation(eid, "wrong data", ts="2026-01-01T12:00:00")
        events = store.replay()
        ids = [e.id for e in events]
        assert eid not in ids
        assert store.count() == 3

    def test_replay_compensation_excludes_correction_events(
        self, store: EventStore
    ) -> None:
        eid = store.append("decision", "portfolio", {"a": 1}, ts="2026-01-01T10:00:00")
        store.append_compensation(eid, "fix", ts="2026-01-01T12:00:00")
        events = store.replay()
        assert all(e.event_type != "correction" for e in events)

    def test_replay_as_of_before_compensation_keeps_original(
        self, store: EventStore
    ) -> None:
        eid = store.append("decision", "portfolio", {"a": 1}, ts="2026-01-01T10:00:00")
        store.append_compensation(eid, "fix", ts="2026-01-01T12:00:00")
        events = store.replay(as_of="2026-01-01T11:00:00")
        assert len(events) == 1
        assert events[0].id == eid


class TestEventStoreStats:
    """T2: stats / count / get_by_id."""

    def test_count(self, store: EventStore) -> None:
        assert store.count() == 0
        store.append("decision", "portfolio", {"a": 1}, ts="2026-01-01T10:00:00")
        assert store.count() == 1
        store.append("drift_alert", "feeder", {"b": 2}, ts="2026-01-01T11:00:00")
        assert store.count() == 2

    def test_stats_by_type(self, store: EventStore) -> None:
        store.append("decision", "portfolio", {"a": 1}, ts="2026-01-01T10:00:00")
        store.append("decision", "portfolio", {"a": 2}, ts="2026-01-01T11:00:00")
        store.append("drift_alert", "feeder", {"b": 2}, ts="2026-01-01T12:00:00")
        s = store.stats()
        assert s["total"] == 3
        assert s["by_type"]["decision"] == 2
        assert s["by_type"]["drift_alert"] == 1

    def test_get_by_id_not_found(self, store: EventStore) -> None:
        assert store.get_by_id(99999) is None
