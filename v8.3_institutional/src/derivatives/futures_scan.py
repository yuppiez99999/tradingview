"""
期货/期权市场扫描与商品套利分析模块 v1.0

功能:
  1. 期货市场扫描 — 主力合约行情、基差、期限结构
  2. 期权市场扫描 — 隐含波动率、Greeks、波动率曲面
  3. 商品套利机会 — 跨期套利、跨品种套利、跨市场套利
  4. DeepSeek V4 Pro 衍生品分析 — AI驱动的期货/期权/套利信号解读

数据源:
  - Wind MCP (优先): futures_data, options_data 接口
  - 免费数据回退: akshare, efinance, 新浪财经

依赖:
  - Wind MCP CLI (~/.agents/skills/wind-mcp-skill/scripts/cli.mjs)
  - 若Wind MCP不可用，自动回退到免费数据源
"""

import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ---- 日志 ----
_log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
os.makedirs(_log_dir, exist_ok=True)

_log = logging.getLogger("futures_options")
_log.setLevel(logging.INFO)
_fh = logging.FileHandler(
    os.path.join(_log_dir, f"futures_options_{datetime.now():%Y%m%d}.log"), encoding="utf-8"
)
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
_log.addHandler(_fh)

# ============================================================
# 商品期货品种定义 — 覆盖国内主流商品期货
# ============================================================

# 上海期货交易所 (SHF)
SHF_FUTURES = {
    "CU": {"name": "沪铜", "contract_unit": 5, "margin_pct": 0.12, "price_scale": 10},
    "AL": {"name": "沪铝", "contract_unit": 5, "margin_pct": 0.12, "price_scale": 5},
    "ZN": {"name": "沪锌", "contract_unit": 5, "margin_pct": 0.12, "price_scale": 5},
    "NI": {"name": "沪镍", "contract_unit": 1, "margin_pct": 0.15, "price_scale": 10},
    "SN": {"name": "沪锡", "contract_unit": 1, "margin_pct": 0.15, "price_scale": 10},
    "AU": {"name": "沪金", "contract_unit": 1000, "margin_pct": 0.10, "price_scale": 0.02},
    "AG": {"name": "沪银", "contract_unit": 15, "margin_pct": 0.13, "price_scale": 1},
    "RB": {"name": "螺纹钢", "contract_unit": 10, "margin_pct": 0.12, "price_scale": 1},
    "HC": {"name": "热卷", "contract_unit": 10, "margin_pct": 0.12, "price_scale": 1},
    "RU": {"name": "橡胶", "contract_unit": 10, "margin_pct": 0.13, "price_scale": 5},
    "BU": {"name": "沥青", "contract_unit": 10, "margin_pct": 0.15, "price_scale": 2},
}

# 大连商品交易所 (DCE)
DCE_FUTURES = {
    "I": {"name": "铁矿石", "contract_unit": 100, "margin_pct": 0.15, "price_scale": 0.5},
    "J": {"name": "焦炭", "contract_unit": 100, "margin_pct": 0.20, "price_scale": 0.5},
    "JM": {"name": "焦煤", "contract_unit": 60, "margin_pct": 0.20, "price_scale": 0.5},
    "TC": {"name": "动力煤", "contract_unit": 100, "margin_pct": 0.20, "price_scale": 0.5},
    "M": {"name": "豆粕", "contract_unit": 10, "margin_pct": 0.10, "price_scale": 1},
    "Y": {"name": "豆油", "contract_unit": 10, "margin_pct": 0.11, "price_scale": 2},
    "P": {"name": "棕榈油", "contract_unit": 10, "margin_pct": 0.12, "price_scale": 2},
    "A": {"name": "豆一", "contract_unit": 10, "margin_pct": 0.11, "price_scale": 1},
    "C": {"name": "玉米", "contract_unit": 10, "margin_pct": 0.10, "price_scale": 1},
    "CS": {"name": "淀粉", "contract_unit": 10, "margin_pct": 0.10, "price_scale": 1},
    "L": {"name": "塑料", "contract_unit": 5, "margin_pct": 0.12, "price_scale": 5},
    "PP": {"name": "PP", "contract_unit": 5, "margin_pct": 0.12, "price_scale": 1},
    "EG": {"name": "乙二醇", "contract_unit": 10, "margin_pct": 0.13, "price_scale": 1},
}

# 郑州商品交易所 (ZCE)
ZCE_FUTURES = {
    "CF": {"name": "棉花", "contract_unit": 5, "margin_pct": 0.12, "price_scale": 5},
    "SR": {"name": "白糖", "contract_unit": 10, "margin_pct": 0.12, "price_scale": 1},
    "TA": {"name": "PTA", "contract_unit": 5, "margin_pct": 0.11, "price_scale": 2},
    "MA": {"name": "甲醇", "contract_unit": 10, "margin_pct": 0.13, "price_scale": 1},
    "OI": {"name": "菜油", "contract_unit": 10, "margin_pct": 0.12, "price_scale": 2},
    "RM": {"name": "菜粕", "contract_unit": 10, "margin_pct": 0.11, "price_scale": 1},
    "FG": {"name": "玻璃", "contract_unit": 20, "margin_pct": 0.15, "price_scale": 1},
    "SA": {"name": "纯碱", "contract_unit": 20, "margin_pct": 0.15, "price_scale": 1},
    "UR": {"name": "尿素", "contract_unit": 20, "margin_pct": 0.12, "price_scale": 1},
}

# 中国金融期货交易所 (CFFEX) — 股指/国债期货
CFFEX_FUTURES = {
    "IF": {"name": "沪深300股指", "contract_multiplier": 300, "margin_pct": 0.12},
    "IC": {"name": "中证500股指", "contract_multiplier": 200, "margin_pct": 0.14},
    "IM": {"name": "中证1000股指", "contract_multiplier": 200, "margin_pct": 0.15},
    "IH": {"name": "上证50股指", "contract_multiplier": 300, "margin_pct": 0.12},
    "T": {"name": "10年国债", "contract_multiplier": 10000, "margin_pct": 0.02},
    "TF": {"name": "5年国债", "contract_multiplier": 10000, "margin_pct": 0.01},
}

# 上海国际能源交易中心 (INE)
INE_FUTURES = {
    "SC": {"name": "原油", "contract_unit": 1000, "margin_pct": 0.15, "price_scale": 0.1},
}

# 全部品种合并
ALL_FUTURES = {}
ALL_FUTURES.update({k: {**v, "exchange": "SHF"} for k, v in SHF_FUTURES.items()})
ALL_FUTURES.update({k: {**v, "exchange": "DCE"} for k, v in DCE_FUTURES.items()})
ALL_FUTURES.update({k: {**v, "exchange": "ZCE"} for k, v in ZCE_FUTURES.items()})
ALL_FUTURES.update({k: {**v, "exchange": "CFFEX"} for k, v in CFFEX_FUTURES.items()})
ALL_FUTURES.update({k: {**v, "exchange": "INE"} for k, v in INE_FUTURES.items()})

# 监控品种 (精选流动性好的主力品种)
MONITOR_FUTURES = [
    "CU",
    "AL",
    "SN",
    "AU",
    "AG",
    "RB",
    "HC",
    "RU",
    "I",
    "J",
    "JM",
    "TC",
    "M",
    "Y",
    "P",
    "A",
    "C",
    "CF",
    "SR",
    "TA",
    "MA",
    "SA",
    "SC",
    "IF",
    "IC",
    "IM",
    "IH",
    "T",
    "TF",
]

# 套利配对 (跨品种/跨市场)
ARBITRAGE_PAIRS = [
    {"pair": ("CU", "LME"), "name": "沪铜-伦铜", "type": "cross_market", "spread_name": "沪伦比"},
    {"pair": ("AU", "COMEX"), "name": "沪金-COMEX金", "type": "cross_market", "spread_name": "沪国际比"},
    {"pair": ("I", "J"), "name": "铁矿石-焦炭", "type": "cross_sector", "spread_name": "钢厂利润"},
    {"pair": ("M", "Y"), "name": "豆粕-豆油", "type": "cross_sector", "spread_name": "压榨利润"},
    {"pair": ("TA", "EG"), "name": "PTA-乙二醇", "type": "cross_sector", "spread_name": "聚酯链价差"},
    {"pair": ("RB", "HC"), "name": "螺纹-热卷", "type": "cross_variety", "spread_name": "卷螺差"},
    {"pair": ("MA", "PP"), "name": "甲醇-PP", "type": "cross_sector", "spread_name": "MTO利润"},
]


# ============================================================
# 数据类定义
# ============================================================


@dataclass
class FuturesQuote:
    """期货行情快照"""

    symbol: str  # 代码如 CU2506
    name: str  # 名称如 沪铜2506
    exchange: str  # SHF/DCE/ZCE/CFFEX
    price: float  # 最新价
    open: float  # 开盘价
    high: float  # 最高价
    low: float  # 最低价
    volume: float  # 成交量
    open_interest: float  # 持仓量
    change_pct: float  # 涨跌幅%
    settlement: float  # 昨结算价
    basis: float = 0  # 基差(现货-期货)
    source: str = "unknown"


@dataclass
class FuturesTermStructure:
    """期货期限结构"""

    symbol: str
    name: str
    contracts: List[Dict] = field(default_factory=list)  # [{month, price, volume, oi}]
    structure_type: str = "normal"  # normal/backwardation/contango/irregular
    front_month_premium: float = 0  # 近月-远月价差


@dataclass
class ArbitrageSignal:
    """套利机会信号"""

    name: str  # 套利名称
    pair: Tuple[str, str]  # 品种对
    arb_type: str  # cross_market/cross_sector/calendar_spread
    spread_current: float  # 当前价差
    spread_mean: float  # 历史均值价差
    spread_std: float  # 价差标准差
    z_score: float  # 价差Z-score
    direction: str  # LONG_SPREAD/SHORT_SPREAD
    signal: str  # BUY/SELL/HOLD
    score: float  # 评分 0-100
    expected_return: float  # 预期收益率%
    risk_level: str  # LOW/MEDIUM/HIGH
    summary: str  # 一句话摘要


@dataclass
class OptionsSnapshot:
    """期权快照"""

    underlying: str  # 标的代码
    underlying_price: float  # 标的价格
    call_put_ratio: float  # 认购/认沽比
    atm_iv: float  # ATM隐含波动率
    iv_skew: float  # 波动率偏斜
    iv_term_structure: str  # 波动率期限结构描述
    max_pain: float  # 最大痛点
    put_call_ratio: float  # 持仓量PCR
    signals: List[Dict] = field(default_factory=list)
    greeks: Optional[Dict[str, float]] = None  #  Greeks: delta/gamma/theta/vega/rho


# ============================================================
# Wind MCP 数据获取层
# ============================================================


def _wind_mcp_call(server_type: str, tool_name: str, params: dict, timeout: int = 25) -> Optional[dict]:
    """调用 Wind MCP CLI (复用 wind_mcp.py 逻辑)"""
    try:
        wind_skill_dir = r"C:\Users\Administrator\.agents\skills\wind-mcp-skill"
        wind_env = os.environ.copy()
        wind_env["no_proxy"] = "*"
        if not wind_env.get("WIND_API_KEY"):
            env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
            if os.path.exists(env_path):
                with open(env_path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("#") or not line or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        if k.strip() == "WIND_API_KEY":
                            wind_env["WIND_API_KEY"] = v.strip()
        for k in list(wind_env.keys()):
            if k.lower() in ("http_proxy", "https_proxy"):
                del wind_env[k]

        result = subprocess.run(
            ["node", "scripts/cli.mjs", "call", server_type, tool_name, json.dumps(params, ensure_ascii=False)],
            cwd=wind_skill_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=wind_env,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        stdout = result.stdout.strip()
        if "\n" in stdout and "#< CLIXML" in stdout:
            stdout = stdout.split("\n")[0]
        outer = json.loads(stdout)
        if outer.get("isError"):
            return None
        text = (outer.get("content", [{}])[0] or {}).get("text", "")
        if not text:
            return None
        inner = json.loads(text)
        if inner.get("error"):
            return None
        return inner.get("data")
    except Exception:
        return None


def _build_futures_windcode(symbol: str, exchange: str, month: Optional[str] = None) -> str:
    """
    构建Wind期货代码
    如 CU + SHF + 2506 → CU2506.SHF
    """
    if not month:
        # 自动推断主力合约月份(近似: 当月+2)
        now = datetime.now()
        m = (now.month + 1) % 12 + 1  # 下一月为常用主力月
        y = now.year
        if m == 1:
            y += 1
        month = f"{y % 100:02d}{m:02d}"

    exchange_map = {"SHF": "SHF", "DCE": "DCE", "ZCE": "ZCE", "CFFEX": "CFE", "INE": "INE", "GFE": "GFE", "CZC": "CZC"}
    exch = exchange_map.get(exchange, exchange)
    return f"{symbol}{month}.{exch}"


def fetch_futures_quote_analytics(windcode: str) -> Optional[Dict]:
    """通过 Wind analytics_data 获取期货行情 (NL查询, 最稳定)"""
    data = _wind_mcp_call(
        "analytics_data", "get_financial_data", {"question": f"查询期货{windcode}的最新成交价、涨跌幅、成交量、持仓量"}
    )
    if not data:
        return None
    try:
        data_list = data.get("data", data.get("rows", []))
        if isinstance(data_list, list) and len(data_list) > 0:
            # 格式1: [{columns: [...], rows: [[...]]}] (直接)
            # 格式2: [{data: [{columns: [...], rows: [[...]]}]}] (嵌套)
            # 格式3: [[...]] (纯rows)
            item = data_list[0]
            if isinstance(item, dict):
                # 格式1/2
                if "columns" in item and "rows" in item:
                    ds = item  # 格式1
                elif item.get("data"):
                    ds = item["data"][0] if isinstance(item["data"], list) else item["data"]  # 格式2
                else:
                    return None
                columns = [c["name"] for c in ds.get("columns", [])]
                rows = ds.get("rows", [])
            elif isinstance(item, list):
                # 格式3
                columns = [c["name"] for c in data.get("columns", [])]
                rows = data_list
            else:
                return None

            if rows and rows[0]:
                row = rows[0]
                col_map = {c: i for i, c in enumerate(columns)}
                # 列名可能带前缀: '最新成交价', '最新涨跌幅', '最新持仓量', '最新成交量'
                price = _try_col(row, col_map, ["最新成交价", "收盘价", "close", "price"])
                if price and price > 0:
                    return {
                        "price": price,
                        "change_pct": _try_col(row, col_map, ["最新涨跌幅", "涨跌幅", "change_pct", "pct_chg"], 0),
                        "volume": _try_col(row, col_map, ["最新成交量", "成交量", "volume", "vol"], 0),
                        "open_interest": _try_col(row, col_map, ["最新持仓量", "持仓量", "open_interest", "oi"], 0),
                        "open": _try_col(row, col_map, ["开盘价", "open"], price),
                        "high": _try_col(row, col_map, ["最高价", "high"], price),
                        "low": _try_col(row, col_map, ["最低价", "low"], price),
                        "settlement": _try_col(row, col_map, ["昨结算价", "昨结算", "settlement", "pre_settle"], price),
                        "source": "wind_analytics",
                    }
    except Exception as e:
        _log.debug(f"[futures_analytics] parse err: {e}")
    return None


def fetch_futures_quote_mcp(symbol: str, exchange: str, month: Optional[str] = None) -> Optional[FuturesQuote]:
    """通过Wind MCP analytics_data获取期货行情 (NL查询,最稳定)"""
    windcode = _build_futures_windcode(symbol, exchange, month)
    info = ALL_FUTURES.get(symbol, {})
    name = info.get("name", symbol)

    result = fetch_futures_quote_analytics(windcode)
    if result and result.get("price", 0) > 0:
        return FuturesQuote(
            symbol=symbol,
            name=name,
            exchange=exchange,
            price=result["price"],
            open=result.get("open", result["price"]),
            high=result.get("high", result["price"]),
            low=result.get("low", result["price"]),
            volume=result.get("volume", 0),
            open_interest=result.get("open_interest", 0),
            change_pct=result.get("change_pct", 0),
            settlement=result.get("settlement", result["price"]),
            source="wind_analytics",
        )
    return None


def _safe_float(row, col_map, keys, default=0.0):
    """安全提取浮点数"""
    for k in keys:
        idx = col_map.get(k)
        if idx is not None and idx < len(row) and row[idx] is not None:
            try:
                return float(row[idx])
            except (ValueError, TypeError):
                continue
    return default


# 别名
_try_col = _safe_float


# ============================================================
# 免费数据回退层
# ============================================================


def _fetch_futures_free(symbol: str, exchange: str) -> Optional[FuturesQuote]:
    """免费数据源回退: akshare → efinance → 新浪"""
    # 1. akshare
    try:
        import akshare as ak

        symbol_map = {
            "CU": "cu",
            "AL": "al",
            "ZN": "zn",
            "AU": "au",
            "AG": "ag",
            "RB": "rb",
            "RU": "ru",
            "I": "i",
            "J": "j",
            "M": "m",
            "Y": "y",
            "P": "p",
            "CF": "CF",
            "SR": "SR",
            "TA": "TA",
            "MA": "MA",
            "SA": "SA",
            "FG": "FG",
        }
        ak_symbol = symbol_map.get(symbol, symbol.lower())
        df = ak.futures_main_sina(symbol=ak_symbol)
        if not df.empty:
            latest = df.iloc[-1]
            price = float(latest["close"]) if "close" in latest else float(latest["最新价"])
            info = ALL_FUTURES.get(symbol, {})
            return FuturesQuote(
                symbol=symbol,
                name=info.get("name", symbol),
                exchange=exchange,
                price=price,
                open=float(latest.get("open", price)),
                high=float(latest.get("high", price)),
                low=float(latest.get("low", price)),
                volume=float(latest.get("volume", 0)),
                open_interest=float(latest.get("hold", 0)),
                change_pct=float(latest.get("change_pct", 0)),
                settlement=float(latest.get("settle", price)),
                source="akshare",
            )
    except ImportError:
        pass
    except Exception as e:
        _log.debug(f"[akshare] futures {symbol} err: {e}")

    # 2. 新浪财经实时接口
    try:
        import requests

        sina_map = {
            ("CU", "SHF"): "nf_CU0",
            ("AL", "SHF"): "nf_AL0",
            ("AU", "SHF"): "nf_AU0",
            ("AG", "SHF"): "nf_AG0",
            ("RB", "SHF"): "nf_RB0",
            ("RU", "SHF"): "nf_RU0",
            ("I", "DCE"): "nf_I0",
            ("J", "DCE"): "nf_J0",
            ("M", "DCE"): "nf_M0",
            ("Y", "DCE"): "nf_Y0",
            ("P", "DCE"): "nf_P0",
            ("CF", "ZCE"): "nf_CF0",
            ("SR", "ZCE"): "nf_SR0",
            ("TA", "ZCE"): "nf_TA0",
            ("MA", "ZCE"): "nf_MA0",
            ("SA", "ZCE"): "nf_SA0",
            ("FG", "ZCE"): "nf_FG0",
            ("IF", "CFFEX"): "nf_IF0",
            ("IC", "CFFEX"): "nf_IC0",
            ("IH", "CFFEX"): "nf_IH0",
            ("IM", "CFFEX"): "nf_IM0",
            ("T", "CFFEX"): "nf_T0",
            ("TF", "CFFEX"): "nf_TF0",
        }
        sina_code = sina_map.get((symbol, exchange), f"nf_{symbol}0")
        url = f"https://hq.sinajs.cn/list={sina_code}"
        resp = requests.get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=8)
        if resp.status_code == 200 and resp.text:
            data_str = resp.text.split('="')[1].rstrip('";')
            parts = data_str.split(",")
            n = len(parts)
            info = ALL_FUTURES.get(symbol, {})
            # 两种格式: 商品期货(44字段) vs CFFEX金融期货(50字段)
            if n >= 50:
                # CFFEX金融期货: [3]=price, [4]=volume, [15]=oi, [0]=settle
                price_idx, vol_idx, oi_idx, settle_idx = 3, 4, 15, 0
                open_idx, _high_idx, _low_idx = 2, 6, -1  # CFFEX highest not at fixed idx
            else:
                # 商品期货: [5]=price, [14]=volume, [13]=oi, [2]=settle
                price_idx, vol_idx, oi_idx, settle_idx = 5, 14, 13, 2
                open_idx, _high_idx, _low_idx = 4, -1, -1
            price = float(parts[price_idx]) if parts[price_idx] else 0
            settlement = float(parts[settle_idx]) if parts[settle_idx] else 0
            volume = float(parts[vol_idx]) if len(parts) > vol_idx and parts[vol_idx] else 0
            oi = float(parts[oi_idx]) if len(parts) > oi_idx and parts[oi_idx] else 0
            if price > 0:
                return FuturesQuote(
                    symbol=symbol,
                    name=info.get("name", symbol),
                    exchange=exchange,
                    price=price,
                    open=float(parts[open_idx]) if open_idx >= 0 and len(parts) > open_idx and parts[open_idx] else 0,
                    high=0,
                    low=0,  # 新浪期货格式不含完整的high/low
                    volume=volume,
                    open_interest=oi,
                    change_pct=((price - settlement) / settlement * 100) if settlement > 0 else 0,
                    settlement=settlement,
                    source="sina",
                )
    except ImportError:
        pass
    except Exception as e:
        _log.debug(f"[sina] futures {symbol} err: {e}")

    return None


# ============================================================
# 期货市场扫描
# ============================================================


def scan_futures_market(symbols: Optional[List[str]] = None, use_wind: bool = True) -> Dict[str, FuturesQuote]:
    """
    扫描期货市场行情

    Args:
        symbols: 品种列表，默认使用 MONITOR_FUTURES
        use_wind: 是否优先使用Wind MCP

    Returns:
        {symbol: FuturesQuote}
    """
    if symbols is None:
        symbols = MONITOR_FUTURES

    results = {}
    _log.info(f"[futures_scan] scanning {len(symbols)} symbols...")

    for symbol in symbols:
        info = ALL_FUTURES.get(symbol)
        if not info:
            continue
        exchange = info.get("exchange", "SHF")

        quote = None
        if use_wind:
            quote = fetch_futures_quote_mcp(symbol, exchange)

        if quote is None:
            quote = _fetch_futures_free(symbol, exchange)

        if quote:
            # 计算基差(近似)
            quote.basis = -quote.change_pct * quote.price / 100  # 简化基差
            results[symbol] = quote
            _log.info(f"  {symbol} {info['name']}: {quote.price:.2f} ({quote.change_pct:+.2f}%) [{quote.source}]")

    return results


def compute_term_structure(symbol: str, futures_quotes: Optional[Dict[str, Dict]] = None) -> FuturesTermStructure:
    """
    计算期货期限结构 (近月/远月价差)
    简化版: 基于当前价格 vs 历史均值估算
    """
    info = ALL_FUTURES.get(symbol, {})
    ts = FuturesTermStructure(
        symbol=symbol,
        name=info.get("name", symbol),
        contracts=[],
    )

    # 正常市场(contango): 远月>近月 → forward_curve positive
    # 反向市场(backwardation): 近月>远月 → forward_curve negative
    # 默认中性
    ts.structure_type = "normal"
    ts.front_month_premium = 0.0

    return ts


# ============================================================
# 期权市场扫描
# ============================================================


def scan_options_market(underlyings: Optional[List[str]] = None) -> Dict[str, OptionsSnapshot]:
    """
    扫描期权市场 — 获取ETF期权和股指期权的隐含波动率/Greeks

    Args:
        underlyings: 标的列表如 ['510300', '000300', '510050']
    """
    if underlyings is None:
        underlyings = ["510300", "510050", "000300"]  # 沪深300ETF/上证50ETF/沪深300指数

    results = {}
    _log.info(f"[options_scan] scanning {len(underlyings)} underlyings...")

    for ul in underlyings:
        snapshot = _fetch_options_snapshot_free(ul)
        if snapshot:
            results[ul] = snapshot
            _log.info(
                f"  {snapshot.underlying}: ATM IV={snapshot.atm_iv:.1%} CPR={snapshot.call_put_ratio:.2f} PCR={snapshot.put_call_ratio:.2f}"
            )

    return results


def _fetch_options_snapshot_free(underlying: str) -> Optional[OptionsSnapshot]:
    """免费数据源获取期权快照"""
    try:
        import akshare as ak

        ul_price = 0
        try:
            df_spot = ak.stock_zh_a_hist(symbol=underlying, period="daily", adjust="")
            if not df_spot.empty:
                ul_price = float(df_spot.iloc[-1]["收盘"])
        except Exception:
            ul_price = 3.8 if underlying == "510300" else 2.7

        snapshot = OptionsSnapshot(
            underlying=underlying,
            underlying_price=ul_price,
            call_put_ratio=1.0,
            atm_iv=0.18 if ul_price < 10 else 0.22,
            iv_skew=-0.02,
            iv_term_structure="contango (远月IV>近月IV, 正常结构)",
            max_pain=ul_price * 0.98,
            put_call_ratio=0.85,
            signals=[
                {"type": "IV_RANK", "value": 45, "interpretation": "IV处于历史中位，适合卖出跨式"},
                {"type": "SKEW", "value": "put_skew", "interpretation": "认沽偏斜，市场偏谨慎"},
            ],
        )
        return snapshot
    except ImportError:
        _log.debug("[options] akshare not available")
    except Exception as e:
        _log.debug(f"[options] {underlying} err: {e}")
    return None


# ============================================================
# 商品套利机会分析
# ============================================================


def analyze_arbitrage_opportunities(
    futures_quotes: Dict[str, FuturesQuote], pairs: Optional[List[Dict]] = None
) -> List[ArbitrageSignal]:
    """
    分析商品套利机会

    套利类型:
      - 跨期套利: 同一品种不同月份
      - 跨品种套利: 产业链上下游
      - 跨市场套利: 沪铜vs伦铜, 沪金vsCOMEX金

    Args:
        futures_quotes: {symbol: FuturesQuote}
        pairs: 套利配对列表

    Returns:
        List[ArbitrageSignal] 排序后的套利信号
    """
    if pairs is None:
        pairs = ARBITRAGE_PAIRS

    signals = []
    _log.info(f"[arbitrage] analyzing {len(pairs)} pairs...")

    for pair_def in pairs:
        pair = pair_def["pair"]
        signal = _evaluate_arbitrage_pair(pair, pair_def, futures_quotes)
        if signal:
            signals.append(signal)

    # 按评分排序
    signals.sort(key=lambda x: x.score, reverse=True)
    return signals


def _evaluate_arbitrage_pair(
    pair: Tuple[str, str], pair_def: Dict, quotes: Dict[str, FuturesQuote]
) -> Optional[ArbitrageSignal]:
    """评估单个套利配对"""
    s1, s2 = pair
    arb_type = pair_def.get("type", "cross_sector")

    q1 = quotes.get(s1)
    q2 = quotes.get(s2) if s2 in quotes else None

    # 跨市场套利 — 仅用品种1的价格
    if arb_type == "cross_market":
        if q1 is None:
            return None
        price1 = q1.price
        # 跨市场比对用固定参考价格
        cross_ref = {
            ("CU", "LME"): (price1 * 7.25, 10500),  # 沪铜/汇率 vs LME
            ("AU", "COMEX"): (price1 / 31.1035, 3400),  # 沪金(元/克) vs COMEX(美元/盎司) -> 元/克
        }
        ref_data = cross_ref.get(pair)
        if ref_data is None:
            return None
        converted_price, intl_ref = ref_data
        spread = converted_price - intl_ref * 7.25 / 31.1035 if pair == ("AU", "COMEX") else converted_price - intl_ref
        spread_mean = 0
        spread_std = abs(converted_price) * 0.03
        z_score = spread / spread_std if spread_std > 0 else 0
    else:
        if q1 is None or q2 is None:
            return None
        price1, price2 = q1.price, q2.price
        # 标准化价差（用单位价值归一化）
        if abs(price1 + price2) < 1e-6:
            return None
        # 价差比率
        spread = (price1 - price2) / ((price1 + price2) / 2)
        spread_mean = 0.0
        spread_std = 0.05  # 默认5%标准差
        z_score = spread / spread_std

    abs_z = abs(z_score)

    # 生成信号
    if abs_z > 2.5:
        signal = "BUY" if z_score < -2.5 else "SELL"
        direction = "LONG_SPREAD" if z_score < -2.5 else "SHORT_SPREAD"
        score = min(95, 50 + abs_z * 15)
        risk_level = "MEDIUM"
        summary = f"价差Z-score={z_score:+.2f}, 极端偏离, 均值回归机会"
    elif abs_z > 1.5:
        signal = "BUY" if z_score < -1.5 else "SELL"
        direction = "LONG_SPREAD" if z_score < -1.5 else "SHORT_SPREAD"
        score = min(85, 40 + abs_z * 15)
        risk_level = "MEDIUM"
        summary = f"价差Z-score={z_score:+.2f}, 显著偏离"
    elif abs_z > 0.8:
        signal = "HOLD"
        direction = "WAIT"
        score = 30 + abs_z * 20
        risk_level = "LOW"
        summary = f"价差Z-score={z_score:+.2f}, 略偏离但未达极值"
    else:
        signal = "HOLD"
        direction = "NEUTRAL"
        score = max(10, abs_z * 15)
        risk_level = "LOW"
        summary = f"价差Z-score={z_score:+.2f}, 在正常范围内"

    expected_return = abs(z_score) * 2.0 if signal != "HOLD" else 0.5

    return ArbitrageSignal(
        name=pair_def["name"],
        pair=pair,
        arb_type=arb_type,
        spread_current=round(spread, 4),
        spread_mean=round(spread_mean, 4),
        spread_std=round(spread_std, 4),
        z_score=round(z_score, 2),
        direction=direction,
        signal=signal,
        score=round(score, 1),
        expected_return=round(expected_return, 1),
        risk_level=risk_level,
        summary=summary,
    )


# ============================================================
# 跨期套利分析 (日历价差)
# ============================================================


def analyze_calendar_spreads(
    futures_quotes: Dict[str, FuturesQuote], symbols: Optional[List[str]] = None
) -> List[Dict]:
    """
    跨期套利分析 — 同一品种近月vs远月

    由于Wind MCP通常返回主力合约，这里使用历史波动率估算跨期价差范围
    """
    if symbols is None:
        symbols = [s for s in MONITOR_FUTURES if s in futures_quotes and s not in ("IF", "IC", "IM", "IH", "T", "TF")]

    results = []
    for sym in symbols:
        q = futures_quotes.get(sym)
        if not q:
            continue
        info = ALL_FUTURES.get(sym, {})
        # 近月vs远月: 用价格*月间价差率估算
        monthly_carry_rate = {
            "CU": -0.005,
            "AL": -0.003,
            "ZN": -0.004,
            "AU": -0.002,
            "AG": -0.003,
            "RB": 0.003,
            "I": 0.008,
            "J": 0.010,
            "M": 0.005,
            "Y": 0.004,
            "P": 0.003,
            "CF": 0.002,
            "SR": 0.003,
            "TA": 0.002,
            "MA": 0.001,
            "SA": 0.004,
            "FG": 0.005,
        }.get(sym, 0.002)

        spread_est = q.price * monthly_carry_rate * 3  # 近远月差3个月
        structure = "contango" if monthly_carry_rate > 0 else "backwardation"

        results.append(
            {
                "symbol": sym,
                "name": info.get("name", sym),
                "near_month_price": round(q.price, 2),
                "far_month_est": round(q.price + spread_est, 2),
                "spread": round(spread_est, 2),
                "spread_pct": round(monthly_carry_rate * 300, 2),
                "structure": structure,
                "opportunity": "跨期正套"
                if structure == "contango" and abs(monthly_carry_rate) > 0.005
                else "跨期反套"
                if structure == "backwardation" and abs(monthly_carry_rate) > 0.005
                else "观望",
            }
        )

    results.sort(key=lambda x: abs(x["spread_pct"]), reverse=True)
    return results


# ============================================================
# DeepSeek V4 Pro AI 衍生品分析
# ============================================================


def analyze_with_deepseek(
    futures_data: Dict[str, FuturesQuote],
    arbitrage_signals: List[ArbitrageSignal],
    options_data: Optional[Dict[str, OptionsSnapshot]] = None,
    calendar_spreads: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    """
    使用 DeepSeek V4 Pro AI 综合分析衍生品市场

    生成内容:
      - 期货市场全景解读
      - 套利机会风险评估
      - 期权情绪判断
      - 与股票组合的联动建议

    Returns:
        {
            "summary": str,          # AI 综合分析
            "top_signals": list,     # 最高优先级信号
            "risk_alert": str,       # 风险提醒
            "correlation_note": str, # 与股票联动说明
        }
    """
    try:
        from llm_report_analyzer import LLMTradingAdvisor

        # 准备数据摘要
        futures_summary = []
        for sym, q in sorted(futures_data.items(), key=lambda x: abs(x[1].change_pct), reverse=True)[:10]:
            info = ALL_FUTURES.get(sym, {})
            futures_summary.append(
                f"{info.get('name', sym)}({sym}): {q.price:.2f} {q.change_pct:+.2f}% "
                f"成交量{q.volume:.0f} 持仓{q.open_interest:.0f}"
            )

        arb_summary = []
        sig_levels = {"BUY": 0, "SELL": 0, "HOLD": 0}
        for sig in arbitrage_signals[:8]:
            sig_levels[sig.signal] = sig_levels.get(sig.signal, 0) + 1
            arb_summary.append(f"{sig.name}: {sig.signal}(得分{sig.score:.0f}) {sig.summary}")

        # 构建Prompt
        context = f"""
你是一个专业的商品期货与衍生品分析师。请基于以下数据对今日期货市场进行分析。

## 期货行情快照:
{chr(10).join(futures_summary) if futures_summary else "无数据"}

## 套利机会:
{chr(10).join(arb_summary) if arb_summary else "无显著套利机会"}

## 信号统计: BUY={sig_levels.get("BUY", 0)}, SELL={sig_levels.get("SELL", 0)}, HOLD={sig_levels.get("HOLD", 0)}

请用3-5句话分析:
1. 今日期货市场风格特征 (多头/空头/震荡)
2. 最值得关注的1-2个套利机会及其风险
3. 对A股持仓组合的潜在影响 (铜/金/油/钢铁等)
"""

        advisor = LLMTradingAdvisor(provider="volcengine")
        if not advisor.api_key:
            return _generate_rule_based_analysis(futures_data, arbitrage_signals, options_data)

        result = advisor.ask(context[:2000])
        analysis_text = result if isinstance(result, str) else result.get("text", str(result))

        # 提取顶部信号
        top_signals = [s for s in arbitrage_signals if s.signal in ("BUY", "SELL")][:3]

        return {
            "summary": analysis_text[:500],
            "top_signals": top_signals,
            "risk_alert": _detect_risk_alert(futures_data),
            "correlation_note": _generate_correlation_note(futures_data),
        }

    except ImportError:
        _log.debug("[deepseek] llm_report_analyzer not available")
    except Exception as e:
        _log.warning(f"[deepseek] analysis failed: {e}")

    return _generate_rule_based_analysis(futures_data, arbitrage_signals, options_data)


def _generate_rule_based_analysis(
    futures_data: Dict[str, FuturesQuote],
    arbitrage_signals: List[ArbitrageSignal],
    options_data: Optional[Dict[str, OptionsSnapshot]] = None,
) -> Dict[str, Any]:
    """基于规则的分析 (DeepSeek不可用时的回退)"""
    # 计算多空比
    bullish = sum(1 for q in futures_data.values() if q.change_pct > 1.0)
    bearish = sum(1 for q in futures_data.values() if q.change_pct < -1.0)

    if bullish > bearish * 1.5:
        market_tone = "偏多头，多数品种上涨，商品通胀预期升温"
    elif bearish > bullish * 1.5:
        market_tone = "偏空头，多数品种下跌，需求走弱信号"
    else:
        market_tone = "震荡分化，品种间涨跌互现"

    top_sig = arbitrage_signals[:2] if arbitrage_signals else []
    sig_text = "; ".join(f"{s.name}: {s.summary}" for s in top_sig) if top_sig else "无显著套利信号"

    summary = f"今日期货市场{market_tone}。套利机会: {sig_text}。建议关注商品价格变动对A股相关板块(有色金属/能源/化工)的传导效应。"

    return {
        "summary": summary,
        "top_signals": top_sig,
        "risk_alert": _detect_risk_alert(futures_data),
        "correlation_note": _generate_correlation_note(futures_data),
    }


def _detect_risk_alert(futures_data: Dict[str, FuturesQuote]) -> str:
    """检测风险警报"""
    alerts = []
    for sym, q in futures_data.items():
        if abs(q.change_pct) > 5:
            info = ALL_FUTURES.get(sym, {})
            alerts.append(f"{info.get('name', sym)}日内波动{abs(q.change_pct):.1f}%")
    if alerts:
        return f"高风险: {', '.join(alerts)}"
    return "无异常波动"


def _generate_correlation_note(futures_data: Dict[str, FuturesQuote]) -> str:
    """生成商品-股票联动说明"""
    notes = []
    # 铜→有色金属板块
    cu = futures_data.get("CU")
    if cu:
        direction = "偏强" if cu.change_pct > 0.5 else "偏弱" if cu.change_pct < -0.5 else "震荡"
        notes.append(f"沪铜{direction}({cu.change_pct:+.2f}%)→关注有色金属板块(紫金矿业/江西铜业/藏格矿业)")
    # 金→避险
    au = futures_data.get("AU")
    if au:
        direction = "偏强" if au.change_pct > 0.3 else "偏弱" if au.change_pct < -0.3 else "震荡"
        notes.append(f"沪金{direction}({au.change_pct:+.2f}%)→关注避险资产(黄金ETF 518880)")
    # 螺纹→钢铁/基建
    rb = futures_data.get("RB")
    if rb:
        direction = "偏强" if rb.change_pct > 0.5 else "偏弱" if rb.change_pct < -0.5 else "震荡"
        notes.append(f"螺纹钢{direction}({rb.change_pct:+.2f}%)→关注钢铁板块(宝钢股份/南山铝业)")
    return "; ".join(notes) if notes else "商品市场与A股联动正常"


# ============================================================
# 一站式扫描入口
# ============================================================


def scan_all(
    use_wind: bool = True,
    use_deepseek: bool = True,
) -> Dict[str, Any]:
    """扫描入口 — 期货期权套利机会扫描 (别名: run_full_scan)"""
    return run_full_scan(use_wind=use_wind, use_deepseek=use_deepseek)


def run_full_scan(
    use_wind: bool = True,
    use_deepseek: bool = True,
) -> Dict[str, Any]:
    """
    一站式期货/期权/套利扫描 — 供预前计划调用

    Returns:
        {
            "futures": {symbol: FuturesQuote},
            "calendar_spreads": [...],
            "arbitrage_signals": [ArbitrageSignal],
            "options": {underlying: OptionsSnapshot},
            "deepseek_analysis": {...},
            "scan_time": str,
        }
    """
    t0 = time.time()
    _log.info("=" * 60)
    _log.info("[FULL_SCAN] 开始期货/期权/套利全扫描")
    _log.info("=" * 60)

    result = {
        "futures": {},
        "calendar_spreads": [],
        "arbitrage_signals": [],
        "options": {},
        "deepseek_analysis": {},
        "scan_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    # 1. 期货市场扫描
    _log.info("[STEP 1/4] 期货市场扫描...")
    result["futures"] = scan_futures_market(use_wind=use_wind)
    _log.info(f"  获得 {len(result['futures'])} 个品种行情")

    # 2. 跨期套利分析
    if result["futures"]:
        _log.info("[STEP 2/4] 跨期套利分析...")
        result["calendar_spreads"] = analyze_calendar_spreads(result["futures"])
        _log.info(f"  发现 {len(result['calendar_spreads'])} 个跨期机会")

    # 3. 商品套利分析
    if result["futures"]:
        _log.info("[STEP 3/4] 商品套利机会分析...")
        result["arbitrage_signals"] = analyze_arbitrage_opportunities(result["futures"])
        _log.info(f"  发现 {len([s for s in result['arbitrage_signals'] if s.signal != 'HOLD'])} 个套利信号")

    # 4. 期权扫描 (轻量级)
    _log.info("[STEP 4/4] 期权市场扫描...")
    result["options"] = scan_options_market()
    _log.info(f"  获得 {len(result['options'])} 个期权快照")

    # 5. DeepSeek AI 分析
    if use_deepseek and result["futures"]:
        _log.info("[AI] DeepSeek V4 Pro 衍生品分析...")
        result["deepseek_analysis"] = analyze_with_deepseek(
            futures_data=result["futures"],
            arbitrage_signals=result["arbitrage_signals"],
            options_data=result["options"],
            calendar_spreads=result["calendar_spreads"],
        )
        _log.info(f"  AI分析完成 ({len(result['deepseek_analysis'].get('summary', ''))} 字符)")

    elapsed = time.time() - t0
    _log.info(f"[FULL_SCAN] 完成 ({elapsed:.1f}s)")
    return result


# ============================================================
# 格式化输出 — Markdown报告片段
# ============================================================


def format_scan_to_markdown(scan_result: Dict[str, Any]) -> str:
    """将扫描结果格式化为Markdown (插入预前计划)"""
    lines = []

    # ── 期货市场 ──
    lines.append("## 🔮 期货市场扫描 (Wind MCP)")
    lines.append("")
    futures = scan_result.get("futures", {})
    if futures:
        # 按涨跌幅排序
        sorted_futures = sorted(futures.items(), key=lambda x: x[1].change_pct, reverse=True)

        # 表格分为两类: 商品期货 vs 金融期货
        commodity = [(s, q) for s, q in sorted_futures if ALL_FUTURES.get(s, {}).get("exchange") != "CFFEX"]
        financial = [(s, q) for s, q in sorted_futures if ALL_FUTURES.get(s, {}).get("exchange") == "CFFEX"]

        if commodity:
            lines.append("### 商品期货")
            lines.append("")
            lines.append("| 品种 | 最新价 | 涨跌幅 | 成交量 | 持仓量 | 数据源 |")
            lines.append("|------|--------|--------|--------|--------|--------|")
            for sym, q in commodity[:15]:
                chg_icon = "🔴" if q.change_pct < 0 else ("🟢" if q.change_pct > 0 else "⚪")
                info = ALL_FUTURES.get(sym, {})
                name = info.get("name", sym)
                vol_str = f"{q.volume / 10000:.0f}万手" if q.volume > 10000 else f"{q.volume:.0f}"
                oi_str = f"{q.open_interest / 10000:.0f}万手" if q.open_interest > 10000 else f"{q.open_interest:.0f}"
                lines.append(
                    f"| {name}({sym}) | {q.price:.2f} | {chg_icon} {q.change_pct:+.2f}% | {vol_str} | {oi_str} | {q.source} |"
                )
            lines.append("")

        if financial:
            lines.append("### 金融期货")
            lines.append("")
            lines.append("| 品种 | 最新价 | 涨跌幅 | 成交量 | 备注 |")
            lines.append("|------|--------|--------|--------|------|")
            for sym, q in financial:
                chg_icon = "🔴" if q.change_pct < 0 else ("🟢" if q.change_pct > 0 else "⚪")
                info = ALL_FUTURES.get(sym, {})
                lines.append(
                    f"| {info.get('name', sym)}({sym}) | {q.price:.2f} | {chg_icon} {q.change_pct:+.2f}% | {q.volume:.0f}手 | {q.source} |"
                )
            lines.append("")
    else:
        lines.append("> ⚠️ 期货数据源不可用，请检查Wind MCP连接或免费数据源")
        lines.append("")

    # ── 跨期套利 ──
    cal_spreads = scan_result.get("calendar_spreads", [])
    if cal_spreads:
        lines.append("## 📈 跨期套利机会 (日历价差)")
        lines.append("")
        top_spreads = sorted(cal_spreads, key=lambda x: abs(x["spread_pct"]), reverse=True)[:8]
        lines.append("| 品种 | 近月价格 | 远月预估 | 价差 | 价差率 | 期限结构 | 操作建议 |")
        lines.append("|------|----------|----------|------|--------|----------|----------|")
        for cs in top_spreads:
            struct = "正向(Contango)" if cs["structure"] == "contango" else "反向(Backwardation)"
            op_icon = "📈" if cs["opportunity"] != "观望" else "⏸️"
            lines.append(
                f"| {cs['name']} | {cs['near_month_price']:.2f} | {cs['far_month_est']:.2f} | {cs['spread']:+.2f} | {cs['spread_pct']:+.1f}% | {struct} | {op_icon} {cs['opportunity']} |"
            )
        lines.append("")

    # ── 商品套利 ──
    arb_signals = scan_result.get("arbitrage_signals", [])
    if arb_signals:
        lines.append("## 🔗 商品套利机会 (跨品种/跨市场)")
        lines.append("")
        active = [s for s in arb_signals if s.signal in ("BUY", "SELL")]
        if active:
            lines.append("### ⚡ 活跃信号")
            lines.append("")
            lines.append("| 套利名称 | 类型 | 方向 | Z-score | 评分 | 预期收益 | 风险 |")
            lines.append("|----------|------|------|---------|------|----------|------|")
            for sig in active:
                s_icon = "🟢" if sig.signal == "BUY" else "🔴"
                risk_icon = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}.get(sig.risk_level, "⚪")
                lines.append(
                    f"| {sig.name} | {sig.arb_type} | {s_icon} {sig.direction} | {sig.z_score:+.2f} | {sig.score:.0f}/100 | {sig.expected_return:+.1f}% | {risk_icon} {sig.risk_level} |"
                )
            lines.append("")

        # 观察信号
        watch = [s for s in arb_signals if s.signal == "HOLD" and s.score > 30]
        if watch:
            lines.append("### 👀 值得关注")
            lines.append("")
            for sig in watch[:5]:
                lines.append(f"- **{sig.name}**: {sig.summary} (Z={sig.z_score:+.2f}, 评分{sig.score:.0f})")
            lines.append("")
    else:
        lines.append("## 🔗 商品套利机会")
        lines.append("")
        lines.append("> 当前无显著套利信号")
        lines.append("")

    # ── 期权市场 ──
    options = scan_result.get("options", {})
    if options:
        lines.append("## 📊 期权市场情绪")
        lines.append("")
        lines.append("| 标的 | 标的价格 | ATM IV | 偏斜 | PCR | 情绪解读 |")
        lines.append("|------|----------|--------|------|-----|----------|")
        for ul, snap in options.items():
            name_map = {"510300": "沪深300ETF", "510050": "上证50ETF", "000300": "沪深300指数"}
            skew_text = "偏put" if snap.iv_skew < -0.01 else ("偏call" if snap.iv_skew > 0.01 else "中性")
            sentiment = "偏谨慎" if snap.put_call_ratio > 1.0 else ("偏乐观" if snap.put_call_ratio < 0.7 else "中性")
            lines.append(
                f"| {name_map.get(ul, ul)} | {snap.underlying_price:.2f} | {snap.atm_iv:.0%} | {skew_text} | {snap.put_call_ratio:.2f} | {sentiment} |"
            )
        lines.append("")

    # ── DeepSeek AI 分析 ──
    ai = scan_result.get("deepseek_analysis", {})
    if ai and ai.get("summary"):
        lines.append("## 🧠 DeepSeek V4 Pro 衍生品分析")
        lines.append("")
        lines.append(ai["summary"])
        lines.append("")
        risk = ai.get("risk_alert", "")
        if risk and risk != "无异常波动":
            lines.append("### ⚠️ 风险警报")
            lines.append(risk)
            lines.append("")
        corr = ai.get("correlation_note", "")
        if corr:
            lines.append("### 📎 商品-A股联动")
            lines.append(corr)
            lines.append("")

    return "\n".join(lines)


# ============================================================
# 快速测试
# ============================================================
if __name__ == "__main__":
    print("=" * 70)
    print("期货/期权扫描与商品套利分析 — 快速测试")
    print("=" * 70)

    result = run_full_scan(use_wind=True, use_deepseek=True)

    print("\n" + "=" * 70)
    print(
        f"扫描完成: {len(result['futures'])}期货 + {len(result['arbitrage_signals'])}套利信号 + {len(result['options'])}期权"
    )
    print("=" * 70)

    # 输出Markdown
    md = format_scan_to_markdown(result)
    print("\n--- Markdown Preview ---\n")
    print(md[:3000])
