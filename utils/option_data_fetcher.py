"""ETF期权数据获取接口 — 多源降级

数据源优先级:
    1. Wind MCP (期权链数据, 需扩展)
    2. 本地缓存 (Parquet/JSON)
    3. BS定价模型 (兜底, 保证永不崩溃)

用法:
    from utils.option_data_fetcher import OptionDataFetcher

    fetcher = OptionDataFetcher()
    data = fetcher.get_option_data(
        underlying="510050",
        spot_price=2.85,
        strike=2.70,
        T=0.125,
        r=0.03,
        sigma=0.20,
        option_type="put",
    )
    # data = {"premium": 0.052, "iv": 0.20, "source": "bs_model", "delta": -0.35, ...}
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CACHE_DIR = _PROJECT_ROOT / "data" / "option_cache"


class OptionDataFetcher:
    """ETF期权数据获取器 — 多源降级"""

    def __init__(self, use_wind: bool = True, use_cache: bool = True):
        self.use_wind = use_wind
        self.use_cache = use_cache
        self._wind_available = False
        if use_wind:
            self._check_wind_availability()
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def _check_wind_availability(self) -> None:
        try:
            from tools.wind_mcp_fetcher import _get_wind_api_key

            key = _get_wind_api_key()
            self._wind_available = key is not None
            if self._wind_available:
                logger.info("Wind MCP期权数据: API Key可用 (期权链接口待扩展)")
            else:
                logger.info("Wind MCP期权数据: API Key未配置, 降级到BS模型")
        except (
            ImportError,
            AttributeError,
            ModuleNotFoundError,
            OSError,
            RuntimeError,
        ) as e:
            logger.warning("Wind MCP检查失败, 降级到BS模型: %s", e)
            self._wind_available = False

    def get_option_data(
        self,
        underlying: str,
        spot_price: float,
        strike: float,
        T: float,
        r: float = 0.03,
        sigma: float | None = None,
        option_type: str = "put",
        trade_date: str | None = None,
    ) -> dict[str, Any]:
        """获取期权数据 — 多源降级

        Returns:
            {"premium": float, "iv": float, "source": str, "delta": float, "gamma": float, "theta": float}
        """
        if sigma is None:
            sigma = 0.20

        if self.use_wind and self._wind_available:
            result = self._fetch_from_wind(
                underlying, strike, T, option_type, trade_date
            )
            if result:
                return result

        if self.use_cache:
            cache_key = (
                f"{underlying}_{strike}_{T:.4f}_{option_type}_{trade_date or 'latest'}"
            )
            result = self._fetch_from_cache(cache_key)
            if result:
                result["source"] = "local_cache"
                return result

        return self._bs_price(spot_price, strike, T, r, sigma, option_type)

    def _fetch_from_wind(
        self,
        underlying: str,
        strike: float,
        T: float,
        option_type: str,
        trade_date: str | None,
    ) -> dict[str, Any] | None:
        """从Wind MCP获取期权链数据 (接口待扩展)"""
        try:
            logger.debug("Wind MCP期权链接口待扩展, 降级到BS模型")
            return None
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
        ) as e:
            logger.warning("Wind MCP期权数据获取失败: %s", e)
            return None

    def _fetch_from_cache(self, cache_key: str) -> dict[str, Any] | None:
        """从本地缓存获取"""
        cache_file = _CACHE_DIR / f"{cache_key}.json"
        if not cache_file.exists():
            return None
        try:
            with open(cache_file, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def _save_to_cache(self, cache_key: str, data: dict[str, Any]) -> None:
        """保存到本地缓存"""
        cache_file = _CACHE_DIR / f"{cache_key}.json"
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except OSError as e:
            logger.warning("缓存保存失败: %s", e)

    @staticmethod
    def _bs_price(
        S: float, K: float, T: float, r: float, sigma: float, option_type: str
    ) -> dict[str, Any]:
        """Black-Scholes定价 + Greeks"""
        if T <= 0 or S <= 0 or K <= 0 or sigma <= 0:
            intrinsic = max(0, K - S) if option_type == "put" else max(0, S - K)
            return {
                "premium": intrinsic,
                "iv": sigma,
                "source": "bs_model",
                "delta": (
                    -1.0
                    if (option_type == "put" and intrinsic > 0)
                    else (1.0 if intrinsic > 0 else 0.0)
                ),
                "gamma": 0.0,
                "theta": 0.0,
            }

        sqrt_T = math.sqrt(T)
        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * sqrt_T)
        d2 = d1 - sigma * sqrt_T

        def _N(x: float) -> float:
            return 0.5 * (1 + math.erf(x / math.sqrt(2)))

        def _n(x: float) -> float:
            return math.exp(-0.5 * x**2) / math.sqrt(2 * math.pi)

        if option_type == "put":
            premium = K * math.exp(-r * T) * _N(-d2) - S * _N(-d1)
            delta = -_N(-d1)
        else:
            premium = S * _N(d1) - K * math.exp(-r * T) * _N(d2)
            delta = _N(d1)

        gamma = _n(d1) / (S * sigma * sqrt_T)
        theta = -(S * _n(d1) * sigma) / (2 * sqrt_T) - r * K * math.exp(-r * T) * (
            _N(d2) if option_type == "call" else _N(-d2)
        )

        return {
            "premium": round(premium, 6),
            "iv": round(sigma, 4),
            "source": "bs_model",
            "delta": round(delta, 4),
            "gamma": round(gamma, 6),
            "theta": round(theta, 6),
        }
