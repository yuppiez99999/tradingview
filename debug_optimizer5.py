# -*- coding: utf-8 -*-
"""直接复制 _risk_parity_with_signal 代码进行调试"""
import numpy as np
import pandas as pd

symbols = ["600519", "000858", "601318"]
mu = np.array([0.711, -0.533, 0.513]) * 0.10
cov = pd.DataFrame(np.diag(np.full(len(symbols), 0.04 / 252)), index=symbols, columns=symbols)

max_weight = 0.30

n = cov.shape[0]
print("n:", n)
vols = np.sqrt(np.diag(cov))
print("vols:", vols.tolist())
vols = np.where(vols > 1e-12, vols, 1e-12)
print("vols after where:", vols.tolist())
inv_vol = 1.0 / vols
print("inv_vol:", inv_vol.tolist())
base_weights = inv_vol / inv_vol.sum()
print("base_weights:", base_weights.tolist())

mu = np.array(mu, dtype=float).reshape(-1)
print("mu after reshape:", mu.tolist())
if mu.shape[0] != n:
    mu = np.zeros(n, dtype=float)
    print("mu adjusted to zeros:", mu.tolist())

mu_adj = np.where(mu > 0.0, mu, 0.0)
print("mu_adj:", mu_adj.tolist())
if mu_adj.sum() > 1e-12:
    signal_weights = mu_adj / mu_adj.sum()
    print("signal_weights:", signal_weights.tolist())
    weights = 0.7 * base_weights + 0.3 * signal_weights
    print("mixed weights:", weights.tolist())
else:
    weights = base_weights.copy()
    print("no signal, base_weights:", weights.tolist())

print("\n--- max_weight iteration ---")
for i in range(5):
    excess = weights > max_weight
    print(f"iter {i}: excess={excess.tolist()}, weights={weights.tolist()}")
    if not np.any(excess):
        print("no excess, break")
        break
    weights = np.minimum(weights, max_weight)
    remaining = 1.0 - weights.sum()
    active = ~excess
    print(f"  after min: weights={weights.tolist()}, remaining={remaining}, active={active.tolist()}")
    if remaining > 1e-12 and np.any(active):
        weights[active] = weights[active] / weights[active].sum() * remaining
        print(f"  after redistribute: weights={weights.tolist()}")
    else:
        print("  no remaining or no active, break")
        break

print("\nfinal weights:", weights.tolist())
