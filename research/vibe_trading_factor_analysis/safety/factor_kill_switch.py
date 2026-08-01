# -*- coding: utf-8 -*-
"""FactorKillSwitch - E5 实盘防线（CIO 视角 v1.0）

实时监控已准入因子，自动降权/禁用/退役。
锁定参数（DECISION v1.0）：
  - 连续 5 日 IC < 0.02  -> degraded（仓位减半）
  - 连续 10 日 IC < 0    -> disabled（自动禁用）
  - 连续 20 日 IC < 0    -> retired（强制退役，不可恢复）
  - 单日回撤 > 3%       -> 仓位减半（T+0）
  - 累计回撤 > 8%       -> 仓位减至 25%
  - 累计回撤 > 12%      -> 全部退出
"""
from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("factor_kill_switch")


# ============== CIO 锁定参数（DECISION v1.0）==============
IC_DEGRADED_THRESHOLD = 0.02     # IC < 0.02 触发 degraded
IC_DISABLED_THRESHOLD = 0.0       # IC < 0 触发 disabled
DEGRADED_CONSECUTIVE_DAYS = 5     # 连续 5 日
DISABLED_CONSECUTIVE_DAYS = 10    # 连续 10 日
RETIRED_CONSECUTIVE_DAYS = 20     # 连续 20 日

DAILY_DRAWDOWN_HALF = 0.03        # 单日回撤 3% 减半
CUMULATIVE_DRAWDOWN_QUARTER = 0.08  # 累计 8% 减至 25%
CUMULATIVE_DRAWDOWN_EXIT = 0.12    # 累计 12% 全部退出


class FactorStatus(str, Enum):
    """因子状态机"""
    ACTIVE = "active"             # 正常运行，满仓
    WARNED = "warned"             # 警告（IC 偏低但未达 degraded）
    DEGRADED = "degraded"         # 仓位减半
    DISABLED = "disabled"         # 自动禁用
    RETIRED = "retired"           # 强制退役（不可恢复）
    EMERGENCY_EXIT = "emergency_exit"  # 紧急退出（回撤触发）


@dataclass
class KillSwitchStatus:
    """KillSwitch 状态"""
    factor_name: str
    status: str = FactorStatus.ACTIVE.value
    consecutive_low_ic_days: int = 0
    consecutive_neg_ic_days: int = 0
    current_position_ratio: float = 1.0    # 当前仓位比例（1.0 = 满仓）
    peak_pnl: float = 0.0
    current_pnl: float = 0.0
    cumulative_drawdown: float = 0.0
    last_ic: float = 0.0
    ic_history: List[float] = field(default_factory=list)
    pnl_history: List[float] = field(default_factory=list)
    triggers: List[str] = field(default_factory=list)  # 触发记录
    last_update: str = ""

    @property
    def is_tradable(self) -> bool:
        """是否可交易（active/warned/degraded 可交易，disabled/retired 不可）"""
        return self.status in (
            FactorStatus.ACTIVE.value,
            FactorStatus.WARNED.value,
            FactorStatus.DEGRADED.value,
        )

    @property
    def is_terminal(self) -> bool:
        """是否终态（retired 不可恢复）"""
        return self.status == FactorStatus.RETIRED.value

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class FactorKillSwitch:
    """因子 KillSwitch（E5 实盘防线）

    用法：
        >>> ks = FactorKillSwitch()
        >>> ks.init("VT_MOM_ILLIQUID_60D")
        >>> # 每日更新 IC 和 PnL
        >>> status = ks.update("VT_MOM_ILLIQUID_60D", ic=0.05, daily_pnl=0.001)
        >>> if not status.is_tradable:
        ...     # 触发 KillSwitch，停止交易
        ...     pass
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        c = config or {}
        self.ic_degraded_threshold = float(c.get("ic_degraded_threshold", IC_DEGRADED_THRESHOLD))
        self.ic_disabled_threshold = float(c.get("ic_disabled_threshold", IC_DISABLED_THRESHOLD))
        self.degraded_days = int(c.get("degraded_consecutive_days", DEGRADED_CONSECUTIVE_DAYS))
        self.disabled_days = int(c.get("disabled_consecutive_days", DISABLED_CONSECUTIVE_DAYS))
        self.retired_days = int(c.get("retired_consecutive_days", RETIRED_CONSECUTIVE_DAYS))
        self.daily_dd_half = float(c.get("daily_dd_half", DAILY_DRAWDOWN_HALF))
        self.cum_dd_quarter = float(c.get("cum_dd_quarter", CUMULATIVE_DRAWDOWN_QUARTER))
        self.cum_dd_exit = float(c.get("cum_dd_exit", CUMULATIVE_DRAWDOWN_EXIT))
        self._states: Dict[str, KillSwitchStatus] = {}
        logger.info(
            "[FactorKillSwitch] 初始化 | degraded=%dd@IC<%.3f disabled=%dd@IC<%.2f retired=%dd@IC<%.2f",
            self.degraded_days, self.ic_degraded_threshold,
            self.disabled_days, self.ic_disabled_threshold,
            self.retired_days, self.ic_disabled_threshold,
        )

    def init(self, factor_name: str) -> KillSwitchStatus:
        """初始化因子监控状态"""
        s = KillSwitchStatus(factor_name=factor_name)
        self._states[factor_name] = s
        logger.info("[FactorKillSwitch] %s 初始化监控", factor_name)
        return s

    def update(
        self,
        factor_name: str,
        ic: float,
        daily_pnl: float,
        timestamp: str = "",
    ) -> KillSwitchStatus:
        """每日更新因子状态

        Args:
            factor_name: 因子名称
            ic: 当日 IC（Information Coefficient）
            daily_pnl: 当日 PnL（收益率，如 0.001 = 0.1%）
            timestamp: 时间戳

        Returns:
            更新后的 KillSwitchStatus
        """
        if factor_name not in self._states:
            self.init(factor_name)
        s = self._states[factor_name]
        s.last_update = timestamp
        s.last_ic = float(ic)
        s.ic_history.append(float(ic))
        s.pnl_history.append(float(daily_pnl))

        # 终态不再更新
        if s.is_terminal:
            return s

        # ============ Step 1: IC 监控 ============
        if not math.isfinite(ic):
            ic = 0.0

        if ic < self.ic_disabled_threshold:
            s.consecutive_neg_ic_days += 1
        else:
            s.consecutive_neg_ic_days = 0

        if ic < self.ic_degraded_threshold:
            s.consecutive_low_ic_days += 1
        else:
            s.consecutive_low_ic_days = 0

        # ============ Step 2: PnL 与回撤监控 ============
        s.current_pnl += daily_pnl
        if s.current_pnl > s.peak_pnl:
            s.peak_pnl = s.current_pnl
        s.cumulative_drawdown = max(0.0, s.peak_pnl - s.current_pnl)

        # ============ Step 3: 状态转移 ============
        prev_status = s.status
        triggers = []

        # 3.1 IC 退役（最高优先级）
        if s.consecutive_neg_ic_days >= self.retired_days:
            s.status = FactorStatus.RETIRED.value
            s.current_position_ratio = 0.0
            triggers.append(f"IC<0 连续 {s.consecutive_neg_ic_days}d -> RETIRED")
        # 3.2 IC 禁用
        elif s.consecutive_neg_ic_days >= self.disabled_days:
            s.status = FactorStatus.DISABLED.value
            s.current_position_ratio = 0.0
            triggers.append(f"IC<0 连续 {s.consecutive_neg_ic_days}d -> DISABLED")
        # 3.3 IC 降级
        elif s.consecutive_low_ic_days >= self.degraded_days:
            if s.status != FactorStatus.DISABLED.value:
                s.status = FactorStatus.DEGRADED.value
                s.current_position_ratio = 0.5
                triggers.append(f"IC<{self.ic_degraded_threshold} 连续 {s.consecutive_low_ic_days}d -> DEGRADED")
        # 3.4 累计回撤紧急退出
        elif s.cumulative_drawdown >= self.cum_dd_exit:
            s.status = FactorStatus.EMERGENCY_EXIT.value
            s.current_position_ratio = 0.0
            triggers.append(f"cum_dd={s.cumulative_drawdown:.3f} >= {self.cum_dd_exit} -> EMERGENCY_EXIT")
        # 3.5 累计回撤减至 25%
        elif s.cumulative_drawdown >= self.cum_dd_quarter:
            if s.status != FactorStatus.DISABLED.value and s.status != FactorStatus.DEGRADED.value:
                s.status = FactorStatus.DEGRADED.value
            s.current_position_ratio = min(s.current_position_ratio, 0.25)
            triggers.append(f"cum_dd={s.cumulative_drawdown:.3f} >= {self.cum_dd_quarter} -> 减至 25%")
        # 3.6 单日回撤减半
        elif daily_pnl < -self.daily_dd_half:
            if s.status not in (FactorStatus.DISABLED.value, FactorStatus.EMERGENCY_EXIT.value):
                s.status = FactorStatus.DEGRADED.value
            s.current_position_ratio = min(s.current_position_ratio, 0.5)
            triggers.append(f"daily_pnl={daily_pnl:.4f} < -{self.daily_dd_half} -> 减半")
        # 3.7 恢复（如果连续多日 IC 良好且无回撤，可恢复 ACTIVE）
        elif (
            s.consecutive_low_ic_days == 0
            and s.cumulative_drawdown < self.daily_dd_half
            and s.status == FactorStatus.DEGRADED.value
        ):
            s.status = FactorStatus.ACTIVE.value
            s.current_position_ratio = 1.0
            triggers.append("IC 恢复 + 无回撤 -> ACTIVE")

        # 警告状态
        if 0 < s.consecutive_low_ic_days < self.degraded_days and s.status == FactorStatus.ACTIVE.value:
            s.status = FactorStatus.WARNED.value
            triggers.append(f"IC 偏低 {s.consecutive_low_ic_days}d -> WARNED")

        # ACTIVE 满仓
        if s.status == FactorStatus.ACTIVE.value:
            s.current_position_ratio = 1.0

        if triggers:
            s.triggers.extend(triggers)
            logger.warning(
                "[FactorKillSwitch] %s | %s -> %s | pos=%.2f ic=%.4f dd=%.3f | %s",
                factor_name, prev_status, s.status,
                s.current_position_ratio, s.last_ic, s.cumulative_drawdown,
                "; ".join(triggers),
            )

        return s

    def get_status(self, factor_name: str) -> Optional[KillSwitchStatus]:
        """获取因子当前状态"""
        return self._states.get(factor_name)

    def list_all(self) -> Dict[str, KillSwitchStatus]:
        """列出所有监控中的因子状态"""
        return dict(self._states)

    def force_retire(self, factor_name: str, reason: str = "") -> bool:
        """人工强制退役（不可恢复）"""
        if factor_name not in self._states:
            return False
        s = self._states[factor_name]
        s.status = FactorStatus.RETIRED.value
        s.current_position_ratio = 0.0
        s.triggers.append(f"人工强制退役: {reason}")
        logger.warning("[FactorKillSwitch] %s 人工强制退役 | reason=%s", factor_name, reason)
        return True


def quick_check(factor_name: str, ic_series: List[float], pnl_series: List[float]) -> KillSwitchStatus:
    """便捷函数：批量输入 IC 和 PnL 序列，返回最终状态"""
    ks = FactorKillSwitch()
    ks.init(factor_name)
    s = None
    for ic, pnl in zip(ic_series, pnl_series):
        s = ks.update(factor_name, ic=ic, daily_pnl=pnl)
        if s.is_terminal:
            break
    return s
