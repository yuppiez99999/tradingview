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
    assert conf >= 0.7  # 辩论 AUTO 提升置信度


def test_aggregate_empty():
    action, strength, _conf = aggregate([])
    assert action == "hold"
    assert strength == 0.0
