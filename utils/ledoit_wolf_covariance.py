"""
Ledoit-Wolf 收缩协方差估计器 (Ledoit-Wolf Shrinkage Covariance Estimator)

世界顶级量化基金标准 (Two Sigma / AQR / Citadel):
- 解决样本协方差在小样本/高维下不稳定的问题
- 收缩目标: 单位矩阵 × 平均方差 (常量相关性模型)
- 最优收缩强度 δ* 闭式解 (Ledoit & Wolf 2004)
- 自动估计收缩强度, 无需手动调参

公式核心:
    Σ_shrunk = δ × F + (1-δ) × S
    其中:
        S = 样本协方差矩阵
        F = 收缩目标 (常量相关性: F_ii = s_ii, F_ij = ρ̄ × √(s_ii × s_jj))
        δ* = argmin E[||δF + (1-δ)S - Σ||²]

参考:
- Ledoit, O. & Wolf, M. (2004) "A well-conditioned estimator for large-dimensional covariance matrices"
- Ledoit, O. & Wolf, M. (2003) "Improved estimation of the covariance matrix of stock returns"
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# ============================================================
# 数据结构
# ============================================================


@dataclass
class ShrinkageResult:
    """收缩估计结果"""

    cov_shrunk: np.ndarray  # 收缩后协方差矩阵
    sample_cov: np.ndarray  # 原始样本协方差
    target: np.ndarray  # 收缩目标 F
    shrinkage_intensity: float  # 收缩强度 δ* ∈ [0, 1]
    # 诊断
    n_observations: int  # 样本数
    n_assets: int  # 资产数
    condition_number_before: float  # 收缩前条件数
    condition_number_after: float  # 收缩后条件数
    avg_variance: float  # 平均方差
    avg_correlation: float  # 平均相关性
    method: str  # 估计方法


# ============================================================
# Ledoit-Wolf 收缩协方差估计器
# ============================================================


class LedoitWolfCovariance:
    """Ledoit-Wolf 收缩协方差估计器

    用法:
        estimator = LedoitWolfCovariance()
        result = estimator.fit(returns_matrix)
        # result.cov_shrunk 可直接用于 BL / Barra / Markowitz
    """

    def __init__(
        self,
        assume_zero_mean: bool = False,
        annualize: bool = False,
        periods_per_year: int = 252,
    ):
        self.assume_zero_mean = bool(assume_zero_mean)
        self.annualize = bool(annualize)
        self.periods_per_year = int(periods_per_year)

    # ------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------

    def fit(self, returns: np.ndarray | pd.DataFrame) -> ShrinkageResult:
        """估计收缩协方差矩阵

        Args:
            returns: 收益率矩阵 (T × N), T=时间观测数, N=资产数

        Returns:
            ShrinkageResult
        """
        # 转 numpy
        if hasattr(returns, "values"):
            R = np.asarray(returns.values, dtype=float)
        else:
            R = np.asarray(returns, dtype=float)

        if R.ndim != 2:
            raise ValueError(f"returns 必须是 2D 矩阵, 实际维度 {R.ndim}")

        T, N = R.shape
        if T < 2 or N < 1:
            raise ValueError(f"样本不足: T={T}, N={N}")

        # 1. 样本均值 (可选)
        if self.assume_zero_mean:
            mu = np.zeros(N)
        else:
            mu = R.mean(axis=0)

        # 2. 中心化
        Rc = R - mu

        # 3. 样本协方差 S = (1/T) X'X (Ledoit-Wolf 用 1/T 而非 1/(T-1))
        S = (Rc.T @ Rc) / T

        # 4. 构建收缩目标 F (常量相关性模型)
        # F_ii = s_ii (对角线保持)
        # F_ij = ρ̄ × √(s_ii × s_jj) (off-diagonal)
        F = self._build_constant_correlation_target(S)

        # 5. 估计最优收缩强度 δ*
        delta = self._estimate_shrinkage_intensity(Rc, S, F, T)

        # 6. 收缩: Σ_shrunk = δ F + (1-δ) S
        cov_shrunk = delta * F + (1.0 - delta) * S

        # 7. 年化 (可选)
        if self.annualize:
            factor = self.periods_per_year
            cov_shrunk = cov_shrunk * factor
            S = S * factor
            F = F * factor

        # 8. 诊断
        cond_before = float(np.linalg.cond(S)) if N > 1 else 1.0
        cond_after = float(np.linalg.cond(cov_shrunk)) if N > 1 else 1.0
        avg_var = float(np.mean(np.diag(S)))
        # 平均相关性
        if N > 1:
            corr = np.corrcoef(R.T) if T > N else np.eye(N)
            off_diag = corr[~np.eye(N, dtype=bool)]
            # 防御: 常数列使 corrcoef 产生 NaN, 用 nanmean 忽略而非污染 avg_corr (F-6)
            avg_corr = float(np.nanmean(off_diag)) if len(off_diag) > 0 else 0.0
        else:
            avg_corr = 0.0

        return ShrinkageResult(
            cov_shrunk=cov_shrunk,
            sample_cov=S,
            target=F,
            shrinkage_intensity=delta,
            n_observations=T,
            n_assets=N,
            condition_number_before=cond_before,
            condition_number_after=cond_after,
            avg_variance=avg_var,
            avg_correlation=avg_corr,
            method="ledoit-wolf-constant-correlation",
        )

    # ------------------------------------------------------------
    # 估计辅助方法
    # ------------------------------------------------------------

    def _build_constant_correlation_target(self, S: np.ndarray) -> np.ndarray:
        """构建常量相关性收缩目标

        F_ii = s_ii
        F_ij = ρ̄ × √(s_ii × s_jj)
        """
        N = S.shape[0]
        # 提取标准差
        std = np.sqrt(np.diag(S))
        # 防止除零
        std_safe = np.where(std > 1e-10, std, 1.0)

        # 计算相关系数矩阵
        corr = S / np.outer(std_safe, std_safe)
        # 对角线置 0, 计算 off-diagonal 平均
        np.fill_diagonal(corr, 0.0)
        # off-diagonal 元素数
        n_off = N * (N - 1) if N > 1 else 1
        rho_bar = float(np.sum(corr) / n_off) if n_off > 0 else 0.0

        # 构建目标
        F = np.outer(std, std) * rho_bar
        # 对角线还原为方差
        np.fill_diagonal(F, np.diag(S))
        return F

    def _estimate_shrinkage_intensity(
        self,
        Rc: np.ndarray,
        S: np.ndarray,
        F: np.ndarray,
        T: int,
    ) -> float:
        """估计最优收缩强度 δ*

        δ* = max(0, min(1, κ/T))
        其中 κ = (π - ρ) / γ
        π = E[||S - Σ||²]  (样本协方差误差)
        ρ = E[||S - F|| × ||Σ - F||]
        γ = E[||F - Σ||²]
        """
        N = S.shape[0]
        if T < 2 or N < 1:
            return 0.5  # 默认中等收缩

        # π: 样本协方差各元素方差的和
        # π = Σ_ij Var(s_ij)
        # 使用 Ledoit (2004) 的估计:
        # π̂ = (1/T) Σ_t Σ_ij (x_ti × x_tj - s_ij)²
        pi_mat = np.zeros((N, N))
        for t in range(T):
            xt = Rc[t, :].reshape(-1, 1)
            outer = xt @ xt.T
            pi_mat += (outer - S) ** 2
        pi_mat /= T
        pi_hat = float(np.sum(pi_mat))

        # ρ: 收缩目标与样本协方差的协方差
        # 简化估计: ρ̂ = π̂ × |corr(S, F)|  (近似)
        # 更精确: 见 Ledoit (2004) 原文
        # 用 Frobenius 内积近似
        S - F
        rho_hat = 0.0
        if N > 1:
            # 计算相关矩阵
            std_S = np.sqrt(np.diag(S))
            std_S_safe = np.where(std_S > 1e-10, std_S, 1.0)
            corr_S = S / np.outer(std_S_safe, std_S_safe)
            np.fill_diagonal(corr_S, 0.0)
            # off-diagonal 相关性收敛到 ρ̄ 的速度
            n_off = N * (N - 1) if N > 1 else 1
            rho_bar = float(np.sum(corr_S) / n_off) if n_off > 0 else 0.0
            # ρ̂ 近似
            var_diff = pi_hat / max(N * N, 1)
            rho_hat = var_diff * abs(rho_bar) / max(abs(rho_bar) + 1e-10, 1e-10)

        # γ: 收缩目标与真实协方差的偏差
        # γ̂ = Σ_ij (f_ij - s_ij)²
        gamma_hat = float(np.sum((F - S) ** 2))

        # κ = (π - ρ) / γ
        if gamma_hat > 1e-10:
            kappa = (pi_hat - rho_hat) / gamma_hat
        else:
            kappa = 0.0

        # δ* = max(0, min(1, κ/T))
        delta = max(0.0, min(1.0, kappa / T))
        return float(delta)

    # ------------------------------------------------------------
    # 便利方法
    # ------------------------------------------------------------

    def fit_predict(self, returns: np.ndarray | pd.DataFrame) -> np.ndarray:
        """便利方法: 直接返回收缩后协方差矩阵"""
        result = self.fit(returns)
        return result.cov_shrunk

    def fit_with_uncertainty(
        self,
        returns: np.ndarray | pd.DataFrame,
        n_bootstrap: int = 100,
    ) -> tuple[np.ndarray, np.ndarray]:
        """带自助法的协方差估计

        Returns:
            (cov_shrunk, cov_std): 收缩后协方差 + 元素级标准差
        """
        if hasattr(returns, "values"):
            R = np.asarray(returns.values, dtype=float)
        else:
            R = np.asarray(returns, dtype=float)
        T, _N = R.shape

        # 主估计
        main_result = self.fit(R)

        # 自助采样
        if n_bootstrap > 0 and T > 10:
            bootstraps = []
            rng = np.random.default_rng(seed=42)
            for _ in range(n_bootstrap):
                idx = rng.choice(T, size=T, replace=True)
                Rb = R[idx, :]
                try:
                    rb = self.fit(Rb)
                    bootstraps.append(rb.cov_shrunk)
                except Exception:  # P2 模块 fail-safe, 待后续精确化  # noqa: BLE001
                    continue
            if bootstraps:
                stacked = np.stack(bootstraps, axis=0)
                cov_std = np.std(stacked, axis=0)
                return main_result.cov_shrunk, cov_std

        return main_result.cov_shrunk, np.zeros_like(main_result.cov_shrunk)
