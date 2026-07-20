# -*- coding: utf-8 -*-
"""
Barra 风险因子暴露分解 (Barra Risk Factor Decomposition)

世界顶级量化基金标准 (AQR / Citadel / Two Sigma):
- 10 个风格因子暴露 (Barra CNS3 模型简化版)
- 行业因子暴露
- 国家因子暴露
- 风险归因: 主动风险 = 因子风险 + 个股风险
- 信息比率分解: IR = Σ(IR_i × 暴露_i)

风格因子:
    1. Size (市值)        — log(市值)
    2. Beta (市场敏感度)  — vs 沪深300
    3. Momentum (动量)    — 12-1月收益
    4. Residual Volatility (残差波动率)
    5. Non-Linear Size (非线性市值)
    6. Book-to-Price (账面市值比)
    7. Liquidity (流动性) — 换手率
    8. Earnings Yield (盈利收益率)
    9. Growth (增长)     — 营收/利润增长
    10. Leverage (杠杆)   — 资产负债率

参考:
- MSCI Barra Risk Model
- Rosenberg, B. (1974) "Extra-Market Components of Covariance in Security Returns"
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

# 10 个 Barra 风格因子
BARRA_STYLE_FACTORS = [
    "Size",                   # 市值
    "Beta",                   # 市场敏感度
    "Momentum",               # 动量
    "ResidualVolatility",     # 残差波动率
    "NonLinearSize",          # 非线性市值
    "BookToPrice",            # 账面市值比
    "Liquidity",              # 流动性
    "EarningsYield",          # 盈利收益率
    "Growth",                 # 增长
    "Leverage",               # 杠杆
]

# 行业分类 (申万一级)
SW_INDUSTRIES = [
    "银行", "非银金融", "食品饮料", "医药生物", "纺织服装",
    "轻工制造", "公用事业", "交通运输", "房地产", "商业贸易",
    "餐饮旅游", "农林牧渔", "采掘", "化工", "钢铁",
    "有色金属", "建筑材料", "建筑装饰", "电气设备", "机械设备",
    "国防军工", "汽车", "家用电器", "电子", "通信",
    "计算机", "传媒", "综合",
]


@dataclass
class FactorExposure:
    """单个因子暴露"""
    factor_name: str
    exposure: float              # 暴露值 (标准化后)
    contribution_to_active_risk: float  # 对主动风险的贡献
    factor_return: float = 0.0  # 因子收益
    contribution_to_active_return: float = 0.0  # 对主动收益的贡献


@dataclass
class BarraDecomposition:
    """Barra 风险分解结果"""
    # 因子暴露
    style_factor_exposures: List[FactorExposure]   # 10 个风格因子
    industry_exposures: Dict[str, float]            # 行业暴露
    country_exposure: float                         # 国家因子暴露

    # 风险分解
    active_risk: float                  # 主动风险 (年化, 跟踪误差)
    factor_risk: float                  # 因子风险贡献
    specific_risk: float                # 个股特异性风险
    factor_risk_pct: float              # 因子风险占比

    # 收益归因
    active_return: float                # 主动收益
    factor_return: float                # 因子收益贡献
    specific_return: float              # 个股特异性收益

    # 信息比率分解
    information_ratio: float            # IR = 主动收益 / 主动风险
    factor_ir: float                    # 因子部分 IR
    specific_ir: float                  # 个股部分 IR

    # 风险预算审计
    risk_budget_used: float             # 已使用风险预算
    risk_budget_remaining: float        # 剩余风险预算
    risk_budget_utilization: float      # 风险预算利用率

    # 持仓列表
    symbols: List[str]
    weights: List[float]
    benchmark_weights: List[float]

    # 诊断
    concentrated_factors: List[str]     # 暴露过大的因子
    missing_factors: List[str]           # 暴露不足的因子


# ============================================================
# Barra 风险分解引擎
# ============================================================

class BarraRiskDecomposer:
    """Barra 风险因子暴露分解引擎

    用法:
        decomposer = BarraRiskDecomposer()
        # 计算每个标的的因子暴露
        factor_data = {
            "600519": {"Size": 1.5, "Beta": 0.8, "Momentum": 0.3, ...},
            "000858": {"Size": 1.2, "Beta": 1.1, "Momentum": -0.2, ...},
        }
        result = decomposer.decompose(
            symbols=["600519", "000858"],
            weights=[0.6, 0.4],
            benchmark_weights=[0.5, 0.5],
            factor_exposures=factor_data,
            factor_returns={"Size": 0.001, "Beta": 0.002, ...},
            factor_cov_matrix=cov_np,  # 10×10
            stock_specific_risks={"600519": 0.02, "000858": 0.025},
            industries={"600519": "食品饮料", "000858": "食品饮料"},
            risk_budget=0.05,  # 5% 主动风险预算
        )
    """

    # 暴露阈值 (标准化后)
    CONCENTRATION_THRESHOLD = 0.8  # |exposure| > 0.8 视为集中
    MISSING_THRESHOLD = -0.3       # exposure < -0.3 视为缺失

    def __init__(self, annualization_factor: float = 252 ** 0.5):
        self.annual_factor = float(annualization_factor)

    # ------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------

    def decompose(
        self,
        symbols: List[str],
        weights: Union[List[float], np.ndarray],
        benchmark_weights: Union[List[float], np.ndarray],
        factor_exposures: Dict[str, Dict[str, float]],
        factor_returns: Optional[Dict[str, float]] = None,
        factor_cov_matrix: Optional[np.ndarray] = None,
        stock_specific_risks: Optional[Dict[str, float]] = None,
        industries: Optional[Dict[str, str]] = None,
        risk_budget: float = 0.05,
    ) -> BarraDecomposition:
        """运行 Barra 风险分解

        Args:
            symbols: 标的列表
            weights: 组合权重
            benchmark_weights: 基准权重
            factor_exposures: {symbol: {factor: exposure}}
            factor_returns: {factor: return}
            factor_cov_matrix: 因子协方差矩阵 (N_factors × N_factors)
            stock_specific_risks: {symbol: specific_risk}
            industries: {symbol: industry_name}
            risk_budget: 风险预算 (年化, 默认 5%)

        Returns:
            BarraDecomposition
        """
        n = len(symbols)
        if n == 0:
            raise ValueError("symbols 不能为空")
        if n != len(weights) or n != len(benchmark_weights):
            raise ValueError("权重维度不匹配")

        w = np.asarray(weights, dtype=float)
        w_bench = np.asarray(benchmark_weights, dtype=float)
        active_weights = w - w_bench  # 主动权重

        # 1. 构建因子暴露矩阵 X (N_stocks × N_factors)
        factors = list(BARRA_STYLE_FACTORS)
        n_factors = len(factors)
        X = np.zeros((n, n_factors))
        for i, sym in enumerate(symbols):
            sym_factors = factor_exposures.get(sym, {})
            for j, f in enumerate(factors):
                X[i, j] = float(sym_factors.get(f, 0.0))

        # 2. 组合因子暴露 = X' w
        portfolio_factor_exposure = X.T @ w
        benchmark_factor_exposure = X.T @ w_bench
        active_factor_exposure = X.T @ active_weights  # 主动暴露

        # 3. 因子风险 (需要因子协方差矩阵)
        if factor_cov_matrix is None:
            # 默认单位矩阵 (假设因子独立, 单位方差)
            factor_cov_matrix = np.eye(n_factors) * 0.01  # 1% 日波动

        factor_cov = np.asarray(factor_cov_matrix, dtype=float)
        if factor_cov.shape != (n_factors, n_factors):
            # 截断或填充
            factor_cov = np.eye(n_factors) * 0.01

        # 因子风险 = (X' w_a)' Σ_f (X' w_a) × 年化
        factor_variance = float(active_factor_exposure @ factor_cov @ active_factor_exposure)
        factor_risk = math.sqrt(max(factor_variance, 0)) * self.annual_factor

        # 4. 个股特异性风险
        specific_var = 0.0
        if stock_specific_risks:
            for i, sym in enumerate(symbols):
                sr = float(stock_specific_risks.get(sym, 0.02))
                specific_var += (active_weights[i] ** 2) * (sr ** 2)
        else:
            # 默认 2% 日波动
            for i in range(n):
                specific_var += (active_weights[i] ** 2) * (0.02 ** 2)
        specific_risk = math.sqrt(max(specific_var, 0)) * self.annual_factor

        # 5. 主动风险 (跟踪误差)
        active_risk = math.sqrt(factor_risk ** 2 + specific_risk ** 2)
        factor_risk_pct = factor_risk ** 2 / active_risk ** 2 if active_risk > 0 else 0.0

        # 6. 因子收益归因
        factor_returns_dict = factor_returns or {f: 0.0 for f in factors}
        # 主动收益 = Σ(active_exposure_i × factor_return_i) + Σ(active_weight_i × specific_return_i)
        factor_return = sum(
            active_factor_exposure[j] * float(factor_returns_dict.get(factors[j], 0.0))
            for j in range(n_factors)
        )
        # 个股特异性收益简化为 0 (实盘接入后由残差填充)
        specific_return = 0.0
        active_return = factor_return + specific_return

        # 7. 信息比率
        information_ratio = active_return / active_risk if active_risk > 0 else 0.0
        factor_ir = factor_return / factor_risk if factor_risk > 0 else 0.0
        specific_ir = specific_return / specific_risk if specific_risk > 0 else 0.0

        # 8. 风险预算审计
        risk_budget_used = active_risk
        risk_budget_remaining = max(risk_budget - active_risk, 0.0)
        risk_budget_utilization = active_risk / risk_budget if risk_budget > 0 else 0.0

        # 9. 行业暴露
        industry_exposures: Dict[str, float] = {}
        if industries:
            for i, sym in enumerate(symbols):
                ind = industries.get(sym, "未知")
                industry_exposures[ind] = industry_exposures.get(ind, 0.0) + float(active_weights[i])

        # 10. 国家因子暴露 (组合 beta vs 基准)
        beta_factor_idx = factors.index("Beta") if "Beta" in factors else 1
        country_exposure = float(active_factor_exposure[beta_factor_idx])

        # 11. 构建因子暴露明细
        style_exposures: List[FactorExposure] = []
        for j, f in enumerate(factors):
            # 对主动风险的贡献: exposure × Σ_f × exposure / active_risk
            marginal_contrib = float(
                (factor_cov[j, :] @ active_factor_exposure) * active_factor_exposure[j]
            )
            contrib_to_risk = math.sqrt(max(marginal_contrib, 0)) * self.annual_factor if marginal_contrib > 0 else 0.0
            f_ret = float(factor_returns_dict.get(f, 0.0))
            contrib_to_return = float(active_factor_exposure[j] * f_ret)

            style_exposures.append(FactorExposure(
                factor_name=f,
                exposure=float(active_factor_exposure[j]),
                contribution_to_active_risk=contrib_to_risk,
                factor_return=f_ret,
                contribution_to_active_return=contrib_to_return,
            ))

        # 12. 集中/缺失因子诊断
        concentrated = [
            fe.factor_name for fe in style_exposures
            if abs(fe.exposure) > self.CONCENTRATION_THRESHOLD
        ]
        missing = [
            fe.factor_name for fe in style_exposures
            if fe.exposure < self.MISSING_THRESHOLD
        ]

        return BarraDecomposition(
            style_factor_exposures=style_exposures,
            industry_exposures=industry_exposures,
            country_exposure=country_exposure,
            active_risk=active_risk,
            factor_risk=factor_risk,
            specific_risk=specific_risk,
            factor_risk_pct=factor_risk_pct,
            active_return=active_return,
            factor_return=factor_return,
            specific_return=specific_return,
            information_ratio=information_ratio,
            factor_ir=factor_ir,
            specific_ir=specific_ir,
            risk_budget_used=risk_budget_used,
            risk_budget_remaining=risk_budget_remaining,
            risk_budget_utilization=risk_budget_utilization,
            symbols=list(symbols),
            weights=w.tolist(),
            benchmark_weights=w_bench.tolist(),
            concentrated_factors=concentrated,
            missing_factors=missing,
        )

    # ------------------------------------------------------------
    # 简化入口: 从持仓自动估算因子暴露
    # ------------------------------------------------------------

    def decompose_from_positions(
        self,
        positions: List[Dict],
        benchmark_weights: Optional[Dict[str, float]] = None,
        risk_budget: float = 0.05,
    ) -> BarraDecomposition:
        """从持仓列表自动估算因子暴露 (简化版)

        Args:
            positions: [{"code": "600519", "amount": 100000, "market_cap": ..., ...}]
            benchmark_weights: {code: weight}, None=等权
            risk_budget: 风险预算
        """
        if not positions:
            raise ValueError("positions 不能为空")

        symbols = [p.get("code", "") for p in positions]
        total_value = sum(float(p.get("amount", 0)) for p in positions)
        if total_value <= 0:
            raise ValueError("持仓总金额必须 > 0")

        weights = [float(p.get("amount", 0)) / total_value for p in positions]
        if benchmark_weights:
            bench = [float(benchmark_weights.get(s, 0)) for s in symbols]
            total_bench = sum(bench)
            if total_bench > 0:
                bench = [b / total_bench for b in bench]
        else:
            bench = [1.0 / len(symbols)] * len(symbols)

        # 简化因子暴露估算
        factor_exposures: Dict[str, Dict[str, float]] = {}
        for p in positions:
            code = p.get("code", "")
            amount = float(p.get("amount", 0))
            market_cap = float(p.get("market_cap", amount))  # 默认用 amount
            pe = float(p.get("pe", 20.0))
            pb = float(p.get("pb", 2.0))
            turnover = float(p.get("turnover", 0.5))
            beta = float(p.get("beta", 1.0))
            momentum = float(p.get("momentum", 0.0))
            vol = float(p.get("volatility", 0.25))
            growth = float(p.get("growth_rate", 0.1))
            leverage = float(p.get("debt_ratio", 0.5))

            # 标准化
            log_size = math.log(max(market_cap, 1)) / math.log(1e8)  # 1亿=0
            ep = 1.0 / pe if pe > 0 else 0
            bp = 1.0 / pb if pb > 0 else 0
            nls = log_size ** 2 - 0.5  # 非线性市值简化

            factor_exposures[code] = {
                "Size": log_size,
                "Beta": (beta - 1.0) * 2,  # 1.0 = 0
                "Momentum": momentum * 5,
                "ResidualVolatility": (vol - 0.25) * 4,
                "NonLinearSize": nls,
                "BookToPrice": bp,
                "Liquidity": (turnover - 0.5) * 2,
                "EarningsYield": ep * 10,
                "Growth": growth * 5,
                "Leverage": (leverage - 0.5) * 2,
            }

        # 简化因子收益 (假设)
        factor_returns = {f: 0.0 for f in BARRA_STYLE_FACTORS}
        # 动量 +0.1%/日, 价值 +0.05%/日
        factor_returns["Momentum"] = 0.001
        factor_returns["BookToPrice"] = 0.0005

        # 简化因子协方差 (对角, 日波动 1%)
        factor_cov = np.eye(len(BARRA_STYLE_FACTORS)) * 0.01 ** 2

        # 个股特异性风险 (年化 30%)
        specific_risks = {s: 0.30 / math.sqrt(252) for s in symbols}

        industries = {p.get("code", ""): p.get("sector", "未知") for p in positions}

        return self.decompose(
            symbols=symbols,
            weights=weights,
            benchmark_weights=bench,
            factor_exposures=factor_exposures,
            factor_returns=factor_returns,
            factor_cov_matrix=factor_cov,
            stock_specific_risks=specific_risks,
            industries=industries,
            risk_budget=risk_budget,
        )

    # ------------------------------------------------------------
    # 保存
    # ------------------------------------------------------------

    def save_result(self, result: BarraDecomposition, path: Union[str, Path]) -> Path:
        """保存分解结果到 JSON"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "style_factor_exposures": [
                {
                    "factor_name": fe.factor_name,
                    "exposure": fe.exposure,
                    "contribution_to_active_risk": fe.contribution_to_active_risk,
                    "factor_return": fe.factor_return,
                    "contribution_to_active_return": fe.contribution_to_active_return,
                } for fe in result.style_factor_exposures
            ],
            "industry_exposures": result.industry_exposures,
            "country_exposure": result.country_exposure,
            "active_risk": result.active_risk,
            "factor_risk": result.factor_risk,
            "specific_risk": result.specific_risk,
            "factor_risk_pct": result.factor_risk_pct,
            "active_return": result.active_return,
            "factor_return": result.factor_return,
            "specific_return": result.specific_return,
            "information_ratio": result.information_ratio,
            "factor_ir": result.factor_ir,
            "specific_ir": result.specific_ir,
            "risk_budget_used": result.risk_budget_used,
            "risk_budget_remaining": result.risk_budget_remaining,
            "risk_budget_utilization": result.risk_budget_utilization,
            "concentrated_factors": result.concentrated_factors,
            "missing_factors": result.missing_factors,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return path
