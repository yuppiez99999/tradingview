"""因子 Transformer 编码 (Wave 6 W6.1.4, EigenAlpha 风格 FactorEncoder 简化 POC)

本模块是 EigenAlpha/src/eigenalpha/modules/factor_encoder.py 的轻量移植 POC
(优先级 Low, 仅做架构预留 + 影子模式验证):

目标: 把截面多因子矩阵 X ∈ R^{N_stocks × F_factors} 编码为深度表征 Z ∈ R^{N_stocks × D}
      用于后续选股/排序/风险因子分解, 作为 GAT/GNN 之外的可选基础表征。

实现双模式:
    1. torch 可用时 → 真实可训练 FactorTransformerEncoder (含线性投影 + 多头自注意力 + FFN + LN)
    2. torch 不可用时 → numpy 影子模式 (参数固定随机初始化, 仅用于验证数据流程与调用栈)

**当前不参与训练/推理闭环**，只作为 Wave 6 W6.1.4 架构预研 POC:
    - 验证输入输出维度匹配 (117 因子 → 64 维 embedding)
    - 验证单截面/批处理调用不报错
    - 为后续接入 LightGBM 增强训练或 GNN 邻居编码提供统一 embedding 入口
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

logger = logging.getLogger("alpha_factor.transformer")

torch: Optional[type]
nn: Optional[type]
_TORCH_AVAILABLE = False
try:  # pragma: no cover - 依赖环境差异
    import torch as _torch_impl
    import torch.nn as _torch_nn_impl

    torch = _torch_impl
    nn = _torch_nn_impl
    _TORCH_AVAILABLE = True
except (ImportError, OSError, AttributeError):  # pragma: no cover - torch 不装/DLL 加载失败/部分初始化也不影响主流程
    torch = None
    nn = None


# ============================================================
# 公共数据结构
# ============================================================


@dataclass
class FactorEncodingResult:
    """因子编码输出 (与 torch / numpy 模式解耦)"""
    # Z ∈ R^{N × D} — 每只股票的 embedding (行对应 stocks 顺序)
    embeddings: np.ndarray
    # 股票顺序列表 (与 embeddings 行号对齐)
    stocks: list[str]
    # 平均注意力权重矩阵 (None = numpy 影子模式未计算)
    attn_weights: Optional[np.ndarray] = None
    # 使用的后端 ("torch" / "numpy")
    backend: str = "numpy"
    # 调试信息 (输入维度/层数等)
    meta: dict[str, Any] = field(default_factory=dict)


# ============================================================
# 1. NumPy 影子模式 (默认可用, 无需 torch 依赖)
# ============================================================


class NumpyFactorEncoder:
    """numpy 版因子编码骨架 (参数固定随机, 仅验证数据流动, 非可训练)

    等价结构:
        Linear(F → D)     投影
        + LayerNorm
        + MultiHeadSelfAttn(影子近似: 对 Z 做行归一化的相关性缩放)
        + FFN(D → 4D → D) (影子近似: tanh + 线性投影)
        + LayerNorm
    """

    def __init__(self, n_factors: int, d_model: int = 64, n_heads: int = 4,
                 n_layers: int = 2, dropout: float = 0.1, seed: int = 42):
        self.n_factors = int(n_factors)
        self.d_model = int(d_model)
        self.n_heads = int(n_heads)
        self.n_layers = int(n_layers)
        self.dropout = float(dropout)
        rng = np.random.default_rng(seed)
        # 影子参数: 固定随机初始化, 不训练, 仅保证维度正确 + 前向传播可运行
        scale = np.sqrt(2.0 / max(1, self.n_factors + self.d_model))
        self.W_proj = rng.standard_normal((self.n_factors, self.d_model)) * scale
        self.b_proj = np.zeros((self.d_model,), dtype=np.float64)
        self.W_qkv = rng.standard_normal((self.d_model, self.d_model * 3)) * 0.1
        self.W_out = rng.standard_normal((self.d_model, self.d_model)) * 0.1
        self.W_ff1 = rng.standard_normal((self.d_model, self.d_model * 4)) * 0.1
        self.b_ff1 = np.zeros((self.d_model * 4,), dtype=np.float64)
        self.W_ff2 = rng.standard_normal((self.d_model * 4, self.d_model)) * 0.1
        self.b_ff2 = np.zeros((self.d_model,), dtype=np.float64)

    # ------------------------------------------------------------
    @staticmethod
    def _layernorm(x: np.ndarray, eps: float = 1e-5) -> np.ndarray:
        mu = x.mean(axis=-1, keepdims=True)
        sd = x.var(axis=-1, keepdims=True) + eps
        return (x - mu) / np.sqrt(sd)

    @staticmethod
    def _gelu(x: np.ndarray) -> np.ndarray:
        # 近似 GELU: 0.5 * x * (1 + tanh(sqrt(2/π) * (x + 0.044715 * x^3)))
        c = np.sqrt(2.0 / np.pi)
        return 0.5 * x * (1.0 + np.tanh(c * (x + 0.044715 * (x ** 3))))

    # ------------------------------------------------------------
    def _self_attn_shadow(self, Z: np.ndarray) -> tuple[np.ndarray, Optional[np.ndarray]]:
        """N×D 影子自注意力: QKV 近似 + softmax + 残差"""
        qkv = Z @ self.W_qkv  # [N, 3D]
        D = self.d_model
        Q, K, V = qkv[:, :D], qkv[:, D: 2 * D], qkv[:, 2 * D:]
        # 单头 (影子模式不拆头, 保持可运行)
        dk = max(1, int(np.sqrt(D)))
        scores = Q @ K.T / dk  # [N, N]
        # 数值稳定 softmax
        s_max = scores.max(axis=-1, keepdims=True)
        exp_s = np.exp(scores - s_max)
        attn = exp_s / (exp_s.sum(axis=-1, keepdims=True) + 1e-9)
        out = (attn @ V) @ self.W_out
        return out, attn

    # ------------------------------------------------------------
    def encode(self, factor_matrix: np.ndarray) -> tuple[np.ndarray, Optional[np.ndarray]]:
        """前向编码: X ∈ R^{N×F} → Z ∈ R^{N×D}"""
        N, F = factor_matrix.shape
        if self.n_factors != F:
            raise ValueError(
                f"输入因子维度 {F} 与编码器 n_factors={self.n_factors} 不匹配"
            )
        X = np.nan_to_num(factor_matrix, nan=0.0, posinf=3.0, neginf=-3.0).astype(np.float64)

        # 投影
        Z = self._layernorm(X @ self.W_proj + self.b_proj)
        last_attn = None
        for _ in range(self.n_layers):
            # SubLayer 1: SelfAttn + 残差 + LN
            attn_out, attn = self._self_attn_shadow(Z)
            Z = self._layernorm(Z + attn_out)
            last_attn = attn
            # SubLayer 2: FFN + 残差 + LN
            hidden = self._gelu(Z @ self.W_ff1 + self.b_ff1)
            ffn_out = hidden @ self.W_ff2 + self.b_ff2
            Z = self._layernorm(Z + ffn_out)
        return Z, last_attn


# ============================================================
# 2. PyTorch 模式 (torch 可用时自动启用; 真实可训练)
# ============================================================


if _TORCH_AVAILABLE:  # pragma: no cover - 仅当环境装了 torch 才走这条分支

    class _TorchFactorEncoder(nn.Module):
        """真实可训练 FactorTransformerEncoder

        结构: FactorProjection → TransformerEncoder × n_layers → FinalLayerNorm
        - 与 EigenAlpha FactorEncoder 基本一致, 但去掉 PositionEncode (截面因子无时序位置)
        """

        def __init__(self, n_factors: int, d_model: int = 64, n_heads: int = 4,
                     n_layers: int = 2, dropout: float = 0.1):
            super().__init__()
            self.n_factors = int(n_factors)
            self.d_model = int(d_model)
            self.proj = nn.Linear(self.n_factors, d_model)
            enc_layer = nn.TransformerEncoderLayer(
                d_model=d_model, nhead=n_heads,
                dim_feedforward=d_model * 4, dropout=dropout,
                batch_first=True,
            )
            self.encoder = nn.TransformerEncoder(enc_layer, num_layers=n_layers)
            self.ln = nn.LayerNorm(d_model)

        def forward(self, X: torch.Tensor) -> torch.Tensor:
            # X: [N, F] → 当作 batch_size=1, seq_len=N 的 Transformer 输入
            Z0 = self.proj(X)  # [N, D]
            Z = self.encoder(Z0.unsqueeze(0)).squeeze(0)  # [N, D]
            return self.ln(Z)


# ============================================================
# 3. 对外统一工厂 / API
# ============================================================


def build_factor_encoder(
    n_factors: int,
    d_model: int = 64,
    n_heads: int = 4,
    n_layers: int = 2,
    dropout: float = 0.1,
    force_backend: Optional[str] = None,
    seed: int = 42,
):
    """构建因子编码器 (可选后端, 优先 torch, 降级 numpy 影子)

    Args:
        n_factors: 输入因子维度 (本项目 AlphaFactorLibrary 通常为 90~120)
        d_model: embedding 维度
        n_heads: 多头数 (仅 torch 有效; numpy 模式按单头近似)
        n_layers: Transformer 层数
        dropout: dropout
        force_backend: 强制 "torch" 或 "numpy" (用于调试/一致性)
        seed: numpy 影子模式随机种子
    """
    use_torch = _TORCH_AVAILABLE
    if force_backend == "torch":
        if not _TORCH_AVAILABLE:
            raise RuntimeError("force_backend=torch 但未安装 torch 依赖")
    elif force_backend == "numpy":
        use_torch = False

    if use_torch:  # pragma: no cover
        enc = _TorchFactorEncoder(n_factors, d_model, n_heads, n_layers, dropout)
        enc.eval()
        return enc
    return NumpyFactorEncoder(n_factors, d_model, n_heads, n_layers, dropout, seed=seed)


def factors_to_matrix(
    factors: dict[str, Any],
    stocks: Optional[list[str]] = None,
) -> tuple[np.ndarray, list[str]]:
    """把 AlphaFactorLibrary 输出的 factors 字典 → 截面矩阵 X ∈ R^{N×F}

    行 = 股票 (按 stocks 顺序; 未指定时自动取所有 symbol 的交集排序)
    列 = 因子 (按因子名字典序, 保证调用稳定)

    Args:
        factors: {factor_name: FactorValue | dict[symbol, value]}
        stocks:  指定顺序的 symbol 列表 (None 表示自动推断)

    Returns:
        (X_matrix, stocks_ordered)
    """
    factor_names = sorted(factors.keys())
    # 推断股票全集
    all_symbols: set[str] = set()
    per_factor_syms: dict[str, set[str]] = {}
    for fname in factor_names:
        container = factors[fname]
        if container is None:
            per_factor_syms[fname] = set()
            continue
        if hasattr(container, "values") and isinstance(container.values, dict):
            sym_map = container.values
        elif isinstance(container, dict):
            sym_map = container
        else:
            try:
                sym_map = dict(container)
            except (TypeError, ValueError):
                per_factor_syms[fname] = set()
                continue
        s_set = {str(s) for s, v in sym_map.items()
                 if v is not None and isinstance(v, (int, float)) and np.isfinite(v)}
        per_factor_syms[fname] = s_set
        if stocks is None:
            all_symbols.update(s_set)
    if stocks is None:
        stocks_list = sorted(all_symbols)
    else:
        stocks_list = [str(s) for s in stocks]

    N = len(stocks_list)
    F = len(factor_names)
    X = np.zeros((N, F), dtype=np.float64)
    for j, fname in enumerate(factor_names):
        container = factors.get(fname)
        if container is None:
            continue
        if hasattr(container, "values") and isinstance(container.values, dict):
            sym_map = container.values
        elif isinstance(container, dict):
            sym_map = container
        else:
            try:
                sym_map = dict(container)
            except (TypeError, ValueError):
                sym_map = {}
        for i, s in enumerate(stocks_list):
            v = sym_map.get(s)
            if v is None:
                continue
            try:
                vf = float(v)
                if np.isfinite(vf):
                    X[i, j] = vf
            except (TypeError, ValueError):
                continue
    return X, stocks_list


def encode_factor_frame(
    factors: dict[str, Any],
    stocks: Optional[list[str]] = None,
    *,
    encoder=None,
    d_model: int = 64,
    n_heads: int = 4,
    n_layers: int = 2,
    force_backend: Optional[str] = None,
    seed: int = 42,
) -> FactorEncodingResult:
    """对 AlphaFactorLibrary 的输出做一次性端到端编码 (POC 主入口)

    Args:
        factors: library_result.factors
        stocks:  指定股票顺序 (None = 自动推断)
        encoder: 预构建的编码器 (None 时自动调用 build_factor_encoder)
        d_model / n_heads / n_layers / force_backend / seed: 仅在 encoder=None 时生效

    Returns:
        FactorEncodingResult
    """
    X, stocks_ord = factors_to_matrix(factors, stocks)
    N, F = X.shape
    if encoder is None:
        encoder = build_factor_encoder(
            n_factors=F, d_model=d_model, n_heads=n_heads,
            n_layers=n_layers, force_backend=force_backend, seed=seed,
        )

    # 输入若为空矩阵 (无股票或无因子) → 空输出
    if N == 0 or F == 0:
        return FactorEncodingResult(
            embeddings=np.zeros((N, d_model), dtype=np.float64),
            stocks=stocks_ord,
            attn_weights=None,
            backend="numpy",
            meta={"n_factors": F, "d_model": d_model, "warning": "empty_input"},
        )

    attn: Optional[np.ndarray] = None
    backend = "numpy"
    emb: np.ndarray

    if isinstance(encoder, NumpyFactorEncoder):
        if encoder.n_factors != F:
            # 重新构建匹配维度的 numpy encoder (保持 seed 一致)
            encoder = NumpyFactorEncoder(
                n_factors=F, d_model=encoder.d_model,
                n_heads=encoder.n_heads, n_layers=encoder.n_layers,
                dropout=encoder.dropout, seed=seed,
            )
        emb, attn = encoder.encode(X)
    else:  # torch 编码器
        if not _TORCH_AVAILABLE:  # pragma: no cover
            raise RuntimeError("torch 编码器对象传入, 但 torch 模块不可用")
        backend = "torch"
        with torch.no_grad():
            Xt = torch.from_numpy(X.astype(np.float32))
            Zt = encoder(Xt)  # [N, D]
            emb = Zt.detach().cpu().numpy()
            attn = None  # PyTorch TransformerEncoder 默认不返回 attn, 如需另行启用

    meta = {
        "n_factors": F,
        "d_model": int(emb.shape[1]) if emb.ndim == 2 else 0,
        "n_stocks": N,
        "n_layers": getattr(encoder, "n_layers", None),
    }
    return FactorEncodingResult(
        embeddings=emb,
        stocks=stocks_ord,
        attn_weights=attn,
        backend=backend,
        meta=meta,
    )
