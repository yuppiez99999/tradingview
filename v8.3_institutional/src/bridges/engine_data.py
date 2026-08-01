# -*- coding: utf-8 -*-
"""数据引擎桥接 — ml_predictor 的数据依赖"""

import logging
from typing import Optional

_log = logging.getLogger("bridges.engine_data")


def fetch_market_data(symbols: list, start_date: Optional[str] = None, end_date: Optional[str] = None) -> dict:
    """获取市场数据 - 桥接到v7.5 data层"""
    try:
        from ..data.market_data import fetch_data

        return fetch_data(symbols, start_date, end_date)
    except ImportError:
        _log.warning("data.market_data 不可用, 返回空数据")
        return {}
