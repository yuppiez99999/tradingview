# -*- coding: utf-8 -*-
"""
相关性矩阵每日监控 — v5.10 P0-7 修复
=====================================
每日计算持仓标的滚动相关矩阵、检测相关性突增(危机信号)、
集中度预警、边际风险贡献(MRC)分析。

功能:
1. rolling_correlation_matrix()   — 滚动60日相关矩阵
2. detect_correlation_surge()     — 相关性突增检测
3. check_concentration_risk()     — 集中度预警
4. marginal_risk_contributions()  — MRC计算
5. ensure_psd()                   — 半正定性修复
6. generate_correlation_report()  — 每日监控报告

参考文献:
- Lopez de Prado (2018). Advances in Financial Machine Learning.
- Bridgewater All-Weather 风险平价框架.
"""

from typing import Dict, List, Optional, Tuple

import numpy as np

# ── 常量 ──
DEFAULT_WINDOW = 60  # 滚动窗口 (交易日)
DEFAULT_SURGE_THRESHOLD = 0.25  # 相关性突增阈值
DEFAULT_CORR_THRESHOLD = 0.70  # 集中度预警阈值
DEFAULT_CONCENTRATION = 0.50  # 高相关资产比例阈值


# ═══════════════════════════════════════════════════════
#  滚动相关矩阵
# ═══════════════════════════════════════════════════════


def rolling_correlation_matrix(
    returns: np.ndarray,
    window: int = DEFAULT_WINDOW,
    asset_names: Optional[List[str]] = None,
) -> Tuple[Optional[np.ndarray], str]:
    """计算最近 window 个交易日的滚动相关矩阵

    Args:
        returns: 日收益率矩阵 (n_days, n_assets)
        window: 滚动窗口大小 (默认60个交易日)
        asset_names: 标的名称列表 (可选, 用于标签)

    Returns:
        (corr_matrix, last_date_str) 或 (None, '') 如果数据不足
    """
    if returns is None or returns.shape[0] < window:
        return None, ""

    recent = returns[-window:, :]
    corr = np.corrcoef(recent, rowvar=False)

    # 确保是对称矩阵 (数值精度)
    corr = (corr + corr.T) / 2.0
    # 对角线强制为1
    np.fill_diagonal(corr, 1.0)

    return corr, f"t-{window}"


# ═══════════════════════════════════════════════════════
#  相关性突增检测
# ═══════════════════════════════════════════════════════


def detect_correlation_surge(
    returns: np.ndarray,
    window: int = DEFAULT_WINDOW,
    short_window: int = 20,
    surge_threshold: float = DEFAULT_SURGE_THRESHOLD,
    asset_names: Optional[List[str]] = None,
) -> List[Dict]:
    """检测相关性突增 (危机预警信号)

    对比长窗口(60日)和短窗口(20日)相关矩阵,
    若某对标的短窗口相关性显著高于长窗口, 发出预警。

    典型场景: 2008年金融危机、2015年A股股灾中
    所有资产相关性趋向1。

    Args:
        returns: 日收益率矩阵 (n_days, n_assets)
        window: 长窗口大小
        short_window: 短窗口大小
        surge_threshold: 突增阈值 (>此值触发)
        asset_names: 标的名称

    Returns:
        List[Dict]: 预警列表, 每项包含 pair/long_corr/short_corr/delta
    """
    n_assets = returns.shape[1]
    if returns.shape[0] < window:
        return []

    if asset_names is None:
        asset_names = [f"Asset_{i}" for i in range(n_assets)]

    # 计算长短窗口相关矩阵
    long_corr, _ = rolling_correlation_matrix(returns, window)
    short_corr, _ = rolling_correlation_matrix(returns, short_window)

    if long_corr is None or short_corr is None:
        return []

    alerts = []
    for i in range(n_assets):
        for j in range(i + 1, n_assets):
            delta = short_corr[i, j] - long_corr[i, j]
            if delta > surge_threshold:
                alerts.append(
                    {
                        "pair": f"{asset_names[i]} vs {asset_names[j]}",
                        "i": i,
                        "j": j,
                        "long_corr": round(float(long_corr[i, j]), 4),
                        "short_corr": round(float(short_corr[i, j]), 4),
                        "delta": round(float(delta), 4),
                    }
                )

    # 按delta降序排列
    alerts.sort(key=lambda x: x["delta"], reverse=True)
    return alerts


# ═══════════════════════════════════════════════════════
#  集中度预警
# ═══════════════════════════════════════════════════════


def check_concentration_risk(
    returns: np.ndarray,
    window: int = DEFAULT_WINDOW,
    corr_threshold: float = DEFAULT_CORR_THRESHOLD,
    concentration_threshold: float = DEFAULT_CONCENTRATION,
    asset_names: Optional[List[str]] = None,
) -> List[Dict]:
    """检查组合集中度风险

    当高于 corr_threshold (0.7) 的相关性对数量
    超过总对数的一定比例 (concentration_threshold),
    发出集中度预警。

    Args:
        returns: 日收益率矩阵
        window: 滚动窗口
        corr_threshold: 相关度阈值
        concentration_threshold: 高相关对比例阈值
        asset_names: 标的名称

    Returns:
        List[Dict]: 预警信息
    """
    corr, _ = rolling_correlation_matrix(returns, window, asset_names)
    if corr is None:
        return []

    n_assets = corr.shape[0]
    if asset_names is None:
        asset_names = [f"Asset_{i}" for i in range(n_assets)]

    total_pairs = n_assets * (n_assets - 1) // 2
    if total_pairs == 0:
        return []

    high_corr_pairs = []
    for i in range(n_assets):
        for j in range(i + 1, n_assets):
            if corr[i, j] > corr_threshold:
                high_corr_pairs.append(
                    {
                        "pair": f"{asset_names[i]} vs {asset_names[j]}",
                        "correlation": round(float(corr[i, j]), 4),
                    }
                )

    ratio = len(high_corr_pairs) / total_pairs
    warnings = []

    if ratio > concentration_threshold:
        warnings.append(
            {
                "type": "concentration",
                "level": "HIGH" if ratio > 0.7 else "MEDIUM",
                "high_corr_pairs": len(high_corr_pairs),
                "total_pairs": total_pairs,
                "ratio": round(float(ratio), 4),
                "threshold": concentration_threshold,
                "details": high_corr_pairs[:10],  # 最多10对
                "message": (
                    f"高相关标的占比 {ratio:.1%} 超过阈值 {concentration_threshold:.0%}, "
                    f"({len(high_corr_pairs)}/{total_pairs} 对相关性 > {corr_threshold})"
                ),
            }
        )

    return warnings


# ═══════════════════════════════════════════════════════
#  边际风险贡献 (MRC)
# ═══════════════════════════════════════════════════════


def marginal_risk_contributions(
    weights: np.ndarray,
    cov_matrix: np.ndarray,
) -> Tuple[np.ndarray, float]:
    """计算各资产的边际风险贡献 (Marginal Risk Contribution)

    MRC_i = w_i * (Σw)_i / σ_p
    σ_p = sqrt(w^T Σ w)

    MRC之和 = σ_p (组合总风险)

    Args:
        weights: 权重向量 (n_assets,)
        cov_matrix: 协方差矩阵 (n_assets, n_assets)

    Returns:
        (mrc, portfolio_risk) where mrc_i 是资产i的风险贡献
    """
    # 组合方差
    p_var = weights @ cov_matrix @ weights
    if p_var <= 0:
        return np.zeros_like(weights), 0.0

    portfolio_risk = float(np.sqrt(p_var))

    # 边际贡献: ∂σ_p/∂w = (Σw)_i / σ_p
    marginal = (cov_matrix @ weights) / portfolio_risk

    # 成分贡献: w_i * ∂σ_p/∂w_i
    mrc = weights * marginal

    return mrc, portfolio_risk


# ═══════════════════════════════════════════════════════
#  协方差半正定性修复
# ═══════════════════════════════════════════════════════


def ensure_psd(matrix: np.ndarray, epsilon: float = 1e-8) -> np.ndarray:
    """确保协方差/相关矩阵半正定 (PSD)

    使用特征值裁剪: λ = max(λ, epsilon)
    然后重构: Σ' = Q diag(λ_clipped) Q^T

    Args:
        matrix: 输入矩阵 (应是对称的)
        epsilon: 最小特征值

    Returns:
        半正定矩阵
    """
    # 确保对称
    matrix = (matrix + matrix.T) / 2.0

    eigenvalues, eigenvectors = np.linalg.eigh(matrix)

    # 检查是否已有负特征值
    if (eigenvalues >= -epsilon).all():
        return matrix  # 已半正定, 无需修改

    # 裁剪特征值
    eigenvalues_clipped = np.maximum(eigenvalues, epsilon)

    # 重构
    repaired = eigenvectors @ np.diag(eigenvalues_clipped) @ eigenvectors.T

    # 确保对称且对角线合理
    repaired = (repaired + repaired.T) / 2.0

    return repaired


# ═══════════════════════════════════════════════════════
#  每日监控报告生成
# ═══════════════════════════════════════════════════════


def generate_correlation_report(
    returns: np.ndarray,
    window: int = DEFAULT_WINDOW,
    asset_names: Optional[List[str]] = None,
    weights: np.ndarray = None,
) -> str:
    """生成每日相关性监控报告

    Args:
        returns: 日收益率矩阵 (n_days, n_assets)
        window: 滚动窗口
        asset_names: 标的名称列表
        weights: 持仓权重 (用于MRC分析)

    Returns:
        Markdown 格式的监控报告
    """
    n_assets = returns.shape[1]
    if asset_names is None:
        asset_names = [f"标的{i + 1}" for i in range(n_assets)]

    if weights is None:
        weights = np.ones(n_assets) / n_assets

    lines = []
    lines.append("## 相关性矩阵每日监控")
    lines.append("")
    lines.append(f"**监控窗口**: {window} 交易日 | **标的数量**: {n_assets}")
    lines.append("")

    # 1. 滚动相关矩阵
    corr, _date_label = rolling_correlation_matrix(returns, window, asset_names)
    if corr is None:
        lines.append(f"⚠️ 数据不足 (需要 ≥{window} 个交易日)")
        return "\n".join(lines)

    # 2. 平均相关性
    mask = ~np.eye(n_assets, dtype=bool)
    avg_corr = float(corr[mask].mean())
    lines.append(f"**平均相关性**: {avg_corr:.4f}")
    lines.append("")

    # 3. 集中度检查
    conc_warnings = check_concentration_risk(
        returns,
        window,
        asset_names=asset_names,
    )

    if conc_warnings:
        lines.append("### 🚨 集中度预警")
        for w in conc_warnings:
            lines.append(f"- **{w['level']}**: {w['message']}")
            if w.get("details"):
                for d in w["details"][:5]:
                    lines.append(f"  - {d['pair']}: {d['correlation']:.4f}")
        lines.append("")

    # 4. 相关性突增检测
    surge_alerts = detect_correlation_surge(
        returns,
        window,
        20,
        asset_names=asset_names,
    )
    if surge_alerts:
        lines.append("### 📈 相关性突增预警")
        for a in surge_alerts[:5]:
            lines.append(
                f"- {a['pair']}: 长窗口{a['long_corr']:.3f} → 短窗口{a['short_corr']:.3f} (Δ={a['delta']:.3f})"
            )
        lines.append("")

    # 5. MRC 分析 (使用协方差矩阵)
    if returns.shape[0] >= window:
        recent = returns[-window:, :]
        cov = np.cov(recent, rowvar=False)
        cov = ensure_psd(cov)
        mrc, total_risk = marginal_risk_contributions(weights, cov)

        lines.append("### 边际风险贡献 (MRC)")
        lines.append("")
        lines.append(f"**组合总风险(日)**: {total_risk:.4%}")
        lines.append("")
        lines.append("| 标的 | 权重 | MRC | 风险占比 |")
        lines.append("|------|------|-----|---------|")

        mrc_pct = mrc / mrc.sum() if mrc.sum() > 0 else np.zeros_like(mrc)
        for i in range(n_assets):
            lines.append(f"| {asset_names[i]} | {weights[i]:.1%} | {mrc[i]:.4%} | {mrc_pct[i]:.1%} |")

        # 最大MRC检查
        max_mrc_idx = int(np.argmax(mrc_pct))
        if mrc_pct[max_mrc_idx] > 0.30:
            lines.append("")
            lines.append(f"⚠️ **{asset_names[max_mrc_idx]}** 风险贡献 = {mrc_pct[max_mrc_idx]:.0%} 超过 30% 上限!")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════
#  自测
# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    np.random.seed(42)

    # 生成6标的模拟数据
    n_days, n_assets = 120, 6
    common_1 = np.random.randn(n_days) * 0.01

    returns = np.zeros((n_days, n_assets))
    for i in range(n_assets):
        returns[:, i] = common_1 * (0.3 + 0.6 * (i < 3)) + np.random.randn(n_days) * 0.005

    names = ["中际旭创", "海光信息", "北方华创", "中国神华", "长江电力", "恒瑞医药"]

    # 生成报告
    report = generate_correlation_report(returns, 60, names)
    print(report)
    print("\n✅ 相关性监控模块自测通过")
