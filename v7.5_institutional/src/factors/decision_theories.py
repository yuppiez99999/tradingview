# -*- coding: utf-8 -*-
"""
四大投资决策理论引擎 v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
将四位投资大师的思维框架融入量化策略系统:

1. 索罗斯反身性理论 (Soros Reflexivity)
   - 识别市场参与者偏见与价格的正反馈循环
   - 检测"盛衰周期"(boom-bust)的阶段
   - 衡量"远离均衡"程度 → 反身性得分

2. 瑞达利奥经济机器理论 (Dalio Economic Machine)
   - 识别债务周期阶段(短期/长期)
   - 经济环境四象限分类(增长/通胀矩阵)
   - 风险平价权重建议 + 全天候配置

3. 第一性原理分析 (First Principles)
   - 将企业/行业拆解到最基础的价值驱动因素
   - 从底层推演内在价值，而非类比推理
   - 挑战市场共识叙事

4. 巴菲特芒格价值投资框架 (Buffett-Munger Model)
   - 护城河评估(品牌/转换成本/网络效应/规模)
   - 安全边际计算(内在价值 vs 市场价)
   - 能力圈置信度评估
   - 高质量企业评分

标准输出: 每个引擎返回 DecisionTheorySignal 字典，包含:
  - signal: BUY / SELL / HOLD
  - score: 0.0~1.0 置信度
  - details: 详细分析文本
  - data: 结构化数据点
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import sys
import math
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field, asdict

logger = logging.getLogger('decision_theories')

# ── 标准信号输出结构 ──────────────────────────────────
@dataclass
class TheoryDecision:
    """统一决策信号结构"""
    theory: str
    signal: str  # BUY / SELL / HOLD / NEUTRAL
    score: float  # 0.0~1.0 综合置信度
    conviction: str  # LOW / MEDIUM / HIGH
    summary: str  # 一句话摘要
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return asdict(self)


# ============================================================
# 1. 索罗斯反身性理论引擎
# ============================================================
class SorosReflexivityEngine:
    """
    索罗斯反身性理论 (Theory of Reflexivity)

    核心思想:
      - 市场参与者对基本面的"偏见"会通过交易行为影响价格，
        价格变化又反过来改变"基本面"（企业融资能力/市场情绪/
        政策预期），形成双向反馈循环。
      - 当正反馈循环自我强化时，价格会远离均衡，进入"盛衰周期"。
      - 关键是在"远离均衡"状态被打破前识别转折点。

    量化实现:
      1. 反身性得分 (Reflexivity Score)
         = 价格动能 × 成交量放大 × 估值偏离 × 情绪一致性
      2. 盛衰周期阶段检测
         = 识别当前处于: 萌芽期/自我强化期/考验期/逆转期
      3. 远离均衡程度
         = |Z-score| × (波动率/历史波动率) × 趋势持续性
    """

    # 盛衰周期阶段定义
    CYCLE_PHASES = {
        "GERMINAL": "萌芽期 — 趋势初现，偏见尚弱，基本面驱动为主",
        "ACCELERATING": "自我强化期 — 价格与偏见相互加强，正反馈主导",
        "TESTING": "考验期 — 趋势遇阻，偏见开始动摇，波动加大",
        "REVERSAL": "逆转期 — 偏见瓦解，正反馈转为负反馈",
        "EQUILIBRIUM": "均衡期 — 价格与基本面大致吻合，无显著偏见",
    }

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.reflexivity_threshold = self.config.get("reflexivity_threshold", 0.6)
        self.zscore_extreme = self.config.get("zscore_extreme", 2.0)
        self.volume_spike_multiple = self.config.get("volume_spike_multiple", 1.5)
        self.momentum_lookback = self.config.get("momentum_lookback", 20)

    def compute_reflexivity_score(
        self,
        price_data: Dict[str, Any],
        volume_data: Dict[str, float] = None,
        sentiment_data: Dict[str, float] = None,
        valuation_data: Dict[str, float] = None,
    ) -> Dict[str, Any]:
        """
        计算每只标的的反身性得分

        Args:
            price_data: {"code": {"price": float, "change_20d": float, "z_score": float, "volatility": float}}
            volume_data: {"code": {"current_vol": float, "avg_vol_20d": float}}
            sentiment_data: {"code": {"sentiment_score": float, "narrative_strength": float}}
            valuation_data: {"code": {"pe_ttm": float, "pe_historical_median": float}}

        Returns:
            {"code": {"score": float, "phase": str, "far_from_eq": bool, "risk": str}}
        """
        results = {}
        volume_data = volume_data or {}
        sentiment_data = sentiment_data or {}
        valuation_data = valuation_data or {}

        for code, pd in price_data.items():
            price = pd.get("price", 0)
            if price <= 0:
                continue

            # 1. 价格动能 (momentum component)
            momentum = abs(pd.get("change_20d", 0))  # 无论涨跌，大幅变动都是反身性信号
            momentum_score = min(momentum / 0.20, 1.0)  # 20%变动 → score=1.0

            # 2. 成交量放大 (volume amplification)
            vol_score = 0.5
            vd = volume_data.get(code, {})
            if vd:
                current_vol = vd.get("current_vol", 0)
                avg_vol = vd.get("avg_vol_20d", 1)
                if avg_vol > 0:
                    vol_ratio = current_vol / avg_vol
                    vol_score = min(vol_ratio / (self.volume_spike_multiple * 2), 1.0)

            # 3. 估值偏离 (valuation deviation)
            val_score = 0.5
            vld = valuation_data.get(code, {})
            if vld:
                pe = vld.get("pe_ttm", 0)
                pe_median = vld.get("pe_historical_median", pe)
                if pe > 0 and pe_median > 0:
                    pe_deviation = abs(pe / pe_median - 1.0)
                    val_score = min(pe_deviation / 0.50, 1.0)  # 50%偏离 → score=1.0

            # 4. 情绪一致性 (sentiment alignment)
            sent_score = 0.5
            sd = sentiment_data.get(code, {})
            if sd:
                sentiment = sd.get("sentiment_score", 0)
                narrative = sd.get("narrative_strength", 0)
                # 情绪越极端、叙事越强 → 反身性越高
                sent_score = min((abs(sentiment) * 0.7 + narrative * 0.3), 1.0)

            # 综合反身性得分
            reflexivity = (
                momentum_score * 0.30 +
                vol_score * 0.25 +
                val_score * 0.25 +
                sent_score * 0.20
            )
            reflexivity = round(reflexivity, 4)

            # 判断盛衰周期阶段
            z_score = pd.get("z_score", 0)
            phase = self._classify_cycle_phase(
                reflexivity, momentum_score, z_score, pd.get("change_20d", 0)
            )

            # 远离均衡判断
            far_from_eq = abs(z_score) > self.zscore_extreme or reflexivity > self.reflexivity_threshold

            # 风险评估
            if reflexivity > 0.8:
                risk = "极度反身性 — 盛衰周期高风险区，随时可能逆转"
            elif reflexivity > self.reflexivity_threshold:
                risk = "反身性显著 — 价格与基本面正反馈循环中"
            elif reflexivity > 0.4:
                risk = "温和反身性 — 市场基本有效"
            else:
                risk = "低反身性 — 价格由基本面驱动"

            results[code] = {
                "score": reflexivity,
                "phase": phase,
                "phase_desc": self.CYCLE_PHASES.get(phase, ""),
                "far_from_equilibrium": far_from_eq,
                "momentum_contrib": round(momentum_score, 3),
                "volume_contrib": round(vol_score, 3),
                "valuation_contrib": round(val_score, 3),
                "sentiment_contrib": round(sent_score, 3),
                "risk_assessment": risk,
                "signal": "SELL" if (reflexivity > 0.7 and z_score > 0) else
                          "BUY" if (reflexivity > 0.5 and z_score < -1.5) else "HOLD",
            }

        return results

    def _classify_cycle_phase(
        self,
        reflexivity: float,
        momentum: float,
        z_score: float,
        change_20d: float,
    ) -> str:
        """分类当前盛衰周期阶段"""
        if reflexivity < 0.3:
            return "EQUILIBRIUM"
        if reflexivity > 0.7:
            if abs(z_score) > 2.5:
                return "TESTING" if abs(momentum) < 0.5 else "REVERSAL"
            return "ACCELERATING"
        if reflexivity > 0.4:
            # 动量方向
            if change_20d > 0.05:
                return "ACCELERATING"
            elif change_20d < -0.05:
                return "REVERSAL"
            return "GERMINAL"
        return "GERMINAL"

    def generate_decision(self, stock_results: Dict[str, Any]) -> TheoryDecision:
        """基于反身性分析生成整体决策"""
        if not stock_results:
            return TheoryDecision(
                theory="索罗斯反身性",
                signal="NEUTRAL",
                score=0.0,
                conviction="LOW",
                summary="无有效数据，无法生成反身性决策",
            )

        scores = [r["score"] for r in stock_results.values()]
        avg_score = sum(scores) / len(scores) if scores else 0
        far_from_eq_count = sum(1 for r in stock_results.values() if r.get("far_from_equilibrium"))

        # 整体观点
        accelerating_count = sum(
            1 for r in stock_results.values() if r["phase"] == "ACCELERATING"
        )
        reversal_count = sum(
            1 for r in stock_results.values() if r["phase"] == "REVERSAL"
        )

        if accelerating_count > len(scores) * 0.4:
            signal = "HOLD"  # 趋势中，持有但警惕
            summary = f"反身性平均得分{avg_score:.2f}，{accelerating_count}只标的正处于自我强化期，趋势可能持续但需警惕逆转"
            conviction = "MEDIUM"
        elif reversal_count > len(scores) * 0.3:
            signal = "SELL"
            summary = f"反身性平均得分{avg_score:.2f}，{reversal_count}只标的进入逆转期，偏见正在瓦解"
            conviction = "HIGH"
        elif far_from_eq_count > len(scores) * 0.4:
            signal = "HOLD"
            summary = f"反身性平均得分{avg_score:.2f}，{far_from_eq_count}只标的远离均衡，需密切关注"
            conviction = "MEDIUM"
        else:
            signal = "BUY" if avg_score < 0.4 else "HOLD"
            summary = f"反身性平均得分{avg_score:.2f}，市场处于较均衡状态，可寻找被低估机会"
            conviction = "LOW"

        return TheoryDecision(
            theory="索罗斯反身性",
            signal=signal,
            score=round(avg_score, 4),
            conviction=conviction,
            summary=summary,
            details={
                "average_reflexivity": round(avg_score, 4),
                "far_from_equilibrium_count": far_from_eq_count,
                "accelerating_count": accelerating_count,
                "reversal_count": reversal_count,
                "stock_signals": stock_results,
            },
        )


# ============================================================
# 2. 瑞达利奥经济机器理论引擎
# ============================================================
class DalioEconomicMachine:
    """
    瑞达利奥经济机器理论 (How the Economic Machine Works)

    核心思想:
      - 经济由三股力量驱动: 生产率增长、短期债务周期(5-8年)、
        长期债务周期(50-75年)
      - 交易 = 买方(货币+信贷) + 卖方(商品/服务/金融资产)
      - 所有市场都遵循"交易→市场→经济"的层级结构
      - 全天候策略: 在不同经济环境(增长/通胀的四象限)中分散配置

    量化实现:
      1. 经济环境分类 (Growth × Inflation 矩阵)
         → 四象限: 繁荣/过热/衰退/通缩再膨胀
      2. 债务周期阶段判断
         → 基于信贷增速 + 债务/GDP比率 + 利率水平
      3. 风险平价权重建议
         → 基于各资产波动率的倒数分配权重
      4. 全天候配置建议
    """

    # 经济环境四象限
    REGIMES = {
        "PROSPERITY": "繁荣期 — 增长↑ 通胀↑ → 超配: 商品/黄金/新兴市场股票",
        "OVERHEAT": "过热期 — 增长↓ 通胀↑ → 超配: 商品/黄金/通胀挂钩债券",
        "RECESSION": "衰退期 — 增长↓ 通胀↓ → 超配: 国债/投资级债券",
        "REFLEXIVITY": "再通胀期 — 增长↑ 通胀↓ → 超配: 股票/信用债",
    }

    # 全天候配置基准
    ALL_WEATHER_BASELINE = {
        "stocks": 0.30,
        "long_term_bonds": 0.40,
        "intermediate_bonds": 0.15,
        "gold": 0.075,
        "commodities": 0.075,
    }

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.debt_warning_threshold = self.config.get("debt_warning_threshold", 0.6)

    def classify_economic_regime(
        self,
        growth_data: Dict[str, float] = None,
        inflation_data: Dict[str, float] = None,
    ) -> Dict[str, Any]:
        """
        经济环境四象限分类

        Args:
            growth_data: {"gdp_growth": float, "pmi": float, "industrial_production": float}
            inflation_data: {"cpi": float, "ppi": float, "core_cpi": float}

        Returns:
            {"regime": str, "growth_trend": str, "inflation_trend": str, "allocation": dict}
        """
        growth_data = growth_data or {}
        inflation_data = inflation_data or {}

        # 增长判定
        pmi = growth_data.get("pmi", 50.0)
        gdp = growth_data.get("gdp_growth", 5.0)
        growth_is_rising = pmi >= 50 and gdp >= 4.5
        growth_is_falling = pmi < 49 or gdp < 4.0

        # 通胀判定
        cpi = inflation_data.get("cpi", 1.5)
        ppi = inflation_data.get("ppi", 0.0)
        inflation_is_rising = cpi > 2.0 or ppi > 1.0
        inflation_is_falling = cpi < 1.0 and ppi < -1.0

        # 象限判断
        if growth_is_rising and not inflation_is_rising:
            regime = "REFLEXIVITY"  # 再通胀/复苏
            regime_name = "再通胀期(复苏)"
        elif growth_is_rising and inflation_is_rising:
            regime = "PROSPERITY"  # 繁荣
            regime_name = "繁荣期"
        elif not growth_is_rising and inflation_is_rising:
            regime = "OVERHEAT"  # 过热/滞胀
            regime_name = "过热期(滞胀风险)"
        else:
            regime = "RECESSION"  # 衰退
            regime_name = "衰退期(通缩)"

        # 各象限配置建议
        allocations = {
            "PROSPERITY": {
                "stocks": 0.35, "bonds": 0.30, "gold": 0.15,
                "commodities": 0.10, "cash": 0.10
            },
            "OVERHEAT": {
                "stocks": 0.20, "bonds": 0.25, "gold": 0.20,
                "commodities": 0.20, "cash": 0.15
            },
            "RECESSION": {
                "stocks": 0.20, "bonds": 0.50, "gold": 0.10,
                "commodities": 0.05, "cash": 0.15
            },
            "REFLEXIVITY": {
                "stocks": 0.40, "bonds": 0.30, "gold": 0.10,
                "commodities": 0.10, "cash": 0.10
            },
        }

        return {
            "regime": regime,
            "regime_name": regime_name,
            "regime_desc": self.REGIMES.get(regime, ""),
            "growth_trend": "上升" if growth_is_rising else "下降",
            "inflation_trend": "上升" if inflation_is_rising else "下降",
            "pmi": pmi,
            "cpi": cpi,
            "recommended_allocation": allocations.get(regime, allocations["RECESSION"]),
            "signal": "HOLD",
        }

    def assess_debt_cycle(
        self,
        debt_data: Dict[str, float] = None,
    ) -> Dict[str, Any]:
        """
        债务周期阶段评估

        Args:
            debt_data: {
                "debt_to_gdp": float (如 2.8 表示280%),
                "credit_growth_yoy": float (如 0.08 表示8%),
                "policy_rate": float (如 0.035),
                "real_rate": float (名义利率-通胀),
            }

        Returns:
            债务周期分析结果
        """
        debt_data = debt_data or {}
        debt_to_gdp = debt_data.get("debt_to_gdp", 2.7)
        credit_growth = debt_data.get("credit_growth_yoy", 0.08)
        policy_rate = debt_data.get("policy_rate", 0.03)
        real_rate = debt_data.get("real_rate", 0.01)

        # 长期债务周期判断
        if debt_to_gdp > 3.0:
            long_cycle = "长期债务周期顶部 — 去杠杆风险高"
            long_score = 0.8
        elif debt_to_gdp > 2.5:
            long_cycle = "长期债务周期偏高 — 关注去杠杆信号"
            long_score = 0.6
        elif debt_to_gdp > 2.0:
            long_cycle = "长期债务周期中期 — 债务水平可控"
            long_score = 0.4
        else:
            long_cycle = "长期债务周期相对安全"
            long_score = 0.2

        # 短期债务周期判断
        if credit_growth > 0.12:
            short_cycle = "信贷扩张期 — 经济可能过热"
            short_score = 0.7
        elif credit_growth > 0.06:
            short_cycle = "信贷温和增长 — 经济正常运行"
            short_score = 0.4
        elif credit_growth > 0.02:
            short_cycle = "信贷增速放缓 — 经济可能减速"
            short_score = 0.5
        else:
            short_cycle = "信贷收缩 — 经济下行风险"
            short_score = 0.8

        # 综合评估
        combined_score = (long_score * 0.6 + short_score * 0.4)

        if combined_score > 0.7:
            risk_level = "高风险 — 建议增加防御配置(国债/黄金)"
            signal = "SELL"
        elif combined_score > 0.5:
            risk_level = "中等风险 — 保持中性配置，关注政策信号"
            signal = "HOLD"
        else:
            risk_level = "低风险 — 可适度积极配置"
            signal = "BUY"

        return {
            "long_debt_cycle": long_cycle,
            "long_cycle_score": round(long_score, 2),
            "short_debt_cycle": short_cycle,
            "short_cycle_score": round(short_score, 2),
            "combined_debt_score": round(combined_score, 2),
            "risk_level": risk_level,
            "debt_to_gdp": debt_to_gdp,
            "credit_growth": credit_growth,
            "policy_rate": policy_rate,
            "signal": signal,
        }

    def compute_risk_parity_weights(
        self,
        asset_volatilities: Dict[str, float] = None,
    ) -> Dict[str, float]:
        """
        风险平价权重计算
        核心公式: w_i ∝ 1/σ_i (各资产风险贡献相等)

        Args:
            asset_volatilities: {"510300": 0.18, "518880": 0.15, ...}

        Returns:
            风险平价权重
        """
        asset_volatilities = asset_volatilities or {
            "stocks": 0.20,
            "bonds": 0.05,
            "gold": 0.15,
            "cash": 0.01,
        }

        # 计算风险倒数
        inv_vols = {k: 1.0 / max(v, 0.01) for k, v in asset_volatilities.items()}
        total_inv = sum(inv_vols.values())

        if total_inv == 0:
            return {k: 0 for k in asset_volatilities}

        weights = {k: round(v / total_inv, 4) for k, v in inv_vols.items()}
        return weights

    def generate_decision(
        self,
        regime_result: Dict[str, Any],
        debt_result: Dict[str, Any],
    ) -> TheoryDecision:
        """生成达利奥框架综合决策"""
        regime = regime_result.get("regime", "RECESSION")
        debt_score = debt_result.get("combined_debt_score", 0.5)

        # 信号判断
        if debt_score > 0.7 and regime in ("OVERHEAT", "RECESSION"):
            signal = "SELL"
            conviction = "HIGH"
            summary = (
                f"达利奥框架预警: 债务周期评分{debt_score:.2f}(高风险)叠加"
                f"{regime_result.get('regime_name', '')}，建议大幅增加防御配置"
            )
        elif debt_score > 0.5 and regime == "RECESSION":
            signal = "HOLD"
            conviction = "MEDIUM"
            summary = (
                f"达利奥框架: 经济处于{regime_result.get('regime_name', '')}，"
                f"债务评分{debt_score:.2f}，保持防御姿态"
            )
        elif regime == "REFLEXIVITY" and debt_score < 0.5:
            signal = "BUY"
            conviction = "HIGH"
            summary = (
                f"达利奥框架: 经济处于再通胀复苏期，债务评分{debt_score:.2f}偏低，"
                f"是积极配置窗口"
            )
        else:
            signal = "HOLD"
            conviction = "MEDIUM"
            summary = (
                f"达利奥框架: {regime_result.get('regime_name', '')}，"
                f"债务评分{debt_score:.2f}，维持标准配置"
            )

        return TheoryDecision(
            theory="达利奥经济机器",
            signal=signal,
            score=round(1.0 - debt_score, 4),
            conviction=conviction,
            summary=summary,
            details={
                "regime": regime_result,
                "debt_cycle": debt_result,
                "recommended_allocation": regime_result.get("recommended_allocation", {}),
            },
        )


# ============================================================
# 3. 第一性原理分析器
# ============================================================
class FirstPrinciplesAnalyzer:
    """
    第一性原理分析 (First Principles Thinking)

    核心思想:
      - 不依赖类比或共识，将问题分解到最基本的"不可再分"的元素
      - 从这些基本真理出发，重新构建理解 → 识别市场错误定价
      - 对于投资: 企业价值 = Σ(未来自由现金流折现) 是终极第一性原理

    量化实现:
      1. 价值驱动因素分解
         → 行业: 需求驱动因素 × 供给约束 × 政策环境
         → 公司: 收入 = 市场容量 × 份额 × 价格
      2. 内在价值估算
         → DCF简化框架: 当前盈利能力 + 成长性 + 风险调整
      3. 共识挑战
         → 对比分析师一致预期 vs 第一性原理推演
    """

    # 各行业第一性价值驱动
    SECTOR_DRIVERS = {
        "科技": {
            "demand": ["用户增长", "技术迭代速度", "国产替代率"],
            "supply": ["研发投入", "工程师供给", "芯片/算力约束"],
            "unit_economics": ["ARPU", "获客成本", "客户生命周期价值"],
            "moat": ["技术壁垒", "专利保护", "生态锁定"],
        },
        "医药": {
            "demand": ["老龄化率", "疾病谱变化", "医保覆盖"],
            "supply": ["研发管线", "临床试验成功率", "GMP产能"],
            "unit_economics": ["药品定价", "市占率", "专利到期时间"],
            "moat": ["专利悬崖", "独家品种", "渠道壁垒"],
        },
        "能源": {
            "demand": ["GDP电力弹性系数", "新能源替代率", "工业生产指数"],
            "supply": ["产能利用率", "进口依赖度", "库存水平"],
            "unit_economics": ["吨煤成本", "长协价占比", "运输成本"],
            "moat": ["资源储量", "采矿权", "运输基础设施"],
        },
        "制造": {
            "demand": ["固定资产投资增速", "出口订单", "PMI新订单"],
            "supply": ["产能利用率", "原材料成本", "劳动力供给"],
            "unit_economics": ["毛利率", "规模效应", "自动化率"],
            "moat": ["技术领先", "客户关系", "规模经济"],
        },
        "金融": {
            "demand": ["社融增速", "居民杠杆率", "企业信贷需求"],
            "supply": ["资本充足率", "不良贷款率", "流动性覆盖率"],
            "unit_economics": ["净息差", "非利息收入占比", "成本收入比"],
            "moat": ["牌照壁垒", "渠道网络", "风控体系"],
        },
        "商品": {
            "demand": ["全球央行购金", "避险需求", "珠宝消费"],
            "supply": ["矿产产量", "回收金供给", "央行售金"],
            "unit_economics": ["美元指数", "实际利率", "地缘风险溢价"],
            "moat": ["稀缺性", "货币替代属性", "零信用风险"],
        },
    }

    def __init__(self, config: dict = None):
        self.config = config or {}

    def decompose_value_drivers(
        self,
        stock_data: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        分解每只标的的第一性价值驱动

        Args:
            stock_data: {"601088": {"sector": "能源", "price": 41.26, "pe": 10.5, "roe": 0.15, ...}}

        Returns:
            每只标的的价值驱动分解
        """
        results = {}
        for code, data in stock_data.items():
            sector = data.get("sector", "其他")
            if sector not in self.SECTOR_DRIVERS:
                sector = "制造"  # 默认

            drivers = self.SECTOR_DRIVERS[sector]
            pe = data.get("pe", 0)
            roe = data.get("roe", 0)
            price = data.get("price", 0)

            # 拆解基本价值元素
            analysis = {
                "sector": sector,
                "key_drivers": drivers,
                "pe_current": pe,
                "roe_current": roe,
                # 彼得·林奇 PEG 启发: 合理PE ≈ 增长率
                "fair_pe_estimate": roe * 100 if roe > 0 else pe,
                "price": price,
            }

            # 判断是否可能被低估
            if pe > 0 and roe > 0:
                implied_growth = pe / 100  # PEG ≈ 1 → PE/100
                if roe > implied_growth + 0.05:
                    analysis["assessment"] = "可能被低估 — ROE显著高于隐含增长率"
                    analysis["signal"] = "BUY"
                elif roe < implied_growth - 0.05:
                    analysis["assessment"] = "可能被高估 — ROE低于隐含增长率"
                    analysis["signal"] = "SELL"
                else:
                    analysis["assessment"] = "估值基本合理 — ROE与PE匹配"
                    analysis["signal"] = "HOLD"
            else:
                analysis["assessment"] = "数据不足，无法评估"
                analysis["signal"] = "HOLD"

            results[code] = analysis

        return results

    def compute_intrinsic_value_range(
        self,
        eps: float,
        growth_rate: float,
        discount_rate: float = 0.10,
        terminal_growth: float = 0.03,
        years: int = 5,
    ) -> Dict[str, float]:
        """
        简化DCF估算内在价值区间

        Args:
            eps: 每股收益
            growth_rate: 预期增长率
            discount_rate: 折现率 (WACC)
            terminal_growth: 永续增长率
            years: 预测年限

        Returns:
            {"low": float, "mid": float, "high": float, "current_price_ratio": float}
        """
        if eps <= 0:
            return {"low": 0, "mid": 0, "high": 0, "margin_of_safety_pct": 0}

        def dcf(g):
            """给定增长率计算DCF"""
            total = 0.0
            for y in range(1, years + 1):
                future_eps = eps * (1 + g) ** y
                pv = future_eps / (1 + discount_rate) ** y
                total += pv
            # 终值
            terminal_eps = eps * (1 + g) ** years * (1 + terminal_growth)
            terminal_value = terminal_eps / (discount_rate - terminal_growth)
            pv_terminal = terminal_value / (1 + discount_rate) ** years
            total += pv_terminal
            return round(total, 2)

        low_val = dcf(max(growth_rate - 0.03, 0.01))
        mid_val = dcf(growth_rate)
        high_val = dcf(growth_rate + 0.03)

        return {
            "low": low_val,
            "mid": mid_val,
            "high": high_val,
        }

    def challenge_market_narrative(
        self,
        stock_code: str,
        market_consensus: str,
        fundamental_data: Dict[str, Any],
    ) -> Dict[str, str]:
        """
        挑战市场共识叙事 — 第一性原理的核心价值

        Args:
            stock_code: 标的代码
            market_consensus: 市场主流叙事描述
            fundamental_data: 基本面数据

        Returns:
            挑战分析
        """
        # 识别叙事中可能的问题
        narrative_flaws = []
        counter_points = []

        pe = fundamental_data.get("pe", 0)
        roe = fundamental_data.get("roe", 0)
        revenue_growth = fundamental_data.get("revenue_growth", 0)

        # 规则1: 高PE必须对应高ROE
        if pe > 30 and roe < 0.15:
            narrative_flaws.append(f"PE={pe}偏高但ROE={roe:.1%}不足，成长性可能被高估")
            counter_points.append("市场可能过度定价了未来增长，而当前盈利能力不足以支撑估值")

        # 规则2: 高增长叙事必须对应实际增长
        if revenue_growth < 0.05 and "高增长" in market_consensus:
            narrative_flaws.append(f"营收增速仅{revenue_growth:.1%}，'高增长'叙事缺乏支撑")
            counter_points.append("需重新审视增长假设，当前营收增速不支持高增长叙事")

        # 规则3: 检验"这次不一样"叙事
        if "这次不一样" in market_consensus or "新范式" in market_consensus:
            narrative_flaws.append("检测到'这次不一样'叙事 — 历史上此类叙事通常不可靠")
            counter_points.append("回归第一性原理: 企业价值=未来现金流折现，不会因叙事改变")

        return {
            "code": stock_code,
            "consensus": market_consensus,
            "flaws": narrative_flaws,
            "counter_points": counter_points,
            "first_principles_view": (
                f"从第一性原理看，该标的的核心价值取决于: "
                f"可持续ROE({roe:.1%})、增长确定性、竞争优势持久性。"
                f"当前PE({pe})蕴含的增长预期为{pe/100:.1%}。"
            ),
        }

    def generate_decision(
        self,
        driver_results: Dict[str, Any],
        market_narratives: Dict[str, str] = None,
    ) -> TheoryDecision:
        """生成第一性原理综合决策"""
        if not driver_results:
            return TheoryDecision(
                theory="第一性原理",
                signal="NEUTRAL",
                score=0.0,
                conviction="LOW",
                summary="无有效数据",
            )

        buy_count = sum(1 for r in driver_results.values() if r.get("signal") == "BUY")
        sell_count = sum(1 for r in driver_results.values() if r.get("signal") == "SELL")
        total = len(driver_results)

        if sell_count > buy_count:
            signal = "SELL"
            score = sell_count / max(total, 1)
            summary = f"第一性原理分析: {sell_count}/{total}标的ROE不足以支撑当前PE，存在高估风险"
        elif buy_count > sell_count:
            signal = "BUY"
            score = buy_count / max(total, 1)
            summary = f"第一性原理分析: {buy_count}/{total}标的ROE高于隐含增长率，存在低估机会"
        else:
            signal = "HOLD"
            score = 0.5
            summary = f"第一性原理分析: 估值与基本面基本匹配，无显著偏离"

        return TheoryDecision(
            theory="第一性原理",
            signal=signal,
            score=round(score, 4),
            conviction="HIGH" if abs(sell_count - buy_count) > total * 0.3 else "MEDIUM",
            summary=summary,
            details={
                "stock_analyses": driver_results,
                "buy_signals": buy_count,
                "sell_signals": sell_count,
                "total_analyzed": total,
            },
        )


# ============================================================
# 4. 巴菲特芒格价值投资框架
# ============================================================
class BuffettMungerFramework:
    """
    巴菲特 & 查理·芒格价值投资框架

    核心思想:
      1. 护城河 (Moat): 企业持久的竞争优势
         - 品牌(品牌溢价能力)
         - 转换成本(客户离开的成本)
         - 网络效应(用户越多价值越大)
         - 规模经济(成本随规模下降)
         - 无形资产(专利/牌照/特许权)
      2. 安全边际 (Margin of Safety): 买价远低于内在价值
      3. 能力圈 (Circle of Competence): 只投资自己能理解的企业
      4. 优质企业 (Quality): ROE持续>15%，低负债，稳定增长
      5. 长期视角: 以10年眼光看待投资

    量化实现:
      1. 护城河评分 (0~100分)
      2. 安全边际计算 (%)
      3. 能力圈置信度
      4. 企业质量综合评分
    """

    # 行业护城河基准分 (不同行业护城河天然强度不同)
    SECTOR_MOAT_BASELINE = {
        "科技": 40, "医药": 45, "能源": 35,
        "制造": 25, "金融": 30, "消费": 35,
        "商品": 20, "公用事业": 40, "电信": 35,
    }

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.moat_passing_score = self.config.get("moat_passing_score", 50)

    def evaluate_moat(
        self,
        stock_data: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        护城河评估

        Args:
            stock_data: {
                "601088": {
                    "sector": "能源", "roic": 0.12, "gross_margin": 0.35,
                    "market_share": 0.15, "revenue_10y_cagr": 0.08,
                    "brand_strength": 0.6, "patent_count": 300,
                    "switching_cost": 0.5,
                }
            }

        Returns:
            护城河评分
        """
        results = {}
        for code, data in stock_data.items():
            sector = data.get("sector", "制造")
            baseline = self.SECTOR_MOAT_BASELINE.get(sector, 25)

            # 1. 品牌/无形资产 (0~25分)
            brand_score = min(data.get("brand_strength", 0.3) * 25, 25)
            patent = data.get("patent_count", 0)
            patent_score = min((patent / 500) * 10, 10) if patent > 0 else 0
            intangible_score = brand_score + patent_score

            # 2. 转换成本 (0~20分)
            switching = data.get("switching_cost", 0.3)  # 0~1
            switching_score = min(switching * 20, 20)

            # 3. 网络效应 (0~15分)
            network = data.get("network_effect", 0.2)  # 0~1
            network_score = min(network * 15, 15)

            # 4. 规模经济/成本优势 (0~20分)
            gross_margin = data.get("gross_margin", 0.25)
            market_share = data.get("market_share", 0.05)
            scale_score = min(gross_margin * 10 + market_share * 40, 20)

            # 5. ROIC持续性 (0~20分)
            roic = data.get("roic", 0.08)
            revenue_cagr = data.get("revenue_10y_cagr", 0.05)
            quality_score = min((roic / 0.15) * 10 + (revenue_cagr / 0.10) * 10, 20)

            # 综合护城河评分
            total_moat = (
                intangible_score +
                switching_score +
                network_score +
                scale_score +
                quality_score
            )
            total_moat = max(0, min(100, total_moat))

            # 分类
            if total_moat >= 70:
                moat_level = "wide"  # 宽阔护城河
                moat_desc = "宽阔护城河 — 竞争优势持久，可长期持有"
            elif total_moat >= 50:
                moat_level = "narrow"  # 狭窄护城河
                moat_desc = "狭窄护城河 — 有一定优势但需持续关注竞争"
            elif total_moat >= 30:
                moat_level = "none"  # 无护城河
                moat_desc = "护城河薄弱 — 竞争优势不明显，需谨慎"
            else:
                moat_level = "fragile"  # 脆弱
                moat_desc = "护城河脆弱 — 竞争地位不稳定，不建议长期持有"

            results[code] = {
                "moat_score": round(total_moat, 1),
                "moat_level": moat_level,
                "moat_desc": moat_desc,
                "breakdown": {
                    "无形资产(品牌+专利)": round(intangible_score, 1),
                    "转换成本": round(switching_score, 1),
                    "网络效应": round(network_score, 1),
                    "规模经济": round(scale_score, 1),
                    "ROIC持续性": round(quality_score, 1),
                },
                "signal": "BUY" if total_moat >= 60 else
                          "HOLD" if total_moat >= 40 else "SELL",
            }

        return results

    def compute_margin_of_safety(
        self,
        intrinsic_value: float,
        market_price: float,
    ) -> Dict[str, Any]:
        """
        安全边际计算

        Margin of Safety = (Intrinsic Value - Market Price) / Intrinsic Value

        Args:
            intrinsic_value: 内在价值估算
            market_price: 市场价格

        Returns:
            安全边际分析
        """
        if intrinsic_value <= 0 or market_price <= 0:
            return {
                "margin_pct": 0,
                "level": "unknown",
                "assessment": "数据不足",
                "signal": "HOLD",
            }

        margin = (intrinsic_value - market_price) / intrinsic_value

        if margin > 0.30:
            level = "deep_value"
            assessment = f"深度价值 ({margin:.1%}安全边际) — 巴菲特会喜欢的价格"
            signal = "BUY"
        elif margin > 0.15:
            level = "value"
            assessment = f"有价值 ({margin:.1%}安全边际) — 可考虑分批建仓"
            signal = "BUY"
        elif margin > 0.05:
            level = "fair"
            assessment = f"公允价值附近 ({margin:.1%}安全边际) — 观望为主"
            signal = "HOLD"
        elif margin > -0.15:
            level = "overvalued"
            assessment = f"轻度高估 ({margin:.1%}安全边际) — 不建议新买入"
            signal = "HOLD"
        else:
            level = "significantly_overvalued"
            assessment = f"显著高估 ({margin:.1%}) — 应考虑减持"
            signal = "SELL"

        return {
            "intrinsic_value": round(intrinsic_value, 2),
            "market_price": round(market_price, 2),
            "margin_pct": round(margin * 100, 1),
            "level": level,
            "assessment": assessment,
            "signal": signal,
        }

    def assess_circle_of_competence(
        self,
        sector: str,
        user_knowledge_score: float = 0.5,
    ) -> Dict[str, Any]:
        """
        能力圈评估

        Args:
            sector: 行业
            user_knowledge_score: 用户/投资者对该行业的了解程度 (0~1)

        Returns:
            能力圈评估
        """
        # 行业理解难度 (越高越难)
        sector_difficulty = {
            "科技": 0.8, "医药": 0.85, "能源": 0.5,
            "制造": 0.6, "金融": 0.7, "消费": 0.4,
            "商品": 0.3, "公用事业": 0.35, "电信": 0.55,
        }
        difficulty = sector_difficulty.get(sector, 0.5)

        # 置信度 = 知识水平 / (知识水平 + 难度)， 但不超过1
        confidence = user_knowledge_score / (user_knowledge_score + difficulty)
        confidence = min(confidence, 1.0)

        if confidence > 0.7:
            level = "核心能力圈"
            advice = "你在该领域有深刻理解，可以做出独立判断"
        elif confidence > 0.4:
            level = "扩展能力圈"
            advice = "你有一定了解，但仍需持续学习，仓位不宜过大"
        else:
            level = "能力圈外"
            advice = "建议配置指数ETF而非个股，或加深学习后再投资"

        return {
            "sector": sector,
            "confidence": round(confidence, 3),
            "level": level,
            "advice": advice,
        }

    def get_quality_score(
        self,
        financial_data: Dict[str, float],
    ) -> Dict[str, Any]:
        """
        企业质量评分 (Quality Score)

        巴菲特芒格关注的核心财务指标:
          - ROE > 15% (持续)
          - 低负债 (D/E < 0.5)
          - 自由现金流充裕 (FCF/营收 > 10%)
          - 盈利稳定性 (过去10年无亏损)
          - 低资本支出需求 (CAPEX/营收 < 10%)
        """
        roe = financial_data.get("roe", 0.10)
        debt_to_equity = financial_data.get("debt_to_equity", 0.5)
        fcf_margin = financial_data.get("fcf_margin", 0.08)
        capex_ratio = financial_data.get("capex_ratio", 0.10)
        profit_stability = financial_data.get("profit_stability", 0.8)  # 0~1

        # 各维度评分
        roe_score = min(roe / 0.20, 1.0) * 30  # 20% ROE → 满分30
        debt_score = max(0, (1.0 - debt_to_equity)) * 20  # 零负债 → 满分20
        fcf_score = min(fcf_margin / 0.15, 1.0) * 20  # 15% FCF率 → 满分20
        capex_score = max(0, (1.0 - capex_ratio / 0.15)) * 15
        stability_score = profit_stability * 15

        total = roe_score + debt_score + fcf_score + capex_score + stability_score
        total = max(0, min(100, total))

        if total >= 70:
            grade = "A — 优质企业"
            desc = "符合巴菲特芒格标准，可重点关注"
        elif total >= 50:
            grade = "B — 良好企业"
            desc = "基本面扎实但非顶尖，需配合好价格"
        elif total >= 30:
            grade = "C — 一般企业"
            desc = "可能有周期性或竞争性问题，需深度研究"
        else:
            grade = "D — 不推荐"
            desc = "不符合价值投资标准"

        return {
            "quality_score": round(total, 1),
            "grade": grade,
            "assessment": desc,
            "breakdown": {
                "ROE持续性(30分)": round(roe_score, 1),
                "低负债(20分)": round(debt_score, 1),
                "自由现金流(20分)": round(fcf_score, 1),
                "低资本支出(15分)": round(capex_score, 1),
                "盈利稳定性(15分)": round(stability_score, 1),
            },
            "signal": "BUY" if total >= 65 else "HOLD" if total >= 40 else "SELL",
        }

    def generate_decision(
        self,
        moat_results: Dict[str, Any],
        margin_results: Dict[str, Any] = None,
        quality_results: Dict[str, Any] = None,
    ) -> TheoryDecision:
        """生成巴菲特芒格框架综合决策"""
        if not moat_results:
            return TheoryDecision(
                theory="巴菲特芒格模型",
                signal="NEUTRAL",
                score=0.0,
                conviction="LOW",
                summary="无有效数据",
            )

        # 统计各维度信号
        wide_moat = sum(1 for r in moat_results.values() if r.get("moat_level") == "wide")
        narrow_moat = sum(1 for r in moat_results.values() if r.get("moat_level") == "narrow")
        total = len(moat_results)

        deep_value_count = 0
        if margin_results:
            deep_value_count = sum(
                1 for r in margin_results.values() if r.get("level") in ("deep_value", "value")
            )

        quality_a_count = 0
        if quality_results:
            quality_a_count = sum(
                1 for r in quality_results.values() if r.get("grade", "").startswith("A")
            )

        # 综合判断: 宽阔护城河 + 安全边际 + 高质量 = 最佳投资
        if wide_moat + narrow_moat > total * 0.5 and deep_value_count > 0:
            signal = "BUY"
            conviction = "HIGH"
            summary = (
                f"巴菲特芒格框架: {wide_moat}只宽阔护城河 + {deep_value_count}只有安全边际 "
                f"→ 符合'以合理价格买入优质企业'原则"
            )
        elif wide_moat > 0 and quality_a_count > 0:
            signal = "BUY"
            conviction = "MEDIUM"
            summary = (
                f"巴菲特芒格框架: 有{wide_moat}只优质标的，但安全边际不足，"
                f"耐心等待更好价格"
            )
        elif wide_moat == 0 and narrow_moat < total * 0.3:
            signal = "SELL"
            conviction = "HIGH"
            summary = "巴菲特芒格框架: 组合中缺乏宽护城河标的，不符合长期价值投资原则"
        else:
            signal = "HOLD"
            conviction = "MEDIUM"
            summary = f"巴菲特芒格框架: {wide_moat}只宽护城河/{narrow_moat}只窄护城河，需要更多安全边际"

        return TheoryDecision(
            theory="巴菲特芒格模型",
            signal=signal,
            score=round((wide_moat + deep_value_count + quality_a_count) / max(total * 3, 1), 4),
            conviction=conviction,
            summary=summary,
            details={
                "moat_analysis": moat_results,
                "margin_analysis": margin_results or {},
                "quality_analysis": quality_results or {},
                "wide_moat_count": wide_moat,
                "deep_value_count": deep_value_count,
            },
        )


# ============================================================
# 5. 四大理论融合引擎 (Meta-Engine)
# ============================================================
class TheoryFusionEngine:
    """
    四大理论融合引擎

    将四个理论引擎的输出融合为综合决策:
      - 加权投票制 (各理论权重可配置)
      - 理论间一致性检验 (高一致性 → 高置信度)
      - 矛盾检测 (理论间冲突 → 降置信度 + 需要人工判断)
    """

    # 默认权重
    DEFAULT_WEIGHTS = {
        "索罗斯反身性": 0.20,   # 短期市场行为 → 择时
        "达利奥经济机器": 0.25, # 宏观环境 → 仓位
        "第一性原理": 0.25,     # 基本面验证 → 选股
        "巴菲特芒格模型": 0.30, # 长期价值 → 持有
    }

    SIGNAL_SCORES = {"BUY": 1.0, "HOLD": 0.5, "SELL": 0.0, "NEUTRAL": 0.5}

    def __init__(self, weights: Dict[str, float] = None):
        self.weights = weights or self.DEFAULT_WEIGHTS

    def fuse_decisions(
        self,
        decisions: List[TheoryDecision],
    ) -> Dict[str, Any]:
        """
        融合四个理论的决策为一个综合观点

        Args:
            decisions: 四个理论引擎的决策列表

        Returns:
            融合后的决策
        """
        if not decisions:
            return {
                "fused_signal": "NEUTRAL",
                "fused_score": 0.5,
                "conviction": "LOW",
                "summary": "所有理论引擎无输出",
                "agreement": 0.0,
                "conflicts": [],
            }

        # 加权综合得分
        weighted_score = 0.0
        total_weight = 0.0
        signals = []

        for d in decisions:
            theory = d.theory
            w = self.weights.get(theory, 0.25)
            s = self.SIGNAL_SCORES.get(d.signal, 0.5)
            weighted_score += w * s
            total_weight += w
            signals.append({"theory": theory, "signal": d.signal, "score": d.score, "weight": w})

        if total_weight > 0:
            weighted_score /= total_weight

        # 转换为信号
        if weighted_score > 0.65:
            fused_signal = "BUY"
        elif weighted_score < 0.35:
            fused_signal = "SELL"
        else:
            fused_signal = "HOLD"

        # 一致性分析
        signals_list = [d.signal for d in decisions if d.signal != "NEUTRAL"]
        unique_signals = set(signals_list)
        agreement = 1.0 - (len(unique_signals) - 1) / max(len(signals_list) - 1, 1) if len(signals_list) > 1 else 1.0

        # 冲突检测
        conflicts = []
        if "BUY" in unique_signals and "SELL" in unique_signals:
            buy_theories = [d.theory for d in decisions if d.signal == "BUY"]
            sell_theories = [d.theory for d in decisions if d.signal == "SELL"]
            conflicts.append(
                f"⚠️ 理论冲突: {', '.join(buy_theories)} 建议买入 vs "
                f"{', '.join(sell_theories)} 建议卖出 → 建议保持观望"
            )

        # 融合置信度
        if agreement > 0.8 and len(signals_list) >= 3:
            conviction = "HIGH"
        elif agreement > 0.5 or len(signals_list) >= 2:
            conviction = "MEDIUM"
        else:
            conviction = "LOW"

        # 综合摘要
        summaries = [d.summary for d in decisions if d.summary]
        combined_summary = " | ".join(summaries[:3])  # 取前3条

        return {
            "fused_signal": fused_signal,
            "fused_score": round(weighted_score, 4),
            "conviction": conviction,
            "summary": combined_summary,
            "agreement": round(agreement, 3),
            "conflicts": conflicts,
            "individual_decisions": [
                {
                    "theory": d.theory,
                    "signal": d.signal,
                    "score": d.score,
                    "conviction": d.conviction,
                    "summary": d.summary,
                }
                for d in decisions
            ],
        }

    def generate_fusion_report(self, fusion_result: Dict[str, Any]) -> str:
        """生成融合报告 Markdown 文本"""
        lines = []
        lines.append("## 🧬 四大理论融合决策")
        lines.append("")

        fs = fusion_result.get("fused_signal", "NEUTRAL")
        fsc = fusion_result.get("fused_score", 0.5)
        symbols = {"BUY": "📈", "SELL": "📉", "HOLD": "⏸️", "NEUTRAL": "⏸️"}
        symbol = symbols.get(fs, "⏸️")

        agreement = fusion_result.get("agreement", 0)
        ag_str = f"理论一致度: {agreement:.0%}"
        lines.append(f"### {symbol} 综合信号: **{fs}** (得分: {fsc:.3f})")
        lines.append(f"置信度: {fusion_result.get('conviction', 'N/A')} | {ag_str}")
        lines.append("")

        # 各理论投票
        lines.append("| 理论框架 | 信号 | 得分 | 置信度 | 权重 | 摘要 |")
        lines.append("|---------|------|------|--------|------|------|")
        for d in fusion_result.get("individual_decisions", []):
            t = d["theory"]
            s = d["signal"]
            sc = d["score"]
            cv = d["conviction"]
            w = self.weights.get(t, 0.25)
            sm = d["summary"][:60] + "..." if len(d["summary"]) > 60 else d["summary"]
            signal_icon = "🟢" if s == "BUY" else "🔴" if s == "SELL" else "🟡"
            lines.append(f"| {t} | {signal_icon} {s} | {sc:.2f} | {cv} | {w:.0%} | {sm} |")
        lines.append("")

        # 冲突警告
        conflicts = fusion_result.get("conflicts", [])
        if conflicts:
            lines.append("### ⚠️ 理论冲突预警")
            for c in conflicts:
                lines.append(f"- {c}")
            lines.append("")

        lines.append(f"*融合引擎由四大理论加权投票生成 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
        return "\n".join(lines)


# ============================================================
# 6. 便捷函数 — 一键运行全部理论分析
# ============================================================
def run_full_theory_analysis(
    price_data: Dict[str, Any],
    macro_data: Dict[str, Any] = None,
    financial_data: Dict[str, Any] = None,
    sector_map: Dict[str, str] = None,
) -> Dict[str, Any]:
    """
    一键运行四大理论完整分析

    Args:
        price_data: 价格数据 {"601088": {"price": 41.26, "change_20d": -0.05, ...}}
        macro_data: 宏观数据 {"pmi": 50.2, "cpi": 0.3, "debt_to_gdp": 2.8, ...}
        financial_data: 财务数据 {"601088": {"sector": "能源", "roe": 0.15, "pe": 10.5, ...}}
        sector_map: 行业映射 {"601088": "能源", ...}

    Returns:
        完整分析结果
    """
    macro_data = macro_data or {}
    financial_data = financial_data or {}
    sector_map = sector_map or {}

    # 为价格数据补充sector信息
    for code in price_data:
        if code not in financial_data:
            financial_data[code] = {}
        if "sector" not in financial_data[code]:
            financial_data[code]["sector"] = sector_map.get(code, "制造")

    decisions = []

    # 1. 索罗斯反身性
    try:
        reflexivity = SorosReflexivityEngine()
        reflex_results = reflexivity.compute_reflexivity_score(price_data)
        reflex_decision = reflexivity.generate_decision(reflex_results)
        decisions.append(reflex_decision)
    except Exception as e:
        logger.warning(f"索罗斯反身性分析失败: {e}")

    # 2. 达利奥经济机器
    try:
        dalio = DalioEconomicMachine()
        growth_data = {
            "pmi": macro_data.get("pmi", 50.0),
            "gdp_growth": macro_data.get("gdp_growth", 5.0),
        }
        inflation_data = {
            "cpi": macro_data.get("cpi", 1.5),
            "ppi": macro_data.get("ppi", 0.0),
        }
        regime = dalio.classify_economic_regime(growth_data, inflation_data)

        debt_data = {
            "debt_to_gdp": macro_data.get("debt_to_gdp", 2.8),
            "credit_growth_yoy": macro_data.get("credit_growth", 0.08),
            "policy_rate": macro_data.get("policy_rate", 0.03),
        }
        debt = dalio.assess_debt_cycle(debt_data)
        dalio_decision = dalio.generate_decision(regime, debt)
        decisions.append(dalio_decision)
    except Exception as e:
        logger.warning(f"达利奥经济机器分析失败: {e}")

    # 3. 第一性原理
    try:
        fpa = FirstPrinciplesAnalyzer()
        driver_results = fpa.decompose_value_drivers(financial_data)
        fpa_decision = fpa.generate_decision(driver_results)
        decisions.append(fpa_decision)
    except Exception as e:
        logger.warning(f"第一性原理分析失败: {e}")

    # 4. 巴菲特芒格框架
    try:
        bm = BuffettMungerFramework()
        moat_results = bm.evaluate_moat(financial_data)
        bm_decision = bm.generate_decision(moat_results)
        decisions.append(bm_decision)
    except Exception as e:
        logger.warning(f"巴菲特芒格分析失败: {e}")

    # 5. 融合
    fusion = TheoryFusionEngine()
    fusion_result = fusion.fuse_decisions(decisions)

    return {
        "fusion": fusion_result,
        "individual_decisions": [d.to_dict() for d in decisions],
        "timestamp": datetime.now().isoformat(),
    }


# ============================================================
# 测试入口
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("  四大决策理论引擎 测试")
    print("=" * 60)

    # 模拟数据
    test_price_data = {
        "601088": {"price": 41.26, "change_20d": -0.05, "z_score": -1.2, "volatility": 0.22},
        "600276": {"price": 50.47, "change_20d": 0.12, "z_score": 1.8, "volatility": 0.26},
        "300308": {"price": 120.0, "change_20d": 0.25, "z_score": 2.5, "volatility": 0.35},
    }

    test_financial_data = {
        "601088": {"sector": "能源", "pe": 10.5, "roe": 0.15, "roic": 0.12, "gross_margin": 0.35, "market_share": 0.15, "brand_strength": 0.7, "debt_to_equity": 0.3, "fcf_margin": 0.12, "profit_stability": 0.9},
        "600276": {"sector": "医药", "pe": 45.0, "roe": 0.22, "roic": 0.18, "gross_margin": 0.85, "market_share": 0.08, "brand_strength": 0.8, "patent_count": 500, "debt_to_equity": 0.2, "fcf_margin": 0.15, "profit_stability": 0.95},
        "300308": {"sector": "科技", "pe": 35.0, "roe": 0.18, "roic": 0.15, "gross_margin": 0.45, "market_share": 0.12, "brand_strength": 0.6, "patent_count": 200, "debt_to_equity": 0.25, "fcf_margin": 0.10, "profit_stability": 0.85},
    }

    test_macro = {
        "pmi": 50.2, "cpi": 0.3, "ppi": -2.1,
        "debt_to_gdp": 2.8, "credit_growth": 0.082, "policy_rate": 0.03,
    }

    print("\n运行完整分析...")
    result = run_full_theory_analysis(
        price_data=test_price_data,
        macro_data=test_macro,
        financial_data=test_financial_data,
    )

    # 打印融合报告
    fusion = TheoryFusionEngine()
    report = fusion.generate_fusion_report(result["fusion"])
    print("\n" + report)

    print("\n" + "=" * 60)
    print("  测试完成")
    print("=" * 60)
