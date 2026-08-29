"""fineng/pricing/monte_carlo.py 单元测试 — 蒙特卡洛定价引擎.

目标模块: utils/fineng/pricing/monte_carlo.py (branch-rate 0.4355 → 高覆盖)
覆盖: MCPricingResult / MonteCarloEngine (price_european/asian/barrier 全分支)
"""

from __future__ import annotations

import pytest

from utils.fineng.pricing.monte_carlo import (
    MCPricingResult,
    MonteCarloEngine,
)

# ============================================================
# MCPricingResultTest — 定价结果数据结构
# ============================================================


class MCPricingResultTest:

    def test_relative_error_positive_price(self):
        r = MCPricingResult(
            price=10.0,
            standard_error=0.5,
            confidence_95=(9.0, 11.0),
            n_paths=1000,
            n_steps=252,
        )
        assert r.relative_error == pytest.approx(0.05)

    def test_relative_error_zero_price(self):
        r = MCPricingResult(
            price=0.0,
            standard_error=0.5,
            confidence_95=(0.0, 1.0),
            n_paths=1000,
            n_steps=252,
        )
        assert r.relative_error == float("inf")

    def test_relative_error_negative_price(self):
        r = MCPricingResult(
            price=-1.0,
            standard_error=0.5,
            confidence_95=(-2.0, 0.0),
            n_paths=1000,
            n_steps=252,
        )
        assert r.relative_error == float("inf")

    def test_default_elapsed(self):
        r = MCPricingResult(
            price=1.0,
            standard_error=0.0,
            confidence_95=(1.0, 1.0),
            n_paths=100,
            n_steps=10,
        )
        assert r.elapsed_seconds == 0.0


# ============================================================
# MonteCarloEuropeanTest — 欧式期权定价
# ============================================================


class MonteCarloEuropeanTest:

    def test_call_option_basic(self):
        eng = MonteCarloEngine(n_paths=10000, n_steps=50, seed=42)
        result = eng.price_european(
            S=100, K=100, T=1.0, r=0.05, sigma=0.20, is_call=True
        )
        assert result.price > 0
        assert result.n_paths > 0
        assert result.standard_error >= 0

    def test_put_option_basic(self):
        eng = MonteCarloEngine(n_paths=10000, n_steps=50, seed=42)
        result = eng.price_european(
            S=100, K=100, T=1.0, r=0.05, sigma=0.20, is_call=False
        )
        assert result.price > 0

    def test_boundary_S_zero(self):
        eng = MonteCarloEngine(n_paths=100, seed=42)
        result = eng.price_european(S=0, K=100, T=1.0)
        assert result.price == 0
        assert result.standard_error == 0.0

    def test_boundary_K_zero(self):
        eng = MonteCarloEngine(n_paths=100, seed=42)
        result = eng.price_european(S=100, K=0, T=1.0, is_call=True)
        assert result.price == 100  # max(S-K,0) = 100

    def test_boundary_T_zero(self):
        eng = MonteCarloEngine(n_paths=100, seed=42)
        result = eng.price_european(S=100, K=90, T=0.0, is_call=True)
        assert result.price == 10  # max(100-90, 0)

    def test_boundary_T_zero_put(self):
        eng = MonteCarloEngine(n_paths=100, seed=42)
        result = eng.price_european(S=90, K=100, T=0.0, is_call=False)
        assert result.price == 10  # max(100-90, 0)

    def test_boundary_sigma_zero(self):
        eng = MonteCarloEngine(n_paths=100, seed=42)
        result = eng.price_european(S=100, K=110, T=1.0, sigma=0.0, is_call=True)
        assert result.price == 0  # max(100-110, 0)

    def test_no_antithetic(self):
        eng = MonteCarloEngine(n_paths=10000, n_steps=50, seed=42, use_antithetic=False)
        result = eng.price_european(S=100, K=100, T=1.0)
        assert result.price > 0
        assert result.n_paths == 10000

    def test_no_control_variate(self):
        eng = MonteCarloEngine(
            n_paths=10000, n_steps=50, seed=42, use_control_variate=False
        )
        result = eng.price_european(S=100, K=100, T=1.0)
        assert result.price > 0

    def test_confidence_interval(self):
        eng = MonteCarloEngine(n_paths=10000, n_steps=50, seed=42)
        result = eng.price_european(S=100, K=100, T=1.0)
        ci_low, ci_high = result.confidence_95
        assert ci_low >= 0
        assert ci_high >= ci_low

    def test_seed_reproducibility(self):
        eng1 = MonteCarloEngine(n_paths=5000, n_steps=20, seed=123)
        r1 = eng1.price_european(S=100, K=100, T=1.0)
        eng2 = MonteCarloEngine(n_paths=5000, n_steps=20, seed=123)
        r2 = eng2.price_european(S=100, K=100, T=1.0)
        assert r1.price == pytest.approx(r2.price)


# ============================================================
# MonteCarloAsianTest — 亚式期权定价
# ============================================================


class MonteCarloAsianTest:

    def test_call_basic(self):
        eng = MonteCarloEngine(n_paths=10000, n_steps=50, seed=42)
        result = eng.price_asian_arithmetic(S=100, K=100, T=1.0, is_call=True)
        assert result.price > 0
        assert result.n_paths > 0

    def test_put_basic(self):
        eng = MonteCarloEngine(n_paths=10000, n_steps=50, seed=42)
        result = eng.price_asian_arithmetic(S=100, K=100, T=1.0, is_call=False)
        assert result.price > 0

    def test_boundary_S_zero(self):
        eng = MonteCarloEngine(n_paths=100, seed=42)
        result = eng.price_asian_arithmetic(S=0, K=100, T=1.0)
        assert result.price == 0

    def test_boundary_K_zero_call(self):
        eng = MonteCarloEngine(n_paths=100, seed=42)
        result = eng.price_asian_arithmetic(S=100, K=0, T=1.0, is_call=True)
        assert result.price == 100

    def test_boundary_T_zero(self):
        eng = MonteCarloEngine(n_paths=100, seed=42)
        result = eng.price_asian_arithmetic(S=100, K=90, T=0.0, is_call=True)
        assert result.price == 10

    def test_custom_n_fixing(self):
        eng = MonteCarloEngine(n_paths=5000, n_steps=50, seed=42)
        result = eng.price_asian_arithmetic(S=100, K=100, T=1.0, n_fixing=10)
        assert result.price > 0

    def test_no_antithetic(self):
        eng = MonteCarloEngine(n_paths=5000, n_steps=50, seed=42, use_antithetic=False)
        result = eng.price_asian_arithmetic(S=100, K=100, T=1.0)
        assert result.price > 0
        assert result.n_paths == 5000


# ============================================================
# MonteCarloBarrierTest — 障碍期权定价
# ============================================================


class MonteCarloBarrierTest:

    def test_basic(self):
        eng = MonteCarloEngine(n_paths=10000, n_steps=50, seed=42)
        result = eng.price_barrier_dao_call(S=100, K=100, barrier=80, T=1.0)
        assert result.price >= 0
        assert result.n_paths > 0

    def test_boundary_S_zero(self):
        eng = MonteCarloEngine(n_paths=100, seed=42)
        result = eng.price_barrier_dao_call(S=0, K=100, barrier=80, T=1.0)
        assert result.price == 0
        assert result.n_paths == 0

    def test_boundary_barrier_zero(self):
        eng = MonteCarloEngine(n_paths=100, seed=42)
        result = eng.price_barrier_dao_call(S=100, K=100, barrier=0, T=1.0)
        assert result.price == 0
        assert result.n_paths == 0

    def test_boundary_T_zero(self):
        eng = MonteCarloEngine(n_paths=100, seed=42)
        result = eng.price_barrier_dao_call(S=100, K=100, barrier=80, T=0.0)
        assert result.price == 0

    def test_low_barrier_knocks_out(self):
        """barrier 很低时大部分路径存活."""
        eng = MonteCarloEngine(n_paths=10000, n_steps=50, seed=42)
        result = eng.price_barrier_dao_call(S=100, K=100, barrier=10, T=1.0)
        assert result.price >= 0

    def test_high_barrier_knocks_out_most(self):
        """barrier 接近 S 时大部分路径被敲出."""
        eng = MonteCarloEngine(n_paths=10000, n_steps=50, seed=42)
        result = eng.price_barrier_dao_call(S=100, K=100, barrier=99, T=1.0)
        # 大部分路径触及 99 → 价格较低
        assert result.price >= 0

    def test_no_antithetic(self):
        eng = MonteCarloEngine(n_paths=5000, n_steps=50, seed=42, use_antithetic=False)
        result = eng.price_barrier_dao_call(S=100, K=100, barrier=80, T=1.0)
        assert result.price >= 0
        assert result.n_paths == 5000


# ============================================================
# MonteCarloInternalTest — 内部模拟方法
# ============================================================


class MonteCarloInternalTest:

    def test_simulate_terminal_positive(self):
        eng = MonteCarloEngine(n_paths=100, n_steps=50, seed=42)
        ST = eng._simulate_terminal(S=100, drift=0.0001, vol_dt=0.01, sign=1.0)
        assert ST > 0

    def test_simulate_terminal_negative_sign(self):
        eng = MonteCarloEngine(n_paths=100, n_steps=50, seed=42)
        ST = eng._simulate_terminal(S=100, drift=0.0001, vol_dt=0.01, sign=-1.0)
        assert ST > 0

    def test_simulate_path_length(self):
        eng = MonteCarloEngine(n_paths=100, n_steps=50, seed=42)
        path = eng._simulate_path(S=100, drift=0.0001, vol_dt=0.01)
        assert len(path) == 50  # 去掉 S0

    def test_simulate_path_positive(self):
        eng = MonteCarloEngine(n_paths=100, n_steps=50, seed=42)
        path = eng._simulate_path(S=100, drift=0.0001, vol_dt=0.01)
        assert all(p > 0 for p in path)
