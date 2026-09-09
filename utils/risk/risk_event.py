"""风控事件类型定义 — 模块整合 8.4 (T3.1).

任务: T3.1
责任层: L5 风控
依赖: T1.4 (Feature Flag 框架)

设计原则:
    1. 事件类型穷举 (7 种) — 覆盖对冲基金主要风控场景
    2. dataclass + frozen=True — 事件不可变, 防止订阅者篡改
    3. 严重级别分级 (INFO/WARN/CRITICAL) — 决策聚合器按级别加权
    4. 与 KillSwitch 解耦 (HC-2) — 事件仅做归档, 不影响同步路径

硬约束:
    - HC-2: KillSwitch 同步直调路径延迟 <1ms, 不走总线
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

logger = logging.getLogger("risk_event")


# ============================================================
# 事件类型枚举 (7 种)
# ============================================================
class RiskEventType(StrEnum):
    """风控事件类型 — 7 种核心场景."""

    # 1. 保证金突破 — KillSwitch L1/L2/L3 触发
    MARGIN_BREACH = "margin_breach"

    # 2. 回撤突破 — 单日/累计回撤超限 (Shadow fail-fast)
    DRAWDOWN_BREACH = "drawdown_breach"

    # 3. VaR 突破 — 风险价值超限 (var_monitor)
    VAR_BREACH = "var_breach"

    # 4. 隔夜跳空 — 开盘价偏离超限 (overnight_gap)
    OVERNIGHT_GAP = "overnight_gap"

    # 5. 流动性突破 — 持仓集中度/成交量不足 (liquidity_monitor)
    LIQUIDITY_BREACH = "liquidity_breach"

    # 6. 集中度突破 — 单标的/单行业权重超限 (concentration_monitor)
    CONCENTRATION_BREACH = "concentration_breach"

    # 7. KillSwitch 触发 — 同步路径触发后归档通知
    KILL_SWITCH_TRIGGERED = "kill_switch_triggered"


class RiskSeverity(StrEnum):
    """事件严重级别."""

    INFO = "info"  # 信息级 (日志记录)
    WARN = "warn"  # 警告级 (减仓/观察)
    CRITICAL = "critical"  # 严重级 (强制平仓/熔断)


class RiskAction(StrEnum):
    """风控决策动作."""

    PASS = "pass"  # nosec B105 # 风控动作枚举值, 非密码  # 通过 (无动作)
    REDUCE_POSITION = "reduce"  # 减仓
    DISABLE_NEW_ORDERS = "disable_new"  # 禁止开新仓
    FORCE_LIQUIDATE = "force_liquidate"  # 强制平仓
    KILL_SWITCH = "kill_switch"  # 触发 KillSwitch


# ============================================================
# 事件载体
# ============================================================
@dataclass(frozen=True)
class RiskEvent:
    """风控事件 (不可变).

    Attributes:
        event_type: 事件类型
        source: 事件源 (如 "kill_switch", "var_monitor", "shadow_account")
        severity: 严重级别
        payload: 事件详情 (如 {"margin_usage": 0.78, "level": 2})
        timestamp: ISO 格式时间戳 (UTC)
        symbol: 相关标的 (可选, None 表示组合级事件)
    """

    event_type: RiskEventType
    source: str
    severity: RiskSeverity
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    symbol: str | None = None

    def __post_init__(self) -> None:
        """校验 + 自动填充时间戳."""
        if not self.timestamp:
            # frozen=True 时用 object.__setattr__ 绕过不可变限制
            object.__setattr__(self, "timestamp", self._utcnow_iso())
        if not isinstance(self.event_type, RiskEventType):
            raise TypeError(
                f"event_type must be RiskEventType, got {type(self.event_type)}"
            )
        if not isinstance(self.severity, RiskSeverity):
            raise TypeError(f"severity must be RiskSeverity, got {type(self.severity)}")
        if not self.source or not isinstance(self.source, str):
            raise ValueError(f"source must be non-empty string, got {self.source!r}")

    @staticmethod
    def _utcnow_iso() -> str:
        """当前 UTC 时间 ISO 格式."""
        return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S") + "Z"

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典 (用于日志/审计)."""
        return {
            "event_type": self.event_type.value,
            "source": self.source,
            "severity": self.severity.value,
            "payload": dict(self.payload),
            "timestamp": self.timestamp,
            "symbol": self.symbol,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RiskEvent:
        """从字典反序列化.

        防御性枚举解析: 遇到无效值回退到安全默认而非 ValueError.
        """
        # GLM 4.5 复核: 原版 RiskEventType(data.get("event_type", "")) 在无效值时
        # 直接抛 ValueError, 会把整条事件链崩断. 改为 try/except 回退到 KILL_SWITCH_TRIGGERED.
        raw_event = data.get("event_type", "")
        try:
            event_type = (
                RiskEventType(raw_event)
                if raw_event
                else RiskEventType.KILL_SWITCH_TRIGGERED
            )
        except ValueError:
            logger.warning(
                "from_dict: 未知 event_type=%r, 回退到 KILL_SWITCH_TRIGGERED", raw_event
            )
            event_type = RiskEventType.KILL_SWITCH_TRIGGERED
        raw_severity = data.get("severity", "info")
        try:
            severity = RiskSeverity(raw_severity) if raw_severity else RiskSeverity.INFO
        except ValueError:
            logger.warning("from_dict: 未知 severity=%r, 回退到 INFO", raw_severity)
            severity = RiskSeverity.INFO
        return cls(
            event_type=event_type,
            source=str(data.get("source", "")),
            severity=severity,
            payload=dict(data.get("payload", {}) or {}),
            timestamp=str(data.get("timestamp", "")),
            symbol=data.get("symbol"),
        )


# ============================================================
# 决策结果
# ============================================================
@dataclass(frozen=True)
class RiskDecision:
    """风控决策 (单模块输出).

    Attributes:
        action: 决策动作 (PASS/REDUCE_POSITION/...)
        reason: 决策原因 (人类可读)
        confidence: 置信度 [0, 1]
        source: 决策来源 (模块名)
        reduce_pct: 减仓比例 (仅 REDUCE_POSITION 时有效, [0, 1])
    """

    action: RiskAction
    reason: str
    confidence: float = 1.0
    source: str = ""
    reduce_pct: float = 0.0

    def __post_init__(self) -> None:
        """校验参数."""
        if not isinstance(self.action, RiskAction):
            raise TypeError(f"action must be RiskAction, got {type(self.action)}")
        if not self.reason or not isinstance(self.reason, str):
            raise ValueError("reason must be non-empty string")
        # confidence 限制在 [0, 1]
        clamped = max(0.0, min(1.0, float(self.confidence)))
        if abs(clamped - self.confidence) > 1e-9:
            object.__setattr__(self, "confidence", clamped)
        # reduce_pct 限制在 [0, 1]
        clamped_pct = max(0.0, min(1.0, float(self.reduce_pct)))
        if abs(clamped_pct - self.reduce_pct) > 1e-9:
            object.__setattr__(self, "reduce_pct", clamped_pct)

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典."""
        return {
            "action": self.action.value,
            "reason": self.reason,
            "confidence": self.confidence,
            "source": self.source,
            "reduce_pct": self.reduce_pct,
        }


# ============================================================
# 便捷工厂函数
# ============================================================
def make_margin_breach_event(
    source: str,
    margin_usage: float,
    level: int,
    severity: RiskSeverity | None = None,
    **extra: Any,
) -> RiskEvent:
    """创建保证金突破事件.

    Args:
        source: 事件源 (如 "kill_switch")
        margin_usage: 保证金占用率 [0, 1]
        level: KillSwitch 级别 (1/2/3)
        severity: 严重级别 (None 时按 level 自动推断)
        **extra: 附加 payload
    """
    if severity is None:
        severity = (
            RiskSeverity.CRITICAL
            if level >= 2
            else RiskSeverity.WARN if level == 1 else RiskSeverity.INFO
        )
    payload = {"margin_usage": float(margin_usage), "level": int(level), **extra}
    return RiskEvent(
        event_type=RiskEventType.MARGIN_BREACH,
        source=source,
        severity=severity,
        payload=payload,
    )


def make_drawdown_breach_event(
    source: str,
    drawdown_pct: float,
    window: str = "daily",
    severity: RiskSeverity | None = None,
    **extra: Any,
) -> RiskEvent:
    """创建回撤突破事件 (Shadow fail-fast).

    Args:
        source: 事件源 (如 "shadow_account")
        drawdown_pct: 回撤百分比 [0, 1]
        window: 窗口 ("daily" / "cumulative_3d")
        severity: 严重级别 (None 时按回撤幅度推断)
        **extra: 附加 payload
    """
    if severity is None:
        severity = (
            RiskSeverity.CRITICAL
            if drawdown_pct >= 0.05
            else RiskSeverity.WARN if drawdown_pct >= 0.03 else RiskSeverity.INFO
        )
    payload = {"drawdown_pct": float(drawdown_pct), "window": window, **extra}
    return RiskEvent(
        event_type=RiskEventType.DRAWDOWN_BREACH,
        source=source,
        severity=severity,
        payload=payload,
    )


def make_kill_switch_triggered_event(
    source: str,
    level: int,
    actions_taken: list[str] | None = None,
    **extra: Any,
) -> RiskEvent:
    """创建 KillSwitch 触发归档事件 (HC-2: 仅归档, 不影响同步路径).

    Args:
        source: 事件源 (如 "kill_switch")
        level: KillSwitch 级别 (1/2/3)
        actions_taken: 已执行动作列表
        **extra: 附加 payload
    """
    payload = {
        "level": int(level),
        "actions_taken": list(actions_taken or []),
        **extra,
    }
    return RiskEvent(
        event_type=RiskEventType.KILL_SWITCH_TRIGGERED,
        source=source,
        severity=RiskSeverity.CRITICAL,  # KillSwitch 触发恒为 CRITICAL
        payload=payload,
    )


__all__ = [
    "RiskAction",
    "RiskDecision",
    "RiskEvent",
    "RiskEventType",
    "RiskSeverity",
    "make_drawdown_breach_event",
    "make_kill_switch_triggered_event",
    "make_margin_breach_event",
]
