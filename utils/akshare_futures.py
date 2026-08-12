"""
期货数据统一接口

优先 Wind MCP（实时行情 + 基础数据），其次 iFinD，不可用时回退 AKShare，最终回退到直接 HTTP。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.parse import quote

import requests
import logging

logger = logging.getLogger(__name__)

# 强制禁用代理，避免系统代理 127.0.0.1:7897 导致外网请求失败
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"
os.environ["HTTP_PROXY"] = ""
os.environ["HTTPS_PROXY"] = ""
os.environ["http_proxy"] = ""
os.environ["https_proxy"] = ""

from ifind_futures_quotes import fetch_futures_base_info, fetch_futures_quotes

_IFIND_QUOTE_INDICATORS = [
    "tradeDate",
    "tradeTime",
    "ms",
    "preClose",
    "open",
    "high",
    "low",
    "latest",
    "latestVolume",
    "avgPrice",
    "volume",
    "change",
    "changeSettle",
    "changeRatio",
    "changeRatioSettle",
    "increasePositionVol",
    "preSettlement",
    "sellVolume",
    "buyVolume",
    "dailyIncreasePosition",
    "swing",
    "latest_price",
    "settlement",
    "dealDirection",
    "dealtype",
    "openInterest",
    "positionDiff",
    "capitalFlow",
    "capitalDeposition",
    "amplitude",
    "upperLimit",
    "downLimit",
    "dealtypecode",
]

_IFIND_BASE_INDICATORS = [
    "ths_future_code_future",
    "ths_future_short_name_future",
    "ths_contract_multiplier",
    "ths_td_unit_future",
    "ths_td_variety_future",
    "ths_variety_type_future",
    "ths_exchange_short_name_future",
    "ths_contract_listed_date_future",
    "ths_start_trade_date_future",
    "ths_last_td_date_future",
    "ths_last_delivery_date_future",
    "ths_open_time_day_future",
    "ths_close_time_day_future",
    "ths_open_time_night_future",
    "ths_close_time_night_future",
]


def _to_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError): # P2 模块 fail-safe, 待后续精确化
        return None


def _to_str(v: Any) -> str | None:
    try:
        if v is None:
            return None
        return str(v)
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError): # P2 模块 fail-safe, 待后续精确化
        return None


def _normalize_ak_quotes(df) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    try:
        records = df.to_dict(orient="records") if hasattr(df, "to_dict") else []
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError): # P2 模块 fail-safe, 待后续精确化
        records = []
    for row in records:
        sym = row.get("symbol") or row.get("合约代码") or row.get("代码")
        if not sym:
            continue
        result[str(sym)] = {
            "symbol": str(sym),
            "source": "akshare",
            "latest": _to_float(row.get("current_price") or row.get("最新价") or row.get("latest")),
            "open": _to_float(row.get("open") or row.get("开盘价")),
            "high": _to_float(row.get("high") or row.get("最高价")),
            "low": _to_float(row.get("low") or row.get("最低价")),
            "prev_close": _to_float(row.get("last_close") or row.get("前收盘价") or row.get("prev_close")),
            "volume": _to_float(row.get("volume") or row.get("成交量")),
            "open_interest": _to_float(row.get("hold") or row.get("持仓量") or row.get("openInterest")),
            "settlement": _to_float(row.get("last_settle_price") or row.get("结算价") or row.get("settlement")),
            "change_ratio": _to_float(row.get("涨跌幅") or row.get("changeRatio")),
        }
    return result


def _normalize_ak_daily(df) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    try:
        records = df.to_dict(orient="records") if hasattr(df, "to_dict") else []
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError): # P2 模块 fail-safe, 待后续精确化
        records = []
    for row in records:
        sym = row.get("symbol") or row.get("合约代码") or row.get("代码")
        if not sym:
            continue
        result[str(sym)] = {
            "symbol": str(sym),
            "source": "akshare_daily",
            "date": _to_str(row.get("date") or row.get("日期")),
            "open": _to_float(row.get("open") or row.get("开盘价")),
            "high": _to_float(row.get("high") or row.get("最高价")),
            "low": _to_float(row.get("low") or row.get("最低价")),
            "close": _to_float(row.get("close") or row.get("收盘价")),
            "volume": _to_float(row.get("volume") or row.get("成交量")),
            "open_interest": _to_float(row.get("open_interest") or row.get("持仓量")),
        }
    return result


def _try_http_futures_quotes(symbols: list[str]) -> dict[str, Any]:
    """直接 HTTP 回退：新浪/腾讯期货实时行情"""
    session = requests.Session()
    session.trust_env = False
    session.proxies = {"http": None, "https": None}
    result: dict[str, dict[str, Any]] = {}

    sina_codes = []
    tencent_codes = []
    for sym in symbols:
        code = sym.split(".")[0]
        sina_codes.append(f"nf_{code}")
        tencent_codes.append(quote(code, safe=""))

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/97.0.4692.71 Safari/537.36",
        "Referer": "https://vip.stock.finance.sina.com.cn/",
    }

    # 新浪期货
    try:
        url = f"https://hq.sinajs.cn/list={','.join(sina_codes)}"
        resp = session.get(url, headers=headers, timeout=10, verify=False)
        text = resp.text
        logger.info(f"[DEBUG] 新浪HTTP状态: {resp.status_code}, 长度: {len(text)}")
        for line in text.splitlines():
            m = re.search(r'var hq_str_nf_([^=]+)="(.+)"', line)
            if not m:
                continue
            raw_code, payload = m.group(1), m.group(2)
            parts = payload.split(",")
            if len(parts) < 30:
                continue
            try:
                result[raw_code] = {
                    "symbol": raw_code,
                    "source": "sina_http",
                    "open": _to_float(parts[2]),
                    "high": _to_float(parts[3]),
                    "low": _to_float(parts[4]),
                    "latest": _to_float(parts[8]),
                    "prev_close": _to_float(parts[5]),
                    "volume": _to_float(parts[14]),
                    "open_interest": _to_float(parts[13]),
                    "settlement": _to_float(parts[10]),
                    "change_ratio": None,
                }
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError): # P2 模块 fail-safe, 待后续精确化
                continue
        if result:
            return result
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # P2 模块 fail-safe, 待后续精确化
        logger.error(f"[DEBUG] 新浪HTTP失败: {e}")

    # 腾讯期货
    try:
        url = f"https://qt.gtimg.cn/q={','.join(tencent_codes)}"
        resp = session.get(url, headers=headers, timeout=10, verify=False)
        text = resp.text
        logger.info(f"[DEBUG] 腾讯HTTP状态: {resp.status_code}, 长度: {len(text)}")
        for line in text.splitlines():
            m = re.search(r'v_(.+)="(.+)"', line)
            if not m:
                continue
            raw_code, payload = m.group(1), m.group(2)
            parts = payload.split("~")
            if len(parts) < 20:
                continue
            try:
                result[raw_code] = {
                    "symbol": raw_code,
                    "source": "tencent_http",
                    "open": _to_float(parts[5]),
                    "high": _to_float(parts[33]),
                    "low": _to_float(parts[34]),
                    "latest": _to_float(parts[3]),
                    "prev_close": _to_float(parts[4]),
                    "volume": _to_float(parts[36]),
                    "open_interest": _to_float(parts[37]),
                    "settlement": _to_float(parts[4]),
                    "change_ratio": _to_float(parts[32]),
                }
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError): # P2 模块 fail-safe, 待后续精确化
                continue
        if result:
            return result
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # P2 模块 fail-safe, 待后续精确化
        logger.error(f"[DEBUG] 腾讯HTTP失败: {e}")

    return {}


def _try_wind_futures_quotes(symbols: list[str]) -> dict[str, Any]:
    """Wind MCP 期货实时行情（优先数据源）"""
    try:
        import os
        import sys

        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from wind_mcp_fetcher import wind_get_quote

        result: dict[str, dict[str, Any]] = {}
        for sym in symbols:
            try:
                wind_code = sym.split(".")[0]
                data = wind_get_quote(wind_code)
                if data and data.get("data"):
                    inner = data["data"]
                    result[sym] = {
                        "symbol": sym,
                        "source": "wind_mcp",
                        "latest": float(inner.get("last", 0) or inner.get("price", 0)),
                        "open": float(inner.get("open", 0)),
                        "high": float(inner.get("high", 0)),
                        "low": float(inner.get("low", 0)),
                        "prev_close": float(inner.get("prev_close", 0) or inner.get("last_close", 0)),
                        "volume": float(inner.get("volume", 0)),
                        "open_interest": float(inner.get("open_interest", 0) or 0),
                        "settlement": float(inner.get("settlement", 0) or 0),
                        "change_ratio": float(inner.get("change_pct", 0) or inner.get("pct_change", 0)),
                    }
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # P2 模块 fail-safe, 待后续精确化
                logger.error(f"[DEBUG] Wind MCP 期货 {sym} 失败: {e}")
                continue
        if result:
            logger.info(f"[DEBUG] Wind MCP 期货返回 {len(result)} 个标的")
            return result
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # P2 模块 fail-safe, 待后续精确化
        logger.info(f"[DEBUG] Wind MCP 期货接口不可用: {e}")
    return {}


def get_futures_realtime(symbols: list[str]) -> dict[str, Any]:
    quotes = _try_wind_futures_quotes(symbols)
    if quotes:
        return quotes

    quotes = fetch_futures_quotes(symbols)
    if quotes:
        return quotes  # type: ignore
    logger.info("[DEBUG] Wind MCP/iFinD 不可用，回退 AKShare 实时行情")
    try:
        import akshare as ak

        seen = set()
        candidates: list[tuple] = []
        if hasattr(ak, "futures_zh_spot"):
            candidates.append(("futures_zh_spot", {"symbol": symbols[0].split(".")[0], "market": "CF"}))
            seen.add("futures_zh_spot")
        if hasattr(ak, "futures_spot_em"):
            candidates.append(("futures_spot_em", {"symbol": ", ".join(symbols)}))
            seen.add("futures_spot_em")
        if hasattr(ak, "futures_zh_realtime") and "futures_zh_spot" not in seen:
            candidates.append(("futures_zh_realtime", {"symbol": symbols[0].split(".")[0]}))
        if hasattr(ak, "futures_zh_daily") and "futures_zh_spot" not in seen:
            candidates.append(("futures_zh_daily", {"symbol": symbols[0].split(".")[0], "market": "CF"}))

        for func_name, kwargs in candidates:
            try:
                df = getattr(ak, func_name)(**kwargs)
                logger.info(f"[DEBUG] AKShare {func_name} 返回: empty={getattr(df, 'empty', 'n/a')}")
                if df is not None and not (hasattr(df, "empty") and df.empty):
                    return _normalize_ak_quotes(df)
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # P2 模块 fail-safe, 待后续精确化
                logger.error(f"[DEBUG] AKShare {func_name} 失败: {e}")
                continue
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # P2 模块 fail-safe, 待后续精确化
        logger.error(f"[DEBUG] AKShare 导入失败: {e}")

    logger.error("[DEBUG] AKShare 实时行情全部失败，回退 HTTP")
    return _try_http_futures_quotes(symbols)


def get_futures_daily(symbol: str, market: str = "CF") -> dict[str, Any]:
    try:
        import akshare as ak

        # 主力日K
        df = ak.futures_main_sina(symbol=symbol)
        logger.info(f"[DEBUG] AKShare 日K 返回: empty={getattr(df, 'empty', 'n/a')}")
        if df is None or (hasattr(df, "empty") and df.empty):
            return {}

        df = df.copy()
        df["symbol"] = symbol
        return _normalize_ak_daily(df)
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # P2 模块 fail-safe, 待后续精确化
        logger.error(f"[DEBUG] AKShare 日K 失败: {e}")
        return {}


def get_futures_base_info(symbols: list[str]) -> dict[str, Any]:
    info = fetch_futures_base_info(symbols)
    if info:
        return info  # type: ignore
    logger.info("[DEBUG] iFinD 基础数据不可用，AKShare 无直接基础数据接口")
    return {}


if __name__ == "__main__":
    demo = [
        "A00.DCE",
        "A01.DCE",
        "A02.DCE",
        "A03.DCE",
        "A2607.DCE",
        "A2609.DCE",
        "A2611.DCE",
        "A2701.DCE",
        "A2703.DCE",
        "A2705.DCE",
        "A8888.DCE",
        "AZL.DCE",
        "AZL1.DCE",
        "AZL2.DCE",
    ]
    quotes = get_futures_realtime(demo)
    logger.info("\n===== 实时行情 =====")
    logger.info(json.dumps(quotes, ensure_ascii=False, indent=2))

    base = get_futures_base_info(demo)
    logger.info("\n===== 基础数据 =====")
    logger.info(json.dumps(base, ensure_ascii=False, indent=2))

    daily = get_futures_daily("A2609")
    logger.info("\n===== 豆一2609日K =====")
    logger.info(json.dumps(daily, ensure_ascii=False, indent=2))
