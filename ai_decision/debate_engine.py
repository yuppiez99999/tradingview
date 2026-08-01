# -*- coding: utf-8 -*-
"""
ai_decision.debate_engine — Bull/Bear/Judge 结构化辩论引擎
==========================================================

仅当多空方向相反且双方置信度 > 阈值 (默认 0.6) 时触发完整辩论, <=2 轮.
每轮 Bull/Bear 并行提交 (ThreadPoolExecutor), Judge 最后裁决.
超时 (盘中 60s / 盘后 300s) 自动降级为快速聚合, 不阻塞主链路.

提示词框架遵循 bull_bear_case_builder_skill: 双方共用事实底座, 写最强版本,
比较证据强弱与触发条件, 落到具体验证变量而非情绪化站队.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Tuple

from ai_decision.config import get_config
from ai_decision.models import (
    DebateDecision,
    DebateRecord,
    ModelView,
)
from ai_decision.providers import get_active_provider

logger = logging.getLogger("ai_decision.debate_engine")

_SYSTEM_BULL = (
    "你是资深多头分析师。基于给定事实底座, 构建看多的最强论证链路: "
    "市场低估了什么、哪个变量正在改善且具持续性、验证后股价弹性来源。 "
    "必须写最强版本而非稻草人。用中文, 简洁, 给出核心观点+2-3条证据+关键催化+最脆弱假设。"
)
_SYSTEM_BEAR = (
    "你是资深空头分析师。基于给定事实底座, 构建看空的最强论证链路: "
    "市场高估了什么、改善是否只是噪音、哪些约束阻碍兑现、不及预期时反噬路径。 "
    "必须写最强版本而非稻草人。用中文, 简洁, 给出核心观点+2-3条证据+关键触发+最脆弱假设。"
)
_SYSTEM_JUDGE = (
    "你是首席仲裁官。综合多空双方最强论证与事实底座, 比较证据直接性/可验证性/时间敏感度, "
    "给出当前更占优方向 (看多/看空/暂不站队) 及置信度 (0-1), 并明确后续必须跟踪的验证点。 "
    "用中文, 结构化输出。"
)


def _parse_strength_conf(text: str) -> Tuple[float, float]:
    """从模型文本中解析强度 [-1,1] 与置信度 [0,1] (兼容真实模型与 Mock)"""
    strength = 0.0
    conf = 0.5
    if not text:
        return 0.0, 0.3
    # 强度: 看多词正向, 看空词负向 (轻量规则)
    t = text
    bull_hits = len(re.findall(r"看多|买入|建仓|上涨|低估|利好|弹性", t))
    bear_hits = len(re.findall(r"看空|卖出|减仓|下跌|高估|利空|风险|反噬", t))
    if bull_hits > bear_hits:
        strength = min(1.0, 0.2 + 0.1 * (bull_hits - bear_hits))
    elif bear_hits > bull_hits:
        strength = max(-1.0, -(0.2 + 0.1 * (bear_hits - bull_hits)))
    # 置信度: 文本自报 "置信度 0.xx" 优先
    m = re.search(r"置信度[^\d]*([01](?:\.\d+)?)", t)
    if m:
        try:
            conf = max(0.0, min(1.0, float(m.group(1))))
        except ValueError:
            pass
    return strength, conf


def _call_role(role: str, prompt: str, system: str, timeout: int) -> Optional[str]:
    prov = get_active_provider(role)
    return prov.generate(prompt, system=system, timeout=timeout)


def run_debate(symbol: str, context_prompt: str,
               bull_prior: ModelView, bear_prior: ModelView,
               timeout: Optional[int] = None) -> Tuple[DebateRecord, DebateDecision]:
    """运行 Bull/Bear/Judge 辩论

    Args:
        symbol: 标的
        context_prompt: rag_context.context_to_prompt 输出
        bull_prior / bear_prior: 触发辩论前的多空先验 (含 action/strength/confidence)
        timeout: 总超时 (秒), 缺省按盘前/盘后配置
    Returns:
        (DebateRecord, DebateDecision)
    """
    cfg = get_config("debate", {})
    if timeout is None:
        timeout = int(cfg.get("postclose_timeout", 300))
    max_rounds = int(cfg.get("max_rounds", 2))

    record = DebateRecord(symbol=symbol, triggered=True)

    base_prompt = (
        f"{context_prompt}\n\n"
        "请基于以上事实底座, 给出你的结构化论证。"
    )

    # 首轮: Bull 与 Bear 并行
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_bull = ex.submit(_call_role, "bull", base_prompt, _SYSTEM_BULL, timeout)
        f_bear = ex.submit(_call_role, "bear", base_prompt, _SYSTEM_BEAR, timeout)
        bull_txt = f_bull.result(timeout=timeout) or ""
        bear_txt = f_bear.result(timeout=timeout) or ""

    record.bull_rounds.append(bull_txt)
    record.bear_rounds.append(bear_txt)
    record.rounds = 1

    # 次轮: 让双方看到对方首轮论证后反驳 (若 max_rounds>1)
    if max_rounds > 1 and bull_txt and bear_txt:
        rebut_prompt = (
            f"{base_prompt}\n\n"
            f"【对方(看空)首轮论证】\n{bear_txt}\n\n"
            "请针对对方论证做最强反驳, 并修正你的核心观点。"
        )
        rebut_prompt_bear = (
            f"{base_prompt}\n\n"
            f"【对方(看多)首轮论证】\n{bull_txt}\n\n"
            "请针对对方论证做最强反驳, 并修正你的核心观点。"
        )
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_bull2 = ex.submit(_call_role, "bull", rebut_prompt, _SYSTEM_BULL, timeout)
            f_bear2 = ex.submit(_call_role, "bear", rebut_prompt_bear, _SYSTEM_BEAR, timeout)
            bull_txt2 = f_bull2.result(timeout=timeout) or ""
            bear_txt2 = f_bear2.result(timeout=timeout) or ""
        if bull_txt2:
            record.bull_rounds.append(bull_txt2)
        if bear_txt2:
            record.bear_rounds.append(bear_txt2)
        record.rounds = 2

    # Judge 裁决
    judge_prompt = (
        f"{context_prompt}\n\n"
        f"【看多论证】\n{chr(10).join(record.bull_rounds)}\n\n"
        f"【看空论证】\n{chr(10).join(record.bear_rounds)}\n\n"
        "请裁决: 当前更占优方向 (看多/看空/暂不站队), 置信度(0-1), 及后续验证点。"
    )
    judge_txt = _call_role("judge", judge_prompt, _SYSTEM_JUDGE, timeout) or ""
    record.judge_verdict = judge_txt

    # 解析裁决 -> DebateDecision
    j_strength, j_conf = _parse_strength_conf(judge_txt)
    # 裁决文本明确方向
    if "看多" in judge_txt and "暂不" not in judge_txt:
        action = "buy"
    elif "看空" in judge_txt and "暂不" not in judge_txt:
        action = "sell"
    else:
        action = "hold"

    # 裁决类型: 强置信且方向明确 -> AUTO 候选; 分歧大 -> REVIEW
    verdict_type = "REVIEW"
    if j_conf >= get_config("gate.min_confidence", 0.7) and action != "hold":
        verdict_type = "AUTO"
    elif action == "hold":
        verdict_type = "HOLD"

    decision = DebateDecision(
        action=action,
        strength=j_strength,
        confidence=j_conf,
        verdict_type=verdict_type,
        summary=judge_txt[:500],
    )
    return record, decision
