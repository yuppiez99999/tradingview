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
from concurrent.futures import wait as cf_wait

from ai_decision.config import get_config
from ai_decision.models import (
    DebateDecision,
    DebateRecord,
    ModelView,
)
from ai_decision.providers import get_active_provider

logger = logging.getLogger("ai_decision.debate_engine")

# ── Bull/Bear/Judge 系统提示词 v2.0 (2026-08-07 升级) ──
# 升级要点: 强制 JSON 输出 + 事实引用约束 + 量化强度/置信 + 裁判矩阵

_SYSTEM_BULL = (
    "你是资深多头分析师（买方思维，非卖方）。"
    "你的任务是基于给定的事实底座，构建看多的最强论证链路——不是稻草人，是真正能说服你的论证。\n\n"
    "## 论证框架\n"
    "1. 市场低估了什么？（被忽略的边际改善、被错误定价的催化剂）\n"
    "2. 哪个关键变量正在改善且具持续性？（引用事实底座中的具体数据点）\n"
    "3. 验证后股价弹性来源？（盈利上修 / 估值扩张 / 情绪修复 / 资金流入）\n\n"
    "## 约束\n"
    "- 每个论点必须引用事实底座中的具体数据，格式: [数据: xxx]\n"
    "- 必须指出你的最脆弱假设（the weakest link）\n"
    "- 强度 1-10 的含义: 1-3(轻仓试探) / 4-6(标准仓位) / 7-8(偏高仓位) / 9-10(重仓表达)\n"
    "- 置信度 0-1: 0.3 以下=猜测 / 0.3-0.5=弱信号 / 0.5-0.7=中等 / 0.7-0.9=强 / 0.9+=极强\n\n"
    "## 输出格式 (严格 JSON，不要其他文字)\n"
    "```json\n"
    "{\n"
    '  "direction": "bullish",\n'
    '  "strength": 1-10,\n'
    '  "confidence": 0.0-1.0,\n'
    '  "core_thesis": "一句话核心观点",\n'
    '  "evidence": ["证据1 [数据: xxx]", "证据2 [数据: xxx]", "证据3 [数据: xxx]"],\n'
    '  "key_catalyst": "未来1-4周最关键催化事件",\n'
    '  "weakest_assumption": "最脆弱假设，什么情况会推翻整个论证",\n'
    '  "price_elasticity": "弹性来源: 盈利上修/估值扩张/情绪修复/资金流入"\n'
    "}\n"
    "```\n"
    "只输出 JSON，不要分析过程。"
)
_SYSTEM_BEAR = (
    "你是资深空头分析师（买方思维，非卖方）。"
    "你的任务是基于给定的事实底座，构建看空的最强论证链路——不是稻草人，是真正能说服你的论证。\n\n"
    "## 论证框架\n"
    "1. 市场高估了什么？（过度乐观的假设、未被定价的下行风险）\n"
    "2. 边际改善是否只是噪音？（区分趋势性改善 vs 一次性/季节性因素）\n"
    "3. 哪些硬约束会阻碍预期兑现？（产能/政策/竞争/流动性）\n"
    "4. 不及预期时的反噬路径？（先杀估值还是先杀盈利？传导速度？）\n\n"
    "## 约束\n"
    "- 每个论点必须引用事实底座中的具体数据，格式: [数据: xxx]\n"
    "- 必须指出你的最脆弱假设（the weakest link）\n"
    "- 强度 1-10 的含义: 1-3(轻仓试探) / 4-6(标准仓位) / 7-8(偏高仓位) / 9-10(重仓表达)\n"
    "- 置信度 0-1: 0.3 以下=猜测 / 0.3-0.5=弱信号 / 0.5-0.7=中等 / 0.7-0.9=强 / 0.9+=极强\n\n"
    "## 输出格式 (严格 JSON，不要其他文字)\n"
    "```json\n"
    "{\n"
    '  "direction": "bearish",\n'
    '  "strength": 1-10,\n'
    '  "confidence": 0.0-1.0,\n'
    '  "core_thesis": "一句话核心观点",\n'
    '  "evidence": ["证据1 [数据: xxx]", "证据2 [数据: xxx]", "证据3 [数据: xxx]"],\n'
    '  "key_trigger": "最可能触发下跌的事件或信号",\n'
    '  "weakest_assumption": "最脆弱假设，什么情况会推翻整个论证",\n'
    '  "feedback_path": "反噬路径: 先杀估值/先杀盈利/流动性枯竭"\n'
    "}\n"
    "```\n"
    "只输出 JSON，不要分析过程。"
)
_SYSTEM_JUDGE = (
    "你是首席仲裁官。综合多空双方最强论证与事实底座，做出裁决。\n\n"
    "## 裁决标准矩阵\n"
    "| 维度 | 权重 | 判断标准 |\n"
    "|------|------|----------|\n"
    "| 证据直接性 | 35% | 引用的数据点是直接相关还是间接推断 |\n"
    "| 证据可验证性 | 25% | 是否可被后续数据确认或证伪 |\n"
    "| 时间敏感度 | 20% | 催化剂在 1-4 周内兑现的概率 |\n"
    "| 假设脆弱性 | 20% | 哪方的最脆弱假设更容易被击穿 |\n\n"
    "## 输出格式 (严格 JSON，不要其他文字)\n"
    "```json\n"
    "{\n"
    '  "verdict": "bullish|bearish|neutral",\n'
    '  "confidence": 0.0-1.0,\n'
    '  "key_reason": "最关键的一条裁决理由",\n'
    '  "decisive_factor": "决定性因素（哪条证据起了关键作用）",\n'
    '  "verification_points": ["后续必须跟踪的验证点1", "验证点2", "验证点3"],\n'
    '  "risk_note": "即使在裁决方向上，最需要注意的风险"\n'
    "}\n"
    "```\n"
    "只输出 JSON，不要分析过程。"
)


def _parse_role_json(text: str) -> dict | None:
    """解析 Bull/Bear/Judge 的 JSON 输出 (v2.0 JSON 优先, 兼容旧文本回退)"""
    if not text:
        return None
    # 尝试 JSON 解析
    import json as _json

    json_match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if json_match:
        try:
            return _json.loads(json_match.group())
        except (_json.JSONDecodeError, ValueError):
            pass
    return None


def _parse_strength_conf(text: str) -> tuple[float, float]:
    """解析强度 [-1,1] 与置信度 [0,1]
    v2.0: JSON 优先 (从 direction/strength/confidence 字段提取)
    回退: 旧版关键词计数 (兼容非 JSON 模型输出)
    """
    strength = 0.0
    conf = 0.5
    if not text:
        return 0.0, 0.3

    # v2.0: 尝试 JSON 解析
    parsed = _parse_role_json(text)
    if parsed:
        direction = str(parsed.get("direction", parsed.get("verdict", ""))).lower()
        raw_strength = parsed.get("strength", 0)
        raw_conf = parsed.get("confidence", 0.5)

        # 方向 → 正负号
        if direction in ("bullish", "看多"):
            strength = min(1.0, max(0.1, float(raw_strength) / 10.0))
        elif direction in ("bearish", "看空"):
            strength = max(-1.0, -min(1.0, float(raw_strength) / 10.0))
        else:
            strength = 0.0

        try:
            conf = max(0.0, min(1.0, float(raw_conf)))
        except (ValueError, TypeError):
            conf = 0.5
        return strength, conf

    # 回退: 旧版关键词计数 (兼容非 JSON 模型)
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


def _should_debate(bull_prior: ModelView, bear_prior: ModelView) -> bool:
    """判断是否需要触发辩论 (v2.0 快速路径)
    方向一致 + 双方置信度 > 0.7 → 无需辩论，直接聚合
    方向一致 + 置信度中等 → 仍辩论以发现盲点
    方向相反 → 必须辩论
    """
    cfg = get_config("debate", {})
    min_conf = float(cfg.get("skip_debate_min_confidence", 0.7))
    if (
        bull_prior.action == bear_prior.action
        and bull_prior.confidence >= min_conf
        and bear_prior.confidence >= min_conf
    ):
        return False
    return True


def _call_role(role: str, prompt: str, system: str, timeout: int) -> str | None:
    prov = get_active_provider(role)
    return prov.generate(prompt, system=system, timeout=timeout)


def run_debate(
    symbol: str,
    context_prompt: str,
    bull_prior: ModelView,
    bear_prior: ModelView,
    timeout: int | None = None,
) -> tuple[DebateRecord, DebateDecision]:
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

    base_prompt = f"{context_prompt}\n\n" "请基于以上事实底座, 给出你的结构化论证。"

    # 首轮: Bull 与 Bear 并行
    # H2 修复 (2026-09-13): 用 wait(timeout) + shutdown(wait=False) 取代
    # with-block 内逐个 result(timeout) — 原实现首个 future 超时后, with 退出
    # 时 shutdown(wait=True) 仍会阻塞等待挂死线程, "超时降级"实际不设界。
    # 超时后放弃未完成 future (cancel_futures 尽力回收), 主流程按预算继续。
    ex = ThreadPoolExecutor(max_workers=2)
    try:
        f_bull = ex.submit(_call_role, "bull", base_prompt, _SYSTEM_BULL, timeout)
        f_bear = ex.submit(_call_role, "bear", base_prompt, _SYSTEM_BEAR, timeout)
        _done, _pending = cf_wait([f_bull, f_bear], timeout=timeout)
        if _pending:
            logger.warning(
                "[Debate] 首轮 %.0fs 超时, 放弃 %d 个未完成 future", timeout, len(_pending)
            )
        bull_txt = f_bull.result() if f_bull.done() else ""
        bear_txt = f_bear.result() if f_bear.done() else ""
    finally:
        ex.shutdown(wait=False, cancel_futures=True)

    record.bull_rounds.append(bull_txt)
    record.bear_rounds.append(bear_txt)
    record.rounds = 1

    # 次轮: 让双方看到对方首轮论证后反驳 (若 max_rounds>1)
    if max_rounds > 1 and bull_txt and bear_txt:
        rebut_prompt = (
            f"{base_prompt}\n\n"
            f"【对方(看空)首轮论证】\n{bear_txt}\n\n"
            "## 反驳任务\n"
            "1. 逐条反驳对方的核心证据（指出数据引用错误、逻辑漏洞或忽略的关键信息）\n"
            "2. 若对方指出的最脆弱假设确实成立，降低你的置信度\n"
            "3. 若对方论证有合理之处但不足以推翻你看多，说明为什么\n"
            "4. 修正你的 core_thesis 和 confidence\n\n"
            "请输出修正后的 JSON（包含所有字段）。"
        )
        rebut_prompt_bear = (
            f"{base_prompt}\n\n"
            f"【对方(看多)首轮论证】\n{bull_txt}\n\n"
            "## 反驳任务\n"
            "1. 逐条反驳对方的核心证据（指出数据引用错误、逻辑漏洞或忽略的关键信息）\n"
            "2. 若对方指出的最脆弱假设确实成立，降低你的置信度\n"
            "3. 若对方论证有合理之处但不足以推翻你看空，说明为什么\n"
            "4. 修正你的 core_thesis 和 confidence\n\n"
            "请输出修正后的 JSON（包含所有字段）。"
        )
        # 次轮同样按预算收口 (H2: wait + 非阻塞 shutdown)
        ex2 = ThreadPoolExecutor(max_workers=2)
        try:
            f_bull2 = ex2.submit(_call_role, "bull", rebut_prompt, _SYSTEM_BULL, timeout)
            f_bear2 = ex2.submit(
                _call_role, "bear", rebut_prompt_bear, _SYSTEM_BEAR, timeout
            )
            _done2, _pending2 = cf_wait([f_bull2, f_bear2], timeout=timeout)
            if _pending2:
                logger.warning(
                    "[Debate] 次轮 %.0fs 超时, 放弃 %d 个未完成 future",
                    timeout,
                    len(_pending2),
                )
            bull_txt2 = f_bull2.result() if f_bull2.done() else ""
            bear_txt2 = f_bear2.result() if f_bear2.done() else ""
        finally:
            ex2.shutdown(wait=False, cancel_futures=True)
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

    # 解析裁决 -> DebateDecision (v2.0 JSON 优先)
    j_strength, j_conf = _parse_strength_conf(judge_txt)
    parsed_judge = _parse_role_json(judge_txt)

    # 从 JSON 提取裁决方向
    if parsed_judge:
        verdict_raw = str(parsed_judge.get("verdict", "")).lower()
        if verdict_raw in ("bullish", "看多"):
            action = "buy"
        elif verdict_raw in ("bearish", "看空"):
            action = "sell"
        else:
            action = "hold"
        # 提取裁决理由作为 summary
        summary = parsed_judge.get("key_reason", judge_txt[:500])
    else:
        # 回退: 文本关键词匹配
        if "看多" in judge_txt and "暂不" not in judge_txt:
            action = "buy"
        elif "看空" in judge_txt and "暂不" not in judge_txt:
            action = "sell"
        else:
            action = "hold"
        summary = judge_txt[:500]

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
        summary=summary,
    )
    return record, decision
