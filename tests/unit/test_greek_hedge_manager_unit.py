"""greek_hedge_manager 单元测试 — Greeks 动态对冲管理器全覆盖.

被测模块: utils/greek_hedge_manager.py
覆盖目标: >=90%
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.greek_hedge_manager import (  # noqa: E402
    GreekExposure,
    GreekHedgeManager,
    HedgeInstrument,
    IVEnvironment,
)

# ============================================================
# __init__ + max_vega
# ============================================================


class TestInit:
    def test_default(self):
        mgr = GreekHedgeManager()
        assert mgr.target_delta == 0.0
        assert mgr.max_vega == 50000.0

    def test_custom(self):
        mgr = GreekHedgeManager(target_delta=0.1, max_vega=10000.0)
        assert mgr.target_delta == 0.1
        assert mgr.max_vega == 10000.0

    def test_set_max_vega(self):
        mgr = GreekHedgeManager(max_vega=50000.0)
        mgr.max_vega = 30000.0
        assert mgr.max_vega == 30000.0


# ============================================================
# 动态 Vega 上限
# ============================================================


class TestDynamicVega:
    def test_no_iv_env(self):
        mgr = GreekHedgeManager(max_vega=50000.0)
        assert mgr.max_vega == 50000.0

    def test_normal_iv(self):
        iv = IVEnvironment(current_iv=0.20, long_term_median_iv=0.20)
        mgr = GreekHedgeManager(max_vega=50000.0, iv_env=iv)
        assert mgr.max_vega > 0

    def test_high_iv_reduces_limit(self):
        iv_normal = IVEnvironment(current_iv=0.20, long_term_median_iv=0.20)
        iv_high = IVEnvironment(current_iv=0.40, long_term_median_iv=0.20)
        mgr_normal = GreekHedgeManager(max_vega=50000.0, iv_env=iv_normal)
        mgr_high = GreekHedgeManager(max_vega=50000.0, iv_env=iv_high)
        assert mgr_high.max_vega < mgr_normal.max_vega

    def test_backwardation_reduces(self):
        iv_contango = IVEnvironment(front_month_iv=0.18, second_month_iv=0.22)
        iv_backward = IVEnvironment(front_month_iv=0.25, second_month_iv=0.20)
        mgr_c = GreekHedgeManager(
            max_vega=50000.0, iv_env=iv_contango / 0.20 if False else iv_contango
        )
        mgr_b = GreekHedgeManager(max_vega=50000.0, iv_env=iv_backward)
        assert mgr_b.max_vega < mgr_c.max_vega

    def test_high_skew_reduces(self):
        iv_low_skew = IVEnvironment(put_25d_iv=0.22, call_25d_iv=0.18)
        iv_high_skew = IVEnvironment(put_25d_iv=0.35, call_25d_iv=0.18)
        mgr_low = GreekHedgeManager(max_vega=50000.0, iv_env=iv_low_skew)
        mgr_high = GreekHedgeManager(max_vega=50000.0, iv_env=iv_high_skew)
        assert mgr_high.max_vega < mgr_low.max_vega

    def test_breakdown(self):
        iv = IVEnvironment(current_iv=0.25, long_term_median_iv=0.20)
        mgr = GreekHedgeManager(max_vega=50000.0, iv_env=iv)
        bd = mgr.get_vega_limit_breakdown()
        assert bd["dynamic"] is True
        assert "iv_ratio_mult" in bd
        assert "composite_multiplier" in bd

    def test_breakdown_no_iv(self):
        mgr = GreekHedgeManager(max_vega=50000.0)
        bd = mgr.get_vega_limit_breakdown()
        assert bd["dynamic"] is False

    def test_set_iv_environment(self):
        mgr = GreekHedgeManager(max_vega=50000.0)
        iv = IVEnvironment(current_iv=0.30, long_term_median_iv=0.20)
        mgr.set_iv_environment(iv)
        assert mgr.iv_env is not None


# ============================================================
# Black-Scholes Greeks
# ============================================================


class TestBSGreeks:
    def test_call_delta(self):
        mgr = GreekHedgeManager()
        d = mgr._bs_delta(S=100, K=100, T=1.0, r=0.02, sigma=0.2, call=True)
        assert 0 < d < 1

    def test_put_delta(self):
        mgr = GreekHedgeManager()
        d = mgr._bs_delta(S=100, K=100, T=1.0, r=0.02, sigma=0.2, call=False)
        assert -1 < d < 0

    def test_gamma_positive(self):
        mgr = GreekHedgeManager()
        g = mgr._bs_gamma(S=100, K=100, T=1.0, r=0.02, sigma=0.2)
        assert g > 0

    def test_vega_positive(self):
        mgr = GreekHedgeManager()
        v = mgr._bs_vega(S=100, K=100, T=1.0, r=0.02, sigma=0.2)
        assert v > 0

    def test_invalid_inputs(self):
        mgr = GreekHedgeManager()
        assert mgr._bs_delta(0, 100, 1.0, 0.02, 0.2) == 0.0
        assert mgr._bs_delta(100, 100, 0, 0.02, 0.2) == 0.0
        assert mgr._bs_gamma(100, 100, 0, 0.02, 0.2) == 0.0

    def test_calc_option_greeks(self):
        mgr = GreekHedgeManager()
        g = mgr.calc_option_greeks(S=100, K=100, T=1.0, r=0.02, sigma=0.2, call=True)
        assert isinstance(g, GreekExposure)
        assert 0 < g.delta < 1
        assert g.gamma > 0


# ============================================================
# calc_portfolio_greeks
# ============================================================


class TestPortfolioGreeks:
    def test_stock_only(self):
        mgr = GreekHedgeManager()
        positions = {"A": {"shares": 100, "est_price": 10.0, "beta": 1.2}}
        prices = {"A": 10.0}
        exp = mgr.calc_portfolio_greeks(positions, prices)
        assert exp.delta > 0

    def test_with_option(self):
        mgr = GreekHedgeManager()
        positions = {
            "A": {"shares": 100, "est_price": 10.0},
            "OPT": {
                "shares": 2,
                "est_price": 5.0,
                "type": "OPTION",
                "underlying_price": 100,
                "strike": 100,
                "days_to_expiry": 90,
                "implied_vol": 0.2,
                "option_type": "PUT",
            },
        }
        prices = {"A": 10.0, "OPT": 5.0}
        exp = mgr.calc_portfolio_greeks(positions, prices)
        assert isinstance(exp, GreekExposure)

    def test_simple_numeric(self):
        mgr = GreekHedgeManager()
        positions = {"A": 100, "B": -50}
        prices = {"A": 10.0, "B": 20.0}
        exp = mgr.calc_portfolio_greeks(positions, prices)
        assert exp.delta == 0.0

    def test_empty(self):
        mgr = GreekHedgeManager()
        exp = mgr.calc_portfolio_greeks({}, {})
        assert exp.delta == 0.0


# ============================================================
# target_futures_delta_hedge
# ============================================================


class TestFuturesHedge:
    def test_basic(self):
        mgr = GreekHedgeManager(target_delta=0.0)
        exposure = GreekExposure(delta=100_000)
        instruments = [
            HedgeInstrument(
                code="IF",
                instrument_type="FUTURES",
                direction="SHORT",
                multiplier=300,
                delta=-1.0,
            )
        ]
        prices = {"IF": 4000.0}
        targets = mgr.target_futures_delta_hedge(exposure, instruments, prices)
        assert "IF" in targets

    def test_no_instruments(self):
        mgr = GreekHedgeManager()
        assert mgr.target_futures_delta_hedge(GreekExposure(delta=100), [], {}) == {}

    def test_zero_residual(self):
        mgr = GreekHedgeManager(target_delta=100.0)
        exposure = GreekExposure(delta=100.0)
        targets = mgr.target_futures_delta_hedge(
            exposure,
            [
                HedgeInstrument(
                    code="IF",
                    instrument_type="FUTURES",
                    direction="SHORT",
                    multiplier=1,
                    delta=1,
                )
            ],
            {"IF": 10},
        )
        assert targets == {}


# ============================================================
# target_option_greeks_hedge
# ============================================================


class TestOptionHedge:
    def test_basic(self):
        mgr = GreekHedgeManager(max_vega=50000.0)
        exposure = GreekExposure(delta=100_000, gamma=500, vega=20000)
        opts = [
            HedgeInstrument(
                code="PUT",
                instrument_type="OPTION",
                direction="LONG",
                delta=-0.3,
                gamma=0.01,
                vega=100,
            )
        ]
        prices = {"PUT": 5.0}
        targets = mgr.target_option_greeks_hedge(exposure, opts, prices)
        assert "PUT" in targets

    def test_empty(self):
        mgr = GreekHedgeManager()
        assert mgr.target_option_greeks_hedge(GreekExposure(), [], {}) == {}


# ============================================================
# hedge_ratio
# ============================================================


class TestHedgeRatio:
    def test_basic(self):
        mgr = GreekHedgeManager()
        assert mgr.hedge_ratio(1_000_000, 500_000) == 0.5

    def test_zero_portfolio(self):
        mgr = GreekHedgeManager()
        assert mgr.hedge_ratio(0, 100) == 0.0


# ============================================================
# rebalance_signal
# ============================================================


class TestRebalanceSignal:
    def test_no_rebalance(self):
        mgr = GreekHedgeManager(
            target_delta=0.0, target_gamma=0.0, max_vega=50000.0, max_theta_burn=-5000.0
        )
        exp = GreekExposure(delta=0.0, gamma=0.0, vega=1000.0, theta=-1000.0)
        sig = mgr.rebalance_signal(exp)
        assert sig["need_rebalance"] is False

    def test_delta_rebalance(self):
        mgr = GreekHedgeManager(target_delta=0.0)
        exp = GreekExposure(delta=100_000)
        sig = mgr.rebalance_signal(exp)
        assert sig["delta_rebalance"] is True
        assert sig["need_rebalance"] is True

    def test_vega_rebalance(self):
        mgr = GreekHedgeManager(max_vega=10000.0)
        exp = GreekExposure(vega=50000.0)
        sig = mgr.rebalance_signal(exp)
        assert sig["vega_rebalance"] is True
