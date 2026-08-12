"""T5.2 因子归因 — Barra 风格因子 + 行业因子 PnL 拆分.

对冲基金 L7 归因层核心模块, 实现 Barra 风险因子收益归因:
  - 风格因子归因 (10 个 Barra 因子: Size/Beta/Momentum/...)
  - 行业因子归因 (8 大行业: tech/finance/consumer/...)
  - 个股特异性收益 (Specific Return)
  - 信息比率分解 (IR = factor_ir + specific_ir)
  - 风险预算审计

数学公式 (Barra 主动收益分解):
    单因子贡献: contribution_i = active_exposure_i × factor_return_i × portfolio_value
    因子收益总和: factor_pnl = Σ contribution_i
    主动收益: active_return = factor_pnl + specific_pnl
    残差: residual = active_return - factor_pnl - specific_pnl (理论上为 0)
    主动风险: active_risk = √(factor_risk² + specific_risk²)
    信息比率: IR = active_return / active_risk

其中:
    active_exposure_i: 主动因子暴露 = portfolio_exposure - benchmark_exposure
    factor_return_i: 因子收益率 (外部提供, 如 Barra 模型输出)
    portfolio_value: 组合总价值
    factor_risk: 因子风险贡献 = √(w_a' Σ_f w_a) × 年化
    specific_risk: 个股特异性风险 = √(Σ active_weight_i² × specific_risk_i²) × 年化

设计原则:
  1. Facade 模式: 不修改 barra_risk_decomposer.py / pnl_attribution_engine.py
  2. 双层 API: 纯算法函数 + Manager 类 (Feature Flag + ConfigManager)
  3. HC-1 Feature Flag 透传: USE_FACTOR_ATTRIBUTION 默认 False, 关闭时返回全零降级结果
  4. HC-5 ConfigManager 4 级优先级: v8.3_institutional/config/factor_attribution.yaml
  5. 因子分类: 10 个 Barra 风格因子 + 8 大行业因子 (对齐 brinson_attribution)
  6. 可选 IC 指标嵌入: 支持注入 FactorICMetrics 作为因子有效性元数据

用法:
    from utils.attribution.factor_attribution import (
        FactorAttributionManager,
        attribute_factors,
    )

    # 方式 1: 使用快捷函数 (风格因子归因)
    result = attribute_factors(
        portfolio_exposures={"Size": 0.5, "Beta": 1.1, "Momentum": 0.3},
        benchmark_exposures={"Size": 0.0, "Beta": 1.0, "Momentum": 0.0},
        factor_returns={"Size": 0.001, "Beta": 0.002, "Momentum": 0.005},
        portfolio_value=1_000_000,
    )

    # 方式 2: 使用主类 (支持 ConfigManager + Feature Flag)
    mgr = FactorAttributionManager()
    result = mgr.attribute(
        portfolio_exposures={"Size": 0.5, "Beta": 1.1, "Momentum": 0.3},
        benchmark_exposures={"Size": 0.0, "Beta": 1.0, "Momentum": 0.0},
        factor_returns={"Size": 0.001, "Beta": 0.002, "Momentum": 0.005},
        portfolio_value=1_000_000,
        attribution_date="2026-07-27",
    )

硬约束:
  - HC-1: flag=False 时返回 FactorAttributionResult(status="feature_flag_disabled") 全零结果
  - HC-5: 配置走 ConfigManager 4 级优先级
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from utils.config_manager import get_config

logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================

# Feature Flag 名称 (HC-1)
FLAG_NAME = "USE_FACTOR_ATTRIBUTION"

# ConfigManager 配置名 (HC-5)
DEFAULT_CONFIG_NAME = "factor_attribution"

# 默认基准标的 (沪深300ETF)
DEFAULT_PRIMARY_BENCHMARK = "510300.SH"
DEFAULT_SECONDARY_BENCHMARK = "510500.SH"

# 年化因子 (252 交易日)
DEFAULT_ANNUALIZATION_FACTOR = 252

# 数值精度
DEFAULT_DECIMAL_PRECISION = 6
DEFAULT_RETURN_PRECISION = 4

# 风险预算 (5% 主动风险上限)
DEFAULT_RISK_BUDGET = 0.05

# 最小因子数
DEFAULT_MIN_FACTORS = 2

# 显著性阈值 (|贡献/组合价值| > 此值视为显著)
DEFAULT_SIGNIFICANCE_THRESHOLD = 0.005

# 零值阈值
ZERO_EXPOSURE_EPSILON = 0.0001
ZERO_RETURN_EPSILON = 0.000001

# 异常因子收益率阈值
ABNORMAL_FACTOR_RETURN_THRESHOLD = 0.10

# 暴露集中度阈值
CONCENTRATION_THRESHOLD = 0.8
MISSING_THRESHOLD = -0.3

# Barra 风格因子 (10 个, 对齐 utils/barra_risk_decomposer.py BARRA_STYLE_FACTORS)
BARRA_STYLE_FACTORS: list[str] = [
    "Size",
    "Beta",
    "Momentum",
    "ResidualVolatility",
    "NonLinearSize",
    "BookToPrice",
    "Liquidity",
    "EarningsYield",
    "Growth",
    "Leverage",
]

# 行业因子 (8 大行业, 对齐 brinson_attribution.py DEFAULT_SECTORS)
SECTOR_FACTORS: list[str] = [
    "tech",
    "manufacturing",
    "cyclical",
    "resources",
    "defensive",
    "finance",
    "consumer",
    "healthcare",
]

# 因子中文名称映射
FACTOR_NAMES: dict[str, str] = {
    # Barra 风格因子
    "Size": "市值",
    "Beta": "市场敏感度",
    "Momentum": "动量",
    "ResidualVolatility": "残差波动率",
    "NonLinearSize": "非线性市值",
    "BookToPrice": "账面市值比",
    "Liquidity": "流动性",
    "EarningsYield": "盈利收益率",
    "Growth": "增长",
    "Leverage": "杠杆",
    # 行业因子
    "tech": "科技",
    "manufacturing": "制造",
    "cyclical": "周期",
    "resources": "资源",
    "defensive": "防御",
    "finance": "金融",
    "consumer": "消费",
    "healthcare": "医药",
}

# 因子类别
CATEGORY_STYLE = "style"
CATEGORY_SECTOR = "sector"
CATEGORY_COUNTRY = "country"
CATEGORY_SPECIFIC = "specific"

# 状态码
STATUS_OK = "ok"
STATUS_FEATURE_FLAG_DISABLED = "feature_flag_disabled"
STATUS_INSUFFICIENT_DATA = "insufficient_data"
STATUS_FACTOR_MISMATCH = "factor_mismatch"
STATUS_EMPTY_INPUT = "empty_input"


# ============================================================
# 异常体系
# ============================================================


class FactorAttributionError(Exception):
    """因子归因基础异常."""


class InsufficientFactorDataError(FactorAttributionError):
    """因子数据不足, 无法执行归因."""


class FactorMismatchError(FactorAttributionError):
    """因子不匹配 (组合与基准的因子集合不一致)."""


class ExposureNotNormalizedError(FactorAttributionError):
    """因子暴露未标准化 (超出合理范围)."""


class InvalidFactorInputError(FactorAttributionError):
    """输入参数无效 (空字典/非数值/缺失关键字段)."""


# ============================================================
# 数据类
# ============================================================


@dataclass
class FactorAttribution:
    """单因子归因结果.

    Attributes:
        factor_name: 因子名称 (如 Size / Beta / finance)
        factor_category: 因子类别 (style / sector / country / specific)
        factor_display_name: 因子中文名称 (如 市值 / 市场)
        portfolio_exposure: 组合因子暴露
        benchmark_exposure: 基准因子暴露
        active_exposure: 主动因子暴露 = portfolio - benchmark
        factor_return: 因子收益率
        contribution_to_pnl: 对总 PnL 的贡献 (绝对值)
        contribution_pct: 贡献占比 (占总因子 PnL 的百分比)
        contribution_to_active_risk: 对主动风险的贡献
        is_significant: 是否显著 (|贡献/组合价值| > 显著性阈值)
        is_concentrated: 是否过度集中 (|暴露| > 集中度阈值)
        is_missing: 是否暴露缺失 (暴露 < 缺失阈值)
        ic_metrics: 可选, 因子 IC 指标 (来自 FactorICMetrics)
    """

    factor_name: str = ""
    factor_category: str = CATEGORY_STYLE
    factor_display_name: str = ""
    portfolio_exposure: float = 0.0
    benchmark_exposure: float = 0.0
    active_exposure: float = 0.0
    factor_return: float = 0.0
    contribution_to_pnl: float = 0.0
    contribution_pct: float = 0.0
    contribution_to_active_risk: float = 0.0
    is_significant: bool = False
    is_concentrated: bool = False
    is_missing: bool = False
    ic_metrics: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """转为字典 (用于 JSON 序列化)."""
        return {
            "factor_name": self.factor_name,
            "factor_category": self.factor_category,
            "factor_display_name": self.factor_display_name,
            "portfolio_exposure": round(self.portfolio_exposure, DEFAULT_DECIMAL_PRECISION),
            "benchmark_exposure": round(self.benchmark_exposure, DEFAULT_DECIMAL_PRECISION),
            "active_exposure": round(self.active_exposure, DEFAULT_DECIMAL_PRECISION),
            "factor_return": round(self.factor_return, DEFAULT_RETURN_PRECISION),
            "contribution_to_pnl": round(self.contribution_to_pnl, DEFAULT_DECIMAL_PRECISION),
            "contribution_pct": round(self.contribution_pct, 4),
            "contribution_to_active_risk": round(self.contribution_to_active_risk, DEFAULT_DECIMAL_PRECISION),
            "is_significant": self.is_significant,
            "is_concentrated": self.is_concentrated,
            "is_missing": self.is_missing,
            "ic_metrics": self.ic_metrics,
        }


@dataclass
class FactorAttributionResult:
    """因子归因总结果.

    Attributes:
        attribution_date: 归因日期
        portfolio_value: 组合总价值
        total_pnl: 总 PnL (组合收益 × 组合价值)
        total_return_pct: 组合总收益率 (百分比)
        active_return: 主动收益 (组合 - 基准)
        active_return_pct: 主动收益率
        factor_pnl: 因子收益贡献总和
        specific_pnl: 个股特异性收益
        residual: 残差 (active_return - factor_pnl - specific_pnl, 应为 0)
        style_factor_attributions: 风格因子归因明细
        sector_factor_attributions: 行业因子归因明细
        n_factors: 因子总数
        n_significant: 显著因子数
        active_risk: 主动风险 (年化, 跟踪误差)
        factor_risk: 因子风险贡献
        specific_risk: 个股特异性风险
        factor_risk_pct: 因子风险占比
        information_ratio: 信息比率 IR = active_return / active_risk
        factor_ir: 因子部分 IR
        specific_ir: 个股部分 IR
        risk_budget: 风险预算
        risk_budget_used: 已使用风险预算
        risk_budget_remaining: 剩余风险预算
        risk_budget_utilization: 风险预算利用率
        concentrated_factors: 暴露过大的因子列表
        missing_factors: 暴露不足的因子列表
        benchmark_code: 基准标的代码
        status: 状态 (ok / feature_flag_disabled / insufficient_data / ...)
        reason: 状态说明
    """

    attribution_date: str = ""
    portfolio_value: float = 0.0
    total_pnl: float = 0.0
    total_return_pct: float = 0.0
    active_return: float = 0.0
    active_return_pct: float = 0.0
    factor_pnl: float = 0.0
    specific_pnl: float = 0.0
    residual: float = 0.0
    style_factor_attributions: list[FactorAttribution] = field(default_factory=list)
    sector_factor_attributions: list[FactorAttribution] = field(default_factory=list)
    n_factors: int = 0
    n_significant: int = 0
    active_risk: float = 0.0
    factor_risk: float = 0.0
    specific_risk: float = 0.0
    factor_risk_pct: float = 0.0
    information_ratio: float = 0.0
    factor_ir: float = 0.0
    specific_ir: float = 0.0
    risk_budget: float = DEFAULT_RISK_BUDGET
    risk_budget_used: float = 0.0
    risk_budget_remaining: float = 0.0
    risk_budget_utilization: float = 0.0
    concentrated_factors: list[str] = field(default_factory=list)
    missing_factors: list[str] = field(default_factory=list)
    benchmark_code: str = ""
    status: str = STATUS_OK
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转为字典 (用于 JSON 序列化)."""
        return {
            "attribution_date": self.attribution_date,
            "portfolio_value": round(self.portfolio_value, 2),
            "total_pnl": round(self.total_pnl, 2),
            "total_return_pct": round(self.total_return_pct, 4),
            "active_return": round(self.active_return, 2),
            "active_return_pct": round(self.active_return_pct, 4),
            "factor_pnl": round(self.factor_pnl, 2),
            "specific_pnl": round(self.specific_pnl, 2),
            "residual": round(self.residual, DEFAULT_DECIMAL_PRECISION),
            "n_factors": self.n_factors,
            "n_significant": self.n_significant,
            "active_risk": round(self.active_risk, 4),
            "factor_risk": round(self.factor_risk, 4),
            "specific_risk": round(self.specific_risk, 4),
            "factor_risk_pct": round(self.factor_risk_pct, 4),
            "information_ratio": round(self.information_ratio, 4),
            "factor_ir": round(self.factor_ir, 4),
            "specific_ir": round(self.specific_ir, 4),
            "risk_budget": round(self.risk_budget, 4),
            "risk_budget_used": round(self.risk_budget_used, 4),
            "risk_budget_remaining": round(self.risk_budget_remaining, 4),
            "risk_budget_utilization": round(self.risk_budget_utilization, 4),
            "concentrated_factors": self.concentrated_factors,
            "missing_factors": self.missing_factors,
            "benchmark_code": self.benchmark_code,
            "status": self.status,
            "reason": self.reason,
            "style_factor_attributions": [f.to_dict() for f in self.style_factor_attributions],
            "sector_factor_attributions": [f.to_dict() for f in self.sector_factor_attributions],
        }

    def to_markdown(self) -> str:
        """转为 Markdown 报告."""
        lines: list[str] = []
        lines.append(f"# 因子归因报告 — {self.attribution_date or 'N/A'}")
        lines.append("")
        lines.append(f"- 基准标的: `{self.benchmark_code or 'N/A'}`")
        lines.append(f"- 组合价值: **{self.portfolio_value:,.2f}**")
        lines.append(f"- 总 PnL: **{self.total_pnl:,.2f}** ({self.total_return_pct:.4%})")
        lines.append(f"- 主动收益: **{self.active_return:,.2f}** ({self.active_return_pct:.4%})")
        lines.append(f"- 状态: `{self.status}`")
        if self.reason:
            lines.append(f"- 说明: {self.reason}")
        lines.append("")
        lines.append("## 收益分解")
        lines.append("")
        lines.append("| 项目 | 金额 | 占比 |")
        lines.append("|------|------|------|")
        items = [
            ("因子收益", self.factor_pnl),
            ("个股特异性收益", self.specific_pnl),
            ("合计", self.factor_pnl + self.specific_pnl),
        ]
        total = self.factor_pnl + self.specific_pnl
        for name, value in items:
            pct = f"{value / total * 100:.2f}%" if abs(total) > ZERO_RETURN_EPSILON else "N/A"
            lines.append(f"| {name} | {value:,.2f} | {pct} |")
        lines.append(f"| 残差 (应为 0) | {self.residual:.6f} | - |")
        lines.append("")
        lines.append("## 风险分解")
        lines.append("")
        lines.append("| 项目 | 数值 |")
        lines.append("|------|------|")
        lines.append(f"| 主动风险 (年化) | {self.active_risk:.4%} |")
        lines.append(f"| 因子风险 | {self.factor_risk:.4%} |")
        lines.append(f"| 个股特异性风险 | {self.specific_risk:.4%} |")
        lines.append(f"| 因子风险占比 | {self.factor_risk_pct:.2%} |")
        lines.append(f"| 信息比率 IR | {self.information_ratio:.4f} |")
        lines.append(f"| 风险预算利用率 | {self.risk_budget_utilization:.2%} |")
        lines.append("")
        if self.style_factor_attributions:
            lines.append("## 风格因子明细 (Barra 10 因子)")
            lines.append("")
            lines.append("| 因子 | 组合暴露 | 基准暴露 | 主动暴露 | 因子收益 | PnL 贡献 | 占比 | 显著 |")
            lines.append("|------|---------|---------|---------|---------|---------|------|------|")
            for f in self.style_factor_attributions:
                sig = "✓" if f.is_significant else ""
                lines.append(
                    f"| {f.factor_display_name or f.factor_name} | {f.portfolio_exposure:.4f} | "
                    f"{f.benchmark_exposure:.4f} | {f.active_exposure:+.4f} | "
                    f"{f.factor_return:.4f} | {f.contribution_to_pnl:+.2f} | "
                    f"{f.contribution_pct:.2%} | {sig} |"
                )
            lines.append("")
        if self.sector_factor_attributions:
            lines.append("## 行业因子明细")
            lines.append("")
            lines.append("| 行业 | 组合暴露 | 基准暴露 | 主动暴露 | 因子收益 | PnL 贡献 | 占比 |")
            lines.append("|------|---------|---------|---------|---------|---------|------|")
            for f in self.sector_factor_attributions:
                lines.append(
                    f"| {f.factor_display_name or f.factor_name} | {f.portfolio_exposure:.4f} | "
                    f"{f.benchmark_exposure:.4f} | {f.active_exposure:+.4f} | "
                    f"{f.factor_return:.4f} | {f.contribution_to_pnl:+.2f} | "
                    f"{f.contribution_pct:.2%} |"
                )
            lines.append("")
        if self.concentrated_factors:
            lines.append(f"## 暴露集中因子 (|exposure| > {CONCENTRATION_THRESHOLD})")
            lines.append("")
            for f in self.concentrated_factors:
                lines.append(f"- `{f}`")
            lines.append("")
        if self.missing_factors:
            lines.append(f"## 暴露缺失因子 (exposure < {MISSING_THRESHOLD})")
            lines.append("")
            for f in self.missing_factors:
                lines.append(f"- `{f}`")
            lines.append("")
        return "\n".join(lines)


# ============================================================
# 核心算法函数 (独立可测, 不依赖主类)
# ============================================================


def compute_factor_contribution(
    active_exposure: float,
    factor_return: float,
    portfolio_value: float,
) -> float:
    """计算单因子对 PnL 的贡献.

    公式: contribution = active_exposure × factor_return × portfolio_value

    Args:
        active_exposure: 主动因子暴露 = portfolio_exposure - benchmark_exposure
        factor_return: 因子收益率
        portfolio_value: 组合总价值

    Returns:
        因子贡献 (元)
    """
    return active_exposure * factor_return * portfolio_value


def compute_active_exposure(
    portfolio_exposure: float,
    benchmark_exposure: float,
) -> float:
    """计算主动因子暴露 = 组合暴露 - 基准暴露.

    Args:
        portfolio_exposure: 组合因子暴露
        benchmark_exposure: 基准因子暴露

    Returns:
        主动因子暴露
    """
    return portfolio_exposure - benchmark_exposure


def compute_factor_risk(
    active_exposures: dict[str, float],
    factor_cov_matrix: dict[str, dict[str, float]] | None = None,
    annualization_factor: float = DEFAULT_ANNUALIZATION_FACTOR,
) -> float:
    """计算因子风险贡献 (年化).

    公式: factor_risk = √(w_a' Σ_f w_a) × √(annualization_factor)
    其中 w_a 是主动因子暴露向量, Σ_f 是因子协方差矩阵

    简化模式 (cov_matrix=None): 假设因子独立, factor_risk = √(Σ active_exposure_i²) × √(年化因子)

    Args:
        active_exposures: {因子名: 主动暴露}
        factor_cov_matrix: 因子协方差矩阵 {因子A: {因子A: cov, 因子B: cov, ...}}
        annualization_factor: 年化因子 (默认 252)

    Returns:
        年化因子风险 (小数, 如 0.05 = 5%)
    """
    if not active_exposures:
        return 0.0

    factors = list(active_exposures.keys())
    w = [float(active_exposures[f]) for f in factors]

    if factor_cov_matrix is None:
        # 简化模式: 假设因子独立, 协方差矩阵为单位矩阵
        variance = sum(wi * wi for wi in w)
    else:
        # 完整模式: 使用协方差矩阵
        variance = 0.0
        for i, fa in enumerate(factors):
            for j, fb in enumerate(factors):
                cov = float(factor_cov_matrix.get(fa, {}).get(fb, 0.0))
                variance += w[i] * w[j] * cov

    if variance < 0:
        return 0.0

    return math.sqrt(variance) * math.sqrt(annualization_factor)


def compute_specific_risk(
    active_weights: dict[str, float],
    stock_specific_risks: dict[str, float],
    annualization_factor: float = DEFAULT_ANNUALIZATION_FACTOR,
) -> float:
    """计算个股特异性风险 (年化).

    公式: specific_risk = √(Σ active_weight_i² × specific_risk_i²) × √(年化因子)

    Args:
        active_weights: {标的代码: 主动权重}
        stock_specific_risks: {标的代码: 个股特异性风险 (日度)}
        annualization_factor: 年化因子

    Returns:
        年化个股特异性风险
    """
    if not active_weights or not stock_specific_risks:
        return 0.0

    variance = 0.0
    for symbol, weight in active_weights.items():
        sr = float(stock_specific_risks.get(symbol, 0.0))
        variance += (float(weight) ** 2) * (sr**2)

    if variance < 0:
        return 0.0

    return math.sqrt(variance) * math.sqrt(annualization_factor)


def compute_information_ratio(active_return: float, active_risk: float) -> float:
    """计算信息比率 IR = 主动收益 / 主动风险.

    Args:
        active_return: 主动收益 (金额或百分比)
        active_risk: 主动风险 (同单位, 年化)

    Returns:
        信息比率 (主动收益 / 主动风险)
    """
    if abs(active_risk) < ZERO_RETURN_EPSILON:
        return 0.0
    return active_return / active_risk


def align_factors(
    portfolio_exposures: dict[str, float],
    benchmark_exposures: dict[str, float],
    factor_returns: dict[str, float],
) -> list[str]:
    """对齐组合与基准的因子集合 (取并集).

    Args:
        portfolio_exposures: 组合因子暴露
        benchmark_exposures: 基准因子暴露
        factor_returns: 因子收益率

    Returns:
        对齐后的因子列表 (按字母序排序)
    """
    factors = set(portfolio_exposures.keys())
    factors.update(benchmark_exposures.keys())
    factors.update(factor_returns.keys())
    return sorted(factors)


def categorize_factor(factor_name: str) -> str:
    """判断因子类别 (style / sector).

    Args:
        factor_name: 因子名称

    Returns:
        因子类别 (CATEGORY_STYLE / CATEGORY_SECTOR)
    """
    if factor_name in BARRA_STYLE_FACTORS:
        return CATEGORY_STYLE
    if factor_name in SECTOR_FACTORS:
        return CATEGORY_SECTOR
    return CATEGORY_STYLE  # 默认归为风格因子


def attribute_factors(
    portfolio_exposures: dict[str, float],
    benchmark_exposures: dict[str, float],
    factor_returns: dict[str, float],
    portfolio_value: float = 1_000_000.0,
    specific_pnl: float = 0.0,
    active_return: float | None = None,
    attribution_date: str = "",
    benchmark_code: str = "",
    factor_names: dict[str, str] | None = None,
    factor_cov_matrix: dict[str, dict[str, float]] | None = None,
    active_weights: dict[str, float] | None = None,
    stock_specific_risks: dict[str, float] | None = None,
    risk_budget: float = DEFAULT_RISK_BUDGET,
    significance_threshold: float = DEFAULT_SIGNIFICANCE_THRESHOLD,
    annualization_factor: float = DEFAULT_ANNUALIZATION_FACTOR,
    ic_metrics: dict[str, Any] | None = None,
) -> FactorAttributionResult:
    """执行因子归因 (核心算法, 不依赖 Feature Flag).

    本函数为纯算法实现, 不读取配置不检查 Feature Flag, 适合被其他模块复用.
    如需 Feature Flag 透传和 ConfigManager 配置, 请使用 FactorAttributionManager.

    Args:
        portfolio_exposures: {因子: 组合暴露}
        benchmark_exposures: {因子: 基准暴露}
        factor_returns: {因子: 因子收益率}
        portfolio_value: 组合总价值 (元)
        specific_pnl: 个股特异性收益 (元, 外部提供)
        active_return: 主动收益 (元, None 时用 factor_pnl + specific_pnl 估算)
        attribution_date: 归因日期
        benchmark_code: 基准标的代码
        factor_names: 因子名称映射 (None 时用默认 FACTOR_NAMES)
        factor_cov_matrix: 因子协方差矩阵 (None 时假设独立)
        active_weights: 主动权重 (None 时跳过个股风险计算)
        stock_specific_risks: 个股特异性风险 (None 时跳过)
        risk_budget: 风险预算 (年化)
        significance_threshold: 显著性阈值
        annualization_factor: 年化因子
        ic_metrics: IC 指标字典 {因子: {ic_mean, ic_ir, ...}}

    Returns:
        FactorAttributionResult 归因结果

    Raises:
        InvalidFactorInputError: 输入为空或参数无效
    """
    # 输入校验
    if not portfolio_exposures and not benchmark_exposures:
        raise InvalidFactorInputError("组合和基准因子暴露均为空")
    if not factor_returns:
        raise InvalidFactorInputError("因子收益率为空")
    if portfolio_value <= 0:
        raise InvalidFactorInputError(f"组合价值非正: {portfolio_value}")

    names_map = factor_names if factor_names is not None else FACTOR_NAMES

    # 对齐因子集合
    factors = align_factors(portfolio_exposures, benchmark_exposures, factor_returns)

    if len(factors) < DEFAULT_MIN_FACTORS:
        return FactorAttributionResult(
            attribution_date=attribution_date,
            portfolio_value=portfolio_value,
            benchmark_code=benchmark_code,
            status=STATUS_INSUFFICIENT_DATA,
            reason=f"因子数 {len(factors)} < 最小要求 {DEFAULT_MIN_FACTORS}",
        )

    # 逐因子计算贡献
    style_results: list[FactorAttribution] = []
    sector_results: list[FactorAttribution] = []
    sum_factor_pnl = 0.0
    concentrated: list[str] = []
    missing: list[str] = []
    n_significant = 0

    for factor in factors:
        p_exp = float(portfolio_exposures.get(factor, 0.0))
        b_exp = float(benchmark_exposures.get(factor, 0.0))
        f_ret = float(factor_returns.get(factor, 0.0))

        active_exp = compute_active_exposure(p_exp, b_exp)
        contribution = compute_factor_contribution(active_exp, f_ret, portfolio_value)
        sum_factor_pnl += contribution

        is_sig = abs(contribution / portfolio_value) > significance_threshold
        if is_sig:
            n_significant += 1

        is_conc = abs(active_exp) > CONCENTRATION_THRESHOLD
        if is_conc:
            concentrated.append(factor)

        is_miss = active_exp < MISSING_THRESHOLD
        if is_miss:
            missing.append(factor)

        # 异常告警
        if abs(f_ret) > ABNORMAL_FACTOR_RETURN_THRESHOLD:
            logger.warning(
                "[FactorAttribution] 因子 %s 收益率异常: %.4f 超过阈值 %.2f",
                factor,
                f_ret,
                ABNORMAL_FACTOR_RETURN_THRESHOLD,
            )

        category = categorize_factor(factor)
        ic_data = None
        if ic_metrics and factor in ic_metrics:
            ic_data = ic_metrics[factor] if isinstance(ic_metrics[factor], dict) else None

        fa = FactorAttribution(
            factor_name=factor,
            factor_category=category,
            factor_display_name=names_map.get(factor, factor),
            portfolio_exposure=p_exp,
            benchmark_exposure=b_exp,
            active_exposure=active_exp,
            factor_return=f_ret,
            contribution_to_pnl=contribution,
            is_significant=is_sig,
            is_concentrated=is_conc,
            is_missing=is_miss,
            ic_metrics=ic_data,
        )

        if category == CATEGORY_SECTOR:
            sector_results.append(fa)
        else:
            style_results.append(fa)

    # 计算贡献占比
    all_results = style_results + sector_results
    for fa in all_results:
        if abs(sum_factor_pnl) > ZERO_RETURN_EPSILON:
            fa.contribution_pct = fa.contribution_to_pnl / sum_factor_pnl
        else:
            fa.contribution_pct = 0.0

    # 排序: 按贡献绝对值降序
    style_results.sort(key=lambda x: abs(x.contribution_to_pnl), reverse=True)
    sector_results.sort(key=lambda x: abs(x.contribution_to_pnl), reverse=True)

    # 主动收益 (如未提供, 用 factor_pnl + specific_pnl 估算)
    if active_return is None:
        active_return = sum_factor_pnl + specific_pnl

    # 残差 = 主动收益 - 因子收益 - 个股收益 (理论上为 0)
    residual = active_return - sum_factor_pnl - specific_pnl

    # 风险分解
    active_exposures_dict = {f.factor_name: f.active_exposure for f in all_results}
    factor_risk = compute_factor_risk(
        active_exposures_dict,
        factor_cov_matrix,
        annualization_factor,
    )
    specific_risk = 0.0
    if active_weights and stock_specific_risks:
        specific_risk = compute_specific_risk(
            active_weights,
            stock_specific_risks,
            annualization_factor,
        )

    if factor_risk > 0 or specific_risk > 0:
        active_risk = math.sqrt(factor_risk**2 + specific_risk**2)
    else:
        active_risk = 0.0

    factor_risk_pct = factor_risk / active_risk if active_risk > 0 else 0.0

    # 信息比率分解
    ir = compute_information_ratio(active_return, active_risk)
    factor_ir = compute_information_ratio(sum_factor_pnl, factor_risk)
    specific_ir = compute_information_ratio(specific_pnl, specific_risk)

    # 风险预算审计
    risk_budget_used = active_risk
    risk_budget_remaining = max(0.0, risk_budget - risk_budget_used)
    risk_budget_utilization = risk_budget_used / risk_budget if risk_budget > 0 else 0.0

    # 总收益率
    total_return_pct = active_return / portfolio_value if portfolio_value > 0 else 0.0

    return FactorAttributionResult(
        attribution_date=attribution_date,
        portfolio_value=portfolio_value,
        total_pnl=active_return,
        total_return_pct=total_return_pct,
        active_return=active_return,
        active_return_pct=total_return_pct,
        factor_pnl=sum_factor_pnl,
        specific_pnl=specific_pnl,
        residual=residual,
        style_factor_attributions=style_results,
        sector_factor_attributions=sector_results,
        n_factors=len(all_results),
        n_significant=n_significant,
        active_risk=active_risk,
        factor_risk=factor_risk,
        specific_risk=specific_risk,
        factor_risk_pct=factor_risk_pct,
        information_ratio=ir,
        factor_ir=factor_ir,
        specific_ir=specific_ir,
        risk_budget=risk_budget,
        risk_budget_used=risk_budget_used,
        risk_budget_remaining=risk_budget_remaining,
        risk_budget_utilization=risk_budget_utilization,
        concentrated_factors=concentrated,
        missing_factors=missing,
        benchmark_code=benchmark_code,
        status=STATUS_OK,
        reason="",
    )


# ============================================================
# FactorAttributionManager 主类
# ============================================================


class FactorAttributionManager:
    """因子归因管理器 (HC-1 Feature Flag + HC-5 ConfigManager).

    Feature Flag:
        USE_FACTOR_ATTRIBUTION=False (默认): 返回全零降级结果
        USE_FACTOR_ATTRIBUTION=True: 执行完整因子归因

    Usage:
        >>> mgr = FactorAttributionManager()
        >>> result = mgr.attribute(
        ...     portfolio_exposures={"Size": 0.5, "Beta": 1.1},
        ...     benchmark_exposures={"Size": 0.0, "Beta": 1.0},
        ...     factor_returns={"Size": 0.001, "Beta": 0.002},
        ...     portfolio_value=1_000_000,
        ...     attribution_date="2026-07-27",
        ... )
    """

    def __init__(
        self,
        config_name: str = DEFAULT_CONFIG_NAME,
        feature_flag_name: str = FLAG_NAME,
        config: dict[str, Any] | None = None,
    ) -> None:
        """初始化.

        Args:
            config_name: ConfigManager 配置名
            feature_flag_name: Feature Flag 名称
            config: 显式配置 (优先于 ConfigManager)
        """
        self._feature_flag_name = feature_flag_name
        self._config = config or self._load_config(config_name)
        self._settings = self._config.get("settings", {}) or {}
        self._factors_cfg = self._config.get("style_factors", []) or []
        self._sectors_cfg = self._config.get("sector_factors", []) or {}
        self._thresholds = self._config.get("thresholds", {}) or {}
        self._report_cfg = self._config.get("report", {}) or {}

        # 从配置加载参数
        self._benchmark_code = str(self._settings.get("primary_benchmark", DEFAULT_PRIMARY_BENCHMARK))
        self._risk_budget = float(self._settings.get("risk_budget", DEFAULT_RISK_BUDGET))
        self._min_factors = int(self._settings.get("min_factors", DEFAULT_MIN_FACTORS))
        self._significance_threshold = float(
            self._settings.get("significance_threshold", DEFAULT_SIGNIFICANCE_THRESHOLD)
        )
        self._annualization_factor = float(self._settings.get("annualization_factor", DEFAULT_ANNUALIZATION_FACTOR))
        self._zero_exposure_epsilon = float(self._thresholds.get("zero_exposure_epsilon", ZERO_EXPOSURE_EPSILON))
        self._concentration_threshold = float(self._thresholds.get("concentration_threshold", CONCENTRATION_THRESHOLD))
        self._missing_threshold = float(self._thresholds.get("missing_threshold", MISSING_THRESHOLD))

        # 因子名称映射 (从配置加载, 不存在则用默认)
        self._factor_names: dict[str, str] = dict(FACTOR_NAMES)
        for f in self._factors_cfg:
            if isinstance(f, dict):
                code = f.get("code", "")
                name = f.get("name", "")
                if code and name:
                    self._factor_names[code] = name
        for f in self._sectors_cfg:
            if isinstance(f, dict):
                code = f.get("code", "")
                name = f.get("name", "")
                if code and name:
                    self._factor_names[code] = name

        # 默认基准因子暴露
        self._default_benchmark_exposures: dict[str, float] = {
            k: float(v) for k, v in (self._config.get("benchmark_factor_exposures", {}) or {}).items()
        }

    def _load_config(self, config_name: str) -> dict[str, Any]:
        """加载配置 (走 ConfigManager 4 级优先级, HC-5)."""
        try:
            cfg = get_config(config_name, default={}) or {}
            if not cfg:
                logger.warning(
                    "[FactorAttribution] 配置未找到: %s, 使用默认配置",
                    config_name,
                )
            return cfg
        except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
            logger.warning(
                "[FactorAttribution] 配置加载失败: %s (%s), 使用默认配置",
                config_name,
                e,
            )
            return {}

    def _is_enabled(self) -> bool:
        """检查 Feature Flag 是否启用 (HC-1)."""
        try:
            from utils.infra.feature_flags import is_enabled

            return bool(is_enabled(self._feature_flag_name))
        except (ImportError, AttributeError):
            return False

    # ============================================================
    # 公开 API
    # ============================================================

    def attribute(
        self,
        portfolio_exposures: dict[str, float],
        benchmark_exposures: dict[str, float] | None = None,
        factor_returns: dict[str, float] | None = None,
        portfolio_value: float = 1_000_000.0,
        specific_pnl: float = 0.0,
        active_return: float | None = None,
        attribution_date: str = "",
        benchmark_code: str | None = None,
        factor_cov_matrix: dict[str, dict[str, float]] | None = None,
        active_weights: dict[str, float] | None = None,
        stock_specific_risks: dict[str, float] | None = None,
        ic_metrics: dict[str, Any] | None = None,
    ) -> FactorAttributionResult:
        """执行因子归因.

        Feature Flag 透传 (HC-1):
            - USE_FACTOR_ATTRIBUTION=False: 返回全零降级结果
            - USE_FACTOR_ATTRIBUTION=True: 执行完整归因

        Args:
            portfolio_exposures: {因子: 组合暴露}
            benchmark_exposures: {因子: 基准暴露} (None 时用配置默认值)
            factor_returns: {因子: 因子收益率}
            portfolio_value: 组合总价值
            specific_pnl: 个股特异性收益 (外部提供)
            active_return: 主动收益 (None 时用 factor_pnl + specific_pnl 估算)
            attribution_date: 归因日期
            benchmark_code: 基准标的代码
            factor_cov_matrix: 因子协方差矩阵
            active_weights: 主动权重
            stock_specific_risks: 个股特异性风险
            ic_metrics: IC 指标字典

        Returns:
            FactorAttributionResult 归因结果
        """
        # HC-1: Feature Flag 透传
        if not self._is_enabled():
            return FactorAttributionResult(
                attribution_date=attribution_date,
                portfolio_value=portfolio_value,
                benchmark_code=benchmark_code or self._benchmark_code,
                status=STATUS_FEATURE_FLAG_DISABLED,
                reason=f"Feature Flag {self._feature_flag_name}=False, 返回全零降级结果",
            )

        # 基准暴露默认值
        if benchmark_exposures is None:
            benchmark_exposures = self._default_benchmark_exposures
            if not benchmark_exposures:
                return FactorAttributionResult(
                    attribution_date=attribution_date,
                    portfolio_value=portfolio_value,
                    benchmark_code=benchmark_code or self._benchmark_code,
                    status=STATUS_INSUFFICIENT_DATA,
                    reason="基准暴露未提供且配置中无默认值",
                )

        # 因子收益率必填
        if not factor_returns:
            return FactorAttributionResult(
                attribution_date=attribution_date,
                portfolio_value=portfolio_value,
                benchmark_code=benchmark_code or self._benchmark_code,
                status=STATUS_INSUFFICIENT_DATA,
                reason="因子收益率未提供",
            )

        try:
            return attribute_factors(
                portfolio_exposures=portfolio_exposures,
                benchmark_exposures=benchmark_exposures,
                factor_returns=factor_returns,
                portfolio_value=portfolio_value,
                specific_pnl=specific_pnl,
                active_return=active_return,
                attribution_date=attribution_date,
                benchmark_code=benchmark_code or self._benchmark_code,
                factor_names=self._factor_names,
                factor_cov_matrix=factor_cov_matrix,
                active_weights=active_weights,
                stock_specific_risks=stock_specific_risks,
                risk_budget=self._risk_budget,
                significance_threshold=self._significance_threshold,
                annualization_factor=self._annualization_factor,
                ic_metrics=ic_metrics,
            )
        except InvalidFactorInputError as e:
            logger.warning("[FactorAttribution] 归因失败: %s", e)
            return FactorAttributionResult(
                attribution_date=attribution_date,
                portfolio_value=portfolio_value,
                benchmark_code=benchmark_code or self._benchmark_code,
                status=STATUS_EMPTY_INPUT,
                reason=str(e),
            )

    def attribute_from_positions(
        self,
        portfolio_positions: list[dict[str, Any]],
        benchmark_positions: list[dict[str, Any]],
        factor_returns: dict[str, float],
        portfolio_value: float = 1_000_000.0,
        attribution_date: str = "",
        benchmark_code: str | None = None,
    ) -> FactorAttributionResult:
        """从持仓列表执行归因 (聚合到因子维度).

        Args:
            portfolio_positions: 组合持仓 [{"code", "weight", "factor_exposures": {...}}, ...]
            benchmark_positions: 基准持仓
            factor_returns: 因子收益率
            portfolio_value: 组合总价值
            attribution_date: 归因日期
            benchmark_code: 基准标的代码

        Returns:
            FactorAttributionResult 归因结果
        """
        if not self._is_enabled():
            return FactorAttributionResult(
                attribution_date=attribution_date,
                portfolio_value=portfolio_value,
                benchmark_code=benchmark_code or self._benchmark_code,
                status=STATUS_FEATURE_FLAG_DISABLED,
                reason=f"Feature Flag {self._feature_flag_name}=False",
            )

        if not portfolio_positions or not benchmark_positions:
            return FactorAttributionResult(
                attribution_date=attribution_date,
                portfolio_value=portfolio_value,
                benchmark_code=benchmark_code or self._benchmark_code,
                status=STATUS_INSUFFICIENT_DATA,
                reason="持仓列表为空",
            )

        p_exposures, p_active_weights = self._aggregate_positions_to_factors(portfolio_positions)
        b_exposures, _ = self._aggregate_positions_to_factors(benchmark_positions)

        return self.attribute(
            portfolio_exposures=p_exposures,
            benchmark_exposures=b_exposures,
            factor_returns=factor_returns,
            portfolio_value=portfolio_value,
            active_weights=p_active_weights,
            attribution_date=attribution_date,
            benchmark_code=benchmark_code,
        )

    def _aggregate_positions_to_factors(
        self,
        positions: list[dict[str, Any]],
    ) -> tuple[dict[str, float], dict[str, float]]:
        """将资产级持仓聚合到因子级 (按权重加权平均).

        Args:
            positions: [{"code", "weight", "factor_exposures": {因子: 暴露}}, ...]

        Returns:
            (factor_exposures, active_weights) 因子暴露字典 + 主动权重字典
        """
        factor_exposures: dict[str, float] = {}
        weight_sum: dict[str, float] = {}
        active_weights: dict[str, float] = {}

        for pos in positions:
            code = str(pos.get("code", ""))
            weight = float(pos.get("weight", 0.0))
            exposures = pos.get("factor_exposures", {}) or {}

            active_weights[code] = weight  # 简化: 用绝对权重作为主动权重

            for factor, exp in exposures.items():
                factor_exposures[factor] = factor_exposures.get(factor, 0.0) + weight * float(exp)
                weight_sum[factor] = weight_sum.get(factor, 0.0) + weight

        # 因子暴露 = 加权平均
        for factor in list(factor_exposures.keys()):
            if weight_sum.get(factor, 0.0) > self._zero_exposure_epsilon:
                factor_exposures[factor] /= weight_sum[factor]

        return factor_exposures, active_weights

    def get_default_benchmark_exposures(self) -> dict[str, float]:
        """获取默认基准因子暴露."""
        return dict(self._default_benchmark_exposures)

    def get_factor_names(self) -> dict[str, str]:
        """获取因子名称映射."""
        return dict(self._factor_names)


# ============================================================
# Feature Flag 透传 (HC-1)
# ============================================================


def is_factor_attribution_enabled() -> bool:
    """检查 USE_FACTOR_ATTRIBUTION Feature Flag 是否启用.

    HC-1 硬约束: Feature Flag 透传, 默认 False.

    Returns:
        True 启用, False 关闭 (默认)
    """
    try:
        from utils.infra.feature_flags import is_enabled

        return bool(is_enabled(FLAG_NAME))
    except ImportError:
        return False
    except (AttributeError, TypeError, ValueError, OSError):
        return False


# ============================================================
# 便捷函数
# ============================================================


def attribute_factors_simple(
    portfolio_exposures: dict[str, float],
    benchmark_exposures: dict[str, float],
    factor_returns: dict[str, float],
    portfolio_value: float = 1_000_000.0,
    attribution_date: str = "",
    benchmark_code: str = DEFAULT_PRIMARY_BENCHMARK,
) -> FactorAttributionResult:
    """因子归因便捷函数 (不走 Feature Flag, 直接执行算法).

    适合脚本/Jupyter/研究场景.

    Args:
        portfolio_exposures: 组合因子暴露
        benchmark_exposures: 基准因子暴露
        factor_returns: 因子收益率
        portfolio_value: 组合总价值
        attribution_date: 归因日期
        benchmark_code: 基准标的代码

    Returns:
        FactorAttributionResult 归因结果
    """
    return attribute_factors(
        portfolio_exposures=portfolio_exposures,
        benchmark_exposures=benchmark_exposures,
        factor_returns=factor_returns,
        portfolio_value=portfolio_value,
        attribution_date=attribution_date,
        benchmark_code=benchmark_code,
    )


def create_default_manager() -> FactorAttributionManager:
    """创建默认配置的 FactorAttributionManager.

    Returns:
        FactorAttributionManager 实例
    """
    return FactorAttributionManager()


# ============================================================
# 模块导出
# ============================================================

__all__ = [
    "ABNORMAL_FACTOR_RETURN_THRESHOLD",
    "BARRA_STYLE_FACTORS",
    "CATEGORY_COUNTRY",
    "CATEGORY_SECTOR",
    "CATEGORY_SPECIFIC",
    # 因子类别
    "CATEGORY_STYLE",
    "CONCENTRATION_THRESHOLD",
    "DEFAULT_ANNUALIZATION_FACTOR",
    "DEFAULT_CONFIG_NAME",
    "DEFAULT_MIN_FACTORS",
    "DEFAULT_PRIMARY_BENCHMARK",
    "DEFAULT_RISK_BUDGET",
    "DEFAULT_SECONDARY_BENCHMARK",
    "DEFAULT_SIGNIFICANCE_THRESHOLD",
    "FACTOR_NAMES",
    # 常量
    "FLAG_NAME",
    "MISSING_THRESHOLD",
    "SECTOR_FACTORS",
    "STATUS_EMPTY_INPUT",
    "STATUS_FACTOR_MISMATCH",
    "STATUS_FEATURE_FLAG_DISABLED",
    "STATUS_INSUFFICIENT_DATA",
    # 状态码
    "STATUS_OK",
    "ZERO_EXPOSURE_EPSILON",
    "ZERO_RETURN_EPSILON",
    "ExposureNotNormalizedError",
    # 数据类
    "FactorAttribution",
    # 异常
    "FactorAttributionError",
    # 主类
    "FactorAttributionManager",
    "FactorAttributionResult",
    "FactorMismatchError",
    "InsufficientFactorDataError",
    "InvalidFactorInputError",
    "align_factors",
    "attribute_factors",
    "attribute_factors_simple",
    "categorize_factor",
    "compute_active_exposure",
    # 核心算法函数
    "compute_factor_contribution",
    "compute_factor_risk",
    "compute_information_ratio",
    "compute_specific_risk",
    "create_default_manager",
    # 便捷函数
    "is_factor_attribution_enabled",
]