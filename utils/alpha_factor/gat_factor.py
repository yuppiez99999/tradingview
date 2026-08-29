"""图注意力网络 (GAT) 因子 — Layer 2 动态注意力

[研究资产 — 不在生产路径] 2026-08-03 Gate2 FAIL 回退后保留为研究资产。
library.py 未 import 本模块, 无生产路径依赖。未来重启条件见 cairn/gnn-supply-chain-factor.md §四。
纯 numpy 版数值梯度失效 (Spearman 不可微), 已由 torch 版 (gat_factor_torch.py) 替代。

Layer 1 用静态边权重 (strength) 聚合邻居信息, 跨窗稳定性不足。
本模块引入「可学习的图注意力」: 用未来收益监督训练注意力参数,
让边权重从数据学习, 突破静态权重的稳定性极限。

设计 (纯 numpy, 不依赖 torch):
- 图注意力: alpha_ij = softmax_j( leaky_relu( a^T[W h_i ∥ W h_j] ) )
  其中 h_i 是节点特征 (动量/基本面), a/W 是可学习参数
- 训练目标: 最大化 GAT 因子对(未来)收益的排序相关性 (Spearman 可微近似)
- GAT 因子: f_i = Σ_j alpha_ij * feature_j (注意力聚合邻居特征)

对比:
  Layer 1 静态因子: f_i = Σ_j strength_ij * feature_j  (手工权重)
  Layer 2 GAT 因子: f_i = Σ_j alpha_ij * feature_j      (学习权重)

用法:
  from utils.alpha_factor.gat_factor import GATFactor, train_gat
  gat = GATFactor(n_hidden=16)
  gat.train(features, adj_matrix, labels)   # 监督训练注意力
  factor_values = gat.compute(features, adj_matrix)
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class GATFactor:
    """轻量图注意力因子 (单层, 纯 numpy).

    Args:
        n_hidden: 节点特征隐层维度
        n_heads: 注意力头数 (多头均值)
        leaky_alpha: LeakyReLU 负斜率
    """

    def __init__(self, n_hidden: int = 16, n_heads: int = 2, leaky_alpha: float = 0.2):
        self.n_hidden = n_hidden
        self.n_heads = n_heads
        self.leaky_alpha = leaky_alpha
        self.W: Optional[np.ndarray] = None  # 特征投影 [heads, n_hidden, n_features]
        self.a: Optional[np.ndarray] = None  # 注意力向量 [heads, 2*n_hidden]
        self.n_features = 0

    def _init_params(self, n_features: int) -> None:
        rng = np.random.default_rng(42)
        self.n_features = n_features
        self.W = rng.normal(0, 0.05, size=(self.n_heads, self.n_hidden, n_features))
        self.a = rng.normal(0, 0.05, size=(self.n_heads, 2 * self.n_hidden))

    # ----------------------------------------------------------
    def _attention(self, features: np.ndarray, adj: np.ndarray) -> np.ndarray:
        """计算注意力系数 alpha [n, n].

        alpha_ij = softmax_j( leaky_relu( Σ_h a_h^T [W_h h_i ∥ W_h h_j] ) )
        """
        features.shape[0]
        H = self.n_heads
        # 多头投影: proj[h] = features @ W[h]^T → [n, H, n_hidden]
        proj = np.stack(
            [features @ self.W[h].T for h in range(H)], axis=1
        )  # [n, H, n_hidden]

        # 注意力分数 [n, n, H]
        # score_ijh = leaky_relu( a_h^T [W_h h_i ∥ W_h h_j] )
        # = a_h[:nh]·(W_h h_i) + a_h[nh:]·(W_h h_j)
        # proj: [n, H, nh], a[:, :nh]: [H, nh] → einsum("nph,ph->np")
        left = np.einsum(
            "nph,ph->np", proj, self.a[:, : self.n_hidden]
        )  # a_h[:nh] · W_h h_i
        right = np.einsum(
            "nph,ph->np", proj, self.a[:, self.n_hidden :]
        )  # a_h[nh:] · W_h h_j
        score = left[:, None, :] + right[None, :, :]  # [n, n, H]
        score = np.where(score > 0, score, self.leaky_alpha * score)  # leaky_relu

        # mask 无边邻居
        score = score * adj[:, :, None]

        # softmax over j (with mask)
        score_max = score.max(axis=1, keepdims=True)
        exp = np.exp(score - score_max)
        exp = exp * adj[:, :, None]
        denom = exp.sum(axis=1, keepdims=True)
        alpha = exp / (denom + 1e-8)

        # 多头均值 [n, n]
        return alpha.mean(axis=2)

    def compute(self, features: np.ndarray, adj: np.ndarray) -> np.ndarray:
        """用当前注意力计算 GAT 因子 (邻居特征加权聚合).

        Args:
            features: [n, n_features] 节点特征 (如动量)
            adj: [n, n] 邻接矩阵 (边强度)

        Returns:
            [n] 因子值 (注意力聚合的邻居特征)
        """
        if self.W is None:
            self._init_params(features.shape[1])
        alpha = self._attention(features, adj)
        # GAT 因子 = Σ_j alpha_ij * feature_j (邻居特征)
        return (alpha * features[:, 0]).sum(axis=1)

    def train(
        self,
        features: np.ndarray,
        adj: np.ndarray,
        labels: np.ndarray,
        epochs: int = 200,
        lr: float = 0.05,
        verbose: bool = False,
    ) -> list[float]:
        """监督训练注意力参数, 最大化因子与未来收益的排序相关.

        Args:
            features: [n, n_features] 节点特征 (含动量等)
            adj: [n, n] 邻接矩阵
            labels: [n] 未来收益 (标签)
            epochs: 训练轮数
            lr: 学习率

        Returns:
            [epochs] 每轮排序相关损失
        """
        features.shape[0]
        if self.W is None:
            self._init_params(features.shape[1])

        # 每轮: 前向 + 近似梯度 (用数值梯度更新注意力参数)
        losses = []
        for ep in range(epochs):
            # 当前因子
            factor = self.compute(features, adj)
            # 排序损失: 因子与 label 的 Spearman 近似 (用 rank 的皮尔逊)
            loss = self._rank_loss(factor, labels)
            losses.append(loss)
            if verbose and (ep + 1) % 50 == 0:
                logger.info(f"  epoch {ep+1}: loss={loss:.4f}")

            # 数值梯度更新 (对 a 和 W)
            grad_a, grad_W = self._numeric_grad(features, adj, labels)
            self.a -= lr * grad_a
            self.W -= lr * grad_W

        return losses

    def _rank_loss(self, factor: np.ndarray, labels: np.ndarray) -> float:
        """排序损失: 因子与标签的 Spearman 相关 (负数, 最小化)."""
        valid = ~np.isnan(factor) & ~np.isnan(labels)
        if valid.sum() < 5:
            return 0.0
        from scipy.stats import spearmanr

        corr, _ = spearmanr(factor[valid], labels[valid])
        if np.isnan(corr):
            return 0.0
        return -float(corr)

    def _numeric_grad(
        self, features: np.ndarray, adj: np.ndarray, labels: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """数值梯度 (有限差分) 用于注意力参数更新."""
        eps = 1e-4
        grad_a = np.zeros_like(self.a)
        grad_W = np.zeros_like(self.W)

        # a 的梯度
        a_flat = self.a.ravel()
        for i in range(a_flat.size):
            orig = a_flat[i]
            a_flat[i] = orig + eps
            l1 = self._rank_loss(self.compute(features, adj), labels)
            a_flat[i] = orig - eps
            l2 = self._rank_loss(self.compute(features, adj), labels)
            a_flat[i] = orig
            grad_a.ravel()[i] = (l1 - l2) / (2 * eps)

        # W 的梯度 (仅对非零 W 优化, 大矩阵数值梯度较慢, 简化只更新首头主方向)
        W_flat = self.W.ravel()
        for i in range(min(W_flat.size, 32)):  # 限制计算量
            orig = W_flat[i]
            W_flat[i] = orig + eps
            l1 = self._rank_loss(self.compute(features, adj), labels)
            W_flat[i] = orig - eps
            l2 = self._rank_loss(self.compute(features, adj), labels)
            W_flat[i] = orig
            grad_W.ravel()[i] = (l1 - l2) / (2 * eps)

        return grad_a, grad_W


def build_adjacency(
    graph, symbols: list[str], weight_key: str = "strength"
) -> tuple[np.ndarray, list[str]]:
    """从 SupplyChainGraph 构建邻接矩阵.

    Args:
        graph: SupplyChainGraph 实例
        symbols: 节点股票列表
        weight_key: 边权重字段 (strength)

    Returns:
        (adj, valid_symbols) — adj [n,n] 边强度, valid_symbols 是图中存在的节点
    """
    idx = {s: i for i, s in enumerate(symbols)}
    n = len(symbols)
    adj = np.zeros((n, n))
    for src, edges in graph.adjacency.items():
        if src not in idx:
            continue
        for e in edges:
            if e.target not in idx:
                continue
            w = float(getattr(e, weight_key, 0) or 0)
            adj[idx[src], idx[e.target]] = w
            adj[idx[e.target], idx[src]] = w  # 无向
    return adj, symbols


def gat_factor_values(
    graph,
    symbols: list[str],
    features: np.ndarray,
    labels: np.ndarray,
    n_hidden: int = 16,
    epochs: int = 150,
    lr: float = 0.05,
) -> tuple[np.ndarray, GATFactor, list[float]]:
    """端到端: 构建邻接矩阵 + 训练 GAT + 生成因子.

    Returns:
        (factor_values, gat_model, losses)
    """
    adj, valid_syms = build_adjacency(graph, symbols)
    gat = GATFactor(n_hidden=n_hidden)
    losses = gat.train(features, adj, labels, epochs=epochs, lr=lr)
    factor = gat.compute(features, adj)
    return factor, gat, losses
