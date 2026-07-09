# -*- coding: utf-8 -*-
"""
iFinD 期货实时行情自建命令封装

支持：
1. iFinD MCP 高频工具（stock/fund/index）
2. 直接 HTTP 调用 THS_RQ（用户提供的自建命令格式）
3. THS_BD 期货基础数据（合约资料、保证金、交易时间等）
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
from typing import Any, Dict, List, Optional

import requests


_IFIND_SKILL = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "skills", "ifind-finance-data"))
if _IFIND_SKILL not in sys.path:
    sys.path.insert(0, _IFIND_SKILL)

try:
    from call import call as _ifind_call, list_tools as _ifind_list_tools
    _HAS_IFIND_MCP = True
except Exception:
    _ifind_call = None
    _ifind_list_tools = None
    _HAS_IFIND_MCP = False


_INDICATOR_MAP = {
    "tradeDate": "trade_date",
    "tradeTime": "trade_time",
    "ms": "ms",
    "preClose": "prev_close",
    "open": "open",
    "high": "high",
    "low": "low",
    "latest": "latest",
    "latestVolume": "latest_volume",
    "avgPrice": "avg_price",
    "volume": "volume",
    "change": "change",
    "changeSettle": "change_settle",
    "changeRatio": "change_ratio",
    "changeRatioSettle": "change_ratio_settle",
    "increasePositionVol": "increase_position_vol",
    "preSettlement": "pre_settlement",
    "sellVolume": "sell_volume",
    "buyVolume": "buy_volume",
    "dailyIncreasePosition": "daily_increase_position",
    "swing": "swing",
    "latest_price": "latest_price",
    "settlement": "settlement",
    "dealDirection": "deal_direction",
    "dealtype": "deal_type",
    "openInterest": "open_interest",
    "positionDiff": "position_diff",
    "capitalFlow": "capital_flow",
    "capitalDeposition": "capital_deposition",
    "amplitude": "amplitude",
    "upperLimit": "upper_limit",
    "downLimit": "down_limit",
    "dealtypecode": "deal_type_code",
}

_BASE_INDICATOR_MAP = {
    "ths_future_code_future": "future_code",
    "ths_future_short_name_future": "short_name",
    "ths_thscode_future": "ths_code",
    "ths_month_contract_code_future": "month_contract_code",
    "ths_listed_status_future": "listed_status",
    "ths_sec_type_future": "sec_type",
    "ths_underlying_code_sif_future": "underlying_code",
    "ths_variety_type_future": "variety_type",
    "ths_td_variety_future": "td_variety",
    "ths_td_unit_future": "td_unit",
    "ths_contract_multiplier": "contract_multiplier",
    "ths_pricing_unit_future": "pricing_unit",
    "ths_currency_type_future": "currency_type",
    "ths_mini_chg_price_future": "mini_chg_price",
    "ths_chg_ratio_lmit_future": "chg_ratio_limit",
    "ths_trade_deposit_future": "trade_deposit",
    "ths_contract_long_deposit_future": "contract_long_deposit",
    "ths_contract_short_deposit_future": "contract_short_deposit",
    "ths_hedging_contract_long_deposit_future": "hedging_long_deposit",
    "ths_hedging_contract_short_deposit_future": "hedging_short_deposit",
    "ths_transaction_procedure_rate_future": "tx_procedure_rate",
    "ths_transaction_procedure_fee_future": "tx_procedure_fee",
    "ths_to_transaction_procedure_rate_future": "to_tx_procedure_rate",
    "ths_to_transaction_procedure_fee_future": "to_tx_procedure_fee",
    "ths_contract_listed_date_future": "listed_date",
    "ths_start_trade_date_future": "start_trade_date",
    "ths_last_td_date_future": "last_td_date",
    "ths_last_delivery_date_future": "last_delivery_date",
    "ths_latest_td_date_future": "latest_td_date",
    "ths_delivery_month_future": "delivery_month",
    "ths_listing_benchmark_price_future": "listing_benchmark_price",
    "ths_initial_td_deposit_future": "initial_td_deposit",
    "ths_contract_month_explain_future": "contract_month_explain",
    "ths_td_time_explain_future": "td_time_explain",
    "ths_last_td_date_explian_future": "last_td_date_explain",
    "ths_delivery_date_explain_future": "delivery_date_explain",
    "ths_exchange_short_name_future": "exchange_short_name",
    "ths_exchange_name_eng_future": "exchange_name_eng",
    "ths_contract_en_short_name_future": "contract_en_short_name",
    "ths_contract_en_name_future": "contract_en_name",
    "ths_open_time_night_future": "open_time_night",
    "ths_close_time_night_future": "close_time_night",
    "ths_open_time_day_future": "open_time_day",
    "ths_close_time_day_future": "close_time_day",
    "ths_rest_start_time_future": "rest_start_time",
    "ths_rest_end_time_future": "rest_end_time",
    "ths_close_time_am_future": "close_time_am",
    "ths_open_time_pm_future": "open_time_pm",
    "ths_remain_trade_days": "remain_trade_days",
    "ths_vol_ratio_future": "vol_ratio",
    "ths_tapi_future": "tapi",
    "ths_vma_future": "vma",
    "ths_vmacd_future": "vmacd",
    "ths_vosc_future": "vosc",
    "ths_vstd_future": "vstd",
    "ths_long_position_future": "long_position",
    "ths_long_position_change_future": "long_position_change",
    "ths_long_position_ratio_future": "long_position_ratio",
    "ths_short_position_future": "short_position",
    "ths_short_position_change_future": "short_position_change",
    "ths_short_position_ratio_future": "short_position_ratio",
    "ths_net_long_position_future": "net_long_position",
    "ths_net_long_position_change_future": "net_long_position_change",
    "ths_net_long_position_ratio_future": "net_long_position_ratio",
    "ths_net_short_position_future": "net_short_position",
    "ths_net_short_position_change_future": "net_short_position_change",
    "ths_net_short_position_ratio_future": "net_short_position_ratio",
    "ths_position_vol_future": "position_vol",
    "ths_vol_chg_future": "vol_chg",
    "ths_position_ratio_vol_future": "position_ratio_vol",
    "ths_oi_lname_future": "oi_lname",
    "ths_oi_sname_future": "oi_sname",
    "ths_oi_vname_future": "oi_vname",
    "ths_new_long_list_future": "new_long_list",
    "ths_net_long_list_future": "net_long_list",
    "ths_short_net_top_list_future": "short_net_top_list",
    "ths_net_short_list_future": "net_short_list",
    "ths_vol_new_top_list_future": "vol_new_top_list",
    "ths_vol_rank_future": "vol_rank",
    "ths_long_vol_future": "long_vol",
    "ths_long_vol_change_future": "long_vol_change",
    "ths_long_vol_rank_future": "long_vol_rank",
    "ths_short_vol_future": "short_vol",
    "ths_short_vol_change_future": "short_vol_change",
    "ths_short_vol_rank_future": "short_vol_rank",
    "ths_net_long_bill_future": "net_long_bill",
    "ths_net_long_bill_change_future": "net_long_bill_change",
    "ths_net_long_bill_rank_future": "net_long_bill_rank",
    "ths_net_shortg_bill_future": "net_short_bill",
    "ths_net_short_bill_change_future": "net_short_bill_change",
    "ths_net_short_bill_rank_future": "net_short_bill_rank",
}


def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def _make_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.proxies = {"http": None, "https": None}
    return session


_SESSION = _make_session()


def _try_mcp_highfreq(symbols: List[str], indicators: List[str]) -> Optional[Dict[str, Any]]:
    """尝试用现有 MCP 高频工具获取数据"""
    if not _HAS_IFIND_MCP:
        print("[DEBUG] MCP 高频工具不可用")
        return None

    def _server_for(symbol: str) -> str:
        s = str(symbol).upper()
        if s.startswith(("IC", "IF", "IH", "IM", "T", "TF", "TS", "TL")):
            return "index"
        if s.startswith(("51", "58", "15", "16")):
            return "fund"
        return "stock"

    grouped: Dict[str, List[str]] = {}
    for symbol in symbols:
        grouped.setdefault(_server_for(symbol), []).append(symbol)

    print(f"[DEBUG] MCP 分组结果: {grouped}")

    merged: Dict[str, Dict[str, Any]] = {}
    for server, group in grouped.items():
        tool_name = {
            "stock": "stock_highfreq_quotes",
            "fund": "fund_highfreq_quotes",
            "index": "index_highfreq_quotes",
        }.get(server)
        if not tool_name:
            continue
        try:
            res = _ifind_list_tools(server)
            tools = res.get("data", {}).get("result", {}).get("tools", [])
            if not any(isinstance(t, dict) and t.get("name") == tool_name for t in tools):
                print(f"[DEBUG] {server} 服务未找到 {tool_name}")
                continue
        except Exception:
            continue

        try:
            resp = _ifind_call(server, tool_name, {
                "symbols": ",".join(group),
                "indicators": ";".join(indicators),
                "data_mode": "real_time",
                "interval": 1,
            })
        except Exception:
            continue

        if not isinstance(resp, dict) or not resp.get("ok"):
            print(f"[DEBUG] {server} 调用返回失败: {resp}")
            continue

        data = resp.get("data") or {}
        result = data.get("result") or {}
        content = result.get("content") or [{}]
        text = content[0].get("text", "") if content else ""
        try:
            parsed = json.loads(text)
        except Exception:
            continue
        payload = parsed.get("data") or parsed
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        tables = payload.get("tables") or []
        if not tables:
            print(f"[DEBUG] {server} 返回空 tables")
            continue

        header = tables[0] if tables else []
        rows = tables[-1] if len(tables) > 1 else []
        if not header or not rows:
            print(f"[DEBUG] {server} tables 为空")
            continue

        index = {str(h): i for i, h in enumerate(header) if h is not None}
        for row in rows:
            if not isinstance(row, list):
                continue
            sym = row[index.get("symbol", 0)] if "symbol" in index else (row[0] if row else None)
            if not sym:
                continue
            item: Dict[str, Any] = {"symbol": sym, "source": f"ifind_mcp_{server}"}
            for ind in indicators:
                key = _INDICATOR_MAP.get(ind, ind)
                pos = index.get(ind)
                item[key] = _safe_float(row[pos]) if pos is not None else None
            merged[sym] = item

    return merged if merged else None


def _get_ifind_token() -> str:
    token = os.getenv("IFIND_TOKEN") or os.getenv("WIND_API_KEY") or ""
    if not token:
        print("[DEBUG] 未找到环境变量 IFIND_TOKEN / WIND_API_KEY")
        try:
            cfg_path = os.path.join(_IFIND_SKILL, "mcp_config.json")
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                token = cfg.get("auth_token", "")
                if token:
                    print("[DEBUG] 从 mcp_config.json 读取 token 成功")
        except Exception:
            pass
    else:
        print("[DEBUG] 使用环境变量 token")
    return token


def _build_ths_command(symbols: List[str], indicators: List[str]) -> str:
    symbols_str = ",".join(symbols)
    indicators_str = ";".join(indicators)
    return f"THS_RQ('{symbols_str}','{indicators_str}','','format:list')"


def _try_ths_http(cmd: str, token: str) -> Optional[Dict[str, Any]]:
    """尝试 iFinD HTTP 接口"""
    if not token:
        print("[DEBUG] THS_RQ token 为空，跳过 HTTP 请求")
        return None

    encoded_cmd = urllib.parse.quote(cmd)
    candidates = [
        "https://api.51ifind.com/api/command?token=" + urllib.parse.quote(token, safe="") + "&cmd=" + encoded_cmd + "&type=text",
        "https://api.51ifind.com/ths/command?token=" + urllib.parse.quote(token, safe="") + "&cmd=" + encoded_cmd,
        "https://open.51ifind.com/api/command?token=" + urllib.parse.quote(token, safe="") + "&cmd=" + encoded_cmd,
    ]

    for url in candidates:
        print(f"[DEBUG] 请求 THS_RQ URL: {url[:120]}...")
        try:
            resp = _SESSION.get(url, timeout=10, verify=False)
            print(f"[DEBUG] THS_RQ 状态码: {resp.status_code}")
            print(f"[DEBUG] THS_RQ 原始返回长度: {len(resp.text)}")
            if not resp.text or resp.text.strip() == "null":
                print("[DEBUG] THS_RQ 返回空")
                continue
            return resp.json()
        except Exception as e:
            print(f"[DEBUG] THS_RQ 请求失败: {e}")
            continue

    return None


def _parse_ths_response(raw: Any, indicators: List[str]) -> Optional[Dict[str, Any]]:
    """解析 THS 返回结果"""
    try:
        if isinstance(raw, str):
            payload = json.loads(raw)
        else:
            payload = raw
    except Exception:
        return None

    text = ""
    if isinstance(payload, dict):
        outer = payload.get("data") or {}
        text = (((outer.get("answer") or "").strip()) or "")
        if not text:
            result = outer.get("result") or {}
            content = result.get("content") or [{}]
            text = content[0].get("text", "") if content else ""
        if not text:
            for key in ("answer", "data", "result"):
                candidate = payload.get(key)
                if isinstance(candidate, str):
                    text = candidate
                    break
    if not text:
        print("[DEBUG] THS_RQ 未解析到文本内容")
        return None

    print(f"[DEBUG] THS_RQ 内层文本长度: {len(text)}")

    try:
        inner = json.loads(text)
    except Exception:
        return None

    tables = (((inner.get("data") or {}).get("tables")) or inner.get("tables") or [])
    if not tables:
        print("[DEBUG] THS_RQ 内层未找到 tables")
        return None

    header = tables[0] if tables else []
    rows = tables[-1] if len(tables) > 1 else []
    if not header or not rows:
        print("[DEBUG] THS_RQ tables 为空")
        return None

    index = {str(h): i for i, h in enumerate(header) if h is not None}
    result: Dict[str, Any] = {}
    for row in rows:
        if not isinstance(row, list):
            continue
        sym = row[index.get("symbol", 0)] if "symbol" in index else (row[0] if row else None)
        if not sym:
            continue
        item: Dict[str, Any] = {"symbol": sym, "source": "ifind_ths_rq"}
        for ind in indicators:
            key = _INDICATOR_MAP.get(ind, ind)
            pos = index.get(ind)
            item[key] = _safe_float(row[pos]) if pos is not None else None
        result[sym] = item

    return result if result else None


def fetch_futures_quotes(symbols: List[str]) -> Dict[str, Any]:
    """获取期货实时行情（优先 MCP 高频，再回退 THS_RQ）"""
    indicators = [
        "tradeDate", "tradeTime", "ms", "preClose", "open", "high", "low",
        "latest", "latestVolume", "avgPrice", "volume", "change", "changeSettle",
        "changeRatio", "changeRatioSettle", "increasePositionVol", "preSettlement",
        "sellVolume", "buyVolume", "dailyIncreasePosition", "swing", "latest_price",
        "settlement", "dealDirection", "dealtype", "openInterest", "positionDiff",
        "capitalFlow", "capitalDeposition", "amplitude", "upperLimit", "downLimit",
        "dealtypecode",
    ]

    print(f"[DEBUG] 请求合约: {symbols}")
    print(f"[DEBUG] 请求字段数: {len(indicators)}")

    data = _try_mcp_highfreq(symbols, indicators)
    if data:
        print("[DEBUG] 使用 MCP 高频数据")
        return data

    token = _get_ifind_token()
    if not token:
        print("[DEBUG] token 为空，返回空结果")
        return {}

    cmd = _build_ths_command(symbols, indicators)
    print(f"[DEBUG] THS_RQ 命令: {cmd}")
    raw = _try_ths_http(cmd, token)
    if not raw:
        print("[DEBUG] THS_RQ 无返回")
        return {}

    parsed = _parse_ths_response(raw, indicators)
    return parsed or {}


def _build_ths_bd_command(symbols: List[str], indicators: List[str]) -> str:
    symbols_str = ",".join(symbols)
    indicators_str = ";".join(indicators)
    return f"THS_BD('{symbols_str}','{indicators_str}',';;;2026-07-06;2026-07-06;;;100;;;2026-07-06;;;;2026-07-06;;;;2026-07-06;2026-07-06,100;2026-07-06;2026-07-06;2026-07-06;2026-07-06;2026-07-06;2026-07-06;2026-07-06;2026-07-06;;;2026-07-06;;2026-07-06;;;;;;;;;;;;;;;;;;;;2026-07-06,0;2026-07-06,5;2026-07-06,6,100;2026-07-06,5;2026-07-06,12,26,9,100;2026-07-06,12,26;2026-07-06,10;2026-07-06,1;2026-07-06,1;2026-07-06,1;2026-07-06,1;2026-07-06,1;2026-07-06,1;2026-07-06,1;2026-07-06,1;2026-07-06,1;2026-07-06,1;2026-07-06,1;2026-07-06,1;2026-07-06,1;2026-07-06,1;2026-07-06,1;2026-07-06,1;2026-07-06,1;2026-07-06,1;2026-07-06,1;2026-07-06,1;2026-07-06,1;2026-07-06,1;2026-07-06,1001;2026-07-06,1001;2026-07-06,1001;2026-07-06,1001;2026-07-06,1001;2026-07-06,1001;2026-07-06,1001;2026-07-06,1001;2026-07-06,1001;2026-07-06,1001;2026-07-06,1001;2026-07-06,1001;2026-07-06,1001')"


def _try_ths_bd_http(symbols: List[str], indicators: List[str]) -> Optional[Dict[str, Any]]:
    """尝试 iFinD THS_BD 基础数据接口"""
    token = _get_ifind_token()
    if not token:
        print("[DEBUG] THS_BD token 为空")
        return None

    cmd = _build_ths_bd_command(symbols, indicators)
    encoded_cmd = urllib.parse.quote(cmd)
    candidates = [
        "https://api.51ifind.com/api/command?token=" + urllib.parse.quote(token, safe="") + "&cmd=" + encoded_cmd + "&type=text",
        "https://api.51ifind.com/ths/command?token=" + urllib.parse.quote(token, safe="") + "&cmd=" + encoded_cmd,
        "https://open.51ifind.com/api/command?token=" + urllib.parse.quote(token, safe="") + "&cmd=" + encoded_cmd,
    ]

    for url in candidates:
        print(f"[DEBUG] 请求 THS_BD URL: {url[:120]}...")
        try:
            resp = _SESSION.get(url, timeout=10, verify=False)
            print(f"[DEBUG] THS_BD 状态码: {resp.status_code}")
            print(f"[DEBUG] THS_BD 原始返回长度: {len(resp.text)}")
            if not resp.text or resp.text.strip() == "null":
                print("[DEBUG] THS_BD 返回空")
                continue
            return resp.json()
        except Exception as e:
            print(f"[DEBUG] THS_BD 请求失败: {e}")
            continue

    return None


def _parse_ths_bd_response(raw: Any, indicators: List[str]) -> Optional[Dict[str, Any]]:
    """解析 THS_BD 返回结果"""
    try:
        if isinstance(raw, str):
            payload = json.loads(raw)
        else:
            payload = raw
    except Exception:
        return None

    text = ""
    if isinstance(payload, dict):
        outer = payload.get("data") or {}
        text = (((outer.get("answer") or "").strip()) or "")
        if not text:
            result = outer.get("result") or {}
            content = result.get("content") or [{}]
            text = content[0].get("text", "") if content else ""
        if not text:
            for key in ("answer", "data", "result"):
                candidate = payload.get(key)
                if isinstance(candidate, str):
                    text = candidate
                    break
    if not text:
        print("[DEBUG] THS_BD 未解析到文本内容")
        return None

    print(f"[DEBUG] THS_BD 内层文本长度: {len(text)}")

    try:
        inner = json.loads(text)
    except Exception:
        return None

    tables = (((inner.get("data") or {}).get("tables")) or inner.get("tables") or [])
    if not tables:
        print("[DEBUG] THS_BD 内层未找到 tables")
        return None

    header = tables[0] if tables else []
    rows = tables[-1] if len(tables) > 1 else []
    if not header or not rows:
        print("[DEBUG] THS_BD tables 为空")
        return None

    index = {str(h): i for i, h in enumerate(header) if h is not None}
    result: Dict[str, Any] = {}
    for row in rows:
        if not isinstance(row, list):
            continue
        sym = row[index.get("ths_future_code_future", 0)] if "ths_future_code_future" in index else (row[0] if row else None)
        if not sym:
            continue
        item: Dict[str, Any] = {"symbol": sym, "source": "ifind_ths_bd"}
        for ind in indicators:
            key = _BASE_INDICATOR_MAP.get(ind, ind)
            pos = index.get(ind)
            item[key] = _safe_float(row[pos]) if pos is not None else None
        result[sym] = item

    return result if result else None


def fetch_futures_base_info(symbols: List[str]) -> Dict[str, Any]:
    """获取期货基础数据（THS_BD）"""
    indicators = list(_BASE_INDICATOR_MAP.keys())

    print(f"[DEBUG] THS_BD 请求合约: {symbols}")
    print(f"[DEBUG] THS_BD 请求字段数: {len(indicators)}")

    token = _get_ifind_token()
    if not token:
        print("[DEBUG] THS_BD token 为空")
        return {}

    raw = _try_ths_bd_http(symbols, indicators)
    if not raw:
        print("[DEBUG] THS_BD 无返回")
        return {}

    parsed = _parse_ths_bd_response(raw, indicators)
    return parsed or {}


def fetch_futures_quotes(symbols: List[str]) -> Dict[str, Any]:
    """获取期货实时行情（优先 MCP 高频，再回退 THS_RQ）"""
    indicators = [
        "tradeDate", "tradeTime", "ms", "preClose", "open", "high", "low",
        "latest", "latestVolume", "avgPrice", "volume", "change", "changeSettle",
        "changeRatio", "changeRatioSettle", "increasePositionVol", "preSettlement",
        "sellVolume", "buyVolume", "dailyIncreasePosition", "swing", "latest_price",
        "settlement", "dealDirection", "dealtype", "openInterest", "positionDiff",
        "capitalFlow", "capitalDeposition", "amplitude", "upperLimit", "downLimit",
        "dealtypecode",
    ]

    print(f"[DEBUG] 请求合约: {symbols}")
    print(f"[DEBUG] 请求字段数: {len(indicators)}")

    data = _try_mcp_highfreq(symbols, indicators)
    if data:
        print("[DEBUG] 使用 MCP 高频数据")
        return data

    token = _get_ifind_token()
    if not token:
        print("[DEBUG] token 为空，返回空结果")
        return {}

    cmd = _build_ths_command(symbols, indicators)
    print(f"[DEBUG] THS_RQ 命令: {cmd}")
    raw = _try_ths_http(cmd, token)
    if not raw:
        print("[DEBUG] THS_RQ 无返回")
        return {}

    parsed = _parse_ths_response(raw, indicators)
    return parsed or {}


if __name__ == "__main__":
    demo = [
        "A00.DCE", "A01.DCE", "A02.DCE", "A03.DCE",
        "A2607.DCE", "A2609.DCE", "A2611.DCE", "A2701.DCE",
        "A2703.DCE", "A2705.DCE", "A8888.DCE", "AZL.DCE",
        "AZL1.DCE", "AZL2.DCE"
    ]
    quotes = fetch_futures_quotes(demo)
    print("\n===== 实时行情结果 =====")
    print(json.dumps(quotes, ensure_ascii=False, indent=2))

    base = fetch_futures_base_info(demo)
    print("\n===== 基础数据结果 =====")
    print(json.dumps(base, ensure_ascii=False, indent=2))