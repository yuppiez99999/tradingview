"""
交易成本模型 (Transaction Cost Model) — v8.3.2 滑点分层升级

Claude Audit 2026-07-22 改进项 #2 — 滑点按市值分组 (评级 B+ → A)

顶级对冲基金标准组件：
- 滑点模型 (Slippage) — 按市值分层 (大/中/小/微盘)
- 佣金模型 (Commission)
- 市场冲击成本模型 (Market Impact) — Square-Root Law
- 策略容量估算 (Capacity Model) — NEW
- 综合交易成本估算

供执行算法和组合优化使用。

分层滑点依据:
    - 沪深300成分: 2-5 bps (日均成交量大, 盘口深)
    - 中证500成分: 5-10 bps
    - 中证1000+国证2000: 10-20 bps
    - 微盘/非指数: 20-50 bps (流动性最差)
    基于 AQR/Two Sigma 2023 交易成本白皮书及 A 股实证。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class MarketCapTier(Enum):
    """市值分组枚举"""

    LARGE = "large"  # 沪深300成分
    MID = "mid"  # 中证500成分
    SMALL = "small"  # 中证1000/国证2000
    MICRO = "micro"  # 微盘/非指数成分


# 分层滑点参数 (bps) — 基于 A 股实盘统计
SLIPPAGE_BY_TIER: dict[MarketCapTier, float] = {
    MarketCapTier.LARGE: 3.0,
    MarketCapTier.MID: 7.0,
    MarketCapTier.SMALL: 15.0,
    MarketCapTier.MICRO: 35.0,
}

# 分层冲击系数 — 平方根法则的系数随市值递减
IMPACT_COEFF_BY_TIER: dict[MarketCapTier, float] = {
    MarketCapTier.LARGE: 0.0006,
    MarketCapTier.MID: 0.0010,
    MarketCapTier.SMALL: 0.0020,
    MarketCapTier.MICRO: 0.0050,
}


def classify_market_cap_tier(market_cap: float | None = None, symbol: str | None = None) -> MarketCapTier:
    """根据市值或股票代码分组。

    简化规则:
        - >= 500亿: LARGE
        - 100-500亿: MID
        - 20-100亿: SMALL
        - < 20亿 或 未知: MICRO

    后期可接入中证指数成分股列表做精确分类。
    """
    if market_cap is None:
        return MarketCapTier.MICRO
    cap_yi = market_cap / 1e8  # 转为亿
    if cap_yi >= 500:
        return MarketCapTier.LARGE
    elif cap_yi >= 100:
        return MarketCapTier.MID
    elif cap_yi >= 20:
        return MarketCapTier.SMALL
    else:
        return MarketCapTier.MICRO


@dataclass
class CostParameters:
    """成本参数 (v8.3.2: 分层滑点 + 容量模型)"""

    # --- 分层滑点 (按 MarketCapTier 覆盖默认值) ---
    default_slippage_bps: float = 10.0
    slippage_by_tier: dict[MarketCapTier, float] = field(default_factory=lambda: dict(SLIPPAGE_BY_TIER))
    impact_coeff_by_tier: dict[MarketCapTier, float] = field(default_factory=lambda: dict(IMPACT_COEFF_BY_TIER))

    slippage_nonlinear_exp: float = 1.2  # 滑点非线性指数 (>1 表示大单滑点加速)
    commission_rate: float = 0.00025  # 佣金率，万2.5 (v8.3.2: 下调至机构实际水平)
    stamp_tax_rate: float = 0.0005  # 印花税，卖出 0.05% (万5)
    transfer_fee: float = 0.00001  # 过户费，双边 0.001%
    min_commission: float = 5.0  # 最低佣金
    impact_coeff: float = 0.0015  # 冲击成本系数 (默认, 会被分层覆盖)
    impact_exponent: float = 0.75  # 冲击成本指数
    impact_volatility_adj: float = 0.5  # 冲击成本波动率调整系数
    participation_rate: float = 0.15  # 参与率上限
    opportunity_cost_rate: float = 0.0001  # 机会成本率 (日度)
    delay_cost_per_hour: float = 0.00005  # 每小时延迟成本

    # --- 容量模型参数 (NEW) ---
    max_pct_of_adv: float = 0.05  # 单日交易不超过 ADV 的 5%
    daily_turnover_threshold: float = 0.005  # 日均换手率阈值 (低于0.5%不可做大额交易)


class TransactionCostModel:
    """交易成本模型 (v8.3.2: 分层滑点)"""

    def __init__(self, params: CostParameters | None = None):
        self.params = params or CostParameters()

    # -----------------------------------------------------------
    # 滑点分层
    # -----------------------------------------------------------
    def get_slippage_bps(self, tier: MarketCapTier) -> float:
        """获取指定市值分层的滑点 (bps)"""
        return self.params.slippage_by_tier.get(tier, self.params.default_slippage_bps)

    def get_impact_coeff(self, tier: MarketCapTier) -> float:
        """获取指定市值分层的冲击系数"""
        return self.params.impact_coeff_by_tier.get(tier, self.params.impact_coeff)

    def estimate_slippage(
        self,
        notional: float,
        volatility: float = 0.02,
        tier: MarketCapTier | None = None,
        market_cap: float | None = None,
    ) -> float:
        """滑点成本 (v8.3.2: 分层滑点模型)

        公式: slippage = notional * (tier_bps/10000) * (1 + volatility_adj)

        Args:
            notional: 名义金额
            volatility: 年化波动率
            tier: 市值分组 (优先使用)
            market_cap: 市值 (用于自动分组)
        """
        if tier is None:
            tier = classify_market_cap_tier(market_cap)
        tier_bps = self.get_slippage_bps(tier)
        base_slippage = notional * tier_bps / 10000.0
        volatility_adj = 1.0 + self.params.impact_volatility_adj * max(volatility - 0.02, 0.0) / 0.02
        return base_slippage * volatility_adj

    def estimate_commission(self, notional: float, side: str = "BUY") -> float:
        """佣金成本 (v8.3.2: 含印花税+过户费)

        A股完整费用结构:
        - 佣金: 双边万2.5
        - 印花税: 卖出万5 (仅卖出)
        - 过户费: 双边万0.1
        """
        commission = notional * self.params.commission_rate
        stamp_tax = notional * self.params.stamp_tax_rate if side.upper() in ("SELL", "SHORT") else 0.0
        transfer = notional * self.params.transfer_fee
        return max(commission, self.params.min_commission) + stamp_tax + transfer

    def estimate_impact(
        self,
        notional: float,
        avg_daily_volume: float,
        volatility: float = 0.02,
        tier: MarketCapTier | None = None,
        market_cap: float | None = None,
    ) -> float:
        """市场冲击成本 (v8.3.2: 分层版 Square-Root Law)

        Square-Root 模型: Impact = notional * coeff * (Q / ADV) ^ exponent * vol_adj
        其中 coeff 按市值分层递减。
        """
        if avg_daily_volume <= 0:
            return 0.0
        # P1-13 修复: 负 notional (卖单/减仓) 会导致 participation ** 0.75 返回复数
        # 强制取绝对值, 冲击成本按绝对金额计算 (方向由 caller 处理)
        notional = abs(float(notional))
        if notional <= 0:
            return 0.0
        if tier is None:
            tier = classify_market_cap_tier(market_cap)
        coeff = self.get_impact_coeff(tier)
        participation = min(notional / avg_daily_volume, self.params.participation_rate)
        base_impact = notional * coeff * (participation**self.params.impact_exponent)
        volatility_adj = 1.0 + self.params.impact_volatility_adj * max(volatility - 0.02, 0.0) / 0.02
        return base_impact * volatility_adj  # type: ignore

    def estimate_opportunity_cost(self, notional: float, days_delayed: float = 1.0) -> float:
        """机会成本 (v8.1: 建仓延迟导致的预期收益损失)"""
        return notional * self.params.opportunity_cost_rate * days_delayed

    def estimate_delay_cost(self, notional: float = 0.0, hours_delayed: float = 0.0) -> float:
        """延迟成本 (v8.1: 执行延迟产生的额外成本)"""
        if hours_delayed <= 0:
            return 0.0
        return notional * self.params.delay_cost_per_hour * hours_delayed

    # -----------------------------------------------------------
    # 容量估算 (NEW - v8.3.2)
    # -----------------------------------------------------------
    def estimate_capacity(self, adv: float, max_pct: float | None = None) -> float:
        """估算单只股票的单日容量上限。

        规则: 不超过日均成交额的 max_pct (默认5%)

        Args:
            adv: 日均成交额
            max_pct: 最大比例, 默认取 CostParameters.max_pct_of_adv

        Returns:
            单日最大可交易金额
        """
        pct = max_pct if max_pct is not None else self.params.max_pct_of_adv
        return adv * pct

    def estimate_strategy_capacity(
        self, adv_list: dict[str, float], position_weights: dict[str, float], total_aum: float
    ) -> float:
        """估算策略总容量 (v8.3.2 NEW)

        方法: 对每只持仓股票, 计算其容量上限 / 其在组合中的权重,
        取所有股票中的最小值作为策略容量瓶颈。

        Args:
            adv_list: {symbol: 日均成交额}
            position_weights: {symbol: 组合权重}
            total_aum: 当前管理规模

        Returns:
            策略容量上限 (元)
        """
        capacities = []
        for symbol, weight in position_weights.items():
            adv = adv_list.get(symbol, 0)
            if adv <= 0 or weight <= 0:
                continue
            single_capacity = self.estimate_capacity(adv)
            # 该股票能支撑的策略总规模 = 单日容量 / 权重
            strategy_cap = single_capacity / weight
            capacities.append(strategy_cap)

        if not capacities:
            return float("inf")

        # 取瓶颈值 (最小值), 并给 80% 安全边际
        bottleneck = min(capacities) * 0.8
        # 按日均换手率做进一步约束
        # 日频策略: 单位换手率约支撑 10-20亿 (A股中小盘经验值)
        # 简化: 直接用瓶颈值
        return bottleneck

    def capacity_usage_pct(self, total_aum: float, estimated_capacity: float) -> float:
        """容量使用率 (%)"""
        if estimated_capacity <= 0:
            return 1.0
        return total_aum / estimated_capacity

    # -----------------------------------------------------------
    # 综合成本
    # -----------------------------------------------------------
    def estimate_total_cost(
        self,
        notional: float,
        adv: float = 0.0,
        volatility: float = 0.02,
        days_delayed: float = 1.0,
        hours_delayed: float = 0.0,
        tier: MarketCapTier | None = None,
        market_cap: float | None = None,
        side: str = "BUY",
    ) -> dict[str, float]:
        """综合交易成本 (v8.3.2: 分层滑点 + 完整费率 + 容量)

        Args:
            notional: 名义金额
            adv: 日均成交额 (0 则跳过冲击成本估算)
            volatility: 年化波动率
            days_delayed: 建仓延迟天数
            hours_delayed: 执行延迟小时数
            tier: 市值分组
            market_cap: 市值 (自动分组备用)
            side: 买卖方向

        Returns:
            成本明细字典, 含 tier 字段
        """
        if tier is None:
            tier = classify_market_cap_tier(market_cap)
        slippage = self.estimate_slippage(notional, volatility, tier=tier)
        commission = self.estimate_commission(notional, side=side)
        impact = self.estimate_impact(notional, adv, volatility, tier=tier)
        opportunity = self.estimate_opportunity_cost(notional, days_delayed)
        delay = self.estimate_delay_cost(notional, hours_delayed)
        total = slippage + commission + impact + opportunity + delay
        return {
            "notional": float(notional),
            "tier": tier.value,  # type: ignore
            "tier_slippage_bps": self.get_slippage_bps(tier),
            "slippage": float(slippage),
            "commission": float(commission),
            "stamp_tax_included": side.upper() in ("SELL", "SHORT"),
            "impact": float(impact),
            "opportunity_cost": float(opportunity),
            "delay_cost": float(delay),
            "total": float(total),
            "cost_bps": float(total / notional * 10000) if notional > 0 else 0.0,
        }

    def cost_penalty(
        self,
        notional: float,
        adv: float = 0.0,
        tier: MarketCapTier | None = None,
        market_cap: float | None = None,
    ) -> float:
        """成本惩罚项，用于优化目标函数 (v8.3.2: 分层)"""
        cost = self.estimate_total_cost(notional, adv, tier=tier, market_cap=market_cap)
        return cost["total"]
