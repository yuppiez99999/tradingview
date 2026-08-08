"""
Black-Litterman 组合优化器 (Black-Litterman Portfolio Optimizer)

世界顶级对冲基金标志性配置方法 (Bridgewater / BlackRock / AQR):
- 市场均衡权重 (Implied Equilibrium Returns) — 反向求解 CAPM 隐含收益
- 观点矩阵 (Views) — 投资者主观观点的数学表达
- 贝叶斯融合 — 市场先验 + 观点后验 = 后验收益分布
- 均值-方差优化 (Mean-Variance Optimization) — Markowitz 框架

公式核心:
    Π = δ × Σ × w_mkt              (市场隐含收益)
    E[R] = [(τΣ)^-1 + P' Ω^-1 P]^-1 × [(τΣ)^-1 Π + P' Ω^-1 Q]   (BL 后验收益)
    w_BL = (δΣ)^-1 × E[R]          (BL 后验权重)

参考:
- Black, F. & Litterman, R. (1991) "Global Asset Allocation with Equities, Bonds, and Currencies"
- Idzorek, T. (2007) "A Step-by-Step Guide to the Black-Litterman Model"
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

# ============================================================
# 数据结构
# ============================================================


@dataclass
class View:
    """投资者主观观点

    Examples:
        # 绝对观点: "标的 A 将上涨 5%"
        View(type="absolute", assets=["A"], weights=[1.0], expected_return=0.05, confidence=0.6)

        # 相对观点: "标的 A 将跑赢 B 3%"
        View(type="relative", assets=["A", "B"], weights=[1.0, -1.0], expected_return=0.03, confidence=0.7)
    """

    type: str  # "absolute" or "relative"
    assets: list[str]
    weights: list[float]  # P 矩阵的行向量
    expected_return: float  # Q 向量元素
    confidence: float = 0.5  # 观点置信度 (0-1), 越高 Ω 越小


@dataclass
class BLResult:
    """Black-Litterman 优化结果"""

    posterior_returns: np.ndarray  # 后验期望收益
    posterior_cov: np.ndarray  # 后验协方差矩阵
    optimal_weights: np.ndarray  # 最优权重
    implied_equilibrium_returns: np.ndarray  # 市场隐含收益 (Π)
    assets: list[str]  # 标的列表
    views: list[View]  # 观点列表
    risk_aversion: float  # 风险厌恶系数 δ

    # 诊断信息
    weight_change_vs_market: np.ndarray  # vs 市场权重变化
    diversification_ratio: float  # 分散化比率
    effective_n: float  # 有效持仓数
    expected_portfolio_return: float  # 组合预期收益
    expected_portfolio_vol: float  # 组合预期波动
    sharpe_ratio: float  # 夏普比率


# ============================================================
# Black-Litterman 优化器
# ============================================================


class BlackLittermanOptimizer:
    """Black-Litterman 组合优化器

    顶级对冲基金标准配置流程:
    1. 计算市场均衡权重 w_mkt (市值加权)
    2. 反向求解市场隐含收益 Π = δΣw_mkt
    3. 收集投资者观点 (P, Q, Ω)
    4. 贝叶斯融合得后验收益 E[R]
    5. 均值-方差优化得最优权重 w_BL

    用法:
        optimizer = BlackLittermanOptimizer(risk_aversion=2.5, tau=0.05)
        result = optimizer.optimize(
            assets=["600519", "000858", "601318"],
            market_weights=[0.5, 0.3, 0.2],
            cov_matrix=cov_np,
            views=[View(type="absolute", assets=["600519"], weights=[1.0],
                       expected_return=0.15, confidence=0.7)],
            risk_free_rate=0.03,
        )
    """

    def __init__(
        self,
        risk_aversion: float = 2.5,  # δ 风险厌恶系数 (典型 2-4)
        tau: float = 0.05,  # τ 观点不确定性缩放 (典型 0.025-0.05)
        default_confidence: float = 0.5,
        use_idzorek_omega: bool = True,  # Idzorek (2007) 置信度→Ω 方法
    ):
        if risk_aversion <= 0:
            raise ValueError(f"risk_aversion 必须 > 0, 实际 {risk_aversion}")
        if not (0 < tau <= 1):
            raise ValueError(f"tau 必须在 (0,1] 区间, 实际 {tau}")
        if not (0 <= default_confidence <= 1):
            raise ValueError("default_confidence 必须在 [0,1] 区间")

        self.delta = float(risk_aversion)
        self.tau = float(tau)
        self.default_confidence = float(default_confidence)
        self.use_idzorek_omega = bool(use_idzorek_omega)

    # ------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------

    def optimize(
        self,
        assets: list[str],
        market_weights: list[float] | np.ndarray,
        cov_matrix: np.ndarray | pd.DataFrame,  # type: ignore[misc]
        views: list[View] | None = None,
        risk_free_rate: float = 0.03,
        target_return: float | None = None,  # None=无约束, 数值=目标收益
        max_weight: float | None = None,  # 单一标的权重上限
        min_weight: float | None = 0.0,  # 单一标的权重下限
    ) -> BLResult:
        """运行 Black-Litterman 优化

        Args:
            assets: 标的代码列表
            market_weights: 市场权重(市值加权) w_mkt
            cov_matrix: 协方差矩阵 Σ (N×N)
            views: 投资者观点列表
            risk_free_rate: 无风险利率 (年化)
            target_return: 可选, 目标组合收益约束
            max_weight: 可选, 单一标的权重上限
            min_weight: 可选, 单一标的权重下限

        Returns:
            BLResult 优化结果
        """
        n = len(assets)
        if n == 0:
            raise ValueError("assets 不能为空")
        if n != len(market_weights):
            raise ValueError(f"assets ({n}) 与 market_weights ({len(market_weights)}) 维度不匹配")

        # 转 numpy
        w_mkt = np.asarray(market_weights, dtype=float)
        if w_mkt.sum() <= 0:
            raise ValueError(f"market_weights 总和必须 > 0, 实际 {w_mkt.sum()}")
        w_mkt = w_mkt / w_mkt.sum()  # 归一化

        cov = self._to_numpy_matrix(cov_matrix)
        if cov.shape != (n, n):
            raise ValueError(f"cov_matrix 维度 {cov.shape} != ({n},{n})")

        # 1. 市场隐含收益 Π = δ Σ w_mkt
        pi = self.delta * cov @ w_mkt

        # 2. 处理观点
        if not views:
            # 无观点: 后验收益 = 隐含收益, 权重 = 市场权重
            posterior_returns = pi.copy()
            posterior_cov = cov.copy()
            P = np.zeros((0, n))
            Q = np.zeros(0)
            omega = np.zeros((0, 0))
        else:
            P, Q, omega = self._build_view_matrices(views, assets, cov)
            # 3. BL 后验收益
            posterior_returns, posterior_cov = self._compute_posterior(pi, cov, P, Q, omega)

        # 4. 均值-方差优化
        optimal_weights = self._mean_variance_optimize(
            posterior_returns,
            posterior_cov,
            risk_free_rate,
            target_return,
            max_weight,
            min_weight,
        )

        # 5. 诊断
        weight_change = optimal_weights - w_mkt
        port_ret = float(optimal_weights @ posterior_returns)
        port_var = float(optimal_weights @ cov @ optimal_weights)
        port_vol = float(np.sqrt(port_var))
        sharpe = (port_ret - risk_free_rate) / port_vol if port_vol > 0 else 0.0
        # 分散化比率 = Σwiσi / (w'Σw)^0.5
        marginal_vols = np.sqrt(np.diag(cov))
        weighted_avg_vol = float(optimal_weights @ marginal_vols)
        diversification_ratio = weighted_avg_vol / port_vol if port_vol > 0 else 1.0
        # 有效持仓数 (1/HHI)
        hhi = float(np.sum(optimal_weights**2))
        effective_n = 1.0 / hhi if hhi > 0 else 0.0

        return BLResult(
            posterior_returns=posterior_returns,
            posterior_cov=posterior_cov,
            optimal_weights=optimal_weights,
            implied_equilibrium_returns=pi,
            assets=list(assets),
            views=list(views or []),
            risk_aversion=self.delta,
            weight_change_vs_market=weight_change,
            diversification_ratio=diversification_ratio,
            effective_n=effective_n,
            expected_portfolio_return=port_ret,
            expected_portfolio_vol=port_vol,
            sharpe_ratio=sharpe,
        )

    # ------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------

    def _build_view_matrices(
        self,
        views: list[View],
        assets: list[str],
        cov: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """构建观点矩阵 P, Q, Ω"""
        n = len(assets)
        k = len(views)
        asset_idx = {a: i for i, a in enumerate(assets)}

        P = np.zeros((k, n))
        Q = np.zeros(k)
        omega_diag = np.zeros(k)

        for i, view in enumerate(views):
            # 验证
            if not view.assets:
                raise ValueError(f"View #{i}: assets 为空")
            if len(view.weights) != len(view.assets):
                raise ValueError(f"View #{i}: weights 维度 {len(view.weights)} != assets {len(view.assets)}")
            if not (0 <= view.confidence <= 1):
                raise ValueError(f"View #{i}: confidence 必须在 [0,1]")

            # 填充 P
            for asset, weight in zip(view.assets, view.weights):
                if asset not in asset_idx:
                    raise ValueError(f"View #{i}: asset '{asset}' 不在 assets 列表中")
                P[i, asset_idx[asset]] = weight

            # 填充 Q
            Q[i] = view.expected_return

            # 计算 Ω (Idzorek 方法)
            if self.use_idzorek_omega:
                # Idzorek (2007): Ω_ii = (1-c) / c × (P Σ P')_ii
                # c = confidence, 越高 Ω 越小 (观点越可信)
                psp = float(P[i] @ cov @ P[i])
                c = max(view.confidence, 1e-6)  # 避免除零
                omega_diag[i] = (1.0 - c) / c * psp
            else:
                # 经典方法: Ω = τ × diag(P Σ P')
                omega_diag[i] = self.tau * float(P[i] @ cov @ P[i])

        # Ω = diag(ω_1, ..., ω_k)
        omega = np.diag(omega_diag)
        return P, Q, omega

    def _compute_posterior(
        self,
        pi: np.ndarray,
        cov: np.ndarray,
        P: np.ndarray,
        Q: np.ndarray,
        omega: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """贝叶斯融合后验收益

        E[R] = [(τΣ)^-1 + P' Ω^-1 P]^-1 × [(τΣ)^-1 Π + P' Ω^-1 Q]
        Σ_post = Σ + [(τΣ)^-1 + P' Ω^-1 P]^-1
        """
        tau_cov = self.tau * cov

        if P.shape[0] == 0:
            return pi.copy(), cov.copy()

        # 处理 Ω 奇异 (使用伪逆)
        try:
            omega_inv = np.linalg.inv(omega)
        except np.linalg.LinAlgError:
            omega_inv = np.linalg.pinv(omega)

        try:
            tau_cov_inv = np.linalg.inv(tau_cov)
        except np.linalg.LinAlgError:
            tau_cov_inv = np.linalg.pinv(tau_cov)

        # M = (τΣ)^-1 + P' Ω^-1 P
        M = tau_cov_inv + P.T @ omega_inv @ P
        try:
            M_inv = np.linalg.inv(M)
        except np.linalg.LinAlgError:
            M_inv = np.linalg.pinv(M)

        # E[R] = M^-1 × [(τΣ)^-1 Π + P' Ω^-1 Q]
        posterior_returns = M_inv @ (tau_cov_inv @ pi + P.T @ omega_inv @ Q)

        # Σ_post = Σ + M^-1
        posterior_cov = cov + M_inv

        return posterior_returns, posterior_cov

    def _mean_variance_optimize(
        self,
        expected_returns: np.ndarray,
        cov: np.ndarray,
        risk_free_rate: float,
        target_return: float | None,
        max_weight: float | None,
        min_weight: float | None,
    ) -> np.ndarray:
        """均值-方差优化

        无约束闭式解: w = (δΣ)^-1 × E[R]
        有约束: 用 scipy.optimize 或投影法
        """
        n = len(expected_returns)

        # 无约束闭式解
        try:
            cov_inv = np.linalg.inv(cov)
        except np.linalg.LinAlgError:
            cov_inv = np.linalg.pinv(cov)

        w_unconstrained = cov_inv @ expected_returns / self.delta

        # 无约束: 检查是否需要归一化 (允许做空则不归一化)
        if max_weight is None and min_weight is None and target_return is None:
            # 简单归一化为满仓
            if w_unconstrained.sum() > 0:
                return w_unconstrained / w_unconstrained.sum()  # type: ignore[misc]
            # 全负则等权
            return np.ones(n) / n

        # 有约束: 使用 scipy
        try:
            from scipy.linalg import cholesky, solve_triangular  # noqa: F401
            from scipy.optimize import minimize

            def neg_sharpe(w):
                ret = float(w @ expected_returns)
                vol = float(np.sqrt(w @ cov @ w))
                return -(ret - risk_free_rate) / vol if vol > 0 else 0.0

            # 约束
            constraints = [{"type": "eq", "fun": lambda w: w.sum() - 1.0}]
            if target_return is not None:
                constraints.append(
                    {
                        "type": "eq",
                        "fun": lambda w: float(w @ expected_returns) - target_return,
                    }
                )

            bounds = []
            for _ in range(n):
                lo = float(min_weight if min_weight is not None else -1.0)
                hi = float(max_weight if max_weight is not None else 1.0)
                bounds.append((lo, hi))

            x0 = np.ones(n) / n  # 等权起点
            res = minimize(
                neg_sharpe,
                x0,
                method="SLSQP",
                bounds=bounds,
                constraints=constraints,
                options={"maxiter": 200, "ftol": 1e-9},
            )
            if res.success:
                w = res.x
                # 归一化 (数值误差)
                if w.sum() > 0:
                    w = w / w.sum()
                return w  # type: ignore[misc]
        except ImportError:
            pass

        # 回退: 截断 + 归一化
        w = w_unconstrained.copy()
        if max_weight is not None:
            w = np.minimum(w, max_weight)
        if min_weight is not None:
            w = np.maximum(w, min_weight)
        if w.sum() > 0:
            return w / w.sum()  # type: ignore[misc]
        return np.ones(n) / n

    def _to_numpy_matrix(self, m) -> np.ndarray:
        """转换输入为 numpy 矩阵"""
        if isinstance(m, np.ndarray):
            return m.astype(float)
        try:
            return np.asarray(m, dtype=float)
        except Exception:  # P2 模块 fail-safe, 待后续精确化  # noqa: BLE001
            return np.array(m, dtype=float)

    # ------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------

    def save_result(self, result: BLResult, path: str | Path) -> Path:
        """保存优化结果到 JSON"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "assets": result.assets,
            "optimal_weights": result.optimal_weights.tolist(),
            "posterior_returns": result.posterior_returns.tolist(),
            "implied_equilibrium_returns": result.implied_equilibrium_returns.tolist(),
            "weight_change_vs_market": result.weight_change_vs_market.tolist(),
            "risk_aversion": result.risk_aversion,
            "expected_portfolio_return": result.expected_portfolio_return,
            "expected_portfolio_vol": result.expected_portfolio_vol,
            "sharpe_ratio": result.sharpe_ratio,
            "diversification_ratio": result.diversification_ratio,
            "effective_n": result.effective_n,
            "views": [
                {
                    "type": v.type,
                    "assets": v.assets,
                    "weights": v.weights,
                    "expected_return": v.expected_return,
                    "confidence": v.confidence,
                }
                for v in result.views
            ],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return path
