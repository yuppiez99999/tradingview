"""FactorApprovalExecutor 单元测试.

测试覆盖:
1. Feature Flag 未启用 → 全部跳过 (HC-1)
2. 无审批工单 / 非因子类工单 → 零执行
3. factor_retire (指定因子) → retire_factor 被调用 + 状态回写 executed
4. factor_retire (无指定因子) → monitor 自动淘汰路径
5. factor_deploy → deploy 被调用 (生成代码 + 注册到 library)
6. factor_generate → 仅 discover+validate, 不部署 (影子优先)
7. Kill Switch L3 熔断 → rejected + 状态回写 (Guard 防线5)
8. 幂等: 同一实例重复执行只处理一次
9. 执行异常 → failed + 状态回写 (fail-close)
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from utils.evolution.factor_approval_executor import (
    ACTION_FACTOR_DEPLOY,
    ACTION_FACTOR_GENERATE,
    ACTION_FACTOR_RETIRE,
    STATUS_APPROVED,
    FactorApprovalExecutor,
)
from utils.evolution.guard import KILL_SWITCH_L3
from utils.evolution.memory import EvolutionMemory
from utils.infra.feature_flags import FeatureFlags

# ============================================================
# 测试替身
# ============================================================


class FakeFactory:
    """AutoFactorFactory 的测试替身 (记录调用)."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.deployed: list = []
        self.suggestions: list = []
        self.discover_result: list = []
        self.validate_result: list = []
        self.fail_on_deploy: bool = False
        self._validated: dict = {}

    def retire_factor(self, name: str, reason: str = "") -> bool:
        self.calls.append(("retire_factor", name, reason))
        return True

    def monitor(self):
        self.calls.append(("monitor",))
        return self.suggestions

    def discover(self):
        self.calls.append(("discover",))
        return self.discover_result

    def validate(self, candidates=None):
        self.calls.append(("validate", candidates))
        return self.validate_result

    def deploy(self, validated=None, generate_code=True, register_to_library=True):
        self.calls.append(("deploy", validated, generate_code, register_to_library))
        if self.fail_on_deploy:
            raise RuntimeError("deploy boom")
        return self.deployed


class MockKillSwitch:
    """模拟 KillSwitch (Guard 防线5)."""

    def __init__(self, events: list[dict] | None = None) -> None:
        self.events = events or []

    def get_event_history(self, days: int = 30) -> list[dict]:
        return list(self.events)


def _make_ks_event(level: int, hours_ago: int = 1) -> dict:
    """构造熔断事件 (ISO UTC 带 Z 后缀)."""
    ts = (datetime.now(UTC) - timedelta(hours=hours_ago)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )
    return {"level": level, "timestamp": ts}


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def mem(tmp_path: Path) -> EvolutionMemory:
    """临时 Memory (每测试独立)."""
    return EvolutionMemory(memory_path=tmp_path / "memory.jsonl")


@pytest.fixture
def temp_override_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离 flag 覆盖目录 (不污染真实 reports/flag_overrides)."""
    override_dir = tmp_path / "flag_overrides"
    override_dir.mkdir()
    monkeypatch.setenv("QUANT_FLAG_OVERRIDE_DIR", str(override_dir))
    FeatureFlags.reset_instance()
    yield override_dir
    FeatureFlags.reset_instance()


def _set_flag(override_dir: Path, name: str, enabled: bool) -> None:
    """写 flag 覆盖文件并重载单例."""
    (override_dir / f"{name}.json").write_text(
        json.dumps(
            {
                "flag_name": name,
                "enabled": enabled,
                "signer": "test",
                "co_signer": "test",
                "reason": "test",
                "action": "override",
                "timestamp": "2026-08-29T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    FeatureFlags.reset_instance()


def _approve(
    mem: EvolutionMemory,
    action_type: str,
    factor_name: str | None = None,
) -> str:
    """写入一张已审批的 L3 因子工单, 返回 proposal_id."""
    metadata: dict = {}
    if factor_name:
        metadata["factor_name"] = factor_name
    return mem.record(
        {
            "level": "L3",
            "action_type": action_type,
            "target_module": "auto_factor_factory",
            "trigger_reason": "test approval",
            "rollback_plan": "回滚: 测试工单",
            "status": STATUS_APPROVED,
            "metadata": metadata,
        }
    )


def _make_executor(
    factory: FakeFactory,
    mem: EvolutionMemory,
    kill_switch: MockKillSwitch | None = None,
    **kwargs,
) -> FactorApprovalExecutor:
    return FactorApprovalExecutor(
        factory=factory,
        memory=mem,
        kill_switch=kill_switch,
        feature_flag="USE_AUTO_FACTOR_FACTORY",
        **kwargs,
    )


# ============================================================
# HC-1: Feature Flag 闸门
# ============================================================


class TestFlagGate:
    def test_flag_disabled_skips_all(self, temp_override_dir, mem):
        """HC-1: flag 未启用 → 全部跳过, 不执行任何动作."""
        _set_flag(temp_override_dir, "USE_AUTO_FACTOR_FACTORY", False)
        factory = FakeFactory()
        _approve(mem, ACTION_FACTOR_RETIRE, factor_name="MOM_60D")

        executor = _make_executor(factory, mem, MockKillSwitch())
        summary = executor.execute_pending_approvals()

        assert summary["checked"] is False
        assert summary["reason"].startswith("feature_flag_disabled")
        assert summary["executed"] == 0
        assert factory.calls == []  # 未执行任何动作
        # 工单状态未被修改 (仍为 approved)
        assert mem.query(status=STATUS_APPROVED)

    def test_flag_enabled_executes(self, temp_override_dir, mem):
        """flag 启用 → 正常执行."""
        _set_flag(temp_override_dir, "USE_AUTO_FACTOR_FACTORY", True)
        factory = FakeFactory()
        _approve(mem, ACTION_FACTOR_RETIRE, factor_name="MOM_60D")

        executor = _make_executor(factory, mem, MockKillSwitch())
        summary = executor.execute_pending_approvals()

        assert summary["checked"] is True
        assert summary["executed"] == 1
        assert ("retire_factor", "MOM_60D", "test approval") in factory.calls


# ============================================================
# 空工单 / 非因子工单
# ============================================================


class TestNoTickets:
    def test_no_approved_tickets(self, temp_override_dir, mem):
        """无审批工单 → 零执行."""
        _set_flag(temp_override_dir, "USE_AUTO_FACTOR_FACTORY", True)
        factory = FakeFactory()

        executor = _make_executor(factory, mem, MockKillSwitch())
        summary = executor.execute_pending_approvals()

        assert summary["checked"] is True
        assert summary["executed"] == 0
        assert factory.calls == []

    def test_non_factor_tickets_ignored(self, temp_override_dir, mem):
        """非因子类工单 (如 retrain) 不消费."""
        _set_flag(temp_override_dir, "USE_AUTO_FACTOR_FACTORY", True)
        factory = FakeFactory()
        mem.record(
            {
                "level": "L3",
                "action_type": "retrain",
                "target_module": "lgb_trainer",
                "trigger_reason": "test",
                "rollback_plan": "回滚: 测试",
                "status": STATUS_APPROVED,
            }
        )

        executor = _make_executor(factory, mem, MockKillSwitch())
        summary = executor.execute_pending_approvals()

        assert summary["executed"] == 0
        assert factory.calls == []


# ============================================================
# factor_retire
# ============================================================


class TestRetire:
    def test_retire_with_factor_name(self, temp_override_dir, mem):
        """指定因子 → retire_factor 被调用 + 状态回写 executed."""
        _set_flag(temp_override_dir, "USE_AUTO_FACTOR_FACTORY", True)
        factory = FakeFactory()
        pid = _approve(mem, ACTION_FACTOR_RETIRE, factor_name="MOM_60D")

        executor = _make_executor(factory, mem, MockKillSwitch())
        summary = executor.execute_pending_approvals()

        assert summary["executed"] == 1
        detail = summary["details"][0]
        assert detail["outcome"] == "executed"
        assert detail["result"]["retired"] == ["MOM_60D"]

        records = mem.query(proposal_id=pid)
        assert records[0].status == "executed"

    def test_retire_via_monitor(self, temp_override_dir, mem):
        """无指定因子 → monitor 自动淘汰路径."""
        _set_flag(temp_override_dir, "USE_AUTO_FACTOR_FACTORY", True)
        factory = FakeFactory()
        factory.suggestions = [SimpleNamespace(name="VOL_20D", suggest_retire=True)]
        pid = _approve(mem, ACTION_FACTOR_RETIRE)

        executor = _make_executor(factory, mem, MockKillSwitch())
        summary = executor.execute_pending_approvals()

        assert summary["executed"] == 1
        assert ("monitor",) in factory.calls
        assert summary["details"][0]["result"]["retired"] == ["VOL_20D"]

        records = mem.query(proposal_id=pid)
        assert records[0].status == "executed"


# ============================================================
# factor_deploy
# ============================================================


class TestDeploy:
    def test_deploy(self, temp_override_dir, mem):
        """deploy 工单 → 生成代码 + 注册到 library."""
        _set_flag(temp_override_dir, "USE_AUTO_FACTOR_FACTORY", True)
        factory = FakeFactory()
        _approve(mem, ACTION_FACTOR_DEPLOY)

        executor = _make_executor(factory, mem, MockKillSwitch())
        summary = executor.execute_pending_approvals()

        assert summary["executed"] == 1
        deploy_calls = [c for c in factory.calls if c[0] == "deploy"]
        assert len(deploy_calls) == 1
        _, validated, generate_code, register_to_library = deploy_calls[0]
        assert validated is None
        assert generate_code is True
        assert register_to_library is True


# ============================================================
# factor_generate (影子优先)
# ============================================================


class TestGenerateShadow:
    def test_generate_shadow_only(self, temp_override_dir, mem):
        """factor_generate: discover+validate 只产候选, 绝不部署."""
        _set_flag(temp_override_dir, "USE_AUTO_FACTOR_FACTORY", True)
        factory = FakeFactory()
        factory.discover_result = [SimpleNamespace(name="NEW_FACTOR_1")]
        factory.validate_result = [SimpleNamespace(name="NEW_FACTOR_1")]
        _approve(mem, ACTION_FACTOR_GENERATE)

        executor = _make_executor(factory, mem, MockKillSwitch())
        summary = executor.execute_pending_approvals()

        assert summary["executed"] == 1
        action_names = [c[0] for c in factory.calls]
        assert "discover" in action_names
        assert "validate" in action_names
        assert "deploy" not in action_names  # 影子: 绝不部署

        detail = summary["details"][0]
        assert detail["result"]["deployed"] is False

    def test_generate_no_candidates_skips_validate(self, temp_override_dir, mem):
        """无候选 → 不进入 validate."""
        _set_flag(temp_override_dir, "USE_AUTO_FACTOR_FACTORY", True)
        factory = FakeFactory()
        factory.discover_result = []
        _approve(mem, ACTION_FACTOR_GENERATE)

        executor = _make_executor(factory, mem, MockKillSwitch())
        summary = executor.execute_pending_approvals()

        assert summary["executed"] == 1
        assert [c[0] for c in factory.calls] == ["discover"]


# ============================================================
# Guard 拒绝 (Kill Switch 熔断)
# ============================================================


class TestGuardReject:
    def test_kill_switch_l3_freezes(self, temp_override_dir, mem):
        """Kill Switch L3 熔断 → 拒绝 + 回写 rejected + 零动作."""
        _set_flag(temp_override_dir, "USE_AUTO_FACTOR_FACTORY", True)
        factory = FakeFactory()
        ks = MockKillSwitch(events=[_make_ks_event(level=KILL_SWITCH_L3, hours_ago=1)])
        pid = _approve(mem, ACTION_FACTOR_RETIRE, factor_name="MOM_60D")

        executor = _make_executor(factory, mem, ks)
        summary = executor.execute_pending_approvals()

        assert summary["rejected"] == 1
        assert factory.calls == []  # 未执行任何动作 (fail-close)

        records = mem.query(proposal_id=pid)
        assert records[0].status == "rejected"


# ============================================================
# 幂等
# ============================================================


class TestIdempotent:
    def test_same_instance_double_run(self, temp_override_dir, mem):
        """同一实例重复执行只处理一次."""
        _set_flag(temp_override_dir, "USE_AUTO_FACTOR_FACTORY", True)
        factory = FakeFactory()
        _approve(mem, ACTION_FACTOR_RETIRE, factor_name="MOM_60D")

        executor = _make_executor(factory, mem, MockKillSwitch())
        s1 = executor.execute_pending_approvals()
        s2 = executor.execute_pending_approvals()

        assert s1["executed"] == 1
        assert s2["executed"] == 0  # 第二轮: 工单已非 approved + 幂等
        assert factory.calls.count(("retire_factor", "MOM_60D", "test approval")) == 1


# ============================================================
# fail-close: 执行异常
# ============================================================


class TestFailClosed:
    def test_execution_exception_marks_failed(self, temp_override_dir, mem):
        """工厂执行抛异常 → failed + 状态回写 + 不向上抛."""
        _set_flag(temp_override_dir, "USE_AUTO_FACTOR_FACTORY", True)
        factory = FakeFactory()
        factory.fail_on_deploy = True
        pid = _approve(mem, ACTION_FACTOR_DEPLOY)

        executor = _make_executor(factory, mem, MockKillSwitch())
        summary = executor.execute_pending_approvals()

        assert summary["failed"] == 1
        assert summary["details"][0]["outcome"] == "failed"

        records = mem.query(proposal_id=pid)
        assert records[0].status == "failed"
