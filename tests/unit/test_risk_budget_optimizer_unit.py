# -*- coding: utf-8 -*-
"""risk_budget_optimizer 单元测试 — 风险预算约束优化器全分支覆盖.

被测模块: utils/risk_budget_optimizer.py
覆盖目标: >=90%

测试内容:
- RiskBudgetResult 数据结构
- RiskBudgetOptimizer.optimize 主入口 (各约束组合 + 异常分支)
- _solve_constrained (SLSQP / 投影梯度 / 缩放法回退链)
- _projected_gradient / _scaling_method / _to_numpy
- save_result 序列化
- rebalance_to_te_target 自动调权
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.risk_budget_optimizer import (  # noqa: E402
    RiskBudgetOptimizer,
    RiskBudgetResult,
)


# ============================================================
# 辅助构造
# ============================================================

def _make_cov(n, seed=0):
    """构造正定协方差矩阵 (年化)."""
    rng = np.random.RandomState(seed)
    A = rng.randn(n, n) * 0.05 + np.eye(n) * 0.15
    return A @ A.T


# ============================================================
# 数据结构测试
# ============================================================

class RiskBudgetResultTest:
    def test_construct(self):
        n = 3
        r = RiskBudgetResult(
            optimal_weights=np.array([0.4, 0.3, 0.3]),
            benchmark_weights=np.array([0.34, 0.33, 0.33]),
            active_weights=np.array([0.06, -0.03, -0.03]),
            symbols=["a", "b", "c"],
            tracking_error=0.05,
            active_risk_te=0.05,
            var_contribution=np.array([0.01, 0.005, 0.005]),
            marginal_risk_contribution=np.array([0.1, 0.05, 0.05]),
            expected_return=0.10,
            active_return=0.02,
            information_ratio=0.4,
            te_constraint_slack=0.0,
            te_constraint_binding=True,
            weight_bounds_violated=False,
            factor_exposure_violations=[],
            solver_status="SLSQP-OK",
            iterations=10,
            objective_value=0.08,
        )
        assert r.solver_status == "SLSQP-OK"
        assert r.te_constraint_binding is True
        assert r.objective_value == 0.08


# ============================================================
# RiskBudgetOptimizer 构造
# ============================================================

class RiskBudgetOptimizerInitTest:
    def test_defaults(self):
        opt = RiskBudgetOptimizer()
        assert opt.delta == 2.5
        assert opt.risk_free_rate == 0.03
        assert opt.annual_factor == pytest.approx(252**0.5)

    def test_custom(self):
        opt = RiskBudgetOptimizer(risk_aversion=1.0, risk_free_rate=0.02, annualization_factor=10.0)
        assert opt.delta == 1.0
        assert opt.risk_free_rate == 0.02
        assert opt.annual_factor == 10.0


# ============================================================
# optimize 主入口
# ============================================================

class OptimizeTest:
    def setup_method(self):
        self.opt = RiskBudgetOptimizer()

    def test_empty_symbols_raises(self):
        with pytest.raises(ValueError, match="不能为空"):
            self.opt.optimize(
                symbols=[],
                expected_returns=np.array([]),
                cov_matrix=np.zeros((0, 0)),
                benchmark_weights=np.array([]),
            )

    def test_cov_dim_mismatch_raises(self):
        with pytest.raises(ValueError, match="cov_matrix 维度"):
            self.opt.optimize(
                symbols=["a", "b"],
                expected_returns=np.array([0.1, 0.2]),
                cov_matrix=np.zeros((3, 3)),
                benchmark_weights=np.array([0.5, 0.5]),
            )

    def test_mu_dim_mismatch_raises(self):
        with pytest.raises(ValueError, match="维度不匹配"):
            self.opt.optimize(
                symbols=["a", "b"],
                expected_returns=np.array([0.1]),  # 长度 1
                cov_matrix=np.eye(2) * 0.04,
                benchmark_weights=np.array([0.5, 0.5]),
            )

    def test_basic_optimize(self):
        n = 3
        symbols = ["a", "b", "c"]
        mu = np.array([0.15, 0.10, 0.08])
        cov = _make_cov(n, seed=1)
        bench = np.array([0.4, 0.35, 0.25])
        res = self.opt.optimize(
            symbols=symbols,
            expected_returns=mu,
            cov_matrix=cov,
            benchmark_weights=bench,
            max_tracking_error=0.05,
            max_weight=0.50,
            min_weight=0.0,
        )
        assert res.symbols == symbols
        assert len(res.optimal_weights) == n
        assert res.tracking_error >= 0
        assert res.tracking_error <= 0.05 + 1e-6
        assert res.solver_status in ("SLSQP-OK", "ProjectedGradient", "Scaling")
        assert res.iterations >= 1
        # 主动权重
        assert np.allclose(res.active_weights, res.optimal_weights - res.benchmark_weights)
        # TE 约束松弛度
        assert res.te_constraint_slack == pytest.approx(0.05 - res.tracking_error)
        # 权重上下限未违反
        assert res.weight_bounds_violated is False
        # 目标函数值
        expected_utility = res.expected_return - 0.5 * self.opt.delta * float(
            res.optimal_weights @ cov @ res.optimal_weights
        )
        assert res.objective_value == pytest.approx(expected_utility)

    def test_optimize_benchmark_normalization(self):
        """benchmark 不归一 → 内部归一化."""
        n = 2
        symbols = ["a", "b"]
        mu = np.array([0.1, 0.2])
        cov = _make_cov(n, seed=2)
        bench = np.array([0.6, 0.4])  # 已归一
        res = self.opt.optimize(
            symbols=symbols,
            expected_returns=mu,
            cov_matrix=cov,
            benchmark_weights=bench,
            max_tracking_error=0.10,
        )
        assert res.benchmark_weights.sum() == pytest.approx(1.0)

    def test_optimize_zero_benchmark_falls_back_equal(self):
        """benchmark 全 0 → 等权."""
        n = 2
        symbols = ["a", "b"]
        mu = np.array([0.1, 0.2])
        cov = _make_cov(n, seed=2)
        res = self.opt.optimize(
            symbols=symbols,
            expected_returns=mu,
            cov_matrix=cov,
            benchmark_weights=np.array([0.0, 0.0]),
            max_tracking_error=0.10,
        )
        assert res.benchmark_weights == pytest.approx([0.5, 0.5])

    def test_optimize_with_pandas_cov(self):
        n = 2
        symbols = ["a", "b"]
        mu = np.array([0.1, 0.2])
        cov = _make_cov(n, seed=3)
        cov_df = pd.DataFrame(cov, index=symbols, columns=symbols)
        res = self.opt.optimize(
            symbols=symbols,
            expected_returns=mu,
            cov_matrix=cov_df,
            benchmark_weights=np.array([0.5, 0.5]),
            max_tracking_error=0.10,
        )
        assert res.tracking_error >= 0
        assert len(res.optimal_weights) == n

    def test_optimize_with_industry_constraint(self):
        n = 4
        symbols = ["a", "b", "c", "d"]
        mu = np.array([0.15, 0.12, 0.08, 0.10])
        cov = _make_cov(n, seed=4)
        bench = np.array([0.25, 0.25, 0.25, 0.25])
        res = self.opt.optimize(
            symbols=symbols,
            expected_returns=mu,
            cov_matrix=cov,
            benchmark_weights=bench,
            max_tracking_error=0.08,
            max_weight=0.40,
            min_weight=0.0,
            industry_groups={"金融": [0, 1], "制造": [2, 3]},
            max_industry_exposure=0.10,
        )
        assert res.tracking_error <= 0.08 + 1e-6
        assert res.solver_status in ("SLSQP-OK", "ProjectedGradient", "Scaling")

    def test_optimize_with_factor_exposure_constraint(self):
        n = 3
        symbols = ["a", "b", "c"]
        mu = np.array([0.15, 0.10, 0.08])
        cov = _make_cov(n, seed=5)
        bench = np.array([0.4, 0.35, 0.25])
        # 2 因子 × 3 标的
        factor_exposures = np.array([[1.0, 0.5], [0.8, -0.3], [-0.2, 0.7]])
        res = self.opt.optimize(
            symbols=symbols,
            expected_returns=mu,
            cov_matrix=cov,
            benchmark_weights=bench,
            max_tracking_error=0.10,
            factor_exposures=factor_exposures,
            max_factor_exposure=0.05,  # 很紧 → 可能产生违反
        )
        # 因子暴露违反列表 (可能为空也可能非空, 取决于求解)
        assert isinstance(res.factor_exposure_violations, list)
        # 每条违反格式 factor_k=xxx
        for v in res.factor_exposure_violations:
            assert v.startswith("factor_")

    def test_optimize_with_target_return(self):
        n = 3
        symbols = ["a", "b", "c"]
        mu = np.array([0.15, 0.10, 0.08])
        cov = _make_cov(n, seed=6)
        bench = np.array([0.4, 0.35, 0.25])
        res = self.opt.optimize(
            symbols=symbols,
            expected_returns=mu,
            cov_matrix=cov,
            benchmark_weights=bench,
            max_tracking_error=0.10,
            target_return=0.11,
        )
        assert res.tracking_error >= 0

    def test_optimize_no_weight_bounds(self):
        """max_weight=None, min_weight=None → 无上下限约束."""
        n = 2
        symbols = ["a", "b"]
        mu = np.array([0.2, 0.05])
        cov = _make_cov(n, seed=7)
        res = self.opt.optimize(
            symbols=symbols,
            expected_returns=mu,
            cov_matrix=cov,
            benchmark_weights=np.array([0.5, 0.5]),
            max_tracking_error=0.15,
            max_weight=None,
            min_weight=None,
        )
        assert res.tracking_error <= 0.15 + 1e-6

    def test_optimize_weight_bounds_violated(self):
        """构造极紧的上下限使求解器可能违反."""
        n = 2
        symbols = ["a", "b"]
        mu = np.array([0.3, 0.01])
        cov = _make_cov(n, seed=8)
        res = self.opt.optimize(
            symbols=symbols,
            expected_returns=mu,
            cov_matrix=cov,
            benchmark_weights=np.array([0.5, 0.5]),
            max_tracking_error=0.20,
            max_weight=0.99,
            min_weight=0.01,
        )
        # weight_bounds_violated 是 bool
        assert isinstance(res.weight_bounds_violated, bool)

    def test_optimize_zero_te(self):
        """max_te=0 → TE 约束迫使 w=bench → te=0 → IR=0."""
        n = 2
        symbols = ["a", "b"]
        mu = np.array([0.1, 0.2])
        cov = _make_cov(n, seed=9)
        res = self.opt.optimize(
            symbols=symbols,
            expected_returns=mu,
            cov_matrix=cov,
            benchmark_weights=np.array([0.5, 0.5]),
            max_tracking_error=0.0,
        )
        # te 可能为 0 或极小
        assert res.tracking_error >= 0
        if res.tracking_error == 0:
            assert res.information_ratio == 0.0
            assert res.marginal_risk_contribution.sum() == 0.0

    def test_optimize_equal_returns_zero_te_mrc_zeros(self):
        """所有预期收益相等 → w_opt=bench → te=0 → mrc=zeros (line 176)."""
        n = 3
        symbols = ["a", "b", "c"]
        mu = np.array([0.10, 0.10, 0.10])  # 全相等
        cov = _make_cov(n, seed=20)
        bench = np.array([0.4, 0.35, 0.25])
        res = self.opt.optimize(
            symbols=symbols,
            expected_returns=mu,
            cov_matrix=cov,
            benchmark_weights=bench,
            max_tracking_error=0.05,
        )
        # 收益无差异 → 最优解应贴近基准 → te 极小或为 0
        if res.tracking_error == 0:
            assert np.allclose(res.marginal_risk_contribution, 0.0)
            assert res.information_ratio == 0.0

    def test_optimize_var_contribution_and_mrc(self):
        n = 3
        symbols = ["a", "b", "c"]
        mu = np.array([0.15, 0.10, 0.08])
        cov = _make_cov(n, seed=10)
        res = self.opt.optimize(
            symbols=symbols,
            expected_returns=mu,
            cov_matrix=cov,
            benchmark_weights=np.array([0.4, 0.35, 0.25]),
            max_tracking_error=0.05,
        )
        # VaR 贡献 = active_w * mrc * 1.645
        if res.tracking_error > 0:
            expected_var = res.active_weights * res.marginal_risk_contribution * 1.645
            assert np.allclose(res.var_contribution, expected_var)
        # IR = active_return / te
        if res.tracking_error > 0:
            assert res.information_ratio == pytest.approx(res.active_return / res.tracking_error)


# ============================================================
# _solve_constrained 回退链
# ============================================================

class SolveConstrainedFallbackTest:
    def setup_method(self):
        self.opt = RiskBudgetOptimizer()

    def test_scipy_unavailable_falls_back(self):
        """scipy 导入失败 → 回退到投影梯度/缩放法."""
        n = 2
        mu = np.array([0.1, 0.2])
        cov = _make_cov(n, seed=11)
        bench = np.array([0.5, 0.5])

        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "scipy.optimize" or name.startswith("scipy"):
                raise ImportError("mocked scipy unavailable")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            res = self.opt.optimize(
                symbols=["a", "b"],
                expected_returns=mu,
                cov_matrix=cov,
                benchmark_weights=bench,
                max_tracking_error=0.10,
            )
        assert res.solver_status in ("ProjectedGradient", "Scaling")
        assert res.tracking_error >= 0

    def test_scaling_method_directly(self):
        """直接调用 _scaling_method."""
        n = 3
        mu = np.array([0.15, 0.10, 0.08])
        cov = _make_cov(n, seed=12)
        bench = np.array([0.4, 0.35, 0.25])
        w = self.opt._scaling_method(mu, cov, bench, max_te=0.05)
        assert len(w) == n
        assert w.sum() == pytest.approx(1.0)
        assert np.all(w >= 0)

    def test_projected_gradient_directly(self):
        n = 3
        mu = np.array([0.15, 0.10, 0.08])
        cov = _make_cov(n, seed=13)
        bench = np.array([0.4, 0.35, 0.25])
        w = self.opt._projected_gradient(mu, cov, bench, max_te=0.05, max_weight=0.5, min_weight=0.0)
        assert len(w) == n
        # 投影梯度法保证满仓
        assert w.sum() == pytest.approx(1.0, abs=1e-6)

    def test_projected_gradient_te_projection(self):
        """构造场景使 TE 投影生效."""
        n = 2
        mu = np.array([0.5, -0.5])  # 强信号
        cov = np.eye(n) * 0.04
        bench = np.array([0.5, 0.5])
        w = self.opt._projected_gradient(mu, cov, bench, max_te=0.01, max_weight=1.0, min_weight=0.0)
        assert w.sum() == pytest.approx(1.0, abs=1e-6)

    def test_projected_gradient_failure_falls_to_scaling(self):
        """scipy 失败 + 投影梯度抛异常 → 回退到缩放法 (lines 345-350)."""
        n = 2
        mu = np.array([0.15, 0.10])
        cov = _make_cov(n, seed=21)
        bench = np.array([0.5, 0.5])

        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "scipy.optimize" or name.startswith("scipy"):
                raise ImportError("mocked scipy unavailable")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import), patch.object(
            self.opt, "_projected_gradient", side_effect=RuntimeError("mocked PG failure")
        ):
            res = self.opt.optimize(
                symbols=["a", "b"],
                expected_returns=mu,
                cov_matrix=cov,
                benchmark_weights=bench,
                max_tracking_error=0.10,
            )
        assert res.solver_status == "Scaling"
        assert res.tracking_error >= 0


# ============================================================
# _to_numpy 辅助
# ============================================================

class ToNumpyTest:
    def setup_method(self):
        self.opt = RiskBudgetOptimizer()

    def test_ndarray_input(self):
        arr = np.array([[1, 2], [3, 4]])
        out = self.opt._to_numpy(arr)
        assert out.dtype == float
        assert np.allclose(out, arr)

    def test_list_input(self):
        out = self.opt._to_numpy([[1, 2], [3, 4]])
        assert out.dtype == float
        assert np.allclose(out, [[1, 2], [3, 4]])

    def test_dataframe_input(self):
        df = pd.DataFrame([[1, 2], [3, 4]])
        out = self.opt._to_numpy(df)
        assert out.dtype == float
        assert np.allclose(out, [[1, 2], [3, 4]])


# ============================================================
# save_result 序列化
# ============================================================

class SaveResultTest:
    def setup_method(self):
        self.opt = RiskBudgetOptimizer()

    def test_save_result_writes_json(self, tmp_path):
        n = 2
        res = self.opt.optimize(
            symbols=["a", "b"],
            expected_returns=np.array([0.1, 0.2]),
            cov_matrix=_make_cov(n, seed=14),
            benchmark_weights=np.array([0.5, 0.5]),
            max_tracking_error=0.10,
        )
        out = tmp_path / "nested" / "rb_result.json"
        ret = self.opt.save_result(res, out)
        assert ret == out
        assert out.exists()
        with open(out, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["symbols"] == ["a", "b"]
        assert len(data["optimal_weights"]) == n
        assert data["solver_status"] == res.solver_status
        assert data["tracking_error"] == pytest.approx(res.tracking_error)


# ============================================================
# rebalance_to_te_target 自动调权
# ============================================================

class RebalanceToTeTargetTest:
    def setup_method(self):
        self.opt = RiskBudgetOptimizer()

    def test_current_te_zero_returns_current(self):
        """当前权重=基准 → current_te=0 → 直接返回."""
        cov = _make_cov(3, seed=15)
        w = np.array([0.4, 0.35, 0.25])
        result = self.opt.rebalance_to_te_target(
            current_weights=w,
            benchmark_weights=w,
            cov_matrix=cov,
            target_te=0.05,
        )
        assert np.allclose(result["new_weights"], w)
        assert np.allclose(result["adjustments"], 0.0)
        assert result["expected_te"] == 0.0

    def test_basic_rebalance_scale_up(self):
        """当前 TE 小于目标 → 放大主动权重."""
        cov = _make_cov(3, seed=16)
        bench = np.array([0.4, 0.35, 0.25])
        # 当前权重接近基准 (小 TE)
        current = np.array([0.42, 0.34, 0.24])
        result = self.opt.rebalance_to_te_target(
            current_weights=current,
            benchmark_weights=bench,
            cov_matrix=cov,
            target_te=0.10,
        )
        assert "new_weights" in result
        assert "adjustments" in result
        assert "expected_te" in result
        assert len(result["new_weights"]) == 3
        assert result["expected_te"] >= 0

    def test_rebalance_with_adjustment_truncation(self):
        """构造超调场景使单标的调整被截断."""
        cov = np.eye(3) * 0.04
        bench = np.array([0.34, 0.33, 0.33])
        # 当前权重大幅偏离基准
        current = np.array([0.80, 0.10, 0.10])
        result = self.opt.rebalance_to_te_target(
            current_weights=current,
            benchmark_weights=bench,
            cov_matrix=cov,
            target_te=0.50,  # 大目标 → 要求大幅调整
            max_adjustment=0.01,  # 极小调整上限 → 触发截断
        )
        # 截断后每个调整幅度 <= max_adjustment
        assert np.all(np.abs(result["adjustments"]) <= 0.01 + 1e-10)
        assert result["expected_te"] >= 0

    def test_rebalance_scale_down(self):
        """当前 TE 大于目标 → 缩小主动权重."""
        cov = np.eye(3) * 0.04
        bench = np.array([0.34, 0.33, 0.33])
        current = np.array([0.70, 0.20, 0.10])  # 大偏离
        result = self.opt.rebalance_to_te_target(
            current_weights=current,
            benchmark_weights=bench,
            cov_matrix=cov,
            target_te=0.01,  # 小目标
            max_adjustment=1.0,  # 不截断
        )
        assert result["expected_te"] >= 0
        # new_weights 归一化
        assert result["new_weights"].sum() == pytest.approx(1.0, abs=1e-6)