"""报警聚合器 (AlertAggregator) — 避免告警风暴.

设计原则:
    1. 同指标 5 分钟内只发一次 (抑制)
    2. 级别升级时立即发 (INFO→WARN→ERROR→CRITICAL)
    3. 持续 30 分钟未恢复 → 升级 (WARN→ERROR)
    4. 单例模式 (全项目共享抑制状态)

Usage:
    >>> from utils.dqc.aggregator import get_aggregator
    >>> agg = get_aggregator()
    >>> should_emit, reason = agg.should_emit("C-01", DQCLevel.ERROR)
    >>> if should_emit:
    ...     # 发送通知 (飞书 / 邮件)
    ...     pass
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import RLock

from utils.dqc.event_types import DQCLevel

logger = logging.getLogger("dqc.aggregator")

# 同指标抑制窗口 (分钟)
SUPPRESS_WINDOW = timedelta(minutes=5)

# WARN → ERROR 升级窗口 (分钟)
ESCALATION_WINDOW = timedelta(minutes=15)

# ERROR → CRITICAL 升级窗口 (分钟, 持续 ERROR 30 分钟触发 CRITICAL)
CRITICAL_ESCALATION_WINDOW = timedelta(minutes=30)


# DQCLevel 级别排序 (str Enum 不能直接比较字符串值, 用成员顺序)
_LEVEL_RANK = {level: i for i, level in enumerate(DQCLevel)}


def _level_rank(level: DQCLevel) -> int:
    """获取级别序数 (INFO=0, WARN=1, ERROR=2, CRITICAL=3)."""
    return _LEVEL_RANK.get(level, 0)


@dataclass
class AlertState:
    """单个指标的报警状态."""

    metric_id: str
    level: DQCLevel
    first_seen: datetime
    last_seen: datetime
    emit_count: int = 0
    last_emit: datetime | None = None
    symbol: str | None = None  # 区分不同标的的同指标

    def is_stale(self, now: datetime, window: timedelta = timedelta(hours=1)) -> bool:
        """是否过期 (1 小时未更新)."""
        return now - self.last_seen > window


class AlertAggregator:
    """报警聚合器 (单例).

    功能:
        1. should_emit(metric_id, level, symbol) → (bool, reason)
           判断是否应该发送通知
        2. record_emit(metric_id, symbol) → None
           记录已发送
        3. check_escalation(metric_id, symbol) → Optional[DQCLevel]
           检查是否应升级
        4. cleanup_stale() → int
           清理过期状态
    """

    _instance: AlertAggregator | None = None
    _lock: RLock = RLock()

    def __init__(self) -> None:
        """初始化聚合器."""
        # key: (metric_id, symbol_or_None)
        self._states: dict[tuple[str, str | None], AlertState] = {}
        self._rlock = RLock()

    @classmethod
    def get_instance(cls) -> AlertAggregator:
        """获取单例."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def should_emit(
        self,
        metric_id: str,
        level: DQCLevel,
        symbol: str | None = None,
    ) -> tuple[bool, str | None]:
        """判断是否应该发送通知.

        Args:
            metric_id: 指标 ID
            level: 当前级别
            symbol: 涉及标的 (None=组合级)

        Returns:
            tuple[是否发送, 原因说明]
            - (True, None): 首次或升级, 应发送
            - (True, "升级 WARN→ERROR"): 升级, 应发送
            - (False, "抑制(同级5min内)"): 同级抑制
        """
        with self._rlock:
            now = datetime.now()
            key = (metric_id, symbol)
            state = self._states.get(key)

            if state is None:
                # 首次出现
                self._states[key] = AlertState(
                    metric_id=metric_id,
                    level=level,
                    first_seen=now,
                    last_seen=now,
                )
                return True, None

            # 更新 last_seen
            state.last_seen = now

            # 升级路径: 级别提升必须立即发
            # (DQCLevel 是 str Enum, 用成员顺序比较而非字符串值)
            if _level_rank(level) > _level_rank(state.level):
                old_level = state.level
                state.level = level
                return True, f"升级 {old_level.name}→{level.name}"

            # 同级抑制: 5 分钟内不重复发
            if state.last_emit and now - state.last_emit < SUPPRESS_WINDOW:
                return False, "抑制(同级5min内)"

            # 同级但超过抑制窗口, 允许重发
            return True, None

    def record_emit(
        self,
        metric_id: str,
        symbol: str | None = None,
        level: DQCLevel | None = None,
    ) -> None:
        """记录已发送通知."""
        with self._rlock:
            key = (metric_id, symbol)
            state = self._states.get(key)
            now = datetime.now()
            if state is None:
                if level is None:
                    return
                state = AlertState(
                    metric_id=metric_id,
                    level=level,
                    first_seen=now,
                    last_seen=now,
                )
                self._states[key] = state
            state.last_emit = now
            state.emit_count += 1

    def check_escalation(
        self,
        metric_id: str,
        symbol: str | None = None,
    ) -> DQCLevel | None:
        """检查是否应升级 (基于持续时间).

        规则:
            - WARN 持续 15 分钟 → 升级为 ERROR
            - ERROR 持续 30 分钟 → 升级为 CRITICAL

        Returns:
            升级后的级别 (None 表示不升级)
        """
        with self._rlock:
            key = (metric_id, symbol)
            state = self._states.get(key)
            if state is None:
                return None

            now = datetime.now()
            duration = now - state.first_seen

            if state.level == DQCLevel.WARN and duration > ESCALATION_WINDOW:
                state.level = DQCLevel.ERROR
                return DQCLevel.ERROR

            if state.level == DQCLevel.ERROR and duration > CRITICAL_ESCALATION_WINDOW:
                state.level = DQCLevel.CRITICAL
                return DQCLevel.CRITICAL

            return None

    def cleanup_stale(self, max_age: timedelta = timedelta(hours=1)) -> int:
        """清理过期状态 (1 小时未更新).

        Returns:
            清理的数量
        """
        with self._rlock:
            now = datetime.now()
            stale_keys = [
                key
                for key, state in self._states.items()
                if now - state.last_seen > max_age
            ]
            for key in stale_keys:
                del self._states[key]
            return len(stale_keys)

    def get_state(
        self, metric_id: str, symbol: str | None = None
    ) -> AlertState | None:
        """获取某指标的当前状态."""
        with self._rlock:
            return self._states.get((metric_id, symbol))

    def all_states(self) -> dict[tuple[str, str | None], AlertState]:
        """获取所有状态 (快照)."""
        with self._rlock:
            return dict(self._states)


# ============================================================
# 模块级单例访问
# ============================================================
def get_aggregator() -> AlertAggregator:
    """获取 AlertAggregator 单例."""
    return AlertAggregator.get_instance()
