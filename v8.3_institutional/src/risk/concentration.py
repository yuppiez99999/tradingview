"""P0-6 组合集中度风险监控模块

解决诊断报告:
  "高端制造板块权重高达40%，内部6只标的行业相关性极高。
   系统自称'风险平价'但实现的是简单 w_i ∝ 1/σ_i，完全忽略资产间相关性。
   真正的风险平价要求各标的对组合的边际风险贡献(MRC)相等。"

功能：
  1. MRC (边际风险贡献) 计算 -- 各标的对组合总风险的增量贡献
  2. RC (风险贡献) -- MRC占比化为百分比
  3. HHI (赫芬达尔指数) -- 集中度归一化指标
  4. Effective N -- 有效分散资产数
  5. 集中度预警 -- MRC超25%/板块超35%/HHI超阈值
  6. 日报生成
"""

import numpy as np
from typing import List, Dict, Optional


def compute_mrc(
    returns: np.ndarray,
    weights: np.ndarray,
) -> np.ndarray:
    """计算边际风险贡献 (Marginal Risk Contribution)。

    MRC_i = w_i * (Σ @ w)_i / σ_p

    即: 权重 * 协方差矩阵第i行·权重 / 组合波动率
    MRC_i 的正规性质: sum(MRC_i) = σ_p

    Args:
        returns: shape (n_periods, n_assets) 历史收益率
        weights: shape (n_assets,) 当前权重

    Returns:
        MRC array shape (n_assets,)
    """
    cov = np.cov(returns, rowvar=False)
    portfolio_var = weights @ cov @ weights
    portfolio_vol = np.sqrt(max(portfolio_var, 1e-20))

    if portfolio_vol < 1e-12:
        return np.zeros(len(weights))

    # MRC = w ∘ (Cov @ w) / σ_p  (逐元素乘积)
    marginal = cov @ weights
    mrc = weights * marginal / portfolio_vol
    return mrc


def compute_risk_contribution(
    returns: np.ndarray,
    weights: np.ndarray,
) -> np.ndarray:
    """计算风险贡献百分比。

    RC_i = MRC_i / sum(MRC) * 100  →  sum(RC) = 100%

    Args:
        returns: 历史收益率
        weights: 当前权重

    Returns:
        RC(%) array shape (n_assets,), sum=100
    """
    mrc = compute_mrc(returns, weights)
    total = mrc.sum()
    if total < 1e-12:
        n = len(weights)
        return np.ones(n) * 100.0 / n
    return mrc / total * 100.0


def effective_number_of_assets(weights: np.ndarray) -> float:
    """计算有效分散资产数。

    Effective N = 1 / sum(w_i^2)  (逆赫芬达尔)

    Args:
        weights: 资产权重

    Returns:
        有效资产数 (1 <= N_eff <= n)
    """
    if weights.sum() < 1e-12:
        return 1.0
    hhi = compute_hhi(weights)
    if hhi < 1e-12:
        return float(len(weights))
    return 1.0 / hhi


def compute_hhi(weights: np.ndarray) -> float:
    """计算赫芬达尔-赫希曼指数 (HHI)。

    HHI = sum(w_i^2) ∈ [1/n, 1]
    - 完全分散: HHI = 1/n
    - 完全集中: HHI = 1

    Args:
        weights: 资产权重

    Returns:
        HHI 值
    """
    return float(np.sum(weights**2))


def detect_concentration_alerts(
    returns: np.ndarray,
    weights: np.ndarray,
    names: Optional[List[str]] = None,
    sectors: Optional[Dict[str, str]] = None,
    mrc_threshold: float = 0.25,
    sector_threshold: float = 0.35,
    hhi_threshold: float = 0.15,
) -> List[Dict]:
    """检测集中度风险警报。

    Args:
        returns: 历史收益率
        weights: 当前权重
        names: 标的名称
        sectors: {标的名称: 板块名称} 映射
        mrc_threshold: 单标的MRC占比阈值 (默认25%)
        sector_threshold: 板块MRC占比阈值 (默认35%)
        hhi_threshold: HHI阈值

    Returns:
        [{'type': 'mrc'|'sector_concentration'|'hhi',
          'asset': name, 'value': float, 'threshold': float, 'message': str}, ...]
    """
    n_assets = len(weights)
    if names is None:
        names = [f"Asset_{i}" for i in range(n_assets)]

    alerts = []

    # 1. MRC 单标的警报
    rc = compute_risk_contribution(returns, weights)
    for i in range(n_assets):
        if rc[i] > mrc_threshold * 100:  # 转为百分比
            alerts.append(
                {
                    "type": "mrc",
                    "asset": names[i],
                    "value": rc[i],
                    "threshold": mrc_threshold * 100,
                    "message": (f"{names[i]} MRC占比{rc[i]:.1f}%超过阈值{mrc_threshold * 100:.0f}%"),
                }
            )

    # 2. 板块集中度
    if sectors is not None:
        sector_weights: Dict[str, float] = {}
        for i in range(n_assets):
            sector = sectors.get(names[i], "未分类")
            sector_weights[sector] = sector_weights.get(sector, 0.0) + weights[i]

        for sector, weight in sector_weights.items():
            if weight > sector_threshold:
                alerts.append(
                    {
                        "type": "sector_concentration",
                        "asset": sector,
                        "value": weight * 100,
                        "threshold": sector_threshold * 100,
                        "message": (f"板块 '{sector}' 权重{weight * 100:.1f}%超过阈值{sector_threshold * 100:.0f}%"),
                    }
                )

    # 3. HHI 警报
    hhi = compute_hhi(weights)
    if hhi > hhi_threshold:
        alerts.append(
            {
                "type": "hhi",
                "asset": "组合整体",
                "value": hhi,
                "threshold": hhi_threshold,
                "message": (f"HHI={hhi:.4f}超过阈值{hhi_threshold:.2f}, 有效分散资产数={1 / hhi:.1f}"),
            }
        )

    return alerts


def generate_concentration_report(
    returns: np.ndarray,
    weights: np.ndarray,
    names: Optional[List[str]] = None,
    sectors: Optional[Dict[str, str]] = None,
    mrc_threshold: float = 0.25,
    sector_threshold: float = 0.35,
    hhi_threshold: float = 0.15,
) -> str:
    """生成组合集中度日报。

    Args:
        returns: 历史收益率
        weights: 当前权重
        names: 标的名称
        sectors: 板块映射
        mrc_threshold: MRC阈值
        sector_threshold: 板块阈值
        hhi_threshold: HHI阈值

    Returns:
        Markdown格式日报
    """
    n_assets = len(weights)
    if names is None:
        names = [f"标的_{i + 1}" for i in range(n_assets)]

    rc = compute_risk_contribution(returns, weights)
    hhi = compute_hhi(weights)
    eff_n = effective_number_of_assets(weights)
    alerts = detect_concentration_alerts(
        returns,
        weights,
        names=names,
        sectors=sectors,
        mrc_threshold=mrc_threshold,
        sector_threshold=sector_threshold,
        hhi_threshold=hhi_threshold,
    )

    # 警报级别
    n_alerts = len(alerts)
    if n_alerts == 0:
        level = "正常"
        level_desc = "组合分散度良好，无集中度风险警报"
    elif n_alerts <= 2:
        level = "关注"
        level_desc = f"{n_alerts}项轻度警报，建议关注"
    else:
        level = "警告"
        level_desc = f"{n_alerts}项警报，建议立即调整"

    lines = []
    lines.append("## 组合集中度风险日报")
    lines.append("")
    lines.append(f"**警报级别**: **{level}** — {level_desc}")
    lines.append("")
    lines.append(f"- 有效分散资产数: **{eff_n:.1f}** / {n_assets}")
    lines.append(f"- HHI: **{hhi:.4f}** (阈值: {hhi_threshold:.2f})")
    lines.append("")

    # MRC 明细表
    lines.append("### 边际风险贡献 (MRC)")
    lines.append("")
    lines.append("| 标的 | 权重 | MRC% | 状态 |")
    lines.append("|------|------|------|------|")
    for i in range(n_assets):
        status = "WARN" if rc[i] > mrc_threshold * 100 else "OK"
        sector_str = f" [{sectors.get(names[i], '')}]" if sectors else ""
        lines.append(f"| {names[i]}{sector_str} | {weights[i]:.1%} | {rc[i]:.1f}% | {status} |")
    lines.append("")

    # 板块汇总
    if sectors is not None:
        sector_weights: Dict[str, float] = {}
        sector_rc: Dict[str, float] = {}
        for i in range(n_assets):
            s = sectors.get(names[i], "未分类")
            sector_weights[s] = sector_weights.get(s, 0.0) + weights[i]
            sector_rc[s] = sector_rc.get(s, 0.0) + rc[i]

        lines.append("### 板块风险分布")
        lines.append("")
        lines.append("| 板块 | 权重 | MRC% | 状态 |")
        lines.append("|------|------|------|------|")
        for sector in sorted(sector_weights):
            s_status = "WARN" if sector_weights[sector] > sector_threshold else "OK"
            lines.append(f"| {sector} | {sector_weights[sector]:.1%} | {sector_rc[sector]:.1f}% | {s_status} |")
        lines.append("")

    # 警报详情
    if alerts:
        lines.append("### 集中度警报")
        lines.append("")
        for a in alerts:
            lines.append(f"- [{a['type']}] {a['message']}")
        lines.append("")

    return "\n".join(lines)
