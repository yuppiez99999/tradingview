"""consensus_aggregator 测试: Brier 权重 / 语义去重 / 多样性奖励数学"""

from __future__ import annotations

import os
import sys

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from ai_decision.consensus_aggregator import (
    _diversity_bonus,
    _load_brier_weights,
    _word_overlap,
    aggregate,
)
from ai_decision.models import DebateDecision, ModelView


def test_word_overlap_identical():
    assert _word_overlap("a b c", "a b c") == 1.0


def test_word_overlap_disjoint():
    assert _word_overlap("a b c", "x y z") == 0.0


def test_word_overlap_chinese_similar():
    """M3 修复回归: 中文按字符 2-gram — 同源微改复读应触发去重 (原 .split() 恒 0)。"""
    a = "贵州茅台业绩超预期 机构上调目标价至2000元"
    b = "贵州茅台业绩超预期 机构上调目标价至1950元"
    assert _word_overlap(a, b) >= 0.6


def test_word_overlap_chinese_disjoint():
    """中文不同主题句不得误判为重复。"""
    a = "贵州茅台业绩超预期"
    b = "宁德时代产能扩张"
    assert _word_overlap(a, b) < 0.3


def test_brier_weights_uniform_when_no_db():
    # 无 DB 时返回均匀权重, 且和为 1
    w = _load_brier_weights(["bull", "bear", "judge"], 30)
    assert abs(sum(w.values()) - 1.0) < 1e-6


def test_diversity_bonus_minority():
    views = [
        ModelView(role="a", action="buy"),
        ModelView(role="b", action="buy"),
        ModelView(role="c", action="sell"),
    ]
    bonus = _diversity_bonus(views)
    # 唯一少数派 c 获得奖励
    assert 2 in bonus and bonus[2] > 0
    assert 0 not in bonus and 1 not in bonus


def test_aggregate_consistent_buy():
    views = [
        ModelView(role="bull", action="buy", strength=0.6, confidence=0.8),
        ModelView(role="bear", action="buy", strength=0.3, confidence=0.7),
        ModelView(role="judge", action="buy", strength=0.5, confidence=0.75),
    ]
    action, strength, conf = aggregate(views)
    assert action == "buy"
    assert strength > 0
    assert 0.0 <= conf <= 1.0


def test_aggregate_with_debate_blend():
    views = [
        ModelView(role="bull", action="buy", strength=0.4, confidence=0.7),
        ModelView(role="bear", action="sell", strength=-0.3, confidence=0.6),
    ]
    debate = DebateDecision(
        action="buy", strength=0.5, confidence=0.9, verdict_type="AUTO"
    )
    action, _strength, conf = aggregate(views, debate=debate)
    assert action == "buy"
    # H3 修复 (2026-09-12): AUTO 裁决不再单方面抬升置信度 (judge 自报值
    # 不得推过 auto 放行线), judge 意见仅经 0.6/0.4 加权融合。
    # 本例视图融合 conf = 0.65 * 1.0 = 0.65, 融合后 = 0.6*0.65 + 0.4*0.9 = 0.75
    expected = 0.6 * (0.7 * 0.5 + 0.6 * 0.5) * (0.5 + 0.5 * 0.5) + 0.4 * 0.9
    assert abs(conf - expected) < 1e-9
    assert conf < 0.9  # 不得被自报值直接拉满


def test_aggregate_no_debate_lift_on_auto():
    """H3 回归: judge 自报 conf=0.9 不得把低于放行线的融合置信度推过 0.7。"""
    views = [
        ModelView(role="bull", action="buy", strength=0.2, confidence=0.4),
        ModelView(role="bear", action="sell", strength=-0.1, confidence=0.3),
    ]
    debate = DebateDecision(
        action="buy", strength=0.1, confidence=0.9, verdict_type="AUTO"
    )
    _action, _strength, conf = aggregate(views, debate=debate)
    assert conf < 0.7  # 融合结果仍在放行线以下, 由 decision_gate 升级人工


def test_aggregate_empty_views_with_debate_no_crash():
    """L1 回归: views 为空 + debate 存在时不得抛 ValueError。"""
    debate = DebateDecision(
        action="hold", strength=0.0, confidence=0.5, verdict_type="HOLD"
    )
    action, _strength, conf = aggregate([], debate=debate)
    assert action == "hold"


def test_aggregate_empty():
    action, strength, _conf = aggregate([])
    assert action == "hold"
    assert strength == 0.0
