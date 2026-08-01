# -*- coding: utf-8 -*-
"""Wind MCP 桥接 — 为 hedge_engine / futures_prices 提供数据接口

BUG-07 修复 (2026-07-31):
  原桥接 _wind_mcp_call(endpoint, params) 尝试导入不存在的 ..data.wind_mcp.query_wind,
  且签名与 futures_prices.py 调用方 (endpoint, tool_name, params, timeout) 不匹配,
  导致 Wind MCP 整条回退链失效 (TypeError / ImportError).

  修复: 扩展签名为 (endpoint, tool_name=None, params=None, timeout=10),
        委托给已验证可用的 tools.wind_mcp_fetcher._call_wind.
"""

import logging
import os
import sys
from typing import Any, Optional

_log = logging.getLogger("bridges.wind_mcp")

# === BUG-07: 延迟导入 wind_mcp_fetcher (避免循环依赖) ===
_wind_fetcher = None
_wind_fetcher_loaded = False


def _load_wind_fetcher() -> Optional[callable]:
    """延迟加载 tools/wind_mcp_fetcher.py (仅首次调用时)"""
    global _wind_fetcher, _wind_fetcher_loaded
    if _wind_fetcher_loaded:
        return _wind_fetcher
    _wind_fetcher_loaded = True
    try:
        # 计算 tools 目录的绝对路径并加入 sys.path
        _repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        _tools_dir = os.path.join(_repo_root, "tools")
        if _tools_dir not in sys.path:
            sys.path.insert(0, _tools_dir)
        from wind_mcp_fetcher import _call_wind  # type: ignore

        _wind_fetcher = _call_wind
        _log.info("[wind_mcp_bridge] wind_mcp_fetcher 加载成功 (tools/wind_mcp_fetcher.py)")
    except Exception as e:
        _log.warning("[wind_mcp_bridge] wind_mcp_fetcher 加载失败: %s", e)
        _wind_fetcher = None
    return _wind_fetcher


# endpoint → Wind MCP server_type 映射
_ENDPOINT_TO_SERVER = {
    "index_data": "stock_data",  # 指数数据走 stock_data server
    "realtime_prices": "stock_data",
    "stock_data": "stock_data",
    "fund_data": "fund_data",
    "analytics_data": "stock_data",
}


def _wind_mcp_call(
    endpoint: str,
    tool_name: Optional[str] = None,
    params: Optional[dict] = None,
    timeout: int = 10,
) -> dict:
    """调用 Wind MCP 接口 (BUG-07 修复版)

    Args:
        endpoint: 逻辑端点名 (如 "index_data", "realtime_prices")
        tool_name: Wind MCP 工具名 (如 "get_index_price_indicators")
        params: 调用参数 dict
        timeout: 超时秒数 (传递给底层 subprocess, 实际超时由 _call_wind 控制)

    Returns:
        dict: 成功时 {"rows": [...], "columns": [...], "source": "wind_mcp"}
              失败时 {"status": "unavailable", "error": "..."}
    """
    call_wind = _load_wind_fetcher()
    if call_wind is None:
        return {"status": "unavailable", "endpoint": endpoint, "error": "fetcher_not_loaded", "data": {}}

    server_type = _ENDPOINT_TO_SERVER.get(endpoint, "stock_data")
    if tool_name is None:
        # 兼容旧调用: 仅传 endpoint + params 时, 用 endpoint 作为 tool_name
        tool_name = endpoint

    # timeout 保留用于未来精细化控制 (底层 _call_wind 内部 subprocess timeout=60s)
    _ = timeout

    try:
        result = call_wind(server_type, tool_name, params or {})
        if not isinstance(result, dict) or not result.get("ok"):
            err = result.get("error", "unknown") if isinstance(result, dict) else "non_dict"
            _log.debug("[wind_mcp_bridge] %s.%s 调用失败: %s", server_type, tool_name, err)
            return {"status": "unavailable", "endpoint": endpoint, "error": err, "data": {}}

        raw_data = result.get("data", {})
        # 解析 Wind MCP 响应格式: content[0].text → {data: {columns, rows}}
        return _parse_wind_response(raw_data, endpoint)
    except Exception as e:
        _log.warning("[wind_mcp_bridge] %s.%s 异常: %s", server_type, tool_name, e)
        return {"status": "unavailable", "endpoint": endpoint, "error": str(e), "data": {}}


def _parse_wind_response(raw_data: Any, endpoint: str) -> dict:  # noqa: ANN401
    """解析 Wind MCP 嵌套响应格式

    Wind MCP 返回结构 (二次 JSON.parse):
      {"result": {"content": [{"text": '{"data": {"columns": [...], "rows": [...]}}'}]}}
    或简化结构:
      {"data": {"columns": [...], "rows": [...]}}
      {"columns": [...], "rows": [...]}
    """
    import json

    try:
        # 层 1: result.content[0].text
        if isinstance(raw_data, dict):
            result_obj = raw_data.get("result", raw_data)
            content = result_obj.get("content", []) if isinstance(result_obj, dict) else []
            if content and isinstance(content[0], dict):
                text = content[0].get("text", "")
                if text:
                    try:
                        parsed = json.loads(text)
                    except (json.JSONDecodeError, TypeError):
                        parsed = {}
                    data_obj = parsed.get("data", parsed) if isinstance(parsed, dict) else {}
                    if isinstance(data_obj, dict) and ("columns" in data_obj or "rows" in data_obj):
                        return {
                            "columns": data_obj.get("columns", []),
                            "rows": data_obj.get("rows", []),
                            "source": "wind_mcp",
                        }
            # 层 2: 直接 data.columns / data.rows
            data_field = raw_data.get("data", raw_data)
            if isinstance(data_field, dict) and ("columns" in data_field or "rows" in data_field):
                return {
                    "columns": data_field.get("columns", []),
                    "rows": data_field.get("rows", []),
                    "source": "wind_mcp",
                }
            # 层 3: 顶层就是 columns/rows
            if "columns" in raw_data or "rows" in raw_data:
                return {
                    "columns": raw_data.get("columns", []),
                    "rows": raw_data.get("rows", []),
                    "source": "wind_mcp",
                }
        return {"status": "unavailable", "endpoint": endpoint, "error": "no_columns_rows", "data": {}}
    except Exception as e:
        _log.warning("[wind_mcp_bridge] 响应解析失败: %s", e)
        return {"status": "unavailable", "endpoint": endpoint, "error": f"parse_error: {e}", "data": {}}


def get_realtime_prices_batch(symbols: list) -> dict:
    """批量获取实时价格"""
    try:
        result = _wind_mcp_call("realtime_prices", "get_stock_quote", {"windcodes": ",".join(symbols)})
        if result.get("status") == "unavailable":
            return {s: None for s in symbols}
        rows = result.get("rows", [])
        columns = result.get("columns", [])
        out = {}
        for row in rows:
            if len(row) >= len(columns):
                record = dict(zip(columns, row))
                windcode = record.get("WINDCODE") or record.get("windcode") or ""
                price = record.get("CLOSE") or record.get("close") or record.get("最新价")
                if windcode and price:
                    try:
                        out[windcode] = float(price)
                    except (TypeError, ValueError):
                        pass
        return out
    except Exception:
        return {s: None for s in symbols}
