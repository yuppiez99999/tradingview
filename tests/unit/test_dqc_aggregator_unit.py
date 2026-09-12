"""dqc/aggregator.py 单元测试 — 报警聚合器.

目标模块: utils/dqc/aggregator.py (branch-rate 0.1154 → 高覆盖)
覆盖: AlertState / AlertAggregator (should_emit/record_emit/check_escalation/cleanup_stale 全分支)
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from utils.datetime_utils import now_bj
from utils.dqc.aggregator import (
    AlertAggregator,
    AlertState,
    _level_rank,
    get_aggregator,
)
from utils.dqc.event_types import DQCLevel

# ============================================================
# LevelRankTest — 级别排序辅助
# ============================================================


class LevelRankTest:

    def test_rank_values(self):
        assert _level_rank(DQCLevel.INFO) == 0
        assert _level_rank(DQCLevel.WARN) == 1
        assert _level_rank(DQCLevel.ERROR) == 2
        assert _level_rank(DQCLevel.CRITICAL) == 3

    def test_rank_unknown(self):
        assert _level_rank("unknown") == 0


# ============================================================
# AlertStateTest — 报警状态数据结构
# ============================================================


class AlertStateTest:

    def test_init(self):
        now = now_bj()
        s = AlertState(
            metric_id="M1", level=DQCLevel.WARN, first_seen=now, last_seen=now
        )
        assert s.metric_id == "M1"
        assert s.level == DQCLevel.WARN
        assert s.emit_count == 0
        assert s.last_emit is None
        assert s.symbol is None

    def test_is_stale_false(self):
        now = now_bj()
        s = AlertState(
            metric_id="M1", level=DQCLevel.WARN, first_seen=now, last_seen=now
        )
        assert s.is_stale(now) is False

    def test_is_stale_true(self):
        now = now_bj()
        old = now - timedelta(hours=2)
        s = AlertState(
            metric_id="M1", level=DQCLevel.WARN, first_seen=old, last_seen=old
        )
        assert s.is_stale(now) is True

    def test_is_stale_custom_window(self):
        now = now_bj()
        recent = now - timedelta(minutes=10)
        s = AlertState(
            metric_id="M1", level=DQCLevel.WARN, first_seen=recent, last_seen=recent
        )
        assert s.is_stale(now, window=timedelta(minutes=5)) is True
        assert s.is_stale(now, window=timedelta(hours=1)) is False


# ============================================================
# AlertAggregatorTest — 报警聚合器核心
# ============================================================


class AlertAggregatorTest:

    @pytest.fixture
    def aggregator(self):
        """每个测试用独立实例 (非单例)."""
        return AlertAggregator()

    def test_should_emit_first_time(self, aggregator):
        should, reason = aggregator.should_emit("M1", DQCLevel.WARN)
        assert should is True
        assert reason is None

    def test_should_emit_same_level_suppressed(self, aggregator):
        aggregator.should_emit("M1", DQCLevel.WARN)
        aggregator.record_emit("M1", level=DQCLevel.WARN)
        should, reason = aggregator.should_emit("M1", DQCLevel.WARN)
        assert should is False
        assert "抑制" in reason

    def test_should_emit_upgrade(self, aggregator):
        aggregator.should_emit("M1", DQCLevel.WARN)
        should, reason = aggregator.should_emit("M1", DQCLevel.ERROR)
        assert should is True
        assert "升级" in reason

    def test_should_emit_downgrade_not_suppressed(self, aggregator):
        """降级不走升级路径, 但无 last_emit 仍允许发送."""
        aggregator.should_emit("M1", DQCLevel.ERROR)
        should, reason = aggregator.should_emit("M1", DQCLevel.WARN)
        # 降级: 不升级, 无 last_emit → 允许
        assert should is True

    def test_should_emit_after_suppress_window(self, aggregator):
        """超过抑制窗口后允许重发."""
        agg = aggregator
        agg.should_emit("M1", DQCLevel.WARN)
        agg.record_emit("M1", level=DQCLevel.WARN)
        # 手动把 last_emit 设为很久以前
        key = ("M1", None)
        agg._states[key].last_emit = now_bj() - timedelta(minutes=10)
        should, reason = agg.should_emit("M1", DQCLevel.WARN)
        assert should is True

    def test_should_emit_with_symbol(self, aggregator):
        should1, _ = aggregator.should_emit("M1", DQCLevel.WARN, symbol="A")
        should2, _ = aggregator.should_emit("M1", DQCLevel.WARN, symbol="B")
        assert should1 is True
        assert should2 is True  # 不同 symbol 独立

    def test_record_emit_new_state(self, aggregator):
        aggregator.record_emit("M1", level=DQCLevel.ERROR)
        state = aggregator.get_state("M1")
        assert state is not None
        assert state.emit_count == 1
        assert state.last_emit is not None

    def test_record_emit_existing_state(self, aggregator):
        aggregator.should_emit("M1", DQCLevel.WARN)
        aggregator.record_emit("M1")
        aggregator.record_emit("M1")
        state = aggregator.get_state("M1")
        assert state.emit_count == 2

    def test_record_emit_no_state_no_level(self, aggregator):
        """state 不存在且 level=None 时直接返回."""
        aggregator.record_emit("M1")  # 无 state, 无 level
        assert aggregator.get_state("M1") is None

    def test_check_escalation_no_state(self, aggregator):
        assert aggregator.check_escalation("M1") is None

    def test_check_escalation_warn_to_error(self, aggregator):
        agg = aggregator
        agg.should_emit("M1", DQCLevel.WARN)
        # 手动设置 first_seen 为很久以前
        agg._states[("M1", None)].first_seen = now_bj() - timedelta(minutes=20)
        result = agg.check_escalation("M1")
        assert result == DQCLevel.ERROR

    def test_check_escalation_error_to_critical(self, aggregator):
        agg = aggregator
        agg.should_emit("M1", DQCLevel.ERROR)
        agg._states[("M1", None)].first_seen = now_bj() - timedelta(minutes=35)
        result = agg.check_escalation("M1")
        assert result == DQCLevel.CRITICAL

    def test_check_escalation_no_upgrade_within_window(self, aggregator):
        agg = aggregator
        agg.should_emit("M1", DQCLevel.WARN)
        # 刚开始, 未超窗口
        result = agg.check_escalation("M1")
        assert result is None

    def test_check_escalation_info_no_upgrade(self, aggregator):
        """INFO 级别不升级."""
        agg = aggregator
        agg.should_emit("M1", DQCLevel.INFO)
        agg._states[("M1", None)].first_seen = now_bj() - timedelta(hours=2)
        result = agg.check_escalation("M1")
        assert result is None

    def test_cleanup_stale(self, aggregator):
        agg = aggregator
        agg.should_emit("M1", DQCLevel.WARN)
        agg.should_emit("M2", DQCLevel.ERROR)
        # M1 过期
        agg._states[("M1", None)].last_seen = now_bj() - timedelta(hours=2)
        removed = agg.cleanup_stale()
        assert removed == 1
        assert agg.get_state("M1") is None
        assert agg.get_state("M2") is not None

    def test_cleanup_stale_custom_max_age(self, aggregator):
        agg = aggregator
        agg.should_emit("M1", DQCLevel.WARN)
        agg._states[("M1", None)].last_seen = now_bj() - timedelta(minutes=10)
        removed = agg.cleanup_stale(max_age=timedelta(minutes=5))
        assert removed == 1

    def test_cleanup_stale_none(self, aggregator):
        agg = aggregator
        agg.should_emit("M1", DQCLevel.WARN)
        removed = agg.cleanup_stale()
        assert removed == 0

    def test_get_state(self, aggregator):
        aggregator.should_emit("M1", DQCLevel.WARN, symbol="A")
        state = aggregator.get_state("M1", symbol="A")
        assert state is not None
        assert state.metric_id == "M1"

    def test_get_state_missing(self, aggregator):
        assert aggregator.get_state("nonexistent") is None

    def test_all_states(self, aggregator):
        aggregator.should_emit("M1", DQCLevel.WARN)
        aggregator.should_emit("M2", DQCLevel.ERROR)
        states = aggregator.all_states()
        assert len(states) == 2

    def test_all_states_snapshot(self, aggregator):
        """all_states 返回副本, 修改不影响内部状态."""
        aggregator.should_emit("M1", DQCLevel.WARN)
        states = aggregator.all_states()
        states.clear()
        assert aggregator.get_state("M1") is not None


# ============================================================
# AlertAggregatorSingletonTest — 单例模式
# ============================================================


class AlertAggregatorSingletonTest:

    def test_get_instance_singleton(self):
        a1 = AlertAggregator.get_instance()
        a2 = AlertAggregator.get_instance()
        assert a1 is a2

    def test_get_aggregator_returns_singleton(self):
        a1 = get_aggregator()
        a2 = AlertAggregator.get_instance()
        assert a1 is a2
