"""Phase 5 共享 fixtures — 合成期权链/风控状态/状态隔离.

设计:
    - SyntheticOptionDataFetcher: BS 定价替代真实 Wind MCP, 零网络依赖
    - 自动隔离状态文件, 避免污染 cache/etf_option_combo_state.json
    - 所有 fixture 遵循 AAA 模式, 可被各测试文件复用
"""

from __future__ import annotations

import math
import sys
from datetime import date
from pathlib import Path

import pytest

# 确保项目根在 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.etf_option_combo.combo_base import (  # noqa: E402
    ComboBase,
    ComboLeg,
    ComboOrder,
    ComboResult,
    ApprovalResult,
    RollResult,
    LegSide,
    OrderStatus,
    StrategyType,
    OptionChainFetcher,
)


# ============================================================
# BS 定价工具 (零第三方依赖)
# ============================================================

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_call_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or S <= 0 or K <= 0 or sigma <= 0:
        return max(0.0, S - K)
    sqT = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqT)
    d2 = d1 - sigma * sqT
    return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)


def bs_put_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or S <= 0 or K <= 0 or sigma <= 0:
        return max(0.0, K - S)
    sqT = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqT)
    d2 = d1 - sigma * sqT
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def bs_greeks(S: float, K: float, T: float, r: float, sigma: float, option_type: str) -> dict:
    if T <= 0 or S <= 0 or K <= 0 or sigma <= 0:
        return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}
    sqT = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqT)
    d2 = d1 - sigma * sqT
    n_d1 = _norm_pdf(d1)
    gamma = n_d1 / (S * sigma * sqT)
    vega = S * n_d1 * sqT
    if option_type == "call":
        delta = _norm_cdf(d1)
        theta = (-S * n_d1 * sigma / (2 * sqT) - r * K * math.exp(-r * T) * _norm_cdf(d2)) / 365.0
        rho = K * T * math.exp(-r * T) * _norm_cdf(d1)
    else:
        delta = _norm_cdf(d1) - 1.0
        theta = (-S * n_d1 * sigma / (2 * sqT) + r * K * math.exp(-r * T) * _norm_cdf(-d2)) / 365.0
        rho = -K * T * math.exp(-r * T) * _norm_cdf(-d2)
    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega, "rho": rho}


# ============================================================
# 合成期权数据源 (替代真实 Wind MCP)
# ============================================================

class SyntheticOptionDataFetcher:
    """合成期权数据源 — BS 定价, 接口兼容 OptionDataFetcher.get_option_data.

    通过 fixture 注入 OptionChainFetcher, 避免网络/数据源依赖.
    """

    def __init__(self, iv: float = 0.20, source: str = "bs_synthetic", volume: int = 1000) -> None:
        self._iv = iv
        self._source = source
        self._volume = volume
        self.call_count = 0

    def get_option_data(
        self,
        underlying: str,
        spot_price: float,
        strike: float,
        T: float,
        r: float,
        option_type: str,
    ) -> dict:
        self.call_count += 1
        ot = option_type.lower()
        if ot == "call":
            premium = bs_call_price(spot_price, strike, T, r, self._iv)
        else:
            premium = bs_put_price(spot_price, strike, T, r, self._iv)
        greeks = bs_greeks(spot_price, strike, T, r, self._iv, ot)
        return {
            "premium": round(premium, 6),
            "iv": self._iv,
            "delta": round(greeks["delta"], 6),
            "gamma": round(greeks["gamma"], 6),
            "theta": round(greeks["theta"], 6),
            "vega": round(greeks["vega"], 6),
            "volume": self._volume,
            "source": self._source,
        }


class FailingOptionDataFetcher:
    """永远失败的期权数据源 — 模拟 Wind MCP 不可用."""

    def get_option_data(self, **kwargs):
        raise RuntimeError("WIND_MCP_UNAVAILABLE")


class SyntheticDataLayer:
    """合成数据层 — 提供 get_spot 方法, 替代真实 Wind/TDX/AKShare.

    OptionChainFetcher.get_spot_price 通过 data_layer.get_spot() 获取现货价.
    """

    def __init__(self, spot_price_map: dict[str, float] | None = None) -> None:
        self._spot_map = spot_price_map or {}

    def get_spot(self, underlying: str) -> float | None:
        return self._spot_map.get(underlying)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(autouse=True)
def _isolate_state_file(tmp_path, monkeypatch):
    """自动隔离状态文件, 避免污染真实 cache/etf_option_combo_state.json."""
    from utils.etf_option_combo import combo_state
    monkeypatch.setattr(
        combo_state,
        "_DEFAULT_STATE_PATH",
        tmp_path / "etf_option_combo_state.json",
    )


@pytest.fixture
def fixed_spot_price() -> float:
    """510050.SH 现货价 3.00."""
    return 3.0


@pytest.fixture
def underlying_code() -> str:
    return "510050.SH"


@pytest.fixture
def synthetic_fetcher() -> SyntheticOptionDataFetcher:
    """合成期权数据源 (BS, iv=0.20)."""
    return SyntheticOptionDataFetcher(iv=0.20, source="bs_synthetic")


@pytest.fixture
def failing_fetcher() -> FailingOptionDataFetcher:
    """永远失败的数据源 — 模拟 Wind MCP 不可用."""
    return FailingOptionDataFetcher()


@pytest.fixture
def synthetic_data_layer(fixed_spot_price) -> SyntheticDataLayer:
    """合成数据层 — 510050/510300/588000/159915 现货价."""
    return SyntheticDataLayer({
        "510050.SH": fixed_spot_price,
        "510300.SH": 4.0,
        "588000.SH": 1.5,
        "159915.SZ": 2.5,
    })


@pytest.fixture
def chain_fetcher(synthetic_fetcher, synthetic_data_layer) -> OptionChainFetcher:
    """注入合成 fetcher + data_layer 的 OptionChainFetcher."""
    return OptionChainFetcher(
        fetcher=synthetic_fetcher,
        data_layer=synthetic_data_layer,
        bs_timeout_ms=500,
    )


@pytest.fixture
def empty_chain_fetcher() -> OptionChainFetcher:
    """无 fetcher 的 OptionChainFetcher (用于降级测试).

    OptionChainFetcher(fetcher=None) 会自动导入真实 OptionDataFetcher,
    故构造后手动置空 _fetcher 以模拟数据源全不可用.
    """
    fetcher = OptionChainFetcher(fetcher=None, bs_timeout_ms=200)
    fetcher._fetcher = None  # 强制置空, 模拟数据源全失败
    return fetcher


@pytest.fixture
def synthetic_option_chain(chain_fetcher, fixed_spot_price) -> list[dict]:
    """合成 CALL 期权链 (DTE 30-60, OTM 2%-8%)."""
    return chain_fetcher.get_option_chain(
        underlying="510050.SH",
        option_type="CALL",
        dte_range=(30, 60),
        otm_range=(0.02, 0.08),
        min_volume=0,
        spot_price=fixed_spot_price,
    )


@pytest.fixture
def synthetic_put_chain(chain_fetcher, fixed_spot_price) -> list[dict]:
    """合成 PUT 期权链 (DTE 30-60, OTM 2%-8%)."""
    return chain_fetcher.get_option_chain(
        underlying="510050.SH",
        option_type="PUT",
        dte_range=(30, 60),
        otm_range=(0.02, 0.08),
        min_volume=0,
        spot_price=fixed_spot_price,
    )


@pytest.fixture
def mock_risk_state():
    """可配置的风控状态工厂."""
    def _make(drawdown_level: str = "L0", kill_switch_active: bool = False, **extra) -> dict:
        state = {"drawdown_level": drawdown_level, "kill_switch_active": kill_switch_active}
        state.update(extra)
        return state
    return _make


@pytest.fixture
def tmp_state_path(tmp_path) -> Path:
    """临时状态文件路径."""
    return tmp_path / "etf_option_combo_state.json"


@pytest.fixture
def state_manager(tmp_state_path):
    """ComboStateManager 实例 (临时路径)."""
    from utils.etf_option_combo.combo_state import ComboStateManager
    return ComboStateManager(state_path=tmp_state_path)


@pytest.fixture
def risk_manager():
    """ComboRiskManager 实例 (默认配置)."""
    from utils.etf_option_combo.combo_risk_manager import ComboRiskManager
    return ComboRiskManager(total_capital=2_000_000)


@pytest.fixture
def base_config() -> dict:
    """策略通用配置."""
    return {
        "total_capital": 2_000_000,
        "annual_budget_pct": 0.015,
        "otm_pct": 0.05,
        "otm_pct_range": (0.02, 0.08),
        "dte_min": 30,
        "dte_max": 60,
        "preferred_dte": 45,
    }


@pytest.fixture
def spot_position_sufficient() -> dict:
    """现货持仓充足 (1 张合约 = 10000 份)."""
    return {"shares": 10000, "available_cash": 100_000.0, "target_weight": 0.10, "current_weight": 0.0}


@pytest.fixture
def make_combo_leg():
    """ComboLeg 工厂."""
    def _make(**overrides) -> ComboLeg:
        defaults = dict(
            instrument="OPTION",
            underlying="510050.SH",
            option_type="CALL",
            side=LegSide.SELL,
            strike=3.15,
            expiry=date.today().replace(year=date.today().year) if date.today().month < 12 else date(date.today().year + 1, 1, 15),
            quantity=1,
            multiplier=10000,
            premium=0.05,
        )
        defaults.update(overrides)
        return ComboLeg(**defaults)
    return _make


# 暴露 BS 工具给测试模块
__all__ = [
    "bs_call_price",
    "bs_put_price",
    "bs_greeks",
    "SyntheticOptionDataFetcher",
    "FailingOptionDataFetcher",
]