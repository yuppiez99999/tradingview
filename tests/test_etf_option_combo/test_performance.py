"""05_05b — 性能测试.

性能基线 (spec.md §9):
    - 50 腿 Greeks 计算 < 100ms
    - 全组合扫描 < 3 秒
    - 建仓 < 500ms
    - BS 降级 < 200ms
    - 状态持久化 < 50ms
"""

from __future__ import annotations

import sys
import time

import pytest

from utils.etf_option_combo.combo_base import (
    ComboBase,
    LegSide,
    OptionChainFetcher,
    StrategyType,
)
from utils.etf_option_combo.combo_state import ComboStateManager
from utils.etf_option_combo.covered_call import CoveredCallEngine


pytestmark = pytest.mark.slow


class TestGreeksCalcPerformance:
    def test_greeks_calc_performance_50_legs(self, chain_fetcher, fixed_spot_price, make_combo_leg):
        """50 腿 Greeks 计算 < 100ms."""

        class DummyStrategy(ComboBase):
            def _select_legs(self, underlying, spot_price, option_chain, spot_position):
                return (None, "DUMMY")

            def _validate_business_rules(self, legs, spot_price):
                return None

        engine = DummyStrategy(
            strategy_type=StrategyType.COVERED_CALL,
            config={}, chain_fetcher=chain_fetcher, greek_manager=None,
        )
        legs = tuple(make_combo_leg() for _ in range(50))
        t0 = time.perf_counter()
        for _ in range(10):
            engine._calc_combo_greeks(legs, fixed_spot_price)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert elapsed_ms < 100, f"50腿Greeks {elapsed_ms:.2f}ms >= 100ms"


class TestFullScanPerformance:
    def test_full_scan_performance(self, chain_fetcher, base_config, underlying_code):
        """全组合扫描 (5 策略) < 3 秒."""
        from utils.etf_option_combo.collar import CollarEngine
        from utils.etf_option_combo.cash_secured_put import CashSecuredPutEngine
        from utils.etf_option_combo.vertical_spread import VerticalSpreadEngine
        from utils.etf_option_combo.calendar_spread import CalendarSpreadEngine

        engines = [
            CoveredCallEngine(base_config, chain_fetcher),
            CollarEngine(base_config, chain_fetcher),
            CashSecuredPutEngine(base_config, chain_fetcher),
            VerticalSpreadEngine(base_config, chain_fetcher),
            CalendarSpreadEngine(base_config, chain_fetcher),
        ]
        pos = {"shares": 10000, "available_cash": 100_000.0, "target_weight": 0.1, "current_weight": 0.0}
        t0 = time.perf_counter()
        for engine in engines:
            engine.generate(underlying_code, pos)
        elapsed = time.perf_counter() - t0
        assert elapsed < 3.0, f"全组合扫描 {elapsed:.2f}s >= 3s"


class TestBuildLatency:
    def test_build_latency(self, chain_fetcher, base_config, underlying_code, spot_position_sufficient):
        """单策略建仓 < 500ms."""
        engine = CoveredCallEngine(base_config, chain_fetcher)
        t0 = time.perf_counter()
        engine.generate(underlying_code, spot_position_sufficient)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert elapsed_ms < 500, f"建仓 {elapsed_ms:.2f}ms >= 500ms"


class TestBSDegradeTimeout:
    def test_bs_degrade_timeout(self, failing_fetcher, fixed_spot_price):
        """BS 降级 (fetcher 失败) < 200ms."""
        fetcher = OptionChainFetcher(fetcher=failing_fetcher, bs_timeout_ms=200)
        t0 = time.perf_counter()
        chain = fetcher.get_option_chain(
            underlying="510050.SH",
            option_type="CALL",
            dte_range=(30, 60),
            otm_range=(0.02, 0.08),
            min_volume=0,
            spot_price=fixed_spot_price,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert chain == []
        assert elapsed_ms < 200, f"BS降级 {elapsed_ms:.2f}ms >= 200ms"


class TestStatePersistenceLatency:
    def test_state_persistence_latency(self, tmp_state_path):
        """状态持久化 < 50ms."""
        mgr = ComboStateManager(state_path=tmp_state_path)
        t0 = time.perf_counter()
        for i in range(10):
            mgr.save_strategy_instance(f"inst_{i}", {"idx": i})
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert elapsed_ms < 50, f"状态持久化 {elapsed_ms:.2f}ms >= 50ms"


class TestPythonCompat:
    def test_python_version(self):
        """Python 3.8+ 兼容."""
        assert sys.version_info >= (3, 8)

    def test_frozen_dataclass_available(self):
        """frozen dataclass 可用 (Python 3.7+)."""
        from dataclasses import is_dataclass
        from utils.etf_option_combo.combo_base import ComboLeg
        assert is_dataclass(ComboLeg)