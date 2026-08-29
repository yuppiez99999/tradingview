"""Alpha 因子库聚合入口 — 国泰海通因子体系对标

13 大类因子统一调度 (Wave 6.4.5 U1 派生升级 · 新增第 13 大类筹码分布):
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
12. LeadLag 图产业链 (graph.py, 需提供 graph) — 5 (可选, 对动量正交化)
13. ChipDistribution 筹码分布 (chip_distribution.py, 2026-08-12) — 4 (可选, window=150)
14. factor-mining 移植因子 (Wave 6 W6.1.1, FM_ 前缀差异补充) — 6+ (Always ON)
15. EigenAlpha 装饰器注册因子 (Wave 6 W6.1.3) — 动态 (默认 ON)
16. Expression 表达式因子 (expression_engine.py, W6.6.1) — 动态 (用户 DSL 定义, 默认 OFF)
17. Hurst 长记忆因子 (hurst.py, 2026-08-23 P0) — 4 (R/S 分析, 默认 ON)
18. InformationTheory 信息论因子 (information_theory.py, 2026-08-23 P0) — 3 (熵/KL, 默认 ON)

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
- stock(myhhub) CYQ 筹码分布经典算法 (150 档 + 三角分布 + 换手衰减)
"""

from __future__ import annotations

import pandas as pd

from utils.alpha_factor.base import (
    FactorLibraryResult,
    FactorValue,
    compute_factor_corr_matrix,
    compute_registered_factors,  # Wave 6 W6.1.3 装饰器因子计算入口
    evaluate_factors,
    list_registered_factors,  # Wave 6 W6.1.3 装饰器因子清单
    neutralize_by_industry,
    neutralize_by_size,
    residualize,
    standardize,
    winsorize,
)
from utils.alpha_factor.chip_distribution import (
    compute_chip_factors,
)  # 第 13 大类 ChipDistribution (2026-08-12)
from utils.alpha_factor.expectation import compute_expectation_factors
from utils.alpha_factor.expression_engine import (  # 第 16 大类 Expression (W6.6.1)
    compute_expression_factors,
)
from utils.alpha_factor.fundamental import (
    compute_growth_factors,
    compute_leverage_factors,
    compute_operation_factors,
    compute_quality_factors,
    compute_value_factors,
)
from utils.alpha_factor.graph import (
    compute_lead_lag_factors,
    orthogonalize_chain_factors,
)
from utils.alpha_factor.hurst import (
    compute_hurst_factors,
)  # 第 17 大类 Hurst (2026-08-23 P0)
from utils.alpha_factor.information_theory import (  # 第 18 大类 InformationTheory (2026-08-23 P0)
    compute_information_factors,
)
from utils.alpha_factor.price_volume import (
    compute_factor_mining_factors,
    compute_liquidity_factors,
    compute_momentum_factors,
    compute_size_factors,
    compute_volatility_factors,
)
from utils.alpha_factor.technical import compute_technical_factors


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
        enable_decorators: bool = True,  # Wave 6 W6.1.3: 装饰器注册因子总开关
        enable_chip: bool = True,  # 第 13 大类: CYQ 筹码分布因子总开关 (默认开)
        chip_window: int = 150,  # 筹码分布滚动窗口 (季线级, 150 天)
        enable_expression: bool = False,  # 第 16 大类: 表达式因子总开关 (需提供 expressions)
        expressions: (
            list | None
        ) = None,  # 表达式因子规格 [(name, expr_str), ...] 或 [ExpressionFactorSpec]
        enable_hurst: bool = True,  # 第 17 大类: Hurst 指数因子 (2026-08-23 P0, 默认开)
        enable_info: bool = True,  # 第 18 大类: 信息论因子 (2026-08-23 P0, 默认开)
    ):
        self.neutralize_industry = bool(neutralize_industry)
        self.neutralize_size = bool(neutralize_size)
        self.enable_technical = bool(enable_technical)
        self.enable_expectation = bool(enable_expectation)
        self.technical_all = bool(technical_all)
        self.enable_graph = bool(enable_graph)
        self.enable_decorators = bool(enable_decorators)
        self.enable_chip = bool(enable_chip)
        self.chip_window = int(chip_window)
        self.enable_expression = bool(enable_expression)
        self.expressions = expressions or []
        self.enable_hurst = bool(enable_hurst)
        self.enable_info = bool(enable_info)

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
        free_float_shares: dict[str, float] | None = None,
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
            free_float_shares: 第 13 大类筹码分布可选 — {symbol: 自由流通股本数},
                缺省时用成交量中位数退化换手率, 不影响因子产出数量.

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
                chain_factors = orthogonalize_chain_factors(
                    chain_factors, result.factors
                )
            result.factors.update(chain_factors)

        # 13. 筹码分布因子 (CYQ, 第 13 大类 · 2026-08-12 派生升级 W6.4.5)
        #     借鉴 stock(myhhub) 经典通达信算法: 150 档三角分布 + 换手衰减 + 一字板 Dirac-δ 坍缩
        #     4 因子: CYQ_PROFIT_RATIO / CYQ_CONCENTRATION / CYQ_COST_DEVIATION / CYQ_PEAK_POSITION
        if self.enable_chip:
            chip_factors = compute_chip_factors(
                price_data,
                window=self.chip_window,
                free_float_shares=free_float_shares,
            )
            if chip_factors:
                result.debug_info["chip_window"] = self.chip_window
                result.debug_info["chip_covered_symbols"] = len(
                    next(iter(chip_factors.values())).values
                )
            result.factors.update(chip_factors)

        # 14. factor-mining 移植因子 (Wave 6 W6.1.1, FM_ 前缀 6 个差异因子)
        fm_factors = compute_factor_mining_factors(
            price_data,
            fundamentals,
            benchmark_returns,
        )
        result.factors.update(fm_factors)

        # 15. 装饰器注册因子 (Wave 6 W6.1.3, EigenAlpha 风格)
        if self.enable_decorators:
            # 构造参数注入 context (键名与被装饰因子函数的参数名按字符串匹配)
            context = {
                "price_data": price_data,
                "fundamentals": fundamentals,
                "fundamentals_prev": fundamentals_prev,
                "industries": industries,
                "benchmark_returns": benchmark_returns,
                "graph": graph,
                "factor_history": factor_history,
                "forward_returns_history": forward_returns_history,
            }
            reg_factors = compute_registered_factors(context)
            if reg_factors:
                result.factors.update(reg_factors)
                # 把装饰器注册的因子名清单写到 debug_info, 便于追溯
                reg_list = list_registered_factors()
                if reg_list:
                    result.debug_info["decorator_factors_loaded"] = [
                        r["name"] for r in reg_list
                    ]

        # 16. 表达式因子 (第 16 大类 · Expression, W6.6.1)
        #     用户用 DSL 字符串自定义因子, 引擎解析为 AST 求值
        #     可引用已有因子 (如 MOM_20D / CYQ_PROFIT_RATIO) 和原始字段 (close/volume/pe/...)
        if self.enable_expression and self.expressions:
            expr_factors = compute_expression_factors(
                price_data,
                fundamentals=fundamentals,
                expressions=self.expressions,
                existing_factors=result.factors,
            )
            if expr_factors:
                result.factors.update(expr_factors)
                result.debug_info["expression_factors"] = list(expr_factors.keys())

        # 17. Hurst 指数因子 (第 17 大类 · LongMemory, 2026-08-23 P0 经典理论)
        #     R/S 分析判断序列长记忆性: H>0.5 趋势 / H<0.5 均值回归
        #     4 因子: HURST_60D / HURST_120D / HURST_252D / HURST_TREND_SCORE
        if self.enable_hurst:
            hurst_factors = compute_hurst_factors(price_data)
            if hurst_factors:
                result.factors.update(hurst_factors)

        # 18. 信息论因子 (第 18 大类 · InformationTheory, 2026-08-23 P0 经典理论)
        #     香农熵 / KL 散度: 量化收益率分布复杂度与漂移
        #     3 因子: INFO_ENTROPY_60D / INFO_ENTROPY_120D / INFO_DRIFT_60D
        if self.enable_info:
            info_factors = compute_information_factors(price_data)
            if info_factors:
                result.factors.update(info_factors)

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
            result,
            price_data,
            factor_history=factor_history,
            forward_returns_history=forward_returns_history,
        )

        # 因子相关性矩阵
        result.factor_corr_matrix = compute_factor_corr_matrix(result.factors)

        return result

    # ------------------------------------------------------------
    # 向后兼容: 预处理方法 (委托给 base 模块函数)
    # ------------------------------------------------------------

    def _winsorize(
        self, values: dict[str, float], n_sigma: float = 3.0
    ) -> dict[str, float]:
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
            result,
            price_data,
            factor_history=factor_history,
            forward_returns_history=forward_returns_history,
        )

    def _compute_factor_corr_matrix(
        self,
        factors: dict[str, FactorValue],
    ) -> pd.DataFrame | None:
        """计算因子间相关性矩阵"""
        return compute_factor_corr_matrix(factors)
