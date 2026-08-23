"""紧急熔断器 — 盘中紧急保护机制。

本组件实现盘中紧急保护，当单日跌幅 >5% 或回撤 >25% 时触发熔断，
熔断后锁定状态，需管理员手动解除。

触发条件:
    - 单日跌幅 > 5%: 触发盘中紧急再评估 (强制对冲至 40%、暂停再平衡买入)
    - 回撤 > 25%: 触发熔断 (清仓高波动至 50%、防御+现金 ≥ 60%)

状态持久化:
    config/auto_hedge_rebalance_state.json 的 breaker_status 字段
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from utils.auto_hedge_rebalance.models import BreakerStatus

logger = logging.getLogger(__name__)


# ============================================================================
# EmergencyAction 数据结构
# ============================================================================


@dataclass(frozen=True)
class EmergencyAction:
    """紧急保护动作。

    Attributes:
        action_type: 动作类型 (emergency_reassess/circuit_break/release)。
        description: 动作描述。
        hedge_ratio_target: 目标对冲比例。
        rebalance_paused: 是否暂停再平衡。
        high_vol_sell_threshold: 高波动清仓阈值。
        defense_cash_min: 防御+现金下限。
    """

    action_type: str = ""
    description: str = ""
    hedge_ratio_target: float = 0.0
    rebalance_paused: bool = False
    high_vol_sell_threshold: float = 0.0
    defense_cash_min: float = 0.0


# ============================================================================
# CircuitBreaker
# ============================================================================


class CircuitBreaker:
    """紧急熔断器。

    盘中紧急保护机制，单日跌幅 >5% 或回撤 >25% 触发熔断，
    熔断状态需管理员手动解除，不得自动恢复。

    Attributes:
        state_path: 状态文件路径 (与状态机共享)。
        daily_drop_trigger: 单日跌幅触发阈值 (默认 5%)。
        extreme_drawdown_trigger: 极端回撤触发阈值 (默认 25%)。
    """

    def __init__(
        self,
        state_path: str = "config/auto_hedge_rebalance_state.json",
        daily_drop_trigger: float = 0.05,
        extreme_drawdown_trigger: float = 0.25,
        high_vol_sell_threshold: float = 0.50,
        defense_cash_min: float = 0.60,
        audit_logger=None,
    ) -> None:
        """初始化紧急熔断器。

        Args:
            state_path: 状态文件路径 (与状态机共享同一文件)。
            daily_drop_trigger: 单日跌幅触发阈值 (默认 5%)。
            extreme_drawdown_trigger: 极端回撤触发阈值 (默认 25%)。
            high_vol_sell_threshold: 高波动清仓阈值 (默认 50%)。
            defense_cash_min: 防御+现金下限 (默认 60%)。
            audit_logger: 审计日志记录器 (可选)。
        """
        self.state_path = Path(state_path)
        self.daily_drop_trigger = daily_drop_trigger
        self.extreme_drawdown_trigger = extreme_drawdown_trigger
        self.high_vol_sell_threshold = high_vol_sell_threshold
        self.defense_cash_min = defense_cash_min
        self.audit_logger = audit_logger
        self._status: BreakerStatus = self._load_status()

    def _load_status(self) -> BreakerStatus:
        """从状态文件加载熔断器状态。"""
        if not self.state_path.exists():
            return BreakerStatus()
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            status_data = data.get("breaker_status", {})
            return BreakerStatus(
                active=status_data.get("active", False),
                trigger_reason=status_data.get("trigger_reason"),
                trigger_time=status_data.get("trigger_time"),
                emergency_action=status_data.get("emergency_action"),
            )
        except Exception as exc:
            logger.warning("加载熔断器状态失败，使用默认状态: %s", exc)
            return BreakerStatus()

    def _persist_status(self, new_status: BreakerStatus) -> None:
        """持久化熔断器状态到文件。"""
        if new_status == self._status:
            return

        data: dict = {}
        if self.state_path.exists():
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}

        data["breaker_status"] = {
            "active": new_status.active,
            "trigger_reason": new_status.trigger_reason,
            "trigger_time": new_status.trigger_time,
            "emergency_action": new_status.emergency_action,
        }

        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        self._status = new_status
        logger.debug("熔断器状态持久化: active=%s", new_status.active)

    def _now_iso(self) -> str:
        return datetime.now().isoformat(timespec="seconds")

    def check(self, daily_drop: float, max_drawdown: float) -> BreakerStatus:
        """检查是否触发熔断。

        Args:
            daily_drop: 单日跌幅 (正数，如 0.05 表示 5%)。
            max_drawdown: 最大回撤 (正数，如 0.25 表示 25%)。

        Returns:
            当前熔断器状态。
        """
        # 已熔断状态，保持锁定
        if self._status.active:
            return self._status

        # 回撤 > 25% 触发熔断
        if max_drawdown > self.extreme_drawdown_trigger:
            self.trigger_emergency("circuit_break")
            return self._status

        # 单日跌幅 > 5% 触发盘中紧急再评估
        if daily_drop > self.daily_drop_trigger:
            self.trigger_emergency("emergency_reassess")
            return self._status

        return self._status

    def trigger_emergency(self, action: str) -> EmergencyAction:
        """执行紧急保护动作。

        Args:
            action: 动作类型 ("emergency_reassess" 或 "circuit_break")。

        Returns:
            紧急保护动作详情。
        """
        now = self._now_iso()

        if action == "circuit_break":
            emergency_action = EmergencyAction(
                action_type="circuit_break",
                description="熔断触发: 清仓高波动至50%，防御+现金≥60%",
                hedge_ratio_target=0.40,
                rebalance_paused=True,
                high_vol_sell_threshold=self.high_vol_sell_threshold,
                defense_cash_min=self.defense_cash_min,
            )
            new_status = BreakerStatus(
                active=True,
                trigger_reason=f"回撤超过{self.extreme_drawdown_trigger:.0%}",
                trigger_time=now,
                emergency_action=emergency_action.description,
            )
            logger.warning("[熔断] %s", emergency_action.description)

        elif action == "emergency_reassess":
            emergency_action = EmergencyAction(
                action_type="emergency_reassess",
                description="盘中紧急再评估: 强制对冲至40%，暂停再平衡买入",
                hedge_ratio_target=0.40,
                rebalance_paused=True,
            )
            new_status = BreakerStatus(
                active=False,  # 紧急再评估不锁定熔断
                trigger_reason=f"单日跌幅超过{self.daily_drop_trigger:.0%}",
                trigger_time=now,
                emergency_action=emergency_action.description,
            )
            logger.warning("[紧急] %s", emergency_action.description)

        else:
            emergency_action = EmergencyAction(action_type=action, description="未知动作")
            new_status = self._status

        self._persist_status(new_status)

        # 记录审计日志
        if self.audit_logger is not None:
            self.audit_logger.log_circuit_breaker(
                {
                    "action": action,
                    "trigger_reason": new_status.trigger_reason,
                    "emergency_action": emergency_action.description,
                    "trigger_time": now,
                }
            )

        return emergency_action

    def is_active(self) -> bool:
        """返回熔断是否活跃。"""
        return self._status.active

    def release(self, approver: str, comment: str = "") -> bool:
        """管理员手动解除熔断。

        Args:
            approver: 审批人姓名。
            comment: 解除备注。

        Returns:
            是否成功解除。
        """
        if not self._status.active:
            logger.info("熔断未激活，无需解除")
            return False

        new_status = BreakerStatus(
            active=False,
            trigger_reason=None,
            trigger_time=None,
            emergency_action=None,
        )
        self._persist_status(new_status)

        if self.audit_logger is not None:
            self.audit_logger.log_circuit_breaker(
                {
                    "action": "release",
                    "trigger_reason": "管理员手动解除",
                    "approver": approver,
                    "comment": comment,
                },
                approver=approver,
            )

        logger.info("熔断状态已解除 (审批人: %s, 备注: %s)", approver, comment)
        return True

    def get_status(self) -> BreakerStatus:
        """返回当前熔断器状态。"""
        return self._status


__all__ = ["CircuitBreaker", "EmergencyAction"]