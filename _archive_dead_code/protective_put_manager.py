# -*- coding: utf-8 -*-
"""
保护性看跌期权管理器 - Protective Put Manager
==============================================

为建仓组合提供轻量级尾部对冲层，用 2-3% 权利金预算覆盖
极端暴跌中的非线性下行保护。

设计原则：
  - 不依赖 v7.0 的五层对冲架构
  - 仅保护建仓阶段（3个月）的裸露 Beta 风险
  - 使用 A 股最活跃的 ETF 期权（50ETF/300ETF/科创50ETF）
  - Delta 对冲比例可配置，按市场状态动态调整
  - 买入持有+滚动展期，不做卖出期权/裸卖空

覆盖的极端情景：
  - 熊市 -35%：虚值 Put 在跌 15% 后提供近似 1:1 保护
  - 黑天鹅 -55%：深度虚值 Put 提供凸性放大赔付

用法：
  put_mgr = ProtectivePutManager(portfolio_value=5_000_000, hedge_budget_pct=0.025)
  recommendation = put_mgr.generate_recommendation(market_state, portfolio_exposure)
"""

import math
import os
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field


# ================================================================
# 数据模型
# ================================================================

@dataclass
class PutOrder:
    """单笔看跌期权买入指令"""
    underlying: str           # 标的ETF代码 (510050/510300/588000)
    name: str                 # 标的名称
    strike_pct: float         # 行权价占现价比例 (0.85=85%行权)
    strike_price: float       # 估算行权价
    delta: float              # 估算 Delta (期权对标的敏感度)
    otm_pct: float            # 虚值幅度
    contracts: int            # 合约张数
    est_premium: float        # 估算单张权利金
    total_cost: float         # 总权利金成本
    expiry_months: int        # 期限（月）
    protection_notional: float  # 保护的名义金额
    protection_level: float   # 保护线（跌破此价开始赔付）
    rationale: str            # 选择依据


@dataclass
class PutRecommendation:
    """保护性看跌期权完整建议"""
    date: str
    summary: str
    portfolio_value: float
    deployed_exposure: float   # 已建仓的名义敞口
    hedge_budget_total: float  # 总对冲预算
    hedge_budget_used: float   # 本期使用的预算
    existing_hedge_notional: float  # 已有对冲的名义金额
    orders: List[PutOrder] = field(default_factory=list)
    actions: List[str] = field(default_factory=list)
    risk_assessment: Dict = field(default_factory=dict)


# ================================================================
# 核心管理器
# ================================================================

class ProtectivePutManager:
    """
    保护性看跌期权管理器

    为建仓组合提供轻量级尾部对冲。
    核心算法：根据组合已建仓敞口、当前波动率、压力测试结果，
    计算需要保护的 Delta 敞口和对应的 Put 数量。
    """

    # ---- 可配置参数 ----
    DEFAULT_HEDGE_BUDGET_PCT = 0.025      # 对冲预算占组合的 2.5%/年
    DEFAULT_MIN_HEDGE_RATIO = 0.30         # 最低对冲比例（至少覆盖30%敞口）
    DEFAULT_MAX_HEDGE_RATIO = 1.0          # 最高对冲比例（最多全对冲）

    # 可用标的及其期权月合约到期日（每月第四个周三）
    AVAILABLE_UNDERLYINGS = {
        "510050": {
            "name": "上证50ETF",
            "lot_size": 10000,              # 合约乘数
            "typical_otm": 0.10,            # 典型虚值幅度 10%
            "min_otm": 0.05,                # 最近虚值 5%
            "max_otm": 0.20,                # 最远虚值 20%
            "beta_to_portfolio": 0.85,      # 与建仓组合的 Beta
            "description": "大市值蓝筹，流动性最好的期权，作为 Beta 对冲底层",
        },
        "510300": {
            "name": "沪深300ETF",
            "lot_size": 10000,
            "typical_otm": 0.10,
            "min_otm": 0.05,
            "max_otm": 0.20,
            "beta_to_portfolio": 0.90,
            "description": "覆盖面更广的宽基，与建仓组合风格更匹配",
        },
        "588000": {
            "name": "科创50ETF",
            "lot_size": 10000,
            "typical_otm": 0.12,             # 科创波动大，虚值更深
            "min_otm": 0.08,
            "max_otm": 0.25,
            "beta_to_portfolio": 1.15,       # 科创与高端制造高相关
            "description": "高 Beta 标的，直接对冲组合中最大的风格敞口",
        },
    }

    # ---- 市场状态对应的对冲比例 ----
    REGIME_HEDGE_RATIOS = {
        "normal":    0.30,   # 正常市场：覆盖 30% 敞口
        "yellow":    0.50,   # 黄色预警：覆盖 50%
        "orange":    0.70,   # 橙色预警：覆盖 70%
        "red":       0.85,   # 红色预警：覆盖 85%
        "extreme":   1.00,   # 极端预警：全覆盖
    }

    def __init__(self,
                 portfolio_value: float = 5_000_000,
                 hedge_budget_pct: float = None):
        """
        Args:
            portfolio_value: 组合总资金（元）
            hedge_budget_pct: 对冲预算比例，默认 2.5%/年
        """
        self.portfolio_value = portfolio_value
        self.hedge_budget_pct = hedge_budget_pct or self.DEFAULT_HEDGE_BUDGET_PCT
        self.hedge_budget_annual = portfolio_value * self.hedge_budget_pct
        self.hedge_budget_monthly = self.hedge_budget_annual / 12.0

        # 累计已使用的对冲预算
        self.hedge_spent_ytd = 0.0

        # 当前持仓的对冲合约
        self.active_puts: List[Dict] = []

        print(f"保护性看跌管理器初始化: 组合={portfolio_value/10000:.0f}万, "
              f"年度对冲预算={self.hedge_budget_annual/10000:.1f}万 "
              f"({self.hedge_budget_pct:.1%})")

    # ---------------------------------------------------------------
    # 核心方法：生成对冲建议
    # ---------------------------------------------------------------
    def generate_recommendation(self,
                                 market_state: Dict,
                                 portfolio_exposure: Dict = None) -> PutRecommendation:
        """
        生成保护性看跌期权建议

        Args:
            market_state: 市场状态字典，包含:
                - vix_proxy: VIX 代理值 (默认22)
                - volatility: 年化波动率 (默认0.18)
                - index_return_20d: 20日涨跌
                - emergency_level: 紧急等级 0-4
                - stress_black_swan_loss: 黑天鹅压力测试损失率
                - index_levels: {code: price} 各指数当前价格
            portfolio_exposure: 组合敞口字典，包含:
                - total_deployed: 已建仓总金额
                - style_weights: 风格权重分布
                - high_beta_exposure: 高Beta标的敞口

        Returns:
            PutRecommendation 完整建议
        """
        vix = market_state.get("vix_proxy", 22)
        volatility = market_state.get("volatility", 0.18)
        emergency = market_state.get("emergency_level", 0)
        black_swan_loss = abs(market_state.get("stress_black_swan_loss", 0.50))

        # 计算已建仓敞口
        deployed = 0.0
        if portfolio_exposure:
            deployed = portfolio_exposure.get("total_deployed", self.portfolio_value * 0.3)

        # ---- Step 1: 确定对冲等级和覆盖比例 ----
        regime = self._determine_regime(emergency, vix, volatility, black_swan_loss)
        hedge_ratio = self.REGIME_HEDGE_RATIOS[regime]

        # 需要保护的名义金额
        exposure_to_hedge = deployed * hedge_ratio

        # 扣除已有对冲
        existing_hedge = self._get_existing_hedge_notional()
        net_exposure_to_hedge = max(0, exposure_to_hedge - existing_hedge)

        # ---- Step 2: 选择对冲标的和 OTM 深度 ----
        # 根据组合风格权重选择标的：科创权重高就用 588000，否则用 510300
        primary_underlying, secondary_underlying = self._select_underlyings(
            portfolio_exposure)

        # OTM 深度根据黑天鹅损失比率决定
        if black_swan_loss > 0.50:
            otm_range = [0.10, 0.15, 0.20]  # 深虚值阶梯：多重保护层
        elif black_swan_loss > 0.35:
            otm_range = [0.08, 0.12]         # 中等虚值
        else:
            otm_range = [0.10]               # 标准虚值

        # ---- Step 3: 计算具体 Put 订单 ----
        orders = []
        total_cost = 0.0
        remaining_exposure = net_exposure_to_hedge
        index_levels = market_state.get("index_levels", {})

        # 主标的对冲（占总量的 70%）
        primary_allocation = remaining_exposure * 0.70
        primary_orders = self._build_put_orders(
            primary_underlying, primary_allocation,
            otm_range, index_levels, volatility, vix
        )
        orders.extend(primary_orders)
        remaining_exposure *= 0.30

        # 副标的对冲（占总量的 30%），如果需要
        if secondary_underlying and remaining_exposure > 100000:  # 至少10万
            secondary_orders = self._build_put_orders(
                secondary_underlying, remaining_exposure,
                [otm_range[0]], index_levels, volatility, vix
            )
            orders.extend(secondary_orders)

        # 总成本
        total_cost = sum(o.total_cost for o in orders)

        # ---- Step 4: 预算约束检查 ----
        # 如果超出月预算，按比例缩减
        budget_adjusted = False
        if total_cost > self.hedge_budget_monthly * 3:  # 允许单月最多 3 个月预算
            scale = (self.hedge_budget_monthly * 3) / total_cost
            for o in orders:
                o.contracts = max(1, int(o.contracts * scale))
                o.total_cost = o.contracts * o.est_premium
            total_cost = sum(o.total_cost for o in orders)
            budget_adjusted = True

        # ---- Step 5: 构建建议 ----
        summary_parts = [
            f"对冲等级: {regime.upper()}",
            f"覆盖敞口: {hedge_ratio:.0%} (已建仓{deployed/10000:.0f}万)"
        ]
        if budget_adjusted:
            summary_parts.append(f"已按预算约束缩减")
        summary_parts.append(f"总权利金: {total_cost/10000:.1f}万")

        actions = []
        if emergency >= 3:
            actions.append("URGENT: 建议今日执行保护性Put买入")
            actions.append("URGENT: 如期权账户未开立，联系券商加急开通")
        elif emergency >= 2:
            actions.append("WARNING: 48小时内完成Put建仓")
        else:
            actions.append("STANDARD: 下次展期时按建议调整")

        # 成本分析
        annual_cost_pct = (total_cost * 12 / self.portfolio_value) \
            if self.portfolio_value > 0 else 0
        budget_remaining = self.hedge_budget_monthly - total_cost
        actions.append(f"成本: {total_cost/10000:.1f}万/月 "
                       f"(年化{annual_cost_pct:.2%}, 月预算剩余{budget_remaining/10000:.1f}万)")

        return PutRecommendation(
            date=datetime.now().strftime("%Y-%m-%d"),
            summary="; ".join(summary_parts),
            portfolio_value=self.portfolio_value,
            deployed_exposure=deployed,
            hedge_budget_total=self.hedge_budget_annual,
            hedge_budget_used=total_cost,
            existing_hedge_notional=existing_hedge,
            orders=orders,
            actions=actions,
            risk_assessment={
                "regime": regime,
                "hedge_ratio": hedge_ratio,
                "vix_proxy": vix,
                "volatility": volatility,
                "black_swan_loss": f"{black_swan_loss:.1%}",
                "net_exposure_hedged": f"{net_exposure_to_hedge/10000:.0f}万",
            },
        )

    # ---------------------------------------------------------------
    # 内部方法
    # ---------------------------------------------------------------
    def _determine_regime(self, emergency: int, vix: float,
                          vol: float, bs_loss: float) -> str:
        """确定对冲等级"""
        if emergency >= 4:
            return "extreme"
        if emergency >= 3:
            return "red"
        if emergency >= 2:
            return "orange"
        if emergency >= 1:
            return "yellow"
        if bs_loss > 0.50:
            return "orange"   # 压力测试预示极高风险
        if vix > 25 and vol > 0.22:
            return "yellow"   # 高波动但还没到紧急
        return "normal"

    def _select_underlyings(self, exposure: Dict = None
                            ) -> Tuple[str, Optional[str]]:
        """选择对冲标的（主+副）"""
        if not exposure:
            return "510300", "510050"

        style_weights = exposure.get("style_weights", {})
        tech_weight = style_weights.get("tech", 0) + style_weights.get("high_end", 0)

        # 如果科技/高端制造占比超过 40%，优先用科创50
        if tech_weight > 0.40:
            return "588000", "510300"
        else:
            return "510300", "510050"

    def _build_put_orders(self,
                           underlying: str,
                           exposure: float,
                           otm_range: List[float],
                           index_levels: Dict,
                           volatility: float,
                           vix: float) -> List[PutOrder]:
        """构建单个标的的 Put 订单"""
        info = self.AVAILABLE_UNDERLYINGS.get(underlying)
        if not info:
            return []

        spot = index_levels.get(underlying, self._estimate_spot(underlying))
        lot_size = info["lot_size"]

        orders = []
        remaining_exposure = exposure

        # 每个 OTM 水平分配等额敞口
        allocation_per_level = exposure / len(otm_range)

        for otm_pct in otm_range:
            strike = spot * (1 - otm_pct)
            allocation = min(allocation_per_level, remaining_exposure)
            if allocation < lot_size * spot * 0.05:  # 最少 5% 名义
                break

            # 估算 Delta（简化 Black-Scholes 近似）
            # OTM Put Delta ≈ -N(-d1) 的简化，虚值越深 Delta 越小
            estimated_delta = self._estimate_put_delta(
                spot, strike, volatility, vix, otm_pct
            )

            # 合约张数 = 需保护名义 / (标的价格 × 合约乘数 × |Delta|)
            delta_abs = abs(estimated_delta)
            raw_contracts = allocation / (spot * lot_size * delta_abs)
            contracts = max(1, int(raw_contracts))

            # 估算单张权利金（BS 定价的简化近似）× 合约乘数
            est_premium = self._estimate_put_premium(
                spot, strike, volatility, otm_pct, estimated_delta) * lot_size

            total_cost = contracts * est_premium
            protection_notional = contracts * lot_size * spot * delta_abs
            protection_level = strike

            orders.append(PutOrder(
                underlying=underlying,
                name=info["name"],
                strike_pct=1 - otm_pct,
                strike_price=round(strike, 3),
                delta=round(estimated_delta, 3),
                otm_pct=otm_pct,
                contracts=contracts,
                est_premium=round(est_premium, 4),
                total_cost=round(total_cost, 2),
                expiry_months=3,   # 默认3个月
                protection_notional=round(protection_notional, 0),
                protection_level=round(protection_level, 3),
                rationale=(
                    f"虚值{otm_pct:.0%}Put, Δ≈{estimated_delta:.2f}, "
                    f"保护线={protection_level:.3f}, "
                    f"对冲{protection_notional/10000:.0f}万名义敞口"
                ),
            ))

            remaining_exposure -= allocation

        return orders

    def _estimate_spot(self, code: str) -> float:
        """估算标的价格（默认值）"""
        defaults = {
            "510050": 3.200,   # 上证50ETF ~3.2
            "510300": 4.500,   # 沪深300ETF ~4.5
            "588000": 1.200,   # 科创50ETF ~1.2
        }
        return defaults.get(code, 3.0)

    def _estimate_put_delta(self, spot: float, strike: float,
                             vol: float, vix: float, otm_pct: float) -> float:
        """
        估算 Put Delta（简化 Black-Scholes）
        虚值 Put Delta 范围约 -0.05 到 -0.45
        虚值越深（otm_pct 越大），Delta 越接近 0
        波动率越高，Delta 越大（更接近 ATM）
        """
        # 基础 Delta 根据虚值衰减
        # ATM: -0.50, 5% OTM: -0.35, 10% OTM: -0.22, 15% OTM: -0.12, 20% OTM: -0.06
        base_delta = -0.50 * math.exp(-otm_pct * 8.0)

        # 波动率调整：高波动时 Put 更贵但 Delta 差异变小
        vol_factor = min(vol / 0.18, 2.0)
        vix_factor = min(vix / 22.0, 2.5)

        # 高 VIX 时 OTM Put 的 Delta 更接近 ATM（恐慌时虚值 Put 也有较好保护效果）
        if vix > 35:
            base_delta *= 1.5

        adjusted_delta = base_delta * vol_factor

        # Delta 范围约束
        return max(-0.50, min(-0.03, adjusted_delta))

    def _estimate_put_premium(self, spot: float, strike: float,
                               vol: float, otm_pct: float,
                               delta: float) -> float:
        """
        估算 Put 权利金（简化 BS + 经验调整）
        返回单张合约的权利金（元）

        简化公式: Premium ≈ Spot × Volatility × e^(-OTM%) × √T
        乘以合约乘数得到总权利金
        """
        T = 0.25  # 3个月 = 0.25年

        # BS 近似：OTM Put = S × (Vol × √T × N'(d1) - K/S × ...)
        # 简化：Premium ≈ S × Vol × e^(-OTM/a) × √T × 0.4
        decay = math.exp(-otm_pct / 0.08)
        premium_per_unit = spot * vol * decay * math.sqrt(T) * 0.4

        # 高 VIX 环境中权利金上升（恐慌溢价）
        premium_per_unit *= (1 + max(0, otm_pct - 0.05) * 0.5)

        return premium_per_unit

    def _get_existing_hedge_notional(self) -> float:
        """获取已有对冲的保护名义金额"""
        now = datetime.now()
        active = []

        for put_info in self.active_puts:
            expiry = put_info.get("expiry_date")
            if expiry and isinstance(expiry, date):
                if expiry > now.date():
                    active.append(put_info)
            elif expiry and isinstance(expiry, str):
                try:
                    exp_date = datetime.strptime(expiry, "%Y-%m-%d").date()
                    if exp_date > now.date():
                        active.append(put_info)
                except ValueError:
                    pass

        self.active_puts = active
        return sum(p.get("protection_notional", 0) for p in active)

    # ---------------------------------------------------------------
    # 报告输出
    # ---------------------------------------------------------------
    def format_recommendation_markdown(self, rec: PutRecommendation) -> str:
        """格式化为 Markdown 报告"""
        lines = [
            f"# 保护性看跌期权对冲建议 — {rec.date}",
            "",
            f"**摘要**: {rec.summary}",
            "",
            f"| 参数 | 值 |",
            f"|:-----|:----|",
            f"| 组合总资金 | {rec.portfolio_value/10000:.0f} 万 |",
            f"| 已建仓敞口 | {rec.deployed_exposure/10000:.0f} 万 |",
            f"| 年度对冲预算 | {rec.hedge_budget_total/10000:.1f} 万 ({self.hedge_budget_pct:.1%}) |",
            f"| 本期权利金 | {rec.hedge_budget_used/10000:.2f} 万 |",
            f"| 已有对冲名义 | {rec.existing_hedge_notional/10000:.0f} 万 |",
            f"| 对冲等级 | {rec.risk_assessment.get('regime', 'N/A').upper()} |",
            f"| VIX 代理 | {rec.risk_assessment.get('vix_proxy', 'N/A')} |",
            f"| 黑天鹅损失预测 | {rec.risk_assessment.get('black_swan_loss', 'N/A')} |",
            "",
        ]

        if rec.orders:
            lines.extend([
                "## 建议买入看跌期权",
                "",
                "| 标的 | 行权价(%现价) | 估算行权价 | Δ | 张数 | 单张权利金 | 总成本 | 保护名义 |",
                "|:-----|:-------------|:----------|:--|-----:|----------:|-------:|--------:|",
            ])

            for o in rec.orders:
                lines.append(
                    f"| {o.underlying} {o.name} "
                    f"| {o.strike_pct:.0%} "
                    f"| {o.strike_price:.3f} "
                    f"| {o.delta:.2f} "
                    f"| {o.contracts} "
                    f"| {o.est_premium:.3f} "
                    f"| {o.total_cost:,.0f} "
                    f"| {o.protection_notional/10000:.0f}万 |"
                )

            total_protection = sum(o.protection_notional for o in rec.orders)
            total_cost_all = sum(o.total_cost for o in rec.orders)
            lines.extend([
                f"| | | | | | | **{total_cost_all:,.0f}** | **{total_protection/10000:.0f}万** |",
                "",
            ])

            # 保护效果说明
            lines.extend([
                "## 极端情景保护效果",
                "",
                "| 情景 | 市场跌幅 | 组合裸跌损失 | Put赔付 | 净损失 | 保护率 |",
                "|:-----|--------:|------------:|-------:|------:|------:|",
            ])

            for name, mkt_drop in [
                ("温和回调", 0.10),
                ("中度下跌", 0.20),
                ("熊市", 0.35),
                ("黑天鹅", 0.55),
            ]:
                # 组合裸跌 ≈ Beta × 市场跌幅
                beta = 1.0
                naked_loss = rec.deployed_exposure * beta * mkt_drop
                # Put 赔付：跌破行权价的部分 × 保护名义
                put_payout = 0
                for o in rec.orders:
                    if mkt_drop > o.otm_pct:
                        effective_drop = mkt_drop - o.otm_pct
                        put_payout += o.protection_notional * effective_drop / (1 - o.otm_pct)
                put_payout = min(put_payout, naked_loss * 0.9)

                net_loss = naked_loss - put_payout
                protection_rate = put_payout / naked_loss if naked_loss > 0 else 0

                lines.append(
                    f"| {name} | -{mkt_drop:.0%} | {naked_loss/10000:.0f}万 "
                    f"| {put_payout/10000:.0f}万 | {net_loss/10000:.0f}万 "
                    f"| {protection_rate:.0%} |"
                )

            lines.append("")

        if rec.actions:
            lines.extend(["## 执行建议", ""])
            for a in rec.actions:
                lines.append(f"- {a}")
            lines.append("")

        # 展期提醒
        lines.extend([
            "## 展期策略",
            "",
            "- 到期前 5 个交易日评估是否需要展期",
            "- 若市场环境改善（VIX<25 且紧急等级=0），可降低对冲比例",
            "- 若市场环境恶化，在展期时加深 OTM 深度以控制成本",
            "- 建仓完成（总敞口接近 500 万）后，考虑转为成本更低的 Put Spread",
            "",
            "---",
            f"*建议生成时间: {rec.date}*",
        ])

        return "\n".join(lines)

    # ---------------------------------------------------------------
    # 展期管理
    # ---------------------------------------------------------------
    def should_roll(self, current_date: Optional[date] = None) -> Tuple[bool, str]:
        """判断是否需要展期"""
        if not self.active_puts:
            return True, "无活跃对冲合约"

        now = current_date or date.today()
        closest_expiry = None
        for p in self.active_puts:
            exp = p.get("expiry_date")
            if isinstance(exp, str):
                exp = datetime.strptime(exp, "%Y-%m-%d").date()
            if closest_expiry is None or exp < closest_expiry:
                closest_expiry = exp

        if closest_expiry is None:
            return True, "无法确定到期日"

        days_left = (closest_expiry - now).days
        if days_left <= 5:
            return True, f"最近到期日 {closest_expiry}，仅剩 {days_left} 天（阈值 5 天）"
        return False, f"最近到期日 {closest_expiry}，还有 {days_left} 天"

    def record_execution(self, orders: List[PutOrder], expiry_date: date):
        """记录已执行的对冲合约"""
        for o in orders:
            self.active_puts.append({
                "underlying": o.underlying,
                "name": o.name,
                "strike_price": o.strike_price,
                "contracts": o.contracts,
                "protection_notional": o.protection_notional,
                "total_cost": o.total_cost,
                "executed_date": datetime.now().strftime("%Y-%m-%d"),
                "expiry_date": expiry_date,
            })
            self.hedge_spent_ytd += o.total_cost

        print(f"已记录 {len(orders)} 笔对冲合约，YTD 权利金支出={self.hedge_spent_ytd/10000:.1f}万")


# ================================================================
# 快速演示
# ================================================================
if __name__ == "__main__":
    # 正常市场
    normal_state = {
        "vix_proxy": 22,
        "volatility": 0.18,
        "emergency_level": 0,
        "stress_black_swan_loss": 0.45,
        "index_levels": {"510050": 3.20, "510300": 4.50, "588000": 1.20},
    }
    normal_exposure = {
        "total_deployed": 1_500_000,
        "style_weights": {"high_end": 0.45, "tech": 0.20, "etf": 0.25, "other": 0.10},
        "high_beta_exposure": 800_000,
    }

    mgr = ProtectivePutManager(portfolio_value=5_000_000)
    rec = mgr.generate_recommendation(normal_state, normal_exposure)
    print(mgr.format_recommendation_markdown(rec))

    print("\n" + "=" * 60)
    print("测试黑天鹅模式")
    print("=" * 60)

    crash_state = {
        "vix_proxy": 55,
        "volatility": 0.40,
        "emergency_level": 4,
        "stress_black_swan_loss": 0.62,
        "index_levels": {"510050": 2.80, "510300": 3.90, "588000": 0.95},
    }

    rec2 = mgr.generate_recommendation(crash_state, normal_exposure)
    print(mgr.format_recommendation_markdown(rec2))
