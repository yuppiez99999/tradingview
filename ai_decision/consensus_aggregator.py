"""
ai_decision.consensus_aggregator — 非线性共识聚合器
===================================================

将"五 Agent 加权投票结果"与"辩论裁决结果"统一融合, 采用:
  - Brier Score 动态权重: 基于 ai_decision_accuracy 表滚动窗口计算每个来源的
    历史校准度, 校准越好权重越高。
    ⚠ 现状如实标注 (M2 修复 2026-09-13): 写入链路尚未建成 (data/ 下无任何
    进程回填 role/brier 记录, 原注释"与 AICoordinator 约定对齐"失实 —
    ai_coordinator.db 无 role/brier 列, schema 不兼容), 当前恒退化为均匀
    权重。回退时每进程告警一次。激活条件: 决策审计回填 brier 记录后自动生效,
    无需改代码。
  - 语义去重: 词重叠近似相似度 (M3 修复 2026-09-13: 中文按字符 2-gram,
    英文按词 — 原 .split() 对中文整句单 token, Jaccard 只能取 0/1, 去重失效)。

硬风控/RiskAgent veto 由 decision_gate 处理, 此处只负责灰色地带的共识合成.
"""

from __future__ import annotations

import logging
import math
import re
import sqlite3
from collections import Counter

from ai_decision.config import get_config
from ai_decision.models import DebateDecision, ModelView

logger = logging.getLogger("ai_decision.consensus_aggregator")

_DB_PATH = "data/ai_decision_accuracy.db"
_BRIER_FALLBACK_LOGGED = False  # 每进程只告警一次 (均匀权重退化)

# 英文词 + 中文单字 (供 2-gram 组合)
_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]")


def _tokens(text: str) -> set[str]:
    """分词: 英文按词, 中文按字符 2-gram (M3 修复 2026-09-13)。

    原 `a.split()` 对中文整句产生单个 token, Jaccard 只能取 0 或 1,
    0.6 阈值几乎只在文本完全相同时触发 — 同源/复读观点重复计权,
    "多模型共识"可能是同一信息的多次回声。
    """
    tokens = {t.lower() for t in _TOKEN_RE.findall(text)}
    words = {t for t in tokens if t.isascii()}
    cjk = [t for t in tokens if not t.isascii()]
    bigrams = {f"{cjk[i]}{cjk[i + 1]}" for i in range(len(cjk) - 1)}
    return words | bigrams


def _word_overlap(a: str, b: str) -> float:
    """近似相似度 (Jaccard), 用于语义去重 — 中英文混合鲁棒。"""
    sa = _tokens(a)
    sb = _tokens(b)
    if not sa and not sb:
        return 0.0
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def _load_brier_weights(roles: list[str], window_days: int) -> dict[str, float]:
    """从 ai_decision_accuracy 表加载各角色 Brier 动态权重

    表结构对齐 AICoordinator: (role, decision_date, predicted_strength,
    actual_outcome, brier). 权重 = softmax(-mean_brier).
    若表不可用, 返回均匀权重 (每进程告警一次, 如实暴露退化状态).

    M2 如实标注 (2026-09-13): 写入链路尚未建成 (无进程回填 role/brier 记录),
    本函数当前恒走均匀权重路径 — 权重机制处于休眠状态, 激活条件见模块 docstring.
    """
    global _BRIER_FALLBACK_LOGGED
    uniform = {r: 1.0 / max(1, len(roles)) for r in roles}
    if not roles:
        return uniform
    try:
        conn = sqlite3.connect(_DB_PATH)
        cur = conn.cursor()
        briers: dict[str, list[float]] = {r: [] for r in roles}
        for r in roles:
            try:
                cur.execute(
                    "SELECT brier FROM ai_decision_accuracy "
                    "WHERE role=? AND decision_date >= date('now', ?) ",
                    (r, f"-{window_days} days"),
                )
                for (b,) in cur.fetchall():
                    if isinstance(b, (int, float)) and math.isfinite(b):
                        briers[r].append(float(b))
            except sqlite3.Error:
                continue
        conn.close()
        # 计算负均 Brier 并 softmax
        neg_mean: dict[str, float] = {}
        for r in roles:
            bs = briers[r]
            if bs:
                neg_mean[r] = -sum(bs) / len(bs)
            else:
                neg_mean[r] = 0.0  # 无历史则中性
        # 无历史时退化为均匀
        if all(v == 0.0 for v in neg_mean.values()):
            if not _BRIER_FALLBACK_LOGGED:
                _BRIER_FALLBACK_LOGGED = True
                logger.info(
                    "[Aggregator] Brier 动态权重休眠 (accuracy 表无记录), "
                    "使用均匀权重 — 写入链路建成后自动生效"
                )
            return uniform
        vals = [neg_mean[r] for r in roles]
        mx = max(vals)
        exps = [math.exp(v - mx) for v in vals]
        s = sum(exps)
        return {r: e / s for r, e in zip(roles, exps, strict=True)}
    except (
        sqlite3.Error,
        ValueError,
        TypeError,
        ZeroDivisionError,
    ) as exc:  # 表不存在/不可用时均匀
        logger.debug("Brier 权重加载失败, 用均匀权重: %s", exc)
        return uniform


def _diversity_bonus(views: list[ModelView]) -> dict[int, float]:
    """多样性奖励: 少数派 (与多数行动不同) 且来源独立, 给予奖励"""
    if not views:
        return {}
    actions = [v.action for v in views]
    cnt = Counter(actions)
    majority = cnt.most_common(1)[0][0] if cnt else "hold"
    bonus_cap = float(get_config("aggregator.diversity_bonus", 0.1))
    bonus: dict[int, float] = {}
    n_minority = sum(1 for a in actions if a != majority)
    if n_minority == 0:
        return bonus
    per = bonus_cap / n_minority
    for i, v in enumerate(views):
        if v.action != majority:
            bonus[i] = per
    return bonus


def aggregate(
    views: list[ModelView],
    debate: DebateDecision | None = None,
    agent_consensus: dict[str, float] | None = None,
) -> tuple[str, float, float]:
    """非线性聚合, 返回 (action, strength, confidence)。

    Args:
        views: 各模型/角色结构化观点 (含辩论前的 bull/bear/judge 或五 Agent 映射)
        debate: 辩论裁决结果 (若触发)
        agent_consensus: 五 Agent 加权共识 {action, strength, confidence}

    Returns:
        Tuple[str, float, float]: (action, strength, confidence) 三元组，
            action ∈ {"buy", "sell", "hold"}，strength ∈ [-1.0, 1.0]，
            confidence ∈ [0.0, 1.0]；输入全空时返回 ("hold", 0.0, 0.3)
    """
    if not views and debate is None and agent_consensus is None:
        return "hold", 0.0, 0.3

    roles = [v.role for v in views]
    base_weights = _load_brier_weights(
        roles, int(get_config("aggregator.brier_window_days", 30))
    )

    # 语义去重: 标记高重叠冗余观点并降权
    dup_thr = float(get_config("aggregator.semantic_dup_threshold", 0.6))
    eff_weights: list[float] = []
    for i, v in enumerate(views):
        w = base_weights.get(v.role, 1.0 / max(1, len(views)))
        # 与已保留观点高重叠 -> 降权
        for j in range(i):
            if (
                _word_overlap(
                    " ".join(v.key_points) or v.reasoning,
                    " ".join(views[j].key_points) or views[j].reasoning,
                )
                > dup_thr
            ):
                w *= 0.3
                break
        # 低置信度降权
        if v.confidence < float(get_config("aggregator.min_confidence", 0.3)):
            w *= 0.5
        eff_weights.append(w)

    # 多样性奖励
    div = _diversity_bonus(views)
    for i in div:
        eff_weights[i] += div[i]

    # 加权合成 strength (按 action 符号)
    sw_sum = sum(eff_weights) or 1.0
    strength = 0.0
    for v, w in zip(views, eff_weights, strict=True):
        sign = 1.0 if v.action == "buy" else (-1.0 if v.action == "sell" else 0.0)
        strength += sign * v.strength * w / sw_sum

    # 置信度: 观点一致度 * 平均置信度
    actions = [v.action for v in views]
    # L1 修复: views 为空 (且 debate/agent_consensus 存在) 时 Counter 为空,
    # max() 抛 ValueError → 用 default=0 降级 (agree=0 → 保守置信度)
    agree = (max(Counter(actions).values(), default=0)) / max(1, len(actions))
    avg_conf = sum(v.confidence for v in views) / max(1, len(views))
    confidence = avg_conf * (0.5 + 0.5 * agree)

    # 融合辩论裁决 (若触发): 辩论结论权重 0.4, 视图 0.6
    if debate is not None:
        d_sign = (
            1.0
            if debate.action == "buy"
            else (-1.0 if debate.action == "sell" else 0.0)
        )
        strength = 0.6 * strength + 0.4 * (d_sign * debate.strength)
        confidence = 0.6 * confidence + 0.4 * debate.confidence
        # H3 修复 (2026-09-12): 删除 `verdict_type == "AUTO" 时
        # confidence = max(confidence, debate.confidence)` 的单方面抬升。
        # debate.confidence 是 judge 的 LLM 自报值, 让被审对象给自己打分并可
        # 直接推过 auto 模式 0.7 放行线。judge 意见已经过上方 0.6/0.4 加权融合,
        # AUTO 裁决不再额外提权 (放行仍由融合置信度 + decision_gate 把关)。

    # 融合五 Agent 加权共识 (若提供): 视为独立强信号, 权重 0.3
    if agent_consensus:
        a_sign = (
            1.0
            if agent_consensus.get("action") == "buy"
            else (-1.0 if agent_consensus.get("action") == "sell" else 0.0)
        )
        a_strength = float(agent_consensus.get("strength", 0.0))
        a_conf = float(agent_consensus.get("confidence", 0.0))
        strength = 0.7 * strength + 0.3 * (a_sign * a_strength)
        confidence = 0.7 * confidence + 0.3 * a_conf

    strength = max(-1.0, min(1.0, strength))
    confidence = max(0.0, min(1.0, confidence))

    if strength > 0.05:
        action = "buy"
    elif strength < -0.05:
        action = "sell"
    else:
        action = "hold"
    return action, strength, confidence
