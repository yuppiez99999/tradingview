"""统计套利模块 — Co-integration + Pairs Trading (零第三方依赖)

与 utils/strategy_lib/pairs_trading.py 互补:
- strategy_lib 版本依赖 statsmodels (缺失时降级不可用)
- 本模块纯 numpy 实现 Engle-Granger + 简化 Johansen, 永不降级

A股应用:
- 同行业龙头-龙二套利 (如 600519 茅台 ↔ 000858 五粮液)
- ETF 套利 (50ETF ↔ 300ETF ↔ 500ETF)
- 跨市场套利 (A-H 股溢价)

参考:
- Engle & Granger (1987) "Co-integration and Error Correction"
- Johansen (1988) "Statistical analysis of cointegration vectors"
- Gatev, Goetzmann & Rouwenhorst (2006) "Pairs Trading: Performance check"
- 经典理论覆盖度审计 cairn/classic-theory-coverage-20260819.md Top 10 #1
"""

from __future__ import annotations

from utils.stat_arb.cointegration import (
    CointegrationResult,
    engle_granger_test,
    johansen_test,
    ols_regression,
)
from utils.stat_arb.pairs_trading import (
    PairSignal,
    PairsTradingEngine,
    find_cointegrated_pairs,
)

__all__ = [
    "CointegrationResult",
    "PairSignal",
    "PairsTradingEngine",
    "engle_granger_test",
    "find_cointegrated_pairs",
    "johansen_test",
    "ols_regression",
]
