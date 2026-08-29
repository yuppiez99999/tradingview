"""
只监控用户交易计划中的 14 个标的。
数据源优先级：Wind MCP > akshare > 新浪 HTTP
"""

import importlib.util
import json
import os
import sys
from datetime import datetime

import requests

# ── 路径修正 ──────────────────────────────────────────
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# Wind MCP 模块导入
_WIND_FETCHER_PATH = os.path.join(REPO_ROOT, "wind_mcp_fetcher.py")
_wind_get_quote = None
_wind_get_batch_quotes = None
if os.path.isfile(_WIND_FETCHER_PATH):
    try:
        spec = importlib.util.spec_from_file_location(
            "wind_mcp_fetcher", _WIND_FETCHER_PATH
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _wind_get_quote = getattr(mod, "wind_get_quote", None)
        _wind_get_batch_quotes = getattr(mod, "wind_get_batch_quotes", None)
    except Exception:
        pass

# akshare（可选降级源，仅股票；ETF 实时快照跳过）
_akshare_source = None
try:
    from utils.akshare_data_source import AKShareDataSource  # noqa: E402

    _akshare_source = AKShareDataSource()
except Exception:
    pass

# ── 你的标的 universe ──────────────────────────────────
STOCK_UNIVERSE = [
    ("600276.SH", "恒瑞医药"),
    ("000408.SZ", "藏格矿业"),
    ("688041.SH", "海光信息"),
    ("600900.SH", "长江电力"),
]

ETF_UNIVERSE = [
    ("512170.SH", "医疗ETF华宝"),
    ("515030.SH", "新能源车ETF华夏"),
    ("512880.SH", "证券ETF国泰"),
    ("512800.SH", "银行ETF华宝"),
    ("518880.SH", "黄金ETF华安"),
    ("510050.SH", "上证50ETF华夏"),
    ("588080.SH", "科创50ETF易方达"),
    ("512760.SH", "半导体ETF国泰"),
    ("510300.SH", "沪深300ETF华泰柏瑞"),
    ("588000.SH", "科创50ETF华夏"),
]

OUTPUT_DIR = os.path.join(REPO_ROOT, "realtime_monitor")
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ── akshare 降级（仅股票）──────────────────────────────
def fetch_akshare_stock_snapshot(codes: list) -> list:
    """通过 AKShareDataSource 获取股票实时快照（不含 ETF）。"""
    if _akshare_source is None:
        return []
    results = []
    for code in codes:
        try:
            quote = _akshare_source.get_realtime_quote(code)
        except Exception:
            continue
        if not quote or not isinstance(quote, dict):
            continue
        price = quote.get("index_price")
        prev_close = quote.get("prev_close")
        change_ratio = ""
        try:
            if price and prev_close:
                change_ratio = f"{(float(price) - float(prev_close)) / float(prev_close) * 100:.6f}"
        except Exception:
            change_ratio = ""
        results.append(
            {
                "code": _normalize_wind_code(code),
                "name": quote.get("name", code),
                "time": quote.get("timestamp", ""),
                "latest": price,
                "change_ratio": change_ratio,
                "amount": quote.get("amount"),
                "volume": quote.get("volume"),
                "chg_1min": "",
                "chg_3min": "",
                "chg_5min": "",
                "source": "akshare",
            }
        )
    return results


# ── Wind MCP 适配 ──────────────────────────────────────
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
    except Exception:
        return []
    if not isinstance(batch, dict):
        return []
    results = []
    for code, quote in batch.items():
        if not quote or not isinstance(quote, dict):
            continue
        results.append(
            {
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
            }
        )
    return results


# ── 新浪 HTTP fallback ─────────────────────────────────
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
    try:
        # B3 修复 (2026-08-08): 关闭 verify=False (B501), 改用 certifi 可信证书包.
        # 原 verify=False 允许 MITM 篡改行情数据 → 股票池监控误判风险.
        # 回归见 docs/CODE_REVIEW_COMPREHENSIVE_20260808.md B3.
        try:
            import certifi

            _verify = certifi.where()
        except ImportError:
            _verify = True  # 回退到系统证书, 仍优于 verify=False
        resp = requests.get(
            url,
            timeout=10,
            headers=headers,
            verify=_verify,
            proxies={"http": None, "https": None},
        )
        text = resp.text.strip()
    except Exception:
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
        if len(parts) < 32:
            continue
        name = parts[0].strip()
        latest = parts[3] if parts[3] else ""
        pre_close = parts[2] if parts[2] else ""
        change_ratio = ""
        try:
            if latest and pre_close:
                change_ratio = (
                    f"{(float(latest) - float(pre_close)) / float(pre_close) * 100:.6f}"
                )
        except Exception:
            change_ratio = ""
        results.append(
            {
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
            }
        )
    return results


# ── 主获取逻辑：Wind MCP > akshare > 新浪 HTTP ─────
def fetch_stock_snapshot() -> list:
    # 1. Wind MCP
    wind_codes = [code for code, _ in STOCK_UNIVERSE]
    rows = fetch_wind_snapshot(wind_codes, is_fund=False)
    if rows:
        return rows

    # 2. akshare
    rows = fetch_akshare_stock_snapshot(wind_codes)
    if rows:
        return rows

    # 3. 新浪 HTTP
    codes = [_to_sina_code(code) for code, _ in STOCK_UNIVERSE]
    return _fetch_sina_realtime(codes)


def fetch_fund_snapshot() -> list:
    # 1. Wind MCP
    wind_codes = [code for code, _ in ETF_UNIVERSE]
    rows = fetch_wind_snapshot(wind_codes, is_fund=True)
    if rows:
        return rows

    # 2. 新浪 HTTP（akshare stock_zh_a_spot_em 不含 ETF）
    codes = [_to_sina_code(code) for code, _ in ETF_UNIVERSE]
    return _fetch_sina_realtime(codes)


def save_snapshot(snapshot: list, date_str: str) -> str:
    path = os.path.join(OUTPUT_DIR, f"realtime_monitor_{date_str}.json")
    payload = {
        "date": date_str,
        "updated_at": datetime.now().isoformat(),
        "count": len(snapshot),
        "items": snapshot,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def main():
    date_str = datetime.now().strftime("%Y-%m-%d")
    stock_rows = fetch_stock_snapshot()
    fund_rows = fetch_fund_snapshot()
    snapshot = stock_rows + fund_rows
    save_snapshot(snapshot, date_str)
    for _item in snapshot:
        pass


if __name__ == "__main__":
    main()
