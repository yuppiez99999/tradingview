"""alpha_factor_library.py — 向后兼容 shim

因子库已迁移至 utils/alpha_factor/ 包, 对标国泰海通因子体系 (11 大类 100+ 因子):
- Value 估值 / Growth 成长 / Quality 质量 / Leverage 杠杆 / Operation 营运 (fundamental.py)
- Momentum 动量 / LowVolatility 低波 / Size 规模 / Liquidity 流动性 (price_volume.py)
- Technical 量价技术 (technical.py, 集成 GTJA191)
- Expectation 预期微观 (expectation.py, SUE/分析师预期/资金面)

本文件保留以维持向后兼容:
    from utils.alpha_factor_library import AlphaFactorLibrary  # 仍可用

新代码推荐:
    from utils.alpha_factor import AlphaFactorLibrary

参考:
- 国泰君安《多因子选股模型之因子分析与筛选》(估值7+成长15+质量10)
- 国泰海通《量化2025年度复盘系列》(PB_INT/SUE/尾盘成交/大单净买入)
- GTJA191 (2017) 国泰君安191因子
"""

from __future__ import annotations

import logging

from utils.alpha_factor.base import (
    FactorLibraryResult,
    FactorValue,
    calc_ic,
    compute_factor_corr_matrix,
    evaluate_factors,
    neutralize_by_industry,
    neutralize_by_size,
    orthogonalize,
    standardize,
    winsorize,
)
from utils.alpha_factor.library import AlphaFactorLibrary
from utils.alpha_factor.technical import (
    DEFAULT_GTJA,
    DEFAULT_GTJA_30,
    list_available_factors,
)

logger = logging.getLogger(__name__)

__all__ = [
    "AlphaFactorLibrary",
    "FactorValue",
    "FactorLibraryResult",
    "winsorize",
    "standardize",
    "neutralize_by_industry",
    "neutralize_by_size",
    "orthogonalize",
    "calc_ic",
    "evaluate_factors",
    "compute_factor_corr_matrix",
    "DEFAULT_GTJA",
    "DEFAULT_GTJA_30",  # 向后兼容别名
    "list_available_factors",
]


# ============================================================
# 演示入口 (合成数据, 体现 11 大类因子体系)
# ============================================================


def _build_demo_data(n_symbols: int = 8, n_days: int = 300):
    """构造合成演示数据 (价格 + 基本面全字段 + 行业 + 基准)"""
    import numpy as np

    rng = np.random.default_rng(seed=42)
    symbols = [f"DEMO{i:03d}" for i in range(n_symbols)]
    industry_pool = ["Manufacturing", "Finance", "Tech", "Resource"]
    industries = {
        sym: industry_pool[i % len(industry_pool)] for i, sym in enumerate(symbols)
    }

    price_data: dict[str, dict[str, list[float]]] = {}
    for sym in symbols:
        base = float(rng.uniform(20.0, 200.0))
        closes = [base]
        drift = float(rng.normal(0.0004, 0.0003))
        for _ in range(n_days - 1):
            closes.append(closes[-1] * (1 + rng.normal(drift, 0.015)))
        vols = (rng.lognormal(15.0, 0.6, n_days) * 1000.0).tolist()
        highs = [c * (1.0 + abs(rng.normal(0.0, 0.006))) for c in closes]
        lows = [c * (1.0 - abs(rng.normal(0.0, 0.006))) for c in closes]
        opens = [c * (1.0 + rng.normal(0.0, 0.003)) for c in closes]
        price_data[sym] = {
            "closes": closes,
            "volumes": vols,
            "highs": highs,
            "lows": lows,
            "opens": opens,
            # 微观结构 (合成)
            "tail_volume_ratio": float(rng.uniform(0.1, 0.4)),
            "open_big_buy_ratio": float(rng.uniform(-0.1, 0.2)),
        }

    fundamentals: dict[str, dict[str, float]] = {}
    for sym in symbols:
        cap = float(rng.uniform(1.0e10, 2.0e12))
        revenue = cap * float(rng.uniform(0.05, 0.5))
        net_profit = revenue * float(rng.uniform(0.05, 0.3))
        ocf = net_profit * float(rng.uniform(0.8, 1.5))
        fundamentals[sym] = {
            # 估值
            "pe": float(rng.uniform(8.0, 50.0)),
            "pb": float(rng.uniform(0.8, 10.0)),
            "ps": float(rng.uniform(0.5, 15.0)),
            "pcf": float(rng.uniform(3.0, 30.0)),
            "pe_excluding": float(rng.uniform(7.0, 45.0)),
            "dividend_yield": float(rng.uniform(0.0, 0.06)),
            "fcf_yield": float(rng.uniform(0.02, 0.18)),
            "ev_ebitda": float(rng.uniform(5.0, 30.0)),
            "sales_ev": float(rng.uniform(0.5, 5.0)),
            "peg": float(rng.uniform(0.5, 3.0)),
            # 规模
            "market_cap": cap,
            "negotiable_value": cap * 0.85,
            "revenue": revenue,
            "total_assets": cap * float(rng.uniform(0.5, 2.0)),
            # 成长 (预计算字段)
            "revenue_yoy": float(rng.normal(0.15, 0.2)),
            "revenue_qoq": float(rng.normal(0.04, 0.08)),
            "net_profit_yoy": float(rng.normal(0.2, 0.3)),
            "net_profit_qoq": float(rng.normal(0.05, 0.1)),
            "operating_cash_flow": ocf,
            "operating_cash_flow_yoy": float(rng.normal(0.1, 0.25)),
            "operating_cash_flow_qoq": float(rng.normal(0.03, 0.08)),
            "roe_yoy": float(rng.normal(0.05, 0.15)),
            "roe_qoq": float(rng.normal(0.01, 0.05)),
            "roa_yoy": float(rng.normal(0.03, 0.12)),
            "roa_qoq": float(rng.normal(0.01, 0.04)),
            # 质量
            "roe": float(rng.uniform(0.05, 0.30)),
            "roa": float(rng.uniform(0.02, 0.15)),
            "roic": float(rng.uniform(0.04, 0.22)),
            "gross_margin": float(rng.uniform(0.15, 0.70)),
            "net_margin": float(rng.uniform(0.03, 0.30)),
            "ebitda_margin": float(rng.uniform(0.1, 0.4)),
            "cash_to_np": float(rng.uniform(0.5, 1.5)),
            "accruals": float(rng.uniform(-0.06, 0.06)),
            "accrual_chg": float(rng.uniform(-0.03, 0.03)),
            "debt_to_equity": float(rng.uniform(0.2, 1.8)),
            "current_ratio": float(rng.uniform(0.8, 2.8)),
            "quick_ratio": float(rng.uniform(0.5, 2.0)),
            "gross_margin_stab": float(rng.uniform(0.5, 0.95)),
            # 杠杆
            "eq_multiplier": float(rng.uniform(1.5, 5.0)),
            "int_debt_ratio": float(rng.uniform(0.05, 0.4)),
            "debt_ratio": float(rng.uniform(0.2, 0.7)),
            "lt_debt_ratio": float(rng.uniform(0.05, 0.3)),
            "int_coverage": float(rng.uniform(2.0, 20.0)),
            # 营运
            "asset_turnover": float(rng.uniform(0.3, 1.5)),
            "inventory_turnover": float(rng.uniform(2.0, 12.0)),
            "ar_turnover": float(rng.uniform(3.0, 20.0)),
            "ap_turnover": float(rng.uniform(3.0, 15.0)),
            "cash_cycle": float(rng.uniform(20.0, 120.0)),
            # 预期微观
            "sue": float(rng.normal(0, 1)),
            "np_revision": float(rng.normal(0, 0.05)),
            "consensus_2y_growth": float(rng.normal(0.15, 0.1)),
            "rd_ratio": float(rng.uniform(0.01, 0.15)),
        }

    benchmark_returns = rng.normal(0.0003, 0.01, n_days).tolist()
    return price_data, fundamentals, industries, benchmark_returns


def _print_summary(result: FactorLibraryResult) -> None:
    """打印因子库摘要 (按 11 大类分组)"""
    from collections import Counter

    logger.info("=" * 72)
    logger.info(" Alpha 因子库 (国泰海通体系对标) 计算结果摘要")
    logger.info("=" * 72)
    logger.info(f"总因子数: {len(result.factors)}")
    cats = Counter(fv.category for fv in result.factors.values())
    for cat, n in sorted(cats.items()):
        logger.info(f"  - {cat:14s}: {n}")
    logger.info(f"强因子 (|IC_5d| > 0.05): {len(result.strong_factors)}")
    logger.info(f"有效因子 (|IC_5d| > 0.03): {len(result.effective_factors)}")
    # 各类别 IC top 3
    logger.info(f"{'因子名':22s} {'类别':14s} {'IC_5d':>8s} {'覆盖数':>6s}")
    logger.info("-" * 58)
    for cat in sorted(cats):
        items = [fv for fv in result.factors.values() if fv.category == cat]
        items.sort(key=lambda x: abs(x.ic_5d), reverse=True)
        for fv in items[:3]:  # 每类只展示 top 3
            cov = len(fv.values)
            logger.info(f"{fv.name:22s} {fv.category:14s} {fv.ic_5d:>8.4f} {cov:>6d}")
        if len(items) > 3:
            logger.info(f"  ... ({len(items) - 3} more)")
    if result.factor_corr_matrix is not None:
        high_corr_pairs = []
        cm = result.factor_corr_matrix
        names = list(cm.columns)
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                c = float(cm.iloc[i, j])
                if abs(c) > 0.7:
                    high_corr_pairs.append((names[i], names[j], c))
        high_corr_pairs.sort(key=lambda x: abs(x[2]), reverse=True)
        logger.info(f"高相关因子对 (|ρ| > 0.7): {len(high_corr_pairs)}")
        for a, b, c in high_corr_pairs[:8]:
            logger.info(f"  {a:22s} <-> {b:22s}  ρ = {c:+.3f}")


def main() -> None:
    """演示入口: 用合成数据计算 11 大类因子并打印摘要"""
    logger.info("[Demo] 构造合成数据 (8 只标的, 300 个交易日, 4 个行业) ...")
    price_data, fundamentals, industries, benchmark_returns = _build_demo_data()
    lib = AlphaFactorLibrary(
        neutralize_industry=False,
        neutralize_size=False,
        enable_technical=True,
        enable_expectation=True,
    )
    logger.info("[Demo] 计算所有因子 (11 大类, 含 GTJA191 量价) ...")
    result = lib.compute_all(
        price_data,
        fundamentals=fundamentals,
        industries=industries,
        benchmark_returns=benchmark_returns,
    )
    _print_summary(result)
    logger.info("=" * 72)
    logger.info("Demo 完成. 生产环境请通过 AlphaFactorLibrary().compute_all(...) 调用.")
    logger.info("新代码推荐: from utils.alpha_factor import AlphaFactorLibrary")
    logger.info("=" * 72)


if __name__ == "__main__":
    main()
