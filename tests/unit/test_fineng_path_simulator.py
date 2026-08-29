"""utils.fineng.path_simulator 单元测试 — PathSimulator / simulate_portfolio / compare_history / report"""

from __future__ import annotations

import random

import pytest

from utils.fineng.path_simulator import (
    PathSimResult,
    PathSimulator,
    generate_stress_report,
)


def _simulator(n_paths: int = 200, seed: int = 123) -> PathSimulator:
    return PathSimulator(
        n_paths=n_paths, n_days=21, seed=seed, residual_method="normal"
    )


def _cov() -> list[list[float]]:
    return [[0.0004, 0.0001], [0.0001, 0.0009]]


def _empty_result() -> PathSimResult:
    """构造空 PathSimResult (供边界测试, NaN 占位)"""
    return PathSimResult(
        dd_p50=float("nan"),
        dd_p75=float("nan"),
        dd_p90=float("nan"),
        dd_p95=float("nan"),
        dd_p99=float("nan"),
        dd_max=float("nan"),
        nav_terminal_p50=float("nan"),
        nav_terminal_p10=float("nan"),
        nav_terminal_p05=float("nan"),
        nav_terminal_p01=float("nan"),
        n_paths=1,
        n_days=1,
        n_assets=2,
    )


def test_simulate_portfolio_shape_and_dd():
    """simulate_portfolio: 返回 PathSimResult, 回撤分位数有序 (P50<=P99<=Max)"""
    sim = _simulator()
    result = sim.simulate_portfolio([0.6, 0.4], _cov(), daily_mean=[0.0003, 0.0002])
    assert isinstance(result, PathSimResult)
    assert result.n_paths == 200
    assert result.n_days == 21
    assert result.dd_p50 <= result.dd_p99
    assert result.dd_p99 <= result.dd_max
    assert result.dd_max >= 0.0


def test_simulate_portfolio_reproducible():
    """simulate_portfolio: 同 seed 两次结果一致 (P50/P99 回撤相等)"""
    w = [0.5, 0.5]
    r1 = _simulator(123).simulate_portfolio(w, _cov())
    random.seed(123)
    r2 = _simulator(123).simulate_portfolio(w, _cov())
    assert r1.dd_p50 == pytest.approx(r2.dd_p50, abs=1e-9)
    assert r1.dd_p99 == pytest.approx(r2.dd_p99, abs=1e-9)


def test_simulate_portfolio_nav_terminal():
    """simulate_portfolio: 正漂移下终端 NAV P50 应 > 1.0 (P10 < P50)"""
    sim = _simulator()
    result = sim.simulate_portfolio([0.5, 0.5], _cov(), daily_mean=[0.0003, 0.0003])
    assert result.nav_terminal_p50 > 1.0
    assert result.nav_terminal_p10 < result.nav_terminal_p50


def test_compare_history_p90_coverage():
    """compare_history: 注入历史事件, 验证 P90 覆盖性标记"""
    sim = _simulator()
    result = sim.simulate_portfolio([0.6, 0.4], _cov(), daily_mean=[0.0003, 0.0002])
    out = sim.compare_history(result, dd_2015=-0.45, dd_2020=-0.15)
    assert out.historical_dd_2015 == pytest.approx(-0.45)
    assert isinstance(out.p90_covers_2015, bool)
    assert isinstance(out.p90_covers_2020, bool)


def test_compare_history_returns_same_object():
    """compare_history: 原地修改并返回同一 result"""
    sim = _simulator()
    result = sim.simulate_portfolio([0.5, 0.5], _cov())
    ret = sim.compare_history(result, dd_2015=-0.40)
    assert ret is result


def test_generate_stress_report():
    """generate_stress_report: 从 PathSimResult 生成 StressTestReport"""
    sim = _simulator()
    result = sim.simulate_portfolio([0.6, 0.4], _cov(), daily_mean=[0.0003, 0.0002])
    sim.compare_history(result, dd_2015=-0.45, dd_2020=-0.15)
    report = generate_stress_report(
        result, portfolio_name="test", date_str="2026-08-10"
    )
    assert report.portfolio_name == "test"
    assert report.n_paths == 200
    assert "P50" in report.dd_distribution_summary
    assert "P99" in report.dd_distribution_summary
    assert "2015_Crash" in report.historical_dd_comparison
    assert report.historical_dd_comparison["2015_Crash"][
        "Historical_DD"
    ] == pytest.approx(-0.45)
    assert isinstance(report.p90_adequate, bool)


def test_bootstrap_without_residuals_uses_normal():
    """residual_method='normal' 模式 (非 bootstrap) 无需历史残差即可模拟"""
    sim = PathSimulator(n_paths=10, n_days=5, seed=1, residual_method="normal")
    result = sim.simulate_portfolio([0.5, 0.5], _cov())
    assert result.n_paths == 10


def test_empty_result_is_nan_safe():
    """空 PathSimResult: compare_history 不修改 (历史字段默认 NaN)"""
    r = _empty_result()
    assert r.n_paths == 1
    # 默认 p90_covers_2015=False (未注入)
    assert r.p90_covers_2015 is False
