"""安全合规跨市场执行代理单元测试.

被测模块: utils/safe_execution_agent.py
文献: #53 Safe Cross-Market Execution (2025.10)
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.safe_execution_agent import (  # noqa: E402
    AuditRecord,
    AuditStatus,
    ConstrainedMDP,
    ConstraintViolation,
    CVaRController,
    ExecutionConstraints,
    SafeExecutionAgent,
    SafeExecutionResult,
    ViolationType,
    ZeroKnowledgeAudit,
)

# ============================================================
# 枚举测试
# ============================================================

class TestEnums:
    def test_violation_types(self):
        assert ViolationType.NONE == "none"
        assert ViolationType.POSITION_LIMIT == "position_limit"

    def test_audit_status(self):
        assert AuditStatus.VERIFIED == "verified"
        assert AuditStatus.FAILED == "failed"


# ============================================================
# 约束测试
# ============================================================

class TestExecutionConstraints:
    def test_defaults(self):
        c = ExecutionConstraints()
        assert c.max_position == 100_000.0
        assert c.max_order_size == 50_000.0
        assert c.max_participation == 0.10
        assert c.max_cvar == 500.0
        assert c.price_limit_pct == 0.10

    def test_custom(self):
        c = ExecutionConstraints(max_position=50000, max_cvar=300)
        assert c.max_position == 50000
        assert c.max_cvar == 300


class TestConstraintViolation:
    def test_construction(self):
        v = ConstraintViolation(
            type=ViolationType.POSITION_LIMIT,
            value=150000, limit=100000,
            message="持仓超限",
        )
        assert v.type == ViolationType.POSITION_LIMIT
        assert v.value == 150000


# ============================================================
# 约束 MDP 测试
# ============================================================

class TestConstrainedMDP:
    def test_compliant_order(self):
        mdp = ConstrainedMDP()
        violations = mdp.check_constraints(
            order_shares=5000, current_position=10000, adv=500000,
        )
        assert len(violations) == 0

    def test_position_limit_violation(self):
        mdp = ConstrainedMDP()
        violations = mdp.check_constraints(
            order_shares=200000, current_position=50000, adv=1000000,
        )
        assert any(v.type == ViolationType.POSITION_LIMIT for v in violations)

    def test_order_size_limit_violation(self):
        mdp = ConstrainedMDP()
        violations = mdp.check_constraints(
            order_shares=60000, current_position=0, adv=1000000,
        )
        assert any(v.type == ViolationType.ORDER_SIZE_LIMIT for v in violations)

    def test_participation_limit_violation(self):
        mdp = ConstrainedMDP()
        violations = mdp.check_constraints(
            order_shares=50000, current_position=0, adv=100000,
        )
        assert any(v.type == ViolationType.PARTICIPATION_LIMIT for v in violations)

    def test_price_limit_violation(self):
        mdp = ConstrainedMDP()
        violations = mdp.check_constraints(
            order_shares=1000, current_position=0, adv=100000,
            price=12.0, reference_price=10.0,
        )
        assert any(v.type == ViolationType.PRICE_LIMIT for v in violations)

    def test_is_compliant_true(self):
        mdp = ConstrainedMDP()
        assert mdp.is_compliant(order_shares=5000, current_position=10000, adv=500000)

    def test_is_compliant_false(self):
        mdp = ConstrainedMDP()
        assert not mdp.is_compliant(order_shares=200000, current_position=50000, adv=1000000)

    def test_project_to_feasible_order_size(self):
        mdp = ConstrainedMDP()
        projected = mdp.project_to_feasible(
            order_shares=60000, current_position=0, adv=1000000,
        )
        assert abs(projected) <= 50000  # 裁剪到 max_order_size

    def test_project_to_feasible_position(self):
        mdp = ConstrainedMDP()
        projected = mdp.project_to_feasible(
            order_shares=200000, current_position=50000, adv=1000000,
        )
        new_pos = 50000 + projected
        assert abs(new_pos) <= 100000  # 持仓不超限

    def test_project_to_feasible_participation(self):
        mdp = ConstrainedMDP()
        projected = mdp.project_to_feasible(
            order_shares=50000, current_position=0, adv=100000,
        )
        assert abs(projected) <= 10000  # 10% × 100000

    def test_project_to_feasible_no_change(self):
        mdp = ConstrainedMDP()
        projected = mdp.project_to_feasible(
            order_shares=5000, current_position=10000, adv=500000,
        )
        assert projected == pytest.approx(5000)


# ============================================================
# CVaR 控制器测试
# ============================================================

class TestCVaRController:
    def test_compute_var(self):
        ctrl = CVaRController(alpha=0.95, max_cvar=500)
        losses = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
        var = ctrl.compute_var(losses)
        assert var > 0

    def test_compute_cvar(self):
        ctrl = CVaRController(alpha=0.95, max_cvar=500)
        losses = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
        cvar = ctrl.compute_cvar(losses)
        assert cvar >= ctrl.compute_var(losses)

    def test_check_cvar_pass(self):
        ctrl = CVaRController(alpha=0.95, max_cvar=1000)
        losses = np.array([1, 2, 3, 4, 5])
        passed, cvar = ctrl.check_cvar(losses)
        assert passed
        assert cvar <= 1000

    def test_check_cvar_fail(self):
        ctrl = CVaRController(alpha=0.95, max_cvar=1.0)
        losses = np.array([10, 20, 30, 40, 50])
        passed, cvar = ctrl.check_cvar(losses)
        assert not passed

    def test_adjust_order_no_change(self):
        ctrl = CVaRController(alpha=0.95, max_cvar=1000)
        losses = np.array([1, 2, 3, 4, 5])
        adjusted = ctrl.adjust_order_for_cvar(10000, losses)
        assert adjusted == pytest.approx(10000)

    def test_adjust_order_reduced(self):
        ctrl = CVaRController(alpha=0.95, max_cvar=1.0)
        losses = np.array([10, 20, 30, 40, 50])
        adjusted = ctrl.adjust_order_for_cvar(10000, losses)
        assert abs(adjusted) < 10000

    def test_empty_losses(self):
        ctrl = CVaRController()
        assert ctrl.compute_var(np.array([])) == 0.0
        assert ctrl.compute_cvar(np.array([])) == 0.0


# ============================================================
# 零知识审计测试
# ============================================================

class TestZeroKnowledgeAudit:
    def test_create_audit_compliant(self):
        audit = ZeroKnowledgeAudit()
        record = audit.create_audit(
            order_shares=5000, violations=[], cvar_value=100, cvar_limit=500,
        )
        assert record.compliant
        assert record.status == AuditStatus.VERIFIED

    def test_create_audit_violation(self):
        audit = ZeroKnowledgeAudit()
        violation = ConstraintViolation(
            type=ViolationType.POSITION_LIMIT, value=150000, limit=100000,
            message="持仓超限",
        )
        record = audit.create_audit(
            order_shares=150000, violations=[violation], cvar_value=100, cvar_limit=500,
        )
        assert not record.compliant
        assert "持仓超限" in record.violations

    def test_verify_audit(self):
        audit = ZeroKnowledgeAudit()
        record = audit.create_audit(
            order_shares=5000, violations=[], cvar_value=100, cvar_limit=500,
        )
        assert audit.verify_audit(record)

    def test_hash_no_strategy_leak(self):
        """审计记录不暴露策略细节 (只有哈希)."""
        audit = ZeroKnowledgeAudit()
        record = audit.create_audit(
            order_shares=12345.67, violations=[], cvar_value=100, cvar_limit=500,
        )
        # order_hash 是哈希, 不是原始订单
        assert record.order_hash != "12345.67"
        assert len(record.order_hash) == 16  # SHA-256 前 16 字符

    def test_summary(self):
        audit = ZeroKnowledgeAudit()
        audit.create_audit(order_shares=5000, violations=[], cvar_value=100, cvar_limit=500)
        audit.create_audit(order_shares=5000, violations=[], cvar_value=200, cvar_limit=500)
        summary = audit.summary()
        assert summary["total_audits"] == 2
        assert summary["compliant_count"] == 2
        assert summary["all_compliant"]


# ============================================================
# 安全执行代理测试
# ============================================================

class TestSafeExecutionAgent:
    def test_compliant_execution(self):
        agent = SafeExecutionAgent()
        result = agent.execute_safely(
            symbol="600519", order_shares=5000, adv=500000,
            current_position=10000, price=1800.0,
        )
        assert result.symbol == "600519"
        assert result.compliant
        assert result.adjusted_order == pytest.approx(5000)

    def test_violation_adjusted(self):
        agent = SafeExecutionAgent()
        result = agent.execute_safely(
            symbol="X", order_shares=200000, adv=1000000,
            current_position=50000,
        )
        # 应该被调整到可行域
        assert abs(result.adjusted_order) < abs(result.original_order)

    def test_cvar_control(self):
        agent = SafeExecutionAgent()
        result = agent.execute_safely(
            symbol="X", order_shares=50000, adv=100000,
        )
        assert result.cvar_value >= 0
        assert result.cvar_limit > 0

    def test_audit_record_created(self):
        agent = SafeExecutionAgent()
        result = agent.execute_safely(
            symbol="X", order_shares=5000, adv=500000,
        )
        assert result.audit is not None
        assert result.audit.status == AuditStatus.VERIFIED

    def test_batch_execute(self):
        agent = SafeExecutionAgent()
        orders = [
            {"symbol": "600519", "order_shares": 5000, "adv": 500000},
            {"symbol": "000001", "order_shares": 2000, "adv": 1000000},
        ]
        results = agent.batch_execute_safely(orders)
        assert len(results) == 2

    def test_compliance_report(self):
        agent = SafeExecutionAgent()
        agent.execute_safely(symbol="X", order_shares=5000, adv=500000)
        report = agent.compliance_report()
        assert "audit" in report
        assert "constraints" in report
        assert report["audit"]["total_audits"] > 0

    def test_custom_constraints(self):
        constraints = ExecutionConstraints(max_position=50000, max_order_size=10000)
        agent = SafeExecutionAgent(constraints=constraints)
        result = agent.execute_safely(
            symbol="X", order_shares=20000, adv=1000000,
            current_position=0,
        )
        assert abs(result.adjusted_order) <= 10000

    def test_no_violation_no_adjustment(self):
        agent = SafeExecutionAgent()
        result = agent.execute_safely(
            symbol="X", order_shares=1000, adv=1000000,
            current_position=0,
        )
        assert result.adjusted_order == pytest.approx(1000)
        assert result.compliant


class TestSafeExecutionResult:
    def test_construction(self):
        audit = AuditRecord(
            order_hash="abc", constraint_hash="def",
            compliant=True, cvar_value=100, cvar_limit=500,
            timestamp="2026", status=AuditStatus.VERIFIED,
        )
        result = SafeExecutionResult(
            symbol="X", original_order=1000, adjusted_order=1000,
            compliant=True, violations=[], cvar_value=100, cvar_limit=500,
            audit=audit,
        )
        assert result.symbol == "X"
        assert result.metadata == {}


# ============================================================
# 验收标准测试
# ============================================================

class TestAcceptanceCriteria:
    """LIT-4.4 验收标准: 无违规 + CVaR 尾部控制 + 零知识审计."""

    def test_no_violation_after_adjustment(self):
        """验收: 调整后订单无违规."""
        agent = SafeExecutionAgent()
        result = agent.execute_safely(
            symbol="600519", order_shares=200000, adv=500000,
            current_position=50000,
        )
        # 即使原始订单违规, 调整后应该合规
        assert result.compliant

    def test_cvar_within_limit(self):
        """验收: CVaR 在阈值内."""
        constraints = ExecutionConstraints(max_cvar=10000)
        agent = SafeExecutionAgent(constraints=constraints)
        result = agent.execute_safely(
            symbol="X", order_shares=5000, adv=500000,
        )
        assert result.cvar_value <= result.cvar_limit or not result.compliant

    def test_zero_knowledge_no_strategy_leak(self):
        """验收: 审计记录不暴露策略细节."""
        agent = SafeExecutionAgent()
        result = agent.execute_safely(
            symbol="X", order_shares=12345.67, adv=500000,
        )
        # 审计哈希不等于原始订单
        assert result.audit.order_hash != str(12345.67)

    def test_all_compliant_in_batch(self):
        """验收: 批量执行全部合规."""
        agent = SafeExecutionAgent()
        orders = [
            {"symbol": "600519", "order_shares": 5000, "adv": 500000, "current_position": 10000},
            {"symbol": "000001", "order_shares": 2000, "adv": 1000000, "current_position": 5000},
            {"symbol": "601318", "order_shares": 3000, "adv": 300000, "current_position": 8000},
        ]
        results = agent.batch_execute_safely(orders)
        assert all(r.compliant for r in results)
