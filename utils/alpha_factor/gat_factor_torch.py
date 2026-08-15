"""图注意力网络 (GAT) 因子 — Layer 2 动态注意力 (torch 版)

[研究资产 — 不在生产路径] 2026-08-03 Gate2 FAIL 回退后保留为研究资产。
library.py 未 import 本模块, 无生产路径依赖。未来重启条件见 cairn/gnn-supply-chain-factor.md §四。
B1-B5 全套无偏验证后 +0.039 增益被证伪 (实际 +0.0017/+0.0064, 不显著), GAT 未稳健优于静态。

纯 numpy 数值梯度无法有效训练图注意力 (Spearman 损失不可微 → 梯度恒为 0)。
本模块用 torch 实现标准 GAT, 用自动微分 + 可微 MSE 损失监督训练。

设计:
- 多头图注意力层 (GATLayer): alpha_ij = softmax_j( leaky_relu( a^T[W h_i ∥ W h_j] ) )
- 训练目标: 预测未来收益 (回归, MSE 损失, Adam 优化器)
- GAT 因子: f_i = Σ_j alpha_ij * feature_j (注意力聚合邻居特征)

对比:
  Layer 1 静态: f_i = Σ_j strength_ij * feature_j   (手工权重)
  Layer 2 GAT:  f_i = Σ_j alpha_ij * feature_j       (学习权重)

依赖: torch (CPU)
用法:
  from utils.alpha_factor.gat_factor_torch import GATFactorTorch, build_torch_data
  model = GATFactorTorch(n_hidden=16, n_heads=4)
  model.train(features, adj, labels, epochs=300)
  factor = model.compute(features, adj)
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

logger = logging.getLogger(__name__)


class GATLayer(nn.Module):
    """多头图注意力层.

    Args:
        n_features: 节点特征维度
        n_hidden: 隐层维度
        n_heads: 注意力头数
        leaky_alpha: LeakyReLU 负斜率
    """

    def __init__(self, n_features: int, n_hidden: int, n_heads: int = 4, leaky_alpha: float = 0.2):
        super().__init__()
        self.n_hidden = n_hidden
        self.n_heads = n_heads
        self.leaky_alpha = leaky_alpha
        # 多头特征投影 [n_heads, n_hidden, n_features]
        self.W = nn.Parameter(torch.empty(n_heads, n_hidden, n_features))
        # 多头注意力向量 [n_heads, 2*n_hidden]
        self.a = nn.Parameter(torch.empty(n_heads, 2 * n_hidden))
        nn.init.xavier_uniform_(self.W)
        nn.init.xavier_uniform_(self.a.unsqueeze(-1))
        self.a.data = self.a.data.squeeze(-1)

    def forward(self, h: torch.Tensor, adj: torch.Tensor) -> torch.Tensor:
        """前向: 多头注意力聚合.

        Args:
            h: [n, n_features] 节点特征
            adj: [n, n] 邻接矩阵 (边强度或 0/1)

        Returns:
            [n, n_hidden] 多头拼接的节点表示
        """
        n = h.size(0)
        H = self.n_heads
        # 多头投影: self.W 形状 [n_heads, n_hidden, n_features]
        # h: [n, d] (d=feature), W: [f, m, d] (f=head, m=hidden, d=feature)
        # proj: [n, f, m]  ← einsum("nd, fmd -> nfm")
        proj = torch.einsum("nd,fmd->nfm", h, self.W)  # [n, H, nh]
        # 注意力分数 [n, n, H]
        # a_h[:nh]·(W_h h_i) + a_h[nh:]·(W_h h_j)
        left = torch.einsum("nfm,fm->nf", proj, self.a[:, :self.n_hidden])
        right = torch.einsum("nfm,fm->nf", proj, self.a[:, self.n_hidden:])
        score = left[:, None, :] + right[None, :, :]  # [n, n, H]
        score = F.leaky_relu(score, self.leaky_alpha)

        # 掩码 + softmax (over j): 无边邻居分数设 -inf → softmax 后归零
        # 修正 B5: 原实现 score*adj 把分数乘 0, softmax(0)=e^0/Σ 仍非零,
        # 导致注意力泄漏到无边邻居。正确做法是 masked_fill(-inf)。
        mask = (adj == 0).unsqueeze(-1)  # [n, n, 1]
        score = score.masked_fill(mask, float("-inf"))
        alpha = F.softmax(score, dim=1)  # [n, n, H]
        # 孤立节点 (整行全 -inf, 无任何邻居) softmax 产生 NaN, 归零 (该节点不聚合)
        alpha = torch.nan_to_num(alpha, nan=0.0)

        # 聚合邻居表示: out_i = Σ_j alpha_ijh * proj_jhm → [i, h, m]
        out = torch.einsum("ijh,jhm->ihm", alpha, proj)  # [n, H, nh]
        # 多头拼接 → [n, n_heads*n_hidden]
        return out.reshape(n, H * self.n_hidden)


class GATFactorTorch:
    """torch GAT 因子模型 (单层 GAT + 输出投影).

    Args:
        n_hidden: 隐层维度
        n_heads: 注意力头数
        lr: 学习率
        weight_decay: 正则化
    """

    def __init__(self, n_hidden: int = 16, n_heads: int = 4, lr: float = 0.005,
                 weight_decay: float = 1e-4, device: str = "cpu"):
        self.n_hidden = n_hidden
        self.n_heads = n_heads
        self.lr = lr
        self.device = torch.device(device)
        self.n_features = 0
        self.gat: Optional[GATLayer] = None
        self.head: Optional[nn.Linear] = None
        self.optimizer = None

    def _init(self, n_features: int) -> None:
        self.n_features = n_features
        self.gat = GATLayer(n_features, self.n_hidden, self.n_heads).to(self.device)
        # 输出投影: GAT 表示 → 未来收益预测
        self.head = nn.Linear(self.n_heads * self.n_hidden, 1).to(self.device)
        params = list(self.gat.parameters()) + list(self.head.parameters())
        self.optimizer = torch.optim.Adam(params, lr=self.lr, weight_decay=1e-4)

    def _tensors(self, features: np.ndarray, adj: np.ndarray) -> Tuple[torch.Tensor, torch.Tensor]:
        return (
            torch.tensor(features, dtype=torch.float32, device=self.device),
            torch.tensor(adj, dtype=torch.float32, device=self.device),
        )

    def _predict(self, features: np.ndarray, adj: np.ndarray) -> torch.Tensor:
        """GAT 预测: 注意力聚合邻居特征 → 预测未来收益."""
        h, adj_t = self._tensors(features, adj)
        gat_out = self.gat(h, adj_t)  # [n, n_heads*n_hidden]
        return self.head(gat_out).squeeze(-1)  # [n]

    def compute(self, features: np.ndarray, adj: np.ndarray) -> np.ndarray:
        """生成 GAT 因子: 注意力聚合邻居的第一维特征.

        标准 GAT 因子 = 注意力加权聚合邻居动量 (与 Layer 1 静态因子可比).
        """
        if self.gat is None:
            self._init(features.shape[1])
        h, adj_t = self._tensors(features, adj)
        with torch.no_grad():
            alpha = self._attention_alpha(h, adj_t)  # [n, n]
            # 因子 = Σ_j alpha_ij * feature_j (邻居动量)
            factor = (alpha * h[:, 0]).sum(dim=1)
        return factor.cpu().numpy()

    def _attention_alpha(self, h: torch.Tensor, adj: torch.Tensor) -> torch.Tensor:
        """计算注意力系数 alpha [n,n] (多头均值)."""
        H = self.n_heads
        proj = torch.einsum("nd,fmd->nfm", h, self.gat.W)  # [n,H,nh]
        left = torch.einsum("nfm,fm->nf", proj, self.gat.a[:, :self.n_hidden])
        right = torch.einsum("nfm,fm->nf", proj, self.gat.a[:, self.n_hidden:])
        score = F.leaky_relu(left[:, None, :] + right[None, :, :], self.gat.leaky_alpha)
        # 修正 B5: masked_fill(-inf) 替代乘 0, 避免无边邻居获得非零注意力
        mask = (adj == 0).unsqueeze(-1)
        score = score.masked_fill(mask, float("-inf"))
        alpha = F.softmax(score, dim=1)
        alpha = torch.nan_to_num(alpha, nan=0.0)
        return alpha.mean(dim=2)

    def train(self, features: np.ndarray, adj: np.ndarray, labels: np.ndarray,
              epochs: int = 300, verbose: bool = False) -> List[float]:
        """监督训练 GAT (MSE 损失, Adam 优化器)."""
        if self.gat is None:
            self._init(features.shape[1])
        h, adj_t = self._tensors(features, adj)
        labels_t = torch.tensor(labels, dtype=torch.float32, device=self.device)

        losses = []
        self.gat.train()
        self.head.train()
        for ep in range(epochs):
            self.optimizer.zero_grad()
            pred = self._predict(features, adj)
            valid = ~torch.isnan(labels_t)
            loss = F.mse_loss(pred[valid], labels_t[valid])
            loss.backward()
            self.optimizer.step()
            losses.append(loss.item())
            if verbose and (ep + 1) % 50 == 0:
                logger.info(f"  epoch {ep+1}: loss={loss.item():.6f}")
        return losses


def build_adjacency(graph, symbols: List[str], weight_key: str = "strength") -> Tuple[np.ndarray, List[str]]:
    """从 SupplyChainGraph 构建邻接矩阵 (无向)."""
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
            adj[idx[e.target], idx[src]] = w
    return adj, symbols
