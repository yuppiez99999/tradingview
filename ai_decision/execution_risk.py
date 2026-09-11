"""
ai_decision.execution_risk — L2 执行层硬风控
=============================================

从 execution_bridge.py 拆分 (v8.6 重构, 接口完全不变).

包含:
  - ExecutionRiskResult: L2 风控结果数据类
  - _run_l1_checks: Phase 1 复用 L1 decision_gate.run_hard_risk()
  - _execution_risk_check: L2 执行层硬风控主入口
  - _build_l2_veto_return: L2 风控否决返回构造
  - _build_grayscale_veto_return: 灰度回滚否决返回构造
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ai_decision.decision_gate import RiskContext, run_hard_risk
from ai_decision.execution_audit import _write_execution_audit
from utils.datetime_utils import now_bj

# S-2 (2026-09-11, Issue #13): L2 阈值/默认净值唯一事实源
from utils.risk_thresholds import get_l2_config

if TYPE_CHECKING:
    from ai_decision.models import TradingDecision

logger = logging.getLogger("ai_decision.execution_risk")


# ============================================================
# 执行层硬风控 (L2)
# ============================================================


@dataclass
class ExecutionRiskResult:
    """L2 执行层硬风控结果。

    Attributes:
        passed: 风控是否通过 (veto 取反)
        veto: 是否硬否决 (True 表示拦截下单)
        veto_reason: 否决原因汇总文本
        checks: 各项检查明细键值对
    """

    passed: bool = True
    veto: bool = False
    veto_reason: str = ""
    checks: dict[str, Any] = field(default_factory=dict)


def _run_l1_checks(
    execution_plan: dict[str, Any],
    risk_context: RiskContext,
    decision: TradingDecision,
    portfolio_value: float,
    checks: dict[str, Any],
    veto_reasons: list[str],
    result: ExecutionRiskResult,
) -> None:
    """Phase 1: 复用 L1 decision_gate.run_hard_risk()

    用执行计划的实际名义金额同步给 L1, 确保 single_pct 检查基于真实下单金额。
    合并 L1 检查项到 checks (加 l1_ 前缀避免覆盖 L2 同名字段)。
    提取自 _execution_risk_check, 零行为变更。
    """
    risk_context.portfolio_value = portfolio_value
    risk_context.proposed_notional = float(execution_plan.get("notional", 0.0))
    if not risk_context.symbol:
        risk_context.symbol = execution_plan.get("symbol", decision.symbol)

    l1_result = run_hard_risk(decision, risk_context)
    for k, v in l1_result.risk_checks.items():
        checks[f"l1_{k}"] = v
    if l1_result.veto:
        result.veto = True
        veto_reasons.append(f"[L1] {l1_result.veto_reason}")


def _execution_risk_check(
    execution_plan: dict[str, Any],
    market_state: str = "normal",
    portfolio_value: float | None = None,
    risk_context: RiskContext | None = None,
    decision: TradingDecision | None = None,
    mode: str = "shadow",
) -> ExecutionRiskResult:
    """L2 执行层硬风控 — 下单前最后一次拦截

    两段式检查 (defense in depth, 不重复造轮子):
      Phase 1 — 复用 L1 decision_gate.run_hard_risk() (当 risk_context + decision 可用时):
        - 黑名单 / RiskAgent 否决 / 涨跌停 / 单笔金额上限 / 日内累计上限
      Phase 2 — L2 执行层特有检查 (L1 不覆盖):
        - 价格缺失 (auto 模式硬 veto) / 价格合理性 / 数量合法性 / 流动性 (crisis 禁买) / 名义金额兜底

    Args:
        execution_plan: _generate_execution_plan 产物
        market_state: normal/volatile/illiquid/stress/crisis
        portfolio_value: 组合净值 (L2 名义金额兜底用);
            None 时读 config/risk_thresholds.yaml ``l2_execution.default_portfolio_value``
            (S-2: 原散落硬编码 1_000_000, 与真实 200 万组合不符)
        risk_context: 可选, L1 风控所需运行态数据; 传入则复用 L1 检查
        decision: 可选, 与 risk_context 配对使用, 用于 L1 检查
        mode: 执行模式 (shadow/paper/auto); auto 模式下 price_missing 强制 veto
    Returns:
        ExecutionRiskResult (passed/veto/veto_reason/checks)
    """
    # S-2 修复 (2026-09-11, Issue #13): 净值/上限口径统一从 risk_thresholds 读取,
    # 消除"只有调用方显式传参才用真实组合"的接缝 (原默认 100 万 + 2% 上限)。
    l2_cfg = get_l2_config()
    if portfolio_value is None:
        portfolio_value = float(l2_cfg["default_portfolio_value"])
    else:
        portfolio_value = float(portfolio_value)

    result = ExecutionRiskResult()
    checks: dict[str, Any] = {}
    veto_reasons: list[str] = []

    # ===== Phase 1: 复用 L1 decision_gate.run_hard_risk() =====
    if risk_context is not None and decision is not None:
        _run_l1_checks(
            execution_plan,
            risk_context,
            decision,
            portfolio_value,
            checks,
            veto_reasons,
            result,
        )

    # ===== Phase 2: L2 执行层特有检查 =====
    # 0. 价格缺失检查 (auto 模式硬 veto, 防止以默认价 10.0 灾难性下单)
    price_missing = bool(execution_plan.get("price_missing", False))
    is_auto_mode = mode.startswith("auto") or mode == "auto"
    checks["price_missing"] = {
        "value": price_missing,
        "mode": mode,
        "ok": not (price_missing and is_auto_mode),
    }
    if price_missing and is_auto_mode:
        result.veto = True
        veto_reasons.append(
            f"[L2] 价格缺失且处于 {mode} 模式, 禁止以默认价下单 (paper/shadow 才允许模拟)"
        )

    # 1. 价格合理性 (非零非负, 非异常跳变)
    price = execution_plan.get("limit_price", 0)
    checks["price_valid"] = {"value": price, "ok": price > 0 and price < 10000}
    if not checks["price_valid"]["ok"]:
        result.veto = True
        veto_reasons.append(f"[L2] 价格异常: {price}")

    # 2. 数量合法性 (>=100 股, 100 整数倍)
    qty = execution_plan.get("qty", 0)
    checks["qty_valid"] = {"value": qty, "ok": qty >= 100 and qty % 100 == 0}
    if not checks["qty_valid"]["ok"]:
        result.veto = True
        veto_reasons.append(f"[L2] 数量异常: {qty} (需 >=100 且为 100 整数倍)")

    # 3. 流动性 (crisis 状态禁买)
    if market_state == "crisis" and execution_plan.get("side") == "BUY":
        result.veto = True
        veto_reasons.append("[L2] 市场危机状态, 禁止买入")
    checks["market_state"] = market_state

    # 4. 名义金额兜底 (即使 L1 已检查 single_pct, L2 仍独立兜底, defense in depth)
    notional = execution_plan.get("notional", 0)
    # S-2: 单笔上限走 risk_thresholds (与 AI 子系统的 gate.max_single_pct 解耦,
    # 避免两处默认值漂移; 定价口径见 config/risk_thresholds.yaml ``l2_execution``)
    max_single = float(l2_cfg["max_single_pct"])
    checks["notional"] = {
        "value": round(notional, 2),
        "max": round(portfolio_value * max_single, 2),
        "ok": 0 < notional <= portfolio_value * max_single,
    }
    if not checks["notional"]["ok"]:
        result.veto = True
        veto_reasons.append(
            f"[L2] 名义金额 {notional:.2f} 超过上限 {portfolio_value * max_single:.2f}"
        )

    result.checks = checks
    result.veto_reason = "; ".join(veto_reasons)
    result.passed = not result.veto
    return result


# ============================================================
# 核心桥接函数 - Helper
# ============================================================


def _build_l2_veto_return(
    decision: TradingDecision,
    mode: str,
    risk_result: ExecutionRiskResult,
    escalation: bool,
    escalation_reason: str,
    execution_plan: dict[str, Any],
) -> dict[str, Any]:
    """构建 L2 风控否决时的审计记录和返回字典"""
    escalation_reason = f"L2 风控否决: {risk_result.veto_reason}"
    record = {
        "timestamp": now_bj().isoformat(),
        "symbol": decision.symbol,
        "action": decision.action,
        "mode": mode,
        "executed": False,
        "veto": True,
        "veto_reason": risk_result.veto_reason,
        "escalation": True,
        "escalation_reason": escalation_reason,
        "checks": risk_result.checks,
    }
    _audit_path = _write_execution_audit(record)
    return {
        "executed": False,
        "mode": mode,
        "execution_plan": execution_plan,
        "execution_result": None,
        "risk_result": risk_result.__dict__,
        "audit_path": _audit_path,
        "message": f"L2 执行风控否决: {risk_result.veto_reason}",
        "veto": True,
        "veto_reason": risk_result.veto_reason,
        "escalation": True,
        "escalation_reason": escalation_reason,
        "tca_pre_estimate": None,
        "tca_post_report": None,
        "tca_error": "",
    }


def _build_grayscale_veto_return(
    decision: TradingDecision,
    mode: str,
    execution_plan: dict[str, Any],
    risk_result: ExecutionRiskResult,
    tca_pre_estimate: dict[str, Any] | None,
    tca_error: str,
    veto_reason: str,
    escalation: bool,
    escalation_reason: str,
    msg: str,
) -> dict[str, Any]:
    """构建灰度回滚到 0 时的审计记录和返回字典"""
    record = {
        "timestamp": now_bj().isoformat(),
        "symbol": decision.symbol,
        "action": decision.action,
        "mode": mode,
        "executed": False,
        "veto": True,
        "veto_reason": veto_reason,
        "escalation": True,
        "escalation_reason": escalation_reason,
    }
    _audit_path = _write_execution_audit(record)
    return {
        "executed": False,
        "mode": mode,
        "execution_plan": execution_plan,
        "execution_result": None,
        "risk_result": risk_result.__dict__,
        "audit_path": _audit_path,
        "message": msg,
        "veto": True,
        "veto_reason": veto_reason,
        "escalation": True,
        "escalation_reason": escalation_reason,
        "tca_pre_estimate": tca_pre_estimate,
        "tca_post_report": None,
        "tca_error": tca_error,
    }
