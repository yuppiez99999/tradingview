"""LLM 模块测试：JSON 修复、extract_json 委托、fail-open 降级。

不触发任何网络；chat 的真实网络路径由 monkeypatch 模拟。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _common as _c  # noqa: E402
import llm  # noqa: E402


def test_repair_json_plain():
    assert llm._repair_json('{"a": 1}') == {"a": 1}


def test_repair_json_code_fence():
    text = '```json\n{"x": 2}\n```'
    assert llm._repair_json(text) == {"x": 2}


def test_repair_json_trailing_comma():
    assert llm._repair_json('{"a": 1,}') == {"a": 1}


def test_repair_json_substring():
    text = 'blah {"k": 9} trailing'
    assert llm._repair_json(text) == {"k": 9}


def test_repair_json_invalid():
    assert llm._repair_json("no json here") is None


def test_extract_json_delegates_to_chat(monkeypatch):
    captured = {}

    def fake_chat(prompt, system=None, temperature=0.2, timeout=30, max_retries=2):
        captured["prompt"] = prompt
        return '{"rating": "buy"}'

    monkeypatch.setattr(llm, "chat", fake_chat)
    out = llm.extract_json("extract something")
    assert out == {"rating": "buy"}
    assert "extract something" in captured["prompt"]


def test_chat_no_backend_returns_none(monkeypatch):
    # 无直接端点 env + GLM-5 标记为不可用 -> fail-open 返回 None
    monkeypatch.delenv("MOBIUS_LLM_API_KEY", raising=False)
    monkeypatch.delenv("MOBIUS_LLM_BASE_URL", raising=False)
    saved = _c._GLM5
    _c._GLM5 = False
    try:
        assert llm.chat("hi") is None
    finally:
        _c._GLM5 = saved
