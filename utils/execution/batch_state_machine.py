"""建仓批次状态机 — v9.5 shadow 只读渲染器

来源与口径
----------
《三百万ETF期权五年方略 v9.5》§五「建仓阶段×资产分配矩阵」（批一/批二/批三/
恢复·回补/末期收官）+ v9.4「单边上涨对向条款 / 时间窗与失效规则」；
对齐 config/etf_option_combo_v95.yaml batch_plan 段。

定位（接入方案 §5.7 F1 路径 C）
-------------------------------
本模块是「只读渲染器」：仅被 shadow 入口 scripts/shadow_batch_plan.py 调用，产出
reports/shadow/batch_plan_{date}.json；不接入生产交易决策路径，不被生产入口 import
（机读验收见 tests/unit 下对应单测与提交说明）。模块自身不写文件、不做数据源访问。

输入契约
--------
调用方提供已计算的「组合状态 + 市场信号」标量（见 BatchInputs）。
「下跌 X%」基准 = 自上批投入日收盘回撤 与 日内累计回撤 孰高；估值分位 =
沪深300 PE_TTM 过去 8 年分位。

已登记口径事项（在输出 notes 透出，渲染不擅自改口径）
-----------------------------------------------------
- 批二矩阵加总 90→150 万（权益 50%）与注记「55%（满档）」差 15 万（Stage A 发现②）。
- 时间窗按正文「自批一投入日起 12 个月」；v9.4 修订摘要「连续 30 个交易日」为不一致
  表述（Stage A 发现③），窗口口径已参数化。
- 预验收 45% 权益上限（硬约束④）：只做超限校核标记，不自动裁剪（拆分细则待复核）。
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["BatchInputs", "BatchPolicy", "render_batch_plan"]

SCHEMA_VERSION = "1.0"

_ASSET_NAMES: dict[str, str] = {
    "510300": "沪深300ETF",
    "515080": "中证红利ETF",
    "510500": "中证500ETF",
    "satellite": "卫星轮动（ETF Score 池）",
    "treasury_gold": "国债+黄金",
    "cash": "现金层（货币基金）",
}

# 批一矩阵（万元）：300 / 红利 / 500 / 卫星 / 国债+黄金（30+30）
_BATCH1_ADD_WAN: dict[str, float] = {
    "510300": 45.0,
    "515080": 30.0,
    "510500": 0.0,
    "satellite": 15.0,
    "treasury_gold": 60.0,
}

# 批二矩阵（万元，下跌路径与对向条款共用同一 60 万弹药）
_BATCH2_ADD_WAN: dict[str, float] = {
    "510300": 30.0,
    "515080": 15.0,
    "510500": 15.0,
    "treasury_gold": 15.0,
}

# 对向条款三等分切片（渲染推断，原文未给切片表；待复核）
_OPPOSITE_SLICE_WAN: dict[str, float] = {
    "510300": 10.0,
    "515080": 5.0,
    "510500": 5.0,
}

_LEVEL_RANK: dict[str, int] = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4}

_NOTE_RECONCILIATION = (
    "批二矩阵加总 90→150 万（权益 50%）与注记「55%（满档）」差 15 万："
    "已登记待复核（Stage A 发现②），本渲染按矩阵金额并如实对账。"
)
_NOTE_WINDOW = (
    "时间窗口径：按正文「自批一投入日起 12 个月」实现；v9.4 修订摘要「连续 30 个交易日」"
    "为不一致表述（Stage A 发现③），窗口口径已参数化。"
)
_NOTE_INFER = "执行细节留白（对向切片三等分、批三现金项、回补批间隔）为渲染推断，待复核。"
_NOTE_DISCLAIMER = "shadow 只读渲染输出；非交易信号，不接入生产链路。"


@dataclass(frozen=True)
class BatchPolicy:
    """口径参数（默认值 = v9.5 正文口径；可覆盖用于回归/敏感性）。"""

    pullback_low: float = 0.05            # 批二下跌触发下沿（自上批投入日）
    pullback_high: float = 0.08           # 批二下跌触发上沿
    pe_pct_down_max: float = 0.55         # 下跌路径 PE 分位上限
    pe_pct_opposite_max: float = 0.65     # 对向路径 PE 分位上限
    ma_slope_min: float = 0.0             # 20 日斜率非负（走平/上拐）
    window_months: int = 12               # 批二时间窗（自批一投入日）
    opposite_tranches: int = 3            # 对向分 3 批
    opposite_tranche_cap_wan: float = 20.0
    opposite_min_gap_bdays: int = 5       # 间隔 ≥5 个交易日（工作日近似）
    reduce_trigger_dd: float = 0.06       # 批三：组合回撤 -6% 转 L1
    recovery_ratio: float = 0.50          # 回补：自低位修复 50%
    recovery_tranches: int = 3
    recovery_tranche_cap_wan: float = 12.0
    equity_cap_pre_acceptance: float = 0.45
    equity_cap_post_acceptance: float = 0.55
    final_stage_start: str = "2029-10-01"  # 末期收官：2029Q4 起
    final_stage_cap_wan: float = 30.0


@dataclass(frozen=True)
class BatchInputs:
    """调用方提供的当日输入（全部为已计算标量；None = 未知，渲染保守处理）。"""

    as_of: str                              # 渲染日期 YYYY-MM-DD（必填）
    batch1_done: bool = False
    b1_entry_date: str | None = None        # 批一投入日
    batch2_done: bool = False
    acceptance_passed: bool = False         # §八 验收清单是否全勾（45%/55% 上限）
    cns_level: str = "L0"                   # 熔断等级 L0..L4
    portfolio_dd_pct: float | None = None   # 组合回撤（负值，如 -0.06）
    dd_since_last_invest_pct: float | None = None   # 自上批投入日收盘回撤（负值）
    intraday_cum_dd_pct: float | None = None        # 日内累计回撤（负值）
    pe_pct_8y: float | None = None          # 沪深300 PE_TTM 8 年分位（0-1）
    close_above_ma250: bool | None = None   # 对向①：收盘价站上 250 日均线
    ma250_slope_20d: float | None = None    # 对向②：20 日斜率（非负 = 走平/上拐）
    low_point_date: str | None = None       # 回补：低点日期（参考字段）
    rebound_from_low_pct: float | None = None  # 回补：自低位反弹幅度（0-1）
    opposite_tranches_done: int = 0         # 对向已投批数（0-3）
    opposite_last_tranche_date: str | None = None
    recovery_tranches_done: int = 0         # 回补已投批数（0-3）


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - timedelta(days=1)).day


def _add_months(d: date, months: int) -> date:
    y, m = d.year, d.month + months
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return date(y, m, min(d.day, _days_in_month(y, m)))


def _weekdays_between(start: date, end: date) -> int:
    """(start, end] 区间内的工作日数（未含法定节假日日历，shadow 近似口径）。"""
    if end <= start:
        return 0
    n = 0
    d = start + timedelta(days=1)
    while d <= end:
        if d.weekday() < 5:
            n += 1
        d += timedelta(days=1)
    return n


def _effective_dd(*values: float | None) -> float | None:
    """下跌基准：多来源回撤取孰高（更深者）；返回负值。"""
    depths = [abs(float(v)) for v in values if v is not None]
    if not depths:
        return None
    return -max(depths)


def _level_rank(level: Any) -> int | None:
    if not isinstance(level, str):
        return None
    return _LEVEL_RANK.get(level.strip().upper())


def _action(
    stage: str,
    asset: str,
    amount_wan: float | None,
    note: str,
    action: str = "buy",
) -> dict[str, Any]:
    return {
        "stage": stage,
        "asset": asset,
        "name": _ASSET_NAMES.get(asset, asset),
        "action": action,
        "amount_wan": amount_wan,
        "note": note,
    }


def _reduce_actions() -> list[dict[str, Any]]:
    """批三·收缩动作（矩阵原文；现金项细则待复核）。"""
    return [
        _action("批三·收缩", "satellite", None, "卫星清仓（回撤 -6% 转 L1，不再投入权益）", action="liquidate"),
        _action("批三·收缩", "treasury_gold", 10.0, "国债+黄金 +10 万", action="buy"),
        _action("批三·收缩", "cash", -30.0, "现金变动 -30 万（矩阵原文口径，细则待复核）", action="adjust"),
    ]


def render_batch_plan(inp: BatchInputs, policy: BatchPolicy | None = None) -> dict[str, Any]:
    """渲染当日批次计划（只读、确定性；返回 JSON-ready dict）。"""
    pol = policy or BatchPolicy()
    warnings: list[str] = []
    notes: list[str] = [_NOTE_DISCLAIMER]

    work_date = _parse_date(inp.as_of)
    if work_date is None:
        raise ValueError("BatchInputs.as_of 必须为 YYYY-MM-DD")

    rank = _level_rank(inp.cns_level)
    level_unknown = rank is None
    if level_unknown:
        warnings.append("熔断等级未知 —— 已按「保守阻断新增权益」处理，请复核输入。")

    portfolio_dd = inp.portfolio_dd_pct
    reduce_mode = bool(
        (rank is not None and rank >= 1)
        or (portfolio_dd is not None and portfolio_dd <= -pol.reduce_trigger_dd)
    )

    state: dict[str, Any] = {
        "batch1": "done" if inp.batch1_done else "not_started",
        "batch2": "not_applicable",
        "reduce_mode": reduce_mode,
        "recovery": None,
        "final_stage": False,
        "acceptance_passed": bool(inp.acceptance_passed),
    }

    b1_date = _parse_date(inp.b1_entry_date)
    if inp.batch1_done and b1_date is None:
        warnings.append("批一已完成但投入日缺失：时间窗无法判定（按未过期处理），请复核输入。")

    window: dict[str, Any] = {
        "months": pol.window_months,
        "start": None,
        "end": None,
        "active": None,
        "remaining_days": None,
    }
    if b1_date is not None:
        w_end = _add_months(b1_date, pol.window_months)
        window["start"] = b1_date.isoformat()
        window["end"] = w_end.isoformat()
        window["active"] = work_date <= w_end
        window["remaining_days"] = max(0, (w_end - work_date).days)

    # ---- 批二双路径判定（数据契约化为标量输入） ----
    pullback: dict[str, Any] = {"met": False, "blocked_by": None, "conditions": {}}
    opposite: dict[str, Any] = {
        "met": False,
        "conditions": {},
        "tranches": {"done": 0, "total": pol.opposite_tranches, "next_eligible": False},
    }

    eff_dd = _effective_dd(inp.dd_since_last_invest_pct, inp.intraday_cum_dd_pct)
    pe = inp.pe_pct_8y
    pullback["conditions"].update(
        {
            "dd_effective": eff_dd,
            "dd_band": [-pol.pullback_high, -pol.pullback_low],
            "pe_pct": pe,
            "pe_max": pol.pe_pct_down_max,
            "data_ok": eff_dd is not None and pe is not None,
        }
    )
    if eff_dd is not None and pe is not None:
        depth = abs(eff_dd)
        in_band = (pol.pullback_low - 1e-9) <= depth <= (pol.pullback_high + 1e-9)
        pe_ok = pe < pol.pe_pct_down_max
        pullback["met"] = in_band and pe_ok
        pullback["conditions"].update({"in_band": in_band, "pe_ok": pe_ok})
    else:
        pullback["blocked_by"] = "insufficient_inputs"

    slope = inp.ma250_slope_20d
    op_conditions = {
        "above_ma250": inp.close_above_ma250,
        "ma250_slope_20d": slope,
        "slope_ok": slope is not None and slope >= pol.ma_slope_min,
        "pe_pct": pe,
        "pe_max": pol.pe_pct_opposite_max,
        "pe_ok": pe is not None and pe < pol.pe_pct_opposite_max,
        "window_active": window["active"],
        "data_ok": (inp.close_above_ma250 is not None and slope is not None and pe is not None),
    }
    opposite["conditions"].update(op_conditions)
    if (
        op_conditions["data_ok"]
        and inp.close_above_ma250 is True
        and op_conditions["slope_ok"] is True
        and op_conditions["pe_ok"] is True
        and window["active"] is True
    ):
        opposite["met"] = True

    if opposite["met"]:
        done = max(0, min(int(inp.opposite_tranches_done), pol.opposite_tranches))
        eligible = done < pol.opposite_tranches
        if eligible and done > 0:
            last = _parse_date(inp.opposite_last_tranche_date)
            if last is None:
                eligible = False
                opposite["conditions"]["gap_weekdays"] = None
            else:
                gap = _weekdays_between(last, work_date)
                opposite["conditions"]["gap_weekdays"] = gap
                eligible = gap >= pol.opposite_min_gap_bdays
        opposite["tranches"] = {
            "done": done,
            "total": pol.opposite_tranches,
            "next_eligible": eligible,
        }

    # ---- 动作装配（风控优先序：reduce > 窗口失效 > 下跌 > 对向 > 等待） ----
    actions: list[dict[str, Any]] = []
    projection: dict[str, Any] | None = None

    if not inp.batch1_done:
        state["batch2"] = "not_applicable"
        for asset, amt in _BATCH1_ADD_WAN.items():
            if amt > 0:
                actions.append(_action("批一·基础", asset, amt, "T+0 开仓立配"))
        projection = {"cum_equity_pct": 30.0, "cum_equity_pct_labeled": 30.0, "note": None}
        notes.append("批一 T+0 开仓立配（阶段矩阵：累计权益 30%）。")
    else:
        notes.append(_NOTE_WINDOW)
        if inp.batch2_done:
            state["batch2"] = "done"
            notes.append("批二已完成（按调用方进度回填）；后续为批三/回补/收官状态机校验。")
        elif reduce_mode:
            state["batch2"] = "blocked_reduce"
            pullback["blocked_by"] = "risk_level"
            actions.extend(_reduce_actions())
            notes.append("风控优先：组合回撤触达 L1（-6%）→ 批三·收缩状态：不再投入权益。")
        elif level_unknown:
            state["batch2"] = "blocked_level_unknown"
            pullback["blocked_by"] = "level_unknown"
            notes.append("熔断等级未知：保守阻断新增权益（请补全 cns_level 输入后重渲）。")
        elif window["active"] is False:
            state["batch2"] = "window_expired"
            notes.append(
                "批二时间窗已满且未触发：剩余权益弹药留存现金层计息、不强制追高、转年度复盘重议；"
                "批三·收缩与恢复·回补不受本时间窗约束。"
            )
        elif pullback["met"]:
            state["batch2"] = "triggered_down"
            for asset, amt in _BATCH2_ADD_WAN.items():
                actions.append(
                    _action("批二·加码", asset, amt, "下跌触发：自上批投入日回撤 5%-8% 且 PE 分位 <55%")
                )
            projection = {"cum_equity_pct": 50.0, "cum_equity_pct_labeled": 55.0, "note": _NOTE_RECONCILIATION}
            notes.append(_NOTE_RECONCILIATION)
        elif opposite["met"]:
            state["batch2"] = "triggered_opposite"
            done = opposite["tranches"]["done"]
            if done >= pol.opposite_tranches:
                state["batch2"] = "done"
                notes.append("对向条款 3 批已投满，批二完成。")
            elif opposite["tranches"]["next_eligible"]:
                for asset, amt in _OPPOSITE_SLICE_WAN.items():
                    actions.append(
                        _action(
                            "批二·对向",
                            asset,
                            amt,
                            f"对向条款第 {done + 1}/{pol.opposite_tranches} 批（每批 ≤{pol.opposite_tranche_cap_wan:.0f} 万、"
                            f"间隔 ≥{pol.opposite_min_gap_bdays} 个交易日）",
                        )
                    )
                notes.append(_NOTE_INFER)
            else:
                gap = opposite["conditions"].get("gap_weekdays")
                notes.append(
                    f"对向条款已触发；第 {done + 1} 批间隔未满（已 {gap} 个工作日，需 ≥{pol.opposite_min_gap_bdays}）。"
                )
        else:
            state["batch2"] = "awaiting"
            if window["active"] is True:
                notes.append(
                    f"批二等待触发中（窗口剩余 {window['remaining_days']} 天）："
                    "下跌路径（自批一 -5%~-8% 且 PE<55%）或对向条款（站上 250 日线 + 斜率非负 + PE<65%）。"
                )

        # 恢复·回补（不受批二时间窗约束；要求熔断等级 ≤ L2）
        rebound = inp.rebound_from_low_pct
        rec_done = max(0, min(int(inp.recovery_tranches_done), pol.recovery_tranches))
        recovery: dict[str, Any] = {
            "eligible": False,
            "tranches_done": rec_done,
            "tranches_total": pol.recovery_tranches,
        }
        if (
            rebound is not None
            and rank is not None
            and rank <= _LEVEL_RANK["L2"]
            and rebound >= pol.recovery_ratio
            and rec_done < pol.recovery_tranches
        ):
            recovery["eligible"] = True
            actions.append(
                _action(
                    "恢复·回补",
                    "510300",
                    pol.recovery_tranche_cap_wan,
                    f"自低位修复 ≥50% 第 {rec_done + 1}/{pol.recovery_tranches} 批（每批 ≤{pol.recovery_tranche_cap_wan:.0f} 万）",
                )
            )
        elif rebound is not None and rebound >= pol.recovery_ratio and rank is not None and rank > _LEVEL_RANK["L2"]:
            recovery["blocked_by"] = "risk_level"
            notes.append("自低位修复已 ≥50%，但熔断等级 >L2：回补条件（≤L2）未满足，继续等待。")
        state["recovery"] = recovery

        # 末期收官（2029Q4 起）
        if work_date >= date.fromisoformat(pol.final_stage_start):
            state["final_stage"] = True
            notes.append(
                f"末期收官窗口（2029Q4 起）：科创/创业板可适度试仓 ≤{pol.final_stage_cap_wan:.0f} 万（累计权益 50% 温和口径）。"
            )

    # ---- 权益上限校核（硬约束④：预验收 45%，不因批二/对向突破） ----
    constraints: list[dict[str, Any]] = []
    cap = pol.equity_cap_post_acceptance if inp.acceptance_passed else pol.equity_cap_pre_acceptance
    if projection is not None and projection.get("cum_equity_pct") is not None:
        projected = float(projection["cum_equity_pct"])
        limit_pct = round(cap * 100.0, 2)
        item: dict[str, Any] = {
            "code": "EQUITY_CAP",
            "limit_pct": limit_pct,
            "projected_pct": projected,
            "status": "violation" if projected > limit_pct + 1e-9 else "ok",
        }
        if item["status"] == "violation":
            item["note"] = (
                "预验收 45% 权益上限『不因批二触发或对向条款而突破』（硬约束④）；"
                "执行拆分细则未定义 → 仅标记、不自动裁剪，待复核。"
            )
        constraints.append(item)

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "render_date": inp.as_of,
        "mode": "shadow",
        "engine": "建仓批次状态机（v9.5 §五 + v9.4 对向条款）",
        "state": state,
        "window": window,
        "triggers": {"pullback_path": pullback, "opposite_path": opposite},
        "proposed_actions": actions,
        "projection": projection,
        "constraints": constraints,
        "warnings": warnings,
        "notes": notes,
        "inputs_echo": asdict(inp),
        "disclaimer": _NOTE_DISCLAIMER,
    }
    logger.debug("rendered shadow batch plan: %s", state.get("batch2"))
    return report
