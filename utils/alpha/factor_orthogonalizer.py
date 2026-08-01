"""因子正交化过滤器 — 模块整合 8.4 Phase 3.

Flag: USE_VIBE_FACTOR_INJECTION (默认 False, 双签启用)
要求: 正交性 corr < 0.7 (来自 feature_flags.yaml 约束)

功能:
    1. 计算因子间相关系数矩阵
    2. 贪心选择: 按方差降序, 跳过与已选因子 |corr| >= 阈值的因子
    3. 返回正交化后的因子子集 + 诊断信息

设计原则:
    1. 失败安全: 任何异常返回空子集, 不阻断主流程
    2. 不可变性: 不修改输入因子字典
    3. HC-1: flag-gated, 关闭时返回空列表 (调用方降级)
    4. 审计: 过滤决策写入日志 (哪些因子被丢弃, 与谁相关)

算法:
    1. 计算相关系数矩阵 C (Pearson)
    2. 按方差降序排序 (方差大 = 信息量高)
    3. 贪心选择: 对每个因子 f, 若与所有已选因子 |C[f][s]| < threshold, 则选入
    4. 复杂度 O(n²), n 为因子数 (典型 <50)

API:
    from utils.alpha.factor_orthogonalizer import orthogonalize_factors

    selected, report = orthogonalize_factors(
        factor_series={"alpha_001": pd.Series(...), "alpha_002": pd.Series(...)},
        threshold=0.7,
    )
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from utils.infra.feature_flags import is_enabled

logger = logging.getLogger("factor_orthogonalizer")

# 默认正交性阈值 (来自 feature_flags.yaml: 正交性 corr < 0.7)
DEFAULT_THRESHOLD = 0.7

# 最小方差阈值 (低于此值的因子视为常数, 直接丢弃)
MIN_VARIANCE = 1e-10

# 最小样本数 (少于此值无法计算可靠相关系数)
MIN_SAMPLES = 20


# ============================================================
# 数据结构
# ============================================================
@dataclass
class OrthogonalizationReport:
    """正交化过滤报告.

    Attributes:
        total_factors: 输入因子总数
        selected_factors: 选中因子列表 (正交化后)
        dropped_factors: 被丢弃因子列表
        drop_reasons: {factor_name: reason} 丢弃原因
        correlation_matrix: 相关系数矩阵 (DataFrame)
        threshold: 使用的阈值
        status: "success" | "disabled" | "error"
        error: 错误信息 (仅 status=error 时)
    """

    total_factors: int = 0
    selected_factors: list[str] = field(default_factory=list)
    dropped_factors: list[str] = field(default_factory=list)
    drop_reasons: dict[str, str] = field(default_factory=dict)
    correlation_matrix: pd.DataFrame | None = None
    threshold: float = DEFAULT_THRESHOLD
    status: str = "success"
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转为字典 (用于审计日志)."""
        return {
            "total_factors": self.total_factors,
            "selected_count": len(self.selected_factors),
            "dropped_count": len(self.dropped_factors),
            "selected_factors": self.selected_factors,
            "dropped_factors": self.dropped_factors,
            "drop_reasons": self.drop_reasons,
            "threshold": self.threshold,
            "status": self.status,
            "error": self.error,
        }


# ============================================================
# 核心算法
# ============================================================
def orthogonalize_factors(
    factor_series: dict[str, pd.Series],
    threshold: float = DEFAULT_THRESHOLD,
) -> tuple[list[str], OrthogonalizationReport]:
    """对因子集合进行正交化过滤.

    HC-1: 受 USE_VIBE_FACTOR_INJECTION flag 控制.
    失败安全: 任何异常返回空列表 + error 报告.

    Args:
        factor_series: {factor_name: pd.Series} 因子时间序列
        threshold: 相关系数阈值, |corr| >= threshold 的因子对视为冗余

    Returns:
        (selected_factor_names, report)
        - selected_factor_names: 正交化后保留的因子名列表
        - report: OrthogonalizationReport 诊断报告
    """
    report = OrthogonalizationReport(
        total_factors=len(factor_series),
        threshold=threshold,
    )

    # HC-1: flag 检查
    if not is_enabled("USE_VIBE_FACTOR_INJECTION"):
        report.status = "disabled"
        logger.debug("USE_VIBE_FACTOR_INJECTION=False, 跳过正交化")
        return [], report

    # 输入校验
    if not factor_series:
        report.status = "success"
        return [], report

    try:
        # 1. 构建因子 DataFrame (index=日期, columns=因子名)
        factor_df = _build_factor_dataframe(factor_series)
        if factor_df is None or factor_df.empty:
            report.status = "error"
            report.error = "无法构建因子 DataFrame (数据不足或全部 NaN)"
            return [], report

        # 2. 计算相关系数矩阵
        corr_matrix = factor_df.corr(method="pearson")
        report.correlation_matrix = corr_matrix

        # 3. 按方差降序排序 (方差大 = 信息量高, 优先保留)
        variances = factor_df.var().sort_values(ascending=False)
        sorted_factors = [f for f in variances.index if variances[f] >= MIN_VARIANCE]

        # 记录低方差丢弃
        low_var_drops = [f for f in factor_df.columns if variances[f] < MIN_VARIANCE]
        for f in low_var_drops:
            report.drop_reasons[f] = f"低方差 ({variances[f]:.2e} < {MIN_VARIANCE})"

        # 4. 贪心选择
        selected: list[str] = []
        for factor in sorted_factors:
            keep = True
            for sel in selected:
                corr_val = corr_matrix.loc[factor, sel]
                if pd.isna(corr_val):
                    continue
                if abs(float(corr_val)) >= threshold:
                    report.drop_reasons[factor] = f"与 {sel} 相关性 {float(corr_val):.3f} >= {threshold}"
                    keep = False
                    break
            if keep:
                selected.append(factor)

        # 5. 填充报告
        report.selected_factors = selected
        report.dropped_factors = [f for f in factor_df.columns if f not in selected]

        logger.info(
            "[orthogonalize] 正交化完成: %d/%d 因子选中 (threshold=%.2f, 丢弃=%d)",
            len(selected),
            len(factor_df.columns),
            threshold,
            len(report.dropped_factors),
        )

        return selected, report

    except Exception as e:
        report.status = "error"
        report.error = f"正交化异常: {e}"
        logger.warning("[orthogonalize] 正交化失败: %s", e, exc_info=True)
        return [], report


def _build_factor_dataframe(factor_series: dict[str, pd.Series]) -> pd.DataFrame | None:
    """将因子时间序列字典转为 DataFrame.

    - 对齐索引 (inner join)
    - 丢弃样本数不足的因子
    - 填充 NaN 为 0 (避免 corr 计算失败)
    """
    if not factor_series:
        return None

    # 过滤无效序列
    valid_series: dict[str, pd.Series] = {}
    for name, series in factor_series.items():
        if series is None or len(series) < MIN_SAMPLES:
            continue
        # 确保是 pd.Series
        if isinstance(series, pd.Series):
            valid_series[name] = series
        elif isinstance(series, pd.DataFrame):
            # 取第一列
            valid_series[name] = series.iloc[:, 0]

    if not valid_series:
        return None

    # 对齐索引 (inner join)
    df = pd.DataFrame(valid_series)
    if df.empty:
        return None

    # 填充 NaN (避免 corr 返回全 NaN)
    df = df.fillna(method="ffill").fillna(0.0)

    return df


# ============================================================
# 辅助函数
# ============================================================
def get_correlation_summary(
    factor_series: dict[str, pd.Series],
) -> dict[str, Any]:
    """获取因子相关性摘要 (不进行过滤, 仅诊断).

    Returns:
        {
            "n_factors": int,
            "high_correlation_pairs": list of (f1, f2, corr),
            "max_correlation": float,
            "mean_abs_correlation": float,
        }
    """
    factor_df = _build_factor_dataframe(factor_series)
    if factor_df is None:
        return {"n_factors": 0, "high_correlation_pairs": [], "max_correlation": 0.0, "mean_abs_correlation": 0.0}

    corr = factor_df.corr()
    n = len(corr)

    high_pairs: list[tuple[str, str, float]] = []
    max_corr = 0.0
    abs_sum = 0.0
    count = 0

    for i in range(n):
        for j in range(i + 1, n):
            c = float(corr.iloc[i, j])
            if pd.isna(c):
                continue
            abs_c = abs(c)
            max_corr = max(max_corr, abs_c)
            abs_sum += abs_c
            count += 1
            if abs_c >= DEFAULT_THRESHOLD:
                high_pairs.append((corr.index[i], corr.columns[j], round(c, 3)))

    return {
        "n_factors": n,
        "high_correlation_pairs": high_pairs,
        "max_correlation": round(max_corr, 3),
        "mean_abs_correlation": round(abs_sum / count, 3) if count > 0 else 0.0,
    }


# ============================================================
# 模块级快捷函数
# ============================================================
def filter_orthogonal(
    factor_series: dict[str, pd.Series],
    threshold: float = DEFAULT_THRESHOLD,
) -> dict[str, pd.Series]:
    """快捷函数: 返回正交化后的因子子集字典.

    Returns:
        {factor_name: pd.Series} 仅包含正交化后保留的因子
    """
    selected, _ = orthogonalize_factors(factor_series, threshold)
    return {name: factor_series[name] for name in selected if name in factor_series}
