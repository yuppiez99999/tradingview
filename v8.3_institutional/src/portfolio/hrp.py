# -*- coding: utf-8 -*-
"""
v7.6 HRP (Hierarchical Risk Parity) — 分层风险平价

对标 Bridgewater All-Weather / 顶级量化组合构建:
  HRP 解决传统 Risk Parity 的三大问题:
    1. 协方差矩阵不稳定 (小样本→大误差)
    2. 集中度风险 (少数低波动资产主导组合)
    3. 尾部依赖 (线性相关不足以描述极值风险)

方法 (Lopez de Prado 2016):
  1. 层次聚类: 基于距离矩阵将资产分层分组
  2. 准对角化: 重排协方差矩阵使大相关聚在一起
  3. 递归平分: 自顶向下分配权重, 每组内部等风险贡献

优势: 不需要逆协方差矩阵 → 更稳定, 样本外表现优于传统 RP
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import dendrogram, linkage
from scipy.spatial.distance import squareform

logger = logging.getLogger("v76.portfolio.hrp")


class HierarchicalRiskParity:
    """
    v7.6 分层风险平价

    Usage:
        returns = pd.DataFrame({symbols: daily_returns})
        hrp = HierarchicalRiskParity()
        weights = hrp.fit(returns)
        # weights: {symbol: weight}
    """

    def __init__(
        self,
        cluster_method: str = "ward",
        distance_method: str = "correlation",
        min_weight: float = 0.01,
        max_weight: float = 0.30,
    ):
        """
        Args:
            cluster_method: 聚类方法 ('ward'/'single'/'complete'/'average')
            distance_method: 距离度量 ('correlation'/'euclidean')
            min_weight: 单标的最小权重
            max_weight: 单标的最大权重 (防过度集中)
        """
        self.cluster_method = cluster_method
        self.distance_method = distance_method
        self.min_weight = min_weight
        self.max_weight = max_weight

        # 拟合后存储
        self.weights_: Dict[str, float] = {}
        self.clusters_: Optional[np.ndarray] = None
        self.sorted_idx_: Optional[np.ndarray] = None
        self.cov_: Optional[np.ndarray] = None

    def _distance_matrix(self, corr: np.ndarray) -> np.ndarray:
        """相关系数 → 距离矩阵"""
        # D = sqrt(0.5 * (1 - ρ))
        dist = np.sqrt(0.5 * (1 - corr))
        # 确保对角线为 0
        np.fill_diagonal(dist, 0.0)
        return dist

    def _quasi_diagonalize(self, link: np.ndarray) -> np.ndarray:
        """准对角化: 排序资产使大相关的聚在一起

        基于 linkage 矩阵的层次结构对资产进行排序
        """
        # link.shape = (n-1, 4), 每个 row = [cluster1, cluster2, dist, count]
        n = link.shape[0] + 1
        sorted_idx = []

        def _walk(node):
            if node < n:
                # 叶子节点
                sorted_idx.append(int(node))
            else:
                # 内部节点: 先左后右或先右后左 (选距离近的先)
                left = int(link[node - n, 0])
                right = int(link[node - n, 1])
                # 先递归距离较近的
                if link[node - n, 2] < link[node - n - 1, 2] if node > n else True:
                    _walk(left)
                    _walk(right)
                else:
                    _walk(right)
                    _walk(left)

        # 从根节点开始 (最后一个聚类)
        _walk(n + len(link) - 2) if len(link) > 0 else None

        # 如果 walk 未覆盖全部, 从根递归最后一次合并
        if len(sorted_idx) < n:
            sorted_idx = list(range(n))

        return np.array(sorted_idx)

    def _recursive_bisection(self, cov: np.ndarray, sorted_idx: np.ndarray) -> List[float]:
        """递归平分: 自顶向下分配权重

        每一层: 将当前组分为两半, 按逆方差比分配权重
        """
        n = len(sorted_idx)
        weights = np.ones(n) / n  # 初始等权

        # 构建聚类树
        clusters = [list(range(n))]  # 初始一个组包含全部

        while clusters:
            new_clusters = []
            for cluster in clusters:
                if len(cluster) == 1:
                    # 单资产, 不再分
                    continue
                elif len(cluster) == 2:
                    # 两个资产: 按逆方差分配
                    i, j = cluster
                    ivar_i = 1.0 / max(cov[sorted_idx[i], sorted_idx[i]], 1e-8)
                    ivar_j = 1.0 / max(cov[sorted_idx[j], sorted_idx[j]], 1e-8)
                    w_i = ivar_i / (ivar_i + ivar_j + 1e-8)
                    w_j = ivar_j / (ivar_i + ivar_j + 1e-8)
                    weights[i] *= w_i * 2
                    weights[j] *= w_j * 2
                    continue

                # 将 cluster 分成两半
                mid = len(cluster) // 2
                left = cluster[:mid]
                right = cluster[mid:]

                # 左半方差
                cov_left = cov[sorted_idx[left]][:, sorted_idx[left]]
                var_left = np.trace(cov_left)

                # 右半方差
                cov_right = cov[sorted_idx[right]][:, sorted_idx[right]]
                var_right = np.trace(cov_right)

                # 逆方差比
                alpha_left = 1.0 / max(var_left, 1e-8) if var_left > 0 else 0.5
                alpha_right = 1.0 / max(var_right, 1e-8) if var_right > 0 else 0.5
                total_alpha = alpha_left + alpha_right

                if total_alpha > 0:
                    w_left = alpha_left / total_alpha
                    w_right = alpha_right / total_alpha
                else:
                    w_left = 0.5
                    w_right = 0.5

                # 更新权重
                for idx in left:
                    weights[idx] *= w_left * (len(cluster) / len(left))
                for idx in right:
                    weights[idx] *= w_right * (len(cluster) / len(right))

                # 继续递归
                if len(left) > 1:
                    new_clusters.append(left)
                if len(right) > 1:
                    new_clusters.append(right)

            clusters = new_clusters

        # 归一化
        weights = weights / weights.sum()
        return weights.tolist()

    def fit(self, returns: pd.DataFrame) -> Dict[str, float]:
        """拟合 HRP 权重

        Args:
            returns: DataFrame, index=日期, columns=标的代码

        Returns:
            {symbol: weight}
        """
        if returns.empty or returns.shape[1] < 2:
            logger.warning("HRP: 数据不足 (需要至少2个标的)")
            return {}

        symbols = list(returns.columns)

        # 1. 计算相关系数矩阵
        corr = returns.corr().values
        # 处理 NaN
        corr = np.nan_to_num(corr, nan=0.0)

        # 2. 距离矩阵
        dist = self._distance_matrix(corr)

        # 3. 层次聚类
        # squareform: 扁平距离 → 方阵
        try:
            condensed_dist = squareform(dist, checks=False)
            link = linkage(condensed_dist, method=self.cluster_method)
        except Exception as e:
            logger.warning(f"HRP 聚类失败: {e}, 回退到朴素 RP")
            return self._fallback_rp(returns)

        # 4. 准对角化
        sorted_idx = self._quasi_diagonalize(link)

        # 5. 协方差矩阵 (用 shrunk 估计)
        cov = returns.cov().values
        # Ledoit-Wolf 收缩
        cov = self._ledoit_wolf_shrink(cov, returns)

        # 6. 递归平分
        raw_weights = self._recursive_bisection(cov, sorted_idx)

        # 7. 映射回原始符号
        weights_map = {}
        for i, idx in enumerate(sorted_idx):
            if idx < len(symbols):
                sym = symbols[idx]
                w = raw_weights[i] if i < len(raw_weights) else 1.0 / len(symbols)
                weights_map[sym] = w

        # 8. 约束: min/max weight
        weights_map = self._apply_constraints(weights_map)

        # 9. 重归一化
        total_w = sum(weights_map.values())
        if total_w > 0:
            weights_map = {k: v / total_w for k, v in weights_map.items()}

        self.weights_ = weights_map
        self.clusters_ = link
        self.sorted_idx_ = sorted_idx
        self.cov_ = cov

        logger.info(
            f"HRP 拟合完成: {len(weights_map)} 标的, top3: {sorted(weights_map.items(), key=lambda x: -x[1])[:3]}"
        )

        return weights_map

    def _ledoit_wolf_shrink(self, sample_cov: np.ndarray, returns: pd.DataFrame, shrinkage: float = 0.3) -> np.ndarray:
        """Ledoit-Wolf 风格收缩估计 (简化版)"""
        n = sample_cov.shape[0]

        # 收缩目标: 对角线为各自方差均值, 非对角线为 0
        avg_var = np.mean(np.diag(sample_cov))
        target = np.eye(n) * avg_var

        # 如果 shrink_factor = 0, 不做收缩
        # 使用固定 shrink 或基于样本量调整
        # shrink = T / (T + N) 风格
        T = len(returns)
        if T > 0:
            shrinkage = min(0.5, n / T)

        shrunk = (1 - shrinkage) * sample_cov + shrinkage * target

        # 确保正定性
        try:
            min_eig = np.linalg.eigvalsh(shrunk).min()
            if min_eig <= 0:
                shrunk += np.eye(n) * (abs(min_eig) + 1e-6)
        except np.linalg.LinAlgError:
            pass

        return shrunk

    def _apply_constraints(self, weights: Dict[str, float]) -> Dict[str, float]:
        """应用 min/max 约束"""
        # 先 clip
        clipped = {k: np.clip(v, self.min_weight, self.max_weight) for k, v in weights.items()}
        return clipped

    def _fallback_rp(self, returns: pd.DataFrame) -> Dict[str, float]:
        """朴素 Risk Parity 回退"""
        symbols = list(returns.columns)
        n = len(symbols)
        vars_ = returns.var().values
        if len(vars_) == 0 or vars_.sum() == 0:
            return {s: 1.0 / n for s in symbols}

        ivar = 1.0 / np.maximum(vars_, 1e-8)
        weights_arr = ivar / ivar.sum()
        return {s: float(w) for s, w in zip(symbols, weights_arr)}

    # ---------- 诊断 ----------
    def portfolio_risk(self, returns: pd.DataFrame) -> Dict:
        """计算 HRP 组合的风险指标"""
        if not self.weights_:
            return {}

        symbols = list(self.weights_.keys())
        w = np.array([self.weights_.get(s, 0) for s in symbols])
        w = w / w.sum()

        cov = returns[symbols].cov().values
        port_var = w @ cov @ w
        port_vol = np.sqrt(max(port_var, 0))

        # 风险贡献 (Euler decomposition)
        mrc = cov @ w  # Marginal Risk Contribution
        rc = w * mrc  # Risk Contribution
        rc_pct = rc / max(rc.sum(), 1e-8)

        return {
            "portfolio_vol": round(float(port_vol), 6),
            "annual_vol": round(float(port_vol * np.sqrt(252)), 4),
            "risk_contributions": {s: round(float(pct), 4) for s, pct in zip(symbols, rc_pct)},
            "risk_concentration_hhi": round(float(np.sum(rc_pct**2)), 4),
            "weight_concentration_hhi": round(float(np.sum(w**2)), 4),
        }

    def plot_dendrogram(self, title: str = "HRP 聚类树"):
        """绘制聚类树 (需要 matplotlib)"""
        if self.clusters_ is None:
            logger.warning("请先调用 fit()")
            return

        try:
            import matplotlib.pyplot as plt

            fig, ax = plt.subplots(figsize=(12, 6))
            dendrogram(
                self.clusters_,
                labels=list(self.weights_.keys()),
                leaf_rotation=90,
                leaf_font_size=8,
                color_threshold=0.7 * max(self.clusters_[:, 2]),
            )
            ax.set_title(title)
            ax.set_xlabel("标的")
            ax.set_ylabel("距离")
            plt.tight_layout()
            return fig
        except ImportError:
            logger.warning("需要 matplotlib")
            return None
