"""debate_engine 测试: 辩论触发条件 / 2 轮 / 结构化解析"""

from __future__ import annotations

import os
import sys

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from ai_decision.debate_engine import _parse_strength_conf, run_debate
from ai_decision.models import DebateTrigger, ModelView
from ai_decision.rag_context import build_context, context_to_prompt


def test_debate_trigger_opposite_high_conf():
    t = DebateTrigger(
        bull_strength=0.5,
        bear_strength=-0.4,
        bull_conf=0.7,
        bear_conf=0.8,
        debate_threshold=0.6,
    )
    assert t.should_debate() is True


def test_debate_trigger_same_direction():
    t = DebateTrigger(
        bull_strength=0.5, bear_strength=0.2, bull_conf=0.9, bear_conf=0.9
    )
    assert t.should_debate() is False  # 同方向不辩论


def test_debate_trigger_low_conf():
    t = DebateTrigger(
        bull_strength=0.5, bear_strength=-0.4, bull_conf=0.5, bear_conf=0.8
    )
    assert t.should_debate() is False  # 置信度不足


def test_parse_strength_conf_bull():
    s, c = _parse_strength_conf("看多观点 买入 上涨 低估 利好, 置信度 0.82")
    assert s > 0, "应解析为正向强度"
    assert abs(c - 0.82) < 1e-6, "应解析置信度 0.82"


def test_parse_strength_conf_bear():
    s, _c = _parse_strength_conf("看空观点 卖出 下跌 高估 风险, 置信度 0.65")
    assert s < 0, "应解析为负向强度"


def test_run_debate_with_mock_no_key():
    # 确保无 Key, 全走 Mock（同时也移除 OLLAMA_BASE_URL 确保 ollama 不被探测为可用）
    for k in (
        "MOONSHOT_API_KEY",
        "CLAUDE_API_KEY",
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "GLM_API_KEY",
        "OLLAMA_BASE_URL",
    ):
        os.environ.pop(k, None)
    ctx = build_context("600519", market_data={"close": 1700, "change_pct": 1.2})
    prompt = context_to_prompt(ctx)
    bull = ModelView(role="bull", action="buy", strength=0.5, confidence=0.8)
    bear = ModelView(role="bear", action="sell", strength=-0.4, confidence=0.85)
    # 超时设 60s: bull/bear 全走 mock 时 <1s，若 ollama 或 deepseek 真实调用酌情等待
    record, decision = run_debate("600519", prompt, bull, bear, timeout=60)
    assert record.triggered is True
    assert record.rounds >= 1
    assert decision.action in ("buy", "sell", "hold")
    assert 0.0 <= decision.confidence <= 1.0
