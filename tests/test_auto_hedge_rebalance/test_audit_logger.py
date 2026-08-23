"""AuditLogger 单元测试 — 覆盖四种事件写入、查询、WAL 模式、UUID 唯一性。

测试策略:
    - 使用临时数据库路径避免污染生产数据
    - AAA 模式 (Arrange-Act-Assert)
    - 覆盖率目标 ≥ 90%
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from uuid import UUID

import pytest

from utils.auto_hedge_rebalance.audit_logger import AuditLogger
from utils.auto_hedge_rebalance.models import (
    StrategyLevel,
    StrategySwitchEvent,
)


@pytest.fixture
def temp_db_path(tmp_path: Path) -> str:
    """提供临时数据库路径。"""
    return str(tmp_path / "test_audit.db")


@pytest.fixture
def audit_logger(temp_db_path: str) -> AuditLogger:
    """提供已初始化的 AuditLogger 实例。"""
    return AuditLogger(db_path=temp_db_path)


class TestAuditLoggerInit:
    """AuditLogger 初始化测试。"""

    def test_init_creates_db_file(self, temp_db_path: str) -> None:
        # Arrange & Act
        logger = AuditLogger(db_path=temp_db_path)
        # Assert
        assert Path(temp_db_path).exists()
        assert logger.db_path == Path(temp_db_path)

    def test_init_creates_table_and_indexes(self, temp_db_path: str) -> None:
        # Arrange
        AuditLogger(db_path=temp_db_path)
        # Act
        with sqlite3.connect(temp_db_path) as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_records'"
            )
            table_exists = cursor.fetchone() is not None
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_audit_event_type'"
            )
            index_exists = cursor.fetchone() is not None
        # Assert
        assert table_exists
        assert index_exists

    def test_init_enables_wal_mode(self, temp_db_path: str) -> None:
        # Arrange
        AuditLogger(db_path=temp_db_path)
        # Act
        with sqlite3.connect(temp_db_path) as conn:
            cursor = conn.execute("PRAGMA journal_mode;")
            mode = cursor.fetchone()[0]
        # Assert
        assert mode.lower() == "wal"

    def test_init_creates_parent_directory(self, tmp_path: Path) -> None:
        # Arrange
        db_path = str(tmp_path / "nested" / "deep" / "test_audit.db")
        # Act
        AuditLogger(db_path=db_path)
        # Assert
        assert Path(db_path).exists()


class TestAuditLoggerWrite:
    """AuditLogger 写入测试。"""

    def test_log_strategy_switch(self, audit_logger: AuditLogger) -> None:
        # Arrange
        event = StrategySwitchEvent(
            event_id="evt-001",
            timestamp="2026-08-22T10:00:00",
            from_level=StrategyLevel.NORMAL,
            to_level=StrategyLevel.MILD_CORRECTION,
            trigger_reason="收益偏离2.5%",
            params_before={"hedge_ratio": 0.0},
            params_after={"hedge_ratio": 0.15},
            approver="admin",
            approved=True,
        )
        # Act
        record_id = audit_logger.log_strategy_switch(event)
        # Assert
        assert record_id is not None
        assert len(record_id) > 0
        UUID(record_id)  # 验证是合法 UUID

    def test_log_target_deviation(self, audit_logger: AuditLogger) -> None:
        # Arrange
        alert = {
            "rolling_annual_return": 0.05,
            "rolling_max_drawdown": 0.12,
            "return_deviation": 0.03,
            "drawdown_margin": 0.08,
            "correction_action": "MILD_TUNE",
            "reason": "收益偏离目标3%",
            "ticker": "IF2409",
        }
        # Act
        record_id = audit_logger.log_target_deviation(alert)
        # Assert
        assert record_id is not None
        UUID(record_id)

    def test_log_circuit_breaker_trigger(self, audit_logger: AuditLogger) -> None:
        # Arrange
        event = {
            "action": "trigger",
            "trigger_reason": "单日跌幅5.1%",
            "emergency_action": "强制对冲至40%",
            "trigger_time": "2026-08-22T14:30:00",
        }
        # Act
        record_id = audit_logger.log_circuit_breaker(event)
        # Assert
        assert record_id is not None
        UUID(record_id)

    def test_log_circuit_breaker_release(self, audit_logger: AuditLogger) -> None:
        # Arrange
        event = {
            "action": "release",
            "trigger_reason": "管理员手动解除",
            "approver": "risk_manager",
        }
        # Act
        record_id = audit_logger.log_circuit_breaker(event, approver="risk_manager")
        # Assert
        assert record_id is not None

    def test_log_degradation_flag(self, audit_logger: AuditLogger) -> None:
        # Arrange
        flag = {
            "flag_name": "对冲降级",
            "source": "hedge_engine",
            "reason": "Wind MCP 不可用，降级至 AKShare",
        }
        # Act
        record_id = audit_logger.log_degradation_flag(flag)
        # Assert
        assert record_id is not None
        UUID(record_id)

    def test_uuid_uniqueness(self, audit_logger: AuditLogger) -> None:
        # Arrange
        flag = {"flag_name": "测试降级", "reason": "测试"}
        # Act
        ids = [audit_logger.log_degradation_flag(flag) for _ in range(100)]
        # Assert
        assert len(set(ids)) == 100  # 全部唯一


class TestAuditLoggerQuery:
    """AuditLogger 查询测试。"""

    def test_query_by_event_type(self, audit_logger: AuditLogger) -> None:
        # Arrange
        audit_logger.log_degradation_flag({"flag_name": "降级1", "reason": "测试1"})
        audit_logger.log_degradation_flag({"flag_name": "降级2", "reason": "测试2"})
        audit_logger.log_circuit_breaker({"action": "trigger", "trigger_reason": "测试"})
        # Act
        records = audit_logger.query(event_type="degradation_flag")
        # Assert
        assert len(records) == 2
        assert all(r.event_type == "degradation_flag" for r in records)

    def test_query_by_time_range(self, audit_logger: AuditLogger) -> None:
        # Arrange
        audit_logger.log_degradation_flag({"flag_name": "降级", "reason": "测试"})
        # Act
        records = audit_logger.query(
            start_time="2026-01-01T00:00:00",
            end_time="2026-12-31T23:59:59",
        )
        # Assert
        assert len(records) >= 1

    def test_query_by_ticker(self, audit_logger: AuditLogger) -> None:
        # Arrange
        audit_logger.log_target_deviation(
            {"reason": "偏离", "ticker": "IF2409"}, approver=""
        )
        audit_logger.log_target_deviation(
            {"reason": "偏离", "ticker": "IC2409"}, approver=""
        )
        # Act
        records = audit_logger.query(ticker="IF2409")
        # Assert
        assert len(records) == 1
        assert records[0].details.get("ticker") == "IF2409"

    def test_query_empty_database(self, audit_logger: AuditLogger) -> None:
        # Arrange & Act
        records = audit_logger.query()
        # Assert
        assert len(records) == 0

    def test_query_returns_records_in_desc_order(self, audit_logger: AuditLogger) -> None:
        # Arrange
        for i in range(5):
            audit_logger.log_degradation_flag({"flag_name": f"降级{i}", "reason": "测试"})
        # Act
        records = audit_logger.query()
        # Assert
        timestamps = [r.timestamp for r in records]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_query_limit(self, audit_logger: AuditLogger) -> None:
        # Arrange
        for i in range(10):
            audit_logger.log_degradation_flag({"flag_name": f"降级{i}", "reason": "测试"})
        # Act
        records = audit_logger.query(limit=3)
        # Assert
        assert len(records) == 3


class TestAuditLoggerCount:
    """AuditLogger 计数测试。"""

    def test_count_all(self, audit_logger: AuditLogger) -> None:
        # Arrange
        audit_logger.log_degradation_flag({"flag_name": "降级", "reason": "测试"})
        audit_logger.log_circuit_breaker({"trigger_reason": "测试"})
        # Act
        total = audit_logger.count()
        # Assert
        assert total == 2

    def test_count_by_event_type(self, audit_logger: AuditLogger) -> None:
        # Arrange
        audit_logger.log_degradation_flag({"flag_name": "降级1", "reason": "测试"})
        audit_logger.log_degradation_flag({"flag_name": "降级2", "reason": "测试"})
        audit_logger.log_circuit_breaker({"trigger_reason": "测试"})
        # Act
        count = audit_logger.count(event_type="degradation_flag")
        # Assert
        assert count == 2

    def test_count_empty(self, audit_logger: AuditLogger) -> None:
        # Arrange & Act
        total = audit_logger.count()
        # Assert
        assert total == 0


class TestAuditLoggerRecordContent:
    """AuditLogger 记录内容验证测试。"""

    def test_record_details_json_serialized(self, audit_logger: AuditLogger) -> None:
        # Arrange
        flag = {"flag_name": "对冲降级", "source": "hedge_engine", "level": 2}
        # Act
        record_id = audit_logger.log_degradation_flag(flag)
        records = audit_logger.query(event_type="degradation_flag")
        # Assert
        assert len(records) == 1
        assert records[0].details["flag_name"] == "对冲降级"
        assert records[0].details["level"] == 2

    def test_strategy_switch_record_content(self, audit_logger: AuditLogger) -> None:
        # Arrange
        event = StrategySwitchEvent(
            event_id="evt-002",
            timestamp="2026-08-22T11:00:00",
            from_level=StrategyLevel.NORMAL,
            to_level=StrategyLevel.MODERATE_CORRECTION,
            trigger_reason="收益偏离4.5%",
            approver="risk_manager",
            approved=True,
        )
        # Act
        audit_logger.log_strategy_switch(event)
        records = audit_logger.query(event_type="strategy_switch")
        # Assert
        assert len(records) == 1
        assert records[0].trigger_reason == "收益偏离4.5%"
        assert records[0].approver == "risk_manager"
        assert records[0].details["from_level"] == "NORMAL"
        assert records[0].details["to_level"] == "MODERATE_CORRECTION"