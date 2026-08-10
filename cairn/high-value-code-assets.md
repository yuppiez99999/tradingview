---
type: asset_map
status: completed
authoring_mode: ai_generated
created: 2026-08-10
updated: 2026-08-10
contains: high-value-code, asset-discovery, quant-system, reusable-algorithms, dedup
related:
  - cairn/code-review-glm45-llm-scan.md
  - cairn/LOG.md
  - 工作区资产扫描统计_20260803.md
---

# 量化系统高价值代码资产地图（E:\各种PY程序）

> 2026-08-10 深度探索：从全工作区挖掘对量化交易系统**有高价值的代码模块/算法/工具**。
> 结论：**>95% 高价值资产集中在 `28-终极量化交易系统8.4/`**（`utils/` 152 py + `ms_strategy/src/` 62 py）。`11_量化策略` 已于 2026-08-03 归档至 `_archive/11_量化策略_v5.10_已废弃/`（模块已合并进 28 系统）；`04_交易与套保执行/` 全部在 `_archive_20260620/` 已废弃；根目录散落脚本多数为断链副本（如 `career_investor_model.py` 引用已删除的 `11_量化策略` 包，**无法运行**）。

## 一、Top 18 高价值清单（按价值排序）

### 第一梯队：机构级独立算法（学术完整 + 零/轻依赖 + 强可复用）

| # | 文件 | 规模 | 核心价值 | 依赖 | 集成状态 |
|---|------|------|----------|------|----------|
| 1 | `utils/hedge_engine.py` | 1754行/72KB | 多指数Beta加权对冲引擎：VaR95/CVaR95/MRC边际风险贡献/历史压力测试/三段式对冲比率/期货+期权对冲。算法密度最高单文件 | **零第三方依赖** | 已集成 |
| 2 | `utils/hedge_rebalance_backtest.py` | 1410行/56KB | 5策略对冲再平衡回测v2.0：**国内罕见"对冲成本全项分离"**（展期+保证金+滑点+佣金独立核算）+ 逐年分解 + 对指数超额 | numpy/pandas/baostock | 已集成 |
| 3 | `utils/execution_algo_engine.py` | 1025行/34KB | 6算法执行引擎：**含完整 Almgren-Chriss 闭式最优轨迹**（`κ=√(λσ²/η)`, `x(t)=X·sinh(κ(T-t))/sinh(κT)`）+TWAP/VWAP/POV/IS/Dark-Iceberg | numpy | 已集成 |
| 4 | `utils/black_litterman_optimizer.py` | 16KB | Black-Litterman 观点驱动配置：市场均衡收益反解+主观观点贝叶斯融合(Idzorek 2007)+约束MVO(SLSQP) | numpy/scipy(可回退) | **未集成**（独立抽取价值极高） |
| 5 | `utils/ledoit_wolf_covariance.py` | 10KB | Ledoit-Wolf 收缩协方差估计：最优收缩强度δ*闭式解+常量相关性目标+bootstrap不确定性 | numpy | 部分（sklearn版在risk_budgeter） |
| 6 | `ms_strategy/src/alpha/factor_library.py` | 25KB | 五维因子库：18+因子(Value/Quality/Momentum/Growth/Safety+技术形态)+Z-score | numpy/pandas | 部分（弱于alpha_factor） |

### 第二梯队：生产级 factor/backtest/hedging 核心

| # | 文件 | 规模 | 核心价值 | 依赖 | 集成状态 |
|---|------|------|----------|------|----------|
| 7 | `utils/alpha_factor/` | 13文件 | 生产级11大类100+因子：winsorize/标准化/行业市值中性化/正交化/IC评估/GTJA191。factor zoo完整度**远超**factor_library | numpy/pandas | 已集成（PortfolioOptimizer消费） |
| 8 | `ms_strategy/src/backtest/combinatorial_purged_cv.py` | 16KB | **CPCV 防泄漏交叉验证**（Lopez de Prado）：purge+embargo+C(N,k)路径+DSR悲观分位 | numpy/pandas | 已集成（机构级） |
| 9 | `ms_strategy/src/backtest/metrics.py` | 10KB | 绩效指标+**Deflated Sharpe Ratio**（Bailey & López de Prado，含极值理论 E[SR_max]）防过拟合 | numpy/scipy | 已集成 |
| 10 | `ms_strategy/src/hedging/`（coordinator+4对冲器） | ~42KB | 三联对冲协调器：Beta+Vol+Corr+Tail并行调度+尾部风险状态机 | numpy | 已集成（与utils版重复） |
| 11 | `ms_strategy/src/risk/risk_budgeter.py` | 12KB | Risk Parity+改进Kelly(James-Stein收缩+半Kelly)+三级回撤熔断 | numpy/scipy | 已集成 |
| 12 | `ms_strategy/src/ml/drift_detector.py` | 24KB | 模型漂移检测：IC衰减/ADWIN/KS/PSI/OOS-IS gap，自动重训触发 | numpy/scipy | 已集成 |

### 第三梯队：信号/执行/风险管理

| # | 文件 | 规模 | 核心价值 | 依赖 | 集成状态 |
|---|------|------|----------|------|----------|
| 13 | `ms_strategy/src/alpha/signal_generator.py` | 9KB | LASSO+Ridge 信号生成：LassoCV特征选择+RidgeCV权重+[-1,1]标准化 | numpy/sklearn | 已集成 |
| 14 | `ms_strategy/src/execution/algo_engine.py` | 14KB | TWAP/VWAP/POV/ICEBERG 拆单引擎（**被utils版 superseded**，更弱） | numpy | 已集成（重复） |
| 15 | `utils/market_impact_model.py` | 14KB | Almgren-Chriss 市场冲击模型：Square-Root+AC分解+最优轨迹+紧急度分级 | numpy | 已集成（institutional_optimizer用） |
| 16 | `ms_strategy/src/backtest/cost_aware_backtest.py`+`cost_model.py` | 22KB | 成本感知回测：事件驱动+逐笔成本(佣金/印花/过户/冲击) | numpy/pandas | 已集成 |
| 17 | `ms_strategy/src/alpha/qlib_signal_adapter.py` | 31KB | Qlib/LightGBM 信号桥接：Qlib优先+本地LGB回退+防前视(bfill删除) | qlib/lightgbm(可选) | 已集成 |
| 18 | `utils/portfolio_optimizer.py::apply_risk_management` | L217-414 | 波动率缩放+回撤去杠杆(昨日净值防前视)+1.5x敞口硬上限。**最值得单抽的风险管理子算法** | numpy | 已集成 |

### 候补高价值（未入 Top18 但值得关注）
- `daily_trade_executor.py` → 建仓计划生成（`calculate_daily_budget`/`_compute_price_band`/`_allocate_position`/`generate_instructions`）：**生产决策链路核心，最值得独立抽取**
- `build_plan_executor.py` → 分批下单（`generate_daily_orders`/`_calculate_session_shares`/`get_emergency_protocol`）
- `ms_strategy/src/alpha/pairs_trading.py` → 协整配对交易（Engle-Granger+OU半衰期），统计套利完整实现
- `utils/kill_switch.py` → 三级熔断 L1/L2/L3，实盘安全关键（强依赖configs）
- `institutional_pipeline_runner.py` → 盘中决策子算法（`_step_market_regime_scaling`/`_apply_momentum_reversal`/`_apply_max_weight_cap`）

## 二、重复实现与去重建议（治理重点）

| 功能 | 重复模块 | 建议 |
|------|----------|------|
| 因子库 | `ms_strategy/src/alpha/factor_library.py` vs `utils/alpha_factor/` | **统一用 `alpha_factor/`**，前者废弃 |
| 执行算法引擎 | `ms_strategy/src/execution/algo_engine.py` vs `utils/execution_algo_engine.py` | utils版更强（含AC/IS/Dark），以utils版为准 |
| 对冲引擎 | `ms_strategy/src/hedging/*` vs `utils/hedge_engine.py`/`hedge_rebalance_*` | utils版生产主用，ms_strategy版为模块化重构，二选一 |
| 协方差收缩 | `utils/ledoit_wolf_covariance.py` vs `risk_budgeter.py`内sklearn LedoitWolf | 自实现版可统一底座 |
| 市场冲击 | `utils/market_impact_model.py` vs `ms_strategy/src/backtest/cost_model.py` | 互补（模型vs回测），可保留 |

## 三、最值得优先独立抽取的 3 个资产

1. **`utils/alpha_factor/`** — 因子底座（11大类+正交化/中性化/IC），全系统因子计算统一基线
2. **`utils/black_litterman_optimizer.py` + `ledoit_wolf_covariance.py`** — 组合优化底座（BL观点配置+收缩协方差），零依赖可独立复用
3. **`daily_trade_executor.py` 的建仓计划生成子算法** — 生产决策链路核心（目标权重→可执行建仓指令）

## 四、与 `工作区资产扫描统计_20260803.md` 的对比修正

| 2026-08-03 旧结论 | 2026-08-10 修正 |
|-------------------|----------------|
| `11_量化策略` 是 v5.10 活跃主线 | **已归档废弃**（2026-08-03 合并进 28 系统），模块已迁移 |
| `04_交易与套保执行/` 有动力煤套保/TRAE | **全部在 `_archive_20260620/` 已废弃**，无独立高价值 |
| 根目录脚本是候选高价值资产 | 多为断链副本（引用已删 `11_量化策略` 包），不可运行 |
| 三套量化系统并列 | 实际 **28 系统是唯一定型主系统**，其余为历史参考 |

## 五、方法论沉淀

- 高价值代码挖掘 = **目录结构分析（定位主系统）+ 算法完整性评估（数学严谨度）+ 依赖审计（零依赖=高可移植）+ 集成状态核查（是否重复）** 四步法。
- LLM 子代理（code-explorer）适合做大规模只读探索，但返回易截断，需分批按区域拆分子任务（本任务分 2 批：Top1-3 + Top4-18）。
- **重复实现识别是高价值挖掘的关键副产品** —— 同一算法多版本并存会稀释维护精力，应明确"唯一真相源"。

## 六、指针

- 工作区总览：`工作区资产扫描统计_20260803.md`（根目录 `E:\各种PY程序\`）
- 代码审查方法论：`cairn/code-review-glm45-llm-scan.md`
- 主系统架构：`AGENTS.md` §1-§4
