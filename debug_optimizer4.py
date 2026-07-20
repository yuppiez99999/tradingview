# -*- coding: utf-8 -*-
"""快速验证优化器信号倾斜 - 直接调用内部函数"""
import numpy as np
import pandas as pd
from utils.institutional_optimizer import InstitutionalPortfolioOptimizer

optimizer = InstitutionalPortfolioOptimizer(total_capital=3_000_000, max_weight=0.40)
symbols = ["600519", "000858", "601318"]
mu = np.array([0.711, -0.533, 0.513]) * 0.10
cov = pd.DataFrame(np.diag(np.full(len(symbols), 0.04 / 252)), index=symbols, columns=symbols)

print("cov type:", type(cov))
print("cov values type:", type(cov.values))
print("cov:\n", cov)
print("mu:", mu)

# 直接调用内部函数
raw_weights = optimizer._risk_parity_with_signal(mu, cov)
print("raw_weights from _risk_parity_with_signal:", raw_weights.tolist())

# 手动模拟
vols = np.sqrt(np.diag(cov))
inv_vol = 1.0 / vols
base_weights = inv_vol / inv_vol.sum()
print("vols:", vols.tolist())
print("base_weights:", base_weights.tolist())

mu_adj = np.where(mu > 0.0, mu, 0.0)
print("mu_adj:", mu_adj.tolist())
signal_weights = mu_adj / mu_adj.sum()
print("signal_weights:", signal_weights.tolist())
mixed = 0.7 * base_weights + 0.3 * signal_weights
print("manual mixed:", mixed.tolist())
