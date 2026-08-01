# v7.6 对冲执行指挥官 -- 解决 Beta=1.05 裸奔问题
# 当前: 3手IF空单 + 有期权计划 + 对冲效果 = 0
# 修复: (1) 确认/强制执行 (2) 自动对齐 (3) 超时告警
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Dict, List, Optional

logger = logging.getLogger("v76.hedge.commander")


class HedgeUrgency(IntEnum):
    ROUTINE = 0  # 例常对齐 (< 10% 偏差)
    ELEVATED = 1  # 需要关注 (10-25% 偏差)
    URGENT = 2  # 紧急 (25-50% 偏差)
    CRITICAL = 3  # 关键 (> 50% 偏差 或 超时 2 天)


@dataclass
class HedgeCommanderConfig:
    target_beta: float = 0.30
    max_beta_tolerance: float = 0.15  # ±0.15 容忍
    urgency_thresholds: Dict[str, float] = field(
        default_factory=lambda: {"routine": 0.10, "elevated": 0.25, "urgent": 0.50}
    )
    max_unhedged_days: int = 2  # 2 天未对齐 → CRITICAL
    auto_close_gap: bool = True  # 自动填补缺口
    min_notional_per_contract: float = 300_000  # IF 合约最小名义


class HedgeExecutionCommander:
    """桥水式: 每日校验 Beta 对齐 → 差距 > 阈值 → 强制发单 → 确认"""

    def __init__(self, config: Optional[HedgeCommanderConfig] = None):
        self.cfg = config or HedgeCommanderConfig()
        self._last_hedge_time: Optional[datetime] = None
        self._consecutive_misalign_days: int = 0
        self._execution_log: List[dict] = []

    def assess(
        self,
        actual_portfolio_beta: float,
        current_hedge_beta_offset: float,
        current_futures_contracts: int,
        portfolio_notional: float,
    ) -> dict:
        """核心: 评估对冲对齐状态 → 返回行动指令"""

        # 1. 计算偏差
        -current_hedge_beta_offset / max(actual_portfolio_beta, 0.001)
        effective_beta = actual_portfolio_beta - current_hedge_beta_offset
        beta_gap = effective_beta - self.cfg.target_beta

        # 2. 紧急度分级
        abs_gap = abs(beta_gap)
        if abs_gap > self.cfg.urgency_thresholds["urgent"]:
            urgency = HedgeUrgency.CRITICAL
        elif abs_gap > self.cfg.urgency_thresholds["elevated"]:
            urgency = HedgeUrgency.URGENT
        elif abs_gap > self.cfg.urgency_thresholds["routine"]:
            urgency = HedgeUrgency.ELEVATED
        else:
            urgency = HedgeUrgency.ROUTINE

        # 3. 超时检查
        days_since_hedge = float("inf")
        if self._last_hedge_time:
            days_since_hedge = (datetime.now() - self._last_hedge_time).days
        if days_since_hedge >= self.cfg.max_unhedged_days:
            urgency = max(urgency, HedgeUrgency.CRITICAL)
            self._consecutive_misalign_days += 1
            logger.warning(
                "对冲超时: %d 天未对齐, Beta_actual=%.3f Beta_target=%.3f",
                self._consecutive_misalign_days,
                effective_beta,
                self.cfg.target_beta,
            )
        else:
            self._consecutive_misalign_days = 0

        # 4. 计算所需合约数
        required_hedge_beta_units = actual_portfolio_beta - self.cfg.target_beta
        portfolio_market_value = portfolio_notional or 1_000_000
        notional_per_contract = self.cfg.min_notional_per_contract
        target_contracts = round(portfolio_market_value * required_hedge_beta_units / notional_per_contract)

        # 5. 生成指令
        contracts_to_adjust = target_contracts - current_futures_contracts

        action = {
            "timestamp": datetime.now().isoformat(),
            "urgency": urgency.name,
            "actual_beta": round(actual_portfolio_beta, 3),
            "hedge_offset": round(current_hedge_beta_offset, 3),
            "effective_beta": round(effective_beta, 3),
            "target_beta": self.cfg.target_beta,
            "beta_gap": round(beta_gap, 3),
            "current_contracts": current_futures_contracts,
            "target_contracts": target_contracts,
            "adjust_contracts": contracts_to_adjust,
            "action_text": self._action_text(contracts_to_adjust, urgency),
            "force_execute": urgency >= HedgeUrgency.URGENT,
            "days_since_last_hedge": days_since_hedge,
        }
        self._execution_log.append(action)

        if urgency >= HedgeUrgency.ELEVATED:
            logger.warning(
                "对冲告警 [%s]: Beta=%.2f/%d 手 → 需要%d手, Δ=%+d手",
                urgency.name,
                effective_beta,
                current_futures_contracts,
                target_contracts,
                contracts_to_adjust,
            )

        return action

    def confirm_executed(self, action: dict):
        """确认对冲已执行"""
        self._last_hedge_time = datetime.now()
        self._consecutive_misalign_days = 0
        action["confirmed"] = True
        logger.info("对冲确认完成: Beta→%.3f", action["effective_beta"])

    @staticmethod
    def _action_text(adj: int, urgency: HedgeUrgency) -> str:
        if adj == 0:
            return "无操作 (已对齐)"
        direction = "加空" if adj > 0 else "减空"
        return f"{urgency.name}: {direction} {abs(adj)} 手"

    def report(self) -> dict:
        if not self._execution_log:
            return {"status": "无历史"}
        latest = self._execution_log[-1]
        return {
            "effective_beta": latest["effective_beta"],
            "target_beta": latest["target_beta"],
            "gap": latest["beta_gap"],
            "urgency": latest["urgency"],
            "contracts_now": latest["current_contracts"],
            "contracts_target": latest["target_contracts"],
            "need_adjust": latest["adjust_contracts"],
            "force_execute": latest["force_execute"],
        }
