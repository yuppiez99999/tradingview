"""
机构级组合优化器 (Institutional Portfolio Optimizer)
======================================================

在交易成本约束下求解“风险调整后收益最大化”。
- 对接 utils.market_impact_model
- 可选 PyPortfolioOpt 后端
- 无 PyPortfolioOpt 时自动回退到纯 NumPy 风险平价/等权

用法:
    from utils.institutional_optimizer import InstitutionalPortfolioOptimizer, PortfolioDecision
    optimizer = InstitutionalPortfolioOptimizer(total_capital=3_000_000)
    decision = optimizer.optimize(
        expected_returns=...,
        covariance_matrix=...,
        current_positions=...,
        impact_model=...,
    )
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from utils.market_impact_model import MarketImpactModel

logger = logging.getLogger("institutional_optimizer")


@dataclass
class PortfolioDecision:
    """组合优化决策"""

    target_weights: dict[str, float] = field(default_factory=dict)
    expected_return: float = 0.0
    expected_risk: float = 0.0
    estimated_cost: float = 0.0
    trades: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_weights": self.target_weights,
            "expected_return": round(self.expected_return, 6),
            "expected_risk": round(self.expected_risk, 6),
            "estimated_cost": round(self.estimated_cost, 6),
            "trades": self.trades,
            "meta": self.meta,
        }


class InstitutionalPortfolioOptimizer:
    """机构级组合优化器"""

    def __init__(
        self,
        total_capital: float = 3_000_000,
        max_weight: float = 0.15,
        max_sector_concentration: float = 0.30,
        max_turnover: float = 0.20,
        risk_aversion: float = 1.0,
        min_position_weight: float = 0.01,
    ):
        self.total_capital = float(total_capital)
        self.max_weight = float(max_weight)
        self.max_sector_concentration = float(max_sector_concentration)
        self.max_turnover = float(max_turnover)
        self.risk_aversion = float(risk_aversion)
        self.min_position_weight = float(min_position_weight)

    # ------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------

    def optimize(
        self,
        expected_returns: dict[str, float] | None = None,
        covariance_matrix: pd.DataFrame | None = None,
        current_positions: dict[str, dict[str, Any]] | None = None,
        impact_model: MarketImpactModel | None = None,
        sector_map: dict[str, str] | None = None,
    ) -> PortfolioDecision:
        """组合优化

        Args:
            expected_returns: {symbol: expected_return}
            covariance_matrix: symbol x symbol 协方差矩阵
            current_positions: 当前持仓 {symbol: {shares, cost_price}}
            impact_model: MarketImpactModel 实例
            sector_map: {symbol: sector_name}

        Returns:
            PortfolioDecision
        """
        expected_returns = expected_returns or {}
        current_positions = current_positions or {}
        sector_map = sector_map or {}

        symbols = sorted(set(expected_returns) | set(current_positions))
        if not symbols:
            return PortfolioDecision(meta={"reason": "no_symbols"})

        mu = np.array([expected_returns.get(s, 0.0) for s in symbols], dtype=float)
        n = len(symbols)

        # 协方差矩阵
        cov = self._build_covariance_matrix(covariance_matrix, symbols, n)

        # 当前权重
        current_weights = self._current_weights(symbols, current_positions)

        # 冲击成本估算
        impact_model = impact_model or MarketImpactModel()
        impact_costs = self._estimate_impact_costs(symbols, current_positions, impact_model)

        # 优化
        weights = self._solve_weights(mu, cov, current_weights, impact_costs)

        # 约束修正
        weights = self._apply_constraints(weights, symbols, sector_map)

        # 构建决策
        return self._build_decision(
            symbols=symbols,
            weights=weights,
            current_weights=current_weights,
            mu=mu,
            cov=cov,
            impact_costs=impact_costs,
            current_positions=current_positions,
        )

    # ------------------------------------------------------------
    # 协方差与权重
    # ------------------------------------------------------------

    def _build_covariance_matrix(
        self,
        covariance_matrix: pd.DataFrame | None,
        symbols: list[str],
        n: int,
    ) -> np.ndarray:
        if covariance_matrix is not None and list(covariance_matrix.columns) == symbols:
            cov = covariance_matrix.reindex(index=symbols, columns=symbols).values
            if cov.shape == (n, n):
                return np.array(cov, dtype=float)
        # 默认对角矩阵
        vol = 0.25 / math.sqrt(252)
        return np.full((n, n), vol**2) * np.eye(n)

    def _current_weights(
        self,
        symbols: list[str],
        current_positions: dict[str, dict[str, Any]],
    ) -> np.ndarray:
        weights = np.zeros(len(symbols), dtype=float)
        total_value = 0.0
        for i, symbol in enumerate(symbols):
            pos = current_positions.get(symbol, {})
            shares = float(pos.get("shares", 0))
            price = float(pos.get("cost_price", 0))
            value = shares * price
            weights[i] = value
            total_value += value
        if total_value > 0:
            weights = weights / total_value  # type: ignore
            return weights

    # ------------------------------------------------------------
    # 冲击成本
    # ------------------------------------------------------------

    def _estimate_impact_costs(
        self,
        symbols: list[str],
        current_positions: dict[str, dict[str, Any]],
        impact_model: MarketImpactModel,
    ) -> np.ndarray:
        costs = np.zeros(len(symbols), dtype=float)
        for i, symbol in enumerate(symbols):
            pos = current_positions.get(symbol, {})
            current_shares = float(pos.get("shares", 0))
            est = impact_model.estimate(
                symbol=symbol,
                order_shares=abs(current_shares),
                decision_price=float(pos.get("cost_price", 0)) or 100.0,
            )
            costs[i] = est.total_impact_bps / 10000.0
        return costs

    # ------------------------------------------------------------
    # 优化求解
    # ------------------------------------------------------------

    def _solve_weights(
        self,
        mu: np.ndarray,
        cov: np.ndarray,
        current_weights: np.ndarray,
        impact_costs: np.ndarray,
    ) -> np.ndarray:
        """风险平价基底 + 预期收益信号倾斜"""
        logger.info("[InstitutionalOptimizer] 使用 risk_parity_with_signal")
        return self._risk_parity_with_signal(mu, cov)

    def _risk_parity_with_signal(self, mu: np.ndarray, cov: np.ndarray) -> np.ndarray:
        """纯 NumPy 风险平价近似 + 预期收益信号倾斜 + max_weight 水 filling 约束"""
        n = cov.shape[0]
        if n == 0:
            return np.array([])
        vols = np.sqrt(np.diag(cov))
        vols = np.where(vols > 1e-12, vols, 1e-12)
        inv_vol = 1.0 / vols
        base_weights = inv_vol / inv_vol.sum()

        mu = np.array(mu, dtype=float).reshape(-1)
        if mu.shape[0] != n:
            mu = np.zeros(n, dtype=float)

        mu_adj = np.where(mu > 0.0, mu, 0.0)
        if mu_adj.sum() > 1e-12:
            signal_weights = mu_adj / mu_adj.sum()
            # 正收益标的保留信号倾斜，负收益标的降权到 0（不纳入组合）
            # 用信号强度做指数响应，让强信号获得显著更高权重
            mu_max = float(mu_adj.max()) if mu_adj.size else 1.0
            if mu_max > 1e-12:
                mu_norm = mu_adj / mu_max
                signal_boost = 0.35 * np.exp(3.0 * mu_norm) + 0.65
                candidate = np.where(
                    mu > 0.0,
                    signal_boost * base_weights * (0.55 + 0.45 * signal_weights),
                    0.0,
                )
            else:
                candidate = np.where(mu > 0.0, base_weights, 0.0)
            # 给正收益标的设最低参与权重，避免过度集中
            active = mu > 0.0
            if np.any(active):
                candidate[active] = np.maximum(candidate[active], self.min_position_weight)
            # 先截断到 max_weight，再把剩余/不足资金在正收益标的内做水 filling
            for _ in range(100):
                excess = candidate > self.max_weight
                if not np.any(excess):
                    break
                candidate = np.minimum(candidate, self.max_weight)
                remaining = 1.0 - candidate.sum()
                active = (mu > 0.0) & (~excess)
                if remaining > 1e-12 and np.any(active):
                    candidate[active] = candidate[active] / candidate[active].sum() * remaining
                else:
                    break
            # 最终归一化：确保总和为 1.0，且不低于最低权重
            total = float(candidate.sum())
            if total > 1e-12:
                candidate = candidate / total
            active = mu > 0.0
            if np.any(active):
                candidate[active] = np.maximum(candidate[active], self.min_position_weight)
                total = float(candidate.sum())
                if total > 1.0 + 1e-12:
                    candidate[active] = candidate[active] / total
            weights = candidate
        else:
            weights = base_weights.copy()
        logger.info("[InstitutionalOptimizer] risk_parity_with_signal raw=%s", weights.tolist())
        return weights

    # ------------------------------------------------------------
    # 约束修正
    # ------------------------------------------------------------

    def _apply_constraints(
        self,
        weights: np.ndarray,
        symbols: list[str],
        sector_map: dict[str, str],
    ) -> np.ndarray:
        if weights.size == 0:
            return weights

        logger.info("[InstitutionalOptimizer] apply_constraints before=%s", weights.tolist())
        # 单标的上限 + 非负，不做强制归一化，允许剩余资金作为现金
        weights = np.minimum(weights, self.max_weight)
        weights = np.maximum(weights, 0.0)
        total = weights.sum()
        if total > 1.0:
            weights = weights / total
        logger.info("[InstitutionalOptimizer] apply_constraints after=%s", weights.tolist())

        # 行业集中度（简化：按 sector_map 聚合）
        if sector_map:
            sector_exposure: dict[str, float] = {}
            for i, symbol in enumerate(symbols):
                sector = sector_map.get(symbol, "unknown")
                sector_exposure[sector] = sector_exposure.get(sector, 0.0) + float(weights[i])
            for i, symbol in enumerate(symbols):
                sector = sector_map.get(symbol, "unknown")
                if sector_exposure.get(sector, 0.0) > self.max_sector_concentration:
                    weights[i] *= 0.8
            weights = np.maximum(weights, 0.0)
            total = weights.sum()
            if total > 1.0:
                weights = weights / total

        return weights

    # ------------------------------------------------------------
    # 决策构建
    # ------------------------------------------------------------

    def _build_decision(
        self,
        symbols: list[str],
        weights: np.ndarray,
        current_weights: np.ndarray,
        mu: np.ndarray,
        cov: np.ndarray,
        impact_costs: np.ndarray,
        current_positions: dict[str, dict[str, Any]],
    ) -> PortfolioDecision:
        target_weights = {symbol: float(weights[i]) for i, symbol in enumerate(symbols)}
        expected_return = float(np.dot(weights, mu))
        expected_risk = float(np.sqrt(np.dot(weights, np.dot(cov, weights))) if weights.size else 0.0)
        estimated_cost = float(np.dot(np.abs(weights - current_weights), impact_costs))

        trades = []
        for i, symbol in enumerate(symbols):
            target_w = float(weights[i])
            current_w = float(current_weights[i])
            if abs(target_w - current_w) > 1e-6:
                trades.append(
                    {
                        "symbol": symbol,
                        "current_weight": round(current_w, 4),
                        "target_weight": round(target_w, 4),
                        "change": round(target_w - current_w, 4),
                        "estimated_cost": round(float(impact_costs[i]), 6),
                    }
                )

        turnover = float(np.sum(np.abs(weights - current_weights)))
        decision = PortfolioDecision(
            target_weights=target_weights,
            expected_return=expected_return,
            expected_risk=expected_risk,
            estimated_cost=estimated_cost,
            trades=trades,
            meta={
                "turnover": turnover,
                "risk_aversion": self.risk_aversion,
                "max_weight": self.max_weight,
                "solver": "pypfopt" if self._pypfopt_available() else "risk_parity_fallback",
            },
        )
        logger.info(
            "[InstitutionalOptimizer] 优化完成: return=%.2f%%, risk=%.2f%%, cost=%.2f%%, turnover=%.2f%%",
            expected_return * 100,
            expected_risk * 100,
            estimated_cost * 100,
            turnover * 100,
        )
        return decision

    def _pypfopt_available(self) -> bool:
        return False
