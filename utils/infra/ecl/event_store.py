"""EventStore — append-only 事件日志 (CTX-A1).

Wave10-CTX Phase A 的 L1 层: 事件源 (Event Sourcing) 唯一可信历史.

设计原则:
    - R1: append-only, 无 UPDATE/DELETE 代码路径; 纠错 = compensation 事件
    - R2: 单调递增 id 全序; replay(as_of) 可重建任意时点状态视图
    - R3: 单条写入 <10ms (EOD 单进程场景)
    - 幂等: (ts, event_type, subject) 唯一索引去重, 脚本可重跑

compensation 语义 (评审点②已确认): 视图级 — 物理行永不删,
    回放时按 payload.corrected_event_id 覆盖被纠正事件.

依据: docs/集成记录/Wave10/CTX-A/spec_CTX-A1.md
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ecl_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT    NOT NULL,
    event_type    TEXT    NOT NULL,
    subject       TEXT    NOT NULL,
    payload       TEXT    NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ecl_events_unique
    ON ecl_events(ts, event_type, subject);
CREATE INDEX IF NOT EXISTS idx_ecl_events_type ON ecl_events(event_type);
CREATE INDEX IF NOT EXISTS idx_ecl_events_ts ON ecl_events(ts);
"""

_PRAGMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;
PRAGMA synchronous=NORMAL;
"""


@dataclass(frozen=True)
class Event:
    """不可变事件记录."""

    id: int
    ts: str
    event_type: str
    subject: str
    payload: dict[str, Any]
    schema_version: int
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Event:
        return cls(
            id=row["id"],
            ts=row["ts"],
            event_type=row["event_type"],
            subject=row["subject"],
            payload=json.loads(row["payload"]),
            schema_version=row["schema_version"],
            created_at=row["created_at"],
        )


class EventStore:
    """Append-only 事件存储.

    所有写入仅 INSERT, 物理行永不删.
    纠错通过 append_compensation 追加 correction 事件,
    replay 时按 corrected_event_id 在视图层覆盖.

    Args:
        db_path: SQLite 数据库文件路径.
    """

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.executescript(_PRAGMA_SQL)
            conn.executescript(_SCHEMA_SQL)
            conn.commit()

    def _now_iso(self) -> str:
        return datetime.now().isoformat(timespec="seconds")

    def append(
        self,
        event_type: str,
        subject: str,
        payload: dict[str, Any],
        ts: str | None = None,
        schema_version: int = 1,
    ) -> int:
        """追加一条事件, 返回 event_id.

        幂等: (ts, event_type, subject) 已存在时返回既有 id, 不重复插入.

        Args:
            event_type: 事件类型 (decision/drift_alert/regime_shift_suggestion/...).
            subject: 事件主体 (portfolio/ticker/...).
            payload: 事件负载 (JSON 序列化存储).
            ts: 事件时间戳 (ISO8601); None 则取当前时间.
            schema_version: schema 版本 (回填历史=1, 直写=1).

        Returns:
            event_id (自增主键).
        """
        if ts is None:
            ts = self._now_iso()
        payload_json = json.dumps(payload, ensure_ascii=False, default=str)
        created_at = self._now_iso()

        with sqlite3.connect(str(self.db_path)) as conn:
            conn.executescript(_PRAGMA_SQL)
            existing = conn.execute(
                "SELECT id FROM ecl_events WHERE ts=? AND event_type=? AND subject=?",
                (ts, event_type, subject),
            ).fetchone()
            if existing is not None:
                return existing[0]
            cursor = conn.execute(
                """
                INSERT INTO ecl_events (ts, event_type, subject, payload, schema_version, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (ts, event_type, subject, payload_json, schema_version, created_at),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def append_compensation(
        self,
        corrected_event_id: int,
        reason: str,
        ts: str | None = None,
    ) -> int:
        """追加一条 compensation (纠错) 事件.

        物理行永不删; replay 时按 corrected_event_id 在视图层覆盖被纠正事件.

        Args:
            corrected_event_id: 被纠正的事件 id.
            reason: 纠错原因.
            ts: 事件时间戳; None 则取当前时间.

        Returns:
            compensation 事件的 event_id.
        """
        payload = {
            "corrected_event_id": corrected_event_id,
            "reason": reason,
        }
        return self.append(
            event_type="correction",
            subject=f"event_{corrected_event_id}",
            payload=payload,
            ts=ts,
        )

    def replay(
        self,
        as_of: str | None = None,
        event_type: str | None = None,
        subject: str | None = None,
    ) -> list[Event]:
        """回放事件, 可重建任意时点状态视图.

        compensation 视图级覆盖: 物理行永不删, 回放时 correction 事件
        按 payload.corrected_event_id 覆盖被纠正事件 (从结果中移除).

        Args:
            as_of: 回放截止时间 (ISO8601, 含边界 <=); None 则全部.
            event_type: 过滤事件类型; None 则全部.
            subject: 过滤主体; None 则全部.

        Returns:
            事件列表 (按 id 升序), 已应用 compensation 视图级覆盖.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if as_of is not None:
            clauses.append("ts <= ?")
            params.append(as_of)
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type)
        if subject is not None:
            clauses.append("subject = ?")
            params.append(subject)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(_PRAGMA_SQL)
            rows = conn.execute(
                # where 仅由硬编码常量子句拼接, 所有值走 params 参数化
                f"SELECT * FROM ecl_events {where} ORDER BY id ASC",  # nosec B608
                params,
            ).fetchall()

        events = [Event.from_row(r) for r in rows]
        return self._apply_compensations(events)

    @staticmethod
    def _apply_compensations(events: list[Event]) -> list[Event]:
        """视图级 compensation 覆盖: 移除被 correction 纠正的事件."""
        corrected_ids: set[int] = set()
        corrections: list[Event] = []
        for ev in events:
            if ev.event_type == "correction":
                corrected_id = ev.payload.get("corrected_event_id")
                if corrected_id is not None:
                    corrected_ids.add(int(corrected_id))
                corrections.append(ev)

        if not corrected_ids:
            return events

        result = [
            ev
            for ev in events
            if ev.id not in corrected_ids and ev.event_type != "correction"
        ]
        return result

    def count(self) -> int:
        """返回事件总数 (含 correction, 不含视图级覆盖)."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.executescript(_PRAGMA_SQL)
            row = conn.execute("SELECT COUNT(*) FROM ecl_events").fetchone()
            return int(row[0])

    def get_by_id(self, event_id: int) -> Event | None:
        """按 id 查询单条事件."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(_PRAGMA_SQL)
            row = conn.execute(
                "SELECT * FROM ecl_events WHERE id = ?", (event_id,)
            ).fetchone()
            return Event.from_row(row) if row else None

    def stats(self) -> dict[str, Any]:
        """返回统计信息 (条目数/事件类型分布)."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.executescript(_PRAGMA_SQL)
            total = conn.execute("SELECT COUNT(*) FROM ecl_events").fetchone()[0]
            type_dist = dict(
                conn.execute(
                    "SELECT event_type, COUNT(*) FROM ecl_events GROUP BY event_type"
                ).fetchall()
            )
        return {
            "total": int(total),
            "by_type": {k: int(v) for k, v in type_dist.items()},
        }
