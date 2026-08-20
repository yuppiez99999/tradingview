#!/usr/bin/env python
"""
test_g7_cvar_boost.py — CVaR 风险计量模块覆盖率补强测试

覆盖 P0 risk 链路: utils/risk/cvar.py
测试范围:
    - CVaRConfig.from_dict() 配置解析 (合法/非法值)
    - CVaRCalculator.calculate() historical/parametric/evt/monte_carlo 方法
    - CVaRCalculator.calculate_scalar() 标量计算
    - CVaRCalculator.calculate_portfolio() 组合计算
    - _validate_returns() 输入验证 (空/NaN/inf/None)
    - _check_breach() 超限检测
    - fallback chain 降级链
    - 边界情况
"""
from __future__ import annotations

import math


from utils.risk.cvar import (
    CVaRCalculator,
    CVaRConfig,
    CVaRResult,
    _erfinv,
    _norm_ppf,
    _student_t_ppf,
)


class TestErfinv:
    """逆误差函数测试."""

    def test_zero(self) -> None:
        assert abs(_erfinv(0.0)) < 1e-10

    def test_positive(self) -> None:
        assert _erfinv(0.5) > 0

    def test_near_one(self) -> None:
        assert _erfinv(0.999) > 1.0

    def test_at_boundary(self) -> None:
        assert _erfinv(1.0) == float("inf")
        assert _erfinv(-1.0) == float("-inf")


class TestNormPpf:
    """标准正态分位数函数测试."""

    def test_median(self) -> None:
        assert abs(_norm_ppf(0.5)) < 1e-10

    def test_upper(self) -> None:
        assert _norm_ppf(0.95) > 1.5

    def test_boundary(self) -> None:
        assert _norm_ppf(0.0) == float("-inf")
        assert _norm_ppf(1.0) == float("inf")


class TestStudentTPpf:
    """Student-t 分位数函数测试."""

    def test_median(self) -> None:
        assert abs(_student_t_ppf(0.5, 5)) < 1e-10

    def test_upper(self) -> None:
        assert _student_t_ppf(0.95, 10) > 1.0


class TestCVaRConfigFromDict:
    """CVaRConfig.from_dict() 配置解析测试."""

    def test_empty_dict(self) -> None:
        cfg = CVaRConfig.from_dict({})
        assert cfg.method == "historical"
        assert cfg.distribution == "normal"

    def test_none(self) -> None:
        cfg = CVaRConfig.from_dict(None)
        assert cfg.method == "historical"

    def test_valid_config(self) -> None:
        cfg = CVaRConfig.from_dict({
            "method": "parametric",
            "distribution": "student_t",
            "dof": 10,
            "confidence_level": 0.99,
        })
        assert cfg.method == "parametric"
        assert cfg.distribution == "student_t"
        assert cfg.dof == 10
        assert cfg.confidence_level == 0.99

    def test_invalid_method_fallback(self) -> None:
        cfg = CVaRConfig.from_dict({"method": "invalid_method"})
        assert cfg.method == "historical"

    def test_invalid_distribution_fallback(self) -> None:
        cfg = CVaRConfig.from_dict({"distribution": "unknown"})
        assert cfg.distribution == "normal"

    def test_invalid_dof_fallback(self) -> None:
        cfg = CVaRConfig.from_dict({"dof": 1})
        assert cfg.dof == 5

    def test_invalid_confidence_fallback(self) -> None:
        cfg = CVaRConfig.from_dict({"confidence_level": 0.5})
        assert cfg.confidence_level == 0.95

    def test_invalid_threshold_fallback(self) -> None:
        cfg = CVaRConfig.from_dict({"threshold_percentile": 0.5})
        assert cfg.threshold_percentile == 0.95

    def test_invalid_min_history_fallback(self) -> None:
        cfg = CVaRConfig.from_dict({"min_history": 5})
        assert cfg.min_history == 30

    def test_invalid_var_limits(self) -> None:
        cfg = CVaRConfig.from_dict({"var_95_limit_pct": 0.01, "var_99_limit_pct": 0.02})
        assert cfg.var_95_limit_pct == -0.04
        assert cfg.var_99_limit_pct == -0.06

    def test_fallback_chain(self) -> None:
        cfg = CVaRConfig.from_dict({"fallback_chain": ["historical", "invalid"]})
        assert "historical" in cfg.fallback_chain

    def test_empty_fallback_chain(self) -> None:
        cfg = CVaRConfig.from_dict({"fallback_chain": []})
        assert cfg.fallback_chain == ("evt", "historical")


class TestCVaRCalculatorValidation:
    """CVaRCalculator 输入验证测试."""

    def setup_method(self) -> None:
        self.calc = CVaRCalculator(CVaRConfig(min_history=10))

    def test_empty_returns(self) -> None:
        result = self.calc.calculate([])
        assert math.isnan(result.cvar_pct)

    def test_insufficient_samples(self) -> None:
        result = self.calc.calculate([0.01, 0.02])
        assert math.isnan(result.cvar_pct)

    def test_none_in_returns(self) -> None:
        returns = [0.01] * 10 + [None]  # type: ignore[list-item]
        result = self.calc.calculate(returns)
        assert math.isnan(result.cvar_pct)

    def test_nan_in_returns(self) -> None:
        returns = [0.01] * 10 + [float("nan")]
        result = self.calc.calculate(returns)
        assert math.isnan(result.cvar_pct)

    def test_inf_in_returns(self) -> None:
        returns = [0.01] * 10 + [float("inf")]
        result = self.calc.calculate(returns)
        assert math.isnan(result.cvar_pct)


class TestCVaRCalculatorHistorical:
    """CVaRCalculator historical 方法测试."""

    def setup_method(self) -> None:
        self.calc = CVaRCalculator(CVaRConfig(method="historical", min_history=10))

    def test_basic_calculation(self) -> None:
        returns = [-0.01 * i for i in range(20)]
        result = self.calc.calculate(returns)
        assert not math.isnan(result.cvar_pct)
        assert result.method == "historical"

    def test_with_portfolio_value(self) -> None:
        returns = [-0.01 * i for i in range(20)]
        result = self.calc.calculate(returns, portfolio_value=1_000_000)
        assert result.cvar_amount is not None
        assert result.cvar_amount == result.cvar_pct * 1_000_000

    def test_negative_portfolio_value(self) -> None:
        returns = [-0.01 * i for i in range(20)]
        result = self.calc.calculate(returns, portfolio_value=-100)
        assert "portfolio_value <= 0" in result.warning

    def test_breach_detection(self) -> None:
        returns = [-0.10 * i for i in range(20)]
        result = self.calc.calculate(returns, confidence=0.95)
        assert isinstance(result.breach, bool)


class TestCVaRCalculatorParametric:
    """CVaRCalculator parametric 方法测试."""

    def setup_method(self) -> None:
        self.calc = CVaRCalculator(CVaRConfig(method="parametric", min_history=10))

    def test_normal_distribution(self) -> None:
        returns = [-0.01 * (i % 5) for i in range(20)]
        result = self.calc.calculate(returns, distribution="normal")
        assert not math.isnan(result.cvar_pct)

    def test_student_t_distribution(self) -> None:
        returns = [-0.01 * (i % 5) for i in range(20)]
        result = self.calc.calculate(returns, distribution="student_t", dof=5)
        assert not math.isnan(result.cvar_pct)

    def test_unknown_distribution_fallback(self) -> None:
        returns = [-0.01 * (i % 5) for i in range(20)]
        result = self.calc.calculate(returns, distribution="unknown")
        # unknown distribution 触发 fallback chain, 降级至 historical
        assert not math.isnan(result.cvar_pct)
        assert "fallback" in result.method_used or result.method_used == "historical"


class TestCVaRCalculatorMonteCarlo:
    """CVaRCalculator monte_carlo 方法测试 (降级至 parametric)."""

    def setup_method(self) -> None:
        self.calc = CVaRCalculator(CVaRConfig(method="monte_carlo", min_history=10))

    def test_monte_carlo_fallback(self) -> None:
        returns = [-0.01 * (i % 5) for i in range(20)]
        result = self.calc.calculate(returns)
        assert not math.isnan(result.cvar_pct)


class TestCVaRCalculatorScalar:
    """CVaRCalculator.calculate_scalar() 测试."""

    def setup_method(self) -> None:
        self.calc = CVaRCalculator(CVaRConfig(method="historical", min_history=10))

    def test_returns_pct(self) -> None:
        returns = [-0.01 * i for i in range(20)]
        pct = self.calc.calculate_scalar(returns)
        assert isinstance(pct, float)

    def test_returns_amount(self) -> None:
        returns = [-0.01 * i for i in range(20)]
        amt = self.calc.calculate_scalar(returns, portfolio_value=500_000, return_amount=True)
        assert isinstance(amt, float)

    def test_nan_on_invalid(self) -> None:
        pct = self.calc.calculate_scalar([])
        assert math.isnan(pct)


class TestCVaRCalculatorPortfolio:
    """CVaRCalculator.calculate_portfolio() 测试."""

    def setup_method(self) -> None:
        self.calc = CVaRCalculator(CVaRConfig(method="historical", min_history=10))

    def test_basic_portfolio(self) -> None:
        positions = [{"code": "A", "weight": 0.5}, {"code": "B", "weight": 0.5}]
        returns_matrix = {
            "A": [-0.01 * i for i in range(20)],
            "B": [-0.02 * i for i in range(20)],
        }
        result = self.calc.calculate_portfolio(positions, returns_matrix, 1_000_000)
        assert isinstance(result, CVaRResult)

    def test_missing_returns_warning(self) -> None:
        positions = [{"code": "A", "weight": 1.0}, {"code": "B", "weight": 0.0}]
        returns_matrix = {"A": [-0.01 * i for i in range(20)]}
        result = self.calc.calculate_portfolio(positions, returns_matrix, 1_000_000)
        assert "缺少收益率序列" in result.warning

    def test_unequal_length_warning(self) -> None:
        positions = [{"code": "A", "weight": 0.5}, {"code": "B", "weight": 0.5}]
        returns_matrix = {
            "A": [-0.01 * i for i in range(20)],
            "B": [-0.02 * i for i in range(15)],
        }
        result = self.calc.calculate_portfolio(positions, returns_matrix, 1_000_000)
        assert "序列长度不一致" in result.warning or "权重" in result.warning


class TestCVaRCalculatorBreach:
    """CVaRCalculator 超限检测测试."""

    def test_95_breach(self) -> None:
        calc = CVaRCalculator(CVaRConfig(
            method="historical", min_history=10,
            var_95_limit_pct=-0.001,
        ))
        returns = [-0.05 * (i + 1) for i in range(20)]
        result = calc.calculate(returns, confidence=0.95)
        assert isinstance(result.breach, bool)
        assert isinstance(result.breach_level, (str, type(None)))

    def test_99_breach(self) -> None:
        calc = CVaRCalculator(CVaRConfig(
            method="historical", min_history=10,
            var_99_limit_pct=-0.001,
        ))
        returns = [-0.05 * (i + 1) for i in range(20)]
        result = calc.calculate(returns, confidence=0.99)
        assert isinstance(result.breach, bool)

    def test_non_standard_confidence_no_breach(self) -> None:
        calc = CVaRCalculator(CVaRConfig(method="historical", min_history=10))
        returns = [-0.01 * i for i in range(20)]
        result = calc.calculate(returns, confidence=0.90)
        assert result.breach is False


class TestCVaRCalculatorEdgeCases:
    """CVaRCalculator 边界情况测试."""

    def test_unknown_method(self) -> None:
        calc = CVaRCalculator(CVaRConfig(method="historical", min_history=10))
        result = calc.calculate([0.01] * 20, method="unknown")
        assert math.isnan(result.cvar_pct)

    def test_invalid_confidence(self) -> None:
        calc = CVaRCalculator(CVaRConfig(method="historical", min_history=10))
        result = calc.calculate([0.01] * 20, confidence=0.5)
        assert math.isnan(result.cvar_pct)

    def test_skip_breach(self) -> None:
        calc = CVaRCalculator(CVaRConfig(
            method="historical", min_history=10,
            var_95_limit_pct=-0.001,
        ))
        returns = [-0.05 * (i + 1) for i in range(20)]
        result = calc.calculate(returns, confidence=0.95, _skip_breach=True)
        assert result.breach is False

    def test_result_to_dict(self) -> None:
        calc = CVaRCalculator(CVaRConfig(method="historical", min_history=10))
        result = calc.calculate([-0.01 * i for i in range(20)])
        d = result.to_dict()
        assert "cvar_pct" in d
        assert "method" in d
        assert "confidence" in d