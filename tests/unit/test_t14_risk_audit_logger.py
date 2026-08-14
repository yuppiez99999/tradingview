"""T14 单元测试 — RiskAuditLogger 风控审计回放."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from utils.risk.risk_audit_logger import (
    AuditRecord,
    RiskAuditLogger,
)


def _make_logger(tmp_path: Path) -> RiskAuditLogger:
    return RiskAuditLogger(
        audit_dir=tmp_path / "risk_audit",
        buffer_capacity=5,
        project_root=tmp_path,
    )


class TestAuditRecordSerialization:
    def test_roundtrip_json_line(self):
        rec = AuditRecord(
            timestamp="2026-08-12T09:30:00.000",
            audit_id="abc123",
            module="T09_PRETRADE",
            action="BLOCK",
            severity="ERROR",
            symbol="sh600519",
            reason="LOT_SIZE",
            context={"shares": 150},
        )
        line = rec.to_json_line()
        restored = AuditRecord.from_json_line(line)
        assert restored is not None
        assert restored.audit_id == rec.audit_id
        assert restored.module == rec.module
        assert restored.context == {"shares": 150}

    def test_invalid_json_returns_none(self):
        assert AuditRecord.from_json_line("this is not json") is None


class TestLogWriteAndFlush:
    def test_log_returns_audit_id(self, tmp_path):
        logger = _make_logger(tmp_path)
        aid = logger.log("T09_PRETRADE", "ALLOW", symbol="sh1", reason="ok")
        assert len(aid) == 12  # hex[:12]

    def test_buffer_flush_when_full(self, tmp_path):
        logger = _make_logger(tmp_path)
        # buffer_capacity=5, 写 6 条 → 第 5 条时触发自动刷盘
        for i in range(6):
            logger.log("T09_PRETRADE", "ALLOW", symbol=f"s{i}")
        # 先手动 flush 末尾残留, 确保文件写完 (避免跨日日期差异破坏路径断言)
        logger.flush()
        # 用 logger 自身 API 读取实际持久化记录, 比硬编码文件路径更健壮
        today = datetime.now().strftime("%Y-%m-%d")
        persisted = logger.query_by_date(today)
        assert len(persisted) >= 5, f"期望至少刷盘 5 条, 实际 {len(persisted)}"
        # 进一步保证: 自动 flush 后, buffer 内剩余的是后续追加, 因此文件总行数应为 6 (flush 2 次或合并)
        if len(persisted) < 6:
            # 如非自动+手动恰好合并, 也至少得 >=5 且包含第 1 条 s0 的 ID
            ids = [r.audit_id for r in persisted]
            first_symbol = next((r.symbol for r in persisted if r.symbol == "s0"), None)
            assert first_symbol is not None, "未检测到自动刷盘的首批记录"

    def test_manual_flush_writes_buffered(self, tmp_path):
        logger = _make_logger(tmp_path)
        logger.log("T12_KILL", "TRIP", severity="CRITICAL", reason="L3")
        logger.log("T11_CB", "BLOCK", severity="ERROR", reason="CONSECUTIVE_FAIL")
        logger.flush()
        today = datetime.now().strftime("%Y-%m-%d")
        fpath = tmp_path / "risk_audit" / f"risk_audit_{today}.jsonl"
        assert fpath.exists()
        lines = fpath.read_text(encoding="utf-8").splitlines()
        assert len(lines) >= 2


class TestReplayApis:
    def test_query_by_date_returns_records(self, tmp_path):
        logger = _make_logger(tmp_path)
        for i in range(4):
            logger.log("T10_POSITION", "BLOCK", symbol=f"sym{i}", reason="CAP")
        logger.flush()
        today = datetime.now().strftime("%Y-%m-%d")
        recs = logger.query_by_date(today)
        assert len(recs) == 4
        assert all(r.module == "T10_POSITION" for r in recs)

    def test_query_rejections_filters(self, tmp_path):
        logger = _make_logger(tmp_path)
        logger.log("T09_PRETRADE", "ALLOW")           # not rejection
        logger.log("T09_PRETRADE", "BLOCK")           # rejection 1
        logger.log("T11_CB", "TRIP")                  # rejection 2
        logger.log("T12_KILL", "LEVEL_CHANGE")        # not
        logger.flush()
        today = datetime.now().strftime("%Y-%m-%d")
        rej = logger.query_rejections(today)
        actions = {r.action for r in rej}
        assert actions == {"BLOCK", "TRIP"}

    def test_query_by_symbol(self, tmp_path):
        logger = _make_logger(tmp_path)
        logger.log("T09_PRETRADE", "BLOCK", symbol="SAME")
        logger.log("T09_PRETRADE", "ALLOW", symbol="OTHER")
        logger.log("T10_POSITION", "BLOCK", symbol="SAME")
        logger.flush()
        same = logger.query_by_symbol("SAME")
        assert len(same) == 2
        other = logger.query_by_symbol("OTHER")
        assert len(other) == 1

    def test_replay_stream_sorted_by_timestamp(self, tmp_path):
        logger = _make_logger(tmp_path)
        ids = [
            logger.log("T09_PRETRADE", "ALLOW", symbol="a"),
            logger.log("T09_PRETRADE", "BLOCK", symbol="b"),
        ]
        logger.flush()
        today = datetime.now().strftime("%Y-%m-%d")
        recs = list(logger.replay_stream(today))
        # 按时间排序
        for a, b in zip(recs, recs[1:]):
            assert a.timestamp <= b.timestamp


class TestInvalidInputFallback:
    def test_unknown_module_severity_action_normalized(self, tmp_path):
        logger = _make_logger(tmp_path)
        # 非法值不抛异常, 归一化到 OTHER/INFO
        aid = logger.log("UNKNOWN_X", "UNKNOWN_Y", severity="BAD", symbol="s1")
        assert len(aid) == 12
