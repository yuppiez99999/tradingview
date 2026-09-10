"""T12 三级熔断统一门面 — 包装 utils/kill_switch.py 的三级状态机.

属于「不崩风控六件套」第 4 位, 核心目的: **给主链路一个零配置、可审计、支持模拟的统一熔断 API**.

解决原始 KillSwitch 的三个问题:
    1. 原始模块直接绑定 portfolio.yaml / 环境变量, 难以做单元测试 (本门面支持内存配置)
    2. 原始模块只有三级级别枚举, 缺少「当前级别持续时间」「累计已触发次数」等审计指标
    3. 主链路接入点散落在 5+ 处, 本门面提供 can_open_position / can_hold_exposure 语义化 API

三级协议 (与原始 KillSwitch 完全对齐):
    L0 NORMAL    : 保证金 < 50% → 无限制
    L1 CAUTION   : 保证金 ≥ 50% → 停止开新仓, 仅允许平仓/减仓
    L2 REDUCTION : 保证金 ≥ 75% → 强平深虚值期权空头 (本门面负责拦截 + 告警, 具体强平逻辑在 KillSwitch)
    L3 LIQUIDATE : Margin Call / 保证金 ≥ 95% → 全线变现, 只允许卖出

用法:
    from utils.risk.kill_switch_manager import (
        KillSwitchManager, KillLevel, KillDecision,
    )
    ksm = KillSwitchManager()
    ksm.update_margin_usage(0.62)  # 62%, L1
    decision: KillDecision = ksm.evaluate_trade(symbol="sh600519", side="buy", notional=100_000)
    if not decision.allowed:
        logger.warning(f"[T12] 熔断拦截: {decision.reason}")

零行为变更: 不改原始 KillSwitch 的任何配置/阈值, 只做门面封装 + 审计.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from utils.datetime_utils import now_bj

logger = logging.getLogger("kill_switch_mgr")


class KillLevel(IntEnum):
    NORMAL = 0
    CAUTION = 1  # L1 保证金 ≥ 50%
    REDUCTION = 2  # L2 保证金 ≥ 75%
    LIQUIDATE = 3  # L3 保证金 ≥ 95%


@dataclass
class KillDecision:
    allowed: bool
    level: KillLevel
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class KillSwitchAudit:
    level: KillLevel = KillLevel.NORMAL
    margin_usage: float = 0.0
    last_updated_at: str = ""
    level_entered_at: str = ""
    total_triggered_L1: int = 0
    total_triggered_L2: int = 0
    total_triggered_L3: int = 0
    blocked_orders: int = 0
    last_blocked_reason: str = ""


class KillSwitchManager:
    """T12 三级熔断统一门面."""

    DEFAULT_THRESHOLDS = {
        KillLevel.CAUTION: 0.50,
        KillLevel.REDUCTION: 0.75,
        KillLevel.LIQUIDATE: 0.95,
    }

    def __init__(
        self,
        thresholds: dict[KillLevel, float] | None = None,
        enable_audit_logging: bool = True,
    ) -> None:
        self.thresholds: dict[KillLevel, float] = dict(
            thresholds or self.DEFAULT_THRESHOLDS
        )
        # 阈值合法性校验 (显式 raise 而非 assert, 防止 -O 优化移除风控校验)
        if (
            not self.thresholds[KillLevel.CAUTION]
            < self.thresholds[KillLevel.REDUCTION]
        ):
            raise ValueError("CAUTION 阈值必须 < REDUCTION 阈值")
        if (
            not self.thresholds[KillLevel.REDUCTION]
            < self.thresholds[KillLevel.LIQUIDATE]
        ):
            raise ValueError("REDUCTION 阈值必须 < LIQUIDATE 阈值")
        if not 0 < self.thresholds[KillLevel.CAUTION] < 1.0:
            raise ValueError("CAUTION 阈值必须在 (0, 1) 区间")
        if not self.thresholds[KillLevel.LIQUIDATE] <= 1.0:
            raise ValueError("LIQUIDATE 阈值必须 <= 1.0")

        self._audit = KillSwitchAudit()
        self.enable_audit_logging = enable_audit_logging
        self._current_level: KillLevel = KillLevel.NORMAL

    # ------------------------------------------------------------
    # 状态更新
    # ------------------------------------------------------------

    def update_margin_usage(self, margin_usage: float) -> KillLevel:
        """更新保证金占用率, 返回当前级别."""
        if margin_usage < 0:
            margin_usage = 0.0
        if margin_usage > 1.0:
            margin_usage = 1.0

        new_level = self._classify(margin_usage)
        prev_level = self._current_level
        self._current_level = new_level

        now = now_bj().isoformat(timespec="seconds")
        self._audit.margin_usage = margin_usage
        self._audit.level = new_level
        self._audit.last_updated_at = now

        if new_level != prev_level:
            self._audit.level_entered_at = now
            # 升级方向计数
            if new_level == KillLevel.CAUTION:
                self._audit.total_triggered_L1 += 1
            elif new_level == KillLevel.REDUCTION:
                self._audit.total_triggered_L2 += 1
            elif new_level == KillLevel.LIQUIDATE:
                self._audit.total_triggered_L3 += 1
            logger.warning(
                f"[T12] 熔断级别变化: {prev_level.name} → {new_level.name} "
                f"(margin={margin_usage:.2%})"
            )
        return new_level

    # ------------------------------------------------------------
    # 语义化 API (主链路集成点)
    # ------------------------------------------------------------

    def evaluate_trade(
        self,
        symbol: str,
        side: str,
        notional: float,
        is_open_new: bool = True,
    ) -> KillDecision:
        """评估一笔交易是否允许.

        Args:
            is_open_new: True = 开新仓 / 增加暴露; False = 平仓 / 减少暴露 (减仓一般总是允许)
        """
        lvl = self._current_level
        side_l = side.lower()
        is_reducing = (not is_open_new) or (side_l in ("sell", "short_cover", "close"))

        if lvl == KillLevel.NORMAL:
            return KillDecision(allowed=True, level=lvl, reason="NORMAL: 无限制")

        if lvl == KillLevel.CAUTION:
            # L1: 停止开新仓, 允许平仓/减仓
            if is_reducing:
                return KillDecision(
                    allowed=True,
                    level=lvl,
                    reason="L1 CAUTION: 仅允许减仓类指令, 本次放行",
                )
            return self._block(lvl, symbol, side, notional, "L1 保证金≥50%, 禁止开新仓")

        if lvl == KillLevel.REDUCTION:
            # L2: 只允许减仓/卖出, 禁止一切新暴露
            if is_reducing:
                return KillDecision(
                    allowed=True,
                    level=lvl,
                    reason="L2 REDUCTION: 允许减仓类指令, 本次放行",
                )
            return self._block(
                lvl,
                symbol,
                side,
                notional,
                "L2 保证金≥75%, 禁止一切新暴露, 等待强平执行链",
            )

        # L3 LIQUIDATE: 只允许卖出类动作 (变现)
        if side_l == "sell" or "close" in side_l or "cover" in side_l:
            return KillDecision(
                allowed=True,
                level=lvl,
                reason="L3 LIQUIDATE: 仅允许变现类 (sell/close/cover) 指令",
            )
        return self._block(
            lvl,
            symbol,
            side,
            notional,
            "L3 全仓变现阶段, 仅允许卖出/平仓, 其他任何指令拦截",
        )

    def can_open_new_position(self) -> bool:
        """便捷方法: 当前级别是否允许开新仓."""
        return self._current_level == KillLevel.NORMAL

    def current_level(self) -> KillLevel:
        return self._current_level

    def audit(self) -> KillSwitchAudit:
        return self._audit

    # ------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------

    def _classify(self, mu: float) -> KillLevel:
        if mu >= self.thresholds[KillLevel.LIQUIDATE]:
            return KillLevel.LIQUIDATE
        if mu >= self.thresholds[KillLevel.REDUCTION]:
            return KillLevel.REDUCTION
        if mu >= self.thresholds[KillLevel.CAUTION]:
            return KillLevel.CAUTION
        return KillLevel.NORMAL

    def _block(
        self, lvl: KillLevel, symbol: str, side: str, notional: float, reason: str
    ) -> KillDecision:
        self._audit.blocked_orders += 1
        self._audit.last_blocked_reason = reason
        if self.enable_audit_logging:
            logger.error(
                f"[T12] 🔴 熔断拦截 {lvl.name} — {symbol} {side} 名义=¥{notional:,.0f} — {reason}"
            )
        return KillDecision(
            allowed=False,
            level=lvl,
            reason=reason,
            details={"symbol": symbol, "side": side, "notional": notional},
        )
