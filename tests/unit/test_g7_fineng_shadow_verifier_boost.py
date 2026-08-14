"""G7 覆盖率冲刺 — utils/fineng/fineng_shadow_verifier.py 补测试.

目标模块: utils/fineng/fineng_shadow_verifier.py (24.79% → 目标 60%+)

覆盖核心路径:
    - 纯函数: _cv / _dir_consistency / _extract_key_metrics / _flatten_metrics / _serialize
    - 数据类默认值: ModuleWindowResult / ModuleFullResult / FinengVerificationReport
    - FinengShadowVerifier:
        * __init__ (min_windows 钳制) / _import_modules (fail-soft 标志位)
        * _generate_windows (正常/短数据/尾部覆盖/精确拟合)
        * _run_garch_window (成功/未收敛/persistence异常/ratio异常/异常路径)
        * _run_kalman_window (成功/未收敛/beta异常/异常路径)
        * _run_evt_window (成功/未收敛/n_excess低/xi异常/异常路径)
        * _run_pathsim_window (成功/dd过小/dd过大/非单调/异常路径)
        * _run_stage_a (全可用/各模块 blocked 分支/kalman 无 idx)
        * _run_stage_b (正常/kalman 窗口无 idx/blocked 跳过)
        * _compute_final_verdict (通过/失败/带 blocked)
        * run_verification (数据不足/窗口不足/正常路径)
        * _save_report (JSON 落盘)
    - run_fineng_shadow_verification (便捷函数)

约束:
    - 不发起任何真实网络请求
    - 用 unittest.mock.patch / MagicMock 隔离外部依赖
    - 测试文件可独立运行:
      python -m pytest tests/unit/test_g7_fineng_shadow_verifier_boost.py -q
"""
from __future__ import annotations

import json
import math
import os
import random
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ============================================================
# 路径设置 (必须在导入被测模块前完成)
# ============================================================
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils.fineng import fineng_shadow_verifier as fsv  # noqa: E402
from utils.fineng.fineng_shadow_verifier import (  # noqa: E402
    CV_ACCEPTANCE,
    DIR_CONSISTENCY_ACCEPTANCE,
    EVT_MIN_EXCESS,
    EVT_XI_MAX,
    EVT_XI_MIN,
    GARCH_PERSISTENCE_MAX,
    GARCH_PERSISTENCE_MIN,
    GARCH_RATIO_MAX,
    GARCH_RATIO_MIN,
    KALMAN_BETA_MAX,
    KALMAN_BETA_MIN,
    MIN_DATA_LENGTH,
    MIN_WINDOWS,
    PATHSIM_DD_P99_MAX,
    PATHSIM_DD_P99_MIN,
    FinengShadowVerifier,
    FinengVerificationReport,
    ModuleFullResult,
    ModuleWindowResult,
    _cv,
    _dir_consistency,
    _extract_key_metrics,
    _flatten_metrics,
    _serialize,
    run_fineng_shadow_verification,
)


# ============================================================
# 工厂辅助
# ============================================================

def _make_garch_mock(converged: bool = True, persistence: float = 0.95,
                     forecast_vol: float = 0.20, long_run_vol: float = 0.18,
                     half_life_days: float = 13.0) -> MagicMock:
    g = MagicMock()
    g.converged = converged
    g.persistence = persistence
    g.forecast_vol = forecast_vol
    g.long_run_vol = long_run_vol
    g.half_life_days = half_life_days
    return g


def _make_kalman_mock(converged: bool = True, latest_beta: float = 1.0,
                      mean_beta: float = 1.0) -> MagicMock:
    k = MagicMock()
    k.converged = converged
    k.latest_beta = latest_beta
    k.mean_beta = mean_beta
    return k


def _make_hedge_mock(kalman_var: float = 0.001, ols_var: float = 0.002,
                     reduction_pct: float = 0.5) -> MagicMock:
    c = MagicMock()
    c.kalman_hedged_variance = kalman_var
    c.ols_hedged_variance = ols_var
    c.variance_reduction_pct = reduction_pct
    return c


def _make_evt_mock(converged: bool = True, xi: float = 0.1, sigma: float = 0.01,
                   n_excess: int = 30, es_99: float = -0.05,
                   var_99: float = -0.04) -> MagicMock:
    e = MagicMock()
    e.converged = converged
    e.xi = xi
    e.sigma = sigma
    e.n_excess = n_excess
    e.es_99 = es_99
    e.var_99 = var_99
    return e


def _make_pathsim_result(dd_p50: float = 0.05, dd_p75: float = 0.07,
                         dd_p90: float = 0.10, dd_p95: float = 0.12,
                         dd_p99: float = 0.18, dd_max: float = 0.25,
                         nav_terminal_p50: float = 1.0) -> MagicMock:
    r = MagicMock()
    r.dd_p50 = dd_p50
    r.dd_p75 = dd_p75
    r.dd_p90 = dd_p90
    r.dd_p95 = dd_p95
    r.dd_p99 = dd_p99
    r.dd_max = dd_max
    r.nav_terminal_p50 = nav_terminal_p50
    return r


def _make_verifier_with_mocks(**overrides) -> FinengShadowVerifier:
    """构造一个 Verifier, 并把四模块 fit 函数替换为可控 mock (全部 available)."""
    v = FinengShadowVerifier()
    v._fit_garch = overrides.get("fit_garch", MagicMock(return_value=_make_garch_mock()))
    v._ewma_vol = overrides.get("ewma_vol", MagicMock(return_value=(0.18, [])))
    v._fit_kalman = overrides.get("fit_kalman", MagicMock(return_value=_make_kalman_mock()))
    v._backtest_hedge = overrides.get("backtest_hedge", MagicMock(return_value=_make_hedge_mock()))
    v._fit_evt = overrides.get("fit_evt", MagicMock(return_value=_make_evt_mock()))
    ps_result = overrides.get("pathsim_result", _make_pathsim_result())
    ps_inst = MagicMock()
    ps_inst.simulate_portfolio = MagicMock(return_value=ps_result)
    v._PathSimulator = MagicMock(return_value=ps_inst)
    v._garch_ok = True
    v._kalman_ok = True
    v._evt_ok = True
    v._pathsim_ok = True
    return v


def _synthetic_returns(n: int = 300, mu: float = 0.0, sigma: float = 0.01,
                       seed: int = 42) -> list:
    rng = random.Random(seed)
    return [rng.gauss(mu, sigma) for _ in range(n)]


# ============================================================
# 1. 纯函数: _cv
# ============================================================


class TestCv:
    """_cv 变异系数纯函数."""

    def test_len_lt_3_returns_nan(self) -> None:
        assert math.isnan(_cv([1.0, 2.0]))
        assert math.isnan(_cv([1.0]))
        assert math.isnan(_cv([]))

    def test_mean_near_zero_with_nonzero_returns_one(self) -> None:
        # mean ≈ 0 (|m| < 1e-12) 但存在 |v| > 1e-10 的非零值 → 1.0
        result = _cv([1e-6, -1e-6, 0.0])
        assert result == 1.0

    def test_mean_near_zero_all_zero_returns_zero(self) -> None:
        # mean ≈ 0 且全为零 → 0.0
        result = _cv([0.0, 0.0, 0.0])
        assert result == 0.0

    def test_normal_case_positive(self) -> None:
        # [1,2,3]: mean=2, var=1, std=1, cv=0.5
        result = _cv([1.0, 2.0, 3.0])
        assert result == pytest.approx(0.5, abs=1e-9)

    def test_negative_values(self) -> None:
        # 负值: cv 应基于绝对值均值
        result = _cv([-1.0, -2.0, -3.0])
        assert result == pytest.approx(0.5, abs=1e-9)

    def test_identical_values_zero_cv(self) -> None:
        result = _cv([5.0, 5.0, 5.0])
        assert result == 0.0


# ============================================================
# 2. 纯函数: _dir_consistency
# ============================================================


class TestDirConsistency:
    """_dir_consistency 相邻窗口方向一致性."""

    def test_lt_2_common_returns_half(self) -> None:
        assert _dir_consistency({"a": 1.0}, {"a": 2.0}) == 0.5
        assert _dir_consistency({}, {}) == 0.5

    def test_all_same_direction(self) -> None:
        # prev 全正, curr 更正 → 同向
        prev = {"a": 1.0, "b": 2.0}
        curr = {"a": 1.5, "b": 2.5}
        assert _dir_consistency(prev, curr) == 1.0

    def test_mixed_direction(self) -> None:
        # a 同向 (正→更正), b 反向 (正→负)
        prev = {"a": 1.0, "b": 2.0}
        curr = {"a": 1.5, "b": -1.0}
        assert _dir_consistency(prev, curr) == 0.5

    def test_zero_prev_treated_as_same(self) -> None:
        # prev=0 → (curr-0)*0 = 0 >= 0 → 同向
        prev = {"a": 0.0, "b": 0.0}
        curr = {"a": 1.0, "b": -1.0}
        assert _dir_consistency(prev, curr) == 1.0


# ============================================================
# 3. 数据类默认值
# ============================================================


class TestDataclassDefaults:
    """dataclass 默认值与字段."""

    def test_module_window_result_defaults(self) -> None:
        wr = ModuleWindowResult(window_idx=0, data_start="d0", data_end="d9", n_days=10,
                                metrics={"x": 1.0})
        assert wr.window_idx == 0
        assert wr.warnings == []
        assert wr.converged is True

    def test_module_full_result_defaults(self) -> None:
        mr = ModuleFullResult(module="garch")
        assert mr.available is True
        assert mr.full_converged is False
        assert mr.full_metrics == {}
        assert mr.full_warnings == []
        assert mr.windows == []
        assert mr.n_windows == 0
        assert mr.n_converged_windows == 0
        assert mr.cross_window_cv == 1.0
        assert mr.direction_consistency == 0.0
        assert mr.passed is False
        assert mr.blocked is False
        assert mr.rejection_reason == ""

    def test_fineng_verification_report_defaults(self) -> None:
        rep = FinengVerificationReport(
            run_timestamp="2026-01-01", min_windows=5,
            total_data_days=300, actual_windows=7,
        )
        assert rep.portfolio_symbols == []
        assert rep.modules == {}
        assert rep.passed_modules == []
        assert rep.failed_modules == []
        assert rep.blocked_modules == []
        assert rep.accepted is False
        assert rep.acceptance_detail == ""
        assert rep.next_step == ""


# ============================================================
# 4. FinengShadowVerifier 构造与 _import_modules
# ============================================================


class TestVerifierInit:
    """构造器与 fail-soft 模块导入."""

    def test_init_default(self) -> None:
        v = FinengShadowVerifier()
        assert v.min_windows == MIN_WINDOWS
        assert v.window_size == 120
        assert v.window_step == 30
        assert v.seed == 42

    def test_init_min_windows_clamped(self) -> None:
        # min_windows < MIN_WINDOWS 被钳制到 MIN_WINDOWS
        v = FinengShadowVerifier(min_windows=2)
        assert v.min_windows == MIN_WINDOWS

    def test_init_custom_params(self) -> None:
        v = FinengShadowVerifier(min_windows=8, window_size=100, window_step=20, seed=7)
        assert v.min_windows == 8
        assert v.window_size == 100
        assert v.window_step == 20
        assert v.seed == 7

    def test_import_modules_sets_flags(self) -> None:
        # 真实模块在本项目存在 → 四个标志位应为 True
        v = FinengShadowVerifier()
        assert v._garch_ok is True
        assert v._kalman_ok is True
        assert v._evt_ok is True
        assert v._pathsim_ok is True

    def test_import_modules_fail_soft(self) -> None:
        # 模拟导入失败: garch 模块不可用
        with patch.dict(sys.modules, {"utils.fineng.vol_forecast": None}):
            v = FinengShadowVerifier()
            # 其他模块仍可用
            assert v._kalman_ok is True


# ============================================================
# 5. _generate_windows
# ============================================================


class TestGenerateWindows:
    """_generate_windows 滑动窗口生成."""

    def test_normal_generation(self) -> None:
        v = FinengShadowVerifier(window_size=120, window_step=30)
        windows = v._generate_windows(300)
        assert len(windows) >= 5
        # 每个窗口长度 = window_size
        for s, e in windows:
            assert e - s == 120
        # 步进正确
        assert windows[0] == (0, 120)
        assert windows[1] == (30, 150)

    def test_short_data_no_windows(self) -> None:
        v = FinengShadowVerifier(window_size=120, window_step=30)
        windows = v._generate_windows(50)
        assert windows == []

    def test_exact_fit_no_tail(self) -> None:
        # 数据正好被整数个窗口覆盖, 不追加尾部
        v = FinengShadowVerifier(window_size=120, window_step=30)
        windows = v._generate_windows(120)
        assert len(windows) == 1
        assert windows[0] == (0, 120)

    def test_tail_coverage_appended(self) -> None:
        # 最后窗口未覆盖末尾 → 追加尾部窗口
        v = FinengShadowVerifier(window_size=120, window_step=30)
        windows = v._generate_windows(140)
        assert len(windows) >= 2
        # 最后窗口应覆盖到 140
        assert windows[-1][1] == 140


# ============================================================
# 6. _run_garch_window
# ============================================================


class TestRunGarchWindow:
    """_run_garch_window 各分支."""

    def test_success_no_warnings(self) -> None:
        v = _make_verifier_with_mocks()
        wr = v._run_garch_window([0.01] * 130)
        assert wr.converged is True
        assert wr.warnings == []
        assert "forecast_vol" in wr.metrics
        assert "persistence" in wr.metrics
        assert "ratio_garch_ewma" in wr.metrics
        assert wr.metrics["ratio_garch_ewma"] == pytest.approx(0.20 / 0.18, abs=1e-6)

    def test_not_converged_warning(self) -> None:
        v = _make_verifier_with_mocks(
            fit_garch=MagicMock(return_value=_make_garch_mock(converged=False))
        )
        wr = v._run_garch_window([0.01] * 130)
        assert wr.converged is False
        assert any("未收敛" in w for w in wr.warnings)

    def test_persistence_out_of_range_warning(self) -> None:
        v = _make_verifier_with_mocks(
            fit_garch=MagicMock(return_value=_make_garch_mock(persistence=0.50))
        )
        wr = v._run_garch_window([0.01] * 130)
        assert any("persistence" in w for w in wr.warnings)

    def test_persistence_too_high_warning(self) -> None:
        v = _make_verifier_with_mocks(
            fit_garch=MagicMock(return_value=_make_garch_mock(persistence=0.9999))
        )
        wr = v._run_garch_window([0.01] * 130)
        assert any("persistence" in w for w in wr.warnings)

    def test_ratio_too_small_warning(self) -> None:
        # forecast_vol=0.05, ewma=0.18 → ratio≈0.28 < 0.50
        v = _make_verifier_with_mocks(
            fit_garch=MagicMock(return_value=_make_garch_mock(forecast_vol=0.05)),
        )
        wr = v._run_garch_window([0.01] * 130)
        assert any("ratio" in w for w in wr.warnings)

    def test_ratio_too_large_warning(self) -> None:
        # forecast_vol=0.50, ewma=0.18 → ratio≈2.78 > 2.00
        v = _make_verifier_with_mocks(
            fit_garch=MagicMock(return_value=_make_garch_mock(forecast_vol=0.50)),
        )
        wr = v._run_garch_window([0.01] * 130)
        assert any("ratio" in w for w in wr.warnings)

    def test_exception_returns_failed_result(self) -> None:
        v = _make_verifier_with_mocks(
            fit_garch=MagicMock(side_effect=ValueError("boom"))
        )
        wr = v._run_garch_window([0.01] * 130)
        assert wr.converged is False
        assert wr.metrics == {}
        assert "boom" in wr.warnings[0]


# ============================================================
# 7. _run_kalman_window
# ============================================================


class TestRunKalmanWindow:
    """_run_kalman_window 各分支."""

    def test_success_no_warnings(self) -> None:
        v = _make_verifier_with_mocks()
        wr = v._run_kalman_window([0.01] * 130, [0.02] * 130)
        assert wr.converged is True
        assert wr.warnings == []
        assert "latest_beta" in wr.metrics
        assert "var_reduction_pct" in wr.metrics

    def test_not_converged_warning(self) -> None:
        v = _make_verifier_with_mocks(
            fit_kalman=MagicMock(return_value=_make_kalman_mock(converged=False))
        )
        wr = v._run_kalman_window([0.01] * 130, [0.02] * 130)
        assert wr.converged is False
        assert any("未收敛" in w for w in wr.warnings)

    def test_beta_out_of_range_warning(self) -> None:
        v = _make_verifier_with_mocks(
            fit_kalman=MagicMock(return_value=_make_kalman_mock(latest_beta=5.0))
        )
        wr = v._run_kalman_window([0.01] * 130, [0.02] * 130)
        assert any("Beta" in w for w in wr.warnings)

    def test_beta_negative_out_of_range_warning(self) -> None:
        v = _make_verifier_with_mocks(
            fit_kalman=MagicMock(return_value=_make_kalman_mock(latest_beta=-2.0))
        )
        wr = v._run_kalman_window([0.01] * 130, [0.02] * 130)
        assert any("Beta" in w for w in wr.warnings)

    def test_exception_returns_failed_result(self) -> None:
        v = _make_verifier_with_mocks(
            fit_kalman=MagicMock(side_effect=RuntimeError("kalman boom"))
        )
        wr = v._run_kalman_window([0.01] * 130, [0.02] * 130)
        assert wr.converged is False
        assert wr.metrics == {}
        assert "kalman boom" in wr.warnings[0]


# ============================================================
# 8. _run_evt_window
# ============================================================


class TestRunEvtWindow:
    """_run_evt_window 各分支."""

    def test_success_no_warnings(self) -> None:
        v = _make_verifier_with_mocks()
        wr = v._run_evt_window([0.01] * 130)
        assert wr.converged is True
        assert wr.warnings == []
        assert "xi" in wr.metrics
        assert "n_excess" in wr.metrics
        assert "es_99" in wr.metrics

    def test_not_converged_warning(self) -> None:
        v = _make_verifier_with_mocks(
            fit_evt=MagicMock(return_value=_make_evt_mock(converged=False))
        )
        wr = v._run_evt_window([0.01] * 130)
        assert wr.converged is False
        assert any("未收敛" in w for w in wr.warnings)

    def test_n_excess_low_warning(self) -> None:
        v = _make_verifier_with_mocks(
            fit_evt=MagicMock(return_value=_make_evt_mock(n_excess=5))
        )
        wr = v._run_evt_window([0.01] * 130)
        assert any("超越样本" in w for w in wr.warnings)

    def test_xi_too_high_warning(self) -> None:
        v = _make_verifier_with_mocks(
            fit_evt=MagicMock(return_value=_make_evt_mock(xi=0.60))
        )
        wr = v._run_evt_window([0.01] * 130)
        assert any("ξ" in w for w in wr.warnings)

    def test_xi_too_low_warning(self) -> None:
        v = _make_verifier_with_mocks(
            fit_evt=MagicMock(return_value=_make_evt_mock(xi=-0.50))
        )
        wr = v._run_evt_window([0.01] * 130)
        assert any("ξ" in w for w in wr.warnings)

    def test_exception_returns_failed_result(self) -> None:
        v = _make_verifier_with_mocks(
            fit_evt=MagicMock(side_effect=ZeroDivisionError("evt boom"))
        )
        wr = v._run_evt_window([0.01] * 130)
        assert wr.converged is False
        assert wr.metrics == {}
        assert "evt boom" in wr.warnings[0]


# ============================================================
# 9. _run_pathsim_window
# ============================================================


class TestRunPathsimWindow:
    """_run_pathsim_window 各分支."""

    def test_success_no_warnings(self) -> None:
        v = _make_verifier_with_mocks()
        wr = v._run_pathsim_window([0.01] * 130)
        assert wr.converged is True
        assert wr.warnings == []
        assert "dd_p99" in wr.metrics
        assert "dd_gradient_ok" in wr.metrics
        assert wr.metrics["dd_gradient_ok"] == 1.0

    def test_dd_p99_too_small_warning(self) -> None:
        v = _make_verifier_with_mocks(
            pathsim_result=_make_pathsim_result(
                dd_p50=0.001, dd_p75=0.002, dd_p90=0.003,
                dd_p95=0.0035, dd_p99=0.004, dd_max=0.005,
            )
        )
        wr = v._run_pathsim_window([0.01] * 130)
        assert any("DD_P99 过小" in w for w in wr.warnings)

    def test_dd_p99_too_large_warning(self) -> None:
        v = _make_verifier_with_mocks(
            pathsim_result=_make_pathsim_result(
                dd_p50=0.50, dd_p75=0.55, dd_p90=0.60,
                dd_p95=0.65, dd_p99=0.70, dd_max=0.80,
            )
        )
        wr = v._run_pathsim_window([0.01] * 130)
        assert any("DD_P99 过大" in w for w in wr.warnings)

    def test_dd_non_monotonic_warning(self) -> None:
        # dd_p99 < dd_p95 → 非单调
        v = _make_verifier_with_mocks(
            pathsim_result=_make_pathsim_result(
                dd_p50=0.05, dd_p75=0.07, dd_p90=0.10,
                dd_p95=0.20, dd_p99=0.15, dd_max=0.25,
            )
        )
        wr = v._run_pathsim_window([0.01] * 130)
        assert any("非单调" in w for w in wr.warnings)
        assert wr.metrics["dd_gradient_ok"] == 0.0

    def test_exception_returns_failed_result(self) -> None:
        v = _make_verifier_with_mocks()
        # 让 PathSimulator 实例的 simulate_portfolio 抛异常
        v._PathSimulator = MagicMock(return_value=MagicMock(
            simulate_portfolio=MagicMock(side_effect=OverflowError("ps boom"))
        ))
        wr = v._run_pathsim_window([0.01] * 130)
        assert wr.converged is False
        assert wr.metrics == {}
        assert "ps boom" in wr.warnings[0]


# ============================================================
# 10. _run_stage_a
# ============================================================


class TestRunStageA:
    """_run_stage_a 全量拟合 + blocked 分支."""

    def test_all_available(self) -> None:
        v = _make_verifier_with_mocks()
        pf = _synthetic_returns(260)
        idx = _synthetic_returns(260, seed=7)
        results = v._run_stage_a(pf, idx, len(pf))
        assert set(results.keys()) == {"garch", "kalman", "evt", "pathsim"}
        for name, mr in results.items():
            assert mr.available is True
            assert mr.blocked is False
            assert mr.full_converged is True

    def test_garch_blocked(self) -> None:
        v = _make_verifier_with_mocks()
        v._garch_ok = False
        pf = _synthetic_returns(260)
        results = v._run_stage_a(pf, None, len(pf))
        assert results["garch"].available is False
        assert results["garch"].blocked is True
        assert "导入失败" in results["garch"].rejection_reason

    def test_kalman_no_idx_blocked(self) -> None:
        v = _make_verifier_with_mocks()
        pf = _synthetic_returns(260)
        results = v._run_stage_a(pf, None, len(pf))
        assert results["kalman"].available is True
        assert results["kalman"].blocked is True
        assert "指数收益率" in results["kalman"].rejection_reason

    def test_kalman_idx_length_mismatch_blocked(self) -> None:
        v = _make_verifier_with_mocks()
        pf = _synthetic_returns(260)
        idx = _synthetic_returns(100, seed=7)  # 长度不一致
        results = v._run_stage_a(pf, idx, len(pf))
        assert results["kalman"].blocked is True
        assert "指数收益率" in results["kalman"].rejection_reason

    def test_kalman_blocked_import(self) -> None:
        v = _make_verifier_with_mocks()
        v._kalman_ok = False
        pf = _synthetic_returns(260)
        results = v._run_stage_a(pf, None, len(pf))
        assert results["kalman"].available is False
        assert results["kalman"].blocked is True
        assert "导入失败" in results["kalman"].rejection_reason

    def test_evt_blocked(self) -> None:
        v = _make_verifier_with_mocks()
        v._evt_ok = False
        pf = _synthetic_returns(260)
        results = v._run_stage_a(pf, None, len(pf))
        assert results["evt"].available is False
        assert results["evt"].blocked is True
        assert "导入失败" in results["evt"].rejection_reason

    def test_pathsim_blocked(self) -> None:
        v = _make_verifier_with_mocks()
        v._pathsim_ok = False
        pf = _synthetic_returns(260)
        results = v._run_stage_a(pf, None, len(pf))
        assert results["pathsim"].available is False
        assert results["pathsim"].blocked is True
        assert "导入失败" in results["pathsim"].rejection_reason

    def test_kalman_full_with_idx(self) -> None:
        v = _make_verifier_with_mocks()
        pf = _synthetic_returns(260)
        idx = _synthetic_returns(260, seed=7)
        results = v._run_stage_a(pf, idx, len(pf))
        assert results["kalman"].blocked is False
        assert "latest_beta" in results["kalman"].full_metrics


# ============================================================
# 11. _run_stage_b
# ============================================================


class TestRunStageB:
    """_run_stage_b 滑动窗口 Walk-Forward."""

    def test_normal_run_populates_windows(self) -> None:
        v = _make_verifier_with_mocks()
        pf = _synthetic_returns(300)
        idx = _synthetic_returns(300, seed=7)
        dates = [f"2024-01-{i:02d}" for i in range(1, 31)] * 10
        windows = v._generate_windows(300)
        full_results = v._run_stage_a(pf, idx, len(pf))
        v._run_stage_b(pf, idx, dates, windows, full_results)
        for name in ["garch", "kalman", "evt", "pathsim"]:
            mr = full_results[name]
            assert mr.n_windows == len(windows)
            assert mr.n_converged_windows == len(windows)
            # 相同 mock 返回 → CV≈0 (浮点噪声), 方向一致性=1.0
            assert mr.cross_window_cv == pytest.approx(0.0, abs=1e-9)
            assert mr.direction_consistency == 1.0
            assert mr.passed is True

    def test_kalman_idx_mismatch_blocked_skipped_in_stage_b(self) -> None:
        # idx 长度 != n → Stage A 标记 kalman blocked → Stage B 跳过 kalman
        v = _make_verifier_with_mocks()
        pf = _synthetic_returns(300)
        idx = _synthetic_returns(150, seed=7)  # 长度不匹配 n=300
        dates = [f"d{i}" for i in range(300)]
        windows = v._generate_windows(300)
        full_results = v._run_stage_a(pf, idx, len(pf))
        assert full_results["kalman"].blocked is True
        v._run_stage_b(pf, idx, dates, windows, full_results)
        # kalman 被 blocked → Stage B 跳过, 不生成窗口
        assert full_results["kalman"].n_windows == 0
        assert full_results["kalman"].windows == []

    def test_blocked_module_skipped(self) -> None:
        v = _make_verifier_with_mocks()
        v._garch_ok = False
        pf = _synthetic_returns(300)
        dates = [f"d{i}" for i in range(300)]
        windows = v._generate_windows(300)
        full_results = v._run_stage_a(pf, None, len(pf))
        # garch 应被 blocked, _run_stage_b 跳过
        v._run_stage_b(pf, None, dates, windows, full_results)
        assert full_results["garch"].n_windows == 0
        assert full_results["garch"].windows == []

    def test_failure_reason_recorded(self) -> None:
        # full_converged=False → 不通过, 记录原因
        v = _make_verifier_with_mocks(
            fit_garch=MagicMock(return_value=_make_garch_mock(converged=False))
        )
        pf = _synthetic_returns(300)
        idx = _synthetic_returns(300, seed=7)
        dates = [f"d{i}" for i in range(300)]
        windows = v._generate_windows(300)
        full_results = v._run_stage_a(pf, idx, len(pf))
        v._run_stage_b(pf, idx, dates, windows, full_results)
        mr = full_results["garch"]
        assert mr.passed is False
        assert "全量未收敛" in mr.rejection_reason


# ============================================================
# 12. _compute_final_verdict
# ============================================================


class TestComputeFinalVerdict:
    """_compute_final_verdict 综合判决."""

    def _make_report(self) -> FinengVerificationReport:
        return FinengVerificationReport(
            run_timestamp="2026-01-01", min_windows=5,
            total_data_days=300, actual_windows=7,
        )

    def test_all_passed_accepted(self) -> None:
        v = _make_verifier_with_mocks()
        report = self._make_report()
        full_results = {
            "garch": ModuleFullResult(module="garch", full_converged=True, passed=True),
            "kalman": ModuleFullResult(module="kalman", full_converged=True, passed=True),
            "evt": ModuleFullResult(module="evt", full_converged=True, passed=True),
            "pathsim": ModuleFullResult(module="pathsim", full_converged=True, passed=True),
        }
        v._compute_final_verdict(report, full_results, [(0, 120), (30, 150)])
        assert report.accepted is True
        assert set(report.passed_modules) == {"garch", "kalman", "evt", "pathsim"}
        assert report.failed_modules == []
        assert "验收通过" in report.acceptance_detail
        assert "Feature Flag" in report.next_step

    def test_too_many_failed_rejected(self) -> None:
        v = _make_verifier_with_mocks()
        report = self._make_report()
        full_results = {
            "garch": ModuleFullResult(module="garch", full_converged=True, passed=True),
            "kalman": ModuleFullResult(module="kalman", full_converged=False, passed=False),
            "evt": ModuleFullResult(module="evt", full_converged=False, passed=False),
            "pathsim": ModuleFullResult(module="pathsim", full_converged=True, passed=True),
        }
        v._compute_final_verdict(report, full_results, [(0, 120)])
        # 2 通过 + 2 失败 → max(2, 4-1=3) = 3 > 2 → 不通过
        assert report.accepted is False
        assert "验收未通过" in report.acceptance_detail
        assert "修复" in report.next_step

    def test_one_failed_still_accepted(self) -> None:
        # 最多允许 1 个失败
        v = _make_verifier_with_mocks()
        report = self._make_report()
        full_results = {
            "garch": ModuleFullResult(module="garch", passed=True),
            "kalman": ModuleFullResult(module="kalman", passed=True),
            "evt": ModuleFullResult(module="evt", passed=True),
            "pathsim": ModuleFullResult(module="pathsim", passed=False),
        }
        v._compute_final_verdict(report, full_results, [(0, 120)])
        # 3 通过 + 1 失败 → max(2, 4-1=3) = 3 <= 3 → 通过
        assert report.accepted is True

    def test_with_blocked_modules(self) -> None:
        v = _make_verifier_with_mocks()
        report = self._make_report()
        full_results = {
            "garch": ModuleFullResult(module="garch", passed=True),
            "kalman": ModuleFullResult(module="kalman", blocked=True),
            "evt": ModuleFullResult(module="evt", passed=True),
            "pathsim": ModuleFullResult(module="pathsim", passed=True),
        }
        v._compute_final_verdict(report, full_results, [(0, 120)])
        assert report.accepted is True
        assert "kalman" in report.blocked_modules
        assert "阻塞" in report.acceptance_detail


# ============================================================
# 13. run_verification 主入口
# ============================================================


class TestRunVerification:
    """run_verification 端到端."""

    def test_data_too_short_returns_early(self) -> None:
        v = _make_verifier_with_mocks()
        pf = _synthetic_returns(100)  # < MIN_DATA_LENGTH (250)
        with patch.object(v, "_save_report") as mock_save:
            report = v.run_verification(pf)
        assert report.accepted is False
        assert "数据不足" in report.acceptance_detail
        assert str(MIN_DATA_LENGTH) in report.next_step
        mock_save.assert_called_once()

    def test_windows_too_few_returns_early(self) -> None:
        # n >= 250 但窗口数 < min_windows (用大 step)
        v = _make_verifier_with_mocks()
        v.window_step = 500  # 极大步进 → 窗口数很少
        pf = _synthetic_returns(260)
        with patch.object(v, "_save_report") as mock_save:
            report = v.run_verification(pf)
        assert report.accepted is False
        assert "有效窗口" in report.acceptance_detail
        mock_save.assert_called_once()

    def test_normal_path_accepted(self) -> None:
        v = _make_verifier_with_mocks()
        pf = _synthetic_returns(300)
        idx = _synthetic_returns(300, seed=7)
        dates = [f"2024-{(i // 30) + 1:02d}-{(i % 30) + 1:02d}" for i in range(300)]
        with patch.object(v, "_save_report") as mock_save:
            report = v.run_verification(pf, idx, dates, portfolio_symbols=["600519"])
        assert report.actual_windows >= 5
        assert report.portfolio_symbols == ["600519"]
        # mock 返回稳定结果 → 全部通过 → accepted
        assert report.accepted is True
        assert len(report.passed_modules) == 4
        mock_save.assert_called_once()

    def test_no_idx_kalman_blocked_in_windows(self) -> None:
        # 不传 idx → kalman 在 Stage A 即 blocked
        v = _make_verifier_with_mocks()
        pf = _synthetic_returns(300)
        with patch.object(v, "_save_report"):
            report = v.run_verification(pf)
        assert "kalman" in report.blocked_modules
        # 其他三模块通过 → accepted (3 通过, 0 失败, 1 blocked)
        assert report.accepted is True

    def test_dates_auto_generated_when_none(self) -> None:
        v = _make_verifier_with_mocks()
        pf = _synthetic_returns(300)
        with patch.object(v, "_save_report"):
            report = v.run_verification(pf)
        # 不崩溃即可, 报告生成成功
        assert report.total_data_days == 300


# ============================================================
# 14. _save_report
# ============================================================


class TestSaveReport:
    """_save_report JSON 落盘."""

    def test_writes_json_files(self, tmp_path) -> None:
        v = _make_verifier_with_mocks()
        report = FinengVerificationReport(
            run_timestamp="2026-01-01T00:00:00", min_windows=5,
            total_data_days=300, actual_windows=7, accepted=True,
            acceptance_detail="测试", next_step="下一步",
        )
        with patch.object(fsv, "REPORT_DIR", tmp_path):
            path_str = v._save_report(report)
        path = Path(path_str)
        assert path.exists()
        assert path.name.startswith("verification_")
        assert path.suffix == ".json"
        # latest 文件
        latest = tmp_path / "verification_latest.json"
        assert latest.exists()
        # 内容可解析
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["accepted"] is True
        assert data["total_data_days"] == 300
        assert data["acceptance_detail"] == "测试"


# ============================================================
# 15. _extract_key_metrics / _flatten_metrics
# ============================================================


class TestExtractKeyMetrics:
    """_extract_key_metrics 关键指标提取."""

    def test_garch_keys(self) -> None:
        windows = [
            ModuleWindowResult(0, "d0", "d9", 10,
                               {"forecast_vol": 0.2, "persistence": 0.95, "ratio_garch_ewma": 1.1}),
            ModuleWindowResult(1, "d10", "d19", 10,
                               {"forecast_vol": 0.21, "persistence": 0.94, "ratio_garch_ewma": 1.2}),
        ]
        result = _extract_key_metrics("garch", windows)
        assert set(result.keys()) == {"forecast_vol", "persistence", "ratio_garch_ewma"}
        assert len(result["forecast_vol"]) == 2

    def test_kalman_keys(self) -> None:
        windows = [
            ModuleWindowResult(0, "d0", "d9", 10,
                               {"latest_beta": 1.0, "mean_beta": 1.0, "var_reduction_pct": 0.5}),
        ]
        result = _extract_key_metrics("kalman", windows)
        assert set(result.keys()) == {"latest_beta", "mean_beta", "var_reduction_pct"}

    def test_evt_keys(self) -> None:
        windows = [
            ModuleWindowResult(0, "d0", "d9", 10,
                               {"xi": 0.1, "es_99": -0.05, "n_excess": 30.0}),
        ]
        result = _extract_key_metrics("evt", windows)
        assert set(result.keys()) == {"xi", "es_99", "n_excess"}

    def test_pathsim_keys(self) -> None:
        windows = [
            ModuleWindowResult(0, "d0", "d9", 10,
                               {"dd_p50": 0.05, "dd_p90": 0.10, "dd_p99": 0.18}),
        ]
        result = _extract_key_metrics("pathsim", windows)
        assert set(result.keys()) == {"dd_p50", "dd_p90", "dd_p99"}

    def test_unknown_mod_returns_empty(self) -> None:
        windows = [ModuleWindowResult(0, "d0", "d9", 10, {"x": 1.0})]
        result = _extract_key_metrics("unknown", windows)
        assert result == {}

    def test_missing_keys_partial(self) -> None:
        windows = [
            ModuleWindowResult(0, "d0", "d9", 10, {"forecast_vol": 0.2}),  # 缺 persistence
        ]
        result = _extract_key_metrics("garch", windows)
        assert "forecast_vol" in result
        assert "persistence" not in result


class TestFlattenMetrics:
    """_flatten_metrics 扁平化."""

    def test_normal_numeric(self) -> None:
        wr = ModuleWindowResult(0, "d0", "d9", 10, {"a": 1.0, "b": 2.5, "c": 3})
        result = _flatten_metrics(wr)
        assert result == {"a": 1.0, "b": 2.5, "c": 3.0}

    def test_skips_nan(self) -> None:
        wr = ModuleWindowResult(0, "d0", "d9", 10, {"a": 1.0, "b": float("nan")})
        result = _flatten_metrics(wr)
        assert "a" in result
        assert "b" not in result

    def test_skips_non_numeric(self) -> None:
        wr = ModuleWindowResult(0, "d0", "d9", 10, {"a": 1.0, "b": "text", "c": None})
        result = _flatten_metrics(wr)
        assert "a" in result
        assert "b" not in result
        assert "c" not in result

    def test_empty_metrics(self) -> None:
        wr = ModuleWindowResult(0, "d0", "d9", 10, {})
        assert _flatten_metrics(wr) == {}


# ============================================================
# 16. _serialize
# ============================================================


class TestSerialize:
    """_serialize 报告序列化."""

    def test_empty_report(self) -> None:
        report = FinengVerificationReport(
            run_timestamp="2026-01-01", min_windows=5,
            total_data_days=300, actual_windows=7,
        )
        data = _serialize(report)
        assert data["run_timestamp"] == "2026-01-01"
        assert data["min_windows"] == 5
        assert data["total_data_days"] == 300
        assert data["modules"] == {}
        assert data["accepted"] is False

    def test_with_modules_and_windows(self) -> None:
        report = FinengVerificationReport(
            run_timestamp="2026-01-01", min_windows=5,
            total_data_days=300, actual_windows=7,
            portfolio_symbols=["600519"],
        )
        mr = ModuleFullResult(
            module="garch", full_converged=True,
            full_metrics={"persistence": 0.95},
            full_warnings=["w1"],
            windows=[ModuleWindowResult(0, "d0", "d9", 10, {"forecast_vol": 0.2})],
            n_windows=1, n_converged_windows=1,
            cross_window_cv=0.1, direction_consistency=0.9,
            passed=True,
        )
        report.modules = {"garch": mr}
        report.passed_modules = ["garch"]
        report.accepted = True
        report.acceptance_detail = "ok"
        report.next_step = "go"
        data = _serialize(report)
        assert data["modules"]["garch"]["module"] == "garch"
        assert data["modules"]["garch"]["full_converged"] is True
        assert data["modules"]["garch"]["n_windows"] == 1
        assert data["modules"]["garch"]["windows"][0]["window_idx"] == 0
        # metrics 数值被格式化为 6 位小数字符串
        assert data["modules"]["garch"]["windows"][0]["metrics"]["forecast_vol"] == 0.2
        assert data["passed_modules"] == ["garch"]
        assert data["portfolio_symbols"] == ["600519"]

    def test_serialize_non_numeric_metric_preserved(self) -> None:
        """_serialize 中非数值 metric 原样保留."""
        report = FinengVerificationReport(
            run_timestamp="ts", min_windows=5, total_data_days=10, actual_windows=1,
        )
        mr = ModuleFullResult(
            module="evt",
            windows=[ModuleWindowResult(0, "d0", "d9", 10, {"label": "text"})],
        )
        report.modules = {"evt": mr}
        data = _serialize(report)
        assert data["modules"]["evt"]["windows"][0]["metrics"]["label"] == "text"


# ============================================================
# 17. run_fineng_shadow_verification 便捷函数
# ============================================================


class TestConvenienceFunction:
    """run_fineng_shadow_verification 一站式入口."""

    def test_data_too_short(self) -> None:
        pf = _synthetic_returns(100)
        with patch.object(fsv.FinengShadowVerifier, "_save_report"):
            report = run_fineng_shadow_verification(pf)
        assert report.accepted is False
        assert "数据不足" in report.acceptance_detail

    def test_with_min_windows_param(self) -> None:
        pf = _synthetic_returns(100)
        with patch.object(fsv.FinengShadowVerifier, "_save_report"):
            report = run_fineng_shadow_verification(pf, min_windows=10)
        # 数据不足仍优先返回
        assert report.accepted is False
