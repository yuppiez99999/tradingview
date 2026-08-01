"""
风险预算约束优化器 (Risk Budget Constrained Optimizer)

世界顶级对冲基金标准 (Bridgewater / AQR / PanAgora):
- 在跟踪误差 (TE) 约束下求最优权重
- 解决 "Barra 显示 TE 超预算但无法自动调整" 的问题
- 多种约束: TE 上限 / 单标的权重 / 行业暴露 / 因子暴露
- 目标函数: 最大化预期收益 或 最小化跟踪误差

公式核心:
    max  w' × E[R]
    s.t. √((w - w_bench)' Σ (w - w_bench)) ≤ TE_max       (跟踪误差约束)
         Σ w_i = 1                                          (满仓约束)
         w_lo ≤ w_i ≤ w_hi                                  (单标的上下限)
         |Σ_{i ∈ industry} (w_i - w_bench_i)| ≤ limit       (行业暴露约束)
         |X' (w - w_bench)| ≤ factor_limit                  (因子暴露约束)

参考:
- Roll, R. (1992) "A Mean/Variance Analysis of Tracking Error"
- Jorion, P. (2003) "Portfolio Optimization with Tracking-Error Constraints"
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

# ============================================================
# 数据结构
# ============================================================


@dataclass
class RiskBudgetResult:
    """风险预算优化结果"""

    optimal_weights: np.ndarray  # 最优权重
    benchmark_weights: np.ndarray  # 基准权重
    active_weights: np.ndarray  # 主动权重
    symbols: list[str]  # 标的列表

    # 风险指标
    tracking_error: float  # 跟踪误差 (年化)
    active_risk_te: float  # 主动风险 = TE
    var_contribution: np.ndarray  # 各标的的 VaR 贡献
    marginal_risk_contribution: np.ndarray  # 边际风险贡献

    # 收益指标
    expected_return: float  # 预期组合收益
    active_return: float  # 主动收益
    information_ratio: float  # 信息比率

    # 约束状态
    te_constraint_slack: float  # TE 约束松弛度 (正=未达上限)
    te_constraint_binding: bool  # TE 约束是否绑定
    weight_bounds_violated: bool  # 权重上下限是否违反
    factor_exposure_violations: list[str]  # 因子暴露违反列表

    # 优化诊断
    solver_status: str  # 求解器状态
    iterations: int  # 迭代次数
    objective_value: float  # 目标函数值


# ============================================================
# 风险预算约束优化器
# ============================================================


class RiskBudgetOptimizer:
    """风险预算约束优化器

    用法:
        optimizer = RiskBudgetOptimizer()
        result = optimizer.optimize(
            symbols=["600519", "000858", "601318"],
            expected_returns=np.array([0.15, 0.10, 0.08]),
            cov_matrix=cov_np,
            benchmark_weights=np.array([0.4, 0.35, 0.25]),
            max_tracking_error=0.05,    # 5% TE 上限
            max_weight=0.30,             # 单标的最多 30%
            min_weight=0.0,              # 不允许做空
        )
    """

    def __init__(
        self,
        risk_aversion: float = 2.5,
        risk_free_rate: float = 0.03,
        annualization_factor: float = 252**0.5,
    ):
        self.delta = float(risk_aversion)
        self.risk_free_rate = float(risk_free_rate)
        self.annual_factor = float(annualization_factor)

    # ------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------

    def optimize(
        self,
        symbols: list[str],
        expected_returns: list[float] | np.ndarray,
        cov_matrix: np.ndarray | pd.DataFrame,  # type: ignore
        benchmark_weights: list[float] | np.ndarray,
        max_tracking_error: float = 0.05,
        max_weight: float | None = None,
        min_weight: float | None = 0.0,
        industry_groups: dict[str, list[int]] | None = None,
        max_industry_exposure: float | None = None,
        factor_exposures: np.ndarray | None = None,
        max_factor_exposure: float | None = None,
        target_return: float | None = None,
    ) -> RiskBudgetResult:
        """在风险预算约束下优化权重

        Args:
            symbols: 标的代码列表
            expected_returns: 预期收益向量
            cov_matrix: 协方差矩阵 (年化)
            benchmark_weights: 基准权重
            max_tracking_error: 跟踪误差上限 (年化, 默认 5%)
            max_weight: 单标的权重上限 (None=无约束)
            min_weight: 单标的权重下限 (None=无约束, 默认 0)
            industry_groups: {industry_name: [asset_indices]}, 用于行业暴露约束
            max_industry_exposure: 单行业主动暴露上限
            factor_exposures: 因子暴露矩阵 (N × K), K=因子数
            max_factor_exposure: 单因子主动暴露上限
            target_return: 可选, 目标组合收益

        Returns:
            RiskBudgetResult
        """
        n = len(symbols)
        if n == 0:
            raise ValueError("symbols 不能为空")

        mu = np.asarray(expected_returns, dtype=float)
        Sigma = self._to_numpy(cov_matrix)
        w_bench = np.asarray(benchmark_weights, dtype=float)
        w_bench = w_bench / w_bench.sum() if w_bench.sum() > 0 else np.ones(n) / n

        if Sigma.shape != (n, n):
            raise ValueError(f"cov_matrix 维度 {Sigma.shape} != ({n},{n})")
        if len(mu) != n or len(w_bench) != n:
            raise ValueError(f"维度不匹配: mu={len(mu)}, w_bench={len(w_bench)}, n={n}")

        # 1. 求解约束优化
        w_opt, solver_status, iterations = self._solve_constrained(
            mu=mu,
            Sigma=Sigma,
            w_bench=w_bench,
            max_te=max_tracking_error,
            max_weight=max_weight,
            min_weight=min_weight,
            industry_groups=industry_groups,
            max_industry_exposure=max_industry_exposure,
            factor_exposures=factor_exposures,
            max_factor_exposure=max_factor_exposure,
            target_return=target_return,
        )

        # 2. 计算风险指标
        active_w = w_opt - w_bench
        te_var = float(active_w @ Sigma @ active_w)
        te = math.sqrt(max(te_var, 0))
        # 边际风险贡献 MRC = (Σ w_a) / √(w_a' Σ w_a)
        if te > 0:
            mrc = (Sigma @ active_w) / te
        else:
            mrc = np.zeros(n)
        # VaR 贡献 (95% 置信度)
        var_contrib = active_w * mrc * 1.645

        # 3. 收益指标
        port_ret = float(w_opt @ mu)
        active_ret = float(active_w @ mu)
        ir = active_ret / te if te > 0 else 0.0

        # 4. 约束状态
        te_slack = max_tracking_error - te
        te_binding = te_slack < 0.001  # < 10bps 视为绑定

        # 权重上下限违反检查
        weight_violated = False
        if max_weight is not None:
            weight_violated = weight_violated or bool(np.any(w_opt > max_weight + 1e-6))
        if min_weight is not None:
            weight_violated = weight_violated or bool(np.any(w_opt < min_weight - 1e-6))

        # 因子暴露违反检查（向量化）
        factor_violations: list[str] = []
        if factor_exposures is not None and max_factor_exposure is not None:
            active_factor = factor_exposures.T @ active_w
            violated_mask = np.abs(active_factor) > max_factor_exposure
            violated_indices = np.where(violated_mask)[0]
            factor_violations = [f"factor_{k}={active_factor[k]:.3f}" for k in violated_indices]

        # 目标函数值 (效用): U = w'μ - (δ/2) w'Σw
        utility = port_ret - 0.5 * self.delta * float(w_opt @ Sigma @ w_opt)

        return RiskBudgetResult(
            optimal_weights=w_opt,
            benchmark_weights=w_bench,
            active_weights=active_w,
            symbols=list(symbols),
            tracking_error=te,
            active_risk_te=te,
            var_contribution=var_contrib,
            marginal_risk_contribution=mrc,
            expected_return=port_ret,
            active_return=active_ret,
            information_ratio=ir,
            te_constraint_slack=te_slack,
            te_constraint_binding=te_binding,
            weight_bounds_violated=weight_violated,
            factor_exposure_violations=factor_violations,
            solver_status=solver_status,
            iterations=iterations,
            objective_value=utility,
        )

    # ------------------------------------------------------------
    # 约束求解
    # ------------------------------------------------------------

    def _solve_constrained(
        self,
        mu: np.ndarray,
        Sigma: np.ndarray,
        w_bench: np.ndarray,
        max_te: float,
        max_weight: float | None,
        min_weight: float | None,
        industry_groups: dict[str, list[int]] | None,
        max_industry_exposure: float | None,
        factor_exposures: np.ndarray | None,
        max_factor_exposure: float | None,
        target_return: float | None,
    ) -> tuple[np.ndarray, str, int]:
        """约束优化求解

        策略:
        1. 优先用 scipy SLSQP (支持多种约束)
        2. 回退到投影梯度法
        3. 最终回退到缩放法
        """
        n = len(mu)

        # 尝试 scipy
        try:
            from scipy.optimize import minimize

            w0 = w_bench.copy()

            # 目标: 最大化 w'μ - (δ/2) TE²
            def neg_utility(w):
                active = w - w_bench
                te2 = float(active @ Sigma @ active)
                ret = float(w @ mu)
                return -(ret - 0.5 * self.delta * te2)

            # 约束列表
            constraints = [
                {"type": "eq", "fun": lambda w: w.sum() - 1.0},  # 满仓
            ]

            # TE 约束 (≤ max_te)
            if max_te > 0:
                constraints.append(
                    {
                        "type": "ineq",
                        "fun": lambda w: max_te**2 - float((w - w_bench) @ Sigma @ (w - w_bench)),
                    }
                )

            # 目标收益约束
            if target_return is not None:
                constraints.append(
                    {
                        "type": "eq",
                        "fun": lambda w: float(w @ mu) - target_return,
                    }
                )

            # 行业暴露约束
            if industry_groups and max_industry_exposure is not None:
                for _ind_name, indices in industry_groups.items():
                    indices_arr = np.array(indices, dtype=int)
                    bench_ind_sum = float(w_bench[indices_arr].sum())
                    constraints.append(
                        {
                            "type": "ineq",
                            "fun": lambda w, idx=indices_arr, bs=bench_ind_sum: (
                                max_industry_exposure - abs(float(w[idx].sum() - bs))
                            ),
                        }
                    )

            # 因子暴露约束
            if factor_exposures is not None and max_factor_exposure is not None:
                K = factor_exposures.shape[1]
                for k in range(K):
                    constraints.append(
                        {
                            "type": "ineq",
                            "fun": lambda w, kk=k: (
                                max_factor_exposure - abs(float(factor_exposures[:, kk] @ (w - w_bench)))
                            ),
                        }
                    )

            # 权重上下限
            lo = float(min_weight) if min_weight is not None else -1.0
            hi = float(max_weight) if max_weight is not None else 1.0
            bounds = [(lo, hi)] * n

            res = minimize(
                neg_utility,
                w0,
                method="SLSQP",
                bounds=bounds,
                constraints=constraints,
                options={"maxiter": 300, "ftol": 1e-10},
            )

            if res.success:
                w = res.x
                if w.sum() > 0:
                    w = w / w.sum()
                return w, "SLSQP-OK", int(res.nit)

        except (ImportError, Exception):
            pass

        # 回退 1: 投影梯度法
        try:
            w = self._projected_gradient(mu, Sigma, w_bench, max_te, max_weight, min_weight)
            return w, "ProjectedGradient", 100
        except Exception:  # P2 模块 fail-safe, 待后续精确化
            pass

        # 回退 2: 缩放法 — 将基准权重向预期收益高的方向倾斜, 同时满足 TE 约束
        w = self._scaling_method(mu, Sigma, w_bench, max_te)
        return w, "Scaling", 1

    def _projected_gradient(
        self,
        mu: np.ndarray,
        Sigma: np.ndarray,
        w_bench: np.ndarray,
        max_te: float,
        max_weight: float | None,
        min_weight: float | None,
        lr: float = 0.01,
        max_iter: int = 100,
    ) -> np.ndarray:
        """投影梯度法"""
        n = len(mu)
        w = w_bench.copy()

        for _ in range(max_iter):
            # 梯度: ∂U/∂w = μ - δ Σ (w - w_bench)
            grad = mu - self.delta * Sigma @ (w - w_bench)
            w_new = w + lr * grad

            # 投影 1: 权重上下限
            if max_weight is not None:
                w_new = np.minimum(w_new, max_weight)
            if min_weight is not None:
                w_new = np.maximum(w_new, min_weight)

            # 投影 2: 满仓
            w_new = w_new / w_new.sum() if w_new.sum() > 0 else np.ones(n) / n

            # 投影 3: TE 约束
            active = w_new - w_bench
            te = math.sqrt(float(active @ Sigma @ active))
            if te > max_te and te > 0:
                scale = max_te / te
                w_new = w_bench + active * scale
                # 再投影满仓
                w_new = w_new / w_new.sum() if w_new.sum() > 0 else np.ones(n) / n

            # 收敛检查
            if np.linalg.norm(w_new - w) < 1e-8:
                break
            w = w_new

        return w

    def _scaling_method(
        self,
        mu: np.ndarray,
        Sigma: np.ndarray,
        w_bench: np.ndarray,
        max_te: float,
    ) -> np.ndarray:
        """缩放法: 基准 + 收益排序倾斜, 缩放到 TE 上限"""
        n = len(mu)
        # 信号方向: 正比于 mu
        signal = mu - mu.mean()
        # 倾斜权重
        tilt = signal / (np.abs(signal).sum() + 1e-10) * 0.1  # 10% 倾斜幅度
        w_active = tilt / max(np.sqrt(float(tilt @ Sigma @ tilt)), 1e-10) * max_te
        w = w_bench + w_active
        # 归一化
        w = np.maximum(w, 0)
        w = w / w.sum() if w.sum() > 0 else np.ones(n) / n
        return w

    # ------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------

    def _to_numpy(self, m) -> np.ndarray:
        if isinstance(m, np.ndarray):
            return m.astype(float)
        try:
            return np.asarray(m, dtype=float)
        except Exception:  # P2 模块 fail-safe, 待后续精确化
            return np.array(m, dtype=float)

    def save_result(self, result: RiskBudgetResult, path: str | Path) -> Path:
        """保存优化结果到 JSON"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "symbols": result.symbols,
            "optimal_weights": result.optimal_weights.tolist(),
            "benchmark_weights": result.benchmark_weights.tolist(),
            "active_weights": result.active_weights.tolist(),
            "tracking_error": result.tracking_error,
            "expected_return": result.expected_return,
            "active_return": result.active_return,
            "information_ratio": result.information_ratio,
            "te_constraint_slack": result.te_constraint_slack,
            "te_constraint_binding": result.te_constraint_binding,
            "weight_bounds_violated": result.weight_bounds_violated,
            "factor_exposure_violations": result.factor_exposure_violations,
            "solver_status": result.solver_status,
            "iterations": result.iterations,
            "objective_value": result.objective_value,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return path

    # ------------------------------------------------------------
    # 自动调权工具: 从当前持仓+TE 反向求解需要调整的权重
    # ------------------------------------------------------------

    def rebalance_to_te_target(
        self,
        current_weights: list[float] | np.ndarray,
        benchmark_weights: list[float] | np.ndarray,
        cov_matrix: np.ndarray,
        target_te: float,
        max_adjustment: float = 0.05,  # 单标的最多调整 5%
    ) -> dict[str, np.ndarray]:
        """将当前权重调整为满足 TE 目标的权重

        Args:
            current_weights: 当前权重
            benchmark_weights: 基准权重
            cov_matrix: 协方差矩阵
            target_te: 目标跟踪误差
            max_adjustment: 单标的最大调整幅度

        Returns:
            {"new_weights": ..., "adjustments": ..., "expected_te": ...}
        """
        w_cur = np.asarray(current_weights, dtype=float)
        w_bench = np.asarray(benchmark_weights, dtype=float)
        Sigma = np.asarray(cov_matrix, dtype=float)
        active = w_cur - w_bench
        current_te = math.sqrt(float(active @ Sigma @ active))

        if current_te <= 0:
            return {
                "new_weights": w_cur,
                "adjustments": np.zeros_like(w_cur),
                "expected_te": 0.0,  # type: ignore
            }

        # 计算缩放因子
        scale = target_te / current_te
        # 限制单标的调整幅度
        new_active = active * scale
        adjustments = new_active - active

        # 截断超调的调整
        over = np.abs(adjustments) > max_adjustment
        if np.any(over):
            adjustments = np.sign(adjustments) * np.minimum(np.abs(adjustments), max_adjustment)
            new_active = active + adjustments

        new_weights = w_bench + new_active
        # 归一化
        new_weights = np.maximum(new_weights, 0)
        new_weights = new_weights / new_weights.sum() if new_weights.sum() > 0 else w_cur

        # 重算 TE
        new_active_final = new_weights - w_bench
        new_te = math.sqrt(float(new_active_final @ Sigma @ new_active_final))

        return {
            "new_weights": new_weights,
            "adjustments": adjustments,
            "expected_te": new_te,
        }
