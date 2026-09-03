"""策略等级状态机 — 6 档单向降级 + 冷却期升级。

本组件管理策略等级状态机的状态转移，保证降级单向进行，升级需冷却期与最小持续日约束。

状态转移规则:
    降级链: NORMAL → MILD_CORRECTION → MODERATE_CORRECTION → SEVERE_CORRECTION
            → CONSERVATIVE_DEFENSE → CIRCUIT_BREAKER
    - 降级: 单向，禁止跨级 (如 NORMAL → MODERATE_CORRECTION 需阻断)
    - 升级: 需冷却期 (≥5 交易日) + 最小持续日 (温和 5/中度 10/重度 20/保守 20)
    - 熔断: CIRCUIT_BREAKER 只能由管理员手动解除

状态持久化:
    config/auto_hedge_rebalance_state.json 的 strategy_state 字段
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from utils.auto_hedge_rebalance.models import (
    CorrectionAction,
    StrategyLevel,
    StrategyState,
    StrategySwitchEvent,
    TransitionResult,
)

logger = logging.getLogger(__name__)


# ============================================================================
# 等级排序与最小持续日配置
# ============================================================================

_LEVEL_ORDER: dict[StrategyLevel, int] = {
    StrategyLevel.NORMAL: 0,
    StrategyLevel.MILD_CORRECTION: 1,
    StrategyLevel.MODERATE_CORRECTION: 2,
    StrategyLevel.SEVERE_CORRECTION: 3,
    StrategyLevel.CONSERVATIVE_DEFENSE: 4,
    StrategyLevel.CIRCUIT_BREAKER: 5,
}

_LEVEL_MIN_HOLD_DAYS: dict[StrategyLevel, int] = {
    StrategyLevel.NORMAL: 0,
    StrategyLevel.MILD_CORRECTION: 5,
    StrategyLevel.MODERATE_CORRECTION: 10,
    StrategyLevel.SEVERE_CORRECTION: 20,
    StrategyLevel.CONSERVATIVE_DEFENSE: 20,
    StrategyLevel.CIRCUIT_BREAKER: 0,  # 熔断需管理员解除，不受持续日约束
}

# 纠偏动作到目标等级的映射
_ACTION_TARGET_LEVEL: dict[CorrectionAction, StrategyLevel] = {
    CorrectionAction.NONE: StrategyLevel.NORMAL,
    CorrectionAction.MILD_TUNE: StrategyLevel.MILD_CORRECTION,
    CorrectionAction.MODERATE_ROTATE: StrategyLevel.MODERATE_CORRECTION,
    CorrectionAction.SEVERE_REVIEW: StrategyLevel.SEVERE_CORRECTION,
    CorrectionAction.DEFENSE_BOOST: StrategyLevel.CONSERVATIVE_DEFENSE,
    CorrectionAction.EMERGENCY_LIQUIDATE: StrategyLevel.CIRCUIT_BREAKER,
}


class StrategyStateMachine:
    """策略等级状态机。

    管理 6 档策略等级的状态转移，保证降级单向进行，
    升级需冷却期与最小持续日约束，熔断需管理员手动解除。

    Attributes:
        state_path: 状态文件路径。
        cooldown_days: 冷却期交易日数。
    """

    def __init__(
        self,
        state_path: str = "config/auto_hedge_rebalance_state.json",
        cooldown_days: int = 5,
        audit_logger=None,
    ) -> None:
        """初始化策略等级状态机。

        Args:
            state_path: 状态文件路径。
            cooldown_days: 冷却期交易日数 (默认 5)。
            audit_logger: 审计日志记录器 (可选)。
        """
        self.state_path = Path(state_path)
        self.cooldown_days = cooldown_days
        self.audit_logger = audit_logger
        self._state: StrategyState = self._load_state()

    def _load_state(self) -> StrategyState:
        """从状态文件加载当前状态。"""
        if not self.state_path.exists():
            return StrategyState()
        try:
            with open(self.state_path, encoding="utf-8") as f:
                data = json.load(f)
            state_data = data.get("strategy_state", {})
            return StrategyState(
                current_level=StrategyLevel(state_data.get("current_level", "NORMAL")),
                last_transition_time=state_data.get("last_transition_time", ""),
                cooldown_until=state_data.get("cooldown_until", ""),
                pending_switch_event_id=state_data.get("pending_switch_event_id"),
                level_min_hold_days=state_data.get("level_min_hold_days", 0),
            )
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            logger.warning("加载策略状态失败，使用默认状态: %s", exc)
            return StrategyState()

    def _persist_state(self, new_state: StrategyState) -> None:
        """持久化状态到文件 (仅在状态变更时写入)。"""
        if new_state == self._state:
            return

        data: dict = {}
        if self.state_path.exists():
            try:
                with open(self.state_path, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError, TypeError):
                data = {}

        data["strategy_state"] = {
            "current_level": new_state.current_level.value,
            "last_transition_time": new_state.last_transition_time,
            "cooldown_until": new_state.cooldown_until,
            "pending_switch_event_id": new_state.pending_switch_event_id,
            "level_min_hold_days": new_state.level_min_hold_days,
        }
        data["last_decision_time"] = datetime.now().isoformat(timespec="seconds")

        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        self._state = new_state
        logger.debug("策略状态持久化: level=%s", new_state.current_level.value)

    def get_current_state(self) -> StrategyState:
        """返回当前策略状态。"""
        return self._state

    def _now_iso(self) -> str:
        return datetime.now().isoformat(timespec="seconds")

    def _parse_time(self, iso_str: str) -> datetime | None:
        if not iso_str:
            return None
        try:
            return datetime.fromisoformat(iso_str)
        except ValueError:
            return None

    def _check_cooldown(self) -> tuple[bool, str]:
        """检查冷却期是否已过。

        Returns:
            (是否可升级, 阻断原因)。
        """
        if not self._state.cooldown_until:
            return True, ""
        cooldown_end = self._parse_time(self._state.cooldown_until)
        if cooldown_end is None:
            return True, ""
        if datetime.now() < cooldown_end:
            remaining = (cooldown_end - datetime.now()).days
            return False, f"冷却期内，剩余{remaining}天"
        return True, ""

    def _check_min_hold_days(self) -> tuple[bool, str]:
        """检查最小持续交易日是否已满足。

        Returns:
            (是否可升级, 阻断原因)。
        """
        min_days = _LEVEL_MIN_HOLD_DAYS.get(self._state.current_level, 0)
        if min_days == 0:
            return True, ""

        last_transition = self._parse_time(self._state.last_transition_time)
        if last_transition is None:
            return True, ""

        held_days = (datetime.now() - last_transition).days
        if held_days < min_days:
            return False, f"最小持续日不足: 已持{held_days}天，需{min_days}天"
        return True, ""

    def transition(
        self,
        current: StrategyLevel,
        action: CorrectionAction,
        emergency: bool = False,
    ) -> TransitionResult:
        """执行状态转移。

        Args:
            current: 当前策略等级 (应与内部状态一致)。
            action: 纠偏动作。
            emergency: v8.7 紧急模式 — 极端事件(单日跌幅>5%/VIX>40)时允许跨级降级,
                       快速进入防御状态而非逐级降级. 默认 False 保持向后兼容.

        Returns:
            状态转移结果 TransitionResult。
        """
        target_level = _ACTION_TARGET_LEVEL.get(action, StrategyLevel.NORMAL)
        current_order = _LEVEL_ORDER.get(current, 0)
        target_order = _LEVEL_ORDER.get(target_level, 0)

        # 无变化
        if target_level == current:
            return TransitionResult(
                new_level=current,
                new_params={},
                cooldown_active=False,
                blocked_reason="",
            )

        # 熔断状态锁定: CIRCUIT_BREAKER 只能由管理员解除
        if (
            current == StrategyLevel.CIRCUIT_BREAKER
            and target_level != StrategyLevel.CIRCUIT_BREAKER
        ):
            return TransitionResult(
                new_level=current,
                new_params={},
                cooldown_active=False,
                blocked_reason="熔断状态锁定，需管理员手动解除",
            )

        # 降级 (目标等级更高 = 更保守)
        if target_order > current_order:
            # v8.7: 紧急模式允许跨级降级 (极端事件快速响应)
            if target_order > current_order + 1 and not emergency:
                return TransitionResult(
                    new_level=current,
                    new_params={},
                    cooldown_active=False,
                    blocked_reason=f"禁止跨级降级: {current.value} → {target_level.value}",
                )
            if target_order > current_order + 1 and emergency:
                logger.warning(
                    "[v8.7紧急跨级降级] %s → %s (紧急模式, 跳过逐级降级)",
                    current.value,
                    target_level.value,
                )
            return self._execute_transition(current, target_level, action)

        # 升级 (目标等级更低 = 更激进)
        # 检查冷却期
        cooldown_ok, cooldown_reason = self._check_cooldown()
        if not cooldown_ok:
            return TransitionResult(
                new_level=current,
                new_params={},
                cooldown_active=True,
                blocked_reason=cooldown_reason,
            )

        # 检查最小持续日
        hold_ok, hold_reason = self._check_min_hold_days()
        if not hold_ok:
            return TransitionResult(
                new_level=current,
                new_params={},
                cooldown_active=False,
                blocked_reason=hold_reason,
            )

        return self._execute_transition(current, target_level, action)

    def _execute_transition(
        self,
        from_level: StrategyLevel,
        to_level: StrategyLevel,
        action: CorrectionAction,
    ) -> TransitionResult:
        """执行实际的状态转移。"""
        now = self._now_iso()
        cooldown_until = (
            datetime.now() + timedelta(days=self.cooldown_days)
        ).isoformat(timespec="seconds")
        min_hold = _LEVEL_MIN_HOLD_DAYS.get(to_level, 0)

        switch_event = StrategySwitchEvent(
            event_id=str(uuid.uuid4()),
            timestamp=now,
            from_level=from_level,
            to_level=to_level,
            trigger_reason=f"纠偏动作: {action.value}",
            params_before={},
            params_after={},
            approver="",
            approved=False,
        )

        new_state = StrategyState(
            current_level=to_level,
            last_transition_time=now,
            cooldown_until=cooldown_until,
            pending_switch_event_id=switch_event.event_id,
            level_min_hold_days=min_hold,
        )
        self._persist_state(new_state)

        # 记录审计日志
        if self.audit_logger is not None:
            self.audit_logger.log_strategy_switch(switch_event)

        logger.info(
            "策略状态转移: %s → %s (action=%s)",
            from_level.value,
            to_level.value,
            action.value,
        )

        return TransitionResult(
            new_level=to_level,
            new_params={},
            switch_event=switch_event,
            cooldown_active=False,
            blocked_reason="",
        )

    def approve_switch(
        self,
        switch_event_id: str,
        approver: str,
        approved: bool,
    ) -> bool:
        """审批策略切换事件。

        Args:
            switch_event_id: 切换事件 ID。
            approver: 审批人。
            approved: 是否批准。

        Returns:
            是否成功处理。
        """
        if self._state.pending_switch_event_id != switch_event_id:
            logger.warning(
                "切换事件ID不匹配: %s != %s",
                switch_event_id,
                self._state.pending_switch_event_id,
            )
            return False

        if not approved:
            logger.info("策略切换被拒绝: %s (审批人: %s)", switch_event_id, approver)
            new_state = StrategyState(
                current_level=self._state.current_level,
                last_transition_time=self._state.last_transition_time,
                cooldown_until=self._state.cooldown_until,
                pending_switch_event_id=None,
                level_min_hold_days=self._state.level_min_hold_days,
            )
            self._persist_state(new_state)
            return True

        # 熔断解除: 从 CIRCUIT_BREAKER 回到 NORMAL
        if self._state.current_level == StrategyLevel.CIRCUIT_BREAKER:
            now = self._now_iso()
            new_state = StrategyState(
                current_level=StrategyLevel.NORMAL,
                last_transition_time=now,
                cooldown_until=(
                    datetime.now() + timedelta(days=self.cooldown_days)
                ).isoformat(timespec="seconds"),
                pending_switch_event_id=None,
                level_min_hold_days=0,
            )
            self._persist_state(new_state)
            logger.info("熔断状态已解除 (审批人: %s)", approver)
            return True

        return True


__all__ = ["StrategyStateMachine"]
