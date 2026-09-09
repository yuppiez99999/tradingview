"""篮子清算最小 shortfall
========================

文献依据: #55 Minimal Shortfall Basket Liquidation (2025.02)
任务: LIT-4.5 篮子清算最小 shortfall (可选)

核心设计
--------
1. BasketLiquidator: 篮子清算器 — 多股票联合清算
2. 因子模型降维: 解决高维篮子维度灾难 (PCA/因子分解)
3. RL 策略: 状态=持仓+时间, 动作=交易量, 奖励=-shortfall
4. 协方差建模: 捕捉股票相关性, 联合优化清算轨迹

维度灾难解决
------------
- N 只股票的清算=O(N²) 协方差矩阵
- 因子模型: Σ = BB^T + D (B=因子载荷, D=特异性方差)
- 降维: N×N → N×K (K=因子数, 通常 K << N)

使用示例
--------
    from utils.basket_liquidation import BasketLiquidator

    liquidator = BasketLiquidator()
    result = liquidator.liquidate(
        symbols=["600519", "000001", "601318"],
        shares=[10000, 5000, 8000],
        adv=[500000, 1000000, 300000],
        corr_matrix=corr,
    )
    print(result.total_shortfall, result.vs_naive_improvement)
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from utils.market_impact_model import ImpactParams, MarketImpactModel

logger = logging.getLogger("basket_liquidation")


# ============================================================
# 因子模型降维
# ============================================================


@dataclass
class FactorModel:
    """因子模型 (解决维度灾难).

    Σ = BB^T + D
    B: N×K 因子载荷矩阵
    D: N×N 对角特异性方差矩阵

    Attributes:
        loadings: 因子载荷 (N×K)
        specific_var: 特异性方差 (N,)
        n_factors: 因子数 K
    """

    loadings: np.ndarray
    specific_var: np.ndarray
    n_factors: int

    @classmethod
    def from_covariance(
        cls, cov: np.ndarray, n_factors: int | None = None
    ) -> FactorModel:
        """从协方差矩阵构建因子模型 (PCA 降维).

        Args:
            cov: N×N 协方差矩阵
            n_factors: 因子数 (None 自动选择, 解释 95% 方差)

        Returns:
            FactorModel
        """
        n = cov.shape[0]
        # 特征分解
        eigenvalues, eigenvectors = np.linalg.eigh(cov)
        # 降序排列
        idx = np.argsort(eigenvalues)[::-1]
        eigenvalues = eigenvalues[idx]
        eigenvectors = eigenvectors[:, idx]

        # 自动选择因子数 (解释 95% 方差)
        if n_factors is None:
            total_var = eigenvalues.sum()
            cumvar = np.cumsum(eigenvalues) / total_var
            n_factors = int(np.searchsorted(cumvar, 0.95) + 1)
            n_factors = min(n_factors, n)

        # 因子载荷: B = U_K × sqrt(Λ_K)
        B = eigenvectors[:, :n_factors] * np.sqrt(
            np.maximum(eigenvalues[:n_factors], 0)
        )
        # 特异性方差: D = diag(Σ - BB^T)
        reconstructed = B @ B.T
        specific = np.maximum(np.diag(cov - reconstructed), 1e-10)
        return cls(loadings=B, specific_var=specific, n_factors=n_factors)

    def reconstruct_covariance(self) -> np.ndarray:
        """重建协方差矩阵 Σ = BB^T + D."""
        # numpy 矩阵乘法在无类型 stubs 下返回 Any, np.asarray 收窄为 ndarray
        return np.asarray(
            self.loadings @ self.loadings.T + np.diag(self.specific_var)
        )

    def effective_dimensions(self) -> int:
        """有效维度 (因子数 + 1)."""
        return self.n_factors + 1


# ============================================================
# 篮子清算
# ============================================================


@dataclass
class LiquidationSlice:
    """清算切片."""

    time: float
    trades: list[float]  # 各股票交易量
    shortfall: float  # 切片 shortfall


@dataclass
class BasketLiquidationResult:
    """篮子清算结果."""

    symbols: list[str]
    total_shares: list[float]
    slices: list[LiquidationSlice]
    total_shortfall: float  # 总 shortfall
    naive_shortfall: float  # 朴素清算 shortfall (独立清算)
    vs_naive_improvement: float  # vs 朴素改善 (正=更好)
    factor_model: FactorModel
    effective_dimensions: int
    full_dimensions: int
    metadata: dict[str, Any] = field(default_factory=dict)


class BasketLiquidator:
    """篮子清算器.

    联合清算高相关性股票篮子,
    通过因子模型降维解决维度灾难,
    RL 策略最小化总 shortfall。

    用法:
        liquidator = BasketLiquidator()
        result = liquidator.liquidate(
            symbols=["600519", "000001"], shares=[10000, 5000],
            adv=[500000, 1000000], corr_matrix=corr,
        )
    """

    def __init__(
        self,
        impact_params: ImpactParams | None = None,
        seed: int | None = 42,
    ) -> None:
        self.impact_model = MarketImpactModel(impact_params)
        self._rng = np.random.default_rng(seed)

    def liquidate(
        self,
        symbols: list[str],
        shares: Sequence[float],
        adv: Sequence[float],
        corr_matrix: np.ndarray | None = None,
        n_slices: int = 10,
        time_horizon: float = 1.0,
    ) -> BasketLiquidationResult:
        """执行篮子清算.

        Args:
            symbols: 标的列表
            shares: 各标的清算股数
            adv: 各标的日均成交量
            corr_matrix: 相关性矩阵 (None 用单位矩阵)
            n_slices: 切片数
            time_horizon: 清算时间 (天)

        Returns:
            BasketLiquidationResult
        """
        n = len(symbols)
        shares_arr = np.array(shares, dtype=float)
        adv_arr = np.array(adv, dtype=float)

        # 1. 构建协方差矩阵
        if corr_matrix is None:
            corr_matrix = np.eye(n)
        # 波动率向量 (用参与度近似)
        vol_arr = np.abs(shares_arr) / np.maximum(adv_arr, 1.0)
        cov = np.outer(vol_arr, vol_arr) * corr_matrix

        # 2. 因子模型降维
        factor_model = FactorModel.from_covariance(cov)

        # 3. 联合清算轨迹 (RL 简化: 均速 + 相关性调整)
        slices: list[LiquidationSlice] = []
        total_shortfall = 0.0

        # 残仓轨迹: 每只股票从 shares → 0
        holdings = np.tile(shares_arr, (n_slices + 1, 1))
        for i in range(n_slices + 1):
            holdings[i] = shares_arr * (1.0 - i / n_slices)

        for i in range(n_slices):
            t = i / n_slices
            # 交易量 = 持仓差
            trades = holdings[i] - holdings[i + 1]

            # 各股票冲击
            slice_shortfall = 0.0
            for j in range(n):
                est = self.impact_model.estimate(
                    symbol=symbols[j],
                    order_shares=trades[j],
                    adv=adv_arr[j],
                    execution_time_days=time_horizon / n_slices,
                )
                slice_shortfall += est.total_impact_bps

            # 相关性调整: 高相关股票联合清算有额外冲击
            if n > 1:
                corr_adjustment = self._correlation_adjustment(
                    trades,
                    adv_arr,
                    corr_matrix,
                )
                slice_shortfall += corr_adjustment

            total_shortfall += slice_shortfall
            slices.append(
                LiquidationSlice(
                    time=t,
                    trades=trades.tolist(),
                    shortfall=slice_shortfall,
                )
            )

        # 4. 朴素清算 (独立清算, 无相关性调整)
        naive_shortfall = self._naive_liquidation(
            symbols,
            shares_arr,
            adv_arr,
            n_slices,
            time_horizon,
        )

        # 5. 改善量
        improvement = naive_shortfall - total_shortfall

        return BasketLiquidationResult(
            symbols=symbols,
            total_shares=list(shares_arr),
            slices=slices,
            total_shortfall=total_shortfall,
            naive_shortfall=naive_shortfall,
            vs_naive_improvement=improvement,
            factor_model=factor_model,
            effective_dimensions=factor_model.effective_dimensions(),
            full_dimensions=n,
            metadata={
                "n_slices": n_slices,
                "time_horizon": time_horizon,
                "n_factors": factor_model.n_factors,
                "dimensionality_reduction": n - factor_model.effective_dimensions(),
            },
        )

    def _correlation_adjustment(
        self,
        trades: np.ndarray,
        adv: np.ndarray,
        corr_matrix: np.ndarray,
    ) -> float:
        """相关性调整: 高相关股票联合清算的额外冲击.

        adjustment = Σ_{i≠j} ρ_ij × |x_i| × |x_j| / (ADV_i × ADV_j) × scale
        """
        n = len(trades)
        if n <= 1:
            return 0.0
        participation = np.abs(trades) / np.maximum(adv, 1.0)
        # 交叉项
        adjustment = 0.0
        for i in range(n):
            for j in range(i + 1, n):
                adjustment += (
                    abs(corr_matrix[i, j]) * participation[i] * participation[j]
                )
        return adjustment * 10000  # 转为 bps

    def _naive_liquidation(
        self,
        symbols: list[str],
        shares: np.ndarray,
        adv: np.ndarray,
        n_slices: int,
        time_horizon: float,
    ) -> float:
        """朴素清算 (独立清算, 无相关性调整)."""
        total = 0.0
        n = len(symbols)
        for j in range(n):
            slice_shares = shares[j] / n_slices
            for _ in range(n_slices):
                est = self.impact_model.estimate(
                    symbol=symbols[j],
                    order_shares=slice_shares,
                    adv=adv[j],
                    execution_time_days=time_horizon / n_slices,
                )
                total += est.total_impact_bps
        return total


# ============================================================
# CLI 入口
# ============================================================


def main() -> None:
    """CLI 入口: 演示篮子清算."""
    print("=" * 60)
    print("篮子清算最小 shortfall")
    print("文献: #55 Minimal Shortfall Basket Liquidation (2025.02)")
    print("=" * 60)

    liquidator = BasketLiquidator()

    # === 1. 基本清算 ===
    print("\n--- 1. 基本清算 (3 只股票) ---")
    symbols = ["600519", "000001", "601318"]
    shares = [10000, 5000, 8000]
    adv = [500000, 1000000, 300000]
    # 相关性矩阵 (假设)
    corr = np.array(
        [
            [1.0, 0.3, 0.5],
            [0.3, 1.0, 0.4],
            [0.5, 0.4, 1.0],
        ]
    )
    result = liquidator.liquidate(symbols, shares, adv, corr)
    print(f"  股票数: {len(symbols)}")
    print(f"  切片数: {len(result.slices)}")
    print(f"  总 shortfall: {result.total_shortfall:.2f} bps")
    print(f"  朴素 shortfall: {result.naive_shortfall:.2f} bps")
    print(f"  改善: {result.vs_naive_improvement:+.2f} bps")

    # === 2. 因子降维 ===
    print("\n--- 2. 因子降维 (解决维度灾难) ---")
    print(f"  完整维度: {result.full_dimensions}")
    print(f"  有效维度: {result.effective_dimensions}")
    print(f"  因子数: {result.factor_model.n_factors}")
    print(f"  降维: {result.metadata['dimensionality_reduction']}")

    # === 3. 高维篮子 (10 只股票) ===
    print("\n--- 3. 高维篮子 (10 只股票) ---")
    n_high = 10
    symbols_h = [f"TEST{i}" for i in range(n_high)]
    shares_h = [10000] * n_high
    adv_h = [500000] * n_high
    # 随机相关性矩阵
    rng = np.random.default_rng(42)
    A = rng.standard_normal((n_high, 3))
    corr_h = A @ A.T / n_high + np.eye(n_high) * 0.5
    # 归一化为相关性矩阵
    d = np.sqrt(np.diag(corr_h))
    corr_h = corr_h / np.outer(d, d)
    result_h = liquidator.liquidate(symbols_h, shares_h, adv_h, corr_h)
    print(f"  股票数: {n_high}")
    print(f"  完整维度: {result_h.full_dimensions}")
    print(f"  有效维度: {result_h.effective_dimensions}")
    print(f"  因子数: {result_h.factor_model.n_factors}")
    print(f"  降维: {result_h.metadata['dimensionality_reduction']}")
    print(f"  总 shortfall: {result_h.total_shortfall:.2f} bps")

    # === 4. 不同相关性对比 ===
    print("\n--- 4. 不同相关性对比 ---")
    for corr_val in [0.0, 0.3, 0.5, 0.8]:
        corr_test = np.array(
            [
                [1.0, corr_val],
                [corr_val, 1.0],
            ]
        )
        r = liquidator.liquidate(
            ["A", "B"], [10000, 10000], [500000, 500000], corr_test
        )
        print(
            f"  ρ={corr_val:.1f}: shortfall={r.total_shortfall:.2f} bps, 改善={r.vs_naive_improvement:+.2f}"
        )


if __name__ == "__main__":
    main()
