# -*- coding: utf-8 -*-
"""Wind MCP 桥接 — 为 hedge_engine 提供数据接口"""
import os, json, logging
_log = logging.getLogger("bridges.wind_mcp")

def _wind_mcp_call(endpoint: str, params: dict = None) -> dict:
    """调用Wind MCP接口"""
    try:
        from ..data.wind_mcp import query_wind
        return query_wind(endpoint, **(params or {}))
    except ImportError:
        _log.warning("Wind MCP 不可用: %s", endpoint)
        return {"status": "unavailable", "endpoint": endpoint, "data": {}}

def get_realtime_prices_batch(symbols: list) -> dict:
    """批量获取实时价格"""
    try:
        result = _wind_mcp_call("realtime_prices", {"symbols": symbols})
        return result.get("data", {})
    except Exception:
        return {s: None for s in symbols}
