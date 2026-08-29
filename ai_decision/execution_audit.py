"""
ai_decision.execution_audit — 执行审计写入与返回构造
====================================================

从 execution_bridge.py 拆分 (v8.6 重构, 接口完全不变).

包含:
  - _write_execution_audit: 审计日志写入 (JSONL)
  - _build_success_audit_record: 成功执行审计记录构造
  - _build_success_return: 成功执行返回字典构造
  - _EXEC_AUDIT_DIR: 审计目录常量
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ai_decision.execution_risk import ExecutionRiskResult
    from ai_decision.models import TradingDecision

logger = logging.getLogger("ai_decision.execution_audit")

_EXEC_AUDIT_DIR = os.path.join("reports", "ai_decision", "execution")


# ============================================================
# 执行审计
# ============================================================


def _write_execution_audit(record: dict[str, Any]) -> str:
    """写入执行审计日志"""
    os.makedirs(_EXEC_AUDIT_DIR, exist_ok=True)
    path = os.path.join(
        _EXEC_AUDIT_DIR, f"exec_{datetime.now().strftime('%Y%m%d')}.jsonl"
    )
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path
    except OSError as exc:
        logger.warning("执行审计写入失败: %s", exc)
        return ""


# ============================================================
# 成功执行审计记录与返回构造
# ============================================================


def _build_success_audit_record(
    decision: "TradingDecision",
    mode: str,
    execution_plan: dict[str, Any],
    execution_result: dict[str, Any] | None,
    risk_result: "ExecutionRiskResult",
    veto: bool,
    veto_reason: str,
    escalation: bool,
    escalation_reason: str,
    tca_pre_estimate: dict[str, Any] | None,
    tca_post_report: dict[str, Any] | None,
    tca_error: str,
    msg: str,
) -> dict[str, Any]:
    """构建成功/最终执行审计记录"""
    return {
        "timestamp": datetime.now().isoformat(),
        "symbol": decision.symbol,
        "action": decision.action,
        "mode": mode,
        "executed": execution_result is not None
        and execution_result.get("success", False),
        "execution_plan": execution_plan,
        "execution_result": execution_result,
        "risk_checks": risk_result.checks,
        "decision_confidence": decision.confidence,
        "decision_strength": decision.strength,
        "verdict_type": decision.verdict_type,
        "veto": veto,
        "veto_reason": veto_reason,
        "escalation": escalation,
        "escalation_reason": escalation_reason,
        "tca_pre_estimate": tca_pre_estimate,
        "tca_post_report": tca_post_report,
        "tca_error": tca_error,
        "message": msg,
    }


def _build_success_return(
    decision: "TradingDecision",
    mode: str,
    execution_plan: dict[str, Any],
    execution_result: dict[str, Any] | None,
    risk_result: "ExecutionRiskResult",
    audit_path: str,
    msg: str,
    veto: bool,
    veto_reason: str,
    escalation: bool,
    escalation_reason: str,
    tca_pre_estimate: dict[str, Any] | None,
    tca_post_report: dict[str, Any] | None,
    tca_error: str,
) -> dict[str, Any]:
    """构建成功执行后的返回字典"""
    return {
        "executed": execution_result is not None
        and execution_result.get("success", False),
        "mode": mode,
        "execution_plan": execution_plan,
        "execution_result": execution_result,
        "risk_result": risk_result.__dict__,
        "audit_path": audit_path,
        "message": msg,
        "veto": veto,
        "veto_reason": veto_reason,
        "escalation": escalation,
        "escalation_reason": escalation_reason,
        "tca_pre_estimate": tca_pre_estimate,
        "tca_post_report": tca_post_report,
        "tca_error": tca_error,
    }
