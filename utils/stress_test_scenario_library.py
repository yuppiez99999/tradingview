# -*- coding: utf-8 -*-
"""
压力测试情景库 (Stress Test Scenario Library)

世界顶级对冲基金风险管理标准 (Bridgewater / MSCI / RiskMetrics):
- 8 个历史危机场景 (2008 金融危机/2015 股灾/2020 疫情 等)
- 每个场景包含: 股票/利率/汇率/商品/波动率 5 类冲击因子
- 组合冲击测试: 应用场景到当前持仓, 计算 P&L 冲击
- 自定义场景: 用户可定义任意冲击因子

参考:
- MSCI Stress Test Framework
- RiskMetrics Stress Testing
- Bridgewater "Stress Testing the Portfolio"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import json
import math
import numpy as np


# ============================================================
# 数据结构
# ============================================================

@dataclass
class ShockFactors:
    """单场景冲击因子 (各类资产的收益率冲击)"""
    equity_market: float = 0.0        # 大盘股票冲击 (e.g. -0.30 = -30%)
    equity_growth: float = 0.0       # 成长股额外冲击
    equity_value: float = 0.0        # 价值股额外冲击
    equity_small_cap: float = 0.0    # 小盘股额外冲击
    equity_tech: float = 0.0         # 科技股额外冲击
    equity_finance: float = 0.0      # 金融股额外冲击
    equity_consumer: float = 0.0     # 消费股额外冲击
    equity_energy: float = 0.0       # 能源股额外冲击

    rates_2y: float = 0.0            # 2 年期利率冲击 (e.g. -0.02 = -200bps)
    rates_10y: float = 0.0           # 10 年期利率冲击
    credit_spread: float = 0.0        # 信用利差冲击

    fx_usd_cny: float = 0.0          # USD/CNY 汇率冲击
    fx_usd_eur: float = 0.0           # USD/EUR 汇率冲击

    commodity_gold: float = 0.0      # 黄金价格冲击
    commodity_oil: float = 0.0        # 原油价格冲击
    commodity_copper: float = 0.0    # 铜价冲击
    commodity_iron: float = 0.0      # 铁矿石价格冲击

    volatility_equity: float = 0.0    # 股票波动率冲击 (e.g. 2.0 = 波动率翻倍)
    volatility_rates: float = 0.0      # 利率波动率冲击

    # 基准利率 (无风险利率)
    risk_free_rate: float = 0.0


@dataclass
class StressScenario:
    """压力测试场景"""
    name: str                        # 场景名 (如 "2008金融危机")
    description: str                 # 场景描述
    start_date: str                  # 历史起止日期
    end_date: str
    severity: str                    # "mild" / "moderate" / "severe" / "extreme"
    shocks: ShockFactors             # 冲击因子
    # 历史参考数据
    historical_market_return: float = 0.0  # 历史市场实际收益
    historical_max_drawdown: float = 0.0


@dataclass
class StressTestResult:
    """压力测试结果"""
    scenario_name: str
    portfolio_pnl: float             # 组合 P&L (金额)
    portfolio_return: float          # 组合收益率
    by_asset: Dict[str, float]        # 单标的 P&L
    by_sector: Dict[str, float]       # 行业 P&L
    by_factor: Dict[str, float]      # 因子贡献
    # 风险指标
    var_before: float                 # 压力前 VaR
    var_after: float                  # 压力后 VaR
    var_change: float                # VaR 变化
    # 通过性
    is_breach: bool                   # 是否突破风险限制
    breach_reason: str = ""           # 突破原因


# ============================================================
# 预定义场景库
# ============================================================

def _build_default_scenarios() -> List[StressScenario]:
    """构建默认 8 个历史危机场景"""
    return [
        StressScenario(
            name="2008全球金融危机",
            description="雷曼破产引发的全球系统性金融危机",
            start_date="2008-09-15",
            end_date="2009-03-09",
            severity="extreme",
            shocks=ShockFactors(
                equity_market=-0.45,
                equity_growth=-0.10,
                equity_value=-0.05,
                equity_small_cap=-0.15,
                equity_tech=-0.08,
                equity_finance=-0.25,
                equity_consumer=-0.10,
                equity_energy=-0.20,
                rates_2y=-0.015,
                rates_10y=-0.01,
                credit_spread=0.04,
                fx_usd_cny=0.05,
                commodity_gold=0.10,
                commodity_oil=-0.55,
                commodity_copper=-0.45,
                volatility_equity=2.5,
            ),
            historical_market_return=-0.45,
            historical_max_drawdown=-0.50,
        ),
        StressScenario(
            name="2015中国股灾",
            description="A股场外配资去杠杆引发的股灾",
            start_date="2015-06-15",
            end_date="2015-09-15",
            severity="extreme",
            shocks=ShockFactors(
                equity_market=-0.40,
                equity_growth=-0.15,
                equity_value=-0.05,
                equity_small_cap=-0.25,
                equity_tech=-0.20,
                equity_finance=-0.15,
                equity_consumer=-0.10,
                equity_energy=-0.10,
                rates_2y=-0.005,
                rates_10y=-0.005,
                credit_spread=0.01,
                fx_usd_cny=0.02,
                commodity_gold=-0.05,
                commodity_oil=-0.30,
                commodity_copper=-0.20,
                volatility_equity=2.0,
            ),
            historical_market_return=-0.40,
            historical_max_drawdown=-0.45,
        ),
        StressScenario(
            name="2020新冠疫情",
            description="新冠疫情引发的全球市场恐慌",
            start_date="2020-02-19",
            end_date="2020-03-23",
            severity="severe",
            shocks=ShockFactors(
                equity_market=-0.34,
                equity_growth=-0.05,
                equity_value=-0.10,
                equity_small_cap=-0.15,
                equity_tech=0.05,
                equity_finance=-0.20,
                equity_consumer=-0.10,
                equity_energy=-0.40,
                rates_2y=-0.015,
                rates_10y=-0.01,
                credit_spread=0.03,
                fx_usd_cny=0.02,
                commodity_gold=-0.03,
                commodity_oil=-0.50,
                commodity_copper=-0.15,
                volatility_equity=3.0,
            ),
            historical_market_return=-0.34,
            historical_max_drawdown=-0.34,
        ),
        StressScenario(
            name="2022美联储加息",
            description="美联储激进加息引发的全球资产重新定价",
            start_date="2022-01-01",
            end_date="2022-12-31",
            severity="moderate",
            shocks=ShockFactors(
                equity_market=-0.20,
                equity_growth=-0.15,
                equity_value=0.05,
                equity_small_cap=-0.15,
                equity_tech=-0.25,
                equity_finance=0.05,
                equity_consumer=-0.05,
                equity_energy=0.20,
                rates_2y=0.03,
                rates_10y=0.025,
                credit_spread=0.015,
                fx_usd_cny=-0.05,
                commodity_gold=-0.05,
                commodity_oil=0.10,
                commodity_copper=-0.15,
                volatility_equity=1.5,
            ),
            historical_market_return=-0.20,
            historical_max_drawdown=-0.25,
        ),
        StressScenario(
            name="2018中美贸易战",
            description="中美贸易战升级引发的市场调整",
            start_date="2018-03-22",
            end_date="2018-12-31",
            severity="moderate",
            shocks=ShockFactors(
                equity_market=-0.25,
                equity_growth=-0.10,
                equity_value=-0.05,
                equity_small_cap=-0.15,
                equity_tech=-0.15,
                equity_finance=-0.10,
                equity_consumer=-0.08,
                equity_energy=-0.05,
                rates_2y=0.005,
                rates_10y=0.005,
                credit_spread=0.01,
                fx_usd_cny=0.03,
                commodity_gold=0.05,
                commodity_oil=-0.15,
                commodity_copper=-0.10,
                volatility_equity=1.5,
            ),
            historical_market_return=-0.25,
            historical_max_drawdown=-0.30,
        ),
        StressScenario(
            name="2000互联网泡沫",
            description="互联网泡沫破裂",
            start_date="2000-03-10",
            end_date="2002-10-09",
            severity="extreme",
            shocks=ShockFactors(
                equity_market=-0.45,
                equity_growth=-0.30,
                equity_value=0.10,
                equity_small_cap=-0.20,
                equity_tech=-0.60,
                equity_finance=-0.20,
                equity_consumer=-0.10,
                equity_energy=0.10,
                rates_2y=-0.03,
                rates_10y=-0.02,
                credit_spread=0.02,
                commodity_gold=0.15,
                commodity_oil=0.20,
                volatility_equity=2.0,
            ),
            historical_market_return=-0.45,
            historical_max_drawdown=-0.50,
        ),
        StressScenario(
            name="2024日元套利平仓",
            description="日元套利交易平仓引发的全球震荡",
            start_date="2024-08-01",
            end_date="2024-08-05",
            severity="mild",
            shocks=ShockFactors(
                equity_market=-0.08,
                equity_growth=-0.05,
                equity_value=-0.03,
                equity_small_cap=-0.10,
                equity_tech=-0.08,
                equity_finance=-0.05,
                equity_consumer=-0.03,
                equity_energy=-0.05,
                rates_2y=-0.002,
                rates_10y=-0.002,
                credit_spread=0.005,
                fx_usd_cny=0.01,
                commodity_gold=0.03,
                commodity_oil=-0.05,
                volatility_equity=2.0,
            ),
            historical_market_return=-0.08,
            historical_max_drawdown=-0.12,
        ),
        StressScenario(
            name="流动性危机",
            description="极端流动性枯竭场景 (假设)",
            start_date="",
            end_date="",
            severity="extreme",
            shocks=ShockFactors(
                equity_market=-0.15,
                equity_growth=-0.10,
                equity_value=-0.05,
                equity_small_cap=-0.20,
                equity_tech=-0.10,
                equity_finance=-0.20,
                equity_consumer=-0.08,
                equity_energy=-0.15,
                rates_2y=0.01,
                rates_10y=0.005,
                credit_spread=0.08,
                fx_usd_cny=0.05,
                commodity_gold=-0.10,
                commodity_oil=-0.20,
                commodity_copper=-0.25,
                volatility_equity=4.0,
            ),
            historical_market_return=-0.15,
            historical_max_drawdown=-0.20,
        ),
    ]


# ============================================================
# 压力测试引擎
# ============================================================

class StressTestEngine:
    """压力测试情景库引擎

    用法:
        engine = StressTestEngine()
        results = engine.run_all_scenarios(
            positions=[{"code": "600519", "amount": 100000, "sector": "食品饮料", ...}],
            total_portfolio_value=5000000,
        )
        # 查看最严重场景
        worst = engine.get_worst_scenario(results)
    """

    def __init__(
        self,
        var_confidence: float = 0.95,
        risk_threshold: float = -0.10,  # -10% 视为风险突破
    ):
        self.var_confidence = float(var_confidence)
        self.risk_threshold = float(risk_threshold)
        self.scenarios = _build_default_scenarios()

    # ------------------------------------------------------------
    # 运行压力测试
    # ------------------------------------------------------------

    def run_scenario(
        self,
        scenario: StressScenario,
        positions: List[Dict],
        total_portfolio_value: float,
    ) -> StressTestResult:
        """对单个场景运行压力测试

        Args:
            scenario: 压力测试场景
            positions: 持仓列表 [{"code": ..., "amount": ..., "sector": ..., "style": ..., "type": ...}]
            total_portfolio_value: 组合总市值

        Returns:
            StressTestResult
        """
        shocks = scenario.shocks
        by_asset: Dict[str, float] = {}
        by_sector: Dict[str, float] = {}
        by_factor: Dict[str, float] = {
            "equity_market": 0.0,
            "equity_style": 0.0,
            "rates": 0.0,
            "credit": 0.0,
            "commodity": 0.0,
            "fx": 0.0,
        }

        total_pnl = 0.0

        for pos in positions:
            code = str(pos.get("code", ""))
            amount = float(pos.get("amount", 0))
            if amount <= 0 or not code:
                continue

            sector = str(pos.get("sector", "未知"))
            style = str(pos.get("style", "")).lower()
            asset_type = str(pos.get("type", "STOCK")).upper()

            # 根据资产类型计算冲击
            asset_pnl = 0.0

            if asset_type in ("STOCK", "ETF"):
                # 大盘冲击
                market_shock = shocks.equity_market
                by_factor["equity_market"] += amount * market_shock
                asset_pnl += amount * market_shock

                # 风格额外冲击
                style_extra = 0.0
                if "growth" in style or "成长" in style:
                    style_extra += shocks.equity_growth
                if "value" in style or "价值" in style:
                    style_extra += shocks.equity_value
                if "small" in style or "小盘" in style:
                    style_extra += shocks.equity_small_cap
                if style_extra != 0:
                    by_factor["equity_style"] += amount * style_extra
                    asset_pnl += amount * style_extra

                # 行业额外冲击
                sector_extra = 0.0
                if "科技" in sector or "电子" in sector or "计算机" in sector:
                    sector_extra += shocks.equity_tech
                elif "金融" in sector or "银行" in sector:
                    sector_extra += shocks.equity_finance
                elif "消费" in sector or "食品" in sector:
                    sector_extra += shocks.equity_consumer
                elif "能源" in sector or "采掘" in sector:
                    sector_extra += shocks.equity_energy
                if sector_extra != 0:
                    by_factor["equity_style"] += amount * sector_extra
                    asset_pnl += amount * sector_extra

            elif asset_type == "BOND":
                # 债券冲击: 利率上升 = 价格下跌 (久期假设 5 年)
                duration = 5.0
                rate_shock = shocks.rates_10y
                bond_pnl = -amount * duration * rate_shock
                credit_pnl = -amount * shocks.credit_spread * 3.0  # 信用利差
                by_factor["rates"] += bond_pnl
                by_factor["credit"] += credit_pnl
                asset_pnl += bond_pnl + credit_pnl

            elif asset_type == "GOLD":
                asset_pnl += amount * shocks.commodity_gold
                by_factor["commodity"] += amount * shocks.commodity_gold

            elif asset_type == "FUTURES":
                asset_pnl += amount * shocks.equity_market
                by_factor["equity_market"] += amount * shocks.equity_market

            by_asset[code] = asset_pnl
            by_sector[sector] = by_sector.get(sector, 0.0) + asset_pnl
            total_pnl += asset_pnl

        portfolio_return = total_pnl / total_portfolio_value if total_portfolio_value > 0 else 0.0

        # VaR 估算 (简化: 假设正态分布, σ × z)
        z = 1.645 if self.var_confidence == 0.95 else 2.326
        # 估算日波动率 (基准 1.5%)
        base_vol = 0.015
        stressed_vol = base_vol * max(shocks.volatility_equity, 1.0)
        var_before = total_portfolio_value * base_vol * z
        var_after = total_portfolio_value * stressed_vol * z

        breach = portfolio_return < self.risk_threshold
        breach_reason = ""
        if breach:
            breach_reason = f"组合收益 {portfolio_return:.2%} 低于阈值 {self.risk_threshold:.2%}"

        return StressTestResult(
            scenario_name=scenario.name,
            portfolio_pnl=total_pnl,
            portfolio_return=portfolio_return,
            by_asset=by_asset,
            by_sector=by_sector,
            by_factor=by_factor,
            var_before=var_before,
            var_after=var_after,
            var_change=var_after - var_before,
            is_breach=breach,
            breach_reason=breach_reason,
        )

    def run_all_scenarios(
        self,
        positions: List[Dict],
        total_portfolio_value: float,
    ) -> List[StressTestResult]:
        """对所有预定义场景运行压力测试"""
        return [self.run_scenario(s, positions, total_portfolio_value) for s in self.scenarios]

    # ------------------------------------------------------------
    # 自定义场景
    # ------------------------------------------------------------

    def add_custom_scenario(self, scenario: StressScenario) -> None:
        """添加自定义场景"""
        self.scenarios.append(scenario)

    def create_custom_shock(
        self,
        name: str,
        description: str,
        shocks: ShockFactors,
        severity: str = "moderate",
    ) -> StressScenario:
        """创建自定义冲击场景"""
        scenario = StressScenario(
            name=name,
            description=description,
            start_date="",
            end_date="",
            severity=severity,
            shocks=shocks,
        )
        self.add_custom_scenario(scenario)
        return scenario

    # ------------------------------------------------------------
    # 分析工具
    # ------------------------------------------------------------

    def get_worst_scenario(
        self,
        results: List[StressTestResult],
    ) -> Optional[StressTestResult]:
        """获取最严重场景"""
        if not results:
            return None
        return min(results, key=lambda r: r.portfolio_return)

    def get_breached_scenarios(
        self,
        results: List[StressTestResult],
    ) -> List[StressTestResult]:
        """获取突破风险阈值的场景"""
        return [r for r in results if r.is_breach]

    def summarize(
        self,
        results: List[StressTestResult],
    ) -> Dict:
        """汇总压力测试结果"""
        if not results:
            return {"n_scenarios": 0}

        worst = self.get_worst_scenario(results)
        breaches = self.get_breached_scenarios(results)

        return {
            "n_scenarios": len(results),
            "worst_scenario": worst.scenario_name if worst else "",
            "worst_return": worst.portfolio_return if worst else 0.0,
            "worst_pnl": worst.portfolio_pnl if worst else 0.0,
            "best_return": max(r.portfolio_return for r in results),
            "avg_return": sum(r.portfolio_return for r in results) / len(results),
            "n_breaches": len(breaches),
            "breach_scenarios": [r.scenario_name for r in breaches],
            "avg_var_change": sum(r.var_change for r in results) / len(results),
        }

    # ------------------------------------------------------------
    # 保存
    # ------------------------------------------------------------

    def save_results(
        self,
        results: List[StressTestResult],
        path: Union[str, Path],
    ) -> Path:
        """保存压力测试结果到 JSON"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "results": [
                {
                    "scenario": r.scenario_name,
                    "portfolio_pnl": r.portfolio_pnl,
                    "portfolio_return": r.portfolio_return,
                    "by_sector": r.by_sector,
                    "by_factor": r.by_factor,
                    "var_before": r.var_before,
                    "var_after": r.var_after,
                    "var_change": r.var_change,
                    "is_breach": r.is_breach,
                    "breach_reason": r.breach_reason,
                } for r in results
            ],
            "summary": self.summarize(results),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return path
