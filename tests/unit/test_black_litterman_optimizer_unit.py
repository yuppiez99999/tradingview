# -*- coding: utf-8 -*-
"""black_litterman_optimizer 单元测试 — BL 组合优化器全覆盖.

被测模块: utils/black_litterman_optimizer.py
覆盖目标: >=90%
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.black_litterman_optimizer import (  # noqa: E402
    BLResult,
    BlackLittermanOptimizer,
    View,
)


# ============================================================
# 辅助: 合成协方差矩阵
# ============================================================

def _make_cov(n: int = 3, seed: int = 42) -> np.ndarray:
    rng = np.random.default_rng(seed)
    A = rng.normal(0, 0.01, (n, n))
    return A @ A.T + np.eye(n) * 0.01


# ============================================================
# __init__
# ============================================================

class TestInit:
    def test_default(self):
        opt = BlackLittermanOptimizer()
        assert opt.delta == 2.5
        assert opt.tau == 0.05

    def test_custom_params(self):
        opt = BlackLittermanOptimizer(risk_aversion=3.0, tau=0.025)
        assert opt.delta == 3.0
        assert opt.tau == 0.025

    def test_invalid_risk_aversion(self):
        with pytest.raises(ValueError):
            BlackLittermanOptimizer(risk_aversion=0)

    def test_invalid_risk_aversion_negative(self):
        with pytest.raises(ValueError):
            BlackLittermanOptimizer(risk_aversion=-1)

    def test_invalid_tau(self):
        with pytest.raises(ValueError):
            BlackLittermanOptimizer(tau=0)
        with pytest.raises(ValueError):
            BlackLittermanOptimizer(tau=1.5)

    def test_invalid_confidence(self):
        with pytest.raises(ValueError):
            BlackLittermanOptimizer(default_confidence=1.5)


# ============================================================
# optimize — 无观点
# ============================================================

class TestOptimizeNoViews:
    def test_no_views_returns_market_weights(self):
        opt = BlackLittermanOptimizer()
        cov = _make_cov(3)
        result = opt.optimize(
            assets=["A", "B", "C"],
            market_weights=[0.4, 0.3, 0.3],
            cov_matrix=cov,
        )
        assert isinstance(result, BLResult)
        assert len(result.optimal_weights) == 3
        assert result.optimal_weights.sum() == pytest.approx(1.0, abs=1e-4)

    def test_implied_returns_computed(self):
        opt = BlackLittermanOptimizer(risk_aversion=2.5)
        cov = _make_cov(3)
        result = opt.optimize(
            assets=["A", "B", "C"],
            market_weights=[0.5, 0.3, 0.2],
            cov_matrix=cov,
        )
        assert len(result.implied_equilibrium_returns) == 3


# ============================================================
# optimize — 有观点
# ============================================================

class TestOptimizeWithViews:
    def test_absolute_view(self):
        opt = BlackLittermanOptimizer()
        cov = _make_cov(3)
        views = [View(type="absolute", assets=["A"], weights=[1.0], expected_return=0.15, confidence=0.7)]
        result = opt.optimize(
            assets=["A", "B", "C"],
            market_weights=[0.33, 0.33, 0.34],
            cov_matrix=cov,
            views=views,
        )
        assert len(result.posterior_returns) == 3
        assert len(result.optimal_weights) == 3

    def test_relative_view(self):
        opt = BlackLittermanOptimizer()
        cov = _make_cov(3)
        views = [View(type="relative", assets=["A", "B"], weights=[1.0, -1.0], expected_return=0.05, confidence=0.6)]
        result = opt.optimize(
            assets=["A", "B", "C"],
            market_weights=[0.33, 0.33, 0.34],
            cov_matrix=cov,
            views=views,
        )
        assert result.optimal_weights.sum() == pytest.approx(1.0, abs=1e-4)

    def test_multiple_views(self):
        opt = BlackLittermanOptimizer()
        cov = _make_cov(3)
        views = [
            View(type="absolute", assets=["A"], weights=[1.0], expected_return=0.12, confidence=0.6),
            View(type="relative", assets=["B", "C"], weights=[1.0, -1.0], expected_return=0.03, confidence=0.5),
        ]
        result = opt.optimize(
            assets=["A", "B", "C"],
            market_weights=[0.4, 0.3, 0.3],
            cov_matrix=cov,
            views=views,
        )
        assert len(result.views) == 2

    def test_result_diagnostics(self):
        opt = BlackLittermanOptimizer()
        cov = _make_cov(3)
        result = opt.optimize(
            assets=["A", "B", "C"],
            market_weights=[0.4, 0.3, 0.3],
            cov_matrix=cov,
        )
        assert result.diversification_ratio > 0
        assert result.effective_n > 0
        assert isinstance(result.sharpe_ratio, float)


# ============================================================
# optimize — 约束
# ============================================================

class TestOptimizeConstraints:
    def test_max_weight(self):
        opt = BlackLittermanOptimizer()
        cov = _make_cov(3)
        result = opt.optimize(
            assets=["A", "B", "C"],
            market_weights=[0.4, 0.3, 0.3],
            cov_matrix=cov,
            max_weight=0.5,
        )
        assert all(w <= 0.5 + 1e-4 for w in result.optimal_weights)

    def test_min_weight(self):
        opt = BlackLittermanOptimizer()
        cov = _make_cov(3)
        result = opt.optimize(
            assets=["A", "B", "C"],
            market_weights=[0.4, 0.3, 0.3],
            cov_matrix=cov,
            min_weight=0.1,
        )
        assert all(w >= 0.1 - 1e-4 for w in result.optimal_weights)


# ============================================================
# optimize — 异常输入
# ============================================================

class TestOptimizeInvalid:
    def test_empty_assets(self):
        opt = BlackLittermanOptimizer()
        with pytest.raises(ValueError):
            opt.optimize(assets=[], market_weights=[], cov_matrix=np.zeros((0, 0)))

    def test_dimension_mismatch(self):
        opt = BlackLittermanOptimizer()
        with pytest.raises(ValueError):
            opt.optimize(
                assets=["A", "B"],
                market_weights=[0.5, 0.3, 0.2],
                cov_matrix=_make_cov(2),
            )

    def test_zero_weight_sum(self):
        opt = BlackLittermanOptimizer()
        with pytest.raises(ValueError):
            opt.optimize(
                assets=["A", "B"],
                market_weights=[0, 0],
                cov_matrix=_make_cov(2),
            )

    def test_cov_dimension_mismatch(self):
        opt = BlackLittermanOptimizer()
        with pytest.raises(ValueError):
            opt.optimize(
                assets=["A", "B"],
                market_weights=[0.5, 0.5],
                cov_matrix=_make_cov(3),
            )

    def test_invalid_view_asset(self):
        opt = BlackLittermanOptimizer()
        views = [View(type="absolute", assets=["X"], weights=[1.0], expected_return=0.1)]
        with pytest.raises(ValueError):
            opt.optimize(
                assets=["A", "B"],
                market_weights=[0.5, 0.5],
                cov_matrix=_make_cov(2),
                views=views,
            )


# ============================================================
# run_shadow
# ============================================================

class TestRunShadow:
    def test_dict_returns(self):
        opt = BlackLittermanOptimizer()
        result = opt.run_shadow(
            assets=["A", "B", "C"],
            expected_returns={"A": 0.1, "B": 0.05, "C": -0.02},
            cov_matrix=_make_cov(3),
        )
        assert isinstance(result, BLResult)
        assert result.optimal_weights.sum() == pytest.approx(1.0, abs=1e-4)

    def test_list_returns(self):
        opt = BlackLittermanOptimizer()
        result = opt.run_shadow(
            assets=["A", "B", "C"],
            expected_returns=[0.1, 0.05, -0.02],
            cov_matrix=_make_cov(3),
        )
        assert len(result.optimal_weights) == 3

    def test_market_cap_weights(self):
        opt = BlackLittermanOptimizer()
        result = opt.run_shadow(
            assets=["A", "B", "C"],
            expected_returns=[0.1, 0.05, 0.0],
            cov_matrix=_make_cov(3),
            market_cap_weights={"A": 0.5, "B": 0.3, "C": 0.2},
        )
        assert result.optimal_weights.sum() == pytest.approx(1.0, abs=1e-4)

    def test_zero_returns_no_views(self):
        opt = BlackLittermanOptimizer()
        result = opt.run_shadow(
            assets=["A", "B"],
            expected_returns=[0.0, 0.0],
            cov_matrix=_make_cov(2),
        )
        assert len(result.views) == 0


# ============================================================
# save_result
# ============================================================

class TestSaveResult:
    def test_save_and_read(self, tmp_path):
        opt = BlackLittermanOptimizer()
        result = opt.optimize(
            assets=["A", "B"],
            market_weights=[0.5, 0.5],
            cov_matrix=_make_cov(2),
        )
        path = opt.save_result(result, tmp_path / "bl_result.json")
        assert path.exists()
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert data["assets"] == ["A", "B"]
        assert len(data["optimal_weights"]) == 2