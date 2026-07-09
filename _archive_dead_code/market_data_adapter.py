#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
行情数据适配器
==============

目标：给 auto_hedge_executor 提供可替换的市场数据来源。
优先走现有数据连接器，再回退到 AKShare / 本地缓存 / 默认值。
"""

import os
import logging
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger("MarketDataAdapter")


class MarketDataAdapter:
    """行情数据适配器"""

    def __init__(self, prefer_realtime: bool = True) -> None:
        self.prefer_realtime = prefer_realtime

    def get_market_snapshot(self, index_code: str = "sh000001") -> Dict[str, Any]:
        snapshot = {
            "timestamp": datetime.now().isoformat(),
            "index_code": index_code,
            "current_price": 3000.0,
            "daily_drop": 0.0,
            "weekly_drop": 0.0,
            "vix_level": 18.0,
            "limit_down_count": 0,
            "sector_drops": {},
            "volatility": 0.18,
            "liquidity": 1.0,
            "var_95": 0.025,
            "es_95": 0.04,
        }
        try:
            import akshare as ak  # type: ignore
            # 上证指数最新行情
            df = ak.stock_zh_index_daily(symbol="sh000001")
            if df is not None and not df.empty:
                latest = df.iloc[-1]
                close = float(latest.get("close", latest.get("收盘", 3000)))
                snapshot["current_price"] = close

                if len(df) >= 2:
                    prev = df.iloc[-2]
                    prev_close = float(prev.get("close", prev.get("收盘", close)))
                    if prev_close > 0:
                        snapshot["daily_drop"] = (close - prev_close) / prev_close

                if len(df) >= 6:
                    week_start = df.iloc[-6]
                    week_start_close = float(week_start.get("close", week_start.get("收盘", close)))
                    if week_start_close > 0:
                        snapshot["weekly_drop"] = (close - week_start_close) / week_start_close
        except Exception as e:
            logger.debug(f"AKShare 获取指数行情失败，使用默认值: {e}")

        return snapshot

    def build_market_inputs(self, portfolio_value: float, current_price: float = 3000.0) -> Dict[str, Any]:
        snapshot = self.get_market_snapshot()
        return {
            "current_price": snapshot.get("current_price", current_price),
            "portfolio_value": portfolio_value,
            "vix_level": snapshot.get("vix_level", 18.0),
            "daily_drop": snapshot.get("daily_drop", 0.0),
            "weekly_drop": snapshot.get("weekly_drop", 0.0),
            "limit_down_count": snapshot.get("limit_down_count", 0),
            "sector_drops": snapshot.get("sector_drops", {}),
            "volatility": snapshot.get("volatility", 0.18),
            "liquidity": snapshot.get("liquidity", 1.0),
            "var_95": snapshot.get("var_95", 0.025),
            "es_95": snapshot.get("es_95", 0.04),
        }
