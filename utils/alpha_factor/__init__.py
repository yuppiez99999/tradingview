"""alpha_factor 包 — 国泰海通因子体系对标

11 大类因子 (98 个):
1. Value 估值类 (fundamental.py)         — EP/BP/SP/CFP/DP/FCFP/EV_EBITDA/PEG/PB_INT/PE_EX (11)
2. Growth 成长类 (fundamental.py)        — REV/NP/OCF/ROE/ROA × Q/YOY/TTM (15)
3. Quality 盈利质量类 (fundamental.py)   — ROE/ROA/ROIC/毛利率/净利率/EBITDA利润率/现金流含量/应计 (13)
4. Leverage 杠杆偿债类 (fundamental.py)  — 权益乘数/有息负债率/速动比率/利息保障/资产负债率 (6)
5. Operation 营运效率类 (fundamental.py) — 资产/存货/应收/应付周转率, 现金循环周期 (5)
6. Momentum 动量类 (price_volume.py)    — MOM_20D/60D/120D/252D/12_1M/REVERSAL/VOLUME_ADJ/UP_DOWN/INDUSTRY_ADJ (10)
7. LowVolatility 低波类 (price_volume.py) — VOL_20D/60D/120D/252D/BETA/DOWNSIDE/IDIO/SKEW (8)
8. Size 规模类 (price_volume.py)        — LOG_MCAP/LOG_NS/LOG_REV/LOG_ASSETS/SMALL_LARGE/NON_LINEAR/CUBIC (7)
9. Liquidity 流动性类 (price_volume.py)  — TURNOVER/AMIHUD/SPREAD/DEPTH/RSVP/ZERO_RET/ZSCORE (8)
10. Technical 量价技术类 (technical.py)  — GTJA191 精选 9 个低相关因子 (国泰君安191因子)
11. Expectation 预期微观类 (expectation.py) — SUE/预期调整/尾盘成交/大单/研发投入 (6)

参考:
- 国泰君安《多因子选股模型之因子分析与筛选》(估值7+成长15+质量10)
- 国泰海通《量化2025年度复盘系列》(PB_INT/SUE/尾盘成交占比/大单净买入)
- 国泰君安 GTJA191 (2017) 191个短周期量价因子
"""

from utils.alpha_factor.base import (
    FactorValue,
    FactorLibraryResult,
    winsorize,
    standardize,
    neutralize_by_industry,
    neutralize_by_size,
    orthogonalize,
    calc_ic,
    calc_ic_series_from_history,
    calc_ic_ir,
    evaluate_factors,
    compute_factor_corr_matrix,
    build_forward_returns_history,   # U1 新增 · 便捷构造器: price_data → forward_returns_history
    build_factor_history_from_prices,  # U1 新增 · 便捷构造器: price_data + factor_fn → factor_history
)
from utils.alpha_factor.library import AlphaFactorLibrary

__all__ = [
    "AlphaFactorLibrary",
    "FactorValue",
    "FactorLibraryResult",
    "winsorize",
    "standardize",
    "neutralize_by_industry",
    "neutralize_by_size",
    "orthogonalize",
    "calc_ic",
    "calc_ic_series_from_history",
    "calc_ic_ir",
    "evaluate_factors",
    "compute_factor_corr_matrix",
    "build_forward_returns_history",
    "build_factor_history_from_prices",
]
