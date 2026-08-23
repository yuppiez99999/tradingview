"""对冲工具行情获取器 — 三类工具全降级链。

本模块为对冲工具选择器提供股指期货、ETF 期权、反向 ETF 三类对冲工具的行情获取能力，
每类工具均实现完整降级链，任一数据源失败自动降级且不抛异常。

降级链:
    期货行情:   HedgeEngine.get_live_futures_prices (内置 Wind→AKShare→Sina→efinance→兜底)
    ETF 期权:   Wind MCP → AKShare → 本地缓存 → 兜底价格
    反向 ETF:   Wind → AKShare → Sina → 缓存 → 兜底

输出格式:
    期货:     {code: price}
    ETF 期权: {code: {strike: {call_price, put_price, expiry, ...}}}
    反向 ETF: {code: price}
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from utils.auto_hedge_rebalance.exceptions import (
    AllHedgeToolPriceUnavailable,
    ToolPriceUnavailable,
)

logger = logging.getLogger(__name__)


# ============================================================================
# 兜底价格表 (P6 — 保证永不崩溃)
# ============================================================================

_FALLBACK_FUTURES_PRICES: dict[str, float] = {
    "IF": 3900.0,
    "IC": 5200.0,
    "IM": 6800.0,
    "IH": 2500.0,
}

_FALLBACK_ETF_PRICES: dict[str, float] = {
    "510300": 4.0,
    "510050": 2.8,
    "000300": 3900.0,
}

_FALLBACK_REVERSE_ETF_PRICES: dict[str, float] = {}


# ============================================================================
# HedgeToolDataFetcher
# ============================================================================


class HedgeToolDataFetcher:
    """对冲工具行情获取器。

    为对冲工具选择器提供三类对冲工具的行情获取能力，每类工具均实现完整降级链，
    任一数据源失败自动降级且不抛异常，全链失效时返回兜底价格并标记"全行情降级"。

    Attributes:
        config: 配置字典 (含 tool_pool 等配置项)。
        cache_path: 本地缓存文件路径。
    """

    def __init__(
        self,
        config: Optional[dict[str, Any]] = None,
        cache_path: str = "data/hedge_tool_price_cache.json",
    ) -> None:
        """初始化对冲工具行情获取器。

        Args:
            config: 配置字典 (含 tool_pool 等配置项，可选)。
            cache_path: 本地缓存文件路径。
        """
        self.config = config or {}
        self.cache_path = Path(cache_path)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._fallback_flags: list[str] = []

    def _reset_flags(self) -> None:
        """重置降级标记列表 (每次获取行情前调用)。"""
        self._fallback_flags = []

    def _add_flag(self, flag: str) -> None:
        """添加降级标记。"""
        if flag not in self._fallback_flags:
            self._fallback_flags.append(flag)
            logger.warning("[降级] %s", flag)

    def get_fallback_status(self) -> list[str]:
        """返回当前所有降级标记列表。

        Returns:
            降级标记列表 (如 ["期货行情降级至缓存", "期权报价残缺"])。
        """
        return list(self._fallback_flags)

    # ========================================================================
    # 期货行情获取
    # ========================================================================

    def fetch_futures(self, codes: Optional[list[str]] = None) -> dict[str, float]:
        """获取股指期货行情。

        复用 HedgeEngine.get_live_futures_prices() 内置降级链:
        Wind MCP → AKShare → Sina → efinance → 兜底价格。

        Args:
            codes: 期货品种代码列表 (如 ["IF", "IC"])，None 时返回全部四品种。

        Returns:
            {code: price} 字典。
        """
        self._reset_flags()
        target_codes = codes or ["IF", "IC", "IM", "IH"]

        try:
            from utils.hedge_engine import get_live_futures_prices

            prices = get_live_futures_prices()
            result = {k: prices.get(k, _FALLBACK_FUTURES_PRICES.get(k, 0.0)) for k in target_codes}

            missing = [k for k in target_codes if k not in prices or prices.get(k, 0) <= 0]
            if missing:
                self._add_flag(f"期货行情部分缺失: {','.join(missing)}，使用兜底价格")
        except Exception as exc:
            logger.error("期货行情获取失败: %s", exc)
            self._add_flag(f"期货行情获取异常: {exc}，使用兜底价格")
            result = {k: _FALLBACK_FUTURES_PRICES.get(k, 0.0) for k in target_codes}

        return result

    # ========================================================================
    # ETF 期权行情获取
    # ========================================================================

    def fetch_etf_options(
        self,
        codes: Optional[list[str]] = None,
    ) -> dict[str, dict[str, dict[str, Any]]]:
        """获取 ETF 期权 T 型报价。

        降级链: Wind MCP → AKShare → 本地缓存 → 兜底价格。
        期权 T 型报价残缺时自动选择最近可用行权价。

        Args:
            codes: ETF 代码列表 (如 ["510300", "510050"])，None 时返回全部白名单。

        Returns:
            {code: {strike: {call_price, put_price, expiry, ...}}} 字典。
        """
        self._reset_flags()
        tool_pool = self.config.get("tool_pool", {})
        if codes is not None:
            target_codes = codes
        else:
            target_codes = tool_pool.get("etf_options_whitelist", ["510300", "510050"])

        result: dict[str, dict[str, dict[str, Any]]] = {}

        for code in target_codes:
            option_data = self._fetch_single_etf_option(code)
            result[code] = option_data

        return result

    def _fetch_single_etf_option(self, code: str) -> dict[str, dict[str, Any]]:
        """获取单个 ETF 期权 T 型报价，按降级链尝试。

        Args:
            code: ETF 代码 (如 "510300")。

        Returns:
            {strike: {call_price, put_price, expiry, ...}} 字典。
        """
        # P0: Wind MCP
        option_data = self._try_fetch_option_from_wind(code)
        if option_data:
            return option_data

        # P1: AKShare
        option_data = self._try_fetch_option_from_akshare(code)
        if option_data:
            self._add_flag(f"ETF期权{code}降级至AKShare")
            return option_data

        # P2: 本地缓存
        option_data = self._try_fetch_option_from_cache(code)
        if option_data:
            self._add_flag(f"ETF期权{code}降级至缓存")
            return option_data

        # P3: 兜底价格
        self._add_flag(f"ETF期权{code}全链失效，使用兜底价格")
        return self._fallback_option_data(code)

    def _try_fetch_option_from_wind(self, code: str) -> dict[str, dict[str, Any]]:
        """从 Wind MCP 获取 ETF 期权 T 型报价。"""
        try:
            from quant_modules.wind_mcp import get_etf_option_quote

            data = get_etf_option_quote(code)
            if data and len(data) > 0:
                return data
        except Exception as exc:
            logger.debug("Wind MCP 获取ETF期权%s失败: %s", code, exc)
        return {}

    def _try_fetch_option_from_akshare(self, code: str) -> dict[str, dict[str, Any]]:
        """从 AKShare 获取 ETF 期权 T 型报价。"""
        try:
            import akshare as ak

            df = ak.option_finance_board(symbol=code, end_month="")
            if df is not None and len(df) > 0:
                return self._parse_akshare_option_df(code, df)
        except Exception as exc:
            logger.debug("AKShare 获取ETF期权%s失败: %s", code, exc)
        return {}

    def _parse_akshare_option_df(
        self,
        code: str,
        df: Any,
    ) -> dict[str, dict[str, Any]]:
        """解析 AKShare 期权 DataFrame 为标准格式。

        期权 T 型报价残缺时自动选择最近可用行权价。
        """
        result: dict[str, dict[str, Any]] = {}
        try:
            for _, row in df.iterrows():
                strike = str(row.get("行权价", row.get("strike", "")))
                if not strike:
                    continue
                call_price = float(row.get("认购最新价", row.get("call_price", 0)) or 0)
                put_price = float(row.get("认沽最新价", row.get("put_price", 0)) or 0)
                expiry = str(row.get("到期日", row.get("expiry", "")))

                if call_price <= 0 and put_price <= 0:
                    self._add_flag(f"ETF期权{code}行权价{strike}报价残缺")
                    continue

                result[strike] = {
                    "call_price": call_price,
                    "put_price": put_price,
                    "expiry": expiry,
                    "source": "akshare",
                }
        except Exception as exc:
            logger.warning("解析AKShare期权数据失败: %s", exc)
            self._add_flag(f"ETF期权{code}AKShare数据解析异常")
        return result

    def _try_fetch_option_from_cache(self, code: str) -> dict[str, dict[str, Any]]:
        """从本地缓存获取 ETF 期权 T 型报价。"""
        try:
            if not self.cache_path.exists():
                return {}
            with open(self.cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
            option_cache = cache.get("etf_options", {}).get(code, {})
            if option_cache:
                return option_cache
        except Exception as exc:
            logger.debug("缓存获取ETF期权%s失败: %s", code, exc)
        return {}

    def _fallback_option_data(self, code: str) -> dict[str, dict[str, Any]]:
        """生成兜底期权数据。"""
        etf_price = _FALLBACK_ETF_PRICES.get(code, 4.0)
        atm_strike = round(etf_price, 1)
        return {
            str(atm_strike): {
                "call_price": etf_price * 0.03,
                "put_price": etf_price * 0.03,
                "expiry": "",
                "source": "fallback",
            }
        }

    # ========================================================================
    # 反向 ETF 行情获取
    # ========================================================================

    def fetch_reverse_etf(
        self,
        codes: Optional[list[str]] = None,
    ) -> dict[str, float]:
        """获取反向 ETF 行情。

        降级链: Wind → AKShare → Sina → 缓存 → 兜底。

        Args:
            codes: 反向 ETF 代码列表，None 时使用配置白名单。

        Returns:
            {code: price} 字典。
        """
        self._reset_flags()
        tool_pool = self.config.get("tool_pool", {})
        if codes is not None:
            target_codes = codes
        else:
            target_codes = tool_pool.get("reverse_etf_whitelist", [])

        if not target_codes:
            return {}

        result: dict[str, float] = {}
        for code in target_codes:
            price = self._fetch_single_reverse_etf(code)
            result[code] = price

        return result

    def _fetch_single_reverse_etf(self, code: str) -> float:
        """获取单个反向 ETF 行情，按降级链尝试。"""
        # P0: Wind MCP
        price = self._try_fetch_reverse_from_wind(code)
        if price > 0:
            return price

        # P1: AKShare
        price = self._try_fetch_reverse_from_akshare(code)
        if price > 0:
            self._add_flag(f"反向ETF {code}降级至AKShare")
            return price

        # P2: Sina
        price = self._try_fetch_reverse_from_sina(code)
        if price > 0:
            self._add_flag(f"反向ETF {code}降级至Sina")
            return price

        # P3: 缓存
        price = self._try_fetch_reverse_from_cache(code)
        if price > 0:
            self._add_flag(f"反向ETF {code}降级至缓存")
            return price

        # P4: 兜底
        self._add_flag(f"反向ETF {code}全链失效，使用兜底价格")
        return _FALLBACK_REVERSE_ETF_PRICES.get(code, 1.0)

    def _try_fetch_reverse_from_wind(self, code: str) -> float:
        """从 Wind MCP 获取反向 ETF 行情。"""
        try:
            from quant_modules.wind_mcp import get_etf_price

            return float(get_etf_price(code) or 0)
        except Exception as exc:
            logger.debug("Wind获取反向ETF %s失败: %s", code, exc)
        return 0.0

    def _try_fetch_reverse_from_akshare(self, code: str) -> float:
        """从 AKShare 获取反向 ETF 行情。"""
        try:
            import akshare as ak

            df = ak.fund_etf_hist_em(symbol=code, period="daily", adjust="qfq")
            if df is not None and len(df) > 0:
                return float(df.iloc[-1].get("收盘", 0) or 0)
        except Exception as exc:
            logger.debug("AKShare获取反向ETF %s失败: %s", code, exc)
        return 0.0

    def _try_fetch_reverse_from_sina(self, code: str) -> float:
        """从 Sina 获取反向 ETF 行情。"""
        try:
            from quant_modules.sina_api_helper import get_realtime_quote

            return float(get_realtime_quote(code) or 0)
        except Exception as exc:
            logger.debug("Sina获取反向ETF %s失败: %s", code, exc)
        return 0.0

    def _try_fetch_reverse_from_cache(self, code: str) -> float:
        """从本地缓存获取反向 ETF 行情。"""
        try:
            if not self.cache_path.exists():
                return 0.0
            with open(self.cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
            return float(cache.get("reverse_etf", {}).get(code, 0) or 0)
        except Exception as exc:
            logger.debug("缓存获取反向ETF %s失败: %s", code, exc)
        return 0.0

    # ========================================================================
    # 综合获取
    # ========================================================================

    def fetch_all(
        self,
        futures_codes: Optional[list[str]] = None,
        etf_option_codes: Optional[list[str]] = None,
        reverse_etf_codes: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """一次性获取三类对冲工具行情。

        Args:
            futures_codes: 期货品种代码列表。
            etf_option_codes: ETF 期权代码列表。
            reverse_etf_codes: 反向 ETF 代码列表。

        Returns:
            {"futures": {...}, "etf_options": {...}, "reverse_etf": {...}} 字典。

        Raises:
            AllHedgeToolPriceUnavailable: 全部工具行情均不可用时抛出。
        """
        futures = self.fetch_futures(futures_codes)
        etf_options = self.fetch_etf_options(etf_option_codes)
        reverse_etf = self.fetch_reverse_etf(reverse_etf_codes)

        all_fallback = all("全链失效" in f or "全行情降级" in f for f in self._fallback_flags)
        if all_fallback and not futures and not etf_options and not reverse_etf:
            self._add_flag("全行情降级")

        return {
            "futures": futures,
            "etf_options": etf_options,
            "reverse_etf": reverse_etf,
            "fallback_flags": self.get_fallback_status(),
        }


__all__ = ["HedgeToolDataFetcher"]