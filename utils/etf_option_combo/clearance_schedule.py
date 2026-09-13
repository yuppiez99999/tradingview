"""2030-Q4 清仓日历 shadow 只读渲染器（v9.5 缺口④）

清仓阶梯（v9.5 §五 5.1 总排期 + R-12 ⑤）::

    2030-10-01  阶梯一  权益上限降至 30%
    2030-11-15  阶梯二  权益上限降至 15%
    2030-12-15  阶梯三  强制清算（权益 → 0%, 硬约束）

定位（接入方案 §四缺口④ + §5.2）
-----------------------------------------------
- shadow 实现（R-4 冻结窗内仅 shadow，不触生产）
- 2030 条款不入 ROADMAP（R-12 ⑤：保留在计划书）
- 与 batch_state_machine 末期收官段衔接（final_stage_start = 2029Q4）
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

__all__ = ["ClearanceSchedule", "ClearanceDecision", "evaluate_clearance"]

_SCHEMA_VERSION = "1.0"

_STEPS: tuple[tuple[str, date, float, bool], ...] = (
    ("阶梯一", date(2030, 10, 1), 0.30, False),
    ("阶梯二", date(2030, 11, 15), 0.15, False),
    ("阶梯三", date(2030, 12, 15), 0.0, True),
)


@dataclass(frozen=True)
class ClearanceSchedule:
    """清仓日历参数（默认 = v9.5 口径）。"""

    step1_date: str = "2030-10-01"
    step1_equity_cap: float = 0.30
    step2_date: str = "2030-11-15"
    step2_equity_cap: float = 0.15
    step3_date: str = "2030-12-15"
    step3_equity_cap: float = 0.0
    final_stage_start: str = "2029-10-01"


@dataclass(frozen=True)
class ClearanceDecision:
    """清仓判定结果。"""

    as_of: str
    active: bool
    step: str | None
    equity_cap_pct: float
    force_liquidation: bool
    days_to_next_step: int | None
    next_step_date: str | None
    note: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse(s: str) -> date:
    return date.fromisoformat(s.strip())


def evaluate_clearance(
    as_of: str,
    schedule: ClearanceSchedule | None = None,
    current_equity_pct: float | None = None,
) -> ClearanceDecision:
    """评估清仓日历状态（只读、确定性）。

    Args:
        as_of: 渲染日期 YYYY-MM-DD。
        schedule: 清仓日历参数（默认 v9.5 口径）。
        current_equity_pct: 当前权益占比（0-1），用于超限检测（None = 不检测）。
    """
    sch = schedule or ClearanceSchedule()
    work = _parse(as_of)

    steps = [
        ("阶梯一", _parse(sch.step1_date), sch.step1_equity_cap, False),
        ("阶梯二", _parse(sch.step2_date), sch.step2_equity_cap, False),
        ("阶梯三", _parse(sch.step3_date), sch.step3_equity_cap, True),
    ]

    active_step: str | None = None
    cap = 1.0
    force = False

    for name, step_date, step_cap, step_force in steps:
        if work >= step_date:
            active_step = name
            cap = step_cap
            force = step_force

    active = active_step is not None

    next_step: str | None = None
    days_to_next: int | None = None
    for _name, step_date, _, _ in steps:
        if work < step_date:
            next_step = step_date.isoformat()
            days_to_next = (step_date - work).days
            break

    notes: list[str] = []
    if not active:
        notes.append(f"清仓日历未激活（首步 {sch.step1_date}）")
    elif force:
        notes.append("强制清算硬约束生效：权益 → 0%，全部清仓")
    else:
        notes.append(f"清仓{active_step}：权益上限降至 {cap:.0%}")

    if current_equity_pct is not None and active and current_equity_pct > cap + 1e-9:
        notes.append(
            f"超限告警：当前权益 {current_equity_pct:.1%} > 上限 {cap:.0%}，需减仓"
        )

    return ClearanceDecision(
        as_of=as_of,
        active=active,
        step=active_step,
        equity_cap_pct=cap,
        force_liquidation=force,
        days_to_next_step=days_to_next,
        next_step_date=next_step,
        note="；".join(notes),
    )
