"""
Wind MCP Fetcher

提供 Wind MCP 的实时行情、批量行情、K线数据获取能力。
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from typing import Any

import requests as _requests

# SC-6 修复 (2026-09-11): 业务时间统一走 now_bj() (naive 北京时间), 消除本机时区依赖
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
from utils.datetime_utils import now_bj  # noqa: E402

SKILL_DIR = os.path.join(
    os.path.dirname(__file__), "..", ".agents", "skills", "wind-mcp-skill"
)
CLI_PATH = os.path.join(SKILL_DIR, "scripts", "cli.mjs")
WIND_STOCK_ENDPOINT = "https://mcp.wind.com.cn/vserver_stock_data/mcp/"
WIND_FUND_ENDPOINT = "https://mcp.wind.com.cn/vserver_fund_data/mcp/"
WIND_FINANCIAL_DOCS_ENDPOINT = "https://mcp.wind.com.cn/vserver_financial_docs/mcp/"
WIND_ECONOMIC_ENDPOINT = "https://mcp.wind.com.cn/vserver_economic_data/mcp/"
WIND_INDEX_ENDPOINT = "https://mcp.wind.com.cn/vserver_index_data/mcp/"


def _ensure_wind_cli() -> str | None:
    if not os.path.isfile(CLI_PATH):
        return None
    node = (
        sys.executable.replace("python.exe", "node.exe")
        if sys.executable.endswith("python.exe")
        else "node"
    )
    if os.path.isfile(node):
        return node
    for candidate in ("node", "node.exe", r"C:\Program Files\nodejs\node.exe"):
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def _parse_sse_generic(text: str) -> dict | None:
    """通用 SSE 解析: 提取 "data: {json}" 行并解析为 dict

    Wind MCP 的 initialize/tools/list 等 RPC 返回 SSE 格式:
        event: message
        data: {"jsonrpc":"2.0",...}

    返回解析后的 dict, 或 None (不是 SSE 格式时)。
    """
    if "data: " not in text:
        return None
    # 提取最后一个 "data: " 行 (MCP 响应通常是单条消息)
    for line in reversed(text.strip().split("\n")):
        line = line.strip()
        if line.startswith("data: "):
            json_str = line[6:]
            try:
                return json.loads(json_str)
            except Exception:
                return None
    return None


def _parse_sse_minute_quote(text: str) -> dict | None:
    """解析 Wind MCP stock_data.get_stock_quote 返回的 SSE 分钟级行情，并打包成 OHLCV。"""
    m = re.search(r"data:\s*(\{.*\})\s*$", text, re.S)
    if not m:
        return None
    try:
        payload = json.loads(m.group(1))
    except Exception:
        return None
    result = (payload.get("result") or {}).get("content") or []
    if not result:
        return None
    first = result[0]
    if not isinstance(first, dict):
        return None
    result = (first.get("text") or "").strip()
    if not result:
        return None
    try:
        inner = json.loads(result)
    except Exception:
        return None
    data = inner.get("data") or inner
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            return None
    columns = [c.get("name") for c in (data.get("columns") or [])]
    rows = data.get("rows") or []
    if not rows:
        return None

    records = []
    for row in rows:
        if len(columns) != len(row):
            continue
        records.append(dict(zip(columns, row, strict=True)))
    if not records:
        return None

    prices = []
    volume = 0.0
    amount = 0.0
    prev_close = None
    time = None

    for r in records:
        price = r.get("MATCH") or r.get("AVGPRICE") or r.get("CLOSE") or r.get("close")
        if price is not None:
            try:
                prices.append(float(price))
            except (TypeError, ValueError):
                pass
        vol = r.get("VOLUME") or r.get("volume")
        if vol is not None:
            try:
                volume += float(vol)
            except (TypeError, ValueError):
                pass
        amt = r.get("TURNOVER") or r.get("amount")
        if amt is not None:
            try:
                amount += float(amt)
            except (TypeError, ValueError):
                pass
        if prev_close is None:
            pc = r.get("PRE_CLOSE") or r.get("prev_close") or r.get("前收盘价")
            if pc is not None:
                try:
                    prev_close = float(pc)
                except (TypeError, ValueError):
                    pass
        t = r.get("TIME") or r.get("time") or r.get("_DATE")
        if t:
            time = t

    if not prices:
        return None

    return {
        "price": prices[-1],
        "open": prices[0],
        "high": max(prices),
        "low": min(prices),
        "prev_close": prev_close,
        "volume": volume,
        "amount": amount,
        "time": time,
        "source": "wind_mcp",
    }


def _wind_http(
    server_endpoint: str, tool_name: str, params: dict, api_key: str, retries: int = 2
) -> dict:
    """绕过 CLI，直接请求 Wind MCP，并尝试解析 SSE。失败时自动重试。"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": params},
    }
    last_err = None
    for _attempt in range(1, retries + 1):
        try:
            resp = _requests.post(
                server_endpoint,
                headers=headers,
                json=payload,
                timeout=60,
                proxies={"http": None, "https": None},
            )
        except Exception as e:
            last_err = f"wind_http_error: {e}"
            continue
        if resp.status_code != 200:
            last_err = f"wind_http_status:{resp.status_code}"
            continue
        # 强制 UTF-8 解码 (requests 默认用 ISO-8859-1, 导致中文乱码)
        # 优先用 resp.content (字节流) + 显式 UTF-8 解码, 避免 charset 推断错误
        try:
            text = resp.content.decode("utf-8", errors="replace")
        except Exception:
            text = resp.text
        if not text or not text.strip():
            last_err = "wind_http_empty"
            continue
        parsed = _parse_sse_minute_quote(text)
        if parsed:
            return {"ok": True, "data": parsed, "sse": True}
        # 通用 SSE 解析: "event: message\ndata: {json}\n\n"
        sse_data = _parse_sse_generic(text)
        if sse_data is not None:
            # SSE 成功响应, 检查 isError
            result = sse_data.get("result") if isinstance(sse_data, dict) else None
            if isinstance(result, dict) and result.get("isError"):
                error_texts = []
                for content in result.get("content") or []:
                    if isinstance(content, dict) and content.get("text"):
                        error_texts.append(content["text"])
                error_msg = (
                    " | ".join(error_texts) if error_texts else "unknown_wind_error"
                )
                return {
                    "ok": False,
                    "error": f"wind_api_error: {error_msg}",
                    "data": sse_data,
                }
            return {"ok": True, "data": sse_data, "sse": True}
        try:
            data = json.loads(text)
        except Exception:
            last_err = "wind_http_bad_json"
            continue
        # 检查业务层错误 (Wind MCP 返回 isError=true 时, 业务调用失败)
        # 常见错误: "单日请求次数超限", "配额耗尽", "无效参数" 等
        result = data.get("result") if isinstance(data, dict) else None
        if isinstance(result, dict) and result.get("isError"):
            error_texts = []
            for content in result.get("content") or []:
                if isinstance(content, dict) and content.get("text"):
                    error_texts.append(content["text"])
            error_msg = " | ".join(error_texts) if error_texts else "unknown_wind_error"
            # 业务错误不重试 (重试也是同样的错误), 直接返回失败
            return {"ok": False, "error": f"wind_api_error: {error_msg}", "data": data}
        return {"ok": True, "data": data, "sse": False}
    return {"ok": False, "error": last_err or "wind_http_failed"}


def _wind_http_generic(
    server_endpoint: str, tool_name: str, params: dict, api_key: str, retries: int = 2
) -> dict:
    """v8.6.11 新增: 通用 HTTP 调用 (不调用 _parse_sse_minute_quote)

    用于 K 线类工具 (get_stock_kline / get_fund_kline), 这类工具返回多行 K 线数据,
    不应被 _parse_sse_minute_quote 误解析为单条 quote。

    解析顺序:
        1. _parse_sse_generic (提取 SSE 中的 JSON)
        2. 直接 json.loads
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": params},
    }
    last_err = None
    for _attempt in range(1, retries + 1):
        try:
            resp = _requests.post(
                server_endpoint,
                headers=headers,
                json=payload,
                timeout=60,
                proxies={"http": None, "https": None},
            )
        except Exception as e:
            last_err = f"wind_http_generic_error: {e}"
            continue
        if resp.status_code != 200:
            last_err = f"wind_http_generic_status:{resp.status_code}"
            continue
        try:
            text = resp.content.decode("utf-8", errors="replace")
        except Exception:
            text = resp.text
        if not text or not text.strip():
            last_err = "wind_http_generic_empty"
            continue
        # 仅使用通用 SSE 解析 (不调用 _parse_sse_minute_quote)
        sse_data = _parse_sse_generic(text)
        if sse_data is not None:
            # 检查业务错误
            result = sse_data.get("result") if isinstance(sse_data, dict) else None
            if isinstance(result, dict) and result.get("isError"):
                error_texts = []
                for content in result.get("content") or []:
                    if isinstance(content, dict) and content.get("text"):
                        error_texts.append(content["text"])
                error_msg = (
                    " | ".join(error_texts) if error_texts else "unknown_wind_error"
                )
                return {
                    "ok": False,
                    "error": f"wind_api_error: {error_msg}",
                    "data": sse_data,
                }
            return {"ok": True, "data": sse_data, "sse": True}
        try:
            data = json.loads(text)
        except Exception:
            last_err = "wind_http_generic_bad_json"
            continue
        result = data.get("result") if isinstance(data, dict) else None
        if isinstance(result, dict) and result.get("isError"):
            error_texts = []
            for content in result.get("content") or []:
                if isinstance(content, dict) and content.get("text"):
                    error_texts.append(content["text"])
            error_msg = " | ".join(error_texts) if error_texts else "unknown_wind_error"
            return {"ok": False, "error": f"wind_api_error: {error_msg}", "data": data}
        return {"ok": True, "data": data, "sse": False}
    return {"ok": False, "error": last_err or "wind_http_generic_failed"}


_WIND_API_KEY_CACHE = None


def _get_wind_api_key() -> str | None:
    global _WIND_API_KEY_CACHE
    if _WIND_API_KEY_CACHE is not None:
        return _WIND_API_KEY_CACHE
    env_key = os.environ.get("WIND_API_KEY")
    if env_key:
        _WIND_API_KEY_CACHE = env_key.strip()
        return _WIND_API_KEY_CACHE
    cfg = os.path.join(os.path.expanduser("~"), ".wind-aifinmarket", "config")
    try:
        if os.path.isfile(cfg):
            with open(cfg, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("export "):
                        line = line[7:].strip()
                    if line.startswith("WIND_API_KEY="):
                        _WIND_API_KEY_CACHE = line.split("=", 1)[1].strip()
                        return _WIND_API_KEY_CACHE
    except Exception:
        import logging

        logging.getLogger(__name__).error(
            "Wind API Key 加载失败: 配置文件 %s 读取异常, 所有Wind数据调用将不可用",
            cfg,
            exc_info=True,
        )
    _WIND_API_KEY_CACHE = None
    return None


def _call_wind(
    server_type: str, tool_name: str, params: dict, retries: int = 2
) -> dict:
    node = _ensure_wind_cli()
    if not node:
        return {"ok": False, "error": "wind_cli_missing"}

    args = [
        node,
        CLI_PATH,
        "call",
        server_type,
        tool_name,
        json.dumps(params, ensure_ascii=False),
    ]

    last_err = None
    for _attempt in range(1, retries + 1):
        try:
            proc = subprocess.run(
                args,
                cwd=SKILL_DIR,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=60,
            )
        except Exception as e:
            last_err = f"wind_cli_error: {e}"
            continue

        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        if not stdout:
            last_err = {"error": "wind_cli_empty", "stderr": stderr}
            continue

        try:
            data = json.loads(stdout)
        except Exception:
            last_err = {"error": "wind_cli_bad_json", "raw": stdout[:1000]}
            continue

        if isinstance(data, dict) and data.get("ok") is False:
            return {
                "ok": False,
                "error": data.get("error", {}).get("code", "wind_error"),
                "detail": data.get("error", {}).get("agent_action"),
                "raw": data,
            }

        return {"ok": True, "data": data}

    if isinstance(last_err, dict):
        return last_err
    return {"ok": False, "error": last_err or "wind_cli_failed"}


def wind_get_quote(windcode: str, is_fund: bool = False) -> dict | None:
    """获取股票/ETF 实时行情快照

    使用 get_stock_price_indicators 工具 (实时快照), 而非 get_stock_quote (分钟级时间序列)。
    参数 indexes 指定需要返回的行情指标字段 (中文名称, 逗号分隔)。
    """
    server_type = "fund_data" if is_fund else "stock_data"
    # 优先使用 get_stock_price_indicators (实时快照工具)
    # 回退工具名: get_fund_quote (ETF) / get_stock_quote (分钟级, 已弃用)
    tool_name_primary = (
        "get_stock_price_indicators" if not is_fund else "get_stock_price_indicators"
    )
    tool_name_fallback = "get_fund_quote" if is_fund else "get_stock_quote"
    api_key = _get_wind_api_key()

    # 请求的行情指标 (中文名称, 与 Wind MCP 文档一致)
    indexes = (
        "最新交易日,交易时间,最新成交价,前收盘价,今日开盘价,"
        "今日最高价,今日最低价,成交量,成交额,涨跌幅,换手率,量比"
    )

    if api_key:
        endpoint = WIND_FUND_ENDPOINT if is_fund else WIND_STOCK_ENDPOINT
        # 第一次尝试: 用 get_stock_price_indicators + indexes 参数
        http_res = _wind_http(
            endpoint,
            tool_name_primary,
            {"windcode": windcode, "indexes": indexes},
            api_key,
        )
        if http_res.get("ok") and isinstance(http_res.get("data"), dict):
            parsed = _extract_price_indicators(http_res["data"])
            if parsed:
                return parsed
        # 第二次尝试: 回退到 get_stock_quote/get_fund_quote (分钟级, 兼容旧接口)
        http_res2 = _wind_http(
            endpoint, tool_name_fallback, {"windcode": windcode}, api_key
        )
        if http_res2.get("ok") and isinstance(http_res2.get("data"), dict):
            d = http_res2["data"]
            if d.get("price") or d.get("prev_close"):
                return {
                    "price": d.get("price"),
                    "open": d.get("open"),
                    "high": d.get("high"),
                    "low": d.get("low"),
                    "prev_close": d.get("prev_close"),
                    "change": d.get("change"),
                    "volume": d.get("volume"),
                    "amount": d.get("amount"),
                    "time": d.get("time"),
                    "source": "wind_mcp",
                }
        # HTTP 失败, 回退到 CLI
        if http_res.get("error"):
            print(f"Wind HTTP ({tool_name_primary}) 失败: {http_res.get('error')}")

    res = _call_wind(server_type, tool_name_fallback, {"windcode": windcode})
    if not res.get("ok"):
        return None

    data = res.get("data") or {}
    _content = (data.get("result") or data).get("content") or []
    if not _content:
        return None
    _first = _content[0]
    if not isinstance(_first, dict):
        return None
    content = _first.get("text") or ""
    if not content:
        return None
    try:
        parsed = json.loads(content)
    except Exception:
        return None

    items = parsed.get("data") or parsed.get("result") or []
    if isinstance(items, dict):
        items = [items]
    if not items:
        return None

    item = items[0]
    return {
        "price": item.get("rt_last")
        or item.get("latest")
        or item.get("close")
        or item.get("MATCH")
        or item.get("CLOSE"),
        "open": item.get("rt_open") or item.get("open") or item.get("OPEN"),
        "high": item.get("rt_high") or item.get("high") or item.get("HIGH"),
        "low": item.get("rt_low") or item.get("low") or item.get("LOW"),
        "prev_close": item.get("rt_pre_close")
        or item.get("prev_close")
        or item.get("PRE_CLOSE"),
        "change": item.get("rt_change") or item.get("change_pct") or item.get("change"),
        "volume": item.get("rt_volume") or item.get("volume") or item.get("VOLUME"),
        "amount": item.get("rt_amount") or item.get("amount") or item.get("TURNOVER"),
        "time": item.get("rt_time") or item.get("time") or item.get("TIME"),
        "source": "wind_mcp",
    }


def _extract_price_indicators(data: dict) -> dict | None:
    """从 get_stock_price_indicators 响应中提取价格数据

    Wind MCP 返回结构 (SSE 或普通 JSON):
      result.content[0].text = 嵌套 JSON 字符串
      嵌套 JSON: {"data": {"columns": [...], "rows": [[...]]}}

    或直接返回字段映射:
      data.rows[0] = {"最新成交价": ..., "前收盘价": ..., ...}
    """
    # 路径1: SSE 已解析, data 直接是 MCP 响应
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, dict):
        result = data  # 可能 data 本身就是 result

    content = result.get("content") if isinstance(result, dict) else None
    if not content or not isinstance(content, list):
        return None

    first = content[0] if content else {}
    if not isinstance(first, dict):
        return None
    text = first.get("text") or ""
    if not text:
        return None

    # text 可能是嵌套 JSON 字符串
    try:
        inner = json.loads(text)
    except Exception:
        # text 可能不是 JSON, 而是纯文本 (错误消息)
        return None

    # 提取数据行
    inner_data = inner.get("data") or inner
    columns = [c.get("name") for c in (inner_data.get("columns") or [])]
    rows = inner_data.get("rows") or []

    if not rows:
        # 可能是 key-value 格式
        if isinstance(inner_data, dict):
            rows = [inner_data]
        elif isinstance(inner_data, list) and inner_data:
            rows = inner_data

    if not rows:
        return None

    row = rows[0]
    # 如果 row 是 list, 按 columns 索引
    if isinstance(row, list):
        col_map = {name: i for i, name in enumerate(columns)}

        def _get(name, default=None):
            idx = col_map.get(name)
            return row[idx] if idx is not None and idx < len(row) else default

    elif isinstance(row, dict):

        def _get(name, default=None):
            return row.get(name, default)

    else:
        return None

    price = _get("最新成交价") or _get("收盘价") or _get("最新价")
    prev_close = _get("前收盘价")
    change_pct = _get("涨跌幅")

    if price is None and prev_close is None:
        return None

    return {
        "price": float(price) if price is not None else None,
        "open": float(_get("今日开盘价") or 0) or None,
        "high": float(_get("今日最高价") or 0) or None,
        "low": float(_get("今日最低价") or 0) or None,
        "prev_close": float(prev_close) if prev_close is not None else None,
        "change": float(change_pct) if change_pct is not None else None,
        "volume": _get("成交量"),
        "amount": _get("成交额"),
        "time": _get("交易时间") or _get("最新交易日"),
        "source": "wind_mcp",
    }


def wind_get_batch_quotes(
    windcodes: list[str], is_fund: bool = False
) -> dict[str, dict | None]:
    result = {}
    for code in windcodes:
        quote = wind_get_quote(code, is_fund=is_fund)
        result[code] = quote
    return result


# Wind MCP K线复权类型 (与 Wind 终端 WSD PriceAdj 语义一致, 用于 get_stock_kline / get_fund_kline):
#   0 = 未复权, 1 = 前复权(qfq, 默认, 与 Wind 终端实测一致 — 份额折算 ETF 回测必须复权), 2 = 后复权(hfq)
KLINE_ADJUST_QFQ = 1
KLINE_ADJUST_NONE = 0
KLINE_ADJUST_HFQ = 2


def wind_get_kline(
    windcode: str,
    days: int = 2,
    is_fund: bool = False,
    adjust: int | None = KLINE_ADJUST_QFQ,
) -> list[dict] | None:
    """获取股票/ETF 历史 K 线数据

    v8.6.14 FIX (2026-08-25 复权口径修复):
        1. 新增 adjust 参数, 显式传 price_type 给 Wind MCP, 默认 1=前复权(qfq)。
           背景: sina 未复权在份额折算 ETF 上严重失真 (512100: +221.5% vs Wind +19.8%),
           ETF 回测必须使用复权数据。
        2. 服务端不支持 price_type 参数时, 自动回退无参调用 (万得服务端默认前复权, 兼容历史口径)。

    v8.6.11 FIX:
        1. 添加 HTTP 直连优先路径 (原仅走 CLI, CLI 不可用时全部失败)
        2. 使用 _wind_http_generic 而非 _wind_http (后者调用 _parse_sse_minute_quote
           会把 K 线数据错误解析成单条 quote)
        3. 优先级: HTTP 直连 (有 api_key) → CLI 回退 → None
    """
    server_type = "fund_data" if is_fund else "stock_data"
    tool_name = "get_fund_kline" if is_fund else "get_stock_kline"
    import datetime as dt

    end_date = now_bj()
    start_date = end_date - dt.timedelta(days=int(days * 1.5))

    # 构造请求参数
    base_params = {
        "windcode": windcode,
        "begin_date": start_date.strftime("%Y%m%d"),
        "end_date": end_date.strftime("%Y%m%d"),
    }

    def _fetch_with(price_type: int | None) -> list[dict] | None:
        params = dict(base_params)
        if price_type is not None:
            params["price_type"] = price_type

        # === 优先路径 1: HTTP 直连 (有 api_key 时, 使用 generic SSE 解析) ===
        api_key = _get_wind_api_key()
        if api_key:
            endpoint = WIND_FUND_ENDPOINT if is_fund else WIND_STOCK_ENDPOINT
            # v8.6.11 FIX: 使用 _wind_http_generic 避免被 _parse_sse_minute_quote 误解析
            http_res = _wind_http_generic(endpoint, tool_name, params, api_key)
            if http_res.get("ok") and isinstance(http_res.get("data"), dict):
                records = _extract_kline_records(http_res["data"])
                if records:
                    return records[-days:] if len(records) > days else records
            # HTTP 失败, 回退到 CLI
            if http_res.get("error"):
                import logging

                logging.getLogger(__name__).debug(
                    f"Wind HTTP ({tool_name}) 失败, 回退到 CLI: {http_res.get('error')}"
                )

        # === 回退路径 2: CLI 调用 ===
        res = _call_wind(
            server_type,
            tool_name,
            params,
        )
        if not res.get("ok"):
            return None

        data = res.get("data") or {}
        records = _extract_kline_records(data)
        if not records:
            return None
        return records[-days:] if len(records) > days else records

    records = _fetch_with(adjust)
    if not records and adjust is not None:
        # v8.6.14: 服务端可能不支持 price_type 参数, 回退无参调用 (默认前复权, 兼容历史口径)
        records = _fetch_with(None)
    return records


#: K 线数据域映射: kind -> (server_type, tool_name, HTTP endpoint)
_KLINE_DOMAINS = {
    "stock": ("stock_data", "get_stock_kline", WIND_STOCK_ENDPOINT),
    "fund": ("fund_data", "get_fund_kline", WIND_FUND_ENDPOINT),
    "index": ("index_data", "get_index_kline", WIND_INDEX_ENDPOINT),
}

#: 复权参数 (名称与取值都不同, 不可混用):
#:   基金/股票 `price_type`: 0=未复权 1=前复权 2=后复权
#:   指数     `aftype`    : 0=前复权 1=后复权 2=不复权
_KLINE_ADJUST_PARAM = {
    "stock": ("price_type", KLINE_ADJUST_QFQ),
    "fund": ("price_type", KLINE_ADJUST_QFQ),
    "index": ("aftype", 0),
}


def _normalize_date(value: str, style: str) -> str:
    """日期规整: 'YYYY-MM-DD' / 'YYYYMMDD' -> 目标风格; 无法解析则原样返回。"""
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    if len(digits) != 8:
        return str(value)
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}" if style == "dash" else digits


def wind_get_kline_range(
    windcode: str,
    begin_date: str,
    end_date: str,
    kind: str = "fund",
    period: str = "1d",
) -> list[dict] | None:
    """按**显式日期区间**取 K 线 (基金/股票/指数通用), 返回记录列表; 失败返回 None。

    与 `wind_get_kline(days=...)` 的区别: days 口径是"回看 N 条", 无法锁定固定研究窗口
    (2021-01-04 ~ 2026-08-20 这类证据基座必须可复现), 故新开本函数。

    容错: 服务端对日期风格不统一 (既有 `YYYYMMDD` 也有 `YYYY-MM-DD` 文档口径),
    故先按 `YYYYMMDD` 请求, 无数据再按 `YYYY-MM-DD` 重试; HTTP 失败自动回落 CLI。
    """
    domain = _KLINE_DOMAINS.get(kind)
    if not domain or not windcode:
        return None
    server_type, tool_name, endpoint = domain
    adjust_key, adjust_val = _KLINE_ADJUST_PARAM[kind]

    def _params(style: str) -> dict[str, Any]:
        params: dict[str, Any] = {
            "windcode": windcode,
            "begin_date": _normalize_date(begin_date, style),
            "end_date": _normalize_date(end_date, style),
            adjust_key: adjust_val,
        }
        if kind == "index":
            params["period"] = period
        return params

    for style in ("plain", "dash"):
        params = _params(style)
        api_key = _get_wind_api_key()
        if api_key:
            http_res = _wind_http_generic(endpoint, tool_name, params, api_key)
            if http_res.get("ok") and isinstance(http_res.get("data"), dict):
                records = _extract_kline_records(http_res["data"])
                if records:
                    return records
        res = _call_wind(server_type, tool_name, params)
        if res.get("ok"):
            records = _extract_kline_records(res.get("data") or {})
            if records:
                return records
    return None


def wind_get_index_kline(
    windcode: str, begin_date: str, end_date: str, period: str = "1d"
) -> list[dict] | None:
    """取指数日 K (如 000300.SH 沪深300); 便捷包装 `wind_get_kline_range(kind='index')`。"""
    return wind_get_kline_range(
        windcode, begin_date, end_date, kind="index", period=period
    )


def wind_get_delisted_kline(
    windcode: str,
    delist_date: str,
    lookback_days: int = 252 * 2,
    *,
    date_tolerance_days: int = 5,
) -> list[dict] | None:
    """取**已退市股票**摘牌前的真实日 K (幸存者偏差修复专用, P0-M1, 2026-09-13)。

    ⚠ 数据陷阱实测结论 (2026-09-13, 三只退市股验证):
        - `wind_get_kline(days=N)` 对退市代码**不可信**: 600532.SH (2023-06-28 摘牌)
          返回 2026-09-11 的"行情" (close=6.56, 4890 万手) — 服务端错误映射;
          300372.SZ 则正确返回 None。days 模式无法区分真假。
        - `wind_get_kline_range` 显式区间模式可靠: 600532/300372/600625 三只
          退市股区间数据均精确截止在摘牌日前最后交易日, 价格为真实仙股口径。
    因此退市股取数**必须**走区间模式, 并用摘牌日期做输出校验 (fail-closed):
    返回记录中任何日期晚于 摘牌日+容差 → 视为服务端脏数据, 整体返回 None。

    Args:
        windcode: 退市股 Wind 代码 (如 "600532.SH"; 名单经
            ``utils/universe/survivorship_free_universe.sync_delisted_from_wind``)
        delist_date: 摘牌日期 YYYY-MM-DD (search_stocks "摘牌日期" 列)
        lookback_days: 回看自然日近似 (默认约 2 年交易日; 内部按日历日 1.5x 展开)
        date_tolerance_days: 摘牌日容差天数 (数据源日期规整误差)

    Returns:
        K 线记录列表 (按时间升序, 全部落在摘牌日前); 校验失败/无数据返回 None
    """
    import datetime as _dt

    try:
        d_end = _dt.datetime.strptime(delist_date, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        logging.getLogger(__name__).warning(
            "[DelistedKline] %s 摘牌日期非法: %r", windcode, delist_date
        )
        return None
    d_begin = d_end - _dt.timedelta(days=int(lookback_days * 1.5))

    records = wind_get_kline_range(
        windcode,
        d_begin.strftime("%Y-%m-%d"),
        d_end.strftime("%Y-%m-%d"),
        kind="stock",
        period="1d",
    )
    if not records:
        return None

    # fail-closed 校验: 任何记录晚于 摘牌日+容差 → 服务端脏数据, 整体拒绝
    cutoff = d_end + _dt.timedelta(days=date_tolerance_days)
    for rec in records:
        raw = str(rec.get("TIME") or rec.get("time") or rec.get("_DATE") or "")
        day = raw[:10]
        try:
            rec_day = _dt.datetime.strptime(day[:10], "%Y-%m-%d").date()
        except ValueError:
            continue  # 无法解析的日期留待下游列映射处理
        if rec_day > cutoff:
            logging.getLogger(__name__).warning(
                "[DelistedKline] %s 返回记录 %s 晚于摘牌日 %s (+%d 天容差) — "
                "服务端脏数据, 整体拒绝 (幸存者偏差数据不可信)",
                windcode,
                day,
                delist_date,
                date_tolerance_days,
            )
            return None
    return records


def _extract_kline_records(data: dict) -> list[dict]:
    """从 Wind MCP 响应中提取 K 线记录 (CLI 和 HTTP 通用)

    支持多种返回格式:
        - CLI: {"content": [{"type": "text", "text": "..."}]}
        - HTTP SSE: {"result": {"content": [{"type": "text", "text": "..."}]}}
        - 直接 JSON: {"data": {"columns": [...], "rows": [...]}}
    """
    if not data:
        return []

    # 路径 1: MCP content[0].text 嵌套 JSON
    _content = (data.get("result") or data).get("content") or []
    if _content and isinstance(_content, list):
        _first = _content[0]
        if isinstance(_first, dict):
            content = _first.get("text") or ""
            if content:
                try:
                    parsed = json.loads(content)
                except Exception:
                    return []
                inner = parsed.get("data") or parsed.get("result") or parsed
                if isinstance(inner, dict):
                    columns = [c.get("name") for c in (inner.get("columns") or [])]
                    rows = inner.get("rows") or []
                    records = []
                    for row in rows:
                        if len(columns) == len(row):
                            records.append(dict(zip(columns, row, strict=True)))
                    return records
                # 也可能是 list 直接返回
                if isinstance(inner, list):
                    return inner

    # 路径 2: 直接 {data: {columns, rows}}
    inner_data = data.get("data") if isinstance(data, dict) else None
    if isinstance(inner_data, dict):
        columns = [c.get("name") for c in (inner_data.get("columns") or [])]
        rows = inner_data.get("rows") or []
        records = []
        for row in rows:
            if len(columns) == len(row):
                records.append(dict(zip(columns, row, strict=True)))
        return records

    return []


def fetch_realtime_price(windcode: str) -> float | None:
    """获取实时价格的便捷接口 (兼容旧调用方)

    Args:
        windcode: Wind 代码, 如 "600036.SH" 或 "588000.SZ"

    Returns:
        最新成交价 (float); 失败返回 None
    """
    # 自动识别 fund/stock: ETF/LOF/基金 以 1/5 开头
    is_fund = bool(windcode) and windcode[:1] in ("1", "5")
    quote = wind_get_quote(windcode, is_fund=is_fund)
    if not quote:
        return None
    price = quote.get("price")
    if price is None:
        # 尝试其他字段
        for key in ("latest", "close", "rt_last", "MATCH", "CLOSE"):
            if key in quote and quote[key] is not None:
                price = quote[key]
                break
    try:
        return float(price) if price is not None else None
    except (TypeError, ValueError):
        return None


# ============================================================
# 财经新闻搜索 (financial_docs 域)
# ============================================================
def wind_search_news(query: str, top_k: int = 20) -> list[dict]:
    """通过 Wind MCP financial_docs.get_financial_news 搜索财经新闻

    Args:
        query: 查询关键词 (如 "海光信息 688041" / "中国神华")
        top_k: 返回的新闻数量

    Returns:
        新闻列表 [{title, snippet, publish_time, source, ...}, ...]
        失败返回空列表
    """
    if not query:
        return []

    # 参数: query 必须不含空格 (Wind 要求), top_k 控制返回数量
    # 注意: Wind MCP 要求 query 去除所有空格
    normalized_query = "".join(query.split())
    params = {"query": normalized_query, "top_k": int(top_k)}
    api_key = _get_wind_api_key()

    # 优先 HTTP 直连 (快)
    if api_key:
        http_res = _wind_http(
            WIND_FINANCIAL_DOCS_ENDPOINT,
            "get_financial_news",
            params,
            api_key,
        )
        if http_res.get("ok"):
            parsed = _extract_news_items(http_res.get("data"))
            if parsed:
                return parsed
        # HTTP 失败, 回退到 CLI
        if http_res.get("error"):
            print(f"Wind HTTP (get_financial_news) 失败: {http_res.get('error')}")

    # CLI 兜底
    res = _call_wind("financial_docs", "get_financial_news", params)
    if not res.get("ok"):
        return []

    data = res.get("data") or {}
    return _extract_news_items(data)


def _extract_news_items(data: Any) -> list[dict]:
    """从 Wind MCP 响应中提取新闻条目

    Wind MCP 返回格式 (CLI / HTTP 两种):
        CLI: {"content": [{"type": "text", "text": "{\"data\": {\"items\": [...]}"}], "isError": false}
        HTTP/SSE: {"result": {"content": [{"type": "text", "text": "..."}]}}
        或嵌套: {"data": {"items": [...]}}
    """
    if not data:
        return []

    items = []

    def _parse_text_to_items(text: str) -> list[dict]:
        """解析 text 字段 (可能是 JSON 字符串) 为新闻列表"""
        if not text:
            return []
        try:
            inner = json.loads(text)
        except Exception:
            # 纯文本, 返回单条
            return [{"text": text, "title": text[:80]}]

        if isinstance(inner, dict):
            # 结构: {"data": {"items": [...]}} 或 {"data": {"columns":[],"rows":[]}}
            inner_data = inner.get("data") or inner.get("result") or {}
            if isinstance(inner_data, dict):
                # items 列表
                news_items = inner_data.get("items") or inner_data.get("news") or []
                if isinstance(news_items, list):
                    return [i for i in news_items if isinstance(i, dict)]
                # columns/rows 格式
                cols = [c.get("name") for c in (inner_data.get("columns") or [])]
                rows = inner_data.get("rows") or []
                if cols and rows:
                    return [
                        dict(zip(cols, row, strict=True))
                        for row in rows
                        if len(cols) == len(row)
                    ]
            elif isinstance(inner_data, list):
                return [i for i in inner_data if isinstance(i, dict)]
        elif isinstance(inner, list):
            return [i for i in inner if isinstance(i, dict)]
        return []

    # 结构1: CLI 格式 {"content": [{"type":"text","text":"..."}]}
    if isinstance(data, dict):
        content = data.get("content") or []
        if isinstance(content, list):
            for c in content:
                if not isinstance(c, dict):
                    continue
                text = c.get("text") or ""
                items.extend(_parse_text_to_items(text))

    # 结构2: SSE 格式 {"result": {"content": [{"type":"text","text":"..."}]}}
    if not items and isinstance(data, dict):
        result = data.get("result")
        if isinstance(result, dict):
            content = result.get("content") or []
            if isinstance(content, list):
                for c in content:
                    if not isinstance(c, dict):
                        continue
                    text = c.get("text") or ""
                    items.extend(_parse_text_to_items(text))

    # 结构3: 直接 {"data": {"items": [...]}}
    if not items and isinstance(data, dict):
        inner_data = data.get("data") or {}
        if isinstance(inner_data, dict):
            news_items = inner_data.get("items") or []
            if isinstance(news_items, list):
                items = [i for i in news_items if isinstance(i, dict)]

    # 标准化字段 (适配不同返回格式)
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            continue
        # 统一字段名: title / snippet / publish_time / source
        title = (
            item.get("title")
            or item.get("Title")
            or item.get("TITLE")
            or item.get("新闻标题")
            or ""
        )
        snippet = (
            item.get("snippet")
            or item.get("content")
            or item.get("Content")
            or item.get("text")
            or item.get("摘要")
            or item.get("新闻内容")
            or ""
        )
        publish_time = (
            item.get("publish_time")
            or item.get("publishTime")
            or item.get("PublishTime")
            or item.get("pub_time")
            or item.get("发布时间")
            or item.get("PublishDate")
            or item.get("date")
            or ""
        )
        source = item.get("source") or item.get("Source") or item.get("来源") or ""

        if not title and not snippet:
            continue

        normalized.append(
            {
                "title": str(title),
                "snippet": str(snippet),
                "publish_time": str(publish_time) if publish_time else "",
                "source": str(source),
            }
        )

    return normalized


# ============================================================
# EDB 宏观/行业经济指标 (economic_data 域)
# ============================================================
def _extract_edb_metrics(data: Any) -> list[dict]:
    """从 Wind EDB 响应中提取 metrics 列表

    economic_data 两个工具的返回结构:
        search_economic_indicator:
            {"metrics": [{code, name, unit, source, magnitude, updateDate, freq}, ...]}
        query_economic_indicator_data:
            {"metrics": [{"meta": {...8 字段...}, "date": [...], "value": [...]}, ...]}

    均包在 result.content[0].text (JSON 字符串) 内, 与 K 线/新闻一致。
    """

    def _dig(obj: Any) -> list[dict]:
        if isinstance(obj, list):
            return [x for x in obj if isinstance(x, dict)]
        if not isinstance(obj, dict):
            return []
        m = obj.get("metrics")
        if isinstance(m, list):
            return [x for x in m if isinstance(x, dict)]
        for key in ("data", "result"):
            sub = obj.get(key)
            if isinstance(sub, (dict, list)):
                found = _dig(sub)
                if found:
                    return found
        return []

    if not data:
        return []

    # 路径 1: MCP content[0].text 嵌套 JSON
    content = ((data.get("result") or data).get("content")) or []
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict):
            text = first.get("text") or ""
            if text:
                try:
                    return _dig(json.loads(text))
                except Exception:
                    return []

    # 路径 2: 直接 {"metrics": [...]} / {"data": {"metrics": [...]}}
    return _dig(data)


def _edb_call(tool_name: str, params: dict) -> dict:
    """调用 economic_data 域工具: HTTP 直连优先 (有 api_key), CLI 兜底"""
    api_key = _get_wind_api_key()
    if api_key:
        res = _wind_http_generic(WIND_ECONOMIC_ENDPOINT, tool_name, params, api_key)
        if res.get("ok"):
            return res
        if res.get("error"):
            import logging

            logging.getLogger(__name__).debug(
                "Wind HTTP (%s) 失败, 回退到 CLI: %s", tool_name, res.get("error")
            )
    return _call_wind("economic_data", tool_name, params)


def wind_search_economic_indicator(question: str) -> list[dict]:
    """检索 Wind EDB 指标元信息 (只找指标, 不取数)

    Args:
        question: 自然语言问句, 如 "秦皇岛港煤炭库存"; 不含时间范围

    Returns:
        指标元信息列表 [{code, name, unit, source, freq, updateDate, ...}, ...]
        失败返回空列表 (调用方按 fail-open 处理)
    """
    if not question or not question.strip():
        return []
    res = _edb_call("search_economic_indicator", {"question": question.strip()})
    if not res.get("ok"):
        return []
    return _extract_edb_metrics(res.get("data"))


def wind_query_economic_indicator(
    question: str,
    begin_date: str | None = None,
    end_date: str | None = None,
    observation: str | None = None,
) -> list[dict]:
    """取 Wind EDB 指标时间序列

    Args:
        question: 自然语言问句或指标代码 (多个代码用英文逗号分隔, 如 "S5103725,Z8948284")
        begin_date / end_date: yyyy-MM-dd, 成对出现, 与 observation 互斥
        observation: 近 N 期, 数字字符串 (如 "6"); 与日期范围互斥

    Returns:
        [{"meta": {...}, "date": [...], "value": [...]}, ...]
        失败返回空列表
    """
    if not question or not question.strip():
        return []

    params: dict[str, Any] = {"question": question.strip()}
    if begin_date and end_date:
        params["beginDate"] = begin_date
        params["endDate"] = end_date
    else:
        # 后端契约: 必须显式提供日期范围或 observation, 只给 question 会被拒绝
        params["observation"] = str(observation or "1")

    res = _edb_call("query_economic_indicator_data", params)
    if not res.get("ok"):
        return []
    return _extract_edb_metrics(res.get("data"))


# 显式声明对外接口 (方便 from wind_mcp_fetcher import *)
__all__ = [
    "fetch_realtime_price",  # 兼容旧接口
    "wind_get_batch_quotes",
    "wind_get_index_kline",
    "wind_get_kline",
    "wind_get_kline_range",
    "wind_get_quote",
    "wind_search_news",  # 财经新闻搜索
    "wind_search_economic_indicator",  # EDB 指标检索 (元信息)
    "wind_query_economic_indicator",  # EDB 指标时间序列
]
