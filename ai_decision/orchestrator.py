# -*- coding: utf-8 -*-
"""
ai_decision.orchestrator — 顶层编排流水线
=========================================

全链路:
  SignalRAG -> 多源分析 (五 Agent + 规则信号) -> 辩论/聚合 -> 风控门 -> 输出

  - 集成现有 FinanceAgentOrchestrator (五 Agent 加权投票, 含 RiskAgent 否决)
  - 条件触发 Bull/Bear/Judge 辩论 (仅在多空相反且双方置信度>阈值)
  - 非线性聚合 (Brier 动态权重 + 语义去重 + 多样性奖励) 融合五 Agent 共识
  - 决策门 (硬风控独立 + shadow/paper/auto 模式)
  - 输出 TradingDecision JSON + 审计日志 (reports/ai_decision/)

默认 Shadow 模式, 不触发真实下单. 无 API Key 时全链路走 Mock 优雅降级.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from ai_decision.config import get_config
from ai_decision.consensus_aggregator import aggregate
from ai_decision.debate_engine import run_debate
from ai_decision.decision_gate import RiskContext, apply_mode, run_hard_risk
from ai_decision.health import ModelHealthMonitor, get_default_monitor
from ai_decision.models import (
    DebateDecision,
    DebateRecord,
    DebateTrigger,
    ModelView,
    TradingDecision,
)
from ai_decision.rag_context import build_context, context_to_prompt

logger = logging.getLogger("ai_decision.orchestrator")

_AUDIT_DIR = os.path.join("reports", "ai_decision")


def _ensure_audit_dir() -> None:
    try:
        os.makedirs(_AUDIT_DIR, exist_ok=True)
    except OSError:
        pass


def _write_audit(decision: TradingDecision) -> str:
    """增量写入审计 jsonl"""
    _ensure_audit_dir()
    path = os.path.join(_AUDIT_DIR, f"audit_{datetime.now().strftime('%Y%m%d')}.jsonl")
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(decision.to_dict(), ensure_ascii=False) + "\n")
        return path
    except OSError as exc:  # pragma: no cover
        logger.warning("审计写入失败: %s", exc)
        return ""


def _run_five_agents(symbol: str, ctx) -> Dict[str, Any]:
    """集成现有五 Agent (FinanceAgentOrchestrator), 失败则回退规则兜底"""
    try:
        from utils.finance_agent_orchestrator import FinanceAgentOrchestrator
        orch = FinanceAgentOrchestrator()
        result = orch.orchestrate(symbol=symbol, context={})
        if hasattr(result, "to_dict"):
            return result.to_dict()
        if isinstance(result, dict):
            return result
    except Exception as exc:
        logger.warning("五 Agent 协调器不可用, 使用规则兜底: %s", exc)
    # 规则兜底: 基于行情变化生成中性信号
    md = ctx.market_data
    change = float(md.get("change_pct", 0.0) or 0.0)
    # change_pct 已是小数形式 (0.05 表示 +5%), 直接用作 strength, clamp 到 [-1, 1]
    # 原公式 change * 0.03 单位错配: 0.05 * 0.03 = 0.0015 << 阈值 0.05, 永远触发 hold
    strength = max(-1.0, min(1.0, change))
    action = "buy" if strength > 0.05 else ("sell" if strength < -0.05 else "hold")
    return {
        "action": action,
        "strength": strength,
        "confidence": 0.4,
        "agent_decisions": [],
        "veto": False,
        "veto_reason": "",
        "mode": "shadow",
    }


def run_decision(symbol: str,
                 market_data: Optional[Dict[str, Any]] = None,
                 fundamentals: Optional[Dict[str, Any]] = None,
                 news: Optional[List[Dict[str, Any]]] = None,
                 macro: Optional[Dict[str, Any]] = None,
                 mode: Optional[str] = None,
                 risk_context: Optional[RiskContext] = None,
                 health_monitor: Optional[ModelHealthMonitor] = None,
                 timeout: Optional[int] = None) -> TradingDecision:
    """运行一次完整决策

    Args:
        symbol: 标的代码
        market_data / fundamentals / news / macro: 上游实时数据 (可空, Mock 兜底)
        mode: 覆盖运行模式
        risk_context: 风控运行态 (组合净值/涨跌停/黑名单等)
        health_monitor: 模型健康监控器 (步骤 3); None 时使用全局单例,
            熔断时自动降级 MockProvider, 保证全链路可跑
        timeout: 辩论总超时
    Returns:
        TradingDecision (含完整审计轨迹)
    """
    # 0. 模型健康检查 (步骤 3): 受 probe_interval 控制, 避免频繁调用 API
    # 未传入 health_monitor 时使用全局单例 (保持向后兼容)
    _mon = health_monitor or get_default_monitor()

    # 1. 构建上下文
    ctx = build_context(symbol, market_data, fundamentals, news, macro)

    # 2. 多源分析: 五 Agent + 规则信号
    agent_res = _run_five_agents(symbol, ctx)
    ctx.agent_decisions = agent_res.get("agent_decisions", [])
    ctx.agent_consensus = {
        "action": agent_res.get("action", "hold"),
        "strength": float(agent_res.get("strength", 0.0)),
        "confidence": float(agent_res.get("confidence", 0.0)),
    }

    # 3. 构造多空先验 (从五 Agent 共识 + 规则解析)
    agent_action = ctx.agent_consensus.get("action", "hold")
    agent_strength = ctx.agent_consensus.get("strength", 0.0)
    agent_conf = ctx.agent_consensus.get("confidence", 0.0)

    # 取 bull/bear 两个视角: bull 用正向强度, bear 用负向强度
    bull_prior = ModelView(role="bull", action="buy",
                           strength=max(0.0, agent_strength),
                           confidence=agent_conf)
    bear_prior = ModelView(role="bear", action="sell",
                           strength=min(0.0, agent_strength),
                           confidence=agent_conf)

    trigger = DebateTrigger(
        bull_strength=bull_prior.strength,
        bear_strength=bear_prior.strength,
        bull_conf=bull_prior.confidence,
        bear_conf=bear_prior.confidence,
        debate_threshold=float(get_config("debate.confidence_threshold", 0.6)),
    )

    ctx_prompt = context_to_prompt(ctx)
    debate: Optional[DebateDecision] = None
    debate_record = DebateRecord(symbol=symbol, triggered=False)

    views: List[ModelView] = [bull_prior, bear_prior]

    if trigger.should_debate() and not _mon.is_circuit_open("judge"):
        # 4a. 触发完整辩论
        # 步骤 3 修复: 辩论路径必须先检查 judge 熔断状态, 熔断时降级到快速聚合
        # 否则熔断状态下仍调用真实 provider (浪费 API + 无法降级)
        #辩论失败也需反馈到熔断器 (与快速聚合路径一致)
        try:
            debate_record, debate = run_debate(
                symbol, ctx_prompt, bull_prior, bear_prior, timeout=timeout)
            _mon.record_success("judge")  # 辩论成功反馈
            views.append(ModelView(role="judge", action=debate.action,
                                   strength=debate.strength,
                                   confidence=debate.confidence,
                                   reasoning=debate.summary))
        except Exception as exc:
            # 辩论失败反馈到熔断器 (累计触发熔断)
            _mon.record_failure("judge")
            logger.warning(
                "[Orchestrator] 辩论异常, 降级快速聚合 + 记录 judge 失败: %s", exc
            )
            debate = None
            # 降级到快速聚合路径 (复用 4b 逻辑)
            prov = _mon.get_provider_with_fallback("judge")
            j_txt = prov.generate(ctx_prompt, system="简要给出方向性判断与置信度。",
                                  timeout=timeout or 30)
            if j_txt is None:
                _mon.record_failure("judge")
            else:
                _mon.record_success("judge")
            if j_txt:
                from ai_decision.debate_engine import _parse_strength_conf
                js, jc = _parse_strength_conf(j_txt)
                j_action = "buy" if js > 0.05 else ("sell" if js < -0.05 else "hold")
                views.append(ModelView(role="judge", action=j_action,
                                       strength=js, confidence=jc, reasoning=j_txt[:300]))
    else:
        # 4b. 快速聚合 (不辩论), 用默认 judge=合规视角 Mock 补充一致性
        # 步骤 3: 通过 health_monitor 获取 provider (熔断自动降级 Mock)
        prov = _mon.get_provider_with_fallback("judge")
        j_txt = prov.generate(ctx_prompt, system="简要给出方向性判断与置信度。",
                              timeout=timeout or 30)
        # 业务反馈: 成功/失败累计影响熔断器 (连续失败 3 次触发熔断)
        if j_txt is None:
            _mon.record_failure("judge")
        else:
            _mon.record_success("judge")
        # 即使不辩论, 也用 judge 视角丰富聚合
        if j_txt:
            from ai_decision.debate_engine import _parse_strength_conf
            js, jc = _parse_strength_conf(j_txt)
            j_action = "buy" if js > 0.05 else ("sell" if js < -0.05 else "hold")
            views.append(ModelView(role="judge", action=j_action,
                                   strength=js, confidence=jc, reasoning=j_txt[:300]))

    # 5. 非线性聚合 (融合辩论 + 五 Agent 共识)
    action, strength, confidence = aggregate(
        views, debate=debate, agent_consensus=ctx.agent_consensus)

    verdict_type = debate.verdict_type if debate else "FAST"

    decision = TradingDecision(
        symbol=symbol,
        action=action,
        strength=strength,
        confidence=confidence,
        verdict_type=verdict_type,
        debate=debate_record.to_dict() if debate_record.triggered else None,
        model_views=[v.to_dict() for v in views],
        agent_consensus=ctx.agent_consensus,
        summary=f"多AI共识: action={action}, strength={strength:.3f}, conf={confidence:.3f}, "
                f"verdict={verdict_type}",
    )

    # 6. 决策门 (硬风控 + 模式)
    rc = risk_context or RiskContext(symbol=symbol)
    rc.agent_veto = bool(agent_res.get("veto", False))
    rc.agent_veto_reason = str(agent_res.get("veto_reason", ""))
    gate = run_hard_risk(decision, rc)
    decision = apply_mode(decision, gate, mode=mode)

    # 7. 审计
    _write_audit(decision)
    return decision


def run_batch(symbols: List[str], mode: Optional[str] = None,
              data_provider=None) -> List[TradingDecision]:
    """批量决策; data_provider(symbol)->dict 可选, 提供每只标的上游数据"""
    out: List[TradingDecision] = []
    for sym in symbols:
        kwargs: Dict[str, Any] = {"mode": mode}
        if data_provider:
            try:
                d = data_provider(sym) or {}
                kwargs.update({k: d.get(k) for k in
                               ("market_data", "fundamentals", "news", "macro")
                               if k in d})
            except Exception as exc:  # pragma: no cover
                logger.warning("批量数据获取失败 %s: %s", sym, exc)
        out.append(run_decision(sym, **kwargs))
    return out
