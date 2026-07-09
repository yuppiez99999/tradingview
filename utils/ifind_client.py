"""
iFinD MCP API 客户端 — 同花顺金融数据服务
协议: JSON-RPC 2.0 over HTTPS + MCP Session

服务类型:
  - stock: A股股票（不支持ETF）
  - fund: 基金/ETF（净值、涨跌幅、历史）
  - edb: 宏观/行业经济指标
  - index: 指数板块行情
  - bond: 债券
  - news: 新闻公告
  - global_stock: 港美股
  - futures: 期货实时行情（支持 THS_RQ 接口字段）

注意: ETF 必须使用 fund 服务，stock 服务不支持 ETF 代码
"""

import json
import re
import time
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_IFIND_SESSION = requests.Session()
_IFIND_SESSION.trust_env = False
_IFIND_SESSION.proxies = {"http": None, "https": None}

BASE = "https://api-mcp.51ifind.com:8643/ds-mcp-servers"
SERVERS = {
    "stock": f"{BASE}/hexin-ifind-ds-stock-mcp",
    "fund": f"{BASE}/hexin-ifind-ds-fund-mcp",
    "edb": f"{BASE}/hexin-ifind-ds-edb-mcp",
    "news": f"{BASE}/hexin-ifind-ds-news-mcp",
    "bond": f"{BASE}/hexin-ifind-ds-bond-mcp",
    "global_stock": f"{BASE}/hexin-ifind-ds-global-stock-mcp",
    "index": f"{BASE}/hexin-ifind-ds-index-mcp",
    "futures": f"{BASE}/hexin-ifind-ds-futures-mcp",
}


def _parse_markdown_table(text: str) -> List[Dict[str, str]]:
    if not text:
        return []
    lines = text.strip().split("\n")
    if len(lines) < 2:
        return []

    header_idx = -1
    for i, line in enumerate(lines):
        if "|" in line and not line.strip().startswith("#"):
            header_idx = i
            break
    if header_idx < 0:
        return []

    sep_idx = header_idx + 1
    if sep_idx < len(lines) and re.match(r'^[\s\|:\-]+$', lines[sep_idx].strip()):
        data_start = sep_idx + 1
    else:
        data_start = header_idx + 1

    raw_headers = [h.strip() for h in lines[header_idx].split("|")]
    raw_headers = [h for h in raw_headers if h]
    headers = [re.sub(r'（[^）]*）', '', h).strip() for h in raw_headers]

    rows = []
    for line in lines[data_start:]:
        line = line.strip()
        if not line or line.startswith("#"):
            if line.startswith("#"):
                break
            continue
        cells = [c.strip() for c in line.split("|")]
        if cells and not cells[0]:
            cells = cells[1:]
        if cells and not cells[-1]:
            cells = cells[:-1]

        if not cells:
            continue

        row = {}
        for j, h in enumerate(headers):
            if j < len(cells):
                row[h] = cells[j]
        if row:
            rows.append(row)
    return rows


def _col(row: Dict[str, str], *keywords: str) -> Optional[str]:
    for k, v in row.items():
        for kw in keywords:
            if k == kw:
                return v
            if k.startswith(kw + "（"):
                return v
    for k, v in row.items():
        for kw in keywords:
            if k.startswith(kw) and kw not in row:
                return v
    return None


def _parse_ifind_response(result: Dict) -> Dict[str, Any]:
    out = {"text": "", "tables": [], "datas": []}

    try:
        content = result.get("data", {}).get("result", {}).get("content", [])
    except (KeyError, AttributeError):
        return out

    if not content:
        return out

    for item in content:
        if item.get("type") != "text":
            continue
        raw = item.get("text", "")
        out["text"] += raw + "\n"

        try:
            inner = json.loads(raw)
        except json.JSONDecodeError:
            continue

        if inner.get("code") != 1:
            continue

        data = inner.get("data", {})

        for d in data.get("datas", []):
            if d.get("success"):
                out["datas"].append(d.get("data", {}))

        for key in ["answer1", "answer", "text"]:
            val = data.get(key, "")
            if not val:
                continue
            if isinstance(val, str) and "|" in val:
                table = _parse_markdown_table(val)
                if table:
                    out["tables"].extend(table)
                    continue
            if isinstance(val, str):
                val = val.strip()
            if isinstance(val, str) and val.startswith("[") and val.endswith("]"):
                try:
                    arr = json.loads(val)
                    if isinstance(arr, list):
                        out["tables"].extend([_normalize_row(r) for r in arr if isinstance(r, dict)])
                        continue
                except json.JSONDecodeError:
                    pass
            if isinstance(val, list):
                out["tables"].extend([_normalize_row(r) for r in val if isinstance(r, dict)])

    return out


def _normalize_row(row: Dict[str, Any]) -> Dict[str, str]:
    return {str(k): str(v) if v is not None else "" for k, v in row.items()}


class IFindClient:
    def __init__(self, auth_token: str = "", max_concurrency: int = 2):
        self.auth_token = auth_token
        self.max_concurrency = max_concurrency
        self._sessions: Dict[str, str] = {}
        self._req_ids: Dict[str, int] = {}
        self._lock = threading.Lock()
        self._semaphore = threading.Semaphore(max_concurrency)
        self._last_request_time: Dict[str, float] = {}
        self.call_count = 0
        self.error_count = 0
        self.last_success: float = 0
        self._quota_exceeded: Dict[str, float] = {}
        self._quota_retry_delay = 3600

    def configure(self, auth_token: str, max_concurrency: int = 2):
        self.auth_token = auth_token
        self.max_concurrency = max_concurrency
        self._semaphore = threading.Semaphore(max_concurrency)
        self._sessions.clear()

    def _next_id(self, t: str) -> int:
        self._req_ids[t] = self._req_ids.get(t, 0) + 1
        return self._req_ids[t]

    def _headers(self, t: str = None) -> Dict:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": self.auth_token,
        }
        if t and t in self._sessions:
            h["Mcp-Session-Id"] = self._sessions[t]
        return h

    def _rate_limit(self, t: str):
        now = time.time()
        last = self._last_request_time.get(t, 0)
        gap = now - last
        if gap < 0.5:
            time.sleep(0.5 - gap)
        self._last_request_time[t] = time.time()

    def _init(self, server_type: str):
        if server_type in self._sessions:
            return
        with self._lock:
            if server_type in self._sessions:
                return

            payload = {
                "jsonrpc": "2.0",
                "id": self._next_id(server_type),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "auto-trading", "version": "1.0.0"},
                },
            }

            resp = _IFIND_SESSION.post(
                SERVERS[server_type], json=payload,
                headers=self._headers(), verify=False, timeout=30,
            )
            resp.raise_for_status()

            session_id = resp.headers.get("Mcp-Session-Id")
            if not session_id:
                raise RuntimeError(f"initialize 未返回 Mcp-Session-Id")

            self._sessions[server_type] = session_id

            notify = {"jsonrpc": "2.0", "method": "notifications/initialized"}
            _IFIND_SESSION.post(
                SERVERS[server_type], json=notify,
                headers=self._headers(server_type), verify=False, timeout=10,
            )

    def call(self, server_type: str, tool_name: str, params: Dict) -> Dict:
        if server_type not in SERVERS:
            return {"ok": False, "error": f"unknown server_type: {server_type}"}

        now = time.time()
        if server_type in self._quota_exceeded:
            if now - self._quota_exceeded[server_type] < self._quota_retry_delay:
                return {"ok": False, "error": f"quota exceeded, retry after {self._quota_retry_delay}s", "quota_exceeded": True}
            else:
                del self._quota_exceeded[server_type]

        self._init(server_type)
        self._rate_limit(server_type)

        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(server_type),
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": params},
        }

        with self._semaphore:
            try:
                resp = _IFIND_SESSION.post(
                    SERVERS[server_type], json=payload,
                    headers=self._headers(server_type), verify=False, timeout=60,
                )
                self.call_count += 1
            except requests.RequestException as e:
                self.error_count += 1
                return {"ok": False, "error": str(e)}

        data = None
        if resp.text.strip():
            try:
                data = resp.json()
            except Exception:
                data = resp.text

        if isinstance(data, dict) and "error" in data:
            self.error_count += 1
            return {"ok": False, "error": data["error"], "raw": data}

        try:
            resp.raise_for_status()
        except requests.HTTPError as e:
            self.error_count += 1
            return {"ok": False, "error": str(e), "status_code": resp.status_code}

        try:
            content = data.get('result', {}).get('content', [])
            for item in content:
                text = item.get('text', '')
                if '超限' in text or 'quota' in text.lower() or 'limit' in text.lower():
                    self._quota_exceeded[server_type] = now
                    return {"ok": False, "error": "用户使用工具已超限", "quota_exceeded": True, "data": data}
        except Exception:
            pass

        self.last_success = time.time()
        return {"ok": True, "status_code": resp.status_code, "data": data}

    def get_historical_klines(self, code: str, days: int = 252) -> Optional[List[Dict]]:
        if code.startswith('5'):
            return self._get_fund_historical(code, days)
        else:
            return self._get_stock_historical(code, days)

    def _get_stock_historical(self, code: str, days: int = 252) -> Optional[List[Dict]]:
        all_rows = []
        seen_dates = set()

        end_date = datetime.now()
        chunks_needed = max(1, days // 60)

        for i in range(chunks_needed):
            chunk_end = end_date - timedelta(days=i * 120)
            chunk_start = chunk_end - timedelta(days=130)
            s = chunk_start.strftime("%Y%m%d")
            e = chunk_end.strftime("%Y%m%d")

            result = self.call("stock", "get_stock_performance", {
                "query": f"{code}从{s}到{e}的开盘价、收盘价、最高价、最低价、成交量"
            })
            parsed = _parse_ifind_response(result)
            for row in parsed.get("tables", []):
                date_str = (_col(row, "日期", "date", "Date", "DATE") or "").strip()
                if not date_str or date_str in seen_dates:
                    continue
                seen_dates.add(date_str)
                try:
                    open_str = _col(row, "开盘价", "开盘", "open", "Open", "OPEN") or "0"
                    close_str = _col(row, "收盘价", "收盘", "close", "Close", "CLOSE") or "0"
                    high_str = _col(row, "最高价", "最高", "high", "High", "HIGH") or "0"
                    low_str = _col(row, "最低价", "最低", "low", "Low", "LOW") or "0"
                    vol_str = _col(row, "成交量", "volume", "Volume", "VOLUME", "成交股数", "成交额") or "0"
                    all_rows.append({
                        "日期": date_str,
                        "开盘价": float(open_str) if open_str else 0,
                        "收盘价": float(close_str) if close_str else 0,
                        "最高价": float(high_str) if high_str else 0,
                        "最低价": float(low_str) if low_str else 0,
                        "成交量": float(vol_str) if vol_str else 0,
                    })
                except (ValueError, TypeError):
                    continue

            if len(all_rows) >= days:
                break
            time.sleep(0.6)

        all_rows.sort(key=lambda x: x["日期"])
        return all_rows if all_rows else None

    def _get_fund_historical(self, code: str, days: int = 252) -> Optional[List[Dict]]:
        etf_data = self.get_etf_historical(code, days)
        if not etf_data:
            return None

        rows = []
        for d in etf_data:
            rows.append({
                "日期": d["date"],
                "开盘价": d["nav"],
                "收盘价": d["nav"],
                "最高价": d["nav"],
                "最低价": d["nav"],
                "成交量": 0,
            })
        return rows

    def get_etf_quotes(self, codes: List[str]) -> Dict[str, Dict]:
        query = "、".join(codes)
        result = self.call("fund", "get_fund_market_performance", {
            "query": f"{query}最新单位净值和涨跌幅"
        })
        parsed = _parse_ifind_response(result)
        quotes = {}
        for row in parsed.get("tables", []):
            code = _col(row, "证券代码") or ""
            code = code.replace(".SH", "").replace(".SZ", "")
            if code in codes:
                try:
                    price_str = _col(row, "单位净值") or "0"
                    chg_str = _col(row, "涨跌幅") or "0"
                    quotes[code] = {
                        "price": float(price_str),
                        "change_pct": float(chg_str),
                        "date": _col(row, "日期") or "",
                    }
                except (ValueError, TypeError):
                    continue
        return quotes

    def get_etf_historical(self, code: str, days: int = 252) -> Optional[List[Dict]]:
        result = self.call("fund", "get_fund_market_performance", {
            "query": f"{code}近{days}个交易日的单位净值、涨跌幅"
        })
        parsed = _parse_ifind_response(result)
        tables = parsed.get("tables", [])
        if not tables:
            return None

        rows = []
        for row in tables:
            date_str = (_col(row, "日期") or "").strip()
            if not date_str or len(date_str) < 8 or not date_str[0:2].isdigit():
                continue
            try:
                nav_str = _col(row, "单位净值") or "0"
                chg_str = _col(row, "涨跌幅") or "0"
                cum_str = _col(row, "累计", "累计单位净值") or _col(row, "累计") or "0"
                rows.append({
                    "date": date_str,
                    "nav": float(nav_str),
                    "change_pct": float(chg_str) if chg_str else 0,
                    "cumulative_nav": float(cum_str) if cum_str else 0,
                })
            except (ValueError, TypeError):
                continue
        return rows if rows else None

    def get_index_historical(self, index_name: str, days: int = 252) -> Optional[List[Dict]]:
        all_rows = []
        seen_dates = set()

        end_date = datetime.now()
        chunks_needed = max(1, days // 60)

        for i in range(chunks_needed):
            chunk_end = end_date - timedelta(days=i * 120)
            chunk_start = chunk_end - timedelta(days=130)
            s = chunk_start.strftime("%Y%m%d")
            e = chunk_end.strftime("%Y%m%d")

            result = self.call("index", "index_data", {
                "query": f"{index_name}从{s}到{e}的收盘价和成交额"
            })
            parsed = _parse_ifind_response(result)
            for row in parsed.get("tables", []):
                date_str = (_col(row, "日期") or "").strip()
                if not date_str or date_str in seen_dates:
                    continue
                seen_dates.add(date_str)
                try:
                    close_str = _col(row, "收盘") or _col(row, "收盘价") or "0"
                    amt_str = _col(row, "成交额") or _col(row, "成交金额") or "0"
                    amt_str = amt_str.replace("亿", "").strip()
                    all_rows.append({
                        "date": date_str,
                        "close": float(close_str),
                        "amount": float(amt_str) * 1e8 if amt_str else 0,
                    })
                except (ValueError, TypeError):
                    continue

            if len(all_rows) >= days:
                break
            time.sleep(0.6)

        all_rows.sort(key=lambda x: x["date"])
        return all_rows if all_rows else None

    def get_index_latest(self, index_name: str) -> Optional[Dict]:
        result = self.call("index", "index_data", {
            "query": f"{index_name}最新收盘价和涨跌幅"
        })
        parsed = _parse_ifind_response(result)
        tables = parsed.get("tables", [])
        if tables:
            row = tables[0]
            try:
                close_str = _col(row, "收盘") or _col(row, "收盘价") or "0"
                chg_str = _col(row, "涨跌幅") or "0"
                return {
                    "close": float(close_str),
                    "change_pct": float(chg_str) if chg_str else 0,
                    "date": datetime.now().strftime("%Y%m%d"),
                }
            except (ValueError, TypeError):
                pass
        return None

    def get_edb_value(self, query: str) -> Optional[float]:
        result = self.call("edb", "get_edb_data", {"query": query})
        parsed = _parse_ifind_response(result)
        for row in parsed.get("tables", []):
            for v in row.values():
                try:
                    return float(v) if v else None
                except (ValueError, TypeError):
                    continue
        return None

    def search_edb(self, query: str) -> Dict:
        return self.call("edb", "search_edb", {"query": query})

    def get_bond_market(self, query: str) -> Dict:
        return self.call("bond", "bond_market_data", {"query": query})

    def get_futures_realtime(self, codes: List[str]) -> Optional[List[Dict]]:
        if not codes:
            return None
        
        codes_str = ",".join(codes)
        fields = "tradeDate;tradeTime;ms;preClose;open;high;low;latest;latestVolume;avgPrice;volume;change;changeSettle;changeRatio;changeRatioSettle;increasePositionVol;preSettlement;sellVolume;buyVolume;dailyIncreasePosition;swing;latest_price;settlement;dealDirection;dealtype;openInterest;positionDiff;capitalFlow;capitalDeposition;amplitude;upperLimit;downLimit;dealtypecode"
        
        result = self.call("futures", "get_futures_realtime", {
            "query": f"{codes_str}",
            "fields": fields
        })
        
        parsed = _parse_ifind_response(result)
        tables = parsed.get("tables", [])
        
        if not tables:
            datas = parsed.get("datas", [])
            if datas:
                tables = datas
        
        if not tables:
            return None
        
        rows = []
        for row in tables:
            try:
                row_data = {
                    "tradeDate": _col(row, "tradeDate", "交易日期", "日期") or "",
                    "tradeTime": _col(row, "tradeTime", "交易时间", "时间") or "",
                    "ms": _col(row, "ms", "毫秒") or "",
                    "preClose": float(_col(row, "preClose", "前收盘价", "昨收")) if _col(row, "preClose", "前收盘价", "昨收") else None,
                    "open": float(_col(row, "open", "开盘价", "开盘")) if _col(row, "open", "开盘价", "开盘") else None,
                    "high": float(_col(row, "high", "最高价", "最高")) if _col(row, "high", "最高价", "最高") else None,
                    "low": float(_col(row, "low", "最低价", "最低")) if _col(row, "low", "最低价", "最低") else None,
                    "latest": float(_col(row, "latest", "最新价", "现价")) if _col(row, "latest", "最新价", "现价") else None,
                    "latestVolume": int(_col(row, "latestVolume", "现手")) if _col(row, "latestVolume", "现手") else None,
                    "avgPrice": float(_col(row, "avgPrice", "均价")) if _col(row, "avgPrice", "均价") else None,
                    "volume": float(_col(row, "volume", "成交量")) if _col(row, "volume", "成交量") else None,
                    "change": float(_col(row, "change", "涨跌")) if _col(row, "change", "涨跌") else None,
                    "changeSettle": float(_col(row, "changeSettle", "涨跌（结算价）")) if _col(row, "changeSettle", "涨跌（结算价）") else None,
                    "changeRatio": float(_col(row, "changeRatio", "涨跌幅")) if _col(row, "changeRatio", "涨跌幅") else None,
                    "changeRatioSettle": float(_col(row, "changeRatioSettle", "涨跌幅（结算价）")) if _col(row, "changeRatioSettle", "涨跌幅（结算价）") else None,
                    "increasePositionVol": float(_col(row, "increasePositionVol", "增仓量")) if _col(row, "increasePositionVol", "增仓量") else None,
                    "preSettlement": float(_col(row, "preSettlement", "昨结算价")) if _col(row, "preSettlement", "昨结算价") else None,
                    "sellVolume": float(_col(row, "sellVolume", "内盘")) if _col(row, "sellVolume", "内盘") else None,
                    "buyVolume": float(_col(row, "buyVolume", "外盘")) if _col(row, "buyVolume", "外盘") else None,
                    "dailyIncreasePosition": float(_col(row, "dailyIncreasePosition", "日增仓")) if _col(row, "dailyIncreasePosition", "日增仓") else None,
                    "swing": float(_col(row, "swing", "振幅")) if _col(row, "swing", "振幅") else None,
                    "latest_price": float(_col(row, "latest_price", "最新成交价")) if _col(row, "latest_price", "最新成交价") else None,
                    "settlement": float(_col(row, "settlement", "结算价")) if _col(row, "settlement", "结算价") else None,
                    "dealDirection": _col(row, "dealDirection", "成交方向") or "",
                    "dealtype": _col(row, "dealtype", "成交性质") or "",
                    "openInterest": float(_col(row, "openInterest", "持仓量")) if _col(row, "openInterest", "持仓量") else None,
                    "positionDiff": float(_col(row, "positionDiff", "仓差")) if _col(row, "positionDiff", "仓差") else None,
                    "capitalFlow": float(_col(row, "capitalFlow", "资金流向")) if _col(row, "capitalFlow", "资金流向") else None,
                    "capitalDeposition": float(_col(row, "capitalDeposition", "资金沉淀")) if _col(row, "capitalDeposition", "资金沉淀") else None,
                    "amplitude": float(_col(row, "amplitude", "振幅")) if _col(row, "amplitude", "振幅") else None,
                    "upperLimit": float(_col(row, "upperLimit", "涨停价")) if _col(row, "upperLimit", "涨停价") else None,
                    "downLimit": float(_col(row, "downLimit", "跌停价")) if _col(row, "downLimit", "跌停价") else None,
                    "dealtypecode": _col(row, "dealtypecode", "成交性质编码") or "",
                }
                rows.append(row_data)
            except (ValueError, TypeError):
                continue
        
        return rows if rows else None

    def search_news(self, query: str, time_start: str = "",
                    time_end: str = "", size: int = 5) -> Dict:
        params = {"query": query, "size": size}
        if time_start:
            params["time_start"] = time_start
        if time_end:
            params["time_end"] = time_end
        return self.call("news", "search_news", params)