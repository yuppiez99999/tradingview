"""Alpha 因子库聚合入口 — 国泰海通因子体系对标

11 大类因子统一调度 (共 98 个) + 第 12 大类图因子:
1. Momentum 动量 (price_volume.py)       — 10
2. LowVolatility 低波 (price_volume.py)  — 8
3. Size 规模 (price_volume.py)           — 7
4. Liquidity 流动性 (price_volume.py)    — 8
5. Value 估值 (fundamental.py)           — 11
6. Growth 成长 (fundamental.py)          — 15
7. Quality 盈利质量 (fundamental.py)     — 13
8. Leverage 杠杆偿债 (fundamental.py)    — 6
9. Operation 营运效率 (fundamental.py)   — 5
10. Technical 量价技术 (technical.py)    — 9 (GTJA191精选低相关, 可配全量)
11. Expectation 预期微观 (expectation.py) — 6
12. LeadLag 图产业链 (graph.py, 需提供 SupplyChainGraph) — 5 (可选, 对动量因子正交化)

用法:
    lib = AlphaFactorLibrary()
    result = lib.compute_all(
        price_data={"600519": {"closes": [...], "volumes": [...]}},
        fundamentals={"600519": {"pe": 25.0, "roe": 0.20, ...}},
    )

参考:
- 国泰君安《多因子选股模型之因子分析与筛选》
- 国泰海通《量化2025年度复盘系列》
- GTJA191 (2017) 国泰君安191因子
"""

from __future__ import annotations

import pandas as pd

from utils.alpha_factor.base import (
    FactorValue,
    FactorLibraryResult,
    winsorize,
    standardize,
    neutralize_by_industry,
    neutralize_by_size,
    evaluate_factors,
    compute_factor_corr_matrix,
    residualize,
)
from utils.alpha_factor.price_volume import (
    compute_momentum_factors,
    compute_volatility_factors,
    compute_size_factors,
    compute_liquidity_factors,
)
from utils.alpha_factor.fundamental import (
    compute_value_factors,
    compute_growth_factors,
    compute_quality_factors,
    compute_leverage_factors,
    compute_operation_factors,
)
from utils.alpha_factor.technical import compute_technical_factors
from utils.alpha_factor.expectation import compute_expectation_factors
from utils.alpha_factor.graph import (
    compute_lead_lag_factors,
    orthogonalize_chain_factors,
)


class AlphaFactorLibrary:
    """Alpha 因子库 (国泰海通因子体系对标)

    11 大类 100+ 因子, 覆盖国泰君安经典三分类 + 风格时钟 + GTJA191 量价。

    Args:
        neutralize_industry: 是否对所有因子做行业中性化
        neutralize_size: 是否对所有因子做规模中性化
        enable_technical: 是否启用 GTJA191 量价技术类
        enable_expectation: 是否启用预期微观类
        technical_all: 是否计算 GTJA191 全量189 (默认精选30)
    """

    def __init__(
        self,
        neutralize_industry: bool = False,
        neutralize_size: bool = False,
        enable_technical: bool = True,
        enable_expectation: bool = True,
        technical_all: bool = False,
        enable_graph: bool = True,
    ):
        self.neutralize_industry = bool(neutralize_industry)
        self.neutralize_size = bool(neutralize_size)
        self.enable_technical = bool(enable_technical)
        self.enable_expectation = bool(enable_expectation)
        self.technical_all = bool(technical_all)
        self.enable_graph = bool(enable_graph)

    # ------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------

    def compute_all(
        self,
        price_data: dict[str, dict[str, list[float]]],
        fundamentals: dict[str, dict[str, float]] | None = None,
        industries: dict[str, str] | None = None,
        benchmark_returns: list[float] | None = None,
        fundamentals_prev: dict[str, dict[str, float]] | None = None,
        technical_selected_ids: list[str] | None = None,
        graph=None,
        factor_history: dict[str, list[dict[str, float]]] | None = None,
        forward_returns_history: list[dict[str, float]] | None = None,
    ) -> FactorLibraryResult:
        """计算所有因子

        Args:
            price_data: {symbol: {"closes": [...], "volumes": [...], "highs": [...], "lows": [...]}}
            fundamentals: {symbol: {"pe": ..., "pb": ..., "roe": ..., "revenue_yoy": ..., ...}}
            industries: {symbol: industry_name}
            benchmark_returns: 基准收益率序列 (计算 Beta)
            fundamentals_prev: 上期基本面 (计算成长类同比, 可选)
            technical_selected_ids: 指定 GTJA191 因子 ID (可选)
            graph: SupplyChainGraph 实例 (启用第 12 大类 Lead-Lag 图因子, 可选)
            factor_history: U1 衔接 — 日频因子值历史 {factor_name: [{symbol: value}, ...]} (可选)
                提供时 evaluate_factors 走时序 IC/ICIR 模式, 未提供降级为单点 IC
            forward_returns_history: U1 衔接 — 日频 forward returns [{symbol: ret}, ...] (可选)

        Returns:
            FactorLibraryResult
        """
        result = FactorLibraryResult()
        fundamentals = fundamentals or {}
        industries = industries or {}

        # 1-4. 价量因子 (动量/低波/规模/流动性)
        result.factors.update(compute_momentum_factors(price_data, industries))
        result.factors.update(compute_volatility_factors(price_data, benchmark_returns))
        result.factors.update(compute_size_factors(fundamentals))
        result.factors.update(compute_liquidity_factors(price_data))

        # 5-9. 基本面因子 (估值/成长/质量/杠杆/营运)
        result.factors.update(compute_value_factors(fundamentals, industries))
        result.factors.update(compute_growth_factors(fundamentals, fundamentals_prev))
        result.factors.update(compute_quality_factors(fundamentals))
        result.factors.update(compute_leverage_factors(fundamentals))
        result.factors.update(compute_operation_factors(fundamentals))

        # 10. 量价技术因子 (GTJA191)
        if self.enable_technical:
            tech_factors = compute_technical_factors(
                price_data,
                selected_ids=technical_selected_ids,
                all_factors=self.technical_all,
            )
            result.factors.update(tech_factors)

        # 11. 预期与微观结构因子
        if self.enable_expectation:
            result.factors.update(compute_expectation_factors(fundamentals, price_data))

        # 12. 图产业链因子 (Lead-Lag) — 需提供 SupplyChainGraph
        if self.enable_graph and graph is not None:
            chain_factors = compute_lead_lag_factors(price_data, graph, industries)
            # 对已有动量因子正交化, 验证「邻居信息」增量价值 (消除共线)
            if chain_factors:
                chain_factors = orthogonalize_chain_factors(chain_factors, result.factors)
            result.factors.update(chain_factors)

        # 中性化处理
        if self.neutralize_industry and industries:
            for fval in result.factors.values():
                fval.values = neutralize_by_industry(fval.values, industries)

        if self.neutralize_size and fundamentals:
            sizes = {s: float(f.get("market_cap", 0)) for s, f in fundamentals.items()}
            for fval in result.factors.values():
                fval.values = neutralize_by_size(fval.values, sizes)

        # 跨类正交化后处理 (消除残余共线)
        # LIQ_AMIHUD 对 VOL_20D 正交化: Amihud = mean(|ret|/vol) 与波动率强相关,
        # 残差化后保留 "单位成交额价格冲击" 信息, 与波动率水平解耦
        vol_20d = result.factors.get("VOL_20D")
        amihud = result.factors.get("LIQ_AMIHUD")
        if vol_20d and amihud and vol_20d.values:
            amihud.values = residualize(amihud.values, vol_20d.values)

        # 因子有效性评估 (U1 衔接: factor_history 可用时走时序 IC/ICIR 模式)
        evaluate_factors(
            result, price_data,
            factor_history=factor_history,
            forward_returns_history=forward_returns_history,
        )

        # 因子相关性矩阵
        result.factor_corr_matrix = compute_factor_corr_matrix(result.factors)

        return result

    # ------------------------------------------------------------
    # 向后兼容: 预处理方法 (委托给 base 模块函数)
    # ------------------------------------------------------------

    def _winsorize(self, values: dict[str, float], n_sigma: float = 3.0) -> dict[str, float]:
        """去极值 (MAD 法)"""
        return winsorize(values, n_sigma)

    def _standardize(self, values: dict[str, float]) -> dict[str, float]:
        """Z-score 标准化"""
        return standardize(values)

    def _neutralize_by_industry(
        self,
        values: dict[str, float],
        industries: dict[str, str],
    ) -> dict[str, float]:
        """行业中性化"""
        return neutralize_by_industry(values, industries)

    def _neutralize_by_size(
        self,
        values: dict[str, float],
        sizes: dict[str, float],
    ) -> dict[str, float]:
        """规模中性化"""
        return neutralize_by_size(values, sizes)

    def _evaluate_factors(
        self,
        result: FactorLibraryResult,
        price_data: dict[str, dict[str, list[float]]],
        factor_history: dict[str, list[dict[str, float]]] | None = None,
        forward_returns_history: list[dict[str, float]] | None = None,
    ) -> None:
        """评估因子有效性 (向后兼容委托, U1 衔接支持时序模式)"""
        evaluate_factors(
            result, price_data,
            factor_history=factor_history,
            forward_returns_history=forward_returns_history,
        )

    def _compute_factor_corr_matrix(
        self,
        factors: dict[str, FactorValue],
    ) -> pd.DataFrame | None:
        """计算因子间相关性矩阵"""
        return compute_factor_corr_matrix(factors)
