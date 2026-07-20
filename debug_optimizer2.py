# -*- coding: utf-8 -*-
"""快速验证优化器信号倾斜 - 带 debug"""
import numpy as np
import pandas as pd
from utils.institutional_optimizer import InstitutionalPortfolioOptimizer

optimizer = InstitutionalPortfolioOptimizer(total_capital=3_000_000, max_weight=0.40)
symbols = ["600519", "000858", "601318"]
mu = np.array([0.711, -0.533, 0.513]) * 0.10
cov = pd.DataFrame(np.diag(np.full(len(symbols), 0.04 / 252)), index=symbols, columns=symbols)

# 手动计算中间步骤
vols = np.sqrt(np.diag(cov))
inv_vol = 1.0 / vols
base_weights = inv_vol / inv_vol.sum()
print("vols:", vols.tolist())
print("inv_vol:", inv_vol.tolist())
print("base_weights:", base_weights.tolist())

mu_adj = np.where(mu > 0.0, mu, 0.0)
print("mu_adj:", mu_adj.tolist())
if mu_adj.sum() > 1e-12:
    signal_weights = mu_adj / mu_adj.sum()
    print("signal_weights:", signal_weights.tolist())
    weights = 0.7 * base_weights + 0.3 * signal_weights
    print("mixed weights (before max_weight):", weights.tolist())
    
    # 模拟 _apply_constraints
    weights = np.minimum(weights, 0.40)
    weights = np.maximum(weights, 0.0)
    total = weights.sum()
    print("after min/max, total=", total)
    if total > 1.0:
        weights = weights / total
    print("after constraints:", weights.tolist())

decision = optimizer.optimize(
    expected_returns=dict(zip(symbols, mu)),
    covariance_matrix=cov,
    current_positions={},
    impact_model=None,
)
print("expected_returns:", dict(zip(symbols, mu.tolist())))
print("target_weights:", decision.target_weights)
print("expected_return:", decision.expected_return)
print("expected_risk:", decision.expected_risk)
