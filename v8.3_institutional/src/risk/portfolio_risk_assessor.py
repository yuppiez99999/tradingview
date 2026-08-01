"""
组合风险评估层 (B3.3: 从 hedge_engine_v59.py 抽取)
=====================================================

本模块集中以下职责:
  - PortfolioRisk 数据类
  - assess_portfolio_risk (组合 Beta / VaR / CVaR / 集中度 / 相关性 / MRC)
  - _compute_weighted_beta / _compute_portfolio_vol_cov / _compute_expected_shortfall / _compute_mrc
  - DEFAULT_BETAS / SECTOR_MAP / HISTORICAL_STRESS_SCENARIOS 等风险常量
  - run_historical_stress_tests (历史极端情景压力测试)

HedgeEngine 保留为对冲信号 + 对冲方案生成器, 风险评估统一委托到本模块。

数据源: 历史收益率字典 (由调用方提供)
依赖: math / logging
"""

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("hedge_engine")


# ── 风险评估数据类 ──
@dataclass
class PortfolioRisk:
    """组合风险评估"""

    total_value: float = 0.0
    stock_exposure: float = 0.0
    cash: float = 0.0
    beta_csi300: float = 0.0
    beta_csi500: float = 0.0
    beta_csi1000: float = 0.0
    beta_sse50: float = 0.0
    volatility_30d: float = 0.0
    var_95_daily: float = 0.0
    cvar_95_daily: float = 0.0
    max_drawdown_current: float = 0.0
    correlation_matrix: Dict[str, float] = field(default_factory=dict)
    concentration_risk: float = 0.0
    # v5.10 P0-6/P0-7: 集中度和相关性增强
    sector_weights: Dict[str, float] = field(default_factory=dict)
    max_sector_weight: float = 0.0
    sector_concentration_warning: str = ""
    avg_pairwise_correlation: float = 0.0
    correlation_warning: str = ""
    mrc_warnings: List[str] = field(default_factory=list)
    max_single_mrc: float = 0.0


# ── v5.10: 类级别Beta常量 — 供压力测试和组合Beta计算复用 ──
DEFAULT_BETAS = {
    "300308": (1.35, 1.50, 1.60, 1.20),
    "688041": (1.40, 1.55, 1.70, 1.25),
    "002371": (1.25, 1.40, 1.50, 1.15),
    "688981": (1.30, 1.45, 1.55, 1.20),
    "300750": (1.20, 1.35, 1.45, 1.10),
    "000425": (1.05, 1.15, 1.25, 0.95),
    "601088": (0.85, 0.80, 0.75, 0.90),
    "600219": (0.90, 0.95, 1.05, 0.85),
    "600019": (0.95, 1.00, 1.10, 0.90),
    "518880": (0.40, 0.35, 0.30, 0.45),
    "000792": (0.80, 0.90, 1.00, 0.75),
    "600276": (0.75, 0.70, 0.65, 0.80),
    "603259": (0.90, 0.95, 1.00, 0.85),
    "002422": (0.70, 0.65, 0.60, 0.75),
}

# v5.10 P0-6: 板块映射 — 集中度监控
SECTOR_MAP = {
    "300308": "高端制造",
    "688041": "高端制造",
    "002371": "高端制造",
    "688981": "高端制造",
    "300750": "高端制造",
    "000425": "高端制造",
    "601088": "顺周期",
    "600219": "顺周期",
    "600019": "顺周期",
    "518880": "黄金ETF",
    "000792": "资源",
    "600276": "防御",
    "603259": "防御",
    "002422": "防御",
    "600900": "防御",  # 长江电力
    "511010": "国债ETF",  # 固收敞口
}

# 固收/国债类不计入股票板块集中度
FIXED_INCOME_TYPES = {"国债ETF"}

SECTOR_LIMIT = 0.35  # 单一板块上限35% (P0-6)
MRC_LIMIT = 0.25  # 单标的边际风险贡献上限25%
CORRELATION_WARN = 0.70  # 平均相关性 > 0.7 触发预警 (P0-7)

# ── v5.10 P0-8: 历史极端压力测试情景 ──
HISTORICAL_STRESS_SCENARIOS = {
    "2015股灾 (沪深300 -45%)": {
        "csi300": -0.45,
        "csi500": -0.50,
        "csi1000": -0.50,
        "sse50": -0.40,
        "gold": 0.02,
        "sector": "全面崩盘, 流动性枯竭, 千股停牌",
    },
    "2016熔断 (沪深300 -25%)": {
        "csi300": -0.25,
        "csi500": -0.30,
        "csi1000": -0.28,
        "sse50": -0.22,
        "gold": 0.01,
        "sector": "指数熔断, 恐慌抛售, 两日触发2次熔断",
    },
    "2018贸易战 (沪深300 -32%)": {
        "csi300": -0.32,
        "csi500": -0.35,
        "csi1000": -0.38,
        "sse50": -0.28,
        "gold": 0.04,
        "sector": "中美贸易摩擦升级, 科技股重挫, 人民币贬值",
    },
    "2020疫情闪崩 (沪深300 -16%)": {
        "csi300": -0.16,
        "csi500": -0.15,
        "csi1000": -0.14,
        "sse50": -0.14,
        "gold": 0.06,
        "sector": "新冠疫情全球爆发, 节后首日3000股跌停",
    },
    "2024国庆后暴跌 (沪深300 -20%)": {
        "csi300": -0.20,
        "csi500": -0.22,
        "csi1000": -0.25,
        "sse50": -0.18,
        "gold": 0.01,
        "sector": "政策宽松预期逆转, 前期过热回调",
    },
    "极端尾部事件 (1% VaR, -40%)": {
        "csi300": -0.40,
        "csi500": -0.45,
        "csi1000": -0.50,
        "sse50": -0.35,
        "gold": 0.08,
        "sector": "复合危机: 流动性枯竭+信用违约+汇率贬值叠加",
    },
}

# ── 兜底价格表 — 用于无实时行情时 ──
FALLBACK_PRICE_MAP = {
    "300308": 105.0, "300308.SZ": 105.0,
    "688041": 62.0, "688041.SH": 62.0,
    "002371": 320.0, "002371.SZ": 320.0,
    "688981": 55.0, "688981.SH": 55.0,
    "300750": 230.0, "300750.SZ": 230.0,
    "000425": 8.5, "000425.SZ": 8.5,
    "601088": 38.0, "601088.SH": 38.0,
    "600219": 5.0, "600219.SH": 5.0,
    "600019": 7.5, "600019.SH": 7.5,
    "518880": 5.2, "518880.SH": 5.2,
    "000792": 28.0, "000792.SZ": 28.0,
    "600900": 22.0, "600900.SH": 22.0,
    "600276": 48.0, "600276.SH": 48.0,
    "603259": 65.0, "603259.SH": 65.0,
    "002422": 32.0, "002422.SZ": 32.0,
    "511010": 108.5, "511010.SH": 108.5,
}


def estimate_default_price(code: str) -> float:
    """兜底价格 — 用于无实时行情时 (B3.3 从 HedgeEngine._estimate_default_price 抽取)"""
    return FALLBACK_PRICE_MAP.get(code, 50.0)


def compute_weighted_beta(weights: Dict[str, float], index: str) -> float:
    """计算加权 Beta (B3.3 从 HedgeEngine._compute_weighted_beta 抽取)

    Args:
        weights: {code: weight}
        index: CSI300/CSI500/CSI1000/SSE50
    """
    idx_map = {"CSI300": 0, "CSI500": 1, "CSI1000": 2, "SSE50": 3}
    idx = idx_map.get(index, 0)

    total_beta = 0.0
    total_w = 0.0
    for code, w in weights.items():
        pure_code = code.split(".")[0] if "." in code else code
        if pure_code in DEFAULT_BETAS:
            total_beta += w * DEFAULT_BETAS[pure_code][idx]
        else:
            total_beta += w * 1.0
        total_w += w

    return total_beta / total_w if total_w > 0 else 0


def compute_portfolio_vol_cov(
    weights: Dict[str, float],
    historical_returns: Dict[str, List[float]],
    codes: List[str],
) -> float:
    """v5.10 协方差矩阵组合波动率 (P0-5修复核心)

    σ_p = sqrt(w^T * Σ * w)
    """
    available_codes = [c for c in codes if c in historical_returns]
    if not available_codes:
        return 0.015

    code_returns = {}
    min_len = float("inf")
    for code in available_codes:
        rets = historical_returns[code]
        if len(rets) < 30:
            continue
        code_returns[code] = rets[-252:]
        min_len = min(min_len, len(rets[-252:]))

    if len(code_returns) < 2:
        if code_returns:
            code = next(iter(code_returns.keys()))
            w = weights.get(code, 0)
            rets = code_returns[code][-min_len:]
            avg_ret = sum(rets) / len(rets)
            vol = math.sqrt(sum((r - avg_ret) ** 2 for r in rets) / (len(rets) - 1))
            return vol * w
        return 0.015

    n = len(available_codes)
    w = [weights.get(c, 0.0) for c in available_codes]

    cov = [[0.0] * n for _ in range(n)]
    for i, ci in enumerate(available_codes):
        rets_i = code_returns[ci][-min_len:]
        mean_i = sum(rets_i) / len(rets_i)
        for j, cj in enumerate(available_codes):
            rets_j = code_returns[cj][-min_len:]
            mean_j = sum(rets_j) / len(rets_j)
            cov_ij = sum((rets_i[k] - mean_i) * (rets_j[k] - mean_j) for k in range(min_len)) / (min_len - 1)
            cov[i][j] = cov_ij

    port_var = 0.0
    for i in range(n):
        for j in range(n):
            port_var += w[i] * cov[i][j] * w[j]

    return math.sqrt(max(port_var, 0))


def compute_expected_shortfall(
    weights: Dict[str, float],
    historical_returns: Dict[str, List[float]],
    codes: List[str],
    total_value: float,
    confidence: float = 0.95,
) -> float:
    """v5.10 历史模拟法计算 Expected Shortfall (P0-5修复)

    ES = 超过VaR的尾部损失平均值
    """
    available_codes = [c for c in codes if c in historical_returns]
    if not available_codes:
        return total_value * 0.015 * 1.645 * 2.0  # 保守回退

    min_len = min(len(historical_returns[c][-252:]) for c in available_codes)

    port_daily_returns = []
    for t in range(min_len):
        port_r = 0.0
        for c in available_codes:
            w = weights.get(c, 0.0)
            rets = historical_returns[c][-min_len:]
            port_r += w * rets[t]
        port_daily_returns.append(port_r)

    sorted_returns = sorted(port_daily_returns)
    cutoff_idx = int(min_len * (1 - confidence))
    if cutoff_idx >= min_len:
        cutoff_idx = min_len - 1

    tail = sorted_returns[: cutoff_idx + 1]
    if not tail:
        return total_value * abs(sorted_returns[0]) * 2.0

    es_return = abs(sum(tail) / len(tail))
    return es_return * total_value


def compute_mrc(
    weights: Dict[str, float],
    historical_returns: Dict[str, List[float]],
    codes: List[str],
    portfolio_vol: float,
) -> Dict[str, float]:
    """v5.10 P0-6: 计算每个标的的边际风险贡献

    MRC_i = w_i * (Σw)_i / σ_p
    """
    available_codes = [c for c in codes if c in historical_returns]
    if len(available_codes) < 2 or portfolio_vol <= 0:
        return {}

    n = len(available_codes)
    min_len = min(len(historical_returns[c][-252:]) for c in available_codes)

    cov = [[0.0] * n for _ in range(n)]
    for i in range(n):
        rets_i = historical_returns[available_codes[i]][-min_len:]
        mean_i = sum(rets_i) / len(rets_i)
        var_i = sum((r - mean_i) ** 2 for r in rets_i) / (len(rets_i) - 1)
        cov[i][i] = var_i
        for j in range(i + 1, n):
            rets_j = historical_returns[available_codes[j]][-min_len:]
            mean_j = sum(rets_j) / len(rets_j)
            cov_ij = sum((rets_i[k] - mean_i) * (rets_j[k] - mean_j) for k in range(min_len)) / (min_len - 1)
            cov[i][j] = cov_ij
            cov[j][i] = cov_ij

    sigma_w = [0.0] * n
    for i in range(n):
        wi = weights.get(available_codes[i], 0.0)
        for j in range(n):
            wj = weights.get(available_codes[j], 0.0)
            sigma_w[i] += cov[i][j] * wj

    mrc = {}
    for i in range(n):
        wi = weights.get(available_codes[i], 0.0)
        mrc_i = wi * sigma_w[i] / portfolio_vol if portfolio_vol > 0 else 0
        mrc[available_codes[i]] = mrc_i

    return mrc


def compute_correlation_matrix(
    historical_returns: Dict[str, List[float]],
    codes: List[str],
    lookback_days: int = 60,
) -> Dict[str, Dict[str, float]]:
    """v5.10 计算组合内资产相关性矩阵 (P0-7修复)

    返回N×N的相关性矩阵。
    """
    available_codes = [c for c in codes if c in historical_returns]
    if len(available_codes) < 2:
        logger.warning("[相关性] 有效标的不足2个，无法计算相关性矩阵")
        return {}

    code_returns = {}
    min_len = float("inf")
    for code in available_codes:
        rets = historical_returns[code]
        if len(rets) < lookback_days:
            continue
        code_returns[code] = rets[-lookback_days:]
        min_len = min(min_len, len(rets[-lookback_days:]))

    if len(code_returns) < 2:
        logger.warning("[相关性] 足够历史数据的标的不足2个")
        return {}

    n = len(code_returns)
    code_list = list(code_returns.keys())

    cov_matrix = [[0.0] * n for _ in range(n)]
    vol_list = [0.0] * n

    for i, ci in enumerate(code_list):
        rets_i = code_returns[ci][-min_len:]
        mean_i = sum(rets_i) / len(rets_i)
        var_i = sum((r - mean_i) ** 2 for r in rets_i) / (len(rets_i) - 1)
        vol_list[i] = math.sqrt(var_i) if var_i > 0 else 0.0001

        for j, cj in enumerate(code_list):
            rets_j = code_returns[cj][-min_len:]
            mean_j = sum(rets_j) / len(rets_j)
            cov_ij = sum((rets_i[k] - mean_i) * (rets_j[k] - mean_j) for k in range(min_len)) / (min_len - 1)
            cov_matrix[i][j] = cov_ij

    corr_matrix = {}
    for i, ci in enumerate(code_list):
        corr_matrix[ci] = {}
        for j, cj in enumerate(code_list):
            denom = vol_list[i] * vol_list[j]
            if denom > 0:
                corr_matrix[ci][cj] = round(cov_matrix[i][j] / denom, 4)
            else:
                corr_matrix[ci][cj] = 0.0

    return corr_matrix


def assess_portfolio_risk(
    positions: Dict[str, Dict[str, Any]],
    prices: Dict[str, float],
    historical_returns: Optional[Dict[str, List[float]]] = None,
    cash: float = 0.0,
) -> PortfolioRisk:
    """评估组合风险 v5.10 — 协方差矩阵VaR修复 (P0-5)

    v5.10 改进:
    - 组合VaR = sqrt(w^T * Σ * w) * z * total_value
    - 当 historical_returns 可用时, 从历史数据推算协方差矩阵
    - 无历史数据时回退到独立假设并标注风险低估警告
    """
    risk = PortfolioRisk()

    total_stock = 0.0
    stock_weights = {}
    codes_in_portfolio = []

    for code, pos in positions.items():
        shares = pos.get("shares", 0)
        price = prices.get(code, 0)
        if price <= 0:
            for suffix in [".SH", ".SZ"]:
                price = prices.get(code + suffix, 0)
                if price > 0:
                    break
        if price <= 0:
            price = estimate_default_price(code)
        market_value = shares * price
        total_stock += market_value
        if market_value > 0:
            stock_weights[code] = market_value
            codes_in_portfolio.append(code)

    risk.stock_exposure = total_stock
    risk.cash = cash
    risk.total_value = total_stock + cash

    if total_stock <= 0:
        return risk

    total_weight = sum(stock_weights.values())
    if total_weight > 0:
        for code in stock_weights:
            stock_weights[code] /= total_weight

    risk.beta_csi300 = compute_weighted_beta(stock_weights, "CSI300")
    risk.beta_csi500 = compute_weighted_beta(stock_weights, "CSI500")
    risk.beta_csi1000 = compute_weighted_beta(stock_weights, "CSI1000")
    risk.beta_sse50 = compute_weighted_beta(stock_weights, "SSE50")

    # ── v5.10 VaR协方差矩阵修复 (P0-5) ──
    z_95 = 1.645  # 95%置信度z-score

    if historical_returns and len(codes_in_portfolio) > 0:
        risk.volatility_30d = compute_portfolio_vol_cov(stock_weights, historical_returns, codes_in_portfolio)
        risk.var_95_daily = risk.total_value * risk.volatility_30d * z_95
        risk.cvar_95_daily = compute_expected_shortfall(
            stock_weights, historical_returns, codes_in_portfolio, risk.total_value, 0.95
        )
    else:
        market_vol = 0.20
        risk.volatility_30d = risk.beta_csi300 * market_vol / math.sqrt(12) if risk.beta_csi300 > 0 else 0.02
        risk.var_95_daily = risk.total_value * risk.volatility_30d * z_95
        risk.cvar_95_daily = risk.var_95_daily * 2.0
        logger.warning(
            "[VaR] 无历史收益率数据, 使用独立假设VaR (可能低估真实风险50%+), "
            "建议提供historical_returns参数以获得准确值"
        )

    # ── v5.10 P0-6/P0-7: 集中度+相关性增强监控 ──
    hhi = sum(w * w for w in stock_weights.values() if w > 0)
    risk.concentration_risk = hhi

    # P0-6: 板块集中度 (排除固收/国债ETF)
    sector_values: Dict[str, float] = {}
    stock_only_weight = 0.0
    for code, w in stock_weights.items():
        pure = code.split(".")[0] if "." in code else code
        sector = SECTOR_MAP.get(pure, "其他")
        if sector in FIXED_INCOME_TYPES:
            continue
        sector_values[sector] = sector_values.get(sector, 0) + w
        stock_only_weight += w
    if stock_only_weight > 0:
        for sector in sector_values:
            sector_values[sector] /= stock_only_weight
    risk.sector_weights = sector_values
    risk.max_sector_weight = max(sector_values.values()) if sector_values else 0

    if risk.max_sector_weight > SECTOR_LIMIT:
        top_sector = max(sector_values, key=sector_values.get) if sector_values else ""
        risk.sector_concentration_warning = (
            f"{top_sector}板块权重{risk.max_sector_weight * 100:.0f}% > {SECTOR_LIMIT * 100:.0f}%上限 (纯股票口径)"
        )

    # P0-6: MRC
    if historical_returns:
        mrc_map = compute_mrc(stock_weights, historical_returns, codes_in_portfolio, risk.volatility_30d)
        for code, mrc in mrc_map.items():
            if mrc > MRC_LIMIT:
                risk.mrc_warnings.append(f"{code} MRC={mrc * 100:.1f}% > {MRC_LIMIT * 100:.0f}%上限")
        risk.max_single_mrc = max(mrc_map.values()) if mrc_map else 0

    # P0-7: 平均相关性
    if historical_returns:
        corr_matrix = compute_correlation_matrix(historical_returns, codes_in_portfolio, lookback_days=60)
        if corr_matrix:
            corr_values = []
            for ci, inner in corr_matrix.items():
                for cj, corr in inner.items():
                    if ci < cj:
                        corr_values.append(corr)
            if corr_values:
                risk.avg_pairwise_correlation = sum(corr_values) / len(corr_values)
                if risk.avg_pairwise_correlation > CORRELATION_WARN:
                    risk.correlation_warning = (
                        f"组合平均相关性{risk.avg_pairwise_correlation:.2f} > {CORRELATION_WARN:.2f}, "
                        f"呈现共振风险(20d均值)"
                    )

    return risk


def run_historical_stress_tests(
    positions: Dict[str, Dict[str, Any]],
    prices: Dict[str, float],
) -> Dict[str, Dict[str, Any]]:
    """v5.10 P0-8修复: 6个历史极端情景压力测试

    返回每个情景下的:
    - estimated_loss: 预估组合损失金额
    - drawdown_pct: 预估回撤百分比
    - breaches_limit: 是否突破15%最大回撤目标
    - surviving_value: 压力后组合剩余价值
    - sector_detail: 各板块受损明细
    """
    stock_codes = list(positions.keys())
    total_mv = sum(
        positions[c]["shares"] * prices.get(c, estimate_default_price(c)) for c in stock_codes
    )
    if total_mv <= 0:
        return {}

    results = {}

    for scenario_name, shocks in HISTORICAL_STRESS_SCENARIOS.items():
        estimated_loss = 0.0

        for code in stock_codes:
            pos = positions[code]
            shares = pos["shares"]
            if shares <= 0:
                continue

            current_price = prices.get(code, estimate_default_price(code))
            current_mv = shares * current_price

            pure = code.split(".")[0] if "." in code else code
            betas = DEFAULT_BETAS.get(pure, [1.0, 1.0, 1.0, 1.0])

            weighted_shock = (
                betas[0] * shocks["csi300"]
                + betas[1] * shocks["csi500"]
                + betas[2] * shocks["csi1000"]
                + betas[3] * shocks["sse50"]
            ) / 4.0

            # 黄金ETF特殊处理: 危机中黄金通常上涨
            if code in ("518880", "518880.SH") or "黄金" in code:
                weighted_shock = -shocks.get("gold", 0.02)

            loss = current_mv * weighted_shock
            estimated_loss += loss

        surviving_value = total_mv + estimated_loss
        drawdown_pct = abs(estimated_loss) / total_mv if total_mv > 0 else 0

        results[scenario_name] = {
            "estimated_loss": round(abs(estimated_loss), 0),
            "loss_pct": round(drawdown_pct * 100, 1),
            "drawdown_pct": round(drawdown_pct * 100, 1),
            "breaches_limit": drawdown_pct > 0.15,
            "surviving_value": round(surviving_value, 0),
            "total_value_before": round(total_mv, 0),
            "sector_impact": shocks["sector"],
        }

    return results


def monitor_daily_correlation(
    positions: Dict[str, Dict[str, Any]],
    historical_returns: Dict[str, List[float]],
    alert_threshold: float = 0.7,
    lookback_days: int = 60,
) -> Dict[str, Any]:
    """v5.10 每日相关性监控 (P0-7修复核心)

    当滚动60日平均相关系数 > 0.7 时触发预警。
    """
    codes = list(positions.keys())
    corr_matrix = compute_correlation_matrix(historical_returns, codes, lookback_days)

    if not corr_matrix:
        return {
            "status": "NO_DATA",
            "correlation_matrix": {},
            "average_correlation": 0.0,
            "max_correlation": 0.0,
            "high_correlation_pairs": [],
            "alert": False,
            "alert_reason": "",
            "risk_score": 0.0,
        }

    avg_corr = 0.0
    max_corr = 0.0
    high_corr_pairs = []
    count = 0

    code_list = list(corr_matrix.keys())
    n = len(code_list)

    for i in range(n):
        for j in range(i + 1, n):
            ci = code_list[i]
            cj = code_list[j]
            corr = corr_matrix[ci][cj]
            avg_corr += corr
            count += 1
            if corr > max_corr:
                max_corr = corr
            if corr > alert_threshold:
                high_corr_pairs.append((ci, cj, round(corr, 4)))

    avg_corr = avg_corr / count if count > 0 else 0.0

    alert = avg_corr > alert_threshold or max_corr > 0.85
    alert_reason = ""

    if avg_corr > alert_threshold:
        alert_reason = f"组合平均相关系数={avg_corr:.4f}超过阈值{alert_threshold}"
        logger.warning(f"[相关性预警] {alert_reason}")
    if max_corr > 0.85:
        if alert_reason:
            alert_reason += "; "
        alert_reason += f"最高相关系数={max_corr:.4f}>0.85，存在共振风险"
        logger.warning(f"[相关性预警] {alert_reason}")

    # B3.3 修复: risk_score 加下界保护, 避免负相关时产生负值 (风险评分应在 [0, 1] 区间)
    risk_score = max(0.0, min(1.0, avg_corr * 1.2 + (max_corr - 0.5) * 0.8))

    return {
        "status": "OK",
        "correlation_matrix": corr_matrix,
        "average_correlation": round(avg_corr, 4),
        "max_correlation": round(max_corr, 4),
        "high_correlation_pairs": high_corr_pairs,
        "alert": alert,
        "alert_reason": alert_reason,
        "risk_score": round(risk_score, 4),
        "lookback_days": lookback_days,
        "timestamp": datetime.now().isoformat(),
    }


def check_sector_concentration(
    positions: Dict[str, Dict[str, Any]],
    historical_returns: Dict[str, List[float]],
    lookback_days: int = 60,
) -> Dict[str, Any]:
    """v5.10 板块集中度风险检查 (P0-6/P0-7联动)

    检查同一板块内标的的相关性是否过高，识别"伪分散化"风险。
    """
    sectors = {}
    for code, pos in positions.items():
        sector = pos.get("category", "unknown")
        if sector not in sectors:
            sectors[sector] = []
        sectors[sector].append(code)

    sector_risks = {}
    overall_risk = 0.0
    alert_sectors = []

    for sector, codes in sectors.items():
        if len(codes) < 2:
            sector_risks[sector] = {"risk": 0.0, "reason": "标的不足"}
            continue

        corr_result = monitor_daily_correlation(
            {c: positions[c] for c in codes},
            historical_returns,
            lookback_days=lookback_days,
        )

        sector_risks[sector] = {
            "codes": codes,
            "count": len(codes),
            "average_correlation": corr_result["average_correlation"],
            "risk": corr_result["risk_score"],
            "alert": corr_result["alert"],
        }

        if corr_result["alert"]:
            alert_sectors.append(sector)
            overall_risk += corr_result["risk_score"] * (len(codes) / len(positions))

    return {
        "sector_concentration": sector_risks,
        "alert_sectors": alert_sectors,
        "overall_concentration_risk": round(overall_risk, 4),
        "timestamp": datetime.now().isoformat(),
    }


__all__ = [
    "PortfolioRisk",
    "DEFAULT_BETAS",
    "SECTOR_MAP",
    "FIXED_INCOME_TYPES",
    "SECTOR_LIMIT",
    "MRC_LIMIT",
    "CORRELATION_WARN",
    "HISTORICAL_STRESS_SCENARIOS",
    "FALLBACK_PRICE_MAP",
    "estimate_default_price",
    "compute_weighted_beta",
    "compute_portfolio_vol_cov",
    "compute_expected_shortfall",
    "compute_mrc",
    "compute_correlation_matrix",
    "assess_portfolio_risk",
    "run_historical_stress_tests",
    "monitor_daily_correlation",
    "check_sector_concentration",
]
