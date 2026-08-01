# -*- coding: utf-8 -*-
"""providers 测试: Mock 降级 / 真实 provider 探测 / 无 Key 跑通"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ai_decision.providers import (
    ClaudeProvider,
    GptProvider,
    MockProvider,
    MoonshotProvider,
    get_active_provider,
)


def test_mock_provider_returns_text():
    m = MockProvider(role="bull")
    out = m.generate("涨跌幅 2.5% PE 15")
    assert isinstance(out, str) and out.strip(), "Mock 必须返回非空文本"


def test_mock_provider_no_network():
    # Mock 不应有任何网络依赖, 纯本地规则
    m = MockProvider(role="bear")
    out = m.generate("涨跌幅 -3.0%")
    assert "看空" in out


def test_get_active_provider_falls_back_to_mock_when_no_key():
    # 确保无 Key 环境变量
    for k in ("MOONSHOT_API_KEY", "CLAUDE_API_KEY", "OPENAI_API_KEY"):
        os.environ.pop(k, None)
    # judge 默认后端 claude, 无 Key 应降级 Mock
    p = get_active_provider("judge")
    assert p.is_mock is True, "无 Key 时 judge 应降级到 MockProvider"
    # 仍应可生成文本
    assert p.generate("测试上下文") is not None


def test_moonshot_unavailable_without_key():
    os.environ.pop("MOONSHOT_API_KEY", None)
    p = MoonshotProvider()
    assert p.available is False
    assert p.generate("x") is None


def test_claude_unavailable_without_key():
    os.environ.pop("CLAUDE_API_KEY", None)
    p = ClaudeProvider()
    assert p.available is False
    assert p.generate("x") is None


def test_gpt_unavailable_without_key():
    os.environ.pop("OPENAI_API_KEY", None)
    p = GptProvider()
    assert p.available is False
    assert p.generate("x") is None


def test_get_active_provider_real_when_key_set():
    os.environ["MOONSHOT_API_KEY"] = "fake-key-for-test"
    p = get_active_provider("research")  # research -> moonshot
    # 有 Key 走真实 Provider (不会是 mock)
    assert p.is_mock is False
    os.environ.pop("MOONSHOT_API_KEY", None)
