"""盯市价格解析 (P0-MTM, 2026-09-12)

问题背景
--------
持仓账本 (config/positions.json) 的 ``est_price`` 是**上次成交价 (含滑点)**,
不是市价。止损/熔断喂数/再平衡此前全部直接消费 est_price:

  - 持仓自上次成交后下跌 30% 也不会触发 8% 止损 (止损检查形同虚设);
  - 组合权益/集中度基于失真市值 (熔断阈值失真);
  - 再平衡的偏离度与限价基于陈旧价 (LIMIT 单挂出即偏离市价)。

本模块提供统一的盯市价解析: 优先实时行情 (``utils.astock_realtime``,
东财 push2 + 腾讯双源, 60s 缓存), 逐标的回退 ``est_price`` 并显式标记来源,
供调用方留痕。

设计约束
--------
- **只读**: 不回写 positions.json — 账本只记录成交事实, 盯市价是会话内
  派生数据 (写入账本会混淆"成交事实"与"行情快照"两种口径);
- **fail-open + 留痕**: 行情不可用/QUANT_OFFLINE=1 时回退 est_price
  (与旧口径一致, 行为不劣化), 但返回值携带 ``source``/``stale`` 标记,
  调用方必须落日志;
- **语义**: 收盘后/非交易时段实时源返回最近收盘价, 仍优于陈旧成交价。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 单次批量盯市的标的数上限 (东财 ulist 单页支持 2000, 此处仅防误用)
_MAX_CODES = 500


def fetch_mark_price_map(pure_codes: list[str]) -> dict[str, dict[str, Any]]:
    """批量拉取实时盯市价 (仅返回成功解析的标的)。

    Args:
        pure_codes: 6 位纯代码列表 (如 ``"600000"``; 后缀在内部剥离)

    Returns:
        ``{pure_code: {"price": float, "source": str}}`` — price>0 才会收录。
        行情源不可用/QUANT_OFFLINE 时返回空 dict (调用方回退 est_price)。
    """
    codes: list[str] = []
    seen: set[str] = set()
    for raw in pure_codes or []:
        code = str(raw or "").strip().split(".")[0]
        if code and code not in seen:
            seen.add(code)
            codes.append(code)
    if not codes:
        return {}
    if len(codes) > _MAX_CODES:
        logger.warning("[MTM] 标的数 %d 超上限 %d, 截断", len(codes), _MAX_CODES)
        codes = codes[:_MAX_CODES]

    try:
        from utils.runtime_mode import is_offline

        if is_offline():
            logger.info("[MTM] QUANT_OFFLINE=1, 跳过实时行情, 全部回退 est_price")
            return {}
    except ImportError:
        pass  # runtime_mode 缺失不构成拦截理由 (防御式, 正常路径不可达)

    try:
        from utils.astock_realtime import get_realtime_quotes

        quotes = get_realtime_quotes(codes, use_cache=True)
    except Exception as e:  # noqa: BLE001 — 行情故障回退 est_price, 不阻断执行链
        logger.warning("[MTM] 实时行情拉取失败, 全部回退 est_price: %s", e)
        return {}

    mark_map: dict[str, dict[str, Any]] = {}
    for code, q in (quotes or {}).items():
        try:
            price = float((q or {}).get("price") or 0)
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        mark_map[str(code).strip().split(".")[0]] = {
            "price": price,
            "source": str((q or {}).get("source", "realtime")),
        }
    return mark_map


def resolve_position_mark_price(
    full_code: str,
    item: dict[str, Any],
    mark_prices: dict[str, dict[str, Any]] | None,
) -> tuple[float, str]:
    """单持仓的盯市价解析: 实时价优先, 回退 est_price。

    Args:
        full_code: 持仓键 (如 ``"600000.SH"`` 或 ``"600000"``)
        item: 持仓明细 (读 ``est_price`` 兜底)
        mark_prices: ``fetch_mark_price_map`` 产物; None/缺失即回退

    Returns:
        ``(price, source)`` — source ∈ {"realtime", "est_price"}。
        两者都不可用时 ``(0.0, "unavailable")`` (调用方按无价处理)。
    """
    mark = (mark_prices or {}).get(str(full_code).strip().split(".")[0]) or {}
    try:
        mark_price = float(mark.get("price") or 0)
    except (TypeError, ValueError):
        mark_price = 0.0
    if mark_price > 0:
        return mark_price, "realtime"
    try:
        est = float(item.get("est_price") or 0)
    except (TypeError, ValueError):
        est = 0.0
    if est > 0:
        return est, "est_price"
    return 0.0, "unavailable"


def summarize_mark_coverage(
    mark_prices: dict[str, dict[str, Any]] | None,
    codes: list[str],
) -> tuple[int, int]:
    """盯市覆盖率统计 (供调用方落日志)。

    Returns:
        ``(realtime_count, total_count)``
    """
    total = len({str(c).strip().split(".")[0] for c in codes or [] if c})
    rt = sum(
        1
        for c in codes or []
        if str(c).strip().split(".")[0] in (mark_prices or {})
    )
    return rt, total


__all__ = [
    "fetch_mark_price_map",
    "resolve_position_mark_price",
    "summarize_mark_coverage",
]
