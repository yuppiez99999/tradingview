"""G7 boost: ms_strategy/src/backtest/scenario_lib.py 单元测试.

覆盖 StressScenario / STRESS_SCENARIOS / ScenarioLibrary 的全部公开接口,
包括场景加载/查询/What-If/蒙特卡洛/合规压力测试的核心路径与边界分支.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "ms_strategy"))

from ms_strategy.src.backtest.scenario_lib import (  # noqa: E402
    STRESS_SCENARIOS,
    STRESS_SCENARIOS_DICT,
    ScenarioLibrary,
    StressScenario,
    StressScenarioLib,
)

# ============================================================
# 1. StressScenario dataclass / 模块级常量
# ============================================================


class TestStressScenario:
    def test_construct(self):
        s = StressScenario(
            name="X", start="2020-01-01", end="2020-02-01", description="test"
        )
        assert s.name == "X"
        assert s.key_metrics == {}
        assert s.asset_shocks == {}

    def test_stress_scenarios_count(self):
        assert len(STRESS_SCENARIOS) == 3

    def test_stress_scenarios_dict_keys(self):
        assert set(STRESS_SCENARIOS_DICT.keys()) == {
            "COVID_CRASH",
            "LUNA_CRASH",
            "YEN_CARRY",
        }

    def test_covid_crash_shocks(self):
        s = STRESS_SCENARIOS_DICT["COVID_CRASH"]
        assert s.asset_shocks["SPX"] == pytest.approx(-0.335)
        assert s.asset_shocks["CSI300"] == pytest.approx(-0.130)


# ============================================================
# 2. ScenarioLibrary 基础
# ============================================================


class TestScenarioLibraryInit:
    def test_init_loads_predefined(self):
        lib = ScenarioLibrary()
        assert len(lib.scenarios) == 3
        assert len(lib.custom_scenarios) == 0

    def test_scenario_list_property(self):
        lib = ScenarioLibrary()
        lst = lib.scenario_list
        assert len(lst) == 3
        assert all(isinstance(s, StressScenario) for s in lst)

    def test_list_scenarios(self):
        lib = ScenarioLibrary()
        names = lib.list_scenarios()
        assert "COVID_CRASH" in names
        assert "LUNA_CRASH" in names
        assert "YEN_CARRY" in names

    def test_get_scenario_existing(self):
        lib = ScenarioLibrary()
        s = lib.get_scenario("COVID_CRASH")
        assert s is not None
        assert s.name == "COVID_CRASH"

    def test_get_scenario_nonexistent(self):
        lib = ScenarioLibrary()
        assert lib.get_scenario("NONEXIST") is None

    def test_add_custom_scenario(self):
        lib = ScenarioLibrary()
        custom = StressScenario(
            name="CUSTOM", start="2021-01-01", end="2021-02-01", description="custom"
        )
        lib.add_scenario(custom)
        assert lib.get_scenario("CUSTOM") is not None
        assert "CUSTOM" in lib.list_scenarios()

    def test_get_custom_scenario(self):
        lib = ScenarioLibrary()
        custom = StressScenario(
            name="C", start="2021-01-01", end="2021-02-01", description="c"
        )
        lib.add_scenario(custom)
        assert lib.get_scenario("C").name == "C"

    def test_stress_scenario_lib_alias(self):
        assert StressScenarioLib is ScenarioLibrary


# ============================================================
# 3. what_if
# ============================================================


class TestWhatIf:
    def test_scenario_not_exist(self):
        lib = ScenarioLibrary()
        result = lib.what_if({}, "NONEXIST", {})
        assert "error" in result

    def test_covid_crash_uses_csi300(self):
        lib = ScenarioLibrary()
        positions = {"AAPL": 100}
        prices = {"AAPL": 100.0}
        result = lib.what_if(positions, "COVID_CRASH", prices)
        assert result["scenario"] == "COVID_CRASH"
        assert result["market_shock_used"] == pytest.approx(-0.130)
        # pnl = (100 * (1 - 0.13) - 100) * 100 = -13 * 100 = -1300
        assert result["pnl_by_symbol"]["AAPL"] == pytest.approx(-1300.0)
        assert result["total_pnl"] == pytest.approx(-1300.0)

    def test_luna_crash_uses_spx(self):
        lib = ScenarioLibrary()
        positions = {"AAPL": 100}
        prices = {"AAPL": 100.0}
        result = lib.what_if(positions, "LUNA_CRASH", prices)
        # LUNA 无 CSI300/SPX/NIKKEI → 默认 -0.05
        assert result["market_shock_used"] == pytest.approx(-0.05)

    def test_yen_carry_uses_spx(self):
        lib = ScenarioLibrary()
        positions = {"AAPL": 100}
        prices = {"AAPL": 100.0}
        result = lib.what_if(positions, "YEN_CARRY", prices)
        # YEN_CARRY 含 SPX (-0.06), 优先于 NIKKEI 匹配
        assert result["market_shock_used"] == pytest.approx(-0.06)

    def test_price_zero_skipped(self):
        lib = ScenarioLibrary()
        positions = {"AAPL": 100, "MSFT": 50}
        prices = {"AAPL": 0.0, "MSFT": 100.0}
        result = lib.what_if(positions, "COVID_CRASH", prices)
        assert "AAPL" not in result["pnl_by_symbol"]
        assert "MSFT" in result["pnl_by_symbol"]

    def test_empty_positions(self):
        lib = ScenarioLibrary()
        result = lib.what_if({}, "COVID_CRASH", {})
        assert result["total_pnl"] == 0.0
        assert result["pnl_pct"] == 0.0

    def test_pnl_pct_calculation(self):
        lib = ScenarioLibrary()
        positions = {"AAPL": 100}
        prices = {"AAPL": 100.0}
        result = lib.what_if(positions, "COVID_CRASH", prices)
        total_nlv = 100.0 * 100
        assert result["pnl_pct"] == pytest.approx(result["total_pnl"] / total_nlv)


# ============================================================
# 4. monte_carlo_tail
# ============================================================


class TestMonteCarloTail:
    def test_empty_positions(self):
        lib = ScenarioLibrary()
        result = lib.monte_carlo_tail({}, {})
        assert result == {}

    def test_total_value_zero(self):
        lib = ScenarioLibrary()
        # qty=0 或 price=0 → w 全 0 → total_value=0
        result = lib.monte_carlo_tail({"X": 0}, {"X": 100.0})
        assert result == {}

    def test_t_dist_normal(self):
        lib = ScenarioLibrary()
        positions = {"AAPL": 100, "MSFT": 50}
        prices = {"AAPL": 100.0, "MSFT": 200.0}
        result = lib.monte_carlo_tail(
            positions, prices, n_simulations=500, use_t_dist=True, df=5
        )
        assert "var_95" in result
        assert "var_99" in result
        assert "expected_shortfall_95" in result
        assert "expected_shortfall_99" in result
        assert "worst_case" in result
        assert result["n_simulations"] == 500
        assert result["var_99"] <= result["var_95"]
        assert result["worst_case"] <= result["var_99"]

    def test_normal_dist(self):
        lib = ScenarioLibrary()
        positions = {"AAPL": 100}
        prices = {"AAPL": 100.0}
        result = lib.monte_carlo_tail(
            positions, prices, n_simulations=500, use_t_dist=False
        )
        assert "var_95" in result
        assert result["n_simulations"] == 500

    def test_deterministic_with_seed(self):
        lib1 = ScenarioLibrary()
        lib2 = ScenarioLibrary()
        positions = {"AAPL": 100}
        prices = {"AAPL": 100.0}
        r1 = lib1.monte_carlo_tail(positions, prices, n_simulations=200)
        r2 = lib2.monte_carlo_tail(positions, prices, n_simulations=200)
        # 固定 seed=42 → 结果一致
        assert r1["var_95"] == pytest.approx(r2["var_95"])

    def test_pct_calculations(self):
        lib = ScenarioLibrary()
        positions = {"AAPL": 100}
        prices = {"AAPL": 100.0}
        result = lib.monte_carlo_tail(positions, prices, n_simulations=200)
        total_value = 100 * 100.0
        assert result["var_95_pct"] == pytest.approx(result["var_95"] / total_value)


# ============================================================
# 5. run_compliance_tests
# ============================================================


class TestRunComplianceTests:
    def test_all_scenarios_present(self):
        lib = ScenarioLibrary()
        positions = {"AAPL": 100}
        prices = {"AAPL": 100.0}
        results = lib.run_compliance_tests(positions, prices)
        assert set(results.keys()) == {"COVID_CRASH", "LUNA_CRASH", "YEN_CARRY"}
        for _name, r in results.items():
            assert "max_dd" in r
            assert "passed" in r
            assert "warning" in r
            assert "pnl_pct" in r
            assert "description" in r

    def test_passed_flag_logic(self):
        lib = ScenarioLibrary()
        positions = {"AAPL": 100}
        prices = {"AAPL": 100.0}
        results = lib.run_compliance_tests(positions, prices)
        for r in results.values():
            assert r["passed"] == (r["max_dd"] < 0.15)
            assert r["warning"] == (0.12 <= r["max_dd"] < 0.15)


# ============================================================
# 6. check_pass_criteria
# ============================================================


class TestCheckPassCriteria:
    def test_all_passed(self):
        lib = ScenarioLibrary()
        compliance = {
            "S1": {"passed": True, "warning": False, "max_dd": 0.05, "pnl_pct": -0.05},
            "S2": {"passed": True, "warning": False, "max_dd": 0.08, "pnl_pct": -0.08},
        }
        all_passed, needs_cro, details = lib.check_pass_criteria(compliance)
        assert all_passed is True
        assert needs_cro is False
        assert details["all_passed"] is True
        assert details["needs_cro_signoff"] is False

    def test_not_passed(self):
        lib = ScenarioLibrary()
        compliance = {
            "S1": {"passed": False, "warning": False, "max_dd": 0.20, "pnl_pct": -0.20},
        }
        all_passed, needs_cro, _ = lib.check_pass_criteria(compliance)
        assert all_passed is False
        assert needs_cro is False

    def test_warning_needs_cro(self):
        lib = ScenarioLibrary()
        compliance = {
            "S1": {"passed": True, "warning": True, "max_dd": 0.13, "pnl_pct": -0.13},
        }
        all_passed, needs_cro, _ = lib.check_pass_criteria(compliance)
        assert all_passed is True
        assert needs_cro is True

    def test_empty_compliance(self):
        lib = ScenarioLibrary()
        all_passed, needs_cro, _ = lib.check_pass_criteria({})
        assert all_passed is True
        assert needs_cro is False


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
