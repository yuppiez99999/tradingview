"""A 股 T+1 可卖约束 (P0-H1, 2026-09-13)

规则
----
A 股 T+1: 当日买入的股票, 次一交易日方可卖出。此前系统的 SELL 生成/校验点
(止损自动平仓 / 再平衡 / 通用执行器) 全部只看 ``positions.shares`` 总量,
不区分当日买入部分 — 模拟环境照常成交, 实盘会被券商拒单, 账实立即分叉。

口径
----
    frozen(当日买入冻结) = FillsStore 当日 BUY 成交合计 (全部策略:
    build/rebalance/hedge — T+1 是交易所规则, 不分来源)
    available = positions.shares - frozen (下限 0)

接线点 (SELL 生成/校验):
    1. ``executor/stop_loss_liquidation._build_one_instruction`` — 平仓数量
       截断到可用部分; 全冻结记 skipped (不静默);
    2. ``rebalance_execution_orders.validate_order`` — SELL 超可用 → error 拒单;
    3. ``daily_trade_executor._execute_single_instruction`` — 执行前最终闸,
       截断并显式告警 (防上游校验缺失后账实分叉)。

fail-open 口径
--------------
FillsStore 读取失败 → frozen=0 (回退"全量可卖"旧行为) + WARNING 落日志。
取舍: 拒绝卖出可能错过止损 (风险更大), 且实盘 broker 对 T+1 违约会硬拒单;
模拟环境选择放行 + 告警, 让账实差异显式可见。
"""

from __future__ import annotations

import logging
from typing import Any

from utils.datetime_utils import now_bj

logger = logging.getLogger(__name__)

# A 股最小交易单位 (可用数量向下取整到整手)
LOT_SIZE = 100


def get_t1_frozen_qty(
    symbol: str,
    date: str | None = None,
) -> int:
    """当日买入冻结股数 (FillsStore 当日 BUY 成交合计)。

    Args:
        symbol: 标的 (6 位代码或带后缀; 内部按数字前缀匹配成交记录)
        date: 交易日 YYYY-MM-DD, 缺省北京时间今天

    Returns:
        冻结股数; FillsStore 不可用/读取失败时 0 (fail-open + WARNING)
    """
    rec_date = date or now_bj().strftime("%Y-%m-%d")
    sym_num = str(symbol).strip().split(".")[0]
    try:
        from utils.execution.fills_store import FillsStore

        records = FillsStore().load_day(rec_date)
    except Exception as e:  # noqa: BLE001 — fail-open: 回退全量可卖, 告警留痕
        logger.warning("[T1] FillsStore 读取失败, 当日买入冻结=0 (fail-open): %s", e)
        return 0

    frozen = 0.0
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        if str(rec.get("side", "")).upper() != "BUY":
            continue
        rec_sym = str(rec.get("symbol", "")).strip().split(".")[0]
        if rec_sym != sym_num:
            continue
        try:
            frozen += float(rec.get("filled_qty", 0) or 0)
        except (TypeError, ValueError):
            continue
    return int(frozen)


def compute_available_qty(
    symbol: str,
    held_shares: float,
    date: str | None = None,
) -> tuple[int, int]:
    """T+1 可卖数量 = max(0, 持仓 - 当日买入冻结)。

    Returns:
        ``(available, frozen)`` — 均为整数股
    """
    frozen = get_t1_frozen_qty(symbol, date)
    available = max(0, int(float(held_shares or 0)) - frozen)
    if frozen > 0:
        logger.info("[T1] %s: 持仓 %s, 当日买入冻结 %d → 可卖 %d", symbol, held_shares, frozen, available)
    return available, frozen


def floor_to_lot(qty: int) -> int:
    """向下取整到整手 (A 股最小交易单位)。"""
    return max(0, int(qty) // LOT_SIZE) * LOT_SIZE


def clamp_sell_quantity(
    symbol: str,
    held_shares: float,
    requested_qty: float,
    *,
    date: str | None = None,
    lot_aligned: bool = True,
) -> tuple[int, int, int]:
    """把卖出请求数量截断到 T+1 可卖范围。

    Args:
        symbol: 标的
        held_shares: 持仓股数
        requested_qty: 请求卖出股数
        date: 交易日 (缺省今天)
        lot_aligned: 结果是否取整到整手 (平仓/再平衡指令需要; 最终闸不截)

    Returns:
        ``(clamped_qty, available, frozen)`` — clamped_qty 为最终允许卖出数量
    """
    available, frozen = compute_available_qty(symbol, held_shares, date)
    clamped = min(int(requested_qty or 0), available)
    if lot_aligned:
        clamped = floor_to_lot(clamped)
    return clamped, available, frozen


def clamp_instruction_sell_qty(
    inst: dict[str, Any],
    positions: dict[str, Any],
) -> tuple[int, str | None]:
    """执行器最终闸 (P0-H1): SELL 指令数量截断到 T+1 可卖范围 (fail-open)。

    上游 (平仓口径 1 / 再平衡 validate_order) 已各自校验, 此处兜底防账实分叉。
    任何异常回退原始数量 (放行) 并在返回值中带告警文案。

    Args:
        inst: 指令 (读 full_code/qty)
        positions: 持仓 dict (读 full_code → shares)

    Returns:
        ``(clamped_qty, note)`` — note 非 None 表示发生截断/异常 (供调用方落日志)
    """
    code = str(inst.get("full_code", ""))
    qty = int(inst.get("qty", 0) or 0)
    try:
        held = float((positions.get(code) or {}).get("shares", 0) or 0)
        clamped, available, frozen = clamp_sell_quantity(code, held, qty, lot_aligned=False)
        if clamped < qty:
            return clamped, (
                f"卖出 {qty} 截断为 {clamped} (持仓 {held:.0f}, 当日买入冻结 {frozen}, 可卖 {available})"
            )
        return qty, None
    except Exception as e:  # noqa: BLE001 — 校验失败放行原始数量, 告警留痕
        return qty, f"可卖校验异常, 放行原始数量 (fail-open): {e}"


__all__ = [
    "LOT_SIZE",
    "clamp_instruction_sell_qty",
    "clamp_sell_quantity",
    "compute_available_qty",
    "floor_to_lot",
    "get_t1_frozen_qty",
]
