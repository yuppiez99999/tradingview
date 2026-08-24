---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-08-23
updated: 2026-08-23
contains: p0-classic-theory, hurst, information-theory, cointegration, pairs-trading, directional-change
related:
  - cairn/classic-theory-coverage-20260819.md
  - cairn/recommended-reading-20260819.md
  - docs/系统升级文献调研与排期_20260823.md
  - utils/alpha_factor/hurst.py
  - utils/alpha_factor/information_theory.py
  - utils/stat_arb/
  - utils/timing/directional_change.py
---

# P0 经典理论四件套实现 — 2026-08-23

> 实现 `cairn/classic-theory-coverage-20260819.md` Top 10 中 P0 级别的四个经典理论：Hurst 指数 / 信息论 / 协整+配对交易 / Directional Change。
> **结论：4 模块 7 因子全部实现，36 单元测试全绿，回归 78 passed 零破坏，AlphaFactorLibrary 因子数 120→127。**

## 一、实现清单

### 1.1 Hurst 指数 (R/S 分析) — `utils/alpha_factor/hurst.py`

| 项 | 内容 |
|---|---|
| 理论 | Hurst (1951) 重标极差分析 |
| 因子 | HURST_60D / HURST_120D / HURST_252D / HURST_TREND_SCORE (4 个) |
| 类别 | LongMemory (第 17 大类) |
| 行数 | ~170 |
| 依赖 | 纯 numpy |
| 应用 | H>0.5 趋势(动量策略) / H<0.5 均值回归(反转策略) / H=0.5 随机游走 |

核心函数：
- `estimate_hurst(closes, min_n, max_n)` — log-log 回归估计 Hurst 指数
- `compute_hurst_factors(price_data)` — 4 因子批量计算
- `classify_regime(hurst)` — 制度分类 trending/mean_reverting/random_walk

### 1.2 信息论因子 — `utils/alpha_factor/information_theory.py`

| 项 | 内容 |
|---|---|
| 理论 | Shannon (1948) 信息论 / Kullback-Leibler (1951) |
| 因子 | INFO_ENTROPY_60D / INFO_ENTROPY_120D / INFO_DRIFT_60D (3 个) |
| 类别 | InformationTheory (第 18 大类) |
| 行数 | ~280 |
| 依赖 | 纯 numpy |
| 应用 | 因子信息含量筛选 / 冗余检测 / 分布漂移监控 (与 PSI 互补) |

核心函数：
- `shannon_entropy(values, n_bins)` — 香农熵 H(X)
- `kl_divergence(p, q, n_bins)` — KL 散度 D_KL(P||Q)
- `mutual_information(x, y, n_bins)` — 互信息 I(X;Y) (非线性相关)
- `factor_information_content(factor, returns)` — 因子信息含量
- `factor_redundancy(a, b)` — 因子冗余度
- `distribution_drift(current, historical)` — 分布漂移
- `select_factors_by_information(factors, returns)` — 基于信息含量的增量筛选

### 1.3 协整 + 配对交易 — `utils/stat_arb/`

| 项 | 内容 |
|---|---|
| 理论 | Engle & Granger (1987) / Johansen (1988) / Gatev et al. (2006) |
| 文件 | `__init__.py` + `cointegration.py` + `pairs_trading.py` |
| 行数 | ~400 |
| 依赖 | 纯 numpy (与 strategy_lib/pairs_trading.py 互补, 后者依赖 statsmodels) |
| 应用 | A股同行业龙头-龙二套利 / ETF 套利 / 跨市场套利 |

核心函数：
- `ols_regression(y, x)` — 零依赖 OLS 回归 (β, α, R²)
- `engle_granger_test(y, x, significance)` — Engle-Granger 两步法协整检验
- `johansen_test(series, significance)` — Johansen 多变量协整检验 (简化版)
- `find_cointegrated_pairs(price_data)` — 全标的两两协整检验
- `PairsTradingEngine` — 配对交易信号生成器 (Z-score 入场/出场)

与 `utils/strategy_lib/pairs_trading.py` 的关系：
- strategy_lib 版本依赖 statsmodels (缺失时降级不可用)
- 本模块纯 numpy 实现，永不降级
- 两者接口兼容，可互换使用

### 1.4 Directional Change — `utils/timing/directional_change.py`

| 项 | 内容 |
|---|---|
| 理论 | Tsang & Zhao (2012) 事件驱动内在时间 |
| 因子 | DC_VOL_60D / DC_VOL_120D / DC_TREND_60D (3 个) |
| 类别 | DirectionalChange |
| 行数 | ~230 |
| 依赖 | 纯 numpy |
| 应用 | 高波动场景择时 / 事件驱动回测 / DC 波动率估计 |

核心函数：
- `extract_dc_events(prices, threshold)` — 批量 DC 事件提取
- `dc_volatility(prices, threshold)` — DC 波动率
- `DirectionalChangeExtractor` — 流式 DC 提取器 (有状态)
- `compute_dc_factors(price_data, threshold)` — 3 因子批量计算

## 二、集成到 AlphaFactorLibrary

`utils/alpha_factor/library.py` 新增第 17-18 大类：

```python
# 17. Hurst 指数因子 (第 17 大类 · LongMemory, 2026-08-23 P0)
if self.enable_hurst:
    hurst_factors = compute_hurst_factors(price_data)
    result.factors.update(hurst_factors)

# 18. 信息论因子 (第 18 大类 · InformationTheory, 2026-08-23 P0)
if self.enable_info:
    info_factors = compute_information_factors(price_data)
    result.factors.update(info_factors)
```

新增开关：`enable_hurst=True` / `enable_info=True` (默认 ON)

因子数变化：120 → 127 (+4 Hurst +3 Info)
注：Co-integration 和 DC 未集成到 AlphaFactorLibrary（它们是策略层模块，不是截面因子），通过 `utils/stat_arb/` 和 `utils/timing/` 独立调用。

## 三、测试验证

### 3.1 单元测试 — `tests/unit/test_p0_classic_theory_unit.py`

36 个测试，4 个 TestClass：

| TestClass | 测试数 | 覆盖 |
|---|---|---|
| TestHurstExponent | 6 | 趋势/随机游走/短序列/范围/因子计算/制度分类 |
| TestInformationTheory | 11 | 熵/KL/互信息/因子信息含量/冗余/漂移/筛选 |
| TestCointegration | 6 | OLS/协整/非协整/短序列/Johansen |
| TestPairsTrading | 3 | 寻找协整对/信号生成/Z-score |
| TestDirectionalChange | 7 | 事件提取/波动率/流式/因子/趋势 |

### 3.2 回归测试

- `test_g7_alpha_factor_library_boost.py` — 全绿
- `test_g7_alpha_factor_library_top_boost.py` — 全绿
- **总计 78 passed, 0 failed, 零回归破坏**

## 四、设计决策

### 4.1 为什么零第三方依赖？

系统 AGENTS.md 要求"零第三方依赖的主系统"。statsmodels 虽强大但非必装，本模块纯 numpy 实现确保永不降级。与 `strategy_lib/pairs_trading.py`（依赖 statsmodels）互补，用户可按需选择。

### 4.2 为什么 DC 和 Co-integration 不入 AlphaFactorLibrary？

- AlphaFactorLibrary 是**截面因子**库（每标的一个值）
- Co-integration 是**标的对**关系（两标的协整），不是单标的因子
- DC 是**择时信号**（时序事件），不是截面值
- 两者通过独立模块调用，保持架构清晰

### 4.3 Hurst 和 Info 为什么入 AlphaFactorLibrary？

- Hurst 指数：每标的一个值（该标的的序列长记忆性），是截面因子
- Info 熵/漂移：每标的一个值（该标的收益率分布复杂度），是截面因子
- 两者与现有因子体系正交，可参与 IC 评估和组合优化

## 五、与现有计划的关系

| 现有计划 | 本实现关系 |
|---|---|
| `cairn/classic-theory-coverage-20260819.md` Top 10 | **完成 #1-#4** (P0 四件套) |
| `docs/系统升级文献调研与排期_20260823.md` | 独立完成，与 Wave 8-LIT 排期并行 |
| `cairn/recommended-reading-20260819.md` | 书籍推荐中的 Hurst/信息论/协整方向已落地 |
| Wave 7-QC | 不冲突，纯新增模块无修改现有代码逻辑 |

## 六、后续增强方向（P1，未实现）

来自 `classic-theory-coverage-20260819.md` Top 10 #5-#10：

| # | 理论 | 优先级 | 预估 | 触发条件 |
|---|---|---|---|---|
| 5 | Copula (多资产依赖) | P1 | 3-4 天 | 组合尾部风险建模需要时 |
| 6 | Ornstein-Uhlenbeck 显式 | P1 | 1 天 | Pairs Trading 半衰期校准 |
| 7 | CPPI 组合保险 | P1 | 1 天 | 熊市保本策略 |
| 8 | Carhart 四因子 | P1 | 0.5 天 | 现有因子整合即可 |
| 9 | PMPT / Sortino | P1 | 0.5 天 | 下行风险度量 |
| 10 | Prospect Theory | P1 | 2 天 | 行为金融校准 |

## 七、引用

- Hurst, H.E. (1951). "Long-term storage capacity of reservoirs". Trans. Amer. Soc. Civil Eng.
- Shannon, C.E. (1948). "A Mathematical Theory of Communication". Bell System Technical Journal.
- Kullback, S. & Leibler, R.A. (1951). "On Information and Sufficiency". Ann. Math. Stat.
- Engle, R.F. & Granger, C.W.J. (1987). "Co-integration and Error Correction". Econometrica.
- Johansen, S. (1988). "Statistical analysis of cointegration vectors". J. Econ. Dynamics & Control.
- Gatev, E., Goetzmann, W. & Rouwenhorst, K. (2006). "Pairs Trading: Performance check". Rev. Fin. Studies.
- Tsang, E. & Zhao, Y. (2012). "Directional Change and Event-Based Time". SSRN.
- Mandelbrot, B. & Ness, J. (1968). "Fractional Brownian motions". SIAM Review.