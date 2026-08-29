"""G7 覆盖率冲刺 — utils/backtest/honest_validation.py 单元测试.

目标: 覆盖率从 32.00% → ≥70%

测试范围:
    1. CPCVSummary / HonestValidationResult dataclass
    2. run_honest_validation: n<20 / 正常 / is_honest 判定各分支
    3. _compute_cpcv_sharpe_paths: ImportError / 样本不足 / 正常 (mock)
    4. _run_noise_test: ImportError / 正常 (mock)

运行:
    python -m pytest tests/unit/test_g7_backtest_honest_validation_boost.py -v
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import numpy as np

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils.backtest.honest_validation import (  # noqa: E402
    CPCVSummary,
    HonestValidationResult,
    _compute_cpcv_sharpe_paths,
    _run_noise_test,
    run_honest_validation,
)

# ============================================================
# 1. dataclass
# ============================================================


class TestDataclasses:
    def test_cpcv_summary_defaults(self) -> None:
        s = CPCVSummary()
        assert s.n_paths == 0
        assert s.is_stable is False
        assert s.path_sharpes == []

    def test_honest_validation_result_defaults(self) -> None:
        r = HonestValidationResult()
        assert r.cpcv.n_paths == 0
        assert r.dsr is None
        assert r.noise is None
        assert r.is_honest is False
        assert r.verdict == ""


# ============================================================
# 2. run_honest_validation
# ============================================================


class TestRunHonestValidation:
    def test_insufficient_samples(self) -> None:
        # n < 20 → "样本不足"
        result = run_honest_validation([0.01, 0.02, -0.01])
        assert result.n_observations == 3
        assert "样本不足" in result.verdict
        assert result.is_honest is False

    def test_normal_returns_runs_full_pipeline(self) -> None:
        # 生成 100 天日收益 (ms_strategy 可能不可用 → CPCV/Noise 走 ImportError 分支)
        rng = np.random.default_rng(42)
        returns = rng.normal(0.001, 0.02, 100).tolist()
        result = run_honest_validation(returns)
        assert result.n_observations == 100
        assert result.dsr is not None
        # verdict 非空
        assert result.verdict != ""

    def test_zero_std_returns(self) -> None:
        # 全零收益 → std=0 → original_sharpe=0
        result = run_honest_validation([0.0] * 50)
        assert result.original_sharpe == 0.0

    def test_is_honest_true_branch(self) -> None:
        # mock DSR/Noise/CPCV 全通过 → is_honest=True
        returns = np.random.default_rng(42).normal(0.001, 0.02, 100).tolist()
        with (
            patch(
                "utils.backtest.honest_validation._compute_cpcv_sharpe_paths"
            ) as mock_cpcv,
            patch("utils.backtest.honest_validation._run_noise_test") as mock_noise,
            patch("utils.backtest.honest_validation.deflated_sharpe_ratio") as mock_dsr,
        ):
            mock_cpcv.return_value = CPCVSummary(
                n_paths=6,
                sharpe_mean=1.5,
                sharpe_std=0.3,
                sharpe_cv=0.2,
                pct_positive=0.9,
                is_stable=True,
                path_sharpes=[1.5] * 6,
            )
            mock_dsr_result = MagicMock()
            mock_dsr_result.is_pass = True
            mock_dsr_result.deflated_sharpe_ratio = 0.96
            mock_dsr.return_value = mock_dsr_result
            mock_noise_result = MagicMock()
            mock_noise_result.is_stable = True
            mock_noise.return_value = mock_noise_result

            result = run_honest_validation(returns)
            assert result.is_honest is True
            assert "HONEST" in result.verdict

    def test_is_honest_false_dsr_fail(self) -> None:
        returns = np.random.default_rng(42).normal(0.001, 0.02, 100).tolist()
        with (
            patch(
                "utils.backtest.honest_validation._compute_cpcv_sharpe_paths"
            ) as mock_cpcv,
            patch("utils.backtest.honest_validation._run_noise_test") as mock_noise,
            patch("utils.backtest.honest_validation.deflated_sharpe_ratio") as mock_dsr,
        ):
            mock_cpcv.return_value = CPCVSummary(
                is_stable=True, n_paths=6, sharpe_cv=0.2
            )
            mock_dsr_result = MagicMock()
            mock_dsr_result.is_pass = False  # DSR 失败
            mock_dsr_result.deflated_sharpe_ratio = 0.5
            mock_dsr.return_value = mock_dsr_result
            mock_noise_result = MagicMock()
            mock_noise_result.is_stable = True
            mock_noise.return_value = mock_noise_result

            result = run_honest_validation(returns)
            assert result.is_honest is False
            assert "DSR FAIL" in result.verdict

    def test_is_honest_false_noise_unstable(self) -> None:
        returns = np.random.default_rng(42).normal(0.001, 0.02, 100).tolist()
        with (
            patch(
                "utils.backtest.honest_validation._compute_cpcv_sharpe_paths"
            ) as mock_cpcv,
            patch("utils.backtest.honest_validation._run_noise_test") as mock_noise,
            patch("utils.backtest.honest_validation.deflated_sharpe_ratio") as mock_dsr,
        ):
            mock_cpcv.return_value = CPCVSummary(
                is_stable=True, n_paths=6, sharpe_cv=0.2
            )
            mock_dsr_result = MagicMock()
            mock_dsr_result.is_pass = True
            mock_dsr_result.deflated_sharpe_ratio = 0.96
            mock_dsr.return_value = mock_dsr_result
            mock_noise_result = MagicMock()
            mock_noise_result.is_stable = False  # Noise 不稳定
            mock_noise.return_value = mock_noise_result

            result = run_honest_validation(returns)
            assert result.is_honest is False
            assert "Noise unstable" in result.verdict

    def test_is_honest_false_cpcv_unstable(self) -> None:
        returns = np.random.default_rng(42).normal(0.001, 0.02, 100).tolist()
        with (
            patch(
                "utils.backtest.honest_validation._compute_cpcv_sharpe_paths"
            ) as mock_cpcv,
            patch("utils.backtest.honest_validation._run_noise_test") as mock_noise,
            patch("utils.backtest.honest_validation.deflated_sharpe_ratio") as mock_dsr,
        ):
            mock_cpcv.return_value = CPCVSummary(
                is_stable=False, n_paths=6, sharpe_cv=0.8
            )
            mock_dsr_result = MagicMock()
            mock_dsr_result.is_pass = True
            mock_dsr_result.deflated_sharpe_ratio = 0.96
            mock_dsr.return_value = mock_dsr_result
            mock_noise_result = MagicMock()
            mock_noise_result.is_stable = True
            mock_noise.return_value = mock_noise_result

            result = run_honest_validation(returns)
            assert result.is_honest is False
            assert "CPCV unstable" in result.verdict

    def test_numpy_array_input(self) -> None:
        # 支持 np.ndarray 输入
        returns = np.random.default_rng(42).normal(0.001, 0.02, 100)
        result = run_honest_validation(returns)
        assert result.n_observations == 100


# ============================================================
# 3. _compute_cpcv_sharpe_paths
# ============================================================


class TestComputeCpcvSharpePaths:
    def test_import_error_returns_empty(self) -> None:
        # ms_strategy 不可用 → ImportError → 空 CPCVSummary
        returns = np.random.default_rng(42).normal(0.001, 0.02, 100)
        # 强制 ImportError
        with patch.dict(
            sys.modules, {"ms_strategy.src.backtest.combinatorial_purged_cv": None}
        ):
            result = _compute_cpcv_sharpe_paths(returns)
            assert result.n_paths == 0

    def test_insufficient_samples(self) -> None:
        # n < n_groups * 5 → 空
        returns = np.array([0.01] * 10)
        result = _compute_cpcv_sharpe_paths(returns, n_groups=6)
        assert result.n_paths == 0

    def test_normal_with_mocked_cpcv(self) -> None:
        # mock CPCV 模块, 模拟正常路径
        returns = np.random.default_rng(42).normal(0.001, 0.02, 100)

        # 构造 mock CPCV 模块
        mock_module = MagicMock()
        mock_config = MagicMock()
        mock_cv_instance = MagicMock()
        # 模拟 split 返回 6 个路径, 每个路径 test_idx 含 20 个样本
        mock_split = MagicMock()
        mock_split.test_idx = list(range(20))
        mock_cv_instance.split.return_value = [mock_split] * 6
        mock_module.CPCVConfig.return_value = mock_config
        mock_module.CombinatorialPurgedCV.return_value = mock_cv_instance

        with patch.dict(
            sys.modules,
            {
                "ms_strategy.src.backtest.combinatorial_purged_cv": mock_module,
            },
        ):
            result = _compute_cpcv_sharpe_paths(returns, n_groups=6, n_test_groups=2)
            assert result.n_paths == 6
            assert len(result.path_sharpes) == 6


# ============================================================
# 4. _run_noise_test
# ============================================================


class TestRunNoiseTest:
    def test_import_error_returns_none(self) -> None:
        returns = np.random.default_rng(42).normal(0.001, 0.02, 100)
        with patch.dict(
            sys.modules, {"ms_strategy.src.backtest.noise_injection_test": None}
        ):
            result = _run_noise_test(returns)
            assert result is None

    def test_normal_with_mocked_module(self) -> None:
        returns = np.random.default_rng(42).normal(0.001, 0.02, 100)
        mock_module = MagicMock()
        mock_result = MagicMock()
        mock_result.is_stable = True
        mock_module.run_noise_injection_test.return_value = mock_result

        with patch.dict(
            sys.modules,
            {
                "ms_strategy.src.backtest.noise_injection_test": mock_module,
            },
        ):
            result = _run_noise_test(returns, noise_ratio=0.1, n_trials=100)
            assert result is mock_result
            mock_module.run_noise_injection_test.assert_called_once()
