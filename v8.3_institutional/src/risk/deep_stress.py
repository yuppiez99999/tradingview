"""
v7.6 Deep Stress Test — 多维关联冲击矩阵

对标 Bridgewater All-Weather 压力测试框架:
  - 2008 GFC: 权益-60% / 信用利差+300bp / 商品-40% / 利率-200bp / VIX+400%
  - 2015 A股崩: A股-45% / 人民币-5% / 新兴市场-25% / 商品-15% / VIX+200%
  - 2020 COVID: 权益-34% / 信用利差+200bp / 原油-65% / 利率-100bp / VIX+500%
  - 2022 股债双杀: 权益-25% / 利率+200bp / 信用利差+150bp / 美元+15%
  - 中国房地产: 恒生-40% / 人民币-8% / 中资美元债-30% / 商品-20%
  - 台海危机(假想): A股-50% / 新兴市场-35% / VIX+800% / 原油+30% / 黄金+25%

核心原理: 单一资产冲击过于简化, 实际危机中所有资产同时移动 →
需要定义冲击矩阵明确各资产类别在每种场景中的移动方向和幅度。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger("v76.risk.deep_stress")


@dataclass
class ShockScenario:
    """一个深度压力场景定义"""

    name: str
    description: str = ""
    probability: float = 0.01  # 假想发生概率 (1%=每百年一次)

    # 冲击矩阵: {asset_class: shock_return}
    # 正值=涨，负值=跌
    shocks: dict[str, float] = field(default_factory=dict)

    # 波动率乘数 {asset_class: vol_mult}
    vol_multipliers: dict[str, float] = field(default_factory=dict)

    # 相关性崩溃程度 (1.0=完全趋同)
    correlation_converge: float = 0.95

    # 流动性 haircut (0.5 = 只能以 50% 价格成交)
    liquidity_haircut: float = 0.0


# ============================================================
# 预设冲击矩阵 — 六大深压场景
# ============================================================
DEEP_SHOCK_SCENARIOS = [
    ShockScenario(
        name="GFC_2008",
        description="全球金融危机: 雷曼破产 → 信贷冻结 → 全球崩盘",
        probability=0.01,
        shocks={
            "equity_global": -0.55,
            "equity_cn": -0.72,
            "credit_spread": +0.03,  # +300bp
            "commodity_oil": -0.65,
            "commodity_industrial": -0.45,
            "rates_10y": -0.02,  # -200bp
            "vix": +4.0,  # +400%
            "usd_index": +0.15,
            "gold": +0.05,
        },
        vol_multipliers={
            "equity_global": 5.0,
            "equity_cn": 5.5,
            "vix": 5.0,
        },
        correlation_converge=0.98,
        liquidity_haircut=0.7,
    ),
    ShockScenario(
        name="CHINA_2015_CRASH",
        description="A股股灾: 杠杆牛市破灭 → 千股跌停 → 国家队救市",
        probability=0.02,
        shocks={
            "equity_cn": -0.45,
            "equity_em": -0.25,
            "equity_global": -0.10,
            "commodity_industrial": -0.20,
            "usd_cny": -0.05,  # 人民币贬值 5%
            "rates_cn": -0.01,  # 降息 100bp
            "cn_bond": +0.04,
        },
        vol_multipliers={
            "equity_cn": 4.5,
            "equity_em": 2.0,
        },
        correlation_converge=0.95,
        liquidity_haircut=0.8,
    ),
    ShockScenario(
        name="COVID_2020",
        description="新冠全球大流行: 经济停摆 → 央行无限QE → V型反弹",
        probability=0.02,
        shocks={
            "equity_global": -0.34,
            "equity_cn": -0.13,
            "credit_spread": +0.02,  # +200bp
            "commodity_oil": -0.65,  # 原油暴跌
            "commodity_industrial": -0.30,
            "rates_10y": -0.01,  # -100bp
            "vix": +5.0,  # VIX 飙至 82
            "gold": +0.08,
            "usd_index": +0.08,
        },
        vol_multipliers={
            "equity_global": 3.0,
            "vix": 5.0,
            "commodity_oil": 6.0,
        },
        correlation_converge=0.90,
        liquidity_haircut=0.5,
    ),
    ShockScenario(
        name="DOUBLE_DEATH_2022",
        description="股债双杀: 通胀失控 → 全球央行暴力加息 → 股票和债券同时暴跌",
        probability=0.015,
        shocks={
            "equity_global": -0.20,
            "equity_cn": -0.22,
            "rates_10y": +0.02,  # +200bp
            "rates_2y": +0.025,  # +250bp
            "credit_spread": +0.015,
            "usd_index": +0.18,
            "commodity_industrial": -0.15,
            "gold": -0.05,  # 黄金也跌 (实际利率飙升)
            "cn_bond": -0.03,  # 中债也不能避险
        },
        vol_multipliers={
            "equity_global": 2.5,
            "rates_10y": 3.0,
        },
        correlation_converge=0.80,
        liquidity_haircut=0.2,
    ),
    ShockScenario(
        name="CHINA_PROPERTY_CRISIS",
        description="中国房地产危机深化: 恒大为代表 → 三道红线 → 地方财政紧张",
        probability=0.03,
        shocks={
            "equity_cn": -0.30,
            "equity_hk": -0.45,
            "usd_cny": -0.08,
            "cn_property_bond": -0.40,
            "commodity_industrial": -0.25,  # 工业金属需求骤降
            "cn_bond": +0.05,  # 避险买国债
            "gold": +0.10,
        },
        vol_multipliers={
            "equity_cn": 3.0,
            "equity_hk": 4.0,
        },
        correlation_converge=0.85,
        liquidity_haircut=0.4,
    ),
    ShockScenario(
        name="TAIWAN_STRAIT_CRISIS",
        description="台海地缘危机(假想): 极端尾部风险 — 制裁+禁运+供应链断裂",
        probability=0.003,  # 极低概率
        shocks={
            "equity_cn": -0.55,
            "equity_global": -0.20,
            "equity_em": -0.35,
            "vix": +8.0,
            "commodity_oil": +0.35,
            "commodity_industrial": -0.40,
            "gold": +0.30,
            "usd_cny": -0.15,
            "cn_bond": -0.05,
            "usd_index": +0.10,
        },
        vol_multipliers={
            "equity_cn": 6.0,
            "vix": 8.0,
            "commodity_oil": 4.0,
        },
        correlation_converge=0.99,
        liquidity_haircut=0.9,
    ),
]


class DeepStressTester:
    """
    v7.6 深度压力测试器

    功能:
      - 多维关联冲击矩阵: 同时冲击多个资产类别
      - 情景概率加权: 每个场景标注发生概率
      - 机制条件测试: 分别在不同市场机制下测试
      - CVaR 汇总: 综合各场景的极端损失
      - 压力测试评级: PASS / REVIEW / SHIELD_UP / FAIL

    Usage:
        tester = DeepStressTester(total_nav=5_000_000)
        tester.add_scenario(DEEP_SHOCK_SCENARIOS[0])
        results = tester.run_all(portfolio_exposures)
    """

    def __init__(self, total_nav: float = 5_000_000, cvar_confidence: float = 0.95):
        self.total_nav = total_nav
        self.cvar_confidence = cvar_confidence
        self.scenarios: list[ShockScenario] = []
        self.custom_scenarios: list[ShockScenario] = []

        # 加载预设场景
        self.scenarios = list(DEEP_SHOCK_SCENARIOS)

        # 运行历史
        self.run_history: list[dict] = []

    def add_custom_scenario(self, sc: ShockScenario) -> None:
        self.custom_scenarios.append(sc)

    def resolve_shock(self, scenario: ShockScenario, portfolio_exposures: dict[str, float]) -> dict[str, float]:
        """将冲击情景映射到组合的实际 PnL 影响

        Args:
            scenario: 冲击情景
            portfolio_exposures: {asset_class: notional_exposure}

        Returns:
            {asset_class: pnl_impact}
        """
        impacts = {}
        for asset, notional in portfolio_exposures.items():
            shock = scenario.shocks.get(asset, 0.0)
            vol_mult = scenario.vol_multipliers.get(asset, 1.0)
            impact = notional * shock * vol_mult
            impacts[asset] = float(impact)
        return impacts

    def run_scenario(self, scenario: ShockScenario, portfolio_exposures: dict[str, float]) -> dict:
        """运行单个深度压力场景

        Args:
            scenario: 冲击情景
            portfolio_exposures: {asset_class: notional_exposure}

        Returns:
            详细的压力测试结果
        """
        impacts = self.resolve_shock(scenario, portfolio_exposures)
        total_impact = sum(impacts.values())

        # 流动性 haircut 额外减记
        liquidity_loss = total_impact * scenario.liquidity_haircut

        # 相关性崩溃 → 分散化失效的额外损失
        diversification_benefit_before = sum(abs(v) for v in impacts.values()) - abs(total_impact)
        diversification_benefit_after = diversification_benefit_before * (1 - scenario.correlation_converge)
        corr_loss = diversification_benefit_before - diversification_benefit_after

        net_impact = total_impact - liquidity_loss - corr_loss
        nav_impact_pct = net_impact / max(self.total_nav, 1)

        # 评级
        if abs(nav_impact_pct) > 0.30:
            rating = "FAIL"  # 极端风险: 组合可能清零
        elif abs(nav_impact_pct) > 0.20:
            rating = "SHIELD_UP"  # 高风险: 必须立即对冲
        elif abs(nav_impact_pct) > 0.10:
            rating = "REVIEW"  # 中度风险: 需评审
        else:
            rating = "PASS"

        result = {
            "scenario": scenario.name,
            "description": scenario.description,
            "probability": scenario.probability,
            "per_asset_impact": {k: round(v, 2) for k, v in impacts.items()},
            "total_impact": round(total_impact, 2),
            "liquidity_loss": round(liquidity_loss, 2),
            "correlation_loss": round(corr_loss, 2),
            "net_impact": round(net_impact, 2),
            "nav_impact_pct": round(nav_impact_pct, 4),
            "rating": rating,
            "correlation_converge": scenario.correlation_converge,
            "liquidity_haircut": scenario.liquidity_haircut,
        }

        logger.info(f"[深度压力] {scenario.name}: impact={nav_impact_pct:.1%} -> {rating}")
        return result

    def run_all(self, portfolio_exposures: dict[str, float]) -> dict:
        """运行全部深度压力场景 (预设 + 自定义)

        Args:
            portfolio_exposures: {asset_class: notional_exposure}
                例如: {'equity_cn': 4_000_000, 'cn_bond': 1_000_000}

        Returns:
            完整压力测试报告
        """
        all_scenarios = self.scenarios + self.custom_scenarios
        results = []
        impacts = []

        for sc in all_scenarios:
            r = self.run_scenario(sc, portfolio_exposures)
            results.append(r)
            impacts.append(r["nav_impact_pct"])

        nav = self.total_nav

        # CVaR 计算: 取最差 5% 场景的平均损失
        sorted_impacts = sorted(impacts)
        cvar_cutoff = int(len(sorted_impacts) * (1 - self.cvar_confidence))
        worst_impacts = sorted_impacts[: max(1, cvar_cutoff)]
        cvar_pct = float(np.mean(worst_impacts)) if worst_impacts else 0
        cvar_abs = cvar_pct * nav

        # 概率加权损失
        prob_weighted_loss = sum(r["nav_impact_pct"] * r["probability"] for r in results)

        # 评级汇总
        ratings = {
            "total": len(results),
            "PASS": sum(1 for r in results if r["rating"] == "PASS"),
            "REVIEW": sum(1 for r in results if r["rating"] == "REVIEW"),
            "SHIELD_UP": sum(1 for r in results if r["rating"] == "SHIELD_UP"),
            "FAIL": sum(1 for r in results if r["rating"] == "FAIL"),
        }
        worst = results[np.argmin(impacts)]

        overall = (
            "FAIL"
            if ratings["FAIL"] > 0
            else ("SHIELD_UP" if ratings["SHIELD_UP"] > 2 else ("REVIEW" if ratings["REVIEW"] > 2 else "PASS"))
        )

        report = {
            "timestamp": __import__("datetime").datetime.now().isoformat(),
            "total_nav": nav,
            "n_scenarios": len(results),
            "scenario_results": results,
            "cvar": {
                "pct": round(cvar_pct, 4),
                "abs": round(cvar_abs, 2),
                "confidence": self.cvar_confidence,
            },
            "prob_weighted_loss_pct": round(prob_weighted_loss, 6),
            "ratings": ratings,
            "worst_scenario": worst["scenario"],
            "worst_impact_pct": worst["nav_impact_pct"],
            "overall_rating": overall,
        }

        self.run_history.append(
            {
                "ts": report["timestamp"],
                "overall": overall,
                "cvar": round(cvar_pct, 4),
            }
        )

        return report

    # ---------- 机制条件测试 ----------
    def regime_stress(self, portfolio_exposures: dict[str, float], regime: str = "normal") -> dict:
        """在不同市场机制下筛选适用的场景并测试

        regimes: 'normal' | 'volatility_spike' | 'bear_market' | 'liquidity_crisis'
        """
        regime_filters = {
            "normal": ["GFC_2008"],  # 即使正常也要能承受
            "volatility_spike": ["GFC_2008", "YEN_CARRY_UNWIND", "TAIWAN_STRAIT_CRISIS"],
            "bear_market": ["GFC_2008", "CHINA_2015_CRASH", "DOUBLE_DEATH_2022", "CHINA_PROPERTY_CRISIS"],
            "liquidity_crisis": ["GFC_2008", "CHINA_2016_CIRCUIT", "COVID_2020", "TAIWAN_STRAIT_CRISIS"],
        }

        applicable = regime_filters.get(regime, regime_filters["normal"])
        original = self.scenarios
        self.scenarios = [s for s in original if s.name in applicable]

        try:
            return self.run_all(portfolio_exposures)
        finally:
            self.scenarios = original
