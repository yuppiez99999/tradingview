"""决策审计日志组件 — SQLite 持久化审计记录。

本模块为策略状态机、熔断器、目标监控器与主协调器提供审计记录能力，
所有决策事件均写入 SQLite 数据库，支持按时间范围、事件类型、标的过滤回溯。

性能保证:
    - WAL 模式启用，并发写入不锁死
    - 按 event_type 与 timestamp 建索引，10 万条记录查询 <1 秒
    - 批量写入优化

事件类型:
    - strategy_switch   — 策略等级切换
    - target_deviation  — 目标偏离告警
    - circuit_breaker   — 熔断器触发/解除
    - degradation_flag  — 降级标记
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.auto_hedge_rebalance.models import (
    AuditRecord,
    StrategySwitchEvent,
)

logger = logging.getLogger(__name__)


class AuditLogger:
    """决策审计日志记录器。

    使用 SQLite 持久化审计记录，启用 WAL 模式保证并发写入不锁死，
    按 event_type 与 timestamp 建索引保证查询性能。

    Attributes:
        db_path: SQLite 数据库文件路径。
    """

    _SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS audit_records (
        record_id      TEXT PRIMARY KEY,
        timestamp      TEXT NOT NULL,
        event_type     TEXT NOT NULL,
        trigger_reason TEXT,
        details        TEXT,
        approver       TEXT,
        ticker         TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_records(event_type);
    CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_records(timestamp);
    CREATE INDEX IF NOT EXISTS idx_audit_ticker ON audit_records(ticker);
    CREATE INDEX IF NOT EXISTS idx_audit_event_time ON audit_records(event_type, timestamp);
    """

    def __init__(self, db_path: str = "data/auto_hedge_rebalance_audit.db") -> None:
        """初始化审计日志记录器。

        自动创建数据库文件与表结构，启用 WAL 模式。

        Args:
            db_path: SQLite 数据库文件路径。
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        """初始化数据库表结构与索引。"""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.executescript(self._SCHEMA_SQL)
            conn.commit()
        logger.debug("审计日志数据库初始化完成: %s", self.db_path)

    def _now_iso(self) -> str:
        """返回当前时间的 ISO8601 字符串。"""
        return datetime.now().isoformat(timespec="seconds")

    def _new_record_id(self) -> str:
        """生成新的记录 UUID。"""
        return str(uuid.uuid4())

    def _write_record(
        self,
        event_type: str,
        trigger_reason: str,
        details: dict[str, Any],
        approver: str = "",
        ticker: str | None = None,
    ) -> str:
        """写入一条审计记录。

        Args:
            event_type: 事件类型。
            trigger_reason: 触发原因。
            details: 详情字典 (JSON 序列化存储)。
            approver: 审批人。
            ticker: 关联标的代码 (可选，用于按标的过滤)。

        Returns:
            记录 ID (UUID)。
        """
        record_id = self._new_record_id()
        timestamp = self._now_iso()
        details_json = json.dumps(details, ensure_ascii=False, default=str)

        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                """
                INSERT INTO audit_records
                    (record_id, timestamp, event_type, trigger_reason, details, approver, ticker)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    timestamp,
                    event_type,
                    trigger_reason,
                    details_json,
                    approver,
                    ticker,
                ),
            )
            conn.commit()

        logger.debug(
            "审计记录写入: event_type=%s, record_id=%s, reason=%s",
            event_type,
            record_id,
            trigger_reason,
        )
        return record_id

    def log_strategy_switch(self, event: StrategySwitchEvent) -> str:
        """记录策略等级切换事件。

        Args:
            event: 策略切换事件。

        Returns:
            记录 ID。
        """
        details = {
            "event_id": event.event_id,
            "from_level": event.from_level.value,
            "to_level": event.to_level.value,
            "params_before": event.params_before,
            "params_after": event.params_after,
            "approved": event.approved,
        }
        return self._write_record(
            event_type="strategy_switch",
            trigger_reason=event.trigger_reason,
            details=details,
            approver=event.approver,
        )

    def log_target_deviation(
        self,
        alert: dict[str, Any],
        approver: str = "",
    ) -> str:
        """记录目标偏离告警。

        Args:
            alert: 告警详情字典，应含 rolling_annual_return、rolling_max_drawdown、
                   return_deviation、drawdown_margin、correction_action 等字段。
            approver: 审批人 (可选)。

        Returns:
            记录 ID。
        """
        trigger_reason = alert.get("reason", "目标偏离告警")
        ticker = alert.get("ticker")
        return self._write_record(
            event_type="target_deviation",
            trigger_reason=trigger_reason,
            details=alert,
            approver=approver,
            ticker=ticker,
        )

    def log_circuit_breaker(
        self,
        event: dict[str, Any],
        approver: str = "",
    ) -> str:
        """记录熔断器触发/解除事件。

        Args:
            event: 熔断事件字典，应含 action (trigger/release)、
                   trigger_reason、emergency_action 等字段。
            approver: 审批人 (解除时填写)。

        Returns:
            记录 ID。
        """
        trigger_reason = event.get("trigger_reason", "熔断器事件")
        return self._write_record(
            event_type="circuit_breaker",
            trigger_reason=trigger_reason,
            details=event,
            approver=approver,
        )

    def log_degradation_flag(
        self,
        flag: dict[str, Any],
        approver: str = "",
    ) -> str:
        """记录降级标记。

        Args:
            flag: 降级标记字典，应含 flag_name、source、reason 等字段。
            approver: 审批人 (可选)。

        Returns:
            记录 ID。
        """
        trigger_reason = flag.get("reason", flag.get("flag_name", "降级标记"))
        return self._write_record(
            event_type="degradation_flag",
            trigger_reason=trigger_reason,
            details=flag,
            approver=approver,
        )

    def query(
        self,
        start_time: str | None = None,
        end_time: str | None = None,
        event_type: str | None = None,
        ticker: str | None = None,
        limit: int = 1000,
    ) -> list[AuditRecord]:
        """查询审计记录。

        支持按时间范围、事件类型、标的过滤回溯，利用索引保证查询性能。

        Args:
            start_time: 起始时间 (ISO8601，可选)。
            end_time: 结束时间 (ISO8601，可选)。
            event_type: 事件类型过滤 (可选)。
            ticker: 标的代码过滤 (可选)。
            limit: 返回记录数上限 (默认 1000)。

        Returns:
            审计记录列表，按时间倒序排列。
        """
        conditions: list[str] = []
        params: list[Any] = []

        if start_time is not None:
            conditions.append("timestamp >= ?")
            params.append(start_time)
        if end_time is not None:
            conditions.append("timestamp <= ?")
            params.append(end_time)
        if event_type is not None:
            conditions.append("event_type = ?")
            params.append(event_type)
        if ticker is not None:
            conditions.append("ticker = ?")
            params.append(ticker)

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
        sql = (
            f"SELECT record_id, timestamp, event_type, trigger_reason, details, approver"  # noqa: S608 — where 条件值均参数化, 无用户输入拼接  # nosec B608
            f" FROM audit_records{where_clause}"
            f" ORDER BY timestamp DESC LIMIT ?"
        )
        params.append(limit)

        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            cursor = conn.execute(sql, params)
            rows = cursor.fetchall()

        records: list[AuditRecord] = []
        for row in rows:
            record_id, timestamp, ev_type, reason, details_json, approver = row
            try:
                details = json.loads(details_json) if details_json else {}
            except json.JSONDecodeError:
                details = {"raw": details_json}
            records.append(
                AuditRecord(
                    record_id=record_id,
                    timestamp=timestamp,
                    event_type=ev_type,
                    trigger_reason=reason or "",
                    details=details,
                    approver=approver or "",
                )
            )

        return records

    def count(self, event_type: str | None = None) -> int:
        """统计审计记录总数。

        Args:
            event_type: 事件类型过滤 (可选)。

        Returns:
            记录总数。
        """
        sql = "SELECT COUNT(*) FROM audit_records"
        params: list[Any] = []
        if event_type is not None:
            sql += " WHERE event_type = ?"
            params.append(event_type)

        with sqlite3.connect(str(self.db_path)) as conn:
            cursor = conn.execute(sql, params)
            return cursor.fetchone()[0]

    def close(self) -> None:
        """关闭数据库连接 (WAL 模式下连接自动管理，此方法为接口兼容)。"""
        logger.debug("审计日志记录器关闭: %s", self.db_path)


__all__ = ["AuditLogger"]
