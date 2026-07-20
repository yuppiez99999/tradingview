# -*- coding: utf-8 -*-
from utils.institutional_optimizer import InstitutionalPortfolioOptimizer
import numpy as np
import pandas as pd

optimizer = InstitutionalPortfolioOptimizer(total_capital=3_000_000, max_weight=0.20)
cov = pd.DataFrame(np.diag(np.full(3, 0.04 / 252)), index=['600519', '000858', '601318'], columns=['600519', '000858', '601318'])
decision = optimizer.optimize(
    expected_returns={'600519': 0.05, '000858': 0.05, '601318': 0.05},
    covariance_matrix=cov,
    current_positions={},
    impact_model=None,
)
print(decision.to_dict())
