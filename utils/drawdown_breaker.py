"""
回撤分级熔断 (Drawdown Circuit Breaker)
=======================================

顶级对冲基金标准：回撤约束必须是硬控制，而非软预测。

此前系统的 RiskControl 仅检查保证金/价差/胖手指，从不检查组合层面回撤；
alpha 引擎虽有 max_drawdown_limit=0.15，但从未在运行时被调用。

本模块实现分级熔断：
- -5%   WATCH        监控并降低杠杆
- -8%   REDUCE       减仓至目标权重的 50%，禁止新建多头
- -12%  FORCE_HEDGE  强制买入尾部保护（股指 Put / 期货对冲）
- -15%  HALT         全面停止买入并启动去风险（清杠杆）

任一环节返回 allow_new_buy=False 即禁止新建多头，确保 15% 上限在运行期
可被强制守住。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DrawdownLevel(StrEnum):
    NORMAL = "NORMAL"
    WATCH = "WATCH"
    REDUCE = "REDUCE"
    FORCE_HEDGE = "FORCE_HEDGE"
    HALT = "HALT"


@dataclass
class DrawdownDecision:
    level: DrawdownLevel
    current_drawdown: float
    action: str
    allow_new_buy: bool
    breach_hard_limit: bool

    def to_dict(self) -> dict:
        return {
            "level": self.level.value,
            "current_drawdown": round(self.current_drawdown, 4),
            "action": self.action,
            "allow_new_buy": self.allow_new_buy,
            "breach_hard_limit": self.breach_hard_limit,
        }


class DrawdownCircuitBreaker:
    """回撤分级熔断控制器"""

    def __init__(
        self,
        max_drawdown: float = 0.15,
        reduce_threshold: float = -0.08,
        force_hedge_threshold: float = -0.12,
        watch_threshold: float = -0.05,
    ):
        # 统一归一化为负值，避免调用方正负号混淆
        self.max_drawdown = -abs(float(max_drawdown))
        self.reduce_threshold = -abs(float(reduce_threshold))
        self.force_hedge_threshold = -abs(float(force_hedge_threshold))
        self.watch_threshold = -abs(float(watch_threshold))

    def evaluate(self, current_drawdown: float) -> DrawdownDecision:
        """评估当前回撤（负数，如 -0.10 表示回撤 10%）

        Args:
            current_drawdown: 当前从高点回撤比例（小数，负值为回撤）
        Returns:
            DrawdownDecision
        """
        dd = float(current_drawdown)
        breach = dd <= self.max_drawdown

        if dd <= self.force_hedge_threshold:
            level = DrawdownLevel.HALT if breach else DrawdownLevel.FORCE_HEDGE
            action = (
                "全面停止买入并启动去风险（清杠杆）"
                if level == DrawdownLevel.HALT
                else "强制买入尾部保护（股指 Put / 期货对冲）"
            )
            return DrawdownDecision(level, dd, action, False, breach)

        if dd <= self.reduce_threshold:
            return DrawdownDecision(
                DrawdownLevel.REDUCE,
                dd,
                "减仓至目标权重的 50%，禁止新建多头",
                False,
                breach,
            )

        if dd <= self.watch_threshold:
            return DrawdownDecision(
                DrawdownLevel.WATCH,
                dd,
                "监控并降低杠杆",
                True,
                breach,
            )

        return DrawdownDecision(DrawdownLevel.NORMAL, dd, "正常交易", True, breach)

    def target_scale(self, current_drawdown: float) -> float:
        """返回当前允许的目标仓位缩放系数（1.0 = 满仓）。

        回撤越深，允许仓位越低，实现波动率目标化与回撤控制。
        自动修正: 若传入正值 (如 +0.10), 转为负值以避免静默误判。
        """
        dd = float(current_drawdown)
        if dd > 0:
            import logging

            logging.getLogger("drawdown_breaker").warning(
                f"target_scale 收到正数回撤 {dd:.4f}, 自动转为负值 (调用方符号可能错误)"
            )
            dd = -dd
        if dd <= self.reduce_threshold:
            return 0.5
        if dd <= self.watch_threshold:
            return 0.8
        return 1.0
