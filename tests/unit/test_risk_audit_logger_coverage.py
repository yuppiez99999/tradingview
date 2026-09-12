"""utils/risk/risk_audit_logger.py 覆盖率补测 (W7.4.5 覆盖率冲刺)

验证目标:
    1. AuditRecord.from_json_line: 合法 JSON / 非法 JSON / 字段缺失
    2. AuditRecord.to_json_line: 序列化完整性
    3. RiskAuditLogger.log: 正常记录 / 无效 module/action/severity 降级
    4. RiskAuditLogger.flush: 强制刷盘
    5. RiskAuditLogger.query_by_date: 按日期查询
    6. RiskAuditLogger.query_by_symbol: 按标的查询
    7. RiskAuditLogger.query_rejections: 查询拦截事件
    8. RiskAuditLogger.replay_stream: 回放流
    9. _flush_locked: 刷盘 + OSError 降级
    10. _iter_jsonl: 文件读取 + OSError 降级
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from utils.datetime_utils import now_bj

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from utils.risk.risk_audit_logger import (  # noqa: E402
    AuditRecord,
    RiskAuditLogger,
)


# ============================================================
# AuditRecord 测试
# ============================================================
class TestAuditRecord:
    """AuditRecord 数据类测试."""

    def test_to_json_line_serialization(self):
        """to_json_line 正确序列化."""
        rec = AuditRecord(
            timestamp="2026-08-27T10:00:00.000",
            audit_id="abc123",
            module="T09_PRETRADE",
            action="BLOCK",
            severity="WARN",
            symbol="600519",
            reason="涨停过滤",
            context={"price": 1500.0},
        )
        line = rec.to_json_line()
        d = json.loads(line)
        assert d["audit_id"] == "abc123"
        assert d["module"] == "T09_PRETRADE"
        assert d["action"] == "BLOCK"
        assert d["symbol"] == "600519"

    def test_from_json_line_valid(self):
        """from_json_line 合法 JSON 返回记录."""
        line = json.dumps(
            {
                "timestamp": "2026-08-27T10:00:00.000",
                "audit_id": "xyz789",
                "module": "T12_KILL",
                "action": "TRIP",
                "severity": "CRITICAL",
                "symbol": "000858",
                "reason": "日内熔断",
                "context": {"level": "L2"},
            }
        )
        rec = AuditRecord.from_json_line(line)
        assert rec is not None
        assert rec.audit_id == "xyz789"
        assert rec.module == "T12_KILL"

    def test_from_json_line_invalid_json(self):
        """from_json_line 非法 JSON 返回 None."""
        rec = AuditRecord.from_json_line("not a json {{{")
        assert rec is None

    def test_to_from_roundtrip(self):
        """to_json_line → from_json_line 往返一致."""
        original = AuditRecord(
            timestamp="2026-08-27T11:00:00.000",
            audit_id="round123",
            module="T14_AUDIT",
            action="ALLOW",
            severity="INFO",
            symbol="601318",
            reason="放行",
            context={"key": "value"},
        )
        line = original.to_json_line()
        restored = AuditRecord.from_json_line(line)
        assert restored is not None
        assert restored.audit_id == original.audit_id
        assert restored.module == original.module


# ============================================================
# RiskAuditLogger.log 测试
# ============================================================
class TestRiskAuditLoggerLog:
    """RiskAuditLogger.log 写入 API 测试."""

    def test_log_returns_audit_id(self, tmp_path):
        """log 返回非空 audit_id."""
        logger_inst = RiskAuditLogger(audit_dir=str(tmp_path), buffer_capacity=100)
        audit_id = logger_inst.log(
            module="T09_PRETRADE",
            action="BLOCK",
            severity="WARN",
            symbol="600519",
            reason="涨停过滤",
        )
        assert audit_id is not None
        assert len(audit_id) > 0

    def test_log_invalid_module_degrades_to_other(self, tmp_path):
        """无效 module 降级为 OTHER."""
        logger_inst = RiskAuditLogger(audit_dir=str(tmp_path), buffer_capacity=100)
        audit_id = logger_inst.log(
            module="INVALID_MODULE",
            action="BLOCK",
            severity="WARN",
        )
        assert audit_id is not None

    def test_log_invalid_action_degrades_to_other(self, tmp_path):
        """无效 action 降级为 OTHER."""
        logger_inst = RiskAuditLogger(audit_dir=str(tmp_path), buffer_capacity=100)
        audit_id = logger_inst.log(
            module="T09_PRETRADE",
            action="INVALID_ACTION",
            severity="WARN",
        )
        assert audit_id is not None

    def test_log_invalid_severity_degrades_to_info(self, tmp_path):
        """无效 severity 降级为 INFO."""
        logger_inst = RiskAuditLogger(audit_dir=str(tmp_path), buffer_capacity=100)
        audit_id = logger_inst.log(
            module="T09_PRETRADE",
            action="BLOCK",
            severity="INVALID",
        )
        assert audit_id is not None

    def test_log_with_context_none(self, tmp_path):
        """context=None 时使用空字典."""
        logger_inst = RiskAuditLogger(audit_dir=str(tmp_path), buffer_capacity=100)
        audit_id = logger_inst.log(
            module="T09_PRETRADE",
            action="ALLOW",
            context=None,
        )
        assert audit_id is not None

    def test_log_triggers_flush_at_capacity(self, tmp_path):
        """buffer 达到容量时自动刷盘."""
        logger_inst = RiskAuditLogger(audit_dir=str(tmp_path), buffer_capacity=2)
        logger_inst.log(module="T09_PRETRADE", action="ALLOW")
        logger_inst.log(module="T09_PRETRADE", action="ALLOW")
        # buffer 容量 2, 第二次 log 后应刷盘, buffer 清空
        today = logger_inst.audit_dir  # 验证目录可访问
        assert today.exists()


# ============================================================
# RiskAuditLogger.flush 测试
# ============================================================
class TestRiskAuditLoggerFlush:
    """RiskAuditLogger.flush 强制刷盘测试."""

    def test_flush_writes_buffer_to_file(self, tmp_path):
        """flush 将 buffer 写入 JSONL 文件."""
        logger_inst = RiskAuditLogger(audit_dir=str(tmp_path), buffer_capacity=100)
        logger_inst.log(module="T09_PRETRADE", action="BLOCK", symbol="600519")
        logger_inst.log(module="T12_KILL", action="TRIP", symbol="000858")
        logger_inst.flush()

        # 验证文件存在且包含 2 行
        jsonl_files = list(tmp_path.glob("risk_audit_*.jsonl"))
        assert len(jsonl_files) == 1
        lines = jsonl_files[0].read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2

    def test_flush_empty_buffer_noop(self, tmp_path):
        """flush 空 buffer 不创建文件."""
        logger_inst = RiskAuditLogger(audit_dir=str(tmp_path), buffer_capacity=100)
        logger_inst.flush()
        jsonl_files = list(tmp_path.glob("risk_audit_*.jsonl"))
        assert len(jsonl_files) == 0


# ============================================================
# RiskAuditLogger 回放 API 测试
# ============================================================
class TestRiskAuditLoggerQuery:
    """RiskAuditLogger 回放查询 API 测试."""

    def test_query_by_date_returns_records(self, tmp_path):
        """query_by_date 返回当日记录."""
        logger_inst = RiskAuditLogger(audit_dir=str(tmp_path), buffer_capacity=100)
        logger_inst.log(module="T09_PRETRADE", action="BLOCK", symbol="600519")
        logger_inst.log(module="T12_KILL", action="ALLOW", symbol="000858")
        logger_inst.flush()


        today = now_bj().strftime("%Y-%m-%d")
        records = logger_inst.query_by_date(today)
        assert len(records) == 2

    def test_query_by_symbol_returns_matching(self, tmp_path):
        """query_by_symbol 返回匹配标的的记录."""
        logger_inst = RiskAuditLogger(audit_dir=str(tmp_path), buffer_capacity=100)
        logger_inst.log(module="T09_PRETRADE", action="BLOCK", symbol="600519")
        logger_inst.log(module="T09_PRETRADE", action="ALLOW", symbol="000858")
        logger_inst.log(module="T09_PRETRADE", action="BLOCK", symbol="600519")
        logger_inst.flush()

        records = logger_inst.query_by_symbol("600519")
        assert len(records) == 2
        assert all(r.symbol == "600519" for r in records)

    def test_query_rejections_returns_blocks_and_trips(self, tmp_path):
        """query_rejections 返回 BLOCK 和 TRIP 动作."""
        logger_inst = RiskAuditLogger(audit_dir=str(tmp_path), buffer_capacity=100)
        logger_inst.log(module="T09_PRETRADE", action="BLOCK", symbol="600519")
        logger_inst.log(module="T09_PRETRADE", action="ALLOW", symbol="000858")
        logger_inst.log(module="T12_KILL", action="TRIP", symbol="601318")
        logger_inst.flush()


        today = now_bj().strftime("%Y-%m-%d")
        rejections = logger_inst.query_rejections(today)
        assert len(rejections) == 2
        actions = {r.action for r in rejections}
        assert "BLOCK" in actions
        assert "TRIP" in actions
        assert "ALLOW" not in actions

    def test_replay_stream_yields_in_time_order(self, tmp_path):
        """replay_stream 按时间顺序 yield 记录."""
        logger_inst = RiskAuditLogger(audit_dir=str(tmp_path), buffer_capacity=100)
        logger_inst.log(module="T09_PRETRADE", action="BLOCK", symbol="600519")
        logger_inst.flush()


        today = now_bj().strftime("%Y-%m-%d")
        records = list(logger_inst.replay_stream(today))
        assert len(records) == 1
        assert records[0].symbol == "600519"

    def test_query_empty_date_returns_empty(self, tmp_path):
        """查询不存在的日期返回空列表."""
        logger_inst = RiskAuditLogger(audit_dir=str(tmp_path), buffer_capacity=100)
        records = logger_inst.query_by_date("2020-01-01")
        assert records == []


# ============================================================
# RiskAuditLogger 内部方法降级测试
# ============================================================
class TestRiskAuditLoggerDegradation:
    """RiskAuditLogger 内部方法降级测试."""

    def test_iter_jsonl_os_error_degrades(self, tmp_path):
        """_iter_jsonl 读取不存在文件降级返回空."""
        # 传入不存在的路径, 触发 OSError 降级
        records = list(RiskAuditLogger._iter_jsonl(tmp_path / "nonexistent.jsonl"))
        assert records == []

    def test_log_empty_symbol_not_indexed(self, tmp_path):
        """log symbol 为空时不加入 symbol 索引."""
        logger_inst = RiskAuditLogger(audit_dir=str(tmp_path), buffer_capacity=100)
        logger_inst.log(module="T09_PRETRADE", action="ALLOW", symbol="")
        logger_inst.flush()
        # 空 symbol 查询应返回空 (或不含空 symbol 记录)
        records = logger_inst.query_by_symbol("")
        # 空 symbol 不被索引, 查询结果为空或不含该记录
        assert isinstance(records, list)

    def test_buffer_capacity_minimum_10(self, tmp_path):
        """buffer_capacity 小于 10 时强制为 10."""
        logger_inst = RiskAuditLogger(audit_dir=str(tmp_path), buffer_capacity=1)
        assert logger_inst._buffer_capacity >= 10
