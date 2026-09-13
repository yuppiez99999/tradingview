"""净值回撤型 L0-L4 熔断器（v9.5 缺口②）

与保证金型 ``KillSwitch``（L1/L2/L3 = 50%/75%/95% 保证金占用）**并存**；
本模块按**净值回撤**分级，输出 L0-L4 等级 + 权益上限 + 保护比例，
被 ``ComboRiskManager``（``drawdown_level`` 状态字段）和 v9.5 配置消费。

阈值口径（v9.5 §五 + 配置 ``cns_thresholds``）::

    L0  dd >  -6%   正常      equity_cap=45%/55%  allow_new=True   protection=0.0
    L1  dd ≤ -6%   警戒      equity_cap=35%      allow_new=True   protection=0.2
    L2  dd ≤ -10%  减仓      equity_cap=25%      allow_new=False  protection=0.4
    L3  dd ≤ -14%  强制对冲   equity_cap=15%      allow_new=False  protection=0.7
    L4  dd ≤ -18%  停止      equity_cap=0%       allow_new=False  protection=1.0

定位（接入方案 §5.7 F1 路径 C + §四缺口②）
-----------------------------------------------
- **可进生产**（R-4 Change Budget「风险与性能优化」例外第 3 类）
- 批一进场前必须完成
- 与 ``DrawdownCircuitBreaker``（NORMAL/WATCH/REDUCE/FORCE_HEDGE/HALT）可互转
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "CnsLevel",
    "CnsDecision",
    "NavDrawdownBreaker",
    "to_drawdown_level",
]

_LEVELS: tuple[str, ...] = ("L0", "L1", "L2", "L3", "L4")
_LEVEL_RANK: dict[str, int] = {lvl: i for i, lvl in enumerate(_LEVELS)}

_LEVEL_MAP: dict[str, str] = {
    "L0": "NORMAL",
    "L1": "WATCH",
    "L2": "REDUCE",
    "L3": "FORCE_HEDGE",
    "L4": "HALT",
}


@dataclass(frozen=True)
class CnsLevel:
    """单级别定义。drawdown_threshold = 进入该级别的回撤阈值（负值）。"""

    name: str
    drawdown_threshold: float
    equity_cap_pct: float
    allow_new_buy: bool
    allow_open: bool
    protection_ratio: float
    action: str


_DEFAULT_LEVELS: tuple[CnsLevel, ...] = (
    CnsLevel("L0", 0.0, 0.45, True, True, 0.0, "正常"),
    CnsLevel("L1", -0.06, 0.35, True, True, 0.2, "警戒：不再投入权益"),
    CnsLevel("L2", -0.10, 0.25, False, False, 0.4, "减仓：缩至目标权重 50%"),
    CnsLevel("L3", -0.14, 0.15, False, False, 0.7, "强制对冲：尾部保护"),
    CnsLevel("L4", -0.18, 0.0, False, False, 1.0, "停止：去风险（清杠杆）"),
)


@dataclass(frozen=True)
class CnsDecision:
    """熔断判定结果。"""

    level: str
    rank: int
    drawdown: float
    equity_cap_pct: float
    allow_new_buy: bool
    allow_open: bool
    protection_ratio: float
    action: str
    breach_hard_limit: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "rank": self.rank,
            "drawdown": round(self.drawdown, 6),
            "equity_cap_pct": self.equity_cap_pct,
            "allow_new_buy": self.allow_new_buy,
            "allow_open": self.allow_open,
            "protection_ratio": self.protection_ratio,
            "action": self.action,
            "breach_hard_limit": self.breach_hard_limit,
        }


def to_drawdown_level(cns: str) -> str:
    """CnsDecision.level → DrawdownCircuitBreaker 级别名（互转桥接）。"""
    return _LEVEL_MAP.get(cns, "NORMAL")


class NavDrawdownBreaker:
    """净值回撤型 L0-L4 熔断器。

    Args:
        levels: 五级定义（默认 = v9.5 口径）；可覆盖用于回归/敏感性。
        post_acceptance_cap: 验收后权益上限（默认 0.55）。
    """

    def __init__(
        self,
        levels: tuple[CnsLevel, ...] | None = None,
        post_acceptance_cap: float = 0.55,
    ) -> None:
        self._levels = levels or _DEFAULT_LEVELS
        if len(self._levels) != 5:
            raise ValueError(f"需要 5 级定义, 收到 {len(self._levels)}")
        self._post_cap = float(post_acceptance_cap)
        thresholds = [lv.drawdown_threshold for lv in self._levels]
        for i in range(len(thresholds) - 1):
            if thresholds[i] <= thresholds[i + 1]:
                raise ValueError(
                    f"回撤阈值必须递减: {thresholds[i]} <= {thresholds[i + 1]}"
                )

    def evaluate(
        self,
        drawdown: float,
        acceptance_passed: bool = False,
    ) -> CnsDecision:
        """评估净值回撤，返回熔断决策。

        Args:
            drawdown: 净值回撤（负值，如 -0.08 = 回撤 8%）。
            acceptance_passed: §八验收是否通过（True → L0 用 post_acceptance_cap）。
        """
        dd = float(drawdown)
        if dd > 0:
            dd = -dd

        breach = dd <= self._levels[-1].drawdown_threshold

        for lv in reversed(self._levels[1:]):
            if dd <= lv.drawdown_threshold:
                return CnsDecision(
                    level=lv.name,
                    rank=_LEVEL_RANK[lv.name],
                    drawdown=dd,
                    equity_cap_pct=lv.equity_cap_pct,
                    allow_new_buy=lv.allow_new_buy,
                    allow_open=lv.allow_open,
                    protection_ratio=lv.protection_ratio,
                    action=lv.action,
                    breach_hard_limit=breach,
                )

        lv0 = self._levels[0]
        cap = self._post_cap if acceptance_passed else lv0.equity_cap_pct
        return CnsDecision(
            level=lv0.name,
            rank=0,
            drawdown=dd,
            equity_cap_pct=cap,
            allow_new_buy=lv0.allow_new_buy,
            allow_open=lv0.allow_open,
            protection_ratio=lv0.protection_ratio,
            action=lv0.action,
            breach_hard_limit=False,
        )

    def equity_cap(self, level: str, acceptance_passed: bool = False) -> float:
        """按级别返回权益上限（供 ComboRiskManager 消费）。"""
        rank = _LEVEL_RANK.get(level, 0)
        lv = self._levels[rank]
        if rank == 0 and acceptance_passed:
            return self._post_cap
        return lv.equity_cap_pct
