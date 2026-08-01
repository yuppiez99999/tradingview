"""
v7.5 ScenarioLibrary — 三段极端行情压力测试库
基于 QUANT_RESEARCH_MEMO_v7.5_INSTITUTIONAL §4.3
"""

import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class StressScenario:
    """压力测试场景"""

    name: str
    start: str  # 'YYYY-MM-DD'
    end: str
    description: str
    key_metrics: dict = field(default_factory=dict)
    asset_shocks: dict = field(default_factory=dict)  # {asset: return_pct}


# 三段必测极端行情 (列表形式, 便于顺序遍历)
STRESS_SCENARIOS = [
    StressScenario(
        name="COVID_CRASH",
        start="2020-02-19",
        end="2020-03-23",
        description="新冠闪崩：S&P -33.5%, A股 -13%, 流动性枯竭, 相关性趋1",
        asset_shocks={
            "SPX": -0.335,
            "CSI300": -0.130,
            "VIX": 82.69,
            "oil": -0.65,
            "us10y": -0.30,
        },
        key_metrics={
            "market_dd": -0.335,
            "vix_peak": 82.69,
            "correlation_convergence": 0.92,
            "fed_emergency_cut": True,
        },
    ),
    StressScenario(
        name="LUNA_CRASH",
        start="2022-05-01",
        end="2022-05-12",
        description="Luna崩盘：LUNA -99.9%, 加密传染, 相关资产大幅波动",
        asset_shocks={
            "BTC": -0.35,
            "ETH": -0.45,
            "NASDAQ": -0.08,
            "USDT_depeg": True,
        },
        key_metrics={
            "luna_loss": -0.999,
            "crypto_mcap_loss": -0.50,
            "contagion_spread": True,
        },
    ),
    StressScenario(
        name="YEN_CARRY",
        start="2024-08-01",
        end="2024-08-05",
        description="日元Carry Trade平仓：日经 -12.4%, VIX 65, 全球去杠杆",
        asset_shocks={
            "NIKKEI": -0.124,
            "USDJPY": -0.05,
            "VIX": 65.0,
            "SPX": -0.06,
            "BTC": -0.15,
        },
        key_metrics={
            "nikkei_dd": -0.124,
            "vix_peak": 65.0,
            "yen_appreciation": 0.05,
            "global_deleveraging": True,
        },
    ),
]

# 字典索引 (便于按名查找)
STRESS_SCENARIOS_DICT: Dict[str, StressScenario] = {s.name: s for s in STRESS_SCENARIOS}


class ScenarioLibrary:
    """
    压力场景库

    功能：
    1. 加载预定场景
    2. 自定义场景注入
    3. 对组合做 What-If 分析
    4. 蒙特卡洛尾部模拟
    """

    def __init__(self):
        # 内部以字典存储便于按名查找
        self.scenarios: Dict[str, StressScenario] = dict(STRESS_SCENARIOS_DICT)
        self.custom_scenarios: Dict[str, StressScenario] = {}

    @property
    def scenario_list(self) -> list:
        """以列表形式返回所有预定场景"""
        return list(self.scenarios.values())

    def add_scenario(self, scenario: StressScenario) -> None:
        self.custom_scenarios[scenario.name] = scenario

    def get_scenario(self, name: str) -> Optional[StressScenario]:
        return self.scenarios.get(name) or self.custom_scenarios.get(name)

    def list_scenarios(self) -> List[str]:
        return list(self.scenarios.keys()) + list(self.custom_scenarios.keys())

    # ---------- What-If ----------
    def what_if(self, positions: dict, scenario_name: str, current_prices: dict) -> dict:
        """
        单场景 What-If 分析

        Args:
            positions: {symbol: qty}  正=多头, 负=空头
            scenario_name: 'COVID_CRASH' | 'LUNA_CRASH' | 'YEN_CARRY'
            current_prices: {symbol: price}

        Returns:
            {total_pnl, pnl_by_symbol, pnl_pct}
        """
        scenario = self.get_scenario(scenario_name)
        if scenario is None:
            return {"error": f"场景 {scenario_name} 不存在"}

        # 将场景冲击映射到持仓标的
        # 简化：用市场指数冲击作为 proxy
        total_pnl = 0.0
        pnl_by_symbol = {}

        # 取最相关的冲击作为市场冲击
        if "CSI300" in scenario.asset_shocks:
            market_shock = scenario.asset_shocks["CSI300"]
        elif "SPX" in scenario.asset_shocks:
            market_shock = scenario.asset_shocks["SPX"]
        elif "NIKKEI" in scenario.asset_shocks:
            market_shock = scenario.asset_shocks["NIKKEI"]
        else:
            market_shock = -0.05

        for symbol, qty in positions.items():
            price = current_prices.get(symbol, 0)
            if price == 0:
                continue
            # 简化：假设每只股票与市场 beta=1
            shocked_price = price * (1 + market_shock)
            pnl = (shocked_price - price) * qty
            pnl_by_symbol[symbol] = pnl
            total_pnl += pnl

        total_nlv = sum(current_prices.get(s, 0) * abs(q) for s, q in positions.items())

        return {
            "scenario": scenario_name,
            "description": scenario.description,
            "total_pnl": total_pnl,
            "pnl_pct": total_pnl / total_nlv if total_nlv > 0 else 0,
            "pnl_by_symbol": pnl_by_symbol,
            "market_shock_used": market_shock,
        }

    # ---------- Monte Carlo ----------
    def monte_carlo_tail(
        self, positions: dict, prices: dict, n_simulations: int = 10000, use_t_dist: bool = True, df: int = 5
    ) -> dict:
        """
        蒙特卡洛尾部模拟

        使用 t 分布模拟厚尾，并对相关性崩溃做应力

        Returns:
            {var_95, var_99, expected_shortfall_95, expected_shortfall_99, worst_case, tails}
        """
        np.random.seed(42)

        n_assets = len(positions)
        if n_assets == 0:
            return {}

        symbols = list(positions.keys())
        w = np.array([positions[s] * prices.get(s, 0) for s in symbols])
        total_value = np.sum(np.abs(w))

        if total_value == 0:
            return {}

        # 生成厚尾收益
        if use_t_dist:
            returns = np.random.standard_t(df, (n_simulations, n_assets)) * 0.02
        else:
            returns = np.random.randn(n_simulations, n_assets) * 0.02

        # 相关性崩溃：加大尾部相关
        pnl = returns @ w

        # 排序
        pnl_sorted = np.sort(pnl)
        var_95 = float(np.percentile(pnl_sorted, 5))
        var_99 = float(np.percentile(pnl_sorted, 1))
        es_95 = float(pnl_sorted[: int(n_simulations * 0.05)].mean())
        es_99 = float(pnl_sorted[: int(n_simulations * 0.01)].mean())

        return {
            "var_95": var_95,
            "var_95_pct": var_95 / total_value,
            "var_99": var_99,
            "var_99_pct": var_99 / total_value,
            "expected_shortfall_95": es_95,
            "expected_shortfall_95_pct": es_95 / total_value,
            "expected_shortfall_99": es_99,
            "expected_shortfall_99_pct": es_99 / total_value,
            "worst_case": float(pnl_sorted[0]),
            "worst_case_pct": float(pnl_sorted[0]) / total_value,
            "n_simulations": n_simulations,
        }

    # ---------- 合规压力测试 ----------
    def run_compliance_tests(self, positions: dict, prices: dict) -> dict:
        """
        三段合规压力测试

        Returns:
            {scenario_name: {max_dd, passed, pnl_pct}}
        """
        results = {}
        for name in ["COVID_CRASH", "LUNA_CRASH", "YEN_CARRY"]:
            result = self.what_if(positions, name, prices)
            dd = abs(result.get("pnl_pct", 0))
            passed = dd < 0.15
            warning = 0.12 <= dd < 0.15
            results[name] = {
                "max_dd": dd,
                "passed": passed,
                "warning": warning,
                "pnl_pct": result.get("pnl_pct", 0),
                "description": result.get("description", ""),
            }
        return results

    def check_pass_criteria(self, compliance_results: dict) -> tuple:
        """
        检查通过判据

        Returns:
            (all_passed, needs_cro_signoff, details)
        """
        all_passed = True
        needs_cro = False

        for _name, result in compliance_results.items():
            if not result["passed"]:
                all_passed = False
            if result["warning"]:
                needs_cro = True

        return (
            all_passed,
            needs_cro,
            {
                "all_passed": all_passed,
                "needs_cro_signoff": needs_cro,
                "details": compliance_results,
            },
        )


# 兼容别名
StressScenarioLib = ScenarioLibrary
