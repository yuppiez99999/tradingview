# -*- coding: utf-8 -*-
"""
基于真实持仓生成收盘行情报告。
数据源：iFinD MCP > Wind MCP > 新浪 HTTP
"""
import os
import json
import sys
import requests
import importlib.util
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from utils.ifind_client import IFindClient  # noqa: E402

# Wind MCP
_WIND_FETCHER_PATH = os.path.join(REPO_ROOT, "wind_mcp_fetcher.py")
_wind_get_batch_quotes = None
if os.path.isfile(_WIND_FETCHER_PATH):
    try:
        spec = importlib.util.spec_from_file_location("wind_mcp_fetcher", _WIND_FETCHER_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _wind_get_batch_quotes = getattr(mod, "wind_get_batch_quotes", None)
    except Exception as e:
        print("wind_import_error=", repr(e))

POSITIONS_PATH = os.path.join(REPO_ROOT, "config", "positions.json")
OUTPUT_DIR = os.path.join(REPO_ROOT, "realtime_monitor")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def load_positions():
    with open(POSITIONS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    positions = data.get("positions", {})
    universe = []
    for code, pos in positions.items():
        universe.append({
            "code": code,
            "name": pos.get("name", ""),
            "shares": pos.get("shares", 0),
            "avg_cost": pos.get("avg_cost", 0.0),
            "target_weight": pos.get("target_weight", 0.0),
            "amount": pos.get("amount", 0.0),
            "style": pos.get("style", ""),
            "sector": pos.get("sector", ""),
            "type": pos.get("type", ""),
        })
    return universe


def _to_wind_code(code: str) -> str:
    s = str(code).strip()
    for suffix in (".SH", ".SZ", ".BJ", ".sh", ".sz", ".bj"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    if not s:
        return s
    if s.startswith(("51", "58")):
        return f"{s}.SH"
    if s.startswith(("15", "16")):
        return f"{s}.SZ"
    if s.startswith(("00", "30")):
        return f"{s}.SZ"
    if s.startswith(("6",)):
        return f"{s}.SH"
    if s.startswith(("4", "8")):
        return f"{s}.BJ"
    return f"{s}.SH"


def _normalize_wind_code(code: str) -> str:
    s = str(code).strip()
    for prefix in ("sh", "sz", "bj", "SH", "SZ", "BJ"):
        if s.startswith(prefix):
            return s
    prefix = "sh"
    if s.startswith(("0", "3")):
        prefix = "sz"
    elif s.startswith(("4", "8")):
        prefix = "bj"
    return f"{prefix}{s}"


def fetch_wind_snapshot(codes: list, is_fund: bool = False) -> list:
    if _wind_get_batch_quotes is None:
        return []
    wind_codes = [_to_wind_code(c) for c in codes]
    try:
        batch = _wind_get_batch_quotes(wind_codes, is_fund=is_fund)
    except Exception as e:
        print("wind_batch_error=", repr(e))
        return []
    if not isinstance(batch, dict):
        return []
    results = []
    for code, quote in batch.items():
        if not quote or not isinstance(quote, dict):
            continue
        results.append({
            "code": _normalize_wind_code(code),
            "name": quote.get("name", code),
            "time": quote.get("time", ""),
            "latest": quote.get("price"),
            "change_ratio": quote.get("change"),
            "amount": quote.get("amount"),
            "volume": quote.get("volume"),
            "chg_1min": "",
            "chg_3min": "",
            "chg_5min": "",
            "source": "wind_mcp",
        })
    return results


def _to_sina_code(code: str) -> str:
    if code.endswith(".SH"):
        return f"sh{code[:6]}"
    if code.endswith(".SZ"):
        return f"sz{code[:6]}"
    if code.startswith("sh") or code.startswith("sz"):
        return code
    if code.startswith("6"):
        return f"sh{code}"
    return f"sz{code}"


def _fetch_sina_realtime(codes):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0 Safari/537.36",
        "Referer": "https://finance.sina.com.cn/",
    }
    url = f"https://hq.sinajs.cn/list={','.join(codes)}"
    print("sina_url=", url)
    try:
        resp = requests.get(url, timeout=10, headers=headers, verify=False, proxies={"http": None, "https": None})
        text = resp.text.strip()
        print("sina_status=", resp.status_code, "text_head=", repr(text[:400]))
    except Exception as e:
        print("sina_error=", repr(e))
        return []

    results = []
    now_str = datetime.now().isoformat()
    for line in text.split("\n"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key.startswith("var hq_str_"):
            continue
        raw_code = key.replace("var hq_str_", "")
        value = value.strip().strip('";')
        parts = value.split(",")
        print("sina_code=", raw_code, "parts=", len(parts), "sample=", parts[:6])
        if len(parts) < 32:
            continue
        name = parts[0].strip()
        latest = parts[3] if parts[3] else ""
        pre_close = parts[2] if parts[2] else ""
        change_ratio = ""
        try:
            if latest and pre_close:
                change_ratio = f"{(float(latest) - float(pre_close)) / float(pre_close) * 100:.6f}"
        except Exception as e:
            change_ratio = ""
        results.append({
            "code": raw_code,
            "name": name,
            "time": now_str,
            "latest": latest,
            "change_ratio": change_ratio,
            "amount": parts[9] if len(parts) > 9 else "",
            "volume": parts[8] if len(parts) > 8 else "",
            "chg_1min": "",
            "chg_3min": "",
            "chg_5min": "",
            "source": "sina_realtime",
        })
    return results


def fetch_stock_snapshot(client: IFindClient, symbols: str) -> list:
    result = client.call("stock", "stock_highfreq_quotes", {
        "data_mode": "real_time",
        "indicators": "最新价,涨跌幅,成交额,成交量,1分钟涨跌幅,3分钟涨跌幅,5分钟涨跌幅",
        "symbols": symbols,
    })
    if result.get("ok"):
        data = result.get("data", {})
        content = data.get("result", {}).get("content", [])
        texts = [item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"]
        raw = texts[0] if texts else ""
        try:
            payload = json.loads(raw)
            tables = payload.get("tables", [])
            if tables and len(tables) >= 2:
                headers = tables[0]
                rows = []
                for row in tables[1:]:
                    item = dict(zip(headers, row))
                    rows.append({
                        "code": item.get("证券代码", ""),
                        "name": item.get("证券简称", ""),
                        "time": item.get("time", ""),
                        "latest": item.get("最新价"),
                        "change_ratio": item.get("涨跌幅"),
                        "amount": item.get("成交额"),
                        "volume": item.get("成交量"),
                        "chg_1min": item.get("1分钟涨跌幅"),
                        "chg_3min": item.get("3分钟涨跌幅"),
                        "chg_5min": item.get("5分钟涨跌幅"),
                        "source": "iFinD_stock",
                    })
                return rows
        except Exception as e:
            raise  # Re-raise unknown exception
    print("stock_ifind_failed=", result.get("error", ""))
    return []


def fetch_fund_snapshot(client: IFindClient, symbols: str) -> list:
    result = client.call("fund", "fund_highfreq_quotes", {
        "data_mode": "real_time",
        "indicators": "最新价,涨跌幅,成交额,成交量,IOPV净值估值,振幅,折价",
        "symbols": symbols,
    })
    if result.get("ok"):
        data = result.get("data", {})
        content = data.get("result", {}).get("content", [])
        texts = [item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"]
        raw = texts[0] if texts else ""
        try:
            payload = json.loads(raw)
            tables = payload.get("tables", [])
            if tables and len(tables) >= 2:
                headers = tables[0]
                rows = []
                for row in tables[1:]:
                    item = dict(zip(headers, row))
                    rows.append({
                        "code": item.get("证券代码", ""),
                        "name": item.get("证券简称", ""),
                        "time": item.get("time", ""),
                        "latest": item.get("最新价"),
                        "change_ratio": item.get("涨跌幅"),
                        "amount": item.get("成交额"),
                        "volume": item.get("成交量"),
                        "iopv": item.get("IOPV（净值估值）"),
                        "swing": item.get("振幅"),
                        "premium": item.get("折价"),
                        "source": "iFinD_fund",
                    })
                return rows
        except Exception as e:
            raise  # Re-raise unknown exception
    print("fund_ifind_failed=", result.get("error", ""))
    return []


def main():
    universe = load_positions()
    if not universe:
        print("positions.json is empty")
        sys.exit(1)

    stocks = [p for p in universe if p["type"] == "STOCK"]
    funds = [p for p in universe if p["type"] == "ETF"]
    stock_symbols = ",".join([p["code"] for p in stocks])
    fund_symbols = ",".join([p["code"] for p in funds])

    date_str = datetime.now().strftime("%Y-%m-%d")
    token = os.getenv("IFIND_TOKEN", "")
    if not token:
        print("IFIND_TOKEN is empty")
        sys.exit(1)
    # 安全修复: IFindClient 构造函数从环境变量自动读取 Token
    client = IFindClient(max_concurrency=2)

    stock_rows = fetch_stock_snapshot(client, stock_symbols)
    fund_rows = fetch_fund_snapshot(client, fund_symbols)

    # iFinD MCP -> Wind MCP -> 新浪 HTTP fallback
    if not stock_rows:
        print("stock_fallback=wind")
        stock_rows = fetch_wind_snapshot([p["code"] for p in stocks], is_fund=False)
    if not stock_rows:
        print("stock_fallback=sina")
        stock_rows = _fetch_sina_realtime([_to_sina_code(p["code"]) for p in stocks])

    if not fund_rows:
        print("fund_fallback=wind")
        fund_rows = fetch_wind_snapshot([p["code"] for p in funds], is_fund=True)
    if not fund_rows:
        print("fund_fallback=sina")
        fund_rows = _fetch_sina_realtime([_to_sina_code(p["code"]) for p in funds])

    # 合并持仓信息
    position_map = {p["code"]: p for p in universe}
    snapshot = []
    for row in stock_rows + fund_rows:
        code = row.get("code", "")
        pos = position_map.get(code, {})
        snapshot.append({
            "code": code,
            "name": row.get("name", pos.get("name", "")),
            "type": pos.get("type", ""),
            "style": pos.get("style", ""),
            "shares": pos.get("shares", 0),
            "avg_cost": pos.get("avg_cost", 0.0),
            "target_weight": pos.get("target_weight", 0.0),
            "latest": row.get("latest", ""),
            "change_ratio": row.get("change_ratio", ""),
            "amount": row.get("amount", ""),
            "volume": row.get("volume", ""),
            "source": row.get("source", ""),
            "time": row.get("time", ""),
        })

    path = os.path.join(OUTPUT_DIR, f"realtime_positions_{date_str}.json")
    payload = {
        "date": date_str,
        "updated_at": datetime.now().isoformat(),
        "count": len(snapshot),
        "items": snapshot,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"saved={path}")
    print(f"stocks={len(stock_rows)} etfs={len(fund_rows)} total={len(snapshot)}")
    for item in snapshot:
        print(item)


if __name__ == "__main__":
    main()
