"""EvolutionMemory 单元测试.

任务编号: T1.2 (Phase 1 防御层加固)
验收要求: 单测覆盖率 >= 90%

测试范围:
    - record(): 基本写入 / 字典输入 / MemoryRecord 输入 / 自动 ID / 校验 / 写入失败
    - query(): 多维过滤 / limit / 时间范围 / 无匹配 / 读取容错
    - learn(): 基本学习 / status 更新 / 空文本 / 未找到 / 不可变性
    - update_status(): 基本更新 / 带 result / 未找到 / 空 status
    - 辅助: count / get_latest_proposal_id / 持久化 / ID 递增 / 损坏行容错
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils.evolution.memory import (
    ACTION_RETRAIN,
    DEFAULT_MEMORY_PATH,
    LEVEL_L1,
    LEVEL_L2,
    LEVEL_L3,
    STATUS_EXECUTED,
    STATUS_FAILED,
    STATUS_LEARNED,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_ROLLED_BACK,
    EvolutionMemory,
    MemoryNotFoundError,
    MemoryRecord,
    MemoryValidationError,
    MemoryWriteError,
)

# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def tmp_memory_path(tmp_path: Path) -> Path:
    """临时 memory.jsonl 路径 (每个测试独立)."""
    return tmp_path / "memory.jsonl"


@pytest.fixture
def memory(tmp_memory_path: Path) -> EvolutionMemory:
    """干净的 EvolutionMemory 实例."""
    return EvolutionMemory(memory_path=tmp_memory_path)


@pytest.fixture
def sample_proposal() -> dict:
    """标准 L2 提案 (含回滚方案)."""
    return {
        "level": LEVEL_L2,
        "action_type": ACTION_RETRAIN,
        "trigger_reason": "IC 衰减 0.08 -> 0.02",
        "target_module": "lgb_enhanced_trainer",
        "rollback_plan": "回滚至 v8.6.14 基线",
        "score_report": {"public_score": 0.72, "private_score": 0.68},
        "metadata": {"source": "drift_monitor"},
    }


# ============================================================
# record() 测试
# ============================================================


class TestRecord:
    def test_record_basic_returns_proposal_id(self, memory: EvolutionMemory, sample_proposal: dict):
        """record 应返回 proposal_id (非空, EVO- 前缀)."""
        pid = memory.record(sample_proposal)
        assert pid.startswith("EVO-")
        assert len(pid) > 10

    def test_record_with_dict_input(self, memory: EvolutionMemory, sample_proposal: dict):
        """字典输入应正确写入."""
        pid = memory.record(sample_proposal)
        results = memory.query(proposal_id=pid)
        assert len(results) == 1
        rec = results[0]
        assert rec.level == LEVEL_L2
        assert rec.action_type == ACTION_RETRAIN
        assert rec.target_module == "lgb_enhanced_trainer"
        assert rec.status == STATUS_PENDING  # 默认状态

    def test_record_with_memory_record_input(self, memory: EvolutionMemory):
        """MemoryRecord 输入应正确写入."""
        record = MemoryRecord(
            proposal_id="EVO-TEST-001",
            timestamp="2026-08-02T10:00:00Z",
            level=LEVEL_L1,
            action_type="fix",
            trigger_reason="heartbeat 字段名不匹配",
            target_module="system_check",
        )
        pid = memory.record(record)
        assert pid == "EVO-TEST-001"
        results = memory.query(proposal_id=pid)
        assert len(results) == 1
        assert results[0].level == LEVEL_L1

    def test_record_auto_generate_proposal_id(self, memory: EvolutionMemory, sample_proposal: dict):
        """未提供 proposal_id 时应自动生成."""
        sample_proposal.pop("proposal_id", None)
        pid = memory.record(sample_proposal)
        assert pid.startswith("EVO-") and len(pid) > 10

    def test_record_auto_generate_timestamp(self, memory: EvolutionMemory, sample_proposal: dict):
        """未提供 timestamp 时应自动生成 UTC ISO8601."""
        sample_proposal.pop("timestamp", None)
        pid = memory.record(sample_proposal)
        rec = memory.query(proposal_id=pid)[0]
        assert rec.timestamp  # 非空
        assert rec.timestamp.endswith("Z")  # UTC 后缀

    def test_record_invalid_type_raises(self, memory: EvolutionMemory):
        """非 dict/MemoryRecord 输入应抛 MemoryValidationError."""
        with pytest.raises(MemoryValidationError):
            memory.record("not a dict")  # type: ignore[arg-type]
        with pytest.raises(MemoryValidationError):
            memory.record(12345)  # type: ignore[arg-type]

    def test_record_missing_required_field_level(self, memory: EvolutionMemory, sample_proposal: dict):
        """缺 level 应抛 MemoryValidationError."""
        sample_proposal.pop("level")
        with pytest.raises(MemoryValidationError):
            memory.record(sample_proposal)

    def test_record_missing_required_field_action_type(self, memory: EvolutionMemory, sample_proposal: dict):
        """缺 action_type 应抛 MemoryValidationError."""
        sample_proposal.pop("action_type")
        with pytest.raises(MemoryValidationError):
            memory.record(sample_proposal)

    def test_record_missing_required_field_target_module(self, memory: EvolutionMemory, sample_proposal: dict):
        """缺 target_module 应抛 MemoryValidationError."""
        sample_proposal.pop("target_module")
        with pytest.raises(MemoryValidationError):
            memory.record(sample_proposal)

    def test_record_invalid_level_raises(self, memory: EvolutionMemory, sample_proposal: dict):
        """无效 level 应抛 MemoryValidationError."""
        sample_proposal["level"] = "L4"
        with pytest.raises(MemoryValidationError):
            memory.record(sample_proposal)

    def test_record_l2_without_rollback_plan_raises(self, memory: EvolutionMemory, sample_proposal: dict):
        """L2 缺 rollback_plan 应抛 MemoryValidationError (HC-3)."""
        sample_proposal.pop("rollback_plan")
        with pytest.raises(MemoryValidationError, match="rollback_plan"):
            memory.record(sample_proposal)

    def test_record_l3_without_rollback_plan_raises(self, memory: EvolutionMemory, sample_proposal: dict):
        """L3 缺 rollback_plan 应抛 MemoryValidationError (HC-3)."""
        sample_proposal["level"] = LEVEL_L3
        sample_proposal.pop("rollback_plan")
        with pytest.raises(MemoryValidationError, match="rollback_plan"):
            memory.record(sample_proposal)

    def test_record_l1_without_rollback_plan_ok(self, memory: EvolutionMemory, sample_proposal: dict):
        """L1 缺 rollback_plan 应允许 (L1 是自动修复, 无需回滚)."""
        sample_proposal["level"] = LEVEL_L1
        sample_proposal.pop("rollback_plan")
        pid = memory.record(sample_proposal)
        assert pid

    def test_record_l2_rejected_without_rollback_plan_ok(
        self, memory: EvolutionMemory, sample_proposal: dict
    ):
        """L2 被 Guard 拒绝的提案缺 rollback_plan 应允许 (HC-2 审计完整性优先).

        设计权衡: STATUS_REJECTED 状态的提案未执行, 无需回滚方案,
        但审计完整性要求记录所有提案 (含被拒的), 故对 rejected 豁免 HC-3.
        """
        sample_proposal.pop("rollback_plan")
        sample_proposal["status"] = STATUS_REJECTED
        pid = memory.record(sample_proposal)
        assert pid

    def test_record_l3_rejected_without_rollback_plan_ok(
        self, memory: EvolutionMemory, sample_proposal: dict
    ):
        """L3 被 Guard 拒绝的提案缺 rollback_plan 应允许 (同 L2 逻辑)."""
        sample_proposal["level"] = LEVEL_L3
        sample_proposal.pop("rollback_plan")
        sample_proposal["status"] = STATUS_REJECTED
        pid = memory.record(sample_proposal)
        assert pid

    def test_record_empty_proposal_id_raises(self, memory: EvolutionMemory):
        """空 proposal_id (MemoryRecord 输入) 应抛 MemoryValidationError.

        Note: 字典输入时空 ID 会被自动生成 (合理行为), 故用 MemoryRecord 测试.
        """
        rec = MemoryRecord(
            proposal_id="", timestamp="t", level=LEVEL_L1,
            action_type="fix", trigger_reason="r", target_module="m",
        )
        with pytest.raises(MemoryValidationError):
            memory.record(rec)

    def test_record_write_failure_raises_memory_write_error(
        self, tmp_path: Path, sample_proposal: dict
    ):
        """写入失败 (父目录不存在且禁用自动创建) 应抛 MemoryWriteError."""
        nonexistent = tmp_path / "nonexistent_dir" / "memory.jsonl"
        mem = EvolutionMemory(memory_path=nonexistent, auto_create_dir=False)
        with pytest.raises(MemoryWriteError):
            mem.record(sample_proposal)

    def test_record_persists_to_disk(self, memory: EvolutionMemory, sample_proposal: dict, tmp_memory_path: Path):
        """record 后文件应非空且为合法 JSONL."""
        memory.record(sample_proposal)
        assert tmp_memory_path.exists()
        content = tmp_memory_path.read_text(encoding="utf-8").strip()
        assert content  # 非空
        # 每行应是合法 JSON
        for line in content.split("\n"):
            data = json.loads(line)
            assert "proposal_id" in data
            assert "level" in data

    def test_record_metadata_preserved(self, memory: EvolutionMemory, sample_proposal: dict):
        """metadata 字段应原样保留."""
        memory.record(sample_proposal)
        rec = memory.query(level=LEVEL_L2)[0]
        assert rec.metadata == {"source": "drift_monitor"}

    def test_record_score_report_preserved(self, memory: EvolutionMemory, sample_proposal: dict):
        """score_report 字段应原样保留."""
        memory.record(sample_proposal)
        rec = memory.query(level=LEVEL_L2)[0]
        assert rec.score_report == {"public_score": 0.72, "private_score": 0.68}


# ============================================================
# query() 测试
# ============================================================


class TestQuery:
    @pytest.fixture
    def populated_memory(self, memory: EvolutionMemory) -> EvolutionMemory:
        """预填充 5 条记录 (覆盖 L1/L2/L3 + 多种 action/status)."""
        memory.record({
            "proposal_id": "EVO-A-001",
            "timestamp": "2026-08-01T10:00:00Z",
            "level": LEVEL_L1, "action_type": "fix",
            "trigger_reason": "r1", "target_module": "mod_a",
            "status": STATUS_EXECUTED,
        })
        memory.record({
            "proposal_id": "EVO-A-002",
            "timestamp": "2026-08-01T11:00:00Z",
            "level": LEVEL_L2, "action_type": ACTION_RETRAIN,
            "trigger_reason": "r2", "target_module": "mod_b",
            "rollback_plan": "rollback", "status": STATUS_PENDING,
        })
        memory.record({
            "proposal_id": "EVO-A-003",
            "timestamp": "2026-08-02T09:00:00Z",
            "level": LEVEL_L2, "action_type": "weight_adjust",
            "trigger_reason": "r3", "target_module": "mod_b",
            "rollback_plan": "rollback", "status": STATUS_EXECUTED,
        })
        memory.record({
            "proposal_id": "EVO-A-004",
            "timestamp": "2026-08-02T14:00:00Z",
            "level": LEVEL_L3, "action_type": "factor_deploy",
            "trigger_reason": "r4", "target_module": "mod_c",
            "rollback_plan": "rollback", "status": STATUS_FAILED,
        })
        memory.record({
            "proposal_id": "EVO-A-005",
            "timestamp": "2026-08-03T10:00:00Z",
            "level": LEVEL_L1, "action_type": "fix",
            "trigger_reason": "r5", "target_module": "mod_a",
            "status": STATUS_LEARNED,
        })
        return memory

    def test_query_all(self, populated_memory: EvolutionMemory):
        """无过滤条件应返回全部 5 条."""
        results = populated_memory.query()
        assert len(results) == 5

    def test_query_by_level(self, populated_memory: EvolutionMemory):
        """按 level 过滤."""
        l1 = populated_memory.query(level=LEVEL_L1)
        l2 = populated_memory.query(level=LEVEL_L2)
        l3 = populated_memory.query(level=LEVEL_L3)
        assert len(l1) == 2
        assert len(l2) == 2
        assert len(l3) == 1

    def test_query_by_action_type(self, populated_memory: EvolutionMemory):
        """按 action_type 过滤."""
        fixes = populated_memory.query(action_type="fix")
        assert len(fixes) == 2
        assert all(r.action_type == "fix" for r in fixes)

    def test_query_by_status(self, populated_memory: EvolutionMemory):
        """按 status 过滤."""
        executed = populated_memory.query(status=STATUS_EXECUTED)
        assert len(executed) == 2

    def test_query_by_proposal_id(self, populated_memory: EvolutionMemory):
        """按 proposal_id 精确匹配."""
        results = populated_memory.query(proposal_id="EVO-A-003")
        assert len(results) == 1
        assert results[0].proposal_id == "EVO-A-003"

    def test_query_by_target_module(self, populated_memory: EvolutionMemory):
        """按 target_module 过滤."""
        mod_b = populated_memory.query(target_module="mod_b")
        assert len(mod_b) == 2
        assert all(r.target_module == "mod_b" for r in mod_b)

    def test_query_by_since(self, populated_memory: EvolutionMemory):
        """按起始时间过滤."""
        results = populated_memory.query(since="2026-08-02T00:00:00Z")
        assert len(results) == 3  # 003, 004, 005

    def test_query_by_until(self, populated_memory: EvolutionMemory):
        """按结束时间过滤."""
        results = populated_memory.query(until="2026-08-01T23:59:59Z")
        assert len(results) == 2  # 001, 002

    def test_query_by_since_and_until(self, populated_memory: EvolutionMemory):
        """按时间范围过滤."""
        results = populated_memory.query(
            since="2026-08-02T00:00:00Z", until="2026-08-02T23:59:59Z"
        )
        assert len(results) == 2  # 003, 004

    def test_query_with_limit(self, populated_memory: EvolutionMemory):
        """limit 限制返回条数."""
        results = populated_memory.query(limit=2)
        assert len(results) == 2

    def test_query_limit_zero(self, populated_memory: EvolutionMemory):
        """limit=0 返回空列表."""
        results = populated_memory.query(limit=0)
        assert len(results) == 0

    def test_query_ascending_by_timestamp(self, populated_memory: EvolutionMemory):
        """结果应按 timestamp 升序."""
        results = populated_memory.query()
        timestamps = [r.timestamp for r in results]
        assert timestamps == sorted(timestamps)

    def test_query_no_match_returns_empty(self, populated_memory: EvolutionMemory):
        """无匹配返回空列表."""
        results = populated_memory.query(level=LEVEL_L1, action_type=ACTION_RETRAIN)
        assert results == []

    def test_query_combined_filters(self, populated_memory: EvolutionMemory):
        """组合过滤条件."""
        results = populated_memory.query(
            level=LEVEL_L2, status=STATUS_EXECUTED, target_module="mod_b"
        )
        assert len(results) == 1
        assert results[0].proposal_id == "EVO-A-003"

    def test_query_read_failure_returns_empty(self, tmp_path: Path):
        """读取失败 (目录而非文件) 应容错返回空列表, 不抛异常."""
        mem = EvolutionMemory(memory_path=tmp_path)  # tmp_path 是目录, 不是文件
        results = mem.query()
        assert results == []

    def test_query_empty_file_returns_empty(self, tmp_memory_path: Path):
        """空文件应返回空列表."""
        tmp_memory_path.write_text("", encoding="utf-8")
        mem = EvolutionMemory(memory_path=tmp_memory_path)
        assert mem.query() == []

    def test_query_skips_blank_lines(self, tmp_memory_path: Path):
        """空行应被跳过."""
        tmp_memory_path.write_text(
            "\n  \n", encoding="utf-8"
        )
        mem = EvolutionMemory(memory_path=tmp_memory_path)
        assert mem.query() == []

    def test_query_skips_corrupted_lines(self, tmp_memory_path: Path):
        """损坏的 JSON 行应被跳过 (容错)."""
        tmp_memory_path.write_text(
            '{"proposal_id":"EVO-X-001","level":"L1","action_type":"fix","target_module":"m"}\n'
            "NOT_VALID_JSON\n"
            '{"proposal_id":"EVO-X-002","level":"L1","action_type":"fix","target_module":"m"}\n',
            encoding="utf-8",
        )
        mem = EvolutionMemory(memory_path=tmp_memory_path)
        results = mem.query()
        assert len(results) == 2  # 跳过损坏行, 保留 2 条


# ============================================================
# learn() 测试
# ============================================================


class TestLearn:
    def test_learn_basic(self, memory: EvolutionMemory, sample_proposal: dict):
        """learn 应写入 lesson 字段."""
        pid = memory.record(sample_proposal)
        assert memory.learn(pid, "夏季 IC 衰减是季节性现象")
        rec = memory.query(proposal_id=pid)[0]
        assert rec.learned == "夏季 IC 衰减是季节性现象"

    def test_learn_updates_status_to_learned(self, memory: EvolutionMemory, sample_proposal: dict):
        """learn 后 status 应变为 learned."""
        pid = memory.record(sample_proposal)
        memory.learn(pid, "lesson")
        rec = memory.query(proposal_id=pid)[0]
        assert rec.status == STATUS_LEARNED

    def test_learn_preserves_other_fields(self, memory: EvolutionMemory, sample_proposal: dict):
        """learn 不应修改其他字段 (不可变性)."""
        pid = memory.record(sample_proposal)
        original = memory.query(proposal_id=pid)[0]
        memory.learn(pid, "new lesson")
        updated = memory.query(proposal_id=pid)[0]
        assert updated.proposal_id == original.proposal_id
        assert updated.timestamp == original.timestamp
        assert updated.level == original.level
        assert updated.action_type == original.action_type
        assert updated.trigger_reason == original.trigger_reason
        assert updated.target_module == original.target_module
        assert updated.score_report == original.score_report
        assert updated.result == original.result
        assert updated.rollback_plan == original.rollback_plan
        assert updated.metadata == original.metadata

    def test_learn_empty_lesson_raises(self, memory: EvolutionMemory, sample_proposal: dict):
        """空 lesson 应抛 MemoryValidationError."""
        pid = memory.record(sample_proposal)
        with pytest.raises(MemoryValidationError):
            memory.learn(pid, "")
        with pytest.raises(MemoryValidationError):
            memory.learn(pid, "   ")  # 纯空白

    def test_learn_not_found_raises(self, memory: EvolutionMemory):
        """未找到 pid 应抛 MemoryNotFoundError."""
        with pytest.raises(MemoryNotFoundError):
            memory.learn("EVO-NONEXIST-999", "lesson")

    def test_learn_write_failure_raises(self, tmp_path: Path, sample_proposal: dict):
        """重写失败应抛 MemoryWriteError."""
        # 先在可写路径 record
        mem_path = tmp_path / "mem.jsonl"
        mem = EvolutionMemory(memory_path=mem_path)
        pid = mem.record(sample_proposal)

        # monkey-patch _rewrite_all 抛 MemoryWriteError (模拟重写失败)
        original_rewrite = mem._rewrite_all

        def failing_rewrite(records):
            raise MemoryWriteError("simulated rewrite failure")

        mem._rewrite_all = failing_rewrite  # type: ignore[method-assign]
        try:
            with pytest.raises(MemoryWriteError):
                mem.learn(pid, "lesson")
        finally:
            mem._rewrite_all = original_rewrite  # type: ignore[method-assign]


# ============================================================
# update_status() 测试
# ============================================================


class TestUpdateStatus:
    def test_update_status_basic(self, memory: EvolutionMemory, sample_proposal: dict):
        """基本状态更新."""
        pid = memory.record(sample_proposal)
        assert memory.update_status(pid, STATUS_EXECUTED)
        rec = memory.query(proposal_id=pid)[0]
        assert rec.status == STATUS_EXECUTED

    def test_update_status_with_result(self, memory: EvolutionMemory, sample_proposal: dict):
        """带 result 更新."""
        pid = memory.record(sample_proposal)
        memory.update_status(pid, STATUS_EXECUTED, result={"new_ic": 0.075})
        rec = memory.query(proposal_id=pid)[0]
        assert rec.result == {"new_ic": 0.075}
        assert rec.executed_at  # 自动填充

    def test_update_status_with_explicit_executed_at(self, memory: EvolutionMemory, sample_proposal: dict):
        """显式 executed_at."""
        pid = memory.record(sample_proposal)
        memory.update_status(pid, STATUS_FAILED, executed_at="2026-08-02T15:00:00Z")
        rec = memory.query(proposal_id=pid)[0]
        assert rec.executed_at == "2026-08-02T15:00:00Z"

    def test_update_status_to_rolled_back(self, memory: EvolutionMemory, sample_proposal: dict):
        """更新为 rolled_back."""
        pid = memory.record(sample_proposal)
        memory.update_status(pid, STATUS_ROLLED_BACK)
        rec = memory.query(proposal_id=pid)[0]
        assert rec.status == STATUS_ROLLED_BACK

    def test_update_status_not_found_raises(self, memory: EvolutionMemory):
        """未找到 pid 应抛 MemoryNotFoundError."""
        with pytest.raises(MemoryNotFoundError):
            memory.update_status("EVO-NOPE-999", STATUS_EXECUTED)

    def test_update_status_empty_status_raises(self, memory: EvolutionMemory, sample_proposal: dict):
        """空 status 应抛 MemoryValidationError."""
        pid = memory.record(sample_proposal)
        with pytest.raises(MemoryValidationError):
            memory.update_status(pid, "")

    def test_update_status_preserves_learned(self, memory: EvolutionMemory, sample_proposal: dict):
        """update_status 不应清除已 learned 的内容."""
        pid = memory.record(sample_proposal)
        memory.learn(pid, "first lesson")
        memory.update_status(pid, STATUS_EXECUTED)
        rec = memory.query(proposal_id=pid)[0]
        # update_status 后 learned 仍保留 (但 status 被覆盖为 executed)
        assert rec.learned == "first lesson"


# ============================================================
# 辅助方法测试
# ============================================================


class TestAuxiliary:
    def test_count_empty(self, memory: EvolutionMemory):
        """空记忆 count=0."""
        assert memory.count() == 0

    def test_count_after_records(self, memory: EvolutionMemory, sample_proposal: dict):
        """记录后 count 应正确."""
        memory.record(sample_proposal)
        memory.record({**sample_proposal, "target_module": "another"})
        assert memory.count() == 2

    def test_count_read_failure_returns_zero(self, tmp_path: Path):
        """count 读取失败应容错返回 0."""
        mem = EvolutionMemory(memory_path=tmp_path)  # 目录而非文件
        assert mem.count() == 0

    def test_get_latest_proposal_id_empty(self, memory: EvolutionMemory):
        """空记忆 get_latest 返回 None."""
        assert memory.get_latest_proposal_id() is None

    def test_get_latest_proposal_id_returns_newest(self, memory: EvolutionMemory):
        """get_latest 应返回 timestamp 最新的记录."""
        memory.record({
            "proposal_id": "EVO-OLD-001", "timestamp": "2026-08-01T10:00:00Z",
            "level": LEVEL_L1, "action_type": "fix", "target_module": "m",
        })
        memory.record({
            "proposal_id": "EVO-NEW-001", "timestamp": "2026-08-03T10:00:00Z",
            "level": LEVEL_L1, "action_type": "fix", "target_module": "m",
        })
        assert memory.get_latest_proposal_id() == "EVO-NEW-001"

    def test_get_latest_read_failure_returns_none(self, tmp_path: Path):
        """get_latest 读取失败容错返回 None."""
        mem = EvolutionMemory(memory_path=tmp_path)
        assert mem.get_latest_proposal_id() is None

    def test_proposal_id_sequence_increments(self, memory: EvolutionMemory, sample_proposal: dict):
        """同日 proposal_id 序号应递增."""
        pid1 = memory.record(sample_proposal)
        pid2 = memory.record({**sample_proposal, "target_module": "mod2"})
        pid3 = memory.record({**sample_proposal, "target_module": "mod3"})

        # 同日序号递增
        seq1 = int(pid1.split("-")[-1])
        seq2 = int(pid2.split("-")[-1])
        seq3 = int(pid3.split("-")[-1])
        assert seq2 == seq1 + 1
        assert seq3 == seq2 + 1

    def test_persistence_across_instances(self, tmp_memory_path: Path, sample_proposal: dict):
        """跨实例持久化: 新实例应能读到旧实例写入的数据."""
        mem1 = EvolutionMemory(memory_path=tmp_memory_path)
        pid = mem1.record(sample_proposal)

        mem2 = EvolutionMemory(memory_path=tmp_memory_path)
        results = mem2.query(proposal_id=pid)
        assert len(results) == 1
        assert results[0].proposal_id == pid


# ============================================================
# MemoryRecord 数据类测试
# ============================================================


class TestMemoryRecord:
    def test_from_dict_required_fields(self):
        """from_dict 应能解析含必填字段的字典."""
        rec = MemoryRecord.from_dict({
            "proposal_id": "EVO-X-001",
            "level": LEVEL_L1,
            "action_type": "fix",
        })
        assert rec.proposal_id == "EVO-X-001"
        assert rec.target_module == ""  # 缺省值
        assert rec.status == STATUS_PENDING

    def test_from_dict_missing_required_raises(self):
        """from_dict 缺必填字段应抛 MemoryValidationError."""
        with pytest.raises(MemoryValidationError):
            MemoryRecord.from_dict({"level": LEVEL_L1})  # 缺 proposal_id

    def test_from_dict_tolerates_none_optional(self):
        """from_dict 应容忍 None 值的可选字段."""
        rec = MemoryRecord.from_dict({
            "proposal_id": "EVO-X-001",
            "level": LEVEL_L1,
            "action_type": "fix",
            "score_report": None,
            "result": None,
            "metadata": None,
        })
        assert rec.score_report == {}
        assert rec.result == {}
        assert rec.metadata == {}

    def test_to_dict_roundtrip(self):
        """to_dict / from_dict 往返应保持数据一致."""
        original = MemoryRecord(
            proposal_id="EVO-X-001",
            timestamp="2026-08-02T10:00:00Z",
            level=LEVEL_L2,
            action_type=ACTION_RETRAIN,
            trigger_reason="test",
            target_module="mod",
            rollback_plan="rollback",
            score_report={"a": 1},
            metadata={"k": "v"},
        )
        d = original.to_dict()
        restored = MemoryRecord.from_dict(d)
        assert restored.proposal_id == original.proposal_id
        assert restored.level == original.level
        assert restored.score_report == original.score_report
        assert restored.metadata == original.metadata

    def test_validate_empty_proposal_id(self):
        """validate 应拒绝空 proposal_id."""
        rec = MemoryRecord(
            proposal_id="", timestamp="t", level=LEVEL_L1,
            action_type="fix", trigger_reason="r", target_module="m",
        )
        with pytest.raises(MemoryValidationError):
            rec.validate()

    def test_validate_empty_action_type(self):
        """validate 应拒绝空 action_type."""
        rec = MemoryRecord(
            proposal_id="X", timestamp="t", level=LEVEL_L1,
            action_type="", trigger_reason="r", target_module="m",
        )
        with pytest.raises(MemoryValidationError):
            rec.validate()

    def test_validate_empty_target_module(self):
        """validate 应拒绝空 target_module."""
        rec = MemoryRecord(
            proposal_id="X", timestamp="t", level=LEVEL_L1,
            action_type="fix", trigger_reason="r", target_module="",
        )
        with pytest.raises(MemoryValidationError):
            rec.validate()

    def test_validate_invalid_level(self):
        """validate 应拒绝无效 level."""
        rec = MemoryRecord(
            proposal_id="X", timestamp="t", level="L9",
            action_type="fix", trigger_reason="r", target_module="m",
        )
        with pytest.raises(MemoryValidationError):
            rec.validate()


# ============================================================
# 默认路径测试
# ============================================================


class TestDefaultPath:
    def test_default_memory_path_is_reports_evolution(self):
        """默认路径应指向 reports/evolution/memory.jsonl."""
        assert DEFAULT_MEMORY_PATH.name == "memory.jsonl"
        assert "reports" in str(DEFAULT_MEMORY_PATH)
        assert "evolution" in str(DEFAULT_MEMORY_PATH)

    def test_default_init_creates_directory(self, tmp_path: Path):
        """auto_create_dir=True 应自动创建父目录."""
        mem_path = tmp_path / "deep" / "nested" / "memory.jsonl"
        EvolutionMemory(memory_path=mem_path, auto_create_dir=True)
        assert mem_path.parent.exists()

    def test_auto_create_dir_false_does_not_create(self, tmp_path: Path):
        """auto_create_dir=False 不应创建父目录 (但也不应报错)."""
        mem_path = tmp_path / "not_created" / "memory.jsonl"
        # 不应抛异常 (init 容错), 但目录不会创建
        EvolutionMemory(memory_path=mem_path, auto_create_dir=False)
        assert not mem_path.parent.exists()
