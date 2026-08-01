"""
同花顺期货通模拟盘适配器
========================

功能:
    1. 通过 iFinD 获取期货行情作为价格发现（替代 MockBroker 的静态价格）
    2. 期权模拟盘（SimOptionsBroker）— 支持 ETF期权/股指期权
    3. 交易日志持久化到 logs/ths_sim_trades.jsonl
    4. 预留 CTP/SuperMind 真实下单接口切换点

设计:
    - 实现 SimFuturesBroker 相同接口，可无缝替换
    - 行情获取优先级: iFinD 实时行情 → 本地缓存 → 订单价格兜底
    - 期权定价: Black-Scholes 简化版 + iFinD 期权行情
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("v75.ths_sim_broker")

# ============================================================
# 1. iFinD 行情适配层
# ============================================================

class THSQuoteProvider:
    """同花顺 iFinD 期货行情提供者

    优先级:
        1. iFinD MCP 实时行情
        2. iFinD HTTP API
        3. 本地缓存
        4. 订单价格兜底
    """

    def __init__(self) -> None:
        self._cache: dict[str, dict[str, Any]] = {}
        self._cache_ts: dict[str, float] = {}
        self._cache_ttl = 30  # 秒

        # 尝试加载 iFinD 客户端
        self._ifind = None
        try:
            import sys
            _IFIND_SKILL = os.path.normpath(
                os.path.join(os.path.dirname(__file__), "..", "skills", "ifind-finance-data")
            )
            if _IFIND_SKILL not in sys.path:
                sys.path.insert(0, _IFIND_SKILL)
            from call import call as _ifind_call
            self._ifind = _ifind_call
            logger.info("iFinD 行情提供者已加载")
        except Exception:
            logger.warning("iFinD 不可用，期货行情将使用订单价格兜底")

    def get_futures_quote(self, symbol: str) -> dict[str, Any] | None:
        """获取期货行情

        Args:
            symbol: 合约代码，如 IF2506、CU2406

        Returns:
            {latest, open, high, low, prev_close, volume, ...} 或 None
        """
        # 检查缓存
        now = time.time()
        if symbol in self._cache and (now - self._cache_ts.get(symbol, 0)) < self._cache_ttl:
            return self._cache[symbol]

        # 尝试 iFinD
        if self._ifind:
            try:
                result = self._ifind(
                    tool_name="ifind_real_time_quotation",
                    codes=symbol,
                    indicators="latest,open,high,low,prev_close,volume,avg_price"
                )
                if result:
                    quote = self._parse_ifind_result(result, symbol)
                    if quote:
                        self._cache[symbol] = quote
                        self._cache_ts[symbol] = now
                        return quote
            except Exception as exc:
                logger.debug("iFinD 行情获取失败 %s: %s", symbol, exc)

        return None

    def get_option_quote(self, symbol: str) -> dict[str, Any] | None:
        """获取期权行情

        Args:
            symbol: 期权合约代码，如 510050C2506M03200

        Returns:
            {latest, implied_vol, delta, gamma, theta, vega, ...} 或 None
        """
        now = time.time()
        if symbol in self._cache and (now - self._cache_ts.get(symbol, 0)) < self._cache_ttl:
            return self._cache[symbol]

        if self._ifind:
            try:
                result = self._ifind(
                    tool_name="ifind_real_time_quotation",
                    codes=symbol,
                    indicators="latest,open,high,low,volume,implied_vol,delta,gamma,theta,vega"
                )
                if result:
                    quote = self._parse_ifind_result(result, symbol)
                    if quote:
                        self._cache[symbol] = quote
                        self._cache_ts[symbol] = now
                        return quote
            except Exception as exc:
                logger.debug("iFinD 期权行情获取失败 %s: %s", symbol, exc)

        return None

    def _parse_ifind_result(self, result: Any, symbol: str) -> dict[str, Any] | None:
        """解析 iFinD 返回结果"""
        try:
            if isinstance(result, str):
                data = json.loads(result)
            elif isinstance(result, dict):
                data = result
            else:
                return None

            # iFinD 返回格式: {"data": {"rows": [...], "columns": [...]}}
            rows = data.get("data", {}).get("rows", [])
            if not rows:
                return None

            row = rows[0]
            if isinstance(row, dict):
                return {k: float(v) if v and v != "-" else 0.0 for k, v in row.items()}
            elif isinstance(row, list):
                columns = data.get("data", {}).get("columns", [])
                return {col: float(val) if val and val != "-" else 0.0 for col, val in zip(columns, row)}
        except Exception:
            pass
        return None


# ============================================================
# 2. Black-Scholes 期权定价（简化版）
# ============================================================

def _norm_cdf(x: float) -> float:
    """标准正态分布累积函数"""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_call_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes 认购期权价格

    Args:
        S: 标的现价
        K: 行权价
        T: 剩余时间（年）
        r: 无风险利率
        sigma: 隐含波动率
    """
    if T <= 0 or sigma <= 0:
        return max(S - K, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)


def bs_put_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes 认沽期权价格"""
    if T <= 0 or sigma <= 0:
        return max(K - S, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def bs_greeks(S: float, K: float, T: float, r: float, sigma: float,
              option_type: str = "call") -> dict[str, float]:
    """计算希腊字母

    Returns:
        {delta, gamma, theta, vega}
    """
    if T <= 0 or sigma <= 0:
        return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}

    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    pdf_d1 = math.exp(-0.5 * d1 ** 2) / math.sqrt(2.0 * math.pi)

    gamma = pdf_d1 / (S * sigma * math.sqrt(T))
    vega = S * pdf_d1 * math.sqrt(T) / 100.0  # 每1%波动率变化

    if option_type == "call":
        delta = _norm_cdf(d1)
        theta = (-(S * pdf_d1 * sigma) / (2.0 * math.sqrt(T))
                 - r * K * math.exp(-r * T) * _norm_cdf(d2)) / 365.0
    else:
        delta = _norm_cdf(d1) - 1.0
        theta = (-(S * pdf_d1 * sigma) / (2.0 * math.sqrt(T))
                 + r * K * math.exp(-r * T) * _norm_cdf(-d2)) / 365.0

    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega}


# ============================================================
# 3. 期权模拟盘
# ============================================================

class SimOptionsBroker:
    """期权模拟盘

    支持:
        - ETF期权（50ETF/300ETF/500ETF）
        - 股指期权（IO/MO/HO）
        - 认购/认沽
        - 开仓/平仓
        - BS 定价 + iFinD 行情
        - 希腊字母计算
    """

    # 期权合约乘数
    CONTRACT_MULTIPLIER = {
        "50ETF": 10000,
        "300ETF": 10000,
        "500ETF": 10000,
        "IO": 100,   # 沪深300股指期权
        "MO": 100,   # 中证1000股指期权
        "HO": 100,   # 上证50股指期权
    }

    def __init__(self, account, quote_provider: THSQuoteProvider | None = None):
        self.account = account
        self.quote_provider = quote_provider or THSQuoteProvider()
        self._pending_orders: dict[str, dict] = {}
        self._fills: list[dict] = []
        self._lock = threading.RLock()

    def _parse_option_symbol(self, symbol: str) -> dict[str, Any]:
        """解析期权合约代码

        格式:
            50ETF:  510050C2506M03200 (代码+C/P+年月+M+行权价)
            股指:   IO2506-C-3900
        """
        s = str(symbol).upper().strip()
        info: dict[str, Any] = {"symbol": symbol}

        # 股指期权: IO2506-C-3900
        if "-" in s:
            parts = s.split("-")
            if len(parts) >= 3:
                info["code"] = parts[0]
                info["type"] = "call" if parts[1] == "C" else "put"
                info["strike"] = float(parts[2])
                return info

        # ETF期权: 510050C2506M03200
        for prefix in ("510050", "510300", "510500", "159919", "588080"):
            if s.startswith(prefix):
                rest = s[len(prefix):]
                info["code"] = prefix
                info["type"] = "call" if rest[0] == "C" else "put"
                # 提取行权价
                if "M" in rest:
                    strike_str = rest.split("M")[-1]
                    info["strike"] = float(strike_str) / 1000.0
                return info

        return info

    def _estimate_option_price(self, symbol: str, underlying_price: float) -> tuple[float, dict[str, float]]:
        """估算期权价格（BS模型）

        Returns:
            (price, greeks)
        """
        info = self._parse_option_symbol(symbol)
        K = info.get("strike", underlying_price)
        S = underlying_price
        T = 30.0 / 365.0  # 默认30天到期
        r = 0.02  # 无风险利率
        sigma = 0.25  # 默认隐含波动率

        # 尝试从 iFinD 获取期权行情
        quote = self.quote_provider.get_option_quote(symbol)
        if quote and quote.get("latest", 0) > 0:
            price = float(quote["latest"])
            greeks = {
                "delta": float(quote.get("delta", 0)),
                "gamma": float(quote.get("gamma", 0)),
                "theta": float(quote.get("theta", 0)),
                "vega": float(quote.get("vega", 0)),
            }
            return price, greeks

        # BS 定价
        if info.get("type") == "put":
            price = bs_put_price(S, K, T, r, sigma)
        else:
            price = bs_call_price(S, K, T, r, sigma)

        greeks = bs_greeks(S, K, T, r, sigma, info.get("type", "call"))
        return max(price, 0.0001), greeks

    def place_order(self, symbol: str, qty: int, side: str,
                    price: float = 0.0, order_type: str = "LIMIT",
                    session: str = "day",
                    underlying_price: float = 0.0,
                    option_type: str | None = None,
                    strike: float | None = None) -> dict:
        """期权下单

        Args:
            symbol: 期权合约代码
            qty: 手数
            side: BUY_OPEN / SELL_OPEN / BUY_CLOSE / SELL_CLOSE
            price: 限价（0=市价）
            underlying_price: 标的价格（用于 BS 定价）
            option_type: call/put
            strike: 行权价
        """
        if qty <= 0:
            return {"order_id": "", "status": "REJECTED", "reason": "数量必须大于0"}

        # 获取标的现价
        if underlying_price <= 0:
            underlying_price = 4.0  # 默认 ETF 价格

        # 期权定价
        est_price, greeks = self._estimate_option_price(symbol, underlying_price)

        # 限价单检查
        if price <= 0:
            price = est_price  # 市价单用理论价

        # 合约乘数
        info = self._parse_option_symbol(symbol)
        code = info.get("code", "")
        multiplier = self.CONTRACT_MULTIPLIER.get(code, 10000)

        # 保证金检查（卖方需要保证金）
        if side in ("SELL_OPEN",):
            margin = est_price * qty * multiplier * 0.15  # 简化保证金
            if margin > self.account.available_cash:
                return {"order_id": "", "status": "REJECTED",
                        "reason": f"保证金不足: 需要{margin:.0f}, 可用{self.account.available_cash:.0f}"}

        order_id = f"OPT-{symbol}-{int(time.time() * 1000)}-{qty}"
        order = {
            "order_id": order_id,
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "price": float(price),
            "est_price": round(est_price, 4),
            "order_type": order_type,
            "status": "PENDING",
            "timestamp": datetime.now().isoformat(),
            "session": session,
            "market": "options",
            "multiplier": multiplier,
            "greeks": greeks,
            "underlying_price": underlying_price,
            "option_type": option_type or info.get("type", "call"),
            "strike": strike or info.get("strike", 0.0),
        }
        self._pending_orders[order_id] = order

        # 模拟成交
        fill = self._simulate_fill(order)
        return fill

    def _simulate_fill(self, order: dict) -> dict:
        """模拟成交"""
        symbol = order["symbol"]
        qty = int(order["qty"])
        side = order["side"]
        price = float(order["price"])
        est_price = float(order.get("est_price", price))
        multiplier = int(order.get("multiplier", 10000))

        # 模拟滑点
        slippage = 0.001  # 期权滑点 0.1%
        if side in ("BUY_OPEN", "BUY_CLOSE"):
            fill_price = price * (1 + slippage)
        else:
            fill_price = price * (1 - slippage)

        amount = qty * fill_price * multiplier

        fill = {
            "order_id": order["order_id"],
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "price": round(fill_price, 4),
            "est_price": round(est_price, 4),
            "amount": round(amount, 2),
            "multiplier": multiplier,
            "slippage_pct": slippage,
            "status": "FILLED",
            "session": order.get("session", "day"),
            "market": "options",
            "greeks": order.get("greeks", {}),
            "underlying_price": order.get("underlying_price", 0),
            "option_type": order.get("option_type", "call"),
            "strike": order.get("strike", 0),
            "timestamp": datetime.now().isoformat(),
        }

        with self._lock:
            self._fills.append(fill)

        # 更新持仓
        self._update_position(fill)
        return fill

    def _update_position(self, fill: dict) -> None:
        """更新期权持仓"""
        symbol = fill["symbol"]
        qty = int(fill["qty"])
        side = fill["side"]
        price = float(fill["price"])
        multiplier = int(fill.get("multiplier", 10000))

        with self._lock:
            positions = self.account.positions
            if symbol not in positions:
                positions[symbol] = {"qty": 0, "avg_price": 0.0, "market_value": 0.0,
                                     "side": "", "multiplier": multiplier}

            pos = positions[symbol]
            if side in ("BUY_OPEN", "SELL_CLOSE"):
                if side == "BUY_OPEN":
                    total_cost = pos["qty"] * pos["avg_price"] + qty * price
                    pos["qty"] += qty
                    pos["avg_price"] = total_cost / pos["qty"] if pos["qty"] > 0 else 0.0
                    pos["side"] = "long"
                else:
                    pos["qty"] -= qty
                    if pos["qty"] <= 0:
                        pos["qty"] = 0
                        pos["avg_price"] = 0.0
            else:  # SELL_OPEN / BUY_CLOSE
                if side == "SELL_OPEN":
                    total_cost = abs(pos["qty"]) * pos["avg_price"] + qty * price
                    pos["qty"] -= qty
                    pos["avg_price"] = total_cost / abs(pos["qty"]) if pos["qty"] != 0 else 0.0
                    pos["side"] = "short"
                else:
                    pos["qty"] += qty
                    if pos["qty"] >= 0:
                        pos["qty"] = 0
                        pos["avg_price"] = 0.0

            pos["market_value"] = abs(pos["qty"]) * price * multiplier
            pos["last_update"] = datetime.now().isoformat()

    def get_positions(self) -> dict[str, dict]:
        with self._lock:
            return dict(self.account.positions)

    def get_account(self):
        return self.account

    def get_fills(self, session: str | None = None) -> list[dict]:
        with self._lock:
            if session:
                return [f for f in self._fills if f.get("session") == session]
            return list(self._fills)

    def get_greek_exposure(self) -> dict[str, float]:
        """获取组合希腊字母暴露"""
        total_delta = 0.0
        total_gamma = 0.0
        total_theta = 0.0
        total_vega = 0.0

        with self._lock:
            for _symbol, pos in self.account.positions.items():
                qty = pos.get("qty", 0)
                if qty == 0:
                    continue
                greeks = pos.get("greeks", {})
                multiplier = pos.get("multiplier", 10000)
                sign = 1 if qty > 0 else -1
                total_delta += sign * abs(qty) * multiplier * greeks.get("delta", 0)
                total_gamma += sign * abs(qty) * multiplier * greeks.get("gamma", 0)
                total_theta += sign * abs(qty) * greeks.get("theta", 0)
                total_vega += sign * abs(qty) * greeks.get("vega", 0)

        return {
            "delta": total_delta,
            "gamma": total_gamma,
            "theta": total_theta,
            "vega": total_vega,
        }


# ============================================================
# 4. 同花顺期货通模拟盘适配器
# ============================================================

class THSSimFuturesBroker:
    """同花顺期货通模拟盘适配器

    功能:
        - 使用 iFinD/AKShare 获取期货行情作为价格发现
        - 与 SimFuturesBroker 接口完全兼容
        - 交易日志持久化到 logs/ths_sim_trades.jsonl
        - 预留 CTP/SuperMind 真实下单接口
        - 完整支持 50+ 期货品种
    """

    FUTURES_CONTRACT_MULTIPLIERS = {
        # 股指期货
        "IF": 300,    # 沪深300股指
        "IC": 200,    # 中证500股指
        "IM": 200,    # 中证1000股指
        "IH": 300,    # 上证50股指
        # 国债期货
        "T": 100000,  # 10年期国债
        "TF": 100000, # 5年期国债
        "TS": 200000, # 2年期国债
        # 有色金属
        "CU": 5,      # 沪铜
        "AL": 5,      # 沪铝
        "ZN": 5,      # 沪锌
        "NI": 1,      # 沪镍
        "PB": 5,      # 沪铅
        "SN": 1,      # 沪锡
        # 贵金属
        "AU": 1000,   # 沪金(克)
        "AG": 15,     # 沪银(千克)
        # 能源化工
        "SC": 1000,   # 原油(桶)
        "LU": 1000,   # 低硫燃油
        "FU": 10,     # 燃油(吨)
        "RB": 10,     # 螺纹钢(吨)
        "HC": 10,     # 热轧卷板(吨)
        "I": 100,     # 铁矿石(吨)
        "J": 100,     # 焦炭(吨)
        "JM": 60,     # 焦煤(吨)
        "ZC": 100,    # 动力煤(吨)
        # 农产品
        "A": 10,      # 豆一(吨)
        "B": 10,      # 豆二(吨)
        "M": 10,      # 豆粕(吨)
        "Y": 10,      # 豆油(吨)
        "P": 10,      # 棕榈油(吨)
        "C": 20,      # 玉米(吨)
        "CS": 20,     # 淀粉(吨)
        "JD": 5,      # 鸡蛋(吨)
        # 软商品
        "CF": 5,      # 棉花(吨)
        "TA": 5,      # PTA(吨)
        "MA": 10,     # 甲醇(吨)
        "FG": 20,     # 玻璃(吨)
        "SA": 20,     # 纯碱(吨)
        "SP": 10,     # 纸浆(吨)
        "UR": 20,     # 尿素(吨)
        # 塑料化工
        "V": 5,       # PVC(吨)
        "PP": 5,      # 聚丙烯(吨)
        "L": 5,       # 塑料(吨)
        "EB": 5,      # 苯乙烯(吨)
        "PG": 20,     # LPG(吨)
        # 其他
        "SS": 5,      # 不锈钢(吨)
        "NR": 10,     # 20号胶(吨)
        "RR": 20,     # 粳米(吨)
        "RS": 10,     # 油菜籽(吨)
        "OI": 10,     # 菜油(吨)
        "RM": 10,     # 菜粕(吨)
        "AP": 10,     # 苹果(吨)
        "CJ": 10,     # 红枣(吨)
        "SF": 5,      # 硅铁(吨)
        "SM": 5,      # 锰硅(吨)
    }

    DEFAULT_MARGIN_RATES = {
        "IF": 0.12, "IC": 0.14, "IM": 0.15, "IH": 0.12,
        "T": 0.02, "TF": 0.02, "TS": 0.02,
        "CU": 0.10, "AL": 0.10, "ZN": 0.12, "NI": 0.12, "PB": 0.10, "SN": 0.12,
        "AU": 0.10, "AG": 0.12,
        "SC": 0.15, "LU": 0.12, "FU": 0.12,
        "RB": 0.13, "HC": 0.12, "I": 0.14, "J": 0.15, "JM": 0.15, "ZC": 0.14,
        "A": 0.10, "B": 0.10, "M": 0.10, "Y": 0.10, "P": 0.10, "C": 0.08, "CS": 0.08, "JD": 0.12,
        "CF": 0.12, "TA": 0.10, "MA": 0.12, "FG": 0.12, "SA": 0.12, "SP": 0.12, "UR": 0.12,
        "V": 0.10, "PP": 0.10, "L": 0.10, "EB": 0.12, "PG": 0.12,
        "SS": 0.12, "NR": 0.12, "RR": 0.08, "RS": 0.10, "OI": 0.10, "RM": 0.10,
        "AP": 0.15, "CJ": 0.15, "SF": 0.12, "SM": 0.12,
    }

    def __init__(self, account, quote_provider: THSQuoteProvider | None = None,
                 margin_rates: dict[str, float] | None = None,
                 trade_log_path: str | None = None):
        self.account = account
        self.quote_provider = quote_provider or THSQuoteProvider()
        self.margin_rates = margin_rates or self.DEFAULT_MARGIN_RATES
        self._pending_orders: dict[str, dict] = {}
        self._fills: list[dict] = []
        self._lock = threading.RLock()

        # 交易日志
        log_dir = Path(trade_log_path) if trade_log_path else Path("logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        self._trade_log = log_dir / "ths_sim_trades.jsonl"

        # 夜盘时段信息（与 SimFuturesBroker 兼容）
        try:
            from sim_broker_integration import FUTURES_NIGHT_SESSIONS
            self._night_session_info = FUTURES_NIGHT_SESSIONS
        except Exception:
            self._night_session_info = {
                "IF": {"name": "沪深300股指", "night_start": "21:00", "night_end": "23:00"},
                "IC": {"name": "中证500股指", "night_start": "21:00", "night_end": "23:00"},
                "IM": {"name": "中证1000股指", "night_start": "21:00", "night_end": "23:00"},
            }

        # 预留 CTP 接口
        self._ctp_enabled = False
        self._ctp_api = None

        # AKShare 行情回退
        self._akshare_available = False
        try:
            import akshare as ak
            self._akshare = ak
            self._akshare_available = True
            logger.info("AKShare 行情回退已启用")
        except Exception:
            logger.warning("AKShare 不可用，行情回退将受限")

    def enable_ctp(self, broker_id: str, app_id: str, auth_code: str,
                   front_address: str = "") -> bool:
        """启用 CTP 真实下单接口（预留）

        Args:
            broker_id: 期货公司代码
            app_id: 应用ID
            auth_code: 授权码
            front_address: 前置地址（SimNow: tcp://180.168.146.187:10031）
        """
        logger.info("CTP 接口预留启用 (未实际连接): broker=%s app=%s", broker_id, app_id)
        self._ctp_enabled = True
        return False  # 当前阶段返回 False，实际未连接

    def _get_futures_price(self, symbol: str, fallback_price: float = 0.0) -> float:
        """获取期货最新价（优先级：iFinD → AKShare → 兜底价格）"""
        quote = self.quote_provider.get_futures_quote(symbol)
        if quote and quote.get("latest", 0) > 0:
            return float(quote["latest"])

        if self._akshare_available:
            try:
                price = self._try_akshare_futures(symbol)
                if price > 0:
                    return price
            except Exception as exc:
                logger.debug("AKShare 行情获取失败 %s: %s", symbol, exc)

        return fallback_price if fallback_price > 0 else 3000.0

    def _try_akshare_futures(self, symbol: str) -> float:
        """通过 AKShare 获取期货价格"""
        try:
            df = self._akshare.futures_zh_spot_em(symbol=symbol)
            if not df.empty:
                latest = df.iloc[0].get("最新价")
                if latest and latest > 0:
                    return float(latest)
        except Exception:
            pass

        try:
            df = self._akshare.futures_daily(symbol=symbol)
            if not df.empty:
                close = df.iloc[0].get("close")
                if close and close > 0:
                    return float(close)
        except Exception:
            pass

        return 0.0

    def _get_contract_multiplier(self, symbol: str) -> int:
        """获取合约乘数"""
        code = symbol[:2] if len(symbol) >= 2 else symbol
        return self.FUTURES_CONTRACT_MULTIPLIERS.get(code, 300)

    def place_order(self, symbol: str, qty: int, side: str,
                    price: float = 0.0, order_type: str = "LIMIT",
                    session: str = "day") -> dict:
        """期货下单

        Args:
            symbol: 合约代码，如 IF2506
            qty: 手数
            side: BUY_OPEN / SELL_OPEN / BUY_CLOSE / SELL_CLOSE
            price: 限价（0=市价，自动获取行情）
        """
        if qty <= 0:
            return {"order_id": "", "status": "REJECTED", "reason": "数量必须大于0"}

        # 获取行情价格
        if price <= 0:
            price = self._get_futures_price(symbol, 3000.0)

        code = symbol[:2] if len(symbol) >= 2 else symbol
        margin_rate = self.margin_rates.get(code, 0.12)
        multiplier = self._get_contract_multiplier(symbol)
        margin = price * qty * margin_rate * multiplier

        if side in ("BUY_OPEN", "SELL_OPEN") and margin > self.account.available_cash:
            return {"order_id": "", "status": "REJECTED",
                    "reason": f"保证金不足: 需要{margin:.0f}, 可用{self.account.available_cash:.0f}"}

        order_id = f"THS-{symbol}-{int(time.time() * 1000)}-{qty}"
        order = {
            "order_id": order_id,
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "price": float(price),
            "order_type": order_type,
            "status": "PENDING",
            "timestamp": datetime.now().isoformat(),
            "session": session,
            "market": "futures",
            "margin": margin,
            "multiplier": multiplier,
            "data_source": "iFinD" if self.quote_provider._ifind else ("akshare" if self._akshare_available else "fallback"),
        }
        self._pending_orders[order_id] = order

        # 模拟成交
        fill = self._simulate_fill(order)
        self._log_trade(fill)
        return fill

    def _simulate_fill(self, order: dict) -> dict:
        """模拟成交"""
        symbol = order["symbol"]
        qty = int(order["qty"])
        side = order["side"]
        price = float(order["price"])

        # 模拟滑点
        slippage = 0.0005
        if side in ("BUY_OPEN", "BUY_CLOSE"):
            fill_price = price * (1 + slippage)
        else:
            fill_price = price * (1 - slippage)

        multiplier = int(order.get("multiplier", 300))
        fill = {
            "order_id": order["order_id"],
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "price": round(fill_price, 2),
            "amount": round(qty * fill_price * multiplier, 2),
            "multiplier": multiplier,
            "slippage_pct": slippage,
            "status": "FILLED",
            "session": order.get("session", "day"),
            "market": "futures",
            "margin": order.get("margin", 0),
            "data_source": order.get("data_source", "fallback"),
            "timestamp": datetime.now().isoformat(),
        }

        with self._lock:
            self._fills.append(fill)

        # 更新持仓
        self._update_position(fill)
        return fill

    def _update_position(self, fill: dict) -> None:
        """更新期货持仓"""
        symbol = fill["symbol"]
        qty = int(fill["qty"])
        side = fill["side"]
        price = float(fill["price"])
        multiplier = int(fill.get("multiplier", 300))

        with self._lock:
            positions = self.account.positions
            if symbol not in positions:
                positions[symbol] = {"qty": 0, "avg_price": 0.0, "market_value": 0.0,
                                     "direction": "", "multiplier": multiplier}

            pos = positions[symbol]
            pos["multiplier"] = multiplier
            if side in ("BUY_OPEN", "SELL_CLOSE"):
                if side == "BUY_OPEN":
                    total_cost = pos["qty"] * pos["avg_price"] + qty * price
                    pos["qty"] += qty
                    pos["avg_price"] = total_cost / pos["qty"] if pos["qty"] > 0 else 0.0
                    pos["direction"] = "long"
                else:
                    pos["qty"] -= qty
                    if pos["qty"] <= 0:
                        pos["qty"] = 0
                        pos["avg_price"] = 0.0
            else:  # SELL_OPEN / BUY_CLOSE
                if side == "SELL_OPEN":
                    total_cost = abs(pos["qty"]) * pos["avg_price"] + qty * price
                    pos["qty"] -= qty
                    pos["avg_price"] = total_cost / abs(pos["qty"]) if pos["qty"] != 0 else 0.0
                    pos["direction"] = "short"
                else:
                    pos["qty"] += qty
                    if pos["qty"] >= 0:
                        pos["qty"] = 0
                        pos["avg_price"] = 0.0

            pos["market_value"] = abs(pos["qty"]) * price * multiplier
            pos["last_update"] = datetime.now().isoformat()

    def _log_trade(self, fill: dict) -> None:
        """记录交易日志"""
        try:
            with open(self._trade_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(fill, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass

    def cancel_order(self, order_id: str) -> bool:
        if order_id in self._pending_orders:
            self._pending_orders[order_id]["status"] = "CANCELLED"
            return True
        return False

    def get_positions(self) -> dict[str, dict]:
        with self._lock:
            return dict(self.account.positions)

    def get_account(self):
        return self.account

    def get_fills(self, session: str | None = None) -> list[dict]:
        with self._lock:
            if session:
                return [f for f in self._fills if f.get("session") == session]
            return list(self._fills)
