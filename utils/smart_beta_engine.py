# -*- coding: utf-8 -*-
"""
Smart Beta 多因子加权引擎 (Smart Beta Multi-Factor Weighting Engine)

世界顶级量化基金标准 (AQR / Research Affiliates / RAFI):
- 因子加权 (Factor Weighting) — 替代市值加权
- 因子择时 (Factor Timing) — 根据因子动量/估值调整权重
- 多空组合 (Long-Short Portfolio) — 因子套利
- 风险预控 (Risk Preactive) — 波动率/相关性约束

公式核心:
    Composite Score = Σ w_i × z(factor_i)
    Smart Beta Weight = softmax(Composite Score / τ)
    Factor Timing Weight = w_base × (1 + α × factor_momentum)

参考:
- Arnott, R. et al. (2013) "The Surprising Alpha from Fundamental Weighting"
- Asness, A. et al. (2015) "A New Core Equity Paradigm"
- Hsu, J. (2006) "Cap-Weighted Portfolios are Sub-Optimal"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union

import numpy as np


# ============================================================
# 数据结构
# ============================================================

@dataclass
class FactorTimingInfo:
    """因子择时信息"""
    factor_name: str
    base_weight: float               # 基础权重
    current_weight: float            # 调整后权重
    timing_signal: float             # 择时信号 [-1, 1]
    factor_momentum: float = 0.0     # 因子动量
    factor_valuation: float = 0.0    # 因子估值


@dataclass
class SmartBetaResult:
    """Smart Beta 优化结果"""
    # 标的权重
    symbols: List[str]
    smart_beta_weights: np.ndarray    # Smart Beta 权重
    market_cap_weights: np.ndarray    # 市值权重 (对照)
    equal_weights: np.ndarray          # 等权 (对照)

    # 组合指标
    composite_score: np.ndarray       # 综合因子得分
    factor_exposure: Dict[str, float]  # 组合因子暴露

    # 风险指标
    expected_return: float           # 预期收益
    expected_volatility: float       # 预期波动
    sharpe_ratio: float              # 夏普比率
    tracking_error: float            # 跟踪误差
    information_ratio: float         # 信息比率

    # 诊断
    weight_concentration: float       # 权重集中度 (HHI)
    effective_n: float                # 有效持仓数
    turnover_vs_market: float        # 相对市值加权的换手率
    alpha_vs_market: float           # 相对市值加权的预期 Alpha

    # 因子择时信息 (有默认值, 放在最后)
    factor_timing: List[FactorTimingInfo] = field(default_factory=list)


# ============================================================
# Smart Beta 引擎
# ============================================================

class SmartBetaEngine:
    """Smart Beta 多因子加权引擎

    用法:
        engine = SmartBetaEngine()
        result = engine.optimize(
            symbols=["600519", "000858", "601318"],
            factor_scores={
                "600519": {"MOM_60D": 0.5, "VAL_PE": 0.3, "QUA_ROE": 0.4},
                "000858": {"MOM_60D": -0.2, "VAL_PE": 0.6, "QUA_ROE": 0.2},
                "601318": {"MOM_60D": 0.8, "VAL_PE": -0.1, "QUA_ROE": 0.5},
            },
            market_caps={"600519": 2e12, "000858": 5e11, "601318": 1e12},
            factor_weights={"MOM_60D": 0.4, "VAL_PE": 0.3, "QUA_ROE": 0.3},
            cov_matrix=cov_np,
        )
    """

    def __init__(
        self,
        # Smart Beta 加权温度参数
        temperature: float = 0.5,       # softmax 温度, 越小越集中
        # 因子择时参数
        enable_factor_timing: bool = True,
        timing_momentum_window: int = 60,  # 因子动量回看窗口
        timing_alpha: float = 0.3,      # 择时调整幅度 (±30%)
        # 风险约束
        max_weight: float = 0.10,        # 单标的最大权重
        min_weight: float = 0.0,
        max_tracking_error: float = 0.08,  # 跟踪误差上限
    ):
        if temperature <= 0:
            raise ValueError(f"temperature 必须 > 0, 实际 {temperature}")

        self.temperature = float(temperature)
        self.enable_timing = bool(enable_factor_timing)
        self.timing_window = int(timing_momentum_window)
        self.timing_alpha = float(timing_alpha)
        self.max_weight = float(max_weight)
        self.min_weight = float(min_weight)
        self.max_te = float(max_tracking_error)

    # ------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------

    def optimize(
        self,
        symbols: List[str],
        factor_scores: Dict[str, Dict[str, float]],
        market_caps: Optional[Dict[str, float]] = None,
        factor_weights: Optional[Dict[str, float]] = None,
        cov_matrix: Optional[np.ndarray] = None,
        benchmark_weights: Optional[np.ndarray] = None,
        factor_returns_history: Optional[Dict[str, List[float]]] = None,
        risk_free_rate: float = 0.03,
    ) -> SmartBetaResult:
        """Smart Beta 多因子加权优化

        Args:
            symbols: 标的列表
            factor_scores: {symbol: {factor: z_score}}
            market_caps: {symbol: market_cap}, 用于市值加权对照
            factor_weights: {factor: weight}, 因子权重, None=等权
            cov_matrix: 协方差矩阵 (年化)
            benchmark_weights: 基准权重 (计算 TE)
            factor_returns_history: {factor: [历史收益]}, 用于因子择时
            risk_free_rate: 无风险利率

        Returns:
            SmartBetaResult
        """
        n = len(symbols)
        if n == 0:
            raise ValueError("symbols 不能为空")

        # 1. 因子权重 (默认等权)
        if factor_weights is None:
            # 收集所有因子
            all_factors = set()
            for scores in factor_scores.values():
                all_factors.update(scores.keys())
            factor_weights = {f: 1.0 / len(all_factors) for f in all_factors} if all_factors else {}

        # 2. 因子择时调整
        factor_timing_info: List[FactorTimingInfo] = []
        if self.enable_timing and factor_returns_history:
            adjusted_factor_weights = self._apply_factor_timing(
                factor_weights, factor_returns_history, factor_timing_info
            )
        else:
            adjusted_factor_weights = dict(factor_weights)
            for fname, w in factor_weights.items():
                factor_timing_info.append(FactorTimingInfo(
                    factor_name=fname,
                    base_weight=w,
                    current_weight=w,
                    timing_signal=0.0,
                ))

        # 3. 计算综合因子得分
        composite = np.zeros(n)
        factor_exposure: Dict[str, float] = {}
        for i, sym in enumerate(symbols):
            scores = factor_scores.get(sym, {})
            for fname, w in adjusted_factor_weights.items():
                z = float(scores.get(fname, 0.0))
                composite[i] += w * z
                factor_exposure[fname] = factor_exposure.get(fname, 0.0) + w * z / n

        # 4. Smart Beta 权重 (softmax 加权)
        # w_i = exp(score_i / τ) / Σ exp(score_j / τ)
        scaled_scores = composite / self.temperature
        # 防溢出: 减最大值
        scaled_scores -= np.max(scaled_scores)
        exp_scores = np.exp(scaled_scores)
        smart_weights = exp_scores / np.sum(exp_scores)

        # 5. 权重约束
        smart_weights = np.clip(smart_weights, self.min_weight, self.max_weight)
        if smart_weights.sum() > 0:
            smart_weights = smart_weights / smart_weights.sum()

        # 6. 对照权重
        if market_caps:
            mc_arr = np.array([float(market_caps.get(s, 0)) for s in symbols])
            if mc_arr.sum() > 0:
                market_weights = mc_arr / mc_arr.sum()
            else:
                market_weights = np.ones(n) / n
        else:
            market_weights = np.ones(n) / n

        equal_weights = np.ones(n) / n

        # 7. 风险指标
        expected_ret = float(np.mean(composite) * 0.1)  # 简化: 因子得分 × 10% 年化
        if cov_matrix is not None and cov_matrix.shape == (n, n):
            port_var = float(smart_weights @ cov_matrix @ smart_weights)
            expected_vol = float(np.sqrt(port_var))
        else:
            expected_vol = 0.20  # 默认 20%

        sharpe = (expected_ret - risk_free_rate) / expected_vol if expected_vol > 0 else 0.0

        # 跟踪误差 vs 市值加权
        if benchmark_weights is None:
            benchmark_weights = market_weights
        active = smart_weights - benchmark_weights
        if cov_matrix is not None:
            te_var = float(active @ cov_matrix @ active)
            te = float(np.sqrt(te_var))
        else:
            te = float(np.sqrt(np.sum(active ** 2)) * 0.20)  # 近似

        ir = (expected_ret - float(np.mean(composite) * 0.05)) / te if te > 0 else 0.0

        # 8. 诊断指标
        hhi = float(np.sum(smart_weights ** 2))
        effective_n = 1.0 / hhi if hhi > 0 else 0.0
        turnover_vs_mkt = float(np.sum(np.abs(active)))
        alpha_vs_mkt = float(active @ composite * 0.1)

        return SmartBetaResult(
            symbols=list(symbols),
            smart_beta_weights=smart_weights,
            market_cap_weights=market_weights,
            equal_weights=equal_weights,
            factor_timing=factor_timing_info,
            composite_score=composite,
            factor_exposure=factor_exposure,
            expected_return=expected_ret,
            expected_volatility=expected_vol,
            sharpe_ratio=sharpe,
            tracking_error=te,
            information_ratio=ir,
            weight_concentration=hhi,
            effective_n=effective_n,
            turnover_vs_market=turnover_vs_mkt,
            alpha_vs_market=alpha_vs_mkt,
        )

    # ------------------------------------------------------------
    # 因子择时
    # ------------------------------------------------------------

    def _apply_factor_timing(
        self,
        base_weights: Dict[str, float],
        factor_returns_history: Dict[str, List[float]],
        timing_info: List[FactorTimingInfo],
    ) -> Dict[str, float]:
        """应用因子择时调整

        逻辑:
        1. 计算因子动量 (最近 K 日收益)
        2. 因子动量为正 → 增加权重
        3. 因子动量为负 → 减少权重
        4. 单次调整幅度 ≤ α
        """
        adjusted = {}
        for fname, base_w in base_weights.items():
            history = factor_returns_history.get(fname, [])
            if len(history) >= self.timing_window:
                # 因子动量: 最近 K 日累积收益
                recent_returns = history[-self.timing_window:]
                momentum = float(np.sum(recent_returns))
                # 归一化到 [-1, 1]
                momentum_signal = np.tanh(momentum * 10)  # tanh 平滑
            else:
                momentum_signal = 0.0

            # 调整权重: w_adj = w_base × (1 + α × signal)
            adj_factor = 1.0 + self.timing_alpha * momentum_signal
            adj_w = base_w * adj_factor
            adjusted[fname] = adj_w

            timing_info.append(FactorTimingInfo(
                factor_name=fname,
                base_weight=base_w,
                current_weight=adj_w,
                timing_signal=float(momentum_signal),
                factor_momentum=float(np.sum(history[-self.timing_window:])
                                      if len(history) >= self.timing_window else 0.0),
            ))

        # 归一化
        total = sum(adjusted.values())
        if total > 0:
            adjusted = {k: v / total for k, v in adjusted.items()}

        return adjusted

    # ------------------------------------------------------------
    # 多空组合生成
    # ------------------------------------------------------------

    def build_long_short_portfolio(
        self,
        symbols: List[str],
        factor_scores: Dict[str, Dict[str, float]],
        factor_weights: Dict[str, float],
        n_long: int = 5,
        n_short: int = 5,
        market_neutral: bool = True,
    ) -> Dict[str, float]:
        """构建多空组合

        Args:
            symbols: 标的列表
            factor_scores: {symbol: {factor: z_score}}
            factor_weights: 因子权重
            n_long: 做多标的数
            n_short: 做空标的数
            market_neutral: 是否市场中性 (多空市值相等)

        Returns:
            {symbol: weight} (正=做多, 负=做空)
        """
        # 计算综合得分
        scores = []
        for sym in symbols:
            score = 0.0
            fs = factor_scores.get(sym, {})
            for fname, w in factor_weights.items():
                score += w * float(fs.get(fname, 0.0))
            scores.append((sym, score))

        # 排序
        sorted_syms = sorted(scores, key=lambda x: x[1], reverse=True)

        # 选取多空
        longs = sorted_syms[:n_long]
        shorts = sorted_syms[-n_short:] if n_short > 0 else []

        # 构建组合
        portfolio: Dict[str, float] = {}
        if market_neutral:
            # 多空等市值
            long_weight = 1.0 / n_long if n_long > 0 else 0.0
            short_weight = -1.0 / n_short if n_short > 0 else 0.0
        else:
            # 仅做多
            long_weight = 1.0 / n_long if n_long > 0 else 0.0
            short_weight = 0.0

        for sym, _ in longs:
            portfolio[sym] = long_weight
        for sym, _ in shorts:
            portfolio[sym] = short_weight

        return portfolio

    # ------------------------------------------------------------
    # 诊断工具
    # ------------------------------------------------------------

    def diagnose_weights(
        self,
        result: SmartBetaResult,
    ) -> Dict:
        """权重诊断"""
        # 权重分布
        w = result.smart_beta_weights
        return {
            "effective_n": result.effective_n,
            "weight_concentration_hhi": result.weight_concentration,
            "max_weight": float(np.max(w)),
            "min_weight": float(np.min(w)),
            "weight_std": float(np.std(w)),
            # 与市值加权偏离
            "active_weight_max": float(np.max(np.abs(w - result.market_cap_weights))),
            "turnover_vs_market": result.turnover_vs_market,
            "alpha_vs_market": result.alpha_vs_market,
            # 风险
            "tracking_error": result.tracking_error,
            "te_within_limit": result.tracking_error <= self.max_te,
            "information_ratio": result.information_ratio,
            "sharpe_ratio": result.sharpe_ratio,
        }
