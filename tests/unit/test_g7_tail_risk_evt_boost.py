#!/usr/bin/env python
"""
test_g7_tail_risk_evt_boost.py — POT-GPD 尾部风险估计覆盖率补强测试

覆盖 P0 risk 链路: utils/fineng/tail_risk_evt.py
"""
from __future__ import annotations

from utils.fineng.tail_risk_evt import (
    EVTResult,
    _gpd_loglik,
    evt_var_es,
    fit_evt,
)


class TestGpdLoglik:
    def test_sigma_zero_returns_inf(self) -> None:
        assert _gpd_loglik([0.01, 0.02], 0.0, 0.1) == -float("inf")

    def test_sigma_negative_returns_inf(self) -> None:
        assert _gpd_loglik([0.01, 0.02], -1.0, 0.1) == -float("inf")

    def test_valid_inputs(self) -> None:
        result = _gpd_loglik([0.01, 0.02, 0.03], 0.02, 0.1)
        assert isinstance(result, float)

    def test_invalid_arg_returns_inf(self) -> None:
        assert _gpd_loglik([1.0], 0.01, -10.0) == -float("inf")


class TestFitEvt:
    def test_insufficient_samples(self) -> None:
        result = fit_evt([0.01] * 50, min_history=120)
        assert result.converged is False

    def test_sufficient_samples(self) -> None:
        returns = [-0.01 * (i % 10) for i in range(150)]
        result = fit_evt(returns, min_history=120)
        assert isinstance(result, EVTResult)
        assert result.n_total == 150

    def test_threshold_percentile(self) -> None:
        returns = [-0.01 * (i % 10) for i in range(150)]
        result = fit_evt(returns, threshold_percentile=0.975, min_history=120)
        assert result.threshold_percentile == 0.975

    def test_result_fields(self) -> None:
        returns = [-0.02 * (i % 8) for i in range(150)]
        result = fit_evt(returns, min_history=120)
        assert hasattr(result, "var_99")
        assert hasattr(result, "es_99")
        assert hasattr(result, "xi")
        assert hasattr(result, "sigma")
        assert hasattr(result, "converged")


class TestEvtVarEs:
    def test_returns_dict(self) -> None:
        returns = [-0.01 * (i % 10) for i in range(150)]
        result = evt_var_es(returns, confidence=0.99, threshold_percentile=0.95)
        assert isinstance(result, dict)

    def test_insufficient_samples(self) -> None:
        result = evt_var_es([0.01] * 50, confidence=0.99, threshold_percentile=0.95)
        assert isinstance(result, dict)
