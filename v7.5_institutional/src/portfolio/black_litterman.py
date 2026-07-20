# v7.6 Black-Litterman 预期收益模型 -- Goldman Sachs 标准
# 替换当前 8% 静态预期收益 → 动态先验 + 观点融合
# 核心理念: 市场均衡收益(隐含) + 主观观点 → 后验收益 → 更优权重
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple

import numpy as np

logger = logging.getLogger("v76.portfolio.black_litterman")


@dataclass
class BLConfig:
    risk_aversion: float = 2.50           # 市场风险厌恶系数
    tau: float = 0.05                     # 先验不确定度 (Black-Litterman τ)
    prior_confidence: float = 0.60        # 默认观点置信度


class BlackLittermanEngine:
    """Black-Litterman 预期收益引擎
    
    E(R) = [(τΣ)^-1 + P^T Ω^-1 P]^-1 [(τΣ)^-1 Π + P^T Ω^-1 Q]
    
    Π = λ Σ w_eq  (隐含均衡收益, 从市值权重反推)
    Q = 主观观点向量 (如 "科技超配 2%" )
    Ω = 观点不确定性矩阵
    """
    
    def __init__(self, config: Optional[BLConfig] = None):
        self.cfg = config or BLConfig()
        self._last_posterior: Optional[np.ndarray] = None
        self._last_weights: Optional[np.ndarray] = None

    def compute_implied_returns(self, cov_matrix: np.ndarray,
                                market_weights: np.ndarray) -> np.ndarray:
        """Π = δ Σ w_mkt — 从市场权重反推均衡收益"""
        pi = self.cfg.risk_aversion * cov_matrix @ market_weights
        return pi

    def blend_views(self, prior_returns: np.ndarray,
                    cov_matrix: np.ndarray,
                    views: List[dict]) -> np.ndarray:
        """融合主观观点 → 后验收益"""
        n = len(prior_returns)
        
        if not views:
            return prior_returns

        # 构建 P 矩阵 (pick matrix) 和 Q 向量
        k = len(views)
        P = np.zeros((k, n))
        Q = np.zeros(k)
        omega_raw = np.zeros((k, k))
        
        for i, v in enumerate(views):
            indices = v.get('indices', [])
            values = v.get('values', [])
            for idx, val in zip(indices, values):
                P[i, idx] = val
            Q[i] = v.get('target', 0.0)
            conf = v.get('confidence', self.cfg.prior_confidence)
            omega_raw[i, i] = conf * (1 - conf)

        # Ω = diag(τ * P Σ P^T) * scale + raw 配置
        tau = self.cfg.tau
        var_views = np.diag(P @ cov_matrix @ P.T) * tau
        Omega = np.diag(np.maximum(var_views, 1e-8)) + omega_raw
        
        # 后验 = [(τΣ)^-1 + P^T Ω^-1 P]^-1 [(τΣ)^-1 Π + P^T Ω^-1 Q]
        tau_sigma_inv = np.linalg.inv(tau * cov_matrix)
        omega_inv = np.linalg.inv(Omega)
        
        posterior_cov_inv = tau_sigma_inv + P.T @ omega_inv @ P
        posterior_cov = np.linalg.inv(posterior_cov_inv)
        
        posterior_returns = posterior_cov @ (
            tau_sigma_inv @ prior_returns + P.T @ omega_inv @ Q
        )
        
        self._last_posterior = posterior_returns
        
        logger.info(
            "BL 融合: %d 观点, 先验均值 %.2f%% → 后验均值 %.2f%%",
            k, float(prior_returns.mean()) * 100, float(posterior_returns.mean()) * 100
        )
        
        return posterior_returns
    
    def optimize_weights(self, returns: np.ndarray, cov_matrix: np.ndarray,
                         max_weight: float = 0.15,
                         min_weight: float = 0.0) -> np.ndarray:
        """均值-方差优化 → 最优权重"""
        inv_cov = np.linalg.inv(cov_matrix)
        raw_weights = inv_cov @ returns
        
        # 归一化
        raw_weights = np.maximum(raw_weights, -0.05)  # 不允许大额做空
        raw_weights /= raw_weights.sum()
        
        # 上/下限裁剪
        weights_clipped = np.clip(raw_weights, min_weight, max_weight)
        weights_clipped /= weights_clipped.sum()
        
        self._last_weights = weights_clipped
        return weights_clipped

    def report(self) -> dict:
        if self._last_posterior is None:
            return {'status': '未运行'}
        return {
            'prior_mean': 0.08,
            'posterior_mean': round(float(self._last_posterior.mean()), 4),
            'posterior_range': (
                round(float(self._last_posterior.min()), 4),
                round(float(self._last_posterior.max()), 4),
            ),
        }
