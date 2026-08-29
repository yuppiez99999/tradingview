"""test_risk_constraints_unit.py — 硬性风险约束执行器单元测试

覆盖要点:
    - enforce_hard_constraints (单标的截断/非负/板块压缩/归一化/循环收敛/无sector)
    - validate_risk_budget (集中度/板块/VaR/无价格数据)
    - _approx_var (有价格/无价格/短历史)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from utils.risk_constraints import (
    _approx_var,
    enforce_hard_constraints,
    validate_risk_budget,
)

# ============================================================
# enforce_hard_constraints
# ============================================================


class TestEnforceHardConstraints:
    @pytest.mark.unit
    def test_no_violation(self):
        weights = {"A": 0.08, "B": 0.07, "C": 0.05}
        clamped, violations = enforce_hard_constraints(weights)
        assert violations == []
        assert abs(sum(clamped.values()) - 0.20) < 1e-9

    @pytest.mark.unit
    def test_single_weight_cap(self):
        weights = {"A": 0.20, "B": 0.05}
        clamped, violations = enforce_hard_constraints(weights, max_weight=0.10)
        assert clamped["A"] == 0.10
        assert any("单标的" in v for v in violations)

    @pytest.mark.unit
    def test_negative_weight(self):
        weights = {"A": -0.05, "B": 0.10}
        clamped, _ = enforce_hard_constraints(weights)
        assert clamped["A"] == 0.0

    @pytest.mark.unit
    def test_sector_cap(self):
        weights = {"A": 0.10, "B": 0.10, "C": 0.10}
        sector_map = {"A": "tech", "B": "tech", "C": "finance"}
        clamped, violations = enforce_hard_constraints(
            weights,
            max_weight=0.15,
            sector_map=sector_map,
            max_sector=0.15,
        )
        # tech 板块 0.20 > 0.15 → 压缩
        tech_total = clamped["A"] + clamped["B"]
        assert tech_total <= 0.15 + 1e-6
        assert any("板块" in v for v in violations)

    @pytest.mark.unit
    def test_normalization(self):
        weights = {"A": 0.10, "B": 0.10, "C": 0.10, "D": 0.10, "E": 0.10, "F": 0.10}
        clamped, violations = enforce_hard_constraints(weights, max_weight=0.20)
        # 总和 0.60 < 1.0, 不触发归一化
        assert abs(sum(clamped.values()) - 0.60) < 1e-6

    @pytest.mark.unit
    def test_normalization_triggered(self):
        weights = {
            "A": 0.10,
            "B": 0.10,
            "C": 0.10,
            "D": 0.10,
            "E": 0.10,
            "F": 0.10,
            "G": 0.10,
            "H": 0.10,
            "I": 0.10,
            "J": 0.10,
            "K": 0.10,
        }
        clamped, violations = enforce_hard_constraints(weights, max_weight=0.20)
        # 总和 1.10 > 1.0 → 归一化
        total = sum(clamped.values())
        if total > 1.0 + 1e-6:
            assert any("100%" in v for v in violations)

    @pytest.mark.unit
    def test_no_sector_map(self):
        weights = {"A": 0.08, "B": 0.07}
        clamped, violations = enforce_hard_constraints(weights)
        assert violations == []

    @pytest.mark.unit
    def test_empty_weights(self):
        clamped, violations = enforce_hard_constraints({})
        assert clamped == {}
        assert violations == []


# ============================================================
# validate_risk_budget
# ============================================================


class TestValidateRiskBudget:
    @pytest.mark.unit
    def test_valid(self):
        weights = {"A": 0.08, "B": 0.07, "C": 0.05}
        ok, violations = validate_risk_budget(weights)
        assert ok is True
        assert violations == []

    @pytest.mark.unit
    def test_weight_exceeded(self):
        weights = {"A": 0.15, "B": 0.05}
        ok, violations = validate_risk_budget(weights, max_weight=0.10)
        assert ok is False
        assert any("单标的" in v for v in violations)

    @pytest.mark.unit
    def test_sector_exceeded(self):
        weights = {"A": 0.08, "B": 0.08, "C": 0.05}
        sector_map = {"A": "tech", "B": "tech", "C": "finance"}
        ok, violations = validate_risk_budget(
            weights,
            max_weight=0.10,
            sector_map=sector_map,
            max_sector=0.15,
        )
        assert ok is False
        assert any("板块" in v for v in violations)

    @pytest.mark.unit
    def test_with_price_data(self):
        weights = {"A": 0.08, "B": 0.07}
        prices = {
            "A": pd.Series([10, 10.1, 10.2, 10.15, 10.3, 10.25, 10.4, 10.35]),
            "B": pd.Series([20, 20.2, 19.8, 20.1, 20.3, 19.9, 20.0, 20.2]),
        }
        ok, violations = validate_risk_budget(weights, price_data=prices)
        # 结果取决于 VaR 计算, 但应不抛异常
        assert isinstance(ok, bool)
        assert isinstance(violations, list)


# ============================================================
# _approx_var
# ============================================================


class TestApproxVar:
    @pytest.mark.unit
    def test_no_price_data(self):
        weights = {"A": 0.08, "B": 0.07}
        port_var, single = _approx_var(weights, {}, 5_000_000)
        assert port_var == 0.0
        assert len(single) == 2

    @pytest.mark.unit
    def test_with_price_data(self):
        weights = {"A": 0.08, "B": 0.07}
        prices = {
            "A": pd.Series(np.random.default_rng(42).normal(10, 0.1, 100)),
            "B": pd.Series(np.random.default_rng(43).normal(20, 0.2, 100)),
        }
        port_var, single = _approx_var(weights, prices, 5_000_000)
        assert port_var >= 0
        assert len(single) == 2

    @pytest.mark.unit
    def test_short_history(self):
        """少于 5 个数据点 → 用默认波动率"""
        weights = {"A": 0.08}
        prices = {"A": pd.Series([10, 10.1, 10.2])}
        port_var, single = _approx_var(weights, prices, 5_000_000)
        assert port_var == 0.0  # 只有一个标的且历史不足, rets 为空
        assert single["A"] > 0  # 用默认波动率
