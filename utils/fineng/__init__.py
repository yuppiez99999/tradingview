"""
fineng — 金融工程内核 (Financial Engineering Kernel)

统一期权定价、希腊字母计算、波动率建模、蒙特卡洛模拟等金融工程基础能力。

包结构:
    fineng.pricing       — 定价内核: Black-Scholes / 二叉树 / 蒙特卡洛 / 隐含波动率
    fineng.greeks        — 希腊字母: 单期权 Greeks / 组合 Greeks 聚合
    fineng.models        — 模型: 波动率曲面 / 期限结构
    fineng.instruments   — 工具定义: 期权合约规格
    fineng               — 高级模块: GARCH 波动率预测 / Kalman 时变Beta / EVT 尾部风险 / 路径模拟

设计原则:
    1. 零模型风险 — 每个函数有明确的数学定义, 不依赖隐式假设
    2. 纯函数 — 输入确定则输出确定, 无副作用, 不依赖外部状态
    3. 零依赖 — 核心定价仅依赖 math 标准库 (无 numpy/scipy 依赖)
    4. 类型安全 — 全部带类型标注, 通过 mypy strict 检查
    5. 单一真相源 — 所有 BS 公式在本包只定义一次

使用:
    from utils.fineng import bs_price, bs_delta, bs_gamma, bs_all_greeks
    from utils.fineng.pricing import BinomialTree, MonteCarloEngine
    from utils.fineng.greeks import PortfolioGreeksAggregator
"""

from utils.fineng.instruments.option_spec import (
    OptionSpec,
    OptionType,
    OptionSide,
    ExerciseStyle,
)

from utils.fineng.pricing.black_scholes import (
    # 核心辅助
    norm_cdf,
    norm_pdf,
    bs_d1_d2,
    # 定价
    bs_call_price,
    bs_put_price,
    bs_price,
    # Greeks
    bs_delta,
    bs_gamma,
    bs_theta,
    bs_vega,
    bs_rho,
    # 批量 Greeks
    bs_all_greeks,
    GreeksResult,
)

from utils.fineng.pricing.implied_vol import (
    implied_vol,
    implied_vol_bisection,
    ImpliedVolResult,
)

from utils.fineng.pricing.binomial import (
    BinomialTree,
    binomial_price,
)

from utils.fineng.pricing.monte_carlo import (
    MonteCarloEngine,
    MCPricingResult,
)

from utils.fineng.greeks.aggregator import (
    PortfolioGreeksAggregator,
    PortfolioGreeks,
)

# ---- Phase 4 新增: 高级金融工程 ----
from utils.fineng.vol_forecast import (
    fit_garch,
    forecast_vol,
    ewma_vol,
    generate_comparison,
    GARCHResult,
    VolComparisonReport,
)

from utils.fineng.kalman_beta import (
    fit_kalman_beta,
    rolling_ols_beta,
    backtest_hedge_comparison,
    KalmanBetaResult,
    BetaHedgeComparison,
)

from utils.fineng.tail_risk_evt import (
    fit_evt,
    evt_var_es,
    EVTResult,
)

from utils.fineng.path_simulator import (
    PathSimulator,
    simulate,
    generate_stress_report,
    PathSimResult,
    StressTestReport,
)

# ---- Phase 4 T4.7: 影子验证器 ----
from utils.fineng.fineng_shadow_verifier import (
    FinengShadowVerifier,
    FinengVerificationReport,
    ModuleFullResult,
    ModuleWindowResult,
    run_fineng_shadow_verification,
)

__all__ = [
    # ---- instruments ----
    "OptionSpec",
    "OptionType",
    "OptionSide",
    "ExerciseStyle",
    # ---- pricing: Black-Scholes ----
    "norm_cdf",
    "norm_pdf",
    "bs_d1_d2",
    "bs_call_price",
    "bs_put_price",
    "bs_price",
    "bs_delta",
    "bs_gamma",
    "bs_theta",
    "bs_vega",
    "bs_rho",
    "bs_all_greeks",
    "GreeksResult",
    # ---- pricing: Implied Vol ----
    "implied_vol",
    "implied_vol_bisection",
    "ImpliedVolResult",
    # ---- pricing: Binomial ----
    "BinomialTree",
    "binomial_price",
    # ---- pricing: Monte Carlo ----
    "MonteCarloEngine",
    "MCPricingResult",
    # ---- greeks ----
    "PortfolioGreeksAggregator",
    "PortfolioGreeks",
    # ---- Phase 4: 高级金融工程 ----
    "fit_garch",
    "forecast_vol",
    "ewma_vol",
    "generate_comparison",
    "GARCHResult",
    "VolComparisonReport",
    "fit_kalman_beta",
    "rolling_ols_beta",
    "backtest_hedge_comparison",
    "KalmanBetaResult",
    "BetaHedgeComparison",
    "fit_evt",
    "evt_var_es",
    "EVTResult",
    "PathSimulator",
    "simulate",
    "generate_stress_report",
    "PathSimResult",
    "StressTestReport",
    # ---- Phase 4 T4.7: 影子验证器 ----
    "FinengShadowVerifier",
    "FinengVerificationReport",
    "ModuleFullResult",
    "ModuleWindowResult",
    "run_fineng_shadow_verification",
]
