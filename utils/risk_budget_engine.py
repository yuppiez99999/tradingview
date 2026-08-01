"""
风险预算引擎 (Risk Budget Engine)
====================================

世界顶级量化基金标准：事前风险预算先于收益目标。
- 日度组合 VaR 95% 硬约束
- 单标的 VaR 硬约束
- 超预算自动拦截新建仓

对接现有模块：
- utils.risk_metrics.calculate_var
- utils.risk_budget_allocator.RiskBudgetAllocator

用法:
    from utils.risk_budget_engine import RiskBudgetEngine, RiskCheckResult
    engine = RiskBudgetEngine(total_capital=3_000_000)
    result = engine.check_pre_trade(target_portfolio, current_positions, price_data)
    if not result.allowed:
        logger.info(result.violations)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from utils.risk_metrics import calculate_var

logger = logging.getLogger("risk_budget_engine")


@dataclass
class RiskCheckResult:
    """事前风险检查结果"""

    allowed: bool = True
    portfolio_var_95: float = 0.0
    portfolio_var_99: float = 0.0
    single_var: dict[str, float] = field(default_factory=dict)
    budget_usage: float = 0.0
    violations: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "portfolio_var_95": round(self.portfolio_var_95, 6),
            "portfolio_var_99": round(self.portfolio_var_99, 6),
            "single_var": {k: round(v, 6) for k, v in self.single_var.items()},
            "budget_usage": round(self.budget_usage, 4),
            "violations": self.violations,
            "meta": self.meta,
        }


class RiskBudgetEngine:
    """事前风险预算引擎"""

    def __init__(
        self,
        total_capital: float = 3_000_000,
        max_daily_var_95: float = 0.035,
        max_single_var_95: float = 0.012,
        max_drawdown: float = 0.15,
        confidence_level: float = 0.95,
        default_volatility: float = 0.25,
        max_weight: float = 0.15,
    ):
        self.total_capital = float(total_capital)
        self.max_daily_var_95 = float(max_daily_var_95)
        self.max_single_var_95 = float(max_single_var_95)
        self.max_drawdown = float(max_drawdown)
        self.confidence_level = float(confidence_level)
        self.default_volatility = float(default_volatility)
        self.max_weight = float(max_weight)

    # ------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------

    def check_pre_trade(
        self,
        target_portfolio: dict[str, float],
        current_positions: dict[str, dict[str, Any]] | None = None,
        price_data: dict[str, pd.Series] | None = None,
    ) -> RiskCheckResult:
        """交易前风险检查

        Args:
            target_portfolio: {symbol: target_weight} 目标权重
            current_positions: 当前持仓 {symbol: {shares, cost_price}}
            price_data: {symbol: price_series} 价格序列

        Returns:
            RiskCheckResult
        """
        result = RiskCheckResult()
        current_positions = current_positions or {}
        price_data = price_data or {}

        if not target_portfolio:
            result.violations.append("目标组合为空")
            result.allowed = False
            return result

        # 1. 组合 VaR
        port_var_95, port_var_99 = self._portfolio_var(target_portfolio, price_data)
        result.portfolio_var_95 = float(port_var_95)
        result.portfolio_var_99 = float(port_var_99)

        if port_var_95 > self.max_daily_var_95:
            result.violations.append(f"组合VaR 95%={port_var_95:.2%} 超过上限 {self.max_daily_var_95:.2%}")

        # 2. 单标的 VaR
        single_vars = self._single_var(target_portfolio, price_data)
        result.single_var = single_vars
        for symbol, var in single_vars.items():
            if var > self.max_single_var_95:
                result.violations.append(f"单标的 {symbol} VaR 95%={var:.2%} 超过上限 {self.max_single_var_95:.2%}")

        # 3. 集中度检查
        concentration_violations = self._check_concentration(target_portfolio)
        result.violations.extend(concentration_violations)

        # 4. 预算使用率
        result.budget_usage = self._compute_budget_usage(target_portfolio, current_positions)

        # 5. 回撤预算检查（如有当前组合数据）
        dd_violations = self._check_drawdown_budget(current_positions, price_data)
        result.violations.extend(dd_violations)

        result.allowed = len(result.violations) == 0
        if not result.allowed:
            logger.warning("[RiskBudgetEngine] 交易被拦截: %s", result.violations)
        else:
            logger.info(
                "[RiskBudgetEngine] 交易通过: VaR95=%.2f%%, usage=%.2f%%",
                port_var_95,
                result.budget_usage * 100,
            )

        return result

    # ------------------------------------------------------------
    # VaR 计算
    # ------------------------------------------------------------

    def _portfolio_var(
        self,
        target_portfolio: dict[str, float],
        price_data: dict[str, pd.Series],
    ) -> tuple[float, float]:
        """组合 VaR：基于历史收益率 + 权重"""
        returns_list = []
        weights = []
        for symbol, weight in target_portfolio.items():
            series = price_data.get(symbol)
            if series is None or not isinstance(series, pd.Series) or len(series) < 5:
                continue
            ret = series.pct_change().dropna()
            if len(ret) == 0:
                continue
            returns_list.append(ret.values)
            weights.append(weight)

        if not returns_list:
            # 无历史数据，用等权波动率近似
            n = len(target_portfolio)
            if n == 0:
                return 0.0, 0.0
            avg_vol = self.default_volatility / math.sqrt(252)
            port_vol = avg_vol * math.sqrt(n) * math.sqrt(1.0 / n)
            return port_vol * 1.65, port_vol * 2.33

        # 对齐长度
        min_len = min(len(r) for r in returns_list)
        if min_len == 0:
            return 0.0, 0.0
        aligned = [r[-min_len:] for r in returns_list]
        returns_matrix = np.column_stack(aligned)
        w = np.array(weights, dtype=float)
        w = w / (w.sum() if w.sum() > 0 else 1.0)

        port_ret = returns_matrix @ w
        var95 = calculate_var(port_ret, confidence_level=self.confidence_level, method="historical")
        var99 = calculate_var(port_ret, confidence_level=0.99, method="historical")
        return float(var95), float(var99)

    def _single_var(
        self,
        target_portfolio: dict[str, float],
        price_data: dict[str, pd.Series],
    ) -> dict[str, float]:
        """单标的 VaR"""
        result = {}
        for symbol, weight in target_portfolio.items():
            series = price_data.get(symbol)
            if series is None or not isinstance(series, pd.Series) or len(series) < 5:
                vol = self.default_volatility / math.sqrt(252)
                var = vol * 1.65 * abs(weight)
                result[symbol] = float(var)
                continue
            ret = series.pct_change().dropna().values
            if len(ret) == 0:
                result[symbol] = 0.0
                continue
            symbol_var = calculate_var(ret, confidence_level=self.confidence_level, method="historical")
            # BUG-03 修复 (2026-07-31): calculate_var 已统一返回正数, 无需再 abs()
            result[symbol] = float(symbol_var * abs(weight))
        return result

    # ------------------------------------------------------------
    # 集中度与回撤预算
    # ------------------------------------------------------------

    def _check_concentration(self, target_portfolio: dict[str, float]) -> list[str]:
        """集中度检查"""
        violations = []
        for symbol, weight in target_portfolio.items():
            if weight > self.max_weight:
                violations.append(f"单标的 {symbol} 权重 {weight:.2%} 超过 {self.max_weight:.2%}")
        return violations

    def _check_drawdown_budget(
        self,
        current_positions: dict[str, dict[str, Any]],
        price_data: dict[str, pd.Series],
    ) -> list[str]:
        """回撤预算检查（当前组合）"""
        if not current_positions:
            return []

        violations = []
        total_value = 0.0
        values = []
        for symbol, pos in current_positions.items():
            shares = float(pos.get("shares", 0))
            cost = float(pos.get("cost_price", 0))
            if shares <= 0 or cost <= 0:
                continue
            series = price_data.get(symbol)
            price = (
                float(series.iloc[-1])
                if series is not None and isinstance(series, pd.Series) and len(series) > 0
                else cost
            )
            value = shares * price
            values.append(value)
            total_value += value

        if total_value <= 0 or not values:
            return []

        # 简易回撤估算：当前市值 vs 成本
        current_value = sum(
            float(pos.get("shares", 0)) * float(pos.get("cost_price", 0)) for pos in current_positions.values()
        )
        if current_value > 0:
            drawdown = (total_value - current_value) / current_value
            if drawdown < -self.max_drawdown:
                violations.append(f"当前回撤 {drawdown:.2%} 超过预算 {self.max_drawdown:.2%}")
        return violations

    # ------------------------------------------------------------
    # 预算使用率
    # ------------------------------------------------------------

    def _compute_budget_usage(
        self,
        target_portfolio: dict[str, float],
        current_positions: dict[str, dict[str, Any]],
    ) -> float:
        """预算使用率：目标组合风险 / 总资本"""
        port_var, _ = self._portfolio_var(target_portfolio, {})
        return float(port_var / self.total_capital) if self.total_capital > 0 else 0.0
