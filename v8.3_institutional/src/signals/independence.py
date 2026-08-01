"""P0-4 信号源独立性验证模块

解决诊断报告:
  "系统融合ML/AI Hedge Fund/GLM5/康波/KRONOS/TimeSFM/Qwen共7个预测源，
   使用历史胜率加权。这是典型的数据窥探。"

功能：
  1. 计算7个预测源的相关性矩阵
  2. 检测高度相关信号对 (r > 0.3 → 冗余)
  3. 残差化融合 -- 对每个新信号，用已选信号回归，保留残差
  4. 贝叶斯收缩权重 -- 向等权收缩，小样本时更强
  5. 有效独立信号源数量 -- 基于特征值分解
"""

from typing import Dict, List, Optional

import numpy as np


def compute_signal_correlation_matrix(
    signals: np.ndarray,
    names: Optional[List[str]] = None,
) -> np.ndarray:
    """计算信号源相关性矩阵。

    Args:
        signals: shape (n_periods, n_signals) 各预测源的方向信号
        names: 信号源名称列表

    Returns:
        correlation matrix shape (n_signals, n_signals)
    """
    n_signals = signals.shape[1]

    # 处理恒值列: 填充微量噪音避免除零
    safe_signals = signals.copy()
    for j in range(n_signals):
        col_std = np.std(signals[:, j])
        if col_std < 1e-12:
            safe_signals[:, j] += np.random.randn(len(safe_signals)) * 1e-10

    corr = np.corrcoef(safe_signals, rowvar=False)
    # 修复 NaN (全零列)
    corr = np.nan_to_num(corr, nan=0.0)
    # 强制对角线为1
    np.fill_diagonal(corr, 1.0)

    return corr


def detect_redundant_signals(
    corr: np.ndarray,
    threshold: float = 0.3,
    names: Optional[List[str]] = None,
) -> List[Dict]:
    """检测高度相关(冗余)的信号对。

    Args:
        corr: 相关性矩阵 (n_signals x n_signals)
        threshold: 相关性阈值，超此值视为冗余
        names: 信号源名称

    Returns:
        [{'signal_a': name, 'signal_b': name, 'correlation': float}, ...]
        每对只报告一次 (i < j)
    """
    n = corr.shape[0]
    if names is None:
        names = [f"Signal_{i}" for i in range(n)]

    redundant = []
    for i in range(n):
        for j in range(i + 1, n):
            if abs(corr[i, j]) >= threshold:
                redundant.append(
                    {
                        "signal_a": names[i],
                        "signal_b": names[j],
                        "correlation": float(abs(corr[i, j])),
                    }
                )

    # 按相关性降序排列
    redundant.sort(key=lambda x: x["correlation"], reverse=True)
    return redundant


def effective_independent_signals(corr: np.ndarray) -> float:
    """有效独立信号源数量 (基于特征值分解)。

    使用 1 / sum(lambda_i^2) 的集中度倒数度量，
    类似有效组合资产数 (Effective N)。

    Args:
        corr: 相关性矩阵

    Returns:
        有效独立信号数 (1 <= n_eff <= n_signals)
    """
    eigenvalues = np.linalg.eigvalsh(corr)
    # 归一化特征值
    eigenvalues = np.maximum(eigenvalues, 0)  # 半正定
    total = eigenvalues.sum()
    if total < 1e-12:
        return 1.0
    norm_eig = eigenvalues / total
    # Effective N = 1 / sum(p_i^2)
    n_eff = 1.0 / (norm_eig**2).sum()
    return min(max(n_eff, 1.0), float(len(eigenvalues)))


def residualize(new_signal: np.ndarray, existing_signal: np.ndarray) -> np.ndarray:
    """对单个已有信号做残差化。

    regress new_signal on existing_signal, 保留残差。
    残差 = 新信号中与已有信号正交的部分。

    Args:
        new_signal: 新信号 (n_periods,)
        existing_signal: 已有信号 (n_periods,)

    Returns:
        残差信号 (n_periods,)
    """
    # OLS: new = beta * existing + residual
    ex_var = np.var(existing_signal)
    if ex_var < 1e-15:
        return new_signal  # 恒值已有信号，无法回归

    # 包含截距项
    X = np.column_stack([np.ones(len(existing_signal)), existing_signal])
    try:
        beta = np.linalg.lstsq(X, new_signal, rcond=None)[0]
        fitted = X @ beta
        residual = new_signal - fitted
    except np.linalg.LinAlgError:
        return new_signal

    return residual


def sequential_residualize(signals: np.ndarray) -> np.ndarray:
    """顺序残差化: 依次将每个信号对前面所有信号做残差化。

    第一个信号保留原样，第二个信号用第一个做残差化，
    第三个用第一、第二个做残差化，以此类推。

    Args:
        signals: shape (n_periods, n_signals)

    Returns:
        残差化后的信号矩阵 (n_periods, n_signals)
    """
    n_periods, n_signals = signals.shape
    residuals = np.zeros_like(signals)
    residuals[:, 0] = signals[:, 0].copy()

    for j in range(1, n_signals):
        # 用前 j 个信号做多元回归
        X = np.column_stack([np.ones(n_periods), residuals[:, :j]])
        y = signals[:, j]
        try:
            beta = np.linalg.lstsq(X, y, rcond=None)[0]
            fitted = X @ beta
            residuals[:, j] = y - fitted
        except np.linalg.LinAlgError:
            residuals[:, j] = y

    return residuals


def bayesian_shrinkage_weights(
    signal_history: np.ndarray,
    historical_accuracy: np.ndarray,
    prior_strength: float = 3.0,
) -> np.ndarray:
    """贝叶斯收缩权重估计。

    将样本内胜率向等权(Empirical Bayes)收缩：
      w_i = (n_i * acc_i + prior_strength * prior_mean) / (n_i + prior_strength)
    其中 prior_mean = mean(accuracy)。

    解决"用样本内胜率确定权重导致严重过拟合"的问题。

    Args:
        signal_history: shape (n_periods, n_signals) 信号历史
        historical_accuracy: shape (n_signals,) 各信号历史方向准确率
        prior_strength: 先验强度 (越大→越向等权收缩)

    Returns:
        收缩后权重 (n_signals,), sum=1
    """
    n_signals = len(historical_accuracy)
    n_periods = signal_history.shape[0]

    # 先验均值: 等权准确率
    prior_mean = np.mean(historical_accuracy)
    if prior_mean <= 0:
        prior_mean = 0.5  # 至少50%

    # 样本有效期: 用信号非零比例调整
    signal_activity = np.mean(np.abs(signal_history), axis=0)
    effective_n = n_periods * np.clip(signal_activity, 0.05, 1.0)

    # 贝叶斯收缩
    posterior = (effective_n * historical_accuracy + prior_strength * prior_mean) / (effective_n + prior_strength)

    # 归一化为权重
    total = posterior.sum()
    if total < 1e-12:
        return np.ones(n_signals) / n_signals

    weights = posterior / total
    return weights


def analyze_signal_independence(
    signals: np.ndarray,
    names: List[str],
    historical_accuracy: np.ndarray,
    corr_threshold: float = 0.3,
) -> str:
    """生成信号独立性完整分析报告。

    Args:
        signals: shape (n_periods, n_signals)
        names: 信号源名称
        historical_accuracy: 各信号源历史准确率
        corr_threshold: 冗余检测阈值

    Returns:
        Markdown格式分析报告
    """
    n_signals = signals.shape[1]

    # 1. 相关性矩阵
    corr = compute_signal_correlation_matrix(signals, names=names)

    # 2. 冗余对检测
    redundant = detect_redundant_signals(corr, threshold=corr_threshold, names=names)

    # 3. 有效独立信号数
    n_eff = effective_independent_signals(corr)

    # 4. 贝叶斯收缩权重
    weights = bayesian_shrinkage_weights(signals, historical_accuracy)

    # 5. 残差化
    residuals = sequential_residualize(signals)
    corr_after = compute_signal_correlation_matrix(residuals, names=names)
    n_eff_after = effective_independent_signals(corr_after)

    lines = []
    lines.append("## 信号源独立性分析报告")
    lines.append("")
    lines.append(f"**分析周期**: {signals.shape[0]} 期 | **信号源数量**: {n_signals}")
    lines.append("")

    # 相关性矩阵
    lines.append("### 信号源相关性矩阵")
    lines.append("")
    header = "| |" + "|".join(f" {n[:8]} " for n in names) + "|"
    lines.append(header)
    sep = "|---|" + "|".join(":---:" for _ in range(n_signals)) + "|"
    lines.append(sep)
    for i in range(n_signals):
        row = f"| {names[i][:8]} |" + "|".join(f" {corr[i, j]:.2f} " for j in range(n_signals)) + "|"
        lines.append(row)
    lines.append("")

    # 冗余检测
    lines.append(f"### 冗余信号对检测 (r > {corr_threshold:.1%})")
    lines.append("")
    if redundant:
        for r in redundant:
            lines.append(f"- **{r['signal_a']}** — **{r['signal_b']}**: r = {r['correlation']:.3f}")
    else:
        lines.append(f"- 未检测到相关性 > {corr_threshold:.1%} 的信号对")

    lines.append("")
    lines.append(f"**冗余对总数**: {len(redundant)}")
    lines.append("")

    # 有效独立信号
    lines.append("### 有效独立信号源数量")
    lines.append("")
    lines.append(f"- 原始: **{n_eff:.2f}** / {n_signals} ({n_eff / n_signals * 100:.0f}%)")
    lines.append(f"- 残差化后: **{n_eff_after:.2f}** / {n_signals} ({n_eff_after / n_signals * 100:.0f}%)")
    improvement = (n_eff_after - n_eff) / n_signals * 100
    lines.append(f"- 提升: {improvement:+.1f}%")
    lines.append("")

    # 贝叶斯收缩权重
    lines.append("### 贝叶斯收缩融合权重")
    lines.append("")
    raw_weights = historical_accuracy / historical_accuracy.sum()
    lines.append("| 信号源 | 原始精度 | 原始权重 | 收缩权重 | 变化 |")
    lines.append("|--------|---------|---------|---------|------|")
    for i in range(n_signals):
        delta = weights[i] - raw_weights[i]
        sign = "+" if delta >= 0 else ""
        lines.append(
            f"| {names[i][:12]} | {historical_accuracy[i]:.1%} | "
            f"{raw_weights[i]:.3f} | {weights[i]:.3f} | {sign}{delta:.3f} |"
        )
    lines.append("")

    # 建议
    lines.append("### 改进建议")
    lines.append("")
    if n_eff < n_signals * 0.6:
        lines.append(f"- 有效信号仅{n_eff:.1f}个(占比{n_eff / n_signals * 100:.0f}%)，建议启用残差化融合以减少冗余")
    if len(redundant) > 0:
        high_pairs = [r for r in redundant if r["correlation"] > 0.5]
        if high_pairs:
            lines.append(f"- {len(high_pairs)}对信号高度相关(r > 0.5)，建议考虑合并或移除冗余信号源")
    if weights.max() > 0.30:
        lines.append(f"- 最大权重{weights.max():.1%}仍较高，可增加 prior_strength 参数")

    return "\n".join(lines)
