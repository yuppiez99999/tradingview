"""W6.4.3 StatisticalArbitrageEngine 配对交易 Walk-Forward 验证子包。

借鉴 StatisticalArbitrageEngine 的 Walk-Forward 样本外验证流程,
在已有 PairsTrading 类之上增加季度重筛选 + OOS 性能评估。

子模块:
    - pairs_trading: Walk-Forward 配对交易验证器
"""
from utils.strategy.arbitrage.pairs_trading import (
    WFValidationReport,
    WFWindowResult,
    WalkForwardPairsValidator,
)

__all__ = [
    "WFValidationReport",
    "WFWindowResult",
    "WalkForwardPairsValidator",
]
