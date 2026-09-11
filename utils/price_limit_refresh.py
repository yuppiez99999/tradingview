"""
utils.price_limit_refresh — 涨跌停状态刷新与持久化 (S-2, Issue #13)
===================================================================

背景
----
L1 决策门 (``ai_decision.decision_gate.run_hard_risk``) 早已实现"涨停不可买 /
跌停不可卖"分支, 但 **主链从不给它喂数据**: ``RiskContext.is_limit_up`` 默认
False, CLI / ``orchestrator.run_decision`` 均不设置, 该保护形同虚设。

本模块把"标的当日涨跌停状态"落成可复用、可审计、可离线复现的一等公民:

- ``compute_price_limit_status(codes)``: 拉取实时快照, 用快照自带的
  ``limit_up``/``limit_down`` 字段 (东财 ``f168``/``f170``) 判定; 缺失时回退到
  ``utils.price_limit_calculator`` 按板块规则 + 前收盘价计算;
- 结果缓存落盘 ``reports/operations/price_limit_status.json``, 带
  ``as_of`` 时间戳与 ``as_of_date`` (交易日归属), 供无网络环境复用;
- ``load_price_limit_status()`` 返回 ``(status_map, stale)``:
  ``stale=True`` 表示缓存不是本交易日的 → 调用方应保守处理 (涨停连续多日,
  旧状态持久化会把"昨日涨停"当"今日涨停"持续 veto)。

设计原则
--------
1. **fail-open 不阻断**: 网络不可用/代码未识别 → 返回已有缓存 (标记 stale),
   绝不抛异常打断决策链 (由 ``RiskContext.price_limit_stale`` 的保守语义接手);
2. **口径单一**: 涨跌停比例规则只来自 ``utils.price_limit_calculator``, 不在此重写;
3. **可测**: ``compute_*`` 接受注入的 ``quotes``, 网络路径与纯计算路径可分开测试。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from utils.datetime_utils import now_bj
from utils.price_limit_calculator import (
    calc_limit_prices,
    get_board_type,
    is_at_limit_down,
    is_at_limit_up,
)

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STATUS_FILE = os.path.join(
    _PROJECT_ROOT, "reports", "operations", "price_limit_status.json"
)

# 状态取值 (与 ai_decision.decision_gate.RiskContext.is_limit_*_for 约定一致)
STATUS_LIMIT_UP = "limit_up"
STATUS_LIMIT_DOWN = "limit_down"
STATUS_NORMAL = "normal"
STATUS_UNKNOWN = "unknown"


def _is_st(code: str, st_codes: set[str] | None) -> bool:
    """判定是否 ST (影响涨跌停比例: ST 为 ±5%)。"""
    if not st_codes:
        return False
    pure = str(code).split(".")[0]
    return pure in st_codes or str(code) in st_codes


def _resolve_limits(
    code: str,
    quote: dict[str, Any],
    st_codes: set[str] | None,
) -> tuple[float, float]:
    """解析标的涨跌停价: 优先行情快照字段, 回退板块规则计算。"""
    limit_up = _to_float(quote.get("limit_up"))
    limit_down = _to_float(quote.get("limit_down"))
    if limit_up and limit_down:
        return limit_up, limit_down

    prev_close = _to_float(quote.get("pre_close"))
    if not prev_close:
        return 0.0, 0.0
    try:
        computed_up, computed_down = calc_limit_prices(
            prev_close=prev_close,
            code=code,
            is_st=_is_st(code, st_codes),
        )
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        logger.debug("[PriceLimit] %s 涨跌停价计算失败: %s", code, exc)
        return 0.0, 0.0
    return (
        limit_up if limit_up else computed_up,
        limit_down if limit_down else computed_down,
    )


def _to_float(value: Any) -> float:
    """宽松数值转换 (行情源可能给 None/str/Decimal)。"""
    if value is None or isinstance(value, bool):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def compute_price_limit_status(
    codes: list[str],
    quotes: dict[str, dict] | None = None,
    st_codes: set[str] | None = None,
    use_cache: bool = True,
) -> dict[str, str]:
    """计算各标的当日涨跌停状态。

    Args:
        codes: 标的代码列表 (支持 ``600519`` / ``600519.SH`` 两种写法)
        quotes: 行情快照 ``{code: {...}}``; None 时自动拉取实时行情。
            注入该参数可离线/可重复测试。
        st_codes: ST 代码集合 (影响 ±5% 口径); None 时不按 ST 处理
        use_cache: 拉取行情时是否使用 astock_realtime 的进程内缓存

    Returns:
        ``{code: "limit_up" | "limit_down" | "normal" | "unknown"}``
        (代码归一化为纯代码形式)
    """
    pure_codes = [str(c).split(".")[0] for c in codes if c]
    if not pure_codes:
        return {}

    if quotes is None:
        try:
            from utils.astock_realtime import get_realtime_quotes

            quotes = get_realtime_quotes(pure_codes, use_cache=use_cache)
        except (
            ImportError,
            ValueError,
            TypeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as exc:
            logger.warning("[PriceLimit] 实时行情不可用, 涨跌停状态未知: %s", exc)
            quotes = {}

    status: dict[str, str] = {}
    for code in pure_codes:
        quote = quotes.get(code) or quotes.get(f"{code}.SH") or quotes.get(f"{code}.SZ")
        if not quote:
            status[code] = STATUS_UNKNOWN
            continue
        price = _to_float(quote.get("price"))
        limit_up, limit_down = _resolve_limits(code, quote, st_codes)
        if price <= 0 or (limit_up <= 0 and limit_down <= 0):
            status[code] = STATUS_UNKNOWN
            continue
        if is_at_limit_up(price, limit_up):
            status[code] = STATUS_LIMIT_UP
        elif is_at_limit_down(price, limit_down):
            status[code] = STATUS_LIMIT_DOWN
        else:
            status[code] = STATUS_NORMAL
    return status


def save_price_limit_status(
    status: dict[str, str],
    details: dict[str, dict] | None = None,
    path: str | None = None,
) -> str:
    """持久化涨跌停状态 (含 as_of 时间戳与交易日归属)。

    Returns:
        写入的文件路径
    """
    target = path or _STATUS_FILE
    now = now_bj()
    payload = {
        "as_of": now.isoformat(),
        "as_of_date": now.strftime("%Y-%m-%d"),
        "status": status,
        "details": details or {},
    }
    os.makedirs(os.path.dirname(target), exist_ok=True)
    try:
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        logger.warning("[PriceLimit] 状态写入失败 (%s): %s", target, exc)
    return target


def load_price_limit_status(
    path: str | None = None,
    trade_date: str | None = None,
) -> tuple[dict[str, str], bool]:
    """读取缓存的涨跌停状态。

    Args:
        path: 状态文件路径 (默认 reports/operations/price_limit_status.json)
        trade_date: 期望的交易日 (``YYYY-MM-DD``); None 时用当前业务日期

    Returns:
        ``(status_map, stale)`` — 文件缺失/损坏 → ``({}, True)``;
        ``as_of_date != trade_date`` → ``(status_map, True)`` (状态过期, 需保守处理)。
    """
    target = path or _STATUS_FILE
    expected_date = trade_date or now_bj().strftime("%Y-%m-%d")
    if not os.path.exists(target):
        return {}, True
    try:
        with open(target, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
        logger.warning("[PriceLimit] 状态读取失败 (%s): %s", target, exc)
        return {}, True

    status = payload.get("status") if isinstance(payload, dict) else None
    if not isinstance(status, dict):
        return {}, True
    stale = payload.get("as_of_date") != expected_date
    if stale:
        logger.warning(
            "[PriceLimit] 涨跌停状态过期 (as_of_date=%s, 期望 %s) — 主链将保守拒绝",
            payload.get("as_of_date"),
            expected_date,
        )
    return {str(k): str(v) for k, v in status.items()}, stale


def refresh_price_limit_status(
    codes: list[str],
    trade_date: str | None = None,
    persist: bool = True,
    quotes: dict[str, dict] | None = None,
) -> tuple[dict[str, str], bool]:
    """刷新并 (可选) 持久化涨跌停状态。

    Returns:
        ``(status_map, stale)`` — 刷新成功必然 stale=False; 行情完全不可用时
        回退到磁盘缓存 (stale 按其日期判定)。
    """
    status = compute_price_limit_status(codes, quotes=quotes)
    resolved = {c: s for c, s in status.items() if s != STATUS_UNKNOWN}
    if not resolved:
        logger.warning("[PriceLimit] 全部标的涨跌停状态未知, 回退缓存 (保守处理)")
        # S-2: 回退路径下"今日状态"并未被证实 → 无论缓存日期如何一律 stale,
        # 否则会把昨日 normal 当作"今日未涨停"从而放行买入 (fail-closed 要求)
        cached, _ = load_price_limit_status(trade_date=trade_date)
        return cached, True
    if persist:
        cached, _ = load_price_limit_status(trade_date=trade_date)
        merged = {**cached, **resolved}
        save_price_limit_status(merged)
        return merged, False
    return resolved, False


__all__ = [
    "STATUS_LIMIT_DOWN",
    "STATUS_LIMIT_UP",
    "STATUS_NORMAL",
    "STATUS_UNKNOWN",
    "compute_price_limit_status",
    "get_board_type",
    "load_price_limit_status",
    "refresh_price_limit_status",
    "save_price_limit_status",
]
