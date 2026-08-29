"""
TeamMemoryHub 单元测试
======================

测试 W.B.2 新增的团队级共享记忆中枢:
- 教训写入 + 检索 (share_lesson / query_relevant_lessons)
- feature-flag 关闭时所有操作 no-op
- 分析师画像 (get_agent_profile)
- 批量获取 + prompt 注入 (get_lessons_for_tickers / build_lessons_prompt_block)
- 统计 (stats)
- 异常容错

使用临时 db, mock is_enabled 返回 True (flag 默认 false).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.ai_memory.team_memory_hub import (  # noqa: E402
    AgentProfile,
    TeamMemoryHub,
    get_team_memory_hub,
)

# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def tmp_db():
    """临时 db 路径"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    try:
        Path(path).unlink(missing_ok=True)
    except (ValueError, TypeError, OSError):
        pass


@pytest.fixture
def hub(tmp_db):
    """TeamMemoryHub 实例 (flag 开启)"""
    h = TeamMemoryHub(db_path=tmp_db)
    with patch("utils.ai_memory.team_memory_hub.is_enabled", return_value=True):
        yield h
    h.close()


@pytest.fixture
def hub_flag_off(tmp_db):
    """TeamMemoryHub 实例 (flag 关闭)"""
    h = TeamMemoryHub(db_path=tmp_db)
    with patch("utils.ai_memory.team_memory_hub.is_enabled", return_value=False):
        yield h
    h.close()


# ============================================================
# 测试组 1: feature-flag 关闭时 no-op
# ============================================================


class TestFlagOff:
    def test_share_lesson_noop(self, hub_flag_off):
        result = hub_flag_off.share_lesson("agent", "600519", "教训")
        assert result is None

    def test_query_noop(self, hub_flag_off):
        result = hub_flag_off.query_relevant_lessons(ticker="600519")
        assert result == []

    def test_build_prompt_noop(self, hub_flag_off):
        result = hub_flag_off.build_lessons_prompt_block(["600519"])
        assert result == ""

    def test_get_profile_noop(self, hub_flag_off):
        profile = hub_flag_off.get_agent_profile("agent")
        assert profile.total_lessons == 0


# ============================================================
# 测试组 2: 写入 + 检索
# ============================================================


class TestShareAndQuery:
    def test_share_and_query_roundtrip(self, hub):
        lesson_id = hub.share_lesson(
            agent_name="warren_buffett",
            ticker="600519",
            lesson_text="看多判断失误, 低估空头证据",
            context="bullish conf=75",
            decision="bullish",
            outcome="wrong1d",
            confidence=0.75,
        )
        assert lesson_id is not None

        lessons = hub.query_relevant_lessons(ticker="600519")
        assert len(lessons) == 1
        assert lessons[0].agent_name == "warren_buffett"
        assert lessons[0].ticker == "600519"
        assert "低估空头证据" in lessons[0].lesson_text
        assert lessons[0].outcome == "wrong1d"

    def test_query_by_agent(self, hub):
        hub.share_lesson("agent_a", "600519", "教训 A")
        hub.share_lesson("agent_b", "600519", "教训 B")
        lessons = hub.query_relevant_lessons(ticker="600519", agent_name="agent_a")
        assert len(lessons) == 1
        assert lessons[0].agent_name == "agent_a"

    def test_query_by_context_keyword(self, hub):
        hub.share_lesson("agent", "600519", "业绩超预期, 看多")
        hub.share_lesson("agent", "000001", "估值偏高, 看空")
        lessons = hub.query_relevant_lessons(context="业绩")
        assert len(lessons) == 1
        assert "业绩超预期" in lessons[0].lesson_text

    def test_query_top_k_limit(self, hub):
        for i in range(10):
            hub.share_lesson("agent", "600519", f"教训 {i}")
        lessons = hub.query_relevant_lessons(ticker="600519", top_k=3)
        assert len(lessons) == 3

    def test_query_order_by_recency(self, hub):
        hub.share_lesson("agent", "600519", "旧教训")
        import time

        time.sleep(0.01)
        hub.share_lesson("agent", "600519", "新教训")
        lessons = hub.query_relevant_lessons(ticker="600519")
        assert "新教训" in lessons[0].lesson_text

    def test_empty_inputs(self, hub):
        assert hub.share_lesson("", "600519", "教训") is None
        assert hub.share_lesson("agent", "", "教训") is None
        assert hub.share_lesson("agent", "600519", "") is None


# ============================================================
# 测试组 3: 批量获取 + prompt 注入
# ============================================================


class TestBatchAndPrompt:
    def test_get_lessons_for_tickers(self, hub):
        hub.share_lesson("agent", "600519", "茅台教训")
        hub.share_lesson("agent", "000001", "平安教训")
        result = hub.get_lessons_for_tickers(["600519", "000001"])
        assert "600519" in result
        assert "000001" in result
        assert len(result["600519"]) == 1
        assert len(result["000001"]) == 1

    def test_build_lessons_prompt_block(self, hub):
        hub.share_lesson("agent", "600519", "看多准确", outcome="correct5d")
        block = hub.build_lessons_prompt_block(["600519"])
        assert "[历史教训]" in block
        assert "600519" in block
        assert "看多准确" in block
        assert "[correct5d]" in block

    def test_build_prompt_empty(self, hub):
        block = hub.build_lessons_prompt_block(["999999"])
        assert block == ""

    def test_build_prompt_no_tickers(self, hub):
        block = hub.build_lessons_prompt_block([])
        assert block == ""


# ============================================================
# 测试组 4: 分析师画像
# ============================================================


class TestAgentProfile:
    def test_profile_basic(self, hub):
        hub.share_lesson("agent_a", "600519", "教训 1", outcome="correct5d")
        hub.share_lesson("agent_a", "000001", "教训 2", outcome="wrong1d")
        hub.share_lesson("agent_a", "600036", "教训 3", outcome="correct1d")
        profile = hub.get_agent_profile("agent_a")
        assert profile.total_lessons == 3
        assert profile.correct_count == 2
        assert profile.wrong_count == 1
        assert profile.accuracy == pytest.approx(2 / 3, abs=0.01)
        assert "600519" in profile.tickers_covered
        assert "000001" in profile.tickers_covered

    def test_profile_no_lessons(self, hub):
        profile = hub.get_agent_profile("nonexistent_agent")
        assert profile.total_lessons == 0
        assert profile.accuracy == 0.0

    def test_profile_to_dict(self, hub):
        hub.share_lesson("agent", "600519", "教训", outcome="correct5d")
        profile = hub.get_agent_profile("agent")
        d = profile.to_dict()
        assert d["agent_name"] == "agent"
        assert d["total_lessons"] == 1
        assert "accuracy" in d


# ============================================================
# 测试组 5: 统计 + 数据类
# ============================================================


class TestStatsAndDataclasses:
    def test_stats(self, hub):
        hub.share_lesson("agent_a", "600519", "教训 1", outcome="correct5d")
        hub.share_lesson("agent_b", "000001", "教训 2", outcome="wrong1d")
        s = hub.stats()
        assert s["total_lessons"] == 2
        assert "agent_a" in s["by_agent"]
        assert "agent_b" in s["by_agent"]

    def test_lesson_to_dict(self, hub):
        hub.share_lesson("agent", "600519", "测试教训", outcome="correct5d")
        lessons = hub.query_relevant_lessons(ticker="600519")
        d = lessons[0].to_dict()
        assert d["agent_name"] == "agent"
        assert d["ticker"] == "600519"

    def test_lesson_to_prompt_text(self, hub):
        hub.share_lesson("agent", "600519", "测试教训文本", outcome="correct5d")
        lessons = hub.query_relevant_lessons(ticker="600519")
        text = lessons[0].to_prompt_text()
        assert "600519" in text
        assert "[correct5d]" in text
        assert "测试教训文本" in text

    def test_agent_profile_accuracy_no_data(self):
        p = AgentProfile(agent_name="x")
        assert p.accuracy == 0.0


# ============================================================
# 测试组 6: 上下文管理 + 单例
# ============================================================


class TestLifecycle:
    def test_context_manager(self, tmp_db):
        with patch("utils.ai_memory.team_memory_hub.is_enabled", return_value=True):
            with TeamMemoryHub(db_path=tmp_db) as h:
                lid = h.share_lesson("agent", "600519", "教训")
                assert lid is not None

    def test_singleton(self):
        with patch("utils.ai_memory.team_memory_hub.is_enabled", return_value=True):
            h1 = get_team_memory_hub()
            h2 = get_team_memory_hub()
            assert h1 is h2
