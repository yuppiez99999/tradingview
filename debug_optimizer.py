# -*- coding: utf-8 -*-
"""快速验证：optimizer 是否对月度信号变化做出权重响应"""
import numpy as np
from utils.institutional_optimizer import InstitutionalPortfolioOptimizer

optimizer = InstitutionalPortfolioOptimizer(max_weight=0.25, min_position_weight=0.05)
symbols = ["600519", "000858", "601318", "000001", "600036", "601398", "600276", "000063"]

# 模拟 3 个月的信号变化
scenarios = [
    {"2024-01": {"600519": 0.20, "000858": 0.15, "601318": 0.10, "000001": -0.05, "600036": -0.02, "601398": 0.08, "600276": 0.12, "000063": 0.03}},
    {"2024-02": {"600519": -0.10, "000858": 0.25, "601318": 0.05, "000001": 0.18, "600036": 0.22, "601398": 0.15, "600276": -0.08, "000063": 0.12}},
    {"2024-03": {"600519": 0.05, "000858": 0.30, "601318": 0.12, "000001": 0.25, "600036": 0.08, "601398": 0.20, "600276": 0.15, "000063": 0.18}},
]

for scenario in scenarios:
    for date, signals in scenario.items():
        mu = np.array([signals.get(s, 0.0) for s in symbols], dtype=float)
        cov = np.diag(np.full(len(symbols), 0.04 / 252))
        weights = optimizer._risk_parity_with_signal(mu, cov)
        print(f"{date}: weights={dict(zip(symbols, [round(w, 4) for w in weights]))}")
        print(f"        sum={weights.sum():.4f}, max={weights.max():.4f}, active={int((weights > 1e-12).sum())}")
