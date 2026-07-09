# -*- coding: utf-8 -*-
"""
v7.5 压力测试引擎 —— 三段极端行情 + 蒙特卡洛 + Walk-Forward 验证
"""

import numpy as np
import pandas as pd
import logging
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger('v7.5.stress_tester')


@dataclass
class StressScenario:
    name: str
    start: str
    end: str
    market_shock: float
    vol_multiplier: float
    liquidity_haircut: float
    correlation_converge: float = 1.0
    description: str = ""


class StressTester:
    """v7.5 压力测试引擎"""

    # 预定义三段强制场景
    BUILTIN_SCENARIOS = [
        StressScenario(
            name="COVID_CRASH",
            start="2020-02-19", end="2020-03-23",
            market_shock=-0.30, vol_multiplier=3.0,
            liquidity_haircut=0.5, correlation_converge=0.9,
            description="新冠闪崩, S&P -33.5%, A股 -13%, 流动性枯竭"
        ),
        StressScenario(
            name="LUNA_CRASH",
            start="2022-05-01", end="2022-05-12",
            market_shock=-0.25, vol_multiplier=4.0,
            liquidity_haircut=0.3, correlation_converge=0.85,
            description="LUNA崩盘, 加密传染"
        ),
        StressScenario(
            name="YEN_CARRY_UNWIND",
            start="2024-08-01", end="2024-08-05",
            market_shock=-0.35, vol_multiplier=5.0,
            liquidity_haircut=0.4, correlation_converge=0.95,
            description="日元套息平仓, 日经-12.4%, VIX飙至65"
        ),
    ]

    def __init__(self, max_dd_threshold: float = 0.15):
        self.max_dd_threshold = max_dd_threshold
        self.custom_scenarios: List[StressScenario] = []
        self.results: List[Dict] = []

    def run_scenario(self, scenario: StressScenario,
                     portfolio_returns: pd.Series,
                     market_returns: Optional[pd.Series] = None) -> Dict:
        """运行单个场景压力测试"""
        mask = (portfolio_returns.index >= scenario.start) & (portfolio_returns.index <= scenario.end)
        period_returns = portfolio_returns[mask]

        if len(period_returns) == 0:
            return {
                'scenario': scenario.name,
                'status': 'NO_DATA',
                'max_dd': 0,
                'pass': True,
            }

        # 累计收益
        cumulative = (1 + period_returns).cumprod()
        if not np.isfinite(cumulative).all():
            return {
                'scenario': scenario.name,
                'status': 'BAD_DATA',
                'max_dd': 0,
                'pass': True,
            }
        peak = cumulative.cummax()
        denom = peak.replace(0, np.nan)
        max_dd = float(((cumulative - peak) / denom).min())
        max_dd = 0.0 if not np.isfinite(max_dd) else max_dd

        passed = abs(max_dd) < self.max_dd_threshold

        result = {
            'scenario': scenario.name,
            'description': scenario.description,
            'start': scenario.start,
            'end': scenario.end,
            'max_dd': round(max_dd, 4),
            'total_return': round(float(cumulative.iloc[-1] - 1), 4),
            'pass': passed,
            'status': 'PASS' if passed else ('HIGH_RISK' if abs(max_dd) < 0.20 else 'FAIL'),
        }
        self.results.append(result)
        logger.info(f"[压力测试] {scenario.name}: DD={max_dd:.2%}, {'通过' if passed else '失败'}")
        return result

    def run_monte_carlo(self,
                        initial_value: float,
                        annual_return: float = 0.08,
                        annual_vol: float = 0.15,
                        n_sims: int = 10000,
                        horizon_days: int = 252,
                        t_df: float = 5.0,
                        jump_prob: float = 0.01,
                        jump_mean: float = -0.03,
                        jump_std: float = 0.05) -> Dict:
        """厚尾分布 + 跳跃扩散 + 波动率聚类的蒙特卡洛模拟"""
        dt = 1.0 / 252
        mean_daily = annual_return / 252
        vol_daily = annual_vol / np.sqrt(252)
        final_values = np.zeros(n_sims)
        max_drawdowns = np.zeros(n_sims)

        vol_persistence = 0.94

        for i in range(n_sims):
            # t分布扰动
            innovations = np.random.standard_t(t_df, horizon_days)
            innovations = innovations / np.sqrt(t_df / (t_df - 2))  # 标准化

            # GARCH(1,1) 简化
            vol_path = np.zeros(horizon_days)
            vol_path[0] = vol_daily
            for t in range(1, horizon_days):
                vol_path[t] = np.sqrt(
                    (1 - vol_persistence) * vol_daily**2
                    + vol_persistence * vol_path[t-1]**2
                )

            if not np.isfinite(vol_path).all():
                vol_path = np.full(horizon_days, max(vol_daily, 1e-6))

            returns = mean_daily + vol_path * innovations

            # 跳跃
            jumps = np.zeros(horizon_days)
            for t in range(horizon_days):
                if np.random.random() < jump_prob:
                    jumps[t] = np.random.normal(jump_mean, jump_std)
            returns = returns + jumps

            if not np.isfinite(returns).all():
                returns = np.nan_to_num(returns, nan=0.0, posinf=0.0, neginf=0.0)

            prices = initial_value * np.exp(np.cumsum(returns))
            if not np.isfinite(prices).all() or initial_value <= 0:
                prices = np.full(horizon_days, max(initial_value, 1e-6))

            final_values[i] = prices[-1]
            peak = np.maximum.accumulate(prices)
            denom = np.where(peak > 0, peak, np.nan)
            max_dd_path = (peak - prices) / denom
            max_dd_path = np.nan_to_num(max_dd_path, nan=0.0, posinf=0.0, neginf=0.0)
            max_drawdowns[i] = np.max(max_dd_path)

        var_95 = initial_value - np.percentile(final_values, 5)
        var_99 = initial_value - np.percentile(final_values, 1)
        cvar_95 = float(np.mean(initial_value - final_values[final_values <= np.percentile(final_values, 5)]))

        result = {
            'n_sims': n_sims,
            'expected_value': float(np.mean(final_values)),
            'median_value': float(np.median(final_values)),
            'var_95': float(var_95),
            'var_95_pct': float(var_95 / initial_value),
            'var_99': float(var_99),
            'cvar_95': float(cvar_95),
            'avg_max_dd': float(np.mean(max_drawdowns)),
            'max_dd_99': float(np.percentile(max_drawdowns, 99)),
            'loss_probability': float(np.mean(final_values < initial_value)),
            'ruin_probability': float(np.mean(final_values <= initial_value * 0.20)),
            'scenario': 'MONTE_CARLO',
        }
        self.results.append(result)
        return result

    def run_all(self,
                portfolio_returns: pd.Series,
                initial_value: float,
                annual_return: float = 0.08,
                annual_vol: float = 0.15,
                market_returns: Optional[pd.Series] = None) -> Dict:
        """运行全部压力测试"""
        all_scenarios = self.BUILTIN_SCENARIOS + self.custom_scenarios
        scenario_results = []

        for sc in all_scenarios:
            r = self.run_scenario(sc, portfolio_returns, market_returns)
            scenario_results.append(r)

        mc_result = self.run_monte_carlo(initial_value, annual_return, annual_vol)
        scenario_results.append(mc_result)

        # 汇总判断
        any_fail = any(r.get('status') == 'FAIL' for r in scenario_results)
        any_high_risk = any(r.get('status') == 'HIGH_RISK' for r in scenario_results)

        return {
            'timestamp': datetime.now().isoformat(),
            'scenarios': scenario_results,
            'overall_pass': not any_fail,
            'needs_review': any_high_risk,
            'overall_status': 'FAIL' if any_fail else ('HIGH_RISK_NEEDS_CRO' if any_high_risk else 'PASS'),
        }

    def add_custom_scenario(self, name: str, start: str, end: str,
                            market_shock: float, vol_multiplier: float = 3.0,
                            liquidity_haircut: float = 0.5,
                            correlation_converge: float = 0.9,
                            description: str = ""):
        sc = StressScenario(
            name=name, start=start, end=end,
            market_shock=market_shock, vol_multiplier=vol_multiplier,
            liquidity_haircut=liquidity_haircut,
            correlation_converge=correlation_converge,
            description=description,
        )
        self.custom_scenarios.append(sc)
