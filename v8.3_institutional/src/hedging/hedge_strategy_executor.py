"""
对冲策略执行层 (B3.3: 从 hedge_engine_v59.py 抽取)
=====================================================

本模块集中以下职责:
  - determine_hedge_signal_strength (五因子信号强度评估)
  - compute_optimal_hedge_ratio (最优对冲比率计算)
  - generate_futures_hedge (多指数Beta加权期货对冲方案)
  - generate_options_hedge (期权对冲方案: protective_put / collar / put_spread)
  - generate_hedge_plan (完整对冲方案生成 + 压力测试联动)
  - _generate_hedge_reason (对冲理由文案)

HedgeEngine 保留为 thin coordinator, 策略执行统一委托到本模块。

依赖:
  - PortfolioRisk (from ..risk.portfolio_risk_assessor)
  - HedgeType / HedgeSignalStrength / HedgeRecommendation (本地定义)
  - INDEX_FUTURES_SPECS / ETF_OPTIONS_SPECS (本地常量)
  - get_live_futures_prices / DEFAULT_FUTURES_PRICES (from ..data.futures_prices)
"""

import logging
import math
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

# 双路径导入: 支持 src.hedging (正常包) 和 hedging (测试 sys.path) 两种加载方式
try:
    from ..data.futures_prices import DEFAULT_FUTURES_PRICES, get_live_futures_prices
    from ..risk.portfolio_risk_assessor import PortfolioRisk, run_historical_stress_tests
except (ImportError, ValueError):  # pragma: no cover — 测试 sys.path 兼容路径
    from data.futures_prices import DEFAULT_FUTURES_PRICES, get_live_futures_prices
    from risk.portfolio_risk_assessor import PortfolioRisk, run_historical_stress_tests

logger = logging.getLogger("hedge_engine")


# ── 对冲类型枚举 (B3.3: 从 hedge_types 共享, 避免重复定义) ──
from .hedge_types import (  # noqa: E402
    HedgeRecommendation,
    HedgeSignalStrength,
    HedgeType,
)

# ============================================================
# B-4.6: 指数规格常量已抽取到 hedging/index_specs.py (从 config/index_specs.yaml 加载)
# 双路径导入: 支持 src.hedging (正常包) 和 hedging (测试 sys.path) 两种加载方式
# ============================================================
try:
    from .index_specs import ETF_OPTIONS_SPECS, INDEX_FUTURES_SPECS  # noqa: E402
except (ImportError, ValueError):  # pragma: no cover — 测试 sys.path 兼容路径
    from index_specs import ETF_OPTIONS_SPECS, INDEX_FUTURES_SPECS  # noqa: E402


# ── v5.9 对冲参数常量 ──
VOLATILITY_TARGET_ANNUAL = 0.18
HEDGE_ROLL_COST_ANNUAL = 0.025  # 年化展期成本(基差+交易费)
HEDGE_MARGIN_OPP_COST = 0.020  # 保证金机会成本(按无风险利率)

# v5.9 多指数Beta分配权重 (指数优先级的判定基于组合在各指数的暴露度)
INDEX_ALLOCATION_ORDER = ["IC", "IM", "IF"]

# v5.9 组合自触发阈值
PORTFOLIO_TAIL_HEDGE_TRIGGERS = {
    "vol_trigger": 0.28,  # 年化波动率>28%触发
    "dd_trigger": 0.12,  # 60日最大回撤>12%触发
    "min_hedge_ratio": 0.25,  # 触发后最小对冲比率
    "max_hedge_ratio": 0.50,  # 触发后最大对冲比率
}

# v5.9 成本效益阈值
COST_BENEFIT_THRESHOLD = 1.5  # 预期对冲收益必须 > 对冲成本 * 1.5 才激活


# ============================================================
# 对冲建议数据类 (B3.3: 从 hedge_types 共享, 此处不再重复定义)
# ============================================================
# 注: HedgeRecommendation 已从 hedge_types.py 导入 (见上方)


# ============================================================
# 策略执行函数
# ============================================================


def determine_hedge_signal_strength(
    risk: PortfolioRisk,
    market_signals: Optional[Dict[str, Any]] = None,
    portfolio_volatility: Optional[float] = None,
    portfolio_drawdown_60d: Optional[float] = None,
) -> Tuple["HedgeSignalStrength", float]:
    """v5.9 五因子模型 — 组合自触发权重提升

    因子权重:
    1. 组合Beta因子 (25%, 从40%降低) — 降权,回测证明CSI300Beta不准确
    2. 组合自波动率因子 (25%, v5.9新增) — 组合自身波动率超过阈值
    3. 组合自回撤因子 (20%, v5.9新增) — 60日最大回撤触发
    4. 集中度因子 (15%, 从20%降低)
    5. VaR尾部风险 (10%)
    6. 外部市场信号 (5%, 从10%降低)
    """
    score = 0.0
    reasons = []

    # 1. Beta因子 (权重25%, v5.9从40%降低)
    beta = max(risk.beta_csi300, risk.beta_csi500, risk.beta_csi1000)
    if beta > 1.5:
        score += 0.25
        reasons.append(f"组合Beta={beta:.2f}(取最大)较高")
    elif beta > 1.2:
        score += 0.15
        reasons.append(f"组合Beta={beta:.2f}偏高")
    elif beta > 0.8:
        score += 0.08

    # 2. 组合自波动率因子 (权重25%, v5.9新增)
    if portfolio_volatility is not None and portfolio_volatility > 0:
        current_vol = portfolio_volatility
    else:
        current_vol = risk.volatility_30d * math.sqrt(252) if risk.volatility_30d > 0 else 0.18

    vol_trigger = PORTFOLIO_TAIL_HEDGE_TRIGGERS["vol_trigger"]
    if current_vol > vol_trigger * 1.2:
        score += 0.25
        reasons.append(f"组合波动率{current_vol * 100:.1f}%严重超标(>{vol_trigger * 120:.0f}%)")
    elif current_vol > vol_trigger:
        score += 0.18
        reasons.append(f"组合波动率{current_vol * 100:.1f}%超标(>{vol_trigger * 100:.0f}%)")
    elif current_vol > vol_trigger * 0.8:
        score += 0.08

    # 3. 组合自回撤因子 (权重20%, v5.9新增)
    dd_trigger = PORTFOLIO_TAIL_HEDGE_TRIGGERS["dd_trigger"]
    if portfolio_drawdown_60d is not None and portfolio_drawdown_60d > 0:
        if portfolio_drawdown_60d > dd_trigger * 1.5:
            score += 0.20
            reasons.append(f"组合60日回撤{portfolio_drawdown_60d * 100:.1f}%严重(>{dd_trigger * 150:.0f}%)")
        elif portfolio_drawdown_60d > dd_trigger:
            score += 0.14
            reasons.append(f"组合60日回撤{portfolio_drawdown_60d * 100:.1f}%超标(>{dd_trigger * 100:.0f}%)")
        elif portfolio_drawdown_60d > dd_trigger * 0.7:
            score += 0.06

    # 4. 集中度因子 (权重15%)
    if risk.concentration_risk > 0.25:
        score += 0.15
        reasons.append(f"集中度HHI={risk.concentration_risk:.3f}过高")
    elif risk.concentration_risk > 0.15:
        score += 0.08

    # 5. VaR因子 (权重10%)
    var_pct = risk.var_95_daily / risk.total_value if risk.total_value > 0 else 0
    if var_pct > 0.03:
        score += 0.10
        reasons.append(f"日VaR(95%)={var_pct * 100:.1f}%")
    elif var_pct > 0.02:
        score += 0.05

    # 6. 外部市场信号 (权重5%, v5.9大幅降低)
    if market_signals:
        panic = market_signals.get("panic_index", 0)
        if panic > 0.75:
            score += 0.05
            reasons.append("外部恐慌指数极高")

    # 信号强度判定
    if score >= 0.65:
        strength = HedgeSignalStrength.STRONG
    elif score >= 0.50:
        strength = HedgeSignalStrength.MODERATE
    elif score >= 0.35:
        strength = HedgeSignalStrength.LIGHT
    else:
        strength = HedgeSignalStrength.NO_HEDGE

    return strength, score


def compute_optimal_hedge_ratio(
    risk: PortfolioRisk,
    hedge_strength: "HedgeSignalStrength",
    method: str = "min_variance",
    portfolio_volatility: Optional[float] = None,
    portfolio_drawdown_60d: Optional[float] = None,
) -> float:
    """v5.9 最优对冲比率 — 组合自触发为上限

    核心逻辑: 对冲比率 = min(Beta中性比率, 尾部保护比率)
    尾部保护仅在组合自身波动率>28%或回撤>12%时显著激活。
    """
    strength_ratio = {
        HedgeSignalStrength.NO_HEDGE: 0.0,
        HedgeSignalStrength.LIGHT: 0.25,
        HedgeSignalStrength.MODERATE: 0.50,
        HedgeSignalStrength.STRONG: 0.75,
        HedgeSignalStrength.FULL: 1.0,
    }
    base_ratio = strength_ratio[hedge_strength]

    # Beta中性比率（使用多指数最大Beta）
    max_beta = max(risk.beta_csi300, risk.beta_csi500, risk.beta_csi1000, 0.5)
    beta_neutral_ratio = max_beta * base_ratio * 0.70  # 70%因子考虑基差
    beta_neutral_ratio = min(beta_neutral_ratio, 1.0)

    # ── v5.9 核心: 组合自触发尾部保护比率 ──
    if portfolio_volatility is None:
        portfolio_volatility = risk.volatility_30d * math.sqrt(252) if risk.volatility_30d > 0 else 0.18

    vol_trigger = PORTFOLIO_TAIL_HEDGE_TRIGGERS["vol_trigger"]
    dd_trigger = PORTFOLIO_TAIL_HEDGE_TRIGGERS["dd_trigger"]
    max_ratio = PORTFOLIO_TAIL_HEDGE_TRIGGERS["max_hedge_ratio"]
    min_ratio = PORTFOLIO_TAIL_HEDGE_TRIGGERS["min_hedge_ratio"]

    tail_ratio = 0.0

    # 波动率触发
    if portfolio_volatility > vol_trigger:
        excess = portfolio_volatility - vol_trigger
        tail_ratio = min_ratio + excess * 2.5  # 每超出1%vol增加2.5%对冲
        tail_ratio = min(tail_ratio, max_ratio)

    # 回撤触发（叠加）
    if portfolio_drawdown_60d is not None and portfolio_drawdown_60d > dd_trigger:
        dd_excess = portfolio_drawdown_60d - dd_trigger
        dd_tail = min_ratio + dd_excess * 3.0
        dd_tail = min(dd_tail, max_ratio)
        tail_ratio = max(tail_ratio, dd_tail)

    # 融合: 取Beta中性(上限)和尾部保护的最小值
    # 尾保模式: 仅在极端行情激活, 日常不打扰
    final_ratio = min(beta_neutral_ratio + tail_ratio * 0.5, max(beta_neutral_ratio, tail_ratio))

    # 成本效益过滤
    if hedge_strength == HedgeSignalStrength.NO_HEDGE:
        return 0.0

    # 对冲成本估算(年化)
    hedge_cost_annual = final_ratio * (HEDGE_ROLL_COST_ANNUAL + HEDGE_MARGIN_OPP_COST)
    # 预期收益(仅尾保部分做减法)
    expected_benefit = tail_ratio * 0.08  # 尾保预期降低8%*ratio的回撤

    if expected_benefit < hedge_cost_annual * COST_BENEFIT_THRESHOLD and tail_ratio < 0.10:
        logger.info(
            f"[成本效益] 对冲预期收益{expected_benefit * 100:.1f}% < "
            f"成本{hedge_cost_annual * 100:.1f}% * {COST_BENEFIT_THRESHOLD}, 降为0"
        )
        return 0.0

    return min(final_ratio, 1.0)


def generate_futures_hedge(
    risk: PortfolioRisk,
    hedge_ratio: float,
    futures_prices: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """v5.9 多指数Beta加权对冲方案

    IC/IM/IF按组合Beta比例分配, 不再单一依赖IF。
    """
    if hedge_ratio <= 0:
        return {
            "contracts": {},
            "total_notional": 0,
            "total_margin": 0,
            "reason": "对冲比率=0, 无需求",
            "price_source": "N/A",
            "fallback_used": [],
        }

    price_source = "user_provided"
    if not futures_prices:
        futures_prices = get_live_futures_prices()
        price_source = "auto"

    fallback_used = []
    for code in ["IF", "IC", "IM", "IH"]:
        if code in futures_prices and abs(futures_prices[code] - DEFAULT_FUTURES_PRICES.get(code, 0)) < 0.1:
            fallback_used.append(code)

    if fallback_used:
        logger.warning(f"[!] 回退价格品种: {', '.join(fallback_used)}")

    # ── v5.9 多指数Beta加权分配 ──
    beta_map = {
        "IC": risk.beta_csi500,
        "IM": risk.beta_csi1000,
        "IF": risk.beta_csi300,
    }

    # 计算每个指数的对冲分配权重
    total_beta = sum(max(b, 0) for b in beta_map.values())
    if total_beta <= 0:
        return {
            "contracts": {},
            "total_notional": 0,
            "total_margin": 0,
            "reason": "组合Beta<=0, 无需对冲",
            "price_source": price_source,
            "fallback_used": fallback_used,
        }

    hedge_notional_total = risk.stock_exposure * hedge_ratio

    result = {}
    remaining = hedge_notional_total

    # 按Beta比例分配, 优先IC和IM
    allocation_order = ["IC", "IM", "IF"]
    for code in allocation_order:
        beta = max(beta_map[code], 0)
        if beta <= 0 or remaining <= 0:
            continue

        spec = INDEX_FUTURES_SPECS[code]
        price = futures_prices.get(code, 0)
        multiplier = spec["multiplier"]

        if price <= 0:
            continue

        # 该指数分配的名义价值 = 总对冲 * (该指数Beta/总Beta)
        alloc_ratio = beta / total_beta
        alloc_notional = hedge_notional_total * alloc_ratio

        contract_value = price * multiplier
        contracts = max(1, round(alloc_notional / contract_value))
        notional = contracts * contract_value
        margin = notional * spec["margin_pct"]

        result[code] = {
            "contracts": contracts,
            "price": price,
            "notional": notional,
            "margin": margin,
            "spec": spec,
            "direction": "SELL",
            "alloc_ratio": alloc_ratio,
        }

        remaining -= notional

    total_notional = sum(v["notional"] for v in result.values())
    total_margin = sum(v["margin"] for v in result.values())

    # 生成理由
    parts = []
    if risk.beta_csi500 > 1.2:
        parts.append(f"CSI500 Beta={risk.beta_csi500:.2f}")
    if risk.beta_csi1000 > 1.2:
        parts.append(f"CSI1000 Beta={risk.beta_csi1000:.2f}")
    if risk.beta_csi300 > 1.0:
        parts.append(f"CSI300 Beta={risk.beta_csi300:.2f}")

    for code, detail in result.items():
        spec = detail.get("spec", {})
        name = spec.get("name", code)
        n = detail["contracts"]
        parts.append(f"做空{n}手{name}")

    reason = "; ".join(parts) if parts else f"多指数对冲{hedge_ratio * 100:.0f}%敞口"
    if fallback_used:
        reason += f" [!]{','.join(fallback_used)}为回退价格"

    return {
        "contracts": result,
        "total_notional": total_notional,
        "total_margin": total_margin,
        "target_hedge_notional": hedge_notional_total,
        "reason": reason,
        "price_source": price_source,
        "fallback_used": fallback_used,
    }


def generate_options_hedge(
    risk: PortfolioRisk,
    hedge_ratio: float,
    options_data: Optional[Dict[str, Any]] = None,
    strategy: str = "protective_put",
) -> Dict[str, Any]:
    """期权对冲方案 — protective_put / collar / put_spread"""
    if hedge_ratio <= 0:
        return {"contracts": [], "total_cost": 0, "reason": "对冲比率=0"}

    default_iv = 0.22
    underlying = "510300" if risk.beta_csi300 > risk.beta_csi500 else "510050"
    index_price = 3950 if underlying == "510300" else 2700
    hedge_notional = risk.stock_exposure * hedge_ratio

    if strategy == "protective_put":
        T = 1 / 12
        atm_put_premium_pct = 0.4 * default_iv * math.sqrt(T)
        atm_put_premium = index_price * atm_put_premium_pct
        multiplier = ETF_OPTIONS_SPECS[underlying]["multiplier"]
        one_contract_hedge = index_price * multiplier
        contracts = max(1, round(hedge_notional / one_contract_hedge))
        total_premium = contracts * atm_put_premium * multiplier

        return {
            "strategy": "protective_put",
            "underlying": underlying,
            "contracts": contracts,
            "strike_type": "ATM",
            "estimated_premium_pct": round(atm_put_premium_pct * 100, 2),
            "total_premium": round(total_premium, 0),
            "total_premium_pct": round(total_premium / risk.total_value * 100, 2) if risk.total_value > 0 else 0,
            "max_protection": round(hedge_notional, 0),
            "reason": f"保护性看跌: {contracts}手{underlying} ATM Put, 权利金{total_premium:,.0f}元",
            "risk": "最大损失=权利金",
        }

    elif strategy == "collar":
        T = 1 / 12
        atm_put_pct = 0.4 * default_iv * math.sqrt(T)
        otm_put_pct = atm_put_pct * 0.7
        otm_call_pct = atm_put_pct * 0.8
        net_cost_pct = otm_put_pct - otm_call_pct
        multiplier = ETF_OPTIONS_SPECS[underlying]["multiplier"]
        one_contract_hedge = index_price * multiplier
        contracts = max(1, round(hedge_notional / one_contract_hedge))
        net_cost = contracts * net_cost_pct * index_price * multiplier

        return {
            "strategy": "collar",
            "underlying": underlying,
            "contracts": contracts,
            "net_cost": round(net_cost, 0),
            "put_strike": f"{index_price * 0.95:.0f} (OTM 95%)",
            "call_strike": f"{index_price * 1.05:.0f} (OTM 105%)",
            "net_cost_pct": round(net_cost / risk.total_value * 100, 2) if risk.total_value > 0 else 0,
            "reason": f"领口: {contracts}手, 净成本{net_cost:,.0f}元",
            "risk": "上行收益封顶+5%",
        }

    elif strategy == "put_spread":
        T = 1 / 12
        atm_put_pct = 0.4 * default_iv * math.sqrt(T)
        sell_otm_put_pct = atm_put_pct * 0.45
        spread_cost_pct = atm_put_pct - sell_otm_put_pct
        multiplier = ETF_OPTIONS_SPECS[underlying]["multiplier"]
        one_contract_hedge = index_price * multiplier
        contracts = max(1, round(hedge_notional / one_contract_hedge))
        spread_cost = contracts * spread_cost_pct * index_price * multiplier

        return {
            "strategy": "put_spread",
            "underlying": underlying,
            "contracts": contracts,
            "spread_cost": round(spread_cost, 0),
            "buy_put_strike": f"{index_price:.0f} (ATM)",
            "sell_put_strike": f"{index_price * 0.90:.0f} (OTM 90%)",
            "max_profit": round(hedge_notional * 0.10, 0),
            "reason": f"看跌价差: {contracts}手, 成本{spread_cost:,.0f}元",
            "risk": "保护10%跌幅",
        }

    return {"contracts": [], "total_cost": 0, "reason": "未知策略"}


def generate_hedge_plan(
    risk: PortfolioRisk,
    market_signals: Optional[Dict[str, Any]] = None,
    futures_prices: Optional[Dict[str, float]] = None,
    prefer_options: bool = False,
    portfolio_volatility: Optional[float] = None,
    portfolio_drawdown_60d: Optional[float] = None,
    positions: Optional[Dict[str, Dict[str, Any]]] = None,
    prices: Optional[Dict[str, float]] = None,
) -> "HedgeRecommendation":
    """v5.10 完整对冲方案 — 组合自触发增强 + P0-8压力测试"""
    recommendation = HedgeRecommendation()
    recommendation.timestamp = datetime.now().isoformat()
    recommendation.risk_signals = market_signals or {}

    # v5.10 P0-8: 历史极端压力测试 (始终运行)
    if positions and prices:
        recommendation.stress_tests = run_historical_stress_tests(positions, prices)

    # v5.10 P0-6: 板块集中度 + MRC 预警
    if risk.sector_concentration_warning:
        recommendation.sector_warnings.append(risk.sector_concentration_warning)
    recommendation.mrc_warnings = risk.mrc_warnings

    # v5.10 P0-7: 相关性预警
    recommendation.correlation_warning = risk.correlation_warning

    strength, score = determine_hedge_signal_strength(
        risk, market_signals, portfolio_volatility, portfolio_drawdown_60d
    )
    recommendation.strength = strength
    recommendation.urgency_score = score

    if strength == HedgeSignalStrength.NO_HEDGE:
        recommendation.hedge_type = HedgeType.NONE
        recommendation.reasoning = "组合风险可控(自波动率+回撤均在安全范围), 无需对冲"
        return recommendation

    recommendation.hedge_type = HedgeType.PUT_PROTECTIVE if prefer_options else HedgeType.FUTURES_SHORT
    hedge_ratio = compute_optimal_hedge_ratio(
        risk,
        strength,
        method="min_variance",
        portfolio_volatility=portfolio_volatility,
        portfolio_drawdown_60d=portfolio_drawdown_60d,
    )
    recommendation.hedge_ratio = hedge_ratio

    if hedge_ratio <= 0:
        recommendation.hedge_type = HedgeType.NONE
        recommendation.reasoning = "对冲经成本效益分析后判定不划算(预期收益<1.5倍成本)"
        return recommendation

    if not prefer_options:
        futures_result = generate_futures_hedge(risk, hedge_ratio, futures_prices)
        recommendation.futures_instruments = list(futures_result.get("contracts", {}).keys())

        contracts = {}
        notionals = {}
        margins = {}
        for code, detail in futures_result.get("contracts", {}).items():
            contracts[code] = detail["contracts"]
            notionals[code] = detail["notional"]
            margins[code] = detail["margin"]

        recommendation.futures_contracts = contracts
        recommendation.futures_notional = notionals
        recommendation.futures_margin = margins
        recommendation.effective_hedge_pct = (
            futures_result.get("total_notional", 0) / risk.stock_exposure if risk.stock_exposure > 0 else 0
        )
        hedge_reason = futures_result.get("reason", "")
    else:
        options_result = generate_options_hedge(
            risk, hedge_ratio, strategy="protective_put" if score < 0.5 else "put_spread"
        )
        recommendation.options_instruments = [options_result.get("underlying", "510300")]
        recommendation.options_strategy = options_result.get("strategy", "")
        recommendation.options_contracts = [
            {
                "underlying": options_result.get("underlying"),
                "contracts": options_result.get("contracts", 0),
                "strategy": options_result.get("strategy"),
                "cost": options_result.get("total_premium", options_result.get("spread_cost", 0)),
            }
        ]
        recommendation.options_cost = options_result.get("total_premium", options_result.get("spread_cost", 0))
        recommendation.effective_hedge_pct = hedge_ratio
        hedge_reason = options_result.get("reason", "")

    max_beta = max(risk.beta_csi300, risk.beta_csi500, risk.beta_csi1000)
    recommendation.expected_beta_after = max_beta * (1 - hedge_ratio)
    recommendation.expected_drawdown_reduce = hedge_ratio * 0.4

    strength_names = {
        HedgeSignalStrength.LIGHT: "轻度",
        HedgeSignalStrength.MODERATE: "中度",
        HedgeSignalStrength.STRONG: "强力",
        HedgeSignalStrength.FULL: "完全",
    }

    vol_info = ""
    if portfolio_volatility and portfolio_volatility > 0:
        vol_info = f"组合波动率={portfolio_volatility * 100:.1f}%, "
    dd_info = ""
    if portfolio_drawdown_60d and portfolio_drawdown_60d > 0:
        dd_info = f"60日回撤={portfolio_drawdown_60d * 100:.1f}%, "

    recommendation.reasoning = (
        f"{strength_names.get(strength, '')}对冲 (评分={score:.2f})。"
        f"最大Beta={max_beta:.2f}, {vol_info}{dd_info}"
        f"对冲比率={hedge_ratio * 100:.0f}%。"
        f"{hedge_reason}"
    )

    return recommendation


def generate_hedge_reason(risk: PortfolioRisk, hedge_ratio: float, contracts: Dict[str, Any]) -> str:
    """生成对冲理由文案"""
    parts = []
    for code, detail in contracts.items():
        spec = detail.get("spec", {})
        name = spec.get("name", code)
        n = detail["contracts"]
        parts.append(f"做空{n}手{name}")
    return "; ".join(parts) if parts else f"对冲{hedge_ratio * 100:.0f}%股票敞口"


__all__ = [
    # 枚举与数据类
    "HedgeType",
    "HedgeSignalStrength",
    "HedgeRecommendation",
    # 常量
    "INDEX_FUTURES_SPECS",
    "ETF_OPTIONS_SPECS",
    "VOLATILITY_TARGET_ANNUAL",
    "HEDGE_ROLL_COST_ANNUAL",
    "HEDGE_MARGIN_OPP_COST",
    "INDEX_ALLOCATION_ORDER",
    "PORTFOLIO_TAIL_HEDGE_TRIGGERS",
    "COST_BENEFIT_THRESHOLD",
    # 策略执行函数
    "determine_hedge_signal_strength",
    "compute_optimal_hedge_ratio",
    "generate_futures_hedge",
    "generate_options_hedge",
    "generate_hedge_plan",
    "generate_hedge_reason",
]
