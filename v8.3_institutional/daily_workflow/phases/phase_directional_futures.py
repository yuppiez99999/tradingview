#!/usr/bin/env python3
"""
Phase implementation: phase_directional_futures

Extracted from original DailyWorkflow class for modularization.
This module contains the standalone phase function implementing the phase_directional_futures phase.

The function receives a DailyWorkflow instance as its first parameter ("workflow").
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def phase_directional_futures(workflow) -> dict[str, Any]:
    """方向性期货交易 — CU(沪铜)/AU(黄金)/T(10年国债) 三品种

    v10.0 macro_hedge_account 中的方向性子模块:
        - CU 沪铜:    新能源需求方向, 默认做多
        - AU 黄金:    避险+通胀对冲, 默认做多
        - T  10年国债: 利率方向, 默认做空 (十五五财政发力推升利率)

    信号源:
        - MA20/MA60 趋势 + RSI 超买超卖 + MACD 动量 + 品种默认方向 0.5 票
        - 三重确认: ≥2 票做多, ≤-2 票做空, 否则空仓

    风控:
        - 单笔最大亏损 20% (保证金视角)
        - 日最大亏损 15% (账户视角)
        - 周连续亏损 25% → 暂停 7 天

    Returns:
        方向性期货交易结果
    """
    logger.info("=" * 60)
    logger.info("Phase 4.9: 方向性期货交易 (CU/AU/T)")
    logger.info("=" * 60)

    result: dict[str, Any] = {
        "status": "PASS",
        "action": "skip",
        "signals": [],
        "orders": [],
        "risk_status": "normal",
        "total_margin_used": 0.0,
        "total_notional": 0.0,
        "pause_until": None,
    }

    if not V10_STRATEGY_READY:
        result["status"] = "SKIP"
        result["reason"] = "v10.0 方向性期货模块未加载"
        logger.warning("[DirectionalFutures] v10.0 模块未加载, 跳过")
        workflow.state["phases"]["directional_futures"] = result
        return result

    try:
        # 1. 加载市场数据 (CU/AU/T 的 OHLCV)
        market_data = workflow._load_directional_futures_market_data()

        # 2. 加载当前持仓
        current_positions = workflow._load_directional_futures_positions()

        # 3. 获取当前价格
        prices = workflow._get_futures_prices(market_data)

        # 4. 加载风控状态
        daily_pnl_pct, weekly_loss_pct, last_pause_date = workflow._load_directional_futures_risk_state()

        # 5. 调用 DirectionalFuturesTrader.run()
        trader = DirectionalFuturesTrader()
        df_result = trader.run(
            market_data=market_data,
            current_positions=current_positions,
            prices=prices,
            trade_date=date.today(),
            daily_pnl_pct=daily_pnl_pct,
            weekly_consecutive_loss_pct=weekly_loss_pct,
            last_loss_pause_date=last_pause_date,
        )

        # 6. 输出摘要
        summary = trader.summary(df_result)
        logger.info("\n" + summary)

        # 7. 保存指令到文件
        orders_saved = workflow._save_directional_futures_orders(df_result.orders)

        # 8. 更新结果
        result.update({
            "status": "PASS",
            "action": df_result.action,
            "signals": [asdict(s) if hasattr(s, '__dataclass_fields__') else dict(s)
                        for s in df_result.signals],
            "orders": [asdict(o) if hasattr(o, '__dataclass_fields__') else dict(o)
                       for o in df_result.orders],
            "risk_status": df_result.risk_status,
            "total_margin_used": df_result.total_margin_used,
            "total_notional": df_result.total_notional,
            "pause_until": df_result.pause_until.isoformat() if df_result.pause_until else None,
            "orders_file": orders_saved,
        })

        # 9. 风控告警
        if df_result.risk_status == "warning":
            logger.warning(f"[DirectionalFutures] 风控告警: 日亏损 {daily_pnl_pct:.2%}")
        elif df_result.risk_status == "paused":
            logger.error(f"[DirectionalFutures] 已暂停交易至 {df_result.pause_until}")

    except Exception as e:
        logger.error(f"[DirectionalFutures] 方向性期货交易失败: {e}", exc_info=True)
        result["status"] = "ERROR"
        result["reason"] = str(e)

    # === 写入 state ===
    workflow.state["phases"]["directional_futures"] = result
    logger.info("-" * 60)
    logger.info("Phase 4.9 完成: 动作=%s, 风控=%s, 保证金=¥%.0f, 名义=¥%.0f",
                result.get("action", ""),
                result.get("risk_status", ""),
                result.get("total_margin_used", 0),
                result.get("total_notional", 0))
    logger.info("=" * 60)
    return result


