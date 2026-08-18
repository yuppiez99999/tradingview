"""test_s5_validation_unit.py — S5 组合层面检验单元测试

覆盖要点:
    - _zscore (正常/零标准差/空数组)
    - _top_bottom_ls (正常/top_pct/样本不足)
    - _calc_annualized_sharpe (正常/样本不足/零标准差)
    - run_s5_validation (mock _build_universe + _compute_factors_at_entry, PASS/FAIL 场景)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from utils.alpha_factor.s5_validation import (
    _calc_annualized_sharpe,
    _top_bottom_ls,
    _zscore,
    run_s5_validation,
)


# ============================================================
# _zscore
# ============================================================


class TestZscore:
    @pytest.mark.unit
    def test_normal(self):
        values = [1, 2, 3, 4, 5]
        result = _zscore(values)
        assert result.shape == (5,)
        np.testing.assert_allclose(result.mean(), 0, atol=1e-10)
        np.testing.assert_allclose(result.std(), 1, atol=1e-10)

    @pytest.mark.unit
    def test_zero_std(self):
        """所有值相同 → 标准差=0 → 返回全零"""
        values = [5, 5, 5, 5]
        result = _zscore(values)
        np.testing.assert_allclose(result, 0)

    @pytest.mark.unit
    def test_single_element(self):
        result = _zscore([42])
        np.testing.assert_allclose(result, 0)

    @pytest.mark.unit
    def test_negative_values(self):
        values = [-10, -5, 0, 5, 10]
        result = _zscore(values)
        assert result.shape == (5,)


# ============================================================
# _top_bottom_ls
# ============================================================


class TestTopBottomLs:
    @pytest.mark.unit
    def test_basic(self):
        factor = [1, 2, 3, 4, 5]
        returns = [0.01, 0.02, 0.03, 0.04, 0.05]
        ls = _top_bottom_ls(factor, returns, top_pct=0.4)
        # top 40% = 2 个: factor 5,4 → ret 0.05,0.04 → mean 0.045
        # bottom 40% = 2 个: factor 1,2 → ret 0.01,0.02 → mean 0.015
        assert ls == pytest.approx(0.03)

    @pytest.mark.unit
    def test_top_pct_default(self):
        factor = list(range(1, 11))
        returns = [0.01 * i for i in range(1, 11)]
        ls = _top_bottom_ls(factor, returns)
        # top 20% = 2 个: factor 10,9 → ret 0.10,0.09 → mean 0.095
        # bottom 20% = 2 个: factor 1,2 → ret 0.01,0.02 → mean 0.015
        assert ls == pytest.approx(0.08)

    @pytest.mark.unit
    def test_min_top_n(self):
        """top_n 最少 2"""
        factor = [1, 2, 3]
        returns = [0.01, 0.02, 0.03]
        ls = _top_bottom_ls(factor, returns, top_pct=0.1)  # 0.3 → int=0 → max(2,0)=2
        # top 2: factor 3,2 → ret 0.03,0.02 → 0.025
        # bottom 2: factor 1,2 → ret 0.01,0.02 → 0.015
        assert ls == pytest.approx(0.01)

    @pytest.mark.unit
    def test_negative_returns(self):
        factor = [1, 2, 3, 4]
        returns = [-0.04, -0.03, -0.02, -0.01]
        ls = _top_bottom_ls(factor, returns, top_pct=0.5)
        # top 2: factor 4,3 → ret -0.01,-0.02 → -0.015
        # bottom 2: factor 1,2 → ret -0.04,-0.03 → -0.035
        assert ls == pytest.approx(0.02)


# ============================================================
# _calc_annualized_sharpe
# ============================================================


class TestCalcAnnualizedSharpe:
    @pytest.mark.unit
    def test_normal(self):
        returns = [0.01, 0.02, -0.01, 0.03, -0.02]
        sharpe = _calc_annualized_sharpe(returns, horizon=20)
        assert isinstance(sharpe, float)

    @pytest.mark.unit
    def test_insufficient_samples(self):
        """< 3 样本 → 0"""
        assert _calc_annualized_sharpe([0.01, 0.02], horizon=20) == 0.0

    @pytest.mark.unit
    def test_zero_std(self):
        """所有收益相同 → std=0 → 0"""
        assert _calc_annualized_sharpe([0.01, 0.01, 0.01], horizon=20) == 0.0

    @pytest.mark.unit
    def test_positive_sharpe(self):
        """正收益 → 正夏普"""
        returns = [0.01, 0.02, 0.015, 0.018, 0.012]
        sharpe = _calc_annualized_sharpe(returns, horizon=20)
        assert sharpe > 0

    @pytest.mark.unit
    def test_negative_sharpe(self):
        """负收益 → 负夏普"""
        returns = [-0.01, -0.02, -0.015, -0.018, -0.012]
        sharpe = _calc_annualized_sharpe(returns, horizon=20)
        assert sharpe < 0


# ============================================================
# run_s5_validation (mock 依赖)
# ============================================================


class TestRunS5Validation:
    @pytest.mark.unit
    def test_insufficient_stocks(self):
        """有效股票不足 10 → FAIL"""
        with patch("utils.alpha_factor.s5_validation._build_universe") as mock_build:
            # 返回少量价格数据, 不足 10 只
            mock_build.return_value = (
                {"A": {"closes": [1] * 100}},  # price_data
                ["A"],  # symbols
                MagicMock(),  # graph
                {},  # industries
            )
            result = run_s5_validation(days=100, horizon=20, windows=2)
        assert result["verdict"] == "FAIL"
        assert "不足" in result["reason"]

    @pytest.mark.unit
    def test_result_structure(self):
        """mock 足够数据, 验证返回结构"""
        # 构造足够数据
        n_stocks = 20
        price_data = {f"S{i}": {"closes": [10 + i * 0.1] * 200} for i in range(n_stocks)}

        with patch("utils.alpha_factor.s5_validation._build_universe") as mock_build, \
             patch("utils.alpha_factor.s5_validation._compute_factors_at_entry") as mock_factors:
            mock_build.return_value = (
                price_data,
                [f"S{i}" for i in range(n_stocks)],
                MagicMock(),
                {},
            )
            # mock 因子计算
            mock_factors.return_value = {
                f"S{i}": {"mom_60d": float(i), "chain_mom_60d": 0.0}
                for i in range(n_stocks)
            }
            result = run_s5_validation(days=200, horizon=20, windows=2)

        assert "verdict" in result
        assert "benchmark_sharpe" in result
        assert "enhanced_sharpe" in result
        assert "marginal_improvement" in result
        assert "threshold" in result
        assert "n_windows" in result
        assert "window_details" in result