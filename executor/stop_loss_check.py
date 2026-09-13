"""盘后止损止盈检查 (2026-09-12 自 daily_trade_executor 迁出 + 盯市接入)

迁出动因: 宿主 1500 行结构护栏 (tests/unit/test_daily_executor_premarket_split_20260910.py)
在 S-1 口径 2 闭环后已顶满; 本函数自包含 (仅依赖 wt_modules/positions 入参),
迁出后宿主以 ``X as X`` 显式重导出, ``monkeypatch.setattr(daily_trade_executor,
"_run_stop_loss_check", ...)`` 语义不变。

P0-MTM (2026-09-12) 盯市接入: 原实现 ``current_price = est_price`` — est_price 是
上次成交价 (含滑点), 持仓长期无新成交时严重偏离市价 → 跌 30% 也不触发 8% 止损。
现经 ``mark_prices`` (utils/execution/mark_to_market.fetch_mark_price_map 产物)
优先使用实时盯市价, 缺失回退 est_price, 触发项携带 ``price_source`` 留痕。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def run_stop_loss_check(
    wt_modules: dict,
    positions: dict,
    mark_prices: dict[str, dict[str, Any]] | None = None,
) -> list:
    """盘后止损止盈检查 — 对每个持仓调用 StopLossManager.check_stop_loss

    P1-1 修复 (2026-08-14): 此前 StopLossManager 被实例化但 check_stop_loss 从未被调用,
    导致止损止盈完全失效。现在在 execute_instructions 执行前对全部持仓检查,
    触发的标的记入返回列表供后续处理。

    Args:
        wt_modules: WonderTrader 模块 dict (含 stop_loss_manager)
        positions: 当前持仓 dict
        mark_prices: 盯市价表 (``fetch_mark_price_map`` 产物);
            None 或缺失标的回退 est_price (P0-MTM fail-open + price_source 留痕)

    Returns:
        触发列表 [{code, action, current_price, price_source, ...}, ...]
    """
    sl_manager = wt_modules.get("stop_loss_manager")
    if not sl_manager:
        # DTE-3: 止损管理器不可用时, 返回特殊标记项让调用方告警可见,
        # 而非静默返回 [] (否则止损风控完全失效且无人察觉)。
        logger.error("[StopLoss] stop_loss_manager 不可用, 止损检查被跳过 (风控降级)")
        return [
            {
                "code": "__manager_unavailable",
                "action": "HOLD",
                "order_info": "stop_loss_manager 不可用, 止损风控降级",
            }
        ]

    from utils.execution.mark_to_market import resolve_position_mark_price

    triggered = []
    for code, item in positions.items():
        if not isinstance(item, dict):
            continue
        avg_cost = item.get("avg_cost", 0)
        qty = (
            item.get("phase1_shares")
            or item.get("total_shares")
            or item.get("shares", 0)
        )
        # P0-MTM: 实时盯市价优先, 回退 est_price (陈旧成交价)
        current_price, price_source = resolve_position_mark_price(code, item, mark_prices)
        if avg_cost <= 0 or qty == 0 or current_price <= 0:
            continue

        pure_code = code.split(".")[0]
        if pure_code not in sl_manager.stop_loss_orders:
            sl_manager.set_stop_loss(pure_code, avg_cost, abs(qty))

        action, order_info = sl_manager.check_stop_loss(pure_code, current_price)
        if action != "none" and order_info:
            triggered.append(
                {
                    "code": code,
                    "name": item.get("name", code),
                    "action": action,
                    "current_price": current_price,
                    "price_source": price_source,
                    "stop_price": order_info.get("stop_price", 0),
                    "take_profit_price": order_info.get("take_profit_price", 0),
                    "pnl_pct": round((current_price - avg_cost) / avg_cost, 4),
                }
            )
            # P&L 用 %.1f%% 而非 %+.1%%: 后者经 printf 解析会残留裸 "%" 并抛
            # ValueError: unsupported format character (日志本身再触发异常)
            logger.warning(
                "[StopLoss] %s (%s) 触发 %s @ ¥%.3f (P&L %+.1f%%, 价源=%s)",
                item.get("name", code),
                code,
                action,
                current_price,
                ((current_price - avg_cost) / avg_cost) * 100,
                price_source,
            )

    if not triggered:
        logger.info("[StopLoss] 全部持仓在安全范围内, 无触发")
    else:
        logger.warning("[StopLoss] 共 %d 个标的触发止损/止盈", len(triggered))
    return triggered


__all__ = ["run_stop_loss_check"]
