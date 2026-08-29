"""TT-DAC-PS 最优执行算法单元测试.

被测模块: utils/execution/tt_dac_ps.py
文献: #51 TT-DAC-PS Optimal Execution (2026.06)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.execution.tt_dac_ps import (  # noqa: E402
    ExecutionSlice,
    LimitOrderBookModel,
    LOBParams,
    OUNoiseProcess,
    OUParams,
    TTDACPSExecutor,
    TTDACPSResult,
)

# ============================================================
# OU 噪声过程测试
# ============================================================


class TestOUParams:
    def test_defaults(self):
        p = OUParams()
        assert p.theta == 5.0
        assert p.mu == 0.0
        assert p.sigma == 0.001
        assert p.dt == 0.01

    def test_custom(self):
        p = OUParams(theta=10.0, mu=0.5, sigma=0.02, dt=0.05)
        assert p.theta == 10.0
        assert p.mu == 0.5


class TestOUNoiseProcess:
    def test_reset(self):
        proc = OUNoiseProcess(seed=42)
        proc.step()
        proc.reset()
        assert proc.current_value() == pytest.approx(0.0)

    def test_step_returns_float(self):
        proc = OUNoiseProcess(seed=42)
        val = proc.step()
        assert isinstance(val, float)

    def test_mean_reversion(self):
        """OU 过程均值回归: 大量步数后均值接近 μ."""
        params = OUParams(theta=10.0, mu=0.0, sigma=0.01, dt=0.01)
        proc = OUNoiseProcess(params=params, seed=42)
        path = proc.simulate(10000)
        assert abs(path.mean()) < 0.05  # 均值接近 0

    def test_simulate_length(self):
        proc = OUNoiseProcess(seed=42)
        path = proc.simulate(100)
        assert len(path) == 100

    def test_reproducible_with_seed(self):
        proc1 = OUNoiseProcess(seed=123)
        proc2 = OUNoiseProcess(seed=123)
        path1 = proc1.simulate(50)
        path2 = proc2.simulate(50)
        assert (path1 == path2).all()

    def test_different_seeds_different(self):
        proc1 = OUNoiseProcess(seed=1)
        proc2 = OUNoiseProcess(seed=2)
        path1 = proc1.simulate(50)
        path2 = proc2.simulate(50)
        assert not (path1 == path2).all()

    def test_current_value_after_step(self):
        proc = OUNoiseProcess(seed=42)
        val = proc.step()
        assert proc.current_value() == val


# ============================================================
# LOB 模型测试
# ============================================================


class TestLOBParams:
    def test_defaults(self):
        p = LOBParams()
        assert p.spread_bps == 10.0
        assert p.depth_shares == 1000.0
        assert p.n_levels == 10
        assert p.impact_exponent == 0.5


class TestLimitOrderBookModel:
    def test_available_liquidity(self):
        lob = LimitOrderBookModel()
        assert lob.available_liquidity() == 10000.0  # 1000 × 10

    def test_available_liquidity_custom_levels(self):
        lob = LimitOrderBookModel()
        assert lob.available_liquidity(5) == 5000.0

    def test_lob_impact_zero_order(self):
        lob = LimitOrderBookModel()
        assert lob.lob_impact_bps(0) == 0.0

    def test_lob_impact_positive(self):
        lob = LimitOrderBookModel()
        assert lob.lob_impact_bps(500) > 0

    def test_lob_impact_increases_with_size(self):
        lob = LimitOrderBookModel()
        small = lob.lob_impact_bps(500)
        large = lob.lob_impact_bps(5000)
        assert large > small

    def test_lob_impact_capped_at_n_levels(self):
        """超过 n_levels 档的订单冲击不超过 n_levels 档上限."""
        lob = LimitOrderBookModel(LOBParams(n_levels=5))
        impact_5levels = lob.lob_impact_bps(5000)  # 5 档
        impact_huge = lob.lob_impact_bps(100000)  # 远超 5 档
        assert impact_huge == pytest.approx(impact_5levels)

    def test_execution_rate_u_shape(self):
        """U 型: 盘中速率高, 开盘尾盘速率低."""
        lob = LimitOrderBookModel()
        rate_open = lob.execution_rate(0.0, 1.0)
        rate_mid = lob.execution_rate(0.5, 1.0)
        rate_close = lob.execution_rate(1.0, 1.0)
        assert rate_mid > rate_open
        assert rate_mid > rate_close

    def test_execution_rate_zero_time(self):
        lob = LimitOrderBookModel()
        assert lob.execution_rate(0.5, 0.0) == 1.0


# ============================================================
# TT-DAC-PS 执行器测试
# ============================================================


class TestTTDACPSExecutor:
    def test_basic_execute(self):
        executor = TTDACPSExecutor()
        result = executor.execute(
            symbol="600519",
            total_shares=10000,
            adv=500000,
            decision_price=1800.0,
        )
        assert result.symbol == "600519"
        assert result.total_shares == 10000
        assert len(result.slices) == 10
        assert result.total_cost_bps > 0

    def test_negative_shares_abs(self):
        executor = TTDACPSExecutor()
        result = executor.execute(
            symbol="X",
            total_shares=-5000,
            adv=100000,
        )
        assert result.total_shares == 5000

    def test_slices_sum_approx_total(self):
        executor = TTDACPSExecutor()
        result = executor.execute(
            symbol="X",
            total_shares=10000,
            adv=500000,
            n_slices=10,
        )
        total = sum(s.shares for s in result.slices)
        # LOB 速率调整后总量可能略有偏差, 但应接近
        assert total == pytest.approx(10000, rel=0.3)

    def test_ou_noise_path_length(self):
        executor = TTDACPSExecutor()
        result = executor.execute(
            symbol="X",
            total_shares=1000,
            adv=100000,
            n_slices=20,
        )
        assert len(result.ou_noise_path) == 20

    def test_reproducible_with_seed(self):
        exec1 = TTDACPSExecutor(seed=42)
        exec2 = TTDACPSExecutor(seed=42)
        r1 = exec1.execute(symbol="X", total_shares=1000, adv=100000)
        r2 = exec2.execute(symbol="X", total_shares=1000, adv=100000)
        assert r1.total_cost_bps == pytest.approx(r2.total_cost_bps)

    def test_metadata(self):
        executor = TTDACPSExecutor()
        result = executor.execute(
            symbol="X",
            total_shares=1000,
            adv=100000,
            n_slices=5,
            risk_aversion=2.0,
        )
        assert result.metadata["n_slices"] == 5
        assert result.metadata["risk_aversion"] == 2.0
        assert result.metadata["adv"] == 100000

    def test_custom_n_slices(self):
        executor = TTDACPSExecutor()
        result = executor.execute(
            symbol="X",
            total_shares=1000,
            adv=100000,
            n_slices=20,
        )
        assert len(result.slices) == 20

    def test_slice_fields(self):
        executor = TTDACPSExecutor()
        result = executor.execute(
            symbol="X",
            total_shares=1000,
            adv=100000,
            decision_price=100.0,
        )
        s = result.slices[0]
        assert s.time >= 0
        assert s.shares > 0
        assert s.impact_bps > 0
        assert s.lob_impact_bps >= 0


class TestCompareWithBenchmarks:
    def test_comparison_report(self):
        executor = TTDACPSExecutor()
        comp = executor.compare_with_benchmarks(
            symbol="600519",
            total_shares=10000,
            adv=100000,
        )
        assert "tt_dac_ps_cost_bps" in comp
        assert "twap_cost_bps" in comp
        assert "vwap_cost_bps" in comp
        assert "ac_cost_bps" in comp
        assert "beats_twap" in comp
        assert "beats_vwap" in comp
        assert "beats_ac" in comp

    def test_participation_rate(self):
        executor = TTDACPSExecutor()
        comp = executor.compare_with_benchmarks(
            symbol="X",
            total_shares=50000,
            adv=100000,
        )
        assert comp["participation_rate"] == pytest.approx(0.5)

    def test_improvement_fields(self):
        executor = TTDACPSExecutor()
        comp = executor.compare_with_benchmarks(
            symbol="X",
            total_shares=10000,
            adv=100000,
        )
        assert comp["vs_twap_improvement_bps"] == pytest.approx(
            comp["twap_cost_bps"] - comp["tt_dac_ps_cost_bps"]
        )
        assert comp["vs_vwap_improvement_bps"] == pytest.approx(
            comp["vwap_cost_bps"] - comp["tt_dac_ps_cost_bps"]
        )

    def test_all_costs_positive(self):
        executor = TTDACPSExecutor()
        comp = executor.compare_with_benchmarks(
            symbol="X",
            total_shares=10000,
            adv=100000,
        )
        assert comp["tt_dac_ps_cost_bps"] > 0
        assert comp["twap_cost_bps"] > 0
        assert comp["vwap_cost_bps"] > 0


class TestTTDACPSResult:
    def test_construction(self):
        result = TTDACPSResult(
            symbol="X",
            total_shares=1000,
            slices=[],
            total_cost_bps=100.0,
            ac_cost_bps=120.0,
            twap_cost_bps=150.0,
            vwap_cost_bps=140.0,
            vs_twap_improvement=50.0,
            vs_vwap_improvement=40.0,
            vs_ac_improvement=20.0,
            ou_noise_path=[0.0, 0.001],
        )
        assert result.symbol == "X"
        assert result.metadata == {}


class TestExecutionSlice:
    def test_construction(self):
        s = ExecutionSlice(
            time=0.5,
            shares=100,
            price=1801.0,
            impact_bps=5.0,
            ou_noise=0.001,
            lob_impact_bps=2.0,
        )
        assert s.time == 0.5
        assert s.shares == 100


class TestTTDACPSBeatsBenchmarks:
    """验收标准: TT-DAC-PS 超越 TWAP/VWAP/AC 基准."""

    def test_beats_twap_large_order(self):
        """大单 (参与度 50%): TT-DAC-PS < TWAP."""
        executor = TTDACPSExecutor()
        comp = executor.compare_with_benchmarks(
            symbol="600519",
            total_shares=50000,
            adv=100000,
        )
        assert comp["beats_twap"] is True

    def test_beats_vwap_large_order(self):
        """大单 (参与度 50%): TT-DAC-PS < VWAP."""
        executor = TTDACPSExecutor()
        comp = executor.compare_with_benchmarks(
            symbol="600519",
            total_shares=50000,
            adv=100000,
        )
        assert comp["beats_vwap"] is True

    def test_beats_ac_large_order(self):
        """大单 (参与度 50%): TT-DAC-PS < AC."""
        executor = TTDACPSExecutor()
        comp = executor.compare_with_benchmarks(
            symbol="600519",
            total_shares=50000,
            adv=100000,
        )
        assert comp["beats_ac"] is True

    def test_beats_all_participations(self):
        """所有参与度 (1%~50%): TT-DAC-PS 超越所有基准."""
        executor = TTDACPSExecutor()
        for participation in [0.05, 0.10, 0.20, 0.50]:
            shares = int(participation * 100000)
            comp = executor.compare_with_benchmarks(
                symbol="TEST",
                total_shares=shares,
                adv=100000,
            )
            assert comp["beats_twap"], f"参与度 {participation}: 未超越 TWAP"
            assert comp["beats_vwap"], f"参与度 {participation}: 未超越 VWAP"
            assert comp["beats_ac"], f"参与度 {participation}: 未超越 AC"
