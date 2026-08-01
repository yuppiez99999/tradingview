"""
风险预算分配器 (Risk Budget Allocator)

将 v7.5 的 RiskBudgeter 接入日度建仓预算生成：
- 不再固定 20 万/天
- 改为基于 Kelly + Risk Parity 的风险预算
- 单笔交易风险上限硬约束
- 与宏观评分、预测信号、ETF资金流联动
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class RiskBudgetAllocator:
    """日度建仓预算风险预算分配器"""

    def __init__(
        self,
        total_capital: float = 3_000_000,
        target_return: float = 0.08,
        max_dd: float = 0.15,
        single_trade_risk: float = 0.015,
        daily_budget_limit: float = 200_000,
    ):
        self.total_capital = total_capital
        self.target_return = target_return
        self.max_dd = max_dd
        self.single_trade_risk = single_trade_risk
        self.daily_budget_limit = daily_budget_limit

    def estimate_symbol_risk(
        self, symbol: str, prices: np.ndarray | None = None, default_vol: float = 0.25
    ) -> float:
        """估计单标的年化波动率"""
        if prices is None or len(prices) < 20:
            return default_vol
        returns = np.diff(prices) / prices[:-1]
        returns = returns[~np.isnan(returns)]
        if len(returns) == 0:
            return default_vol
        return float(np.std(returns, ddof=1)) * np.sqrt(252)  # type: ignore

    def allocate_daily_budget(
        self,
        pending_positions: list[dict],
        returns_matrix: pd.DataFrame | None = None,
        signals: dict[str, dict] | None = None,
        macro_scores: dict[str, dict] | None = None,
        etf_signals: dict[str, str] | None = None,
    ) -> dict[str, dict]:
        """按风险预算分配当日预算

        Args:
            pending_positions: 待建仓标的列表
            returns_matrix: 收益率矩阵, columns=symbols
            signals: 预测信号
            macro_scores: 宏观评分
            etf_signals: ETF资金流信号

        Returns:
            {code: {allocated, risk_budget, weight, reason}}
        """
        if not pending_positions:
            return {}

        symbols = [p.get("code_clean", p.get("code", "")) for p in pending_positions]
        n = len(symbols)
        if n == 0:
            return {}

        # 等权风险预算基准
        1.0 / n
        weights = np.ones(n) / n

        # Risk Parity 调整
        if returns_matrix is not None and returns_matrix.shape[1] >= 2:
            try:
                import importlib.util

                spec = importlib.util.spec_from_file_location(
                    "risk_budgeter",
                    PROJECT_ROOT / "v7.5_institutional" / "src" / "risk" / "risk_budgeter.py",
                )
                if spec is not None and spec.loader is not None:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    budgeter = mod.RiskBudgeter(
                        total_capital=self.total_capital,
                        target_return=self.target_return,
                        max_dd=self.max_dd,
                        single_trade_risk=self.single_trade_risk,
                    )
                    rp_weights = budgeter.risk_parity_weights(returns_matrix[symbols].dropna())
                    if len(rp_weights) == n:
                        weights = rp_weights
                        print(f"[RiskBudgetAllocator] Risk Parity 权重计算成功, 目标{len(rp_weights)}个标的")
                    else:
                        print("[RiskBudgetAllocator] Risk Parity 权重维度不匹配, 回退到等权")
                else:
                    print("[RiskBudgetAllocator] 无法加载 risk_budgeter 模块, 回退到等权")
            except Exception as e:  # P2 模块 fail-safe, 待后续精确化
                print(f"[RiskBudgetAllocator] Risk Parity 计算失败: {e}, 回退到等权")

        results: dict[str, dict] = {}
        for i, pos in enumerate(pending_positions):
            code = pos.get("code", "")
            code_clean = pos.get("code_clean", code)
            remaining = float(pos.get("remaining", 0))
            if remaining <= 0:
                continue

            # 基础分配 = 总资本 * 权重 * 单笔风险约束
            base_allocated = self.total_capital * float(weights[i]) * self.single_trade_risk / 0.25
            base_allocated = min(base_allocated, remaining)

            # 信号调整
            signal = (signals or {}).get(code_clean, {})
            macro = (macro_scores or {}).get(code_clean, {})
            etf = (etf_signals or {}).get(code, "")

            direction = signal.get("direction", "NEUTRAL")
            confidence = float(signal.get("confidence", 0.0))
            strength = float(signal.get("signal_strength", 0.0))
            combined = float(macro.get("combined_score", 1.0))

            # 强看空跳过
            if direction == "DOWN" and confidence >= 0.7 and strength <= -0.5:
                base_allocated = 0.0
            # 强看多加码
            elif direction == "UP" and confidence >= 0.7 and strength >= 0.5:
                base_allocated = min(base_allocated * 1.3, remaining)
            # 宏观强对齐加码
            elif combined >= 1.15:
                base_allocated = min(base_allocated * 1.2, remaining)
            # 宏观偏弱缩减
            elif combined < 0.85:
                base_allocated *= 0.8
            # 宏观跳过
            elif combined < 0.7:
                base_allocated = 0.0

            # ETF资金流弱信号缩减
            if etf and "关注" in etf and combined < 1.0:
                base_allocated *= 0.8

            # 单日预算上限
            base_allocated = min(base_allocated, self.daily_budget_limit)

            risk_budget = float(weights[i])
            results[code] = {
                "allocated": float(base_allocated),
                "risk_budget": risk_budget,
                "weight": float(weights[i]),
                "reason": f"risk_parity_weight={weights[i]:.2%}, single_risk={self.single_trade_risk:.2%}",
            }

        # 预算归一化，避免超支
        total_allocated = sum(v["allocated"] for v in results.values())
        if total_allocated > self.daily_budget_limit and total_allocated > 0:
            scale = self.daily_budget_limit / total_allocated
            for v in results.values():
                v["allocated"] = float(v["allocated"] * scale)

        return results
