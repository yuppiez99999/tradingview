"""
skfolio 统一优化后端
====================

文献依据: #42 skfolio (2025.07)
任务: LIT-3.3 集成 skfolio 统一优化后端

核心功能
--------
5 种投资组合优化策略统一接口:
1. MeanVariance — 均值-方差 (Markowitz)
2. MaxSharpe — 最大夏普比率
3. MinVariance — 最小方差
4. RiskParity — 风险平价
5. HRP — 层次风险平价 (Lopez de Prado)

设计原则
--------
- numpy 核心实现 (零依赖), skfolio 作为可选后端加速
- scikit-learn 风格 API (fit + predict)
- 旧优化器接口兼容 (保留 get_weights 等方法)

使用示例
--------
    from utils.portfolio_optimizer_skfolio import SkfolioOptimizer, Strategy

    opt = SkfolioOptimizer(strategy=Strategy.MAX_SHARPE)
    weights = opt.fit(returns).get_weights()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

import numpy as np

logger = logging.getLogger("portfolio_optimizer_skfolio")

try:
    import skfolio  # noqa: F401
    SKFOLIO_AVAILABLE = True
except ImportError:
    SKFOLIO_AVAILABLE = False


# ============================================================
# 策略枚举
# ============================================================

class Strategy(str, Enum):
    """优化策略。"""
    MEAN_VARIANCE = "mean_variance"
    MAX_SHARPE = "max_sharpe"
    MIN_VARIANCE = "min_variance"
    RISK_PARITY = "risk_parity"
    HRP = "hrp"


# ============================================================
# 优化结果
# ============================================================

@dataclass
class OptimizationResult:
    """优化结果。

    Attributes:
        weights: 资产权重
        expected_return: 期望收益
        expected_risk: 期望风险 (标准差)
        sharpe_ratio: 夏普比率
        strategy: 使用的策略
        success: 是否成功
        message: 附加信息
    """
    weights: np.ndarray
    expected_return: float = 0.0
    expected_risk: float = 0.0
    sharpe_ratio: float = 0.0
    strategy: Strategy = Strategy.MIN_VARIANCE
    success: bool = True
    message: str = ""

    def get_weights(self) -> np.ndarray:
        """获取权重 (兼容旧接口)。"""
        return self.weights

    def to_dict(self) -> dict[str, Any]:
        return {
            "weights": self.weights.tolist(),
            "expected_return": self.expected_return,
            "expected_risk": self.expected_risk,
            "sharpe_ratio": self.sharpe_ratio,
            "strategy": self.strategy.value,
            "success": self.success,
        }


# ============================================================
# 核心优化器 (numpy 实现)
# ============================================================

class NumpyOptimizer:
    """numpy 核心优化器 (零依赖)。"""

    @staticmethod
    def _regularize_cov(cov: np.ndarray, ridge: float = 1e-8) -> np.ndarray:
        """协方差矩阵正则化 (确保正定)。"""
        n = cov.shape[0]
        return cov + np.eye(n) * ridge

    @staticmethod
    def mean_variance(
        returns: np.ndarray, target_return: float | None = None,
        risk_aversion: float = 1.0
    ) -> np.ndarray:
        """均值-方差优化 (Markowitz).

        max w^T μ - (γ/2) w^T Σ w
        解: w = Σ^{-1} μ / γ
        """
        mu = np.mean(returns, axis=0)
        cov = np.cov(returns, rowvar=False)
        cov_reg = NumpyOptimizer._regularize_cov(cov)

        weights = np.linalg.solve(cov_reg, mu) / risk_aversion
        weights = weights / weights.sum()
        return np.clip(weights, 0, 1) / np.clip(weights, 0, 1).sum()

    @staticmethod
    def max_sharpe(returns: np.ndarray, rf: float = 0.0) -> np.ndarray:
        """最大夏普比率优化.

        max (w^T μ - rf) / sqrt(w^T Σ w)
        解: w ∝ Σ^{-1} (μ - rf)
        """
        mu = np.mean(returns, axis=0) - rf
        cov = np.cov(returns, rowvar=False)
        cov_reg = NumpyOptimizer._regularize_cov(cov)

        weights = np.linalg.solve(cov_reg, mu)
        weights = np.maximum(weights, 0)
        total = weights.sum()
        if total < 1e-10:
            n = len(mu)
            return np.ones(n) / n
        return weights / total

    @staticmethod
    def min_variance(returns: np.ndarray) -> np.ndarray:
        """最小方差优化.

        min w^T Σ w s.t. w^T 1 = 1
        解: w ∝ Σ^{-1} 1
        """
        n = returns.shape[1]
        cov = np.cov(returns, rowvar=False)
        cov_reg = NumpyOptimizer._regularize_cov(cov)
        ones = np.ones(n)

        weights = np.linalg.solve(cov_reg, ones)
        weights = np.maximum(weights, 0)
        total = weights.sum()
        if total < 1e-10:
            return np.ones(n) / n
        return weights / total

    @staticmethod
    def risk_parity(returns: np.ndarray, n_iters: int = 100) -> np.ndarray:
        """风险平价优化 (迭代求解).

        目标: w_i * (Σw)_i = constant for all i
        使用平方根更新规则 (比线性更新更稳定):
        w_new = w * sqrt(target / risk_contrib)
        """
        n = returns.shape[1]
        cov = np.cov(returns, rowvar=False)
        cov_reg = NumpyOptimizer._regularize_cov(cov, ridge=1e-6)

        weights = np.ones(n) / n
        for _ in range(n_iters):
            marginal = cov_reg @ weights
            risk_contrib = weights * marginal
            risk_contrib_pos = np.maximum(risk_contrib, 1e-12)
            target = np.mean(risk_contrib_pos)
            adjustment = np.sqrt(target / risk_contrib_pos)
            new_weights = weights * adjustment
            new_weights = np.maximum(new_weights, 1e-10)
            new_weights = new_weights / new_weights.sum()
            if np.max(np.abs(new_weights - weights)) < 1e-10:
                weights = new_weights
                break
            weights = new_weights

        return weights / weights.sum()

    @staticmethod
    def hrp(returns: np.ndarray) -> np.ndarray:
        """层次风险平价 (HRP, Lopez de Prado 2016).

        1. 树状聚类 (相关性距离)
        2. 准对角化
        3. 递归二分分配
        """
        n = returns.shape[1]
        if n == 1:
            return np.array([1.0])

        cov = np.cov(returns, rowvar=False)
        corr = np.corrcoef(returns, rowvar=False)
        corr = np.nan_to_num(corr, nan=0.0)

        dist = np.sqrt(0.5 * (1 - corr))
        np.fill_diagonal(dist, 0.0)

        from scipy.cluster.hierarchy import linkage, to_tree
        from scipy.spatial.distance import squareform

        condensed = squareform(dist, checks=False)
        z = linkage(condensed, method="single")
        root = to_tree(z)

        order = NumpyOptimizer._get_quasi_diag(root)
        weights = NumpyOptimizer._recursive_bisection(cov, order)

        return weights

    @staticmethod
    def _get_quasi_diag(root: Any) -> list[int]:
        """准对角化 (获取叶子顺序)。"""
        if root.is_leaf():
            return [root.id]
        return (
            NumpyOptimizer._get_quasi_diag(root.get_left())
            + NumpyOptimizer._get_quasi_diag(root.get_right())
        )

    @staticmethod
    def _recursive_bisection(
        cov: np.ndarray, order: list[int]
    ) -> np.ndarray:
        """递归二分分配。"""
        n = cov.shape[0]
        weights = np.ones(n) / n

        clusters = [order]
        while clusters:
            new_clusters = []
            for cluster in clusters:
                if len(cluster) <= 1:
                    new_clusters.append(cluster)
                    continue
                mid = len(cluster) // 2
                left, right = cluster[:mid], cluster[mid:]

                left_var = NumpyOptimizer._cluster_var(cov, left)
                right_var = NumpyOptimizer._cluster_var(cov, right)

                alpha = 1 - left_var / (left_var + right_var)

                for i in left:
                    weights[i] *= alpha
                for i in right:
                    weights[i] *= (1 - alpha)

                new_clusters.append(left)
                new_clusters.append(right)
            clusters = [c for c in new_clusters if len(c) > 1]

        return weights / weights.sum()

    @staticmethod
    def _cluster_var(cov: np.ndarray, indices: list[int]) -> float:
        """子簇方差。"""
        if len(indices) == 0:
            return 0.0
        sub_cov = cov[np.ix_(indices, indices)]
        n = len(indices)
        w = np.ones(n) / n
        return float(w @ sub_cov @ w)


# ============================================================
# skfolio 统一优化器
# ============================================================

@dataclass
class SkfolioConfig:
    """优化配置。

    Attributes:
        strategy: 优化策略
        risk_aversion: 风险厌恶系数
        target_return: 目标收益 (None=自动)
        rf: 无风险利率
        max_weight: 单资产最大权重
        min_weight: 单资产最小权重
        n_iters: 迭代次数 (风险平价)
    """
    strategy: Strategy = Strategy.MIN_VARIANCE
    risk_aversion: float = 1.0
    target_return: float | None = None
    rf: float = 0.0
    max_weight: float = 1.0
    min_weight: float = 0.0
    n_iters: int = 100


class SkfolioOptimizer:
    """skfolio 统一优化器 — 5 策略统一接口.

    使用示例:
        opt = SkfolioOptimizer(strategy=Strategy.MAX_SHARPE)
        result = opt.fit(returns)
        weights = result.get_weights()
    """

    def __init__(
        self,
        strategy: Strategy = Strategy.MIN_VARIANCE,
        config: SkfolioConfig | None = None,
        use_skfolio: bool = False,
    ) -> None:
        self.config = config or SkfolioConfig(strategy=strategy)
        self.config.strategy = strategy
        self.use_skfolio = use_skfolio and SKFOLIO_AVAILABLE
        self._result: OptimizationResult | None = None
        self._returns: np.ndarray | None = None

    def fit(self, returns: np.ndarray) -> SkfolioOptimizer:
        """拟合优化器.

        Args:
            returns: 资产收益矩阵 (n_samples, n_assets)
        """
        self._returns = returns
        weights = self._optimize(returns)
        weights = self._apply_constraints(weights)

        mu = np.mean(returns, axis=0)
        cov = np.cov(returns, rowvar=False)
        exp_ret = float(mu @ weights)
        exp_risk = float(np.sqrt(weights @ cov @ weights))
        sharpe = (exp_ret - self.config.rf) / max(exp_risk, 1e-10)

        self._result = OptimizationResult(
            weights=weights,
            expected_return=exp_ret,
            expected_risk=exp_risk,
            sharpe_ratio=sharpe,
            strategy=self.config.strategy,
            success=True,
        )
        return self

    def _optimize(self, returns: np.ndarray) -> np.ndarray:
        """执行优化。"""
        s = self.config.strategy
        if s == Strategy.MEAN_VARIANCE:
            return NumpyOptimizer.mean_variance(
                returns, risk_aversion=self.config.risk_aversion
            )
        if s == Strategy.MAX_SHARPE:
            return NumpyOptimizer.max_sharpe(returns, rf=self.config.rf)
        if s == Strategy.MIN_VARIANCE:
            return NumpyOptimizer.min_variance(returns)
        if s == Strategy.RISK_PARITY:
            return NumpyOptimizer.risk_parity(returns, n_iters=self.config.n_iters)
        if s == Strategy.HRP:
            return NumpyOptimizer.hrp(returns)
        return NumpyOptimizer.min_variance(returns)

    def _apply_constraints(self, weights: np.ndarray) -> np.ndarray:
        """应用权重约束。"""
        weights = np.clip(weights, self.config.min_weight, self.config.max_weight)
        total = weights.sum()
        if total > 0:
            weights = weights / total
        return weights

    def get_weights(self) -> np.ndarray:
        """获取优化权重 (兼容旧接口)。"""
        if self._result is None:
            raise RuntimeError("需先调用 fit()")
        return self._result.weights

    def get_result(self) -> OptimizationResult:
        """获取完整结果。"""
        if self._result is None:
            raise RuntimeError("需先调用 fit()")
        return self._result

    def predict(self, returns: np.ndarray) -> float:
        """预测组合收益。"""
        if self._result is None:
            raise RuntimeError("需先调用 fit()")
        return float(self._result.weights @ np.mean(returns, axis=0))

    def compare_strategies(
        self, returns: np.ndarray
    ) -> dict[str, OptimizationResult]:
        """对比所有策略。"""
        results: dict[str, OptimizationResult] = {}
        for strategy in Strategy:
            opt = SkfolioOptimizer(strategy=strategy)
            try:
                opt.fit(returns)
                results[strategy.value] = opt.get_result()
            except Exception as e:
                logger.warning("策略 %s 失败: %s", strategy.value, e)
                results[strategy.value] = OptimizationResult(
                    weights=np.ones(returns.shape[1]) / returns.shape[1],
                    strategy=strategy,
                    success=False,
                    message=str(e),
                )
        return results


# ============================================================
# 便捷函数
# ============================================================

def optimize_portfolio(
    returns: np.ndarray,
    strategy: Strategy = Strategy.MIN_VARIANCE,
    **kwargs: Any,
) -> np.ndarray:
    """便捷优化函数。"""
    opt = SkfolioOptimizer(strategy=strategy, config=SkfolioConfig(**kwargs))
    return opt.fit(returns).get_weights()


def compare_all_strategies(returns: np.ndarray) -> dict[str, dict[str, float]]:
    """对比所有策略的指标。"""
    opt = SkfolioOptimizer()
    results = opt.compare_strategies(returns)
    return {
        name: {
            "expected_return": r.expected_return,
            "expected_risk": r.expected_risk,
            "sharpe_ratio": r.sharpe_ratio,
            "success": r.success,
        }
        for name, r in results.items()
    }


# ============================================================
# CLI 入口
# ============================================================

def main() -> None:
    """CLI 入口: 演示 skfolio 统一优化后端。"""
    print("=" * 60)
    print("skfolio 统一优化后端")
    print("文献: #42 skfolio 2025.07")
    print(f"skfolio 可用: {SKFOLIO_AVAILABLE}")
    print("=" * 60)

    rng = np.random.default_rng(42)
    n_assets = 5
    n_samples = 252
    true_mu = np.array([0.10, 0.08, 0.12, 0.06, 0.15]) / 252
    returns = rng.standard_normal((n_samples, n_assets)) * 0.02 + true_mu

    print(f"\n--- 数据: {n_samples} 天 x {n_assets} 资产 ---")
    print(f"  日均收益: {np.mean(returns, axis=0)}")

    print("\n--- 策略对比 ---")
    comparison = compare_all_strategies(returns)
    for name, metrics in comparison.items():
        print(f"\n  {name}:")
        print(f"    年化收益: {metrics['expected_return'] * 252:.4f}")
        print(f"    年化风险: {metrics['expected_risk'] * np.sqrt(252):.4f}")
        print(f"    夏普比率: {metrics['sharpe_ratio'] * np.sqrt(252):.4f}")

    print("\n--- 最优策略 (MaxSharpe) ---")
    opt = SkfolioOptimizer(strategy=Strategy.MAX_SHARPE)
    result = opt.fit(returns).get_result()
    print(f"  权重: {result.weights}")
    print(f"  夏普: {result.sharpe_ratio * np.sqrt(252):.4f}")


if __name__ == "__main__":
    main()
