"""T5.1 Brinson-Fachler 三效应归因 — 配置/选股/交互效应分解.

对冲基金 L7 归因层核心模块, 实现 Brinson-Fachler (1985) 经典归因模型:
  - 配置效应 (Allocation Effect): 衡量行业权重偏离基准带来的超额收益
  - 选股效应 (Selection Effect): 衡量行业内选股能力带来的超额收益
  - 交互效应 (Interaction Effect): 衡量权重偏离与选股能力的协同效应

数学公式 (Brinson-Fachler 1985):
    AR_i = (w_p_i - w_b_i) × (R_b_i - R_b)        # 配置效应
    SR_i = w_b_i × (R_p_i - R_b_i)                # 选股效应
    IR_i = (w_p_i - w_b_i) × (R_p_i - R_b_i)      # 交互效应
    ER   = Σ(AR_i + SR_i + IR_i) = R_p - R_b       # 总超额收益

其中:
    w_p_i: 组合在行业 i 的权重
    w_b_i: 基准在行业 i 的权重
    R_p_i: 组合在行业 i 的收益率
    R_b_i: 基准在行业 i 的收益率
    R_p:   组合总收益率 = Σ w_p_i × R_p_i
    R_b:   基准总收益率 = Σ w_b_i × R_b_i

设计原则:
  1. 经典模型严格实现: 配置/选股/交互三效应数学等价于 R_p - R_b
  2. Facade 模式: 不修改 pnl_attribution_engine.py, 与之并行存在
  3. HC-1 Feature Flag 透传: USE_BRINSON_ATTRIBUTION 默认 False, 关闭时返回全零降级结果
  4. HC-5 ConfigManager 4 级优先级: v8.3_institutional/config/brinson_attribution.yaml
  5. 行业分类对齐: 8 大行业 (tech/manufacturing/cyclical/resources/defensive/finance/consumer/healthcare)
  6. 双层归因: 支持行业级 (sector) 和资产级 (asset) 两种粒度

用法:
    from utils.attribution.brinson_attribution import (
        BrinsonAttributionManager,
        attribute_brinson,
    )

    # 方式 1: 使用快捷函数 (行业级归因)
    result = attribute_brinson(
        portfolio_weights={"finance": 0.4, "tech": 0.6},
        benchmark_weights={"finance": 0.3, "tech": 0.7},
        portfolio_returns={"finance": 0.02, "tech": 0.05},
        benchmark_returns={"finance": 0.01, "tech": 0.03},
    )
    # result.total_allocation_effect / total_selection_effect / total_interaction_effect

    # 方式 2: 使用主类 (支持 ConfigManager + Feature Flag)
    mgr = BrinsonAttributionManager()
    result = mgr.attribute(
        portfolio_weights={"finance": 0.4, "tech": 0.6},
        benchmark_weights={"finance": 0.3, "tech": 0.7},
        portfolio_returns={"finance": 0.02, "tech": 0.05},
        benchmark_returns={"finance": 0.01, "tech": 0.03},
        attribution_date="2026-07-27",
    )

硬约束:
  - HC-1: flag=False 时返回 BrinsonResult(status="feature_flag_disabled") 全零结果
  - HC-5: 配置走 ConfigManager 4 级优先级
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from utils.config_manager import get_config

logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================

# Feature Flag 名称 (HC-1)
FLAG_NAME = "USE_BRINSON_ATTRIBUTION"

# ConfigManager 配置名 (HC-5)
DEFAULT_CONFIG_NAME = "brinson_attribution"

# 默认基准标的 (沪深300ETF, 项目主基准)
DEFAULT_PRIMARY_BENCHMARK = "510300.SH"
DEFAULT_SECONDARY_BENCHMARK = "510500.SH"

# 数值精度
DEFAULT_DECIMAL_PRECISION = 6
DEFAULT_RETURN_PRECISION = 4

# 权重和容差 (允许组合/基准权重和轻微偏离 1.0)
DEFAULT_WEIGHT_SUM_TOLERANCE = 0.01

# 最小行业数 (低于此数视为数据不足)
DEFAULT_MIN_SECTORS = 2

# 零值阈值 (避免浮点误差)
ZERO_WEIGHT_EPSILON = 0.0001
ZERO_RETURN_EPSILON = 0.000001

# 异常收益率阈值 (超过此值告警)
ABNORMAL_RETURN_THRESHOLD = 0.20

# 行业分类 (对齐 utils/pnl_attribution_engine.py 的 SECTORS, 8 大行业)
DEFAULT_SECTORS: list[str] = [
    "tech",
    "manufacturing",
    "cyclical",
    "resources",
    "defensive",
    "finance",
    "consumer",
    "healthcare",
]

# 行业中文名称映射
SECTOR_NAMES: dict[str, str] = {
    "tech": "科技",
    "manufacturing": "制造",
    "cyclical": "周期",
    "resources": "资源",
    "defensive": "防御",
    "finance": "金融",
    "consumer": "消费",
    "healthcare": "医药",
}

# 状态码
STATUS_OK = "ok"
STATUS_FEATURE_FLAG_DISABLED = "feature_flag_disabled"
STATUS_INSUFFICIENT_DATA = "insufficient_data"
STATUS_SECTOR_MISMATCH = "sector_mismatch"
STATUS_EMPTY_INPUT = "empty_input"


# ============================================================
# 异常体系
# ============================================================


class BrinsonAttributionError(Exception):
    """Brinson 归因基础异常."""


class InsufficientDataError(BrinsonAttributionError):
    """数据不足, 无法执行归因 (行业数过少或权重缺失)."""


class SectorMismatchError(BrinsonAttributionError):
    """行业不匹配 (组合与基准的行业集合不一致)."""


class WeightNotNormalizedError(BrinsonAttributionError):
    """权重未归一化 (权重和偏离 1.0 超过容差)."""


class InvalidInputError(BrinsonAttributionError):
    """输入参数无效 (负权重/非数值/空字典等)."""


# ============================================================
# 数据类
# ============================================================


@dataclass
class SectorAttribution:
    """单行业归因结果.

    Attributes:
        sector: 行业代码 (如 finance / tech)
        sector_name: 行业中文名称 (如 金融 / 科技)
        portfolio_weight: 组合权重 w_p_i
        benchmark_weight: 基准权重 w_b_i
        portfolio_return: 组合收益率 R_p_i
        benchmark_return: 基准收益率 R_b_i
        weight_diff: 权重偏离 (w_p_i - w_b_i)
        return_diff: 收益偏离 (R_p_i - R_b_i)
        allocation_effect: 配置效应 AR_i
        selection_effect: 选股效应 SR_i
        interaction_effect: 交互效应 IR_i
        total_effect: 行业总效应 (AR_i + SR_i + IR_i)
        contribution_to_excess: 对总超额收益的贡献 (w_p_i × R_p_i - w_b_i × R_b_i)
    """

    sector: str = ""
    sector_name: str = ""
    portfolio_weight: float = 0.0
    benchmark_weight: float = 0.0
    portfolio_return: float = 0.0
    benchmark_return: float = 0.0
    weight_diff: float = 0.0
    return_diff: float = 0.0
    allocation_effect: float = 0.0
    selection_effect: float = 0.0
    interaction_effect: float = 0.0
    total_effect: float = 0.0
    contribution_to_excess: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """转为字典 (用于 JSON 序列化)."""
        return {
            "sector": self.sector,
            "sector_name": self.sector_name,
            "portfolio_weight": round(self.portfolio_weight, DEFAULT_DECIMAL_PRECISION),
            "benchmark_weight": round(self.benchmark_weight, DEFAULT_DECIMAL_PRECISION),
            "portfolio_return": round(self.portfolio_return, DEFAULT_RETURN_PRECISION),
            "benchmark_return": round(self.benchmark_return, DEFAULT_RETURN_PRECISION),
            "weight_diff": round(self.weight_diff, DEFAULT_DECIMAL_PRECISION),
            "return_diff": round(self.return_diff, DEFAULT_RETURN_PRECISION),
            "allocation_effect": round(
                self.allocation_effect, DEFAULT_DECIMAL_PRECISION
            ),
            "selection_effect": round(self.selection_effect, DEFAULT_DECIMAL_PRECISION),
            "interaction_effect": round(
                self.interaction_effect, DEFAULT_DECIMAL_PRECISION
            ),
            "total_effect": round(self.total_effect, DEFAULT_DECIMAL_PRECISION),
            "contribution_to_excess": round(
                self.contribution_to_excess, DEFAULT_DECIMAL_PRECISION
            ),
        }


@dataclass
class BrinsonResult:
    """Brinson 归因总结果.

    Attributes:
        attribution_date: 归因日期 (YYYY-MM-DD)
        total_return: 组合总收益率 R_p
        benchmark_return: 基准总收益率 R_b
        excess_return: 超额收益 (R_p - R_b)
        total_allocation_effect: 配置效应总和 Σ AR_i
        total_selection_effect: 选股效应总和 Σ SR_i
        total_interaction_effect: 交互效应总和 Σ IR_i
        residual: 残差 (excess_return - 三效应总和, 应为 0, 用于验证)
        sector_attributions: 各行业归因明细列表
        n_sectors: 行业数
        benchmark_code: 基准标的代码
        status: 状态 (ok / feature_flag_disabled / insufficient_data / ...)
        reason: 状态说明 (status != ok 时填充)
    """

    attribution_date: str = ""
    total_return: float = 0.0
    benchmark_return: float = 0.0
    excess_return: float = 0.0
    total_allocation_effect: float = 0.0
    total_selection_effect: float = 0.0
    total_interaction_effect: float = 0.0
    residual: float = 0.0
    sector_attributions: list[SectorAttribution] = field(default_factory=list)
    n_sectors: int = 0
    benchmark_code: str = ""
    status: str = STATUS_OK
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转为字典 (用于 JSON 序列化)."""
        return {
            "attribution_date": self.attribution_date,
            "total_return": round(self.total_return, DEFAULT_RETURN_PRECISION),
            "benchmark_return": round(self.benchmark_return, DEFAULT_RETURN_PRECISION),
            "excess_return": round(self.excess_return, DEFAULT_RETURN_PRECISION),
            "total_allocation_effect": round(
                self.total_allocation_effect, DEFAULT_DECIMAL_PRECISION
            ),
            "total_selection_effect": round(
                self.total_selection_effect, DEFAULT_DECIMAL_PRECISION
            ),
            "total_interaction_effect": round(
                self.total_interaction_effect, DEFAULT_DECIMAL_PRECISION
            ),
            "residual": round(self.residual, DEFAULT_DECIMAL_PRECISION),
            "n_sectors": self.n_sectors,
            "benchmark_code": self.benchmark_code,
            "status": self.status,
            "reason": self.reason,
            "sector_attributions": [s.to_dict() for s in self.sector_attributions],
        }

    def to_markdown(self) -> str:
        """转为 Markdown 表格 (用于报告输出)."""
        lines: list[str] = []
        lines.append(f"# Brinson 归因报告 — {self.attribution_date or 'N/A'}")
        lines.append("")
        lines.append(f"- 基准标的: `{self.benchmark_code or 'N/A'}`")
        lines.append(f"- 组合总收益: **{self.total_return:.4%}**")
        lines.append(f"- 基准总收益: **{self.benchmark_return:.4%}**")
        lines.append(f"- 超额收益: **{self.excess_return:.4%}**")
        lines.append(f"- 状态: `{self.status}`")
        if self.reason:
            lines.append(f"- 说明: {self.reason}")
        lines.append("")
        lines.append("## 三效应汇总")
        lines.append("")
        lines.append("| 效应类型 | 数值 | 占比 |")
        lines.append("|---------|------|------|")
        total_effect = (
            self.total_allocation_effect
            + self.total_selection_effect
            + self.total_interaction_effect
        )
        for name, value in [
            ("配置效应", self.total_allocation_effect),
            ("选股效应", self.total_selection_effect),
            ("交互效应", self.total_interaction_effect),
            ("合计", total_effect),
        ]:
            pct = (
                f"{value / total_effect * 100:.2f}%"
                if abs(total_effect) > ZERO_RETURN_EPSILON
                else "N/A"
            )
            lines.append(f"| {name} | {value:.6f} | {pct} |")
        lines.append(f"| 残差 (应为 0) | {self.residual:.6f} | - |")
        lines.append("")
        if self.sector_attributions:
            lines.append("## 行业明细")
            lines.append("")
            lines.append(
                "| 行业 | 组合权重 | 基准权重 | 权重偏离 | 组合收益 | 基准收益 | 配置效应 | 选股效应 | 交互效应 | 总效应 |"
            )
            lines.append(
                "|------|---------|---------|---------|---------|---------|---------|---------|---------|-------|"
            )
            for s in self.sector_attributions:
                lines.append(
                    f"| {s.sector_name or s.sector} | {s.portfolio_weight:.4f} | {s.benchmark_weight:.4f} | "
                    f"{s.weight_diff:+.4f} | {s.portfolio_return:.4f} | {s.benchmark_return:.4f} | "
                    f"{s.allocation_effect:+.6f} | {s.selection_effect:+.6f} | "
                    f"{s.interaction_effect:+.6f} | {s.total_effect:+.6f} |"
                )
        return "\n".join(lines)


# ============================================================
# 核心算法函数 (独立可测, 不依赖主类)
# ============================================================


def compute_allocation_effect(
    portfolio_weight: float,
    benchmark_weight: float,
    benchmark_sector_return: float,
    benchmark_total_return: float,
) -> float:
    """计算配置效应 AR_i = (w_p_i - w_b_i) × (R_b_i - R_b).

    Args:
        portfolio_weight: 组合在行业 i 的权重 w_p_i
        benchmark_weight: 基准在行业 i 的权重 w_b_i
        benchmark_sector_return: 基准在行业 i 的收益率 R_b_i
        benchmark_total_return: 基准总收益率 R_b

    Returns:
        配置效应 AR_i
    """
    return (portfolio_weight - benchmark_weight) * (
        benchmark_sector_return - benchmark_total_return
    )


def compute_selection_effect(
    benchmark_weight: float,
    portfolio_sector_return: float,
    benchmark_sector_return: float,
) -> float:
    """计算选股效应 SR_i = w_b_i × (R_p_i - R_b_i).

    Args:
        benchmark_weight: 基准在行业 i 的权重 w_b_i
        portfolio_sector_return: 组合在行业 i 的收益率 R_p_i
        benchmark_sector_return: 基准在行业 i 的收益率 R_b_i

    Returns:
        选股效应 SR_i
    """
    return benchmark_weight * (portfolio_sector_return - benchmark_sector_return)


def compute_interaction_effect(
    portfolio_weight: float,
    benchmark_weight: float,
    portfolio_sector_return: float,
    benchmark_sector_return: float,
) -> float:
    """计算交互效应 IR_i = (w_p_i - w_b_i) × (R_p_i - R_b_i).

    Args:
        portfolio_weight: 组合在行业 i 的权重 w_p_i
        benchmark_weight: 基准在行业 i 的权重 w_b_i
        portfolio_sector_return: 组合在行业 i 的收益率 R_p_i
        benchmark_sector_return: 基准在行业 i 的收益率 R_b_i

    Returns:
        交互效应 IR_i
    """
    weight_diff = portfolio_weight - benchmark_weight
    return_diff = portfolio_sector_return - benchmark_sector_return
    return weight_diff * return_diff


def compute_total_return(weights: dict[str, float], returns: dict[str, float]) -> float:
    """计算加权总收益率 R = Σ w_i × R_i.

    Args:
        weights: {sector: weight}
        returns: {sector: return}

    Returns:
        总收益率
    """
    total = 0.0
    for sector, w in weights.items():
        r = returns.get(sector, 0.0)
        total += w * r
    return total


def validate_weights(
    weights: dict[str, float],
    tolerance: float = DEFAULT_WEIGHT_SUM_TOLERANCE,
    epsilon: float = ZERO_WEIGHT_EPSILON,
) -> tuple[bool, float, str]:
    """校验权重合法性 (非负 + 归一化).

    Args:
        weights: 权重字典
        tolerance: 权重和容差
        epsilon: 零值阈值

    Returns:
        (is_valid, weight_sum, error_msg)
    """
    if not weights:
        return False, 0.0, "权重字典为空"

    for sector, w in weights.items():
        if not isinstance(w, (int, float)):
            return False, 0.0, f"行业 {sector} 权重非数值: {type(w).__name__}"
        if w < -epsilon:
            return False, 0.0, f"行业 {sector} 权重为负: {w}"

    weight_sum = sum(float(w) for w in weights.values())
    if abs(weight_sum - 1.0) > tolerance:
        return (
            False,
            weight_sum,
            f"权重和 {weight_sum:.6f} 偏离 1.0 超过容差 {tolerance}",
        )

    return True, weight_sum, ""


def align_sectors(
    portfolio_weights: dict[str, float],
    benchmark_weights: dict[str, float],
    portfolio_returns: dict[str, float],
    benchmark_returns: dict[str, float],
) -> list[str]:
    """对齐组合与基准的行业集合 (取并集, 缺失行业补 0).

    Args:
        portfolio_weights: 组合权重
        benchmark_weights: 基准权重
        portfolio_returns: 组合收益率
        benchmark_returns: 基准收益率

    Returns:
        对齐后的行业列表 (按字母序排序)
    """
    sectors = set(portfolio_weights.keys())
    sectors.update(benchmark_weights.keys())
    sectors.update(portfolio_returns.keys())
    sectors.update(benchmark_returns.keys())
    return sorted(sectors)


def attribute_brinson(
    portfolio_weights: dict[str, float],
    benchmark_weights: dict[str, float],
    portfolio_returns: dict[str, float],
    benchmark_returns: dict[str, float],
    attribution_date: str = "",
    benchmark_code: str = "",
    sector_names: dict[str, str] | None = None,
    validate: bool = True,
    weight_tolerance: float = DEFAULT_WEIGHT_SUM_TOLERANCE,
) -> BrinsonResult:
    """执行 Brinson-Fachler 三效应归因 (核心算法, 不依赖 Feature Flag).

    本函数为纯算法实现, 不读取配置不检查 Feature Flag, 适合被其他模块复用.
    如需 Feature Flag 透传和 ConfigManager 配置, 请使用 BrinsonAttributionManager.

    Args:
        portfolio_weights: {行业: 组合权重}
        benchmark_weights: {行业: 基准权重}
        portfolio_returns: {行业: 组合收益率}
        benchmark_returns: {行业: 基准收益率}
        attribution_date: 归因日期
        benchmark_code: 基准标的代码
        sector_names: 行业代码 -> 中文名称映射 (None 时用默认 SECTOR_NAMES)
        validate: 是否校验权重归一化
        weight_tolerance: 权重和容差

    Returns:
        BrinsonResult 归因结果

    Raises:
        InvalidInputError: 输入为空或参数无效
        WeightNotNormalizedError: 权重未归一化 (validate=True 时)
    """
    # 输入校验
    if not portfolio_weights or not benchmark_weights:
        raise InvalidInputError("组合或基准权重字典为空")
    if not portfolio_returns or not benchmark_returns:
        raise InvalidInputError("组合或基准收益率字典为空")

    # 权重校验 (可选)
    if validate:
        p_valid, _p_sum, p_err = validate_weights(portfolio_weights, weight_tolerance)
        if not p_valid:
            raise WeightNotNormalizedError(f"组合权重校验失败: {p_err}")
        b_valid, _b_sum, b_err = validate_weights(benchmark_weights, weight_tolerance)
        if not b_valid:
            raise WeightNotNormalizedError(f"基准权重校验失败: {b_err}")

    names_map = sector_names if sector_names is not None else SECTOR_NAMES

    # 对齐行业集合
    sectors = align_sectors(
        portfolio_weights,
        benchmark_weights,
        portfolio_returns,
        benchmark_returns,
    )

    if len(sectors) < DEFAULT_MIN_SECTORS:
        return BrinsonResult(
            attribution_date=attribution_date,
            benchmark_code=benchmark_code,
            n_sectors=len(sectors),
            status=STATUS_INSUFFICIENT_DATA,
            reason=f"行业数 {len(sectors)} < 最小要求 {DEFAULT_MIN_SECTORS}",
        )

    # 计算总收益率
    total_p = compute_total_return(portfolio_weights, portfolio_returns)
    total_b = compute_total_return(benchmark_weights, benchmark_returns)
    excess_return = total_p - total_b

    # 逐行业计算三效应
    sector_results: list[SectorAttribution] = []
    sum_ar = 0.0
    sum_sr = 0.0
    sum_ir = 0.0

    for sector in sectors:
        w_p = float(portfolio_weights.get(sector, 0.0))
        w_b = float(benchmark_weights.get(sector, 0.0))
        r_p = float(portfolio_returns.get(sector, 0.0))
        r_b = float(benchmark_returns.get(sector, 0.0))

        ar = compute_allocation_effect(w_p, w_b, r_b, total_b)
        sr = compute_selection_effect(w_b, r_p, r_b)
        ir = compute_interaction_effect(w_p, w_b, r_p, r_b)
        total_effect = ar + sr + ir
        contribution = w_p * r_p - w_b * r_b

        sector_results.append(
            SectorAttribution(
                sector=sector,
                sector_name=names_map.get(sector, sector),
                portfolio_weight=w_p,
                benchmark_weight=w_b,
                portfolio_return=r_p,
                benchmark_return=r_b,
                weight_diff=w_p - w_b,
                return_diff=r_p - r_b,
                allocation_effect=ar,
                selection_effect=sr,
                interaction_effect=ir,
                total_effect=total_effect,
                contribution_to_excess=contribution,
            )
        )

        sum_ar += ar
        sum_sr += sr
        sum_ir += ir

    # 残差 = 超额收益 - 三效应总和 (理论上应为 0, 用于验证数值精度)
    three_effects_sum = sum_ar + sum_sr + sum_ir
    residual = excess_return - three_effects_sum

    # 异常告警 (不阻断)
    for s in sector_results:
        if abs(s.portfolio_return) > ABNORMAL_RETURN_THRESHOLD:
            logger.warning(
                "[Brinson] 行业 %s 组合收益率异常: %.4f 超过阈值 %.2f",
                s.sector,
                s.portfolio_return,
                ABNORMAL_RETURN_THRESHOLD,
            )

    return BrinsonResult(
        attribution_date=attribution_date,
        total_return=total_p,
        benchmark_return=total_b,
        excess_return=excess_return,
        total_allocation_effect=sum_ar,
        total_selection_effect=sum_sr,
        total_interaction_effect=sum_ir,
        residual=residual,
        sector_attributions=sector_results,
        n_sectors=len(sector_results),
        benchmark_code=benchmark_code,
        status=STATUS_OK,
        reason="",
    )


# ============================================================
# BrinsonAttributionManager 主类
# ============================================================


class BrinsonAttributionManager:
    """Brinson 归因管理器 (HC-1 Feature Flag + HC-5 ConfigManager).

    Feature Flag:
        USE_BRINSON_ATTRIBUTION=False (默认): 返回全零降级结果
        USE_BRINSON_ATTRIBUTION=True: 执行完整 Brinson 归因

    Usage:
        >>> mgr = BrinsonAttributionManager()
        >>> result = mgr.attribute(
        ...     portfolio_weights={"finance": 0.4, "tech": 0.6},
        ...     benchmark_weights={"finance": 0.3, "tech": 0.7},
        ...     portfolio_returns={"finance": 0.02, "tech": 0.05},
        ...     benchmark_returns={"finance": 0.01, "tech": 0.03},
        ...     attribution_date="2026-07-27",
        ... )
        >>> logger.info(result.total_allocation_effect)
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
        self._sectors_cfg = self._config.get("sectors", []) or []
        self._thresholds = self._config.get("thresholds", {}) or {}
        self._report_cfg = self._config.get("report", {}) or {}

        # 从配置加载参数
        self._benchmark_code = str(
            self._settings.get("primary_benchmark", DEFAULT_PRIMARY_BENCHMARK)
        )
        self._weight_tolerance = float(
            self._settings.get("weight_sum_tolerance", DEFAULT_WEIGHT_SUM_TOLERANCE)
        )
        self._min_sectors = int(self._settings.get("min_sectors", DEFAULT_MIN_SECTORS))
        self._zero_weight_epsilon = float(
            self._thresholds.get("zero_weight_epsilon", ZERO_WEIGHT_EPSILON)
        )
        self._abnormal_threshold = float(
            self._thresholds.get("abnormal_return_threshold", ABNORMAL_RETURN_THRESHOLD)
        )

        # 行业名称映射 (从配置加载, 不存在则用默认)
        self._sector_names: dict[str, str] = dict(SECTOR_NAMES)
        for s in self._sectors_cfg:
            if isinstance(s, dict):
                code = s.get("code", "")
                name = s.get("name", "")
                if code and name:
                    self._sector_names[code] = name

        # 默认基准权重 (从配置加载)
        self._default_benchmark_weights: dict[str, float] = {
            k: float(v)
            for k, v in (self._config.get("benchmark_sector_weights", {}) or {}).items()
        }

    def _load_config(self, config_name: str) -> dict[str, Any]:
        """加载配置 (走 ConfigManager 4 级优先级, HC-5)."""
        try:
            cfg = get_config(config_name, default={}) or {}
            if not cfg:
                logger.warning(
                    "[Brinson] 配置未找到: %s, 使用默认配置",
                    config_name,
                )
            return cfg
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            logger.warning(
                "[Brinson] 配置加载失败: %s (%s), 使用默认配置",
                config_name,
                e,
            )
            return {}

    def _is_enabled(self) -> bool:
        """检查 Feature Flag 是否启用 (HC-1)."""
        try:
            from utils.infra.feature_flags import is_enabled

            return bool(is_enabled(self._feature_flag_name))
        except (
            ImportError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ):  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            return False

    # ============================================================
    # 公开 API
    # ============================================================

    def attribute(
        self,
        portfolio_weights: dict[str, float],
        benchmark_weights: dict[str, float] | None = None,
        portfolio_returns: dict[str, float] | None = None,
        benchmark_returns: dict[str, float] | None = None,
        attribution_date: str = "",
        benchmark_code: str | None = None,
        validate: bool = True,
    ) -> BrinsonResult:
        """执行 Brinson 三效应归因.

        Feature Flag 透传 (HC-1):
            - USE_BRINSON_ATTRIBUTION=False: 返回 BrinsonResult(status="feature_flag_disabled") 全零
            - USE_BRINSON_ATTRIBUTION=True: 执行完整归因

        Args:
            portfolio_weights: {行业: 组合权重}
            benchmark_weights: {行业: 基准权重} (None 时用配置中的默认基准权重)
            portfolio_returns: {行业: 组合收益率}
            benchmark_returns: {行业: 基准收益率}
            attribution_date: 归因日期 (YYYY-MM-DD)
            benchmark_code: 基准标的代码 (None 时用配置中的 primary_benchmark)
            validate: 是否校验权重归一化

        Returns:
            BrinsonResult 归因结果
        """
        # HC-1: Feature Flag 透传
        if not self._is_enabled():
            return BrinsonResult(
                attribution_date=attribution_date,
                benchmark_code=benchmark_code or self._benchmark_code,
                status=STATUS_FEATURE_FLAG_DISABLED,
                reason=f"Feature Flag {self._feature_flag_name}=False, 返回全零降级结果",
            )

        # 基准权重默认值
        if benchmark_weights is None:
            benchmark_weights = self._default_benchmark_weights
            if not benchmark_weights:
                return BrinsonResult(
                    attribution_date=attribution_date,
                    benchmark_code=benchmark_code or self._benchmark_code,
                    status=STATUS_INSUFFICIENT_DATA,
                    reason="基准权重未提供且配置中无默认值",
                )

        # 收益率必填
        if not portfolio_returns or not benchmark_returns:
            return BrinsonResult(
                attribution_date=attribution_date,
                benchmark_code=benchmark_code or self._benchmark_code,
                status=STATUS_INSUFFICIENT_DATA,
                reason="组合或基准收益率未提供",
            )

        try:
            return attribute_brinson(
                portfolio_weights=portfolio_weights,
                benchmark_weights=benchmark_weights,
                portfolio_returns=portfolio_returns,
                benchmark_returns=benchmark_returns,
                attribution_date=attribution_date,
                benchmark_code=benchmark_code or self._benchmark_code,
                sector_names=self._sector_names,
                validate=validate,
                weight_tolerance=self._weight_tolerance,
            )
        except (InvalidInputError, WeightNotNormalizedError) as e:
            logger.warning("[Brinson] 归因失败: %s", e)
            return BrinsonResult(
                attribution_date=attribution_date,
                benchmark_code=benchmark_code or self._benchmark_code,
                status=(
                    STATUS_EMPTY_INPUT
                    if isinstance(e, InvalidInputError)
                    else STATUS_SECTOR_MISMATCH
                ),
                reason=str(e),
            )

    def attribute_from_positions(
        self,
        portfolio_positions: list[dict[str, Any]],
        benchmark_positions: list[dict[str, Any]],
        attribution_date: str = "",
        benchmark_code: str | None = None,
    ) -> BrinsonResult:
        """从持仓列表执行归因 (聚合到行业维度).

        Args:
            portfolio_positions: 组合持仓列表 [{"code", "weight", "return", "sector"}, ...]
            benchmark_positions: 基准持仓列表 [{"code", "weight", "return", "sector"}, ...]
            attribution_date: 归因日期
            benchmark_code: 基准标的代码

        Returns:
            BrinsonResult 归因结果
        """
        if not self._is_enabled():
            return BrinsonResult(
                attribution_date=attribution_date,
                benchmark_code=benchmark_code or self._benchmark_code,
                status=STATUS_FEATURE_FLAG_DISABLED,
                reason=f"Feature Flag {self._feature_flag_name}=False",
            )

        if not portfolio_positions or not benchmark_positions:
            return BrinsonResult(
                attribution_date=attribution_date,
                benchmark_code=benchmark_code or self._benchmark_code,
                status=STATUS_INSUFFICIENT_DATA,
                reason="持仓列表为空",
            )

        # 聚合到行业维度
        p_weights, p_returns = self._aggregate_positions_to_sectors(portfolio_positions)
        b_weights, b_returns = self._aggregate_positions_to_sectors(benchmark_positions)

        return self.attribute(
            portfolio_weights=p_weights,
            benchmark_weights=b_weights,
            portfolio_returns=p_returns,
            benchmark_returns=b_returns,
            attribution_date=attribution_date,
            benchmark_code=benchmark_code,
        )

    def _aggregate_positions_to_sectors(
        self,
        positions: list[dict[str, Any]],
    ) -> tuple[dict[str, float], dict[str, float]]:
        """将资产级持仓聚合到行业级 (按 sector 字段分组加权).

        Args:
            positions: [{"code", "weight", "return", "sector"}, ...]

        Returns:
            (sector_weights, sector_returns)
        """
        sector_weights: dict[str, float] = {}
        sector_weighted_returns: dict[str, float] = {}

        for pos in positions:
            sector = str(pos.get("sector", "unknown"))
            weight = float(pos.get("weight", 0.0))
            ret = float(pos.get("return", 0.0))

            sector_weights[sector] = sector_weights.get(sector, 0.0) + weight
            sector_weighted_returns[sector] = (
                sector_weighted_returns.get(sector, 0.0) + weight * ret
            )

        # 行业收益率 = 行业内加权平均收益率 = Σ(w_i × r_i) / Σ(w_i)
        sector_returns: dict[str, float] = {}
        for sector, w in sector_weights.items():
            if w > self._zero_weight_epsilon:
                sector_returns[sector] = sector_weighted_returns[sector] / w
            else:
                sector_returns[sector] = 0.0

        return sector_weights, sector_returns

    def get_default_benchmark_weights(self) -> dict[str, float]:
        """获取默认基准行业权重 (从配置加载)."""
        return dict(self._default_benchmark_weights)

    def get_sector_names(self) -> dict[str, str]:
        """获取行业代码 -> 中文名称映射."""
        return dict(self._sector_names)


# ============================================================
# Feature Flag 透传 (HC-1)
# ============================================================


def is_brinson_attribution_enabled() -> bool:
    """检查 USE_BRINSON_ATTRIBUTION Feature Flag 是否启用.

    HC-1 硬约束: Feature Flag 透传, 默认 False (关闭时返回全零降级结果).

    Returns:
        True 启用, False 关闭 (默认)
    """
    try:
        from utils.infra.feature_flags import is_enabled

        return bool(is_enabled(FLAG_NAME))
    except ImportError:
        return False
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
        TimeoutError,
        ConnectionError,
    ):  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
        return False


# ============================================================
# 便捷函数
# ============================================================


def attribute_brinson_simple(
    portfolio_weights: dict[str, float],
    benchmark_weights: dict[str, float],
    portfolio_returns: dict[str, float],
    benchmark_returns: dict[str, float],
    attribution_date: str = "",
    benchmark_code: str = DEFAULT_PRIMARY_BENCHMARK,
) -> BrinsonResult:
    """Brinson 归因便捷函数 (不走 Feature Flag, 直接执行算法).

    适合脚本/Jupyter/研究场景, 不依赖 Feature Flag 配置.

    Args:
        portfolio_weights: 组合行业权重
        benchmark_weights: 基准行业权重
        portfolio_returns: 组合行业收益率
        benchmark_returns: 基准行业收益率
        attribution_date: 归因日期
        benchmark_code: 基准标的代码

    Returns:
        BrinsonResult 归因结果
    """
    return attribute_brinson(
        portfolio_weights=portfolio_weights,
        benchmark_weights=benchmark_weights,
        portfolio_returns=portfolio_returns,
        benchmark_returns=benchmark_returns,
        attribution_date=attribution_date,
        benchmark_code=benchmark_code,
    )


def create_default_manager() -> BrinsonAttributionManager:
    """创建默认配置的 BrinsonAttributionManager.

    Returns:
        BrinsonAttributionManager 实例
    """
    return BrinsonAttributionManager()


# ============================================================
# 模块导出
# ============================================================

__all__ = [
    "ABNORMAL_RETURN_THRESHOLD",
    "DEFAULT_CONFIG_NAME",
    "DEFAULT_MIN_SECTORS",
    "DEFAULT_PRIMARY_BENCHMARK",
    "DEFAULT_SECONDARY_BENCHMARK",
    "DEFAULT_SECTORS",
    "DEFAULT_WEIGHT_SUM_TOLERANCE",
    # 常量
    "FLAG_NAME",
    "SECTOR_NAMES",
    "STATUS_EMPTY_INPUT",
    "STATUS_FEATURE_FLAG_DISABLED",
    "STATUS_INSUFFICIENT_DATA",
    # 状态码
    "STATUS_OK",
    "STATUS_SECTOR_MISMATCH",
    "ZERO_RETURN_EPSILON",
    "ZERO_WEIGHT_EPSILON",
    # 异常
    "BrinsonAttributionError",
    # 主类
    "BrinsonAttributionManager",
    "BrinsonResult",
    "InsufficientDataError",
    "InvalidInputError",
    # 数据类
    "SectorAttribution",
    "SectorMismatchError",
    "WeightNotNormalizedError",
    "align_sectors",
    "attribute_brinson",
    "attribute_brinson_simple",
    # 核心算法函数
    "compute_allocation_effect",
    "compute_interaction_effect",
    "compute_selection_effect",
    "compute_total_return",
    "create_default_manager",
    # 便捷函数
    "is_brinson_attribution_enabled",
    "validate_weights",
]
