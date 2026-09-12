"""param_adjustment_governor 单元测试 — 参数调整治理器全覆盖.

被测模块: utils/param_adjustment_governor.py
覆盖目标: >=90%
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from utils.datetime_utils import now_bj

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.param_adjustment_governor import (  # noqa: E402
    AdjustmentReason,
    AdjustmentRequest,
    ParameterAdjustmentGovernor,
    RejectionCode,
)

# ============================================================
# AdjustmentRequest
# ============================================================


class TestAdjustmentRequest:
    def test_basic(self):
        req = AdjustmentRequest(
            param="max_weight",
            old_value=0.10,
            new_value=0.08,
            reason="ic_decay",
            operator="rm",
        )
        assert req.param == "max_weight"
        assert req.value_changed is True

    def test_no_change(self):
        req = AdjustmentRequest(
            param="x", old_value=0.1, new_value=0.1, reason="ic_decay", operator="rm"
        )
        assert req.value_changed is False

    def test_auto_timestamp(self):
        req = AdjustmentRequest(
            param="x", old_value=0, new_value=1, reason="ic_decay", operator="rm"
        )
        assert req.requested_at is not None


# ============================================================
# ParameterAdjustmentGovernor.request (审批逻辑)
# ============================================================


class TestRequest:
    def test_approve(self):
        gov = ParameterAdjustmentGovernor(require_evidence=False)
        req = AdjustmentRequest(
            param="x", old_value=0.1, new_value=0.08, reason="ic_decay", operator="rm"
        )
        result = gov.request(req)
        assert result.approved is True

    def test_reject_duplicate(self):
        gov = ParameterAdjustmentGovernor(require_evidence=False)
        req = AdjustmentRequest(
            param="x", old_value=0.1, new_value=0.1, reason="ic_decay", operator="rm"
        )
        result = gov.request(req)
        assert result.approved is False
        assert result.rejection_code == RejectionCode.DUPLICATE_REQUEST

    def test_reject_invalid_reason(self):
        gov = ParameterAdjustmentGovernor(require_evidence=False)
        req = AdjustmentRequest(
            param="x",
            old_value=0.1,
            new_value=0.08,
            reason="invalid_reason",
            operator="rm",
        )
        result = gov.request(req)
        assert result.approved is False
        assert result.rejection_code == RejectionCode.INVALID_REASON

    def test_reject_missing_evidence(self):
        gov = ParameterAdjustmentGovernor(require_evidence=True)
        req = AdjustmentRequest(
            param="x",
            old_value=0.1,
            new_value=0.08,
            reason="ic_decay",
            operator="rm",
            evidence={},
        )
        result = gov.request(req)
        assert result.approved is False
        assert result.rejection_code == RejectionCode.MISSING_EVIDENCE

    def test_approve_with_evidence(self):
        gov = ParameterAdjustmentGovernor(require_evidence=True)
        req = AdjustmentRequest(
            param="x",
            old_value=0.1,
            new_value=0.08,
            reason="ic_decay",
            operator="rm",
            evidence={"ic": 0.02},
        )
        result = gov.request(req)
        assert result.approved is True

    def test_manual_override_no_evidence_needed(self):
        gov = ParameterAdjustmentGovernor(require_evidence=True)
        req = AdjustmentRequest(
            param="x",
            old_value=0.1,
            new_value=0.08,
            reason="manual_override",
            operator="rm",
        )
        result = gov.request(req)
        assert result.approved is True

    def test_reject_cooldown(self):
        gov = ParameterAdjustmentGovernor(min_interval_days=30, require_evidence=False)
        req1 = AdjustmentRequest(
            param="x", old_value=0.1, new_value=0.08, reason="ic_decay", operator="rm"
        )
        result1 = gov.request(req1)
        gov.commit(result1)

        req2 = AdjustmentRequest(
            param="x", old_value=0.08, new_value=0.06, reason="ic_decay", operator="rm"
        )
        result2 = gov.request(req2)
        assert result2.approved is False
        assert result2.rejection_code == RejectionCode.COOLDOWN_ACTIVE

    def test_reject_monthly_limit(self):
        gov = ParameterAdjustmentGovernor(
            min_interval_days=0, max_per_month=1, require_evidence=False
        )
        req1 = AdjustmentRequest(
            param="x", old_value=0.1, new_value=0.08, reason="ic_decay", operator="rm"
        )
        gov.commit(gov.request(req1))

        req2 = AdjustmentRequest(
            param="x",
            old_value=0.08,
            new_value=0.06,
            reason="oos_gap_critical",
            operator="rm",
            evidence={"gap": 0.1},
        )
        result2 = gov.request(req2)
        assert result2.approved is False
        assert result2.rejection_code == RejectionCode.MONTHLY_LIMIT_EXCEEDED


# ============================================================
# commit
# ============================================================


class TestCommit:
    def test_commit_approved(self):
        gov = ParameterAdjustmentGovernor(require_evidence=False)
        req = AdjustmentRequest(
            param="x", old_value=0.1, new_value=0.08, reason="ic_decay", operator="rm"
        )
        result = gov.request(req)
        record = gov.commit(result)
        assert record.param == "x"
        assert record.new_value == 0.08

    def test_commit_rejected_raises(self):
        gov = ParameterAdjustmentGovernor(require_evidence=False)
        req = AdjustmentRequest(
            param="x", old_value=0.1, new_value=0.1, reason="ic_decay", operator="rm"
        )
        result = gov.request(req)
        with pytest.raises(ValueError):
            gov.commit(result)


# ============================================================
# rollback
# ============================================================


class TestRollback:
    def test_rollback(self):
        gov = ParameterAdjustmentGovernor(require_evidence=False)
        req = AdjustmentRequest(
            param="x", old_value=0.1, new_value=0.08, reason="ic_decay", operator="rm"
        )
        gov.commit(gov.request(req))

        record = gov.rollback("x", operator="rm", reason="bad result")
        assert record is not None
        assert record.new_value == 0.1

    def test_rollback_no_history(self):
        gov = ParameterAdjustmentGovernor()
        record = gov.rollback("nonexistent", operator="rm")
        assert record is None


# ============================================================
# get_history / get_cooldown_status
# ============================================================


class TestQuery:
    def test_get_history_all(self):
        gov = ParameterAdjustmentGovernor(require_evidence=False)
        req = AdjustmentRequest(
            param="x", old_value=0.1, new_value=0.08, reason="ic_decay", operator="rm"
        )
        gov.commit(gov.request(req))
        history = gov.get_history()
        assert len(history) == 1

    def test_get_history_by_param(self):
        gov = ParameterAdjustmentGovernor(require_evidence=False)
        req = AdjustmentRequest(
            param="x", old_value=0.1, new_value=0.08, reason="ic_decay", operator="rm"
        )
        gov.commit(gov.request(req))
        history = gov.get_history("x")
        assert len(history) == 1
        history_empty = gov.get_history("y")
        assert len(history_empty) == 0

    def test_cooldown_no_history(self):
        gov = ParameterAdjustmentGovernor()
        status = gov.get_cooldown_status("x")
        assert status["in_cooldown"] is False

    def test_cooldown_active(self):
        gov = ParameterAdjustmentGovernor(min_interval_days=30, require_evidence=False)
        req = AdjustmentRequest(
            param="x", old_value=0.1, new_value=0.08, reason="ic_decay", operator="rm"
        )
        gov.commit(gov.request(req))
        status = gov.get_cooldown_status("x")
        assert status["in_cooldown"] is True
        assert status["days_until_available"] > 0

    def test_cooldown_expired(self):
        gov = ParameterAdjustmentGovernor(min_interval_days=1, require_evidence=False)
        req = AdjustmentRequest(
            param="x", old_value=0.1, new_value=0.08, reason="ic_decay", operator="rm"
        )
        gov.commit(gov.request(req))
        future = now_bj() + timedelta(days=10)
        status = gov.get_cooldown_status("x", now=future)
        assert status["in_cooldown"] is False


# ============================================================
# 持久化
# ============================================================


class TestPersistence:
    def test_save_and_load(self, tmp_path):
        history_file = tmp_path / "history.json"
        gov1 = ParameterAdjustmentGovernor(
            require_evidence=False, history_file=history_file
        )
        req = AdjustmentRequest(
            param="x", old_value=0.1, new_value=0.08, reason="ic_decay", operator="rm"
        )
        gov1.commit(gov1.request(req))

        gov2 = ParameterAdjustmentGovernor(history_file=history_file)
        history = gov2.get_history()
        assert len(history) == 1
        assert history[0].param == "x"

    def test_load_nonexistent(self, tmp_path):
        gov = ParameterAdjustmentGovernor(history_file=tmp_path / "nonexistent.json")
        assert len(gov.get_history()) == 0


# ============================================================
# AdjustmentReason / RejectionCode enums
# ============================================================


class TestEnums:
    def test_adjustment_reasons(self):
        assert AdjustmentReason.IC_DECAY.value == "ic_decay"
        assert AdjustmentReason.MANUAL_OVERRIDE.value == "manual_override"

    def test_rejection_codes(self):
        assert RejectionCode.COOLDOWN_ACTIVE.value == "cooldown_active"
        assert RejectionCode.DUPLICATE_REQUEST.value == "duplicate"
