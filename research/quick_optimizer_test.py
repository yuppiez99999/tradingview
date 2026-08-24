"""快速验证优化器信号倾斜"""
import numpy as np
import pandas as pd

from utils.institutional_optimizer import InstitutionalPortfolioOptimizer

optimizer = InstitutionalPortfolioOptimizer(total_capital=3_000_000, max_weight=0.25)
symbols = ["600519", "000858", "601318"]
mu = np.array([0.711, -0.533, 0.513]) * 0.10  # 来自 pipeline_backtest.json 的 alpha_strength
cov = pd.DataFrame(np.diag(np.full(len(symbols), 0.04 / 252)), index=symbols, columns=symbols)

decision = optimizer.optimize(
    expected_returns=dict(zip(symbols, mu, strict=True)),
    covariance_matrix=cov,
    current_positions={},
    impact_model=None,
)
print("expected_returns:", dict(zip(symbols, mu.tolist(), strict=True)))
print("target_weights:", decision.target_weights)
print("expected_return:", decision.expected_return)
print("expected_risk:", decision.expected_risk)
