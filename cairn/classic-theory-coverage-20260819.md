# 经典理论覆盖度审计 — 2026-08-19

> 来源：用户问"check 全网还有哪些经典理论适合我的交易系统"。从 Wikipedia 算法交易/数理金融/行为金融/微观结构/量化分析等页面拉取理论清单，用 grep 对 `utils/` 目录逐一比对实现状态。
> 范围：A股量化交易系统适用 — 衍生品定价、风险管理、组合优化、行为金融、微观结构、机器学习、随机过程、执行算法、统计套利、因子投资、经济理论。
> 用途：(1) 识别未覆盖的高价值经典理论；(2) 评估每个理论的适用性；(3) 给出实现优先级与不推荐实现的理由。
> 关联：`cairn/self-evolution-framework.md` §九（自我升级计划改进点）、`cairn/recommended-reading-20260819.md`（18 本推荐书目）。

---

## 一、已覆盖理论清单（30+，按类别分组）

### 1.1 衍生品定价与随机过程
| 理论 | 实现位置 | 备注 |
|---|---|---|
| Black-Scholes 期权定价 | `utils/fineng/pricing/black_scholes.py` | bs_call_price / bs_put_price / bs_price |
| 隐含波动率（Bisection） | `utils/fineng/pricing/implied_vol.py` | implied_vol_bisection |
| Monte Carlo 模拟 | `utils/fineng/pricing/monte_carlo.py` | 路径模拟 |
| Greeks 对冲（Delta/Gamma/Vega/Theta/Rho） | `utils/fineng/greeks/aggregator.py` + `utils/greek_hedge_manager.py` | BS Greeks + 期货 Delta 对冲 |
| 无套利下界 | `utils/fineng/pricing/implied_vol.py:_no_arbitrage_lower_bound` | 部分实现 |
| GARCH 波动率预测 | `utils/fineng/vol_forecast.py:fit_garch` | 肥尾/波动率聚集 |
| Ornstein-Uhlenbeck 均值回归 | 部分实现（`utils/tf_price_predictor.py` MA-momentum） | 隐式实现，未显式 OU 过程 |

### 1.2 风险度量
| 理论 | 实现位置 | 备注 |
|---|---|---|
| VaR（历史/参数/蒙特卡洛） | `utils/wt_risk_control.py` + `utils/risk/cvar.py` | 多方法 |
| CVaR / Expected Shortfall | `utils/risk/cvar.py:CVaRCalculator` | 历史/参数/EVT 三方法 |
| 极值理论 EVT | `utils/risk/cvar.py:_calculate_evt` | POT + Hill 估计 |
| 压力测试 | `utils/stress_test_runner.py` + `utils/hedge_engine.py:run_historical_stress_tests` | 历史场景重放 |
| Student-t 肥尾分布 | `utils/wt_risk_control.py:_cvar_monte_carlo` | 蒙特卡洛采样 |
| Transaction Cost Model | `utils/transaction_cost_model.py` + `utils/tca_pre_trade_estimator.py` | 滑点/佣金/冲击/机会/延迟 |
| Almgren-Chriss 最优执行 | `utils/market_impact_model.py` + `utils/execution_algorithm_engine.py` | 2000 论文 |
| Markov 转移矩阵 | `utils/var_backtest.py:transition_matrix` | VaR 失败独立性检验 |

### 1.3 组合优化
| 理论 | 实现位置 | 备注 |
|---|---|---|
| MPT 均值-方差 | `utils/black_litterman_optimizer.py`（隐含） | Markowitz |
| Black-Litterman | `utils/black_litterman_optimizer.py:BlackLittermanOptimizer` | 完整实现 |
| Risk Parity / Risk Budget | `utils/institutional_optimizer.py` + `utils/risk_budget_allocator.py` | 风险预算 |
| Kelly 准则 | `utils/risk_budget_allocator.py` + `utils/multi_strategy_coordinator.py` | 仓位 sizing |
| Bayesian Shrinkage | `utils/signal_fusion.py:bayesian_shrinkage_weights` | 信号融合 |
| CAPM（隐含均衡收益） | `utils/black_litterman_optimizer.py`（反向求解） | 部分实现 |

### 1.4 因子投资与回测
| 理论 | 实现位置 | 备注 |
|---|---|---|
| Fama-French 三因子/五因子 | `utils/factor_model.py`（整合自 five_factor_model.py） | 部分实现 |
| 因子投资框架 | `utils/alpha_factor/` | Gate1/S5 验证 + 因子记忆 |
| IR / IC / ICIR | `utils/alpha_factor/` | 信息比率 |
| LightGBM 增强训练 | `utils/alpha/ml_enhanced_selector.py` | 机器学习选股 |
| Triple-Barrier Labeling | `utils/backtest/triple_barrier.py` | López de Prado AFML Ch.3 |
| Purged K-Fold / CPCV | `utils/backtest/`（已有） | López de Prado |
| Walk-Forward 分析 | `utils/backtest/`（已有） | 滚动窗口 |
| Deflated Sharpe Ratio | `utils/backtest/`（已有） | 多重比较校正 |
| Momentum / Trend Following | `utils/alpha_factor/graph.py:_momentum` + `utils/tf_price_predictor.py` | 多周期动量 |
| Mean Reversion | `utils/tf_price_predictor.py:_ma_momentum_forecast` | MA 动量+均值回归 |

### 1.5 执行算法
| 理论 | 实现位置 | 备注 |
|---|---|---|
| VWAP | `utils/execution_algorithm_engine.py` + `utils/wt_execution_algo.py` | 量加权平均 |
| TWAP | `utils/execution_algorithm_engine.py` + `utils/wt_execution_algo.py` | 时加权平均 |
| Implementation Shortfall | `utils/tca_engine.py:opportunity_cost_bps` | 机会成本 |
| Smart Order Routing | `utils/execution/broker_adapters.py:list_supported_brokers` | 多经纪商 |

### 1.6 哲学与系统科学
| 理论 | 实现位置 | 备注 |
|---|---|---|
| 控制论（反馈闭环） | `utils/alpha/evolution_orchestrator.py` + `theoretical_metrics.py:FeedbackPhaseAnalyzer` | 相位裕度 |
| 反身性（Soros） | `utils/alpha/`（已有） | 自我强化循环 |
| 反脆弱（Taleb） | `utils/alpha/`（已有） | Tail risk + 反脆弱度量 |
| 证伪主义（Popper） | `utils/alpha/hypothesis_verifier.py` | 假设验证 |
| 第一性原理 | `utils/alpha/`（已有） | 因子分解 |
| 价值投资 | `utils/alpha_factor/`（价值因子） | Graham/Dodd |
| 经济机器运行（Dalio） | `utils/`（已有） | 周期分析 |
| 康波周期（Kondratiev） | `utils/kondratiev_cycle.py`（~370 行） | 周金涛涛动周期 |
| 行为金融 | `utils/alpha/`（部分） | 信号融合中的非理性 |
| Lyapunov 稳定性 | `utils/alpha/theoretical_metrics.py:LyapunovStabilityMeter` | 闭环稳定性 |
| 变异选择平衡 | `utils/alpha/theoretical_metrics.py:VariationSelectionBalancer` | 进化系统 |

### 1.7 信号与数据处理
| 理论 | 实现位置 | 备注 |
|---|---|---|
| 二元交叉熵 | `utils/alpha/ml_enhanced_selector.py:_binary_cross_entropy` | LightGBM 损失 |
| 情感分析 | `utils/sentiment_*` + `utils/last30days_adapter.py` | 舆情信号 |
| Drift 检测（PSI） | `utils/alpha/drift_monitor.py:compute_psi` + `drift_shadow_integrator.py` | 总体稳定性指数 |

---

## 二、未覆盖理论清单 + 适用性评估

### 2.1 强烈推荐实现（P0，A股高频/中频适用，ROI 高）

| # | 理论 | 类别 | 适用性 | 实现复杂度 | 优先级 | 理由 |
|---|---|---|---|---|---|---|
| 1 | **Co-integration / Johansen / Engle-Granger** | 统计套利 | ★★★★★ | 中 | **P0** | A股同行业/ETF配对交易核心；现系统有 momentum/mean-reversion 但无配对交易；Johansen 检验可识别长期均衡关系；可复用 `utils/backtest/` 框架 |
| 2 | **Pairs Trading（统计套利）** | 统计套利 | ★★★★★ | 中 | **P0** | 与 Co-integration 配套；价差 Z-score > 阈值开仓；A股 50ETF/300ETF、同行业龙头-龙二套利经典；与 LightGBM 选股正交 |
| 3 | **Hurst Exponent / R/S 分析** | 长记忆过程 | ★★★★☆ | 低 | **P0** | 判断序列持续性（H>0.5 趋势 / H<0.5 均值回归 / H=0.5 随机游走）；可加在 `utils/alpha_factor/` 作为新因子；~50 行实现 |
| 4 | **Information Theory Entropy / KL Divergence / Mutual Information** | 信号信息含量 | ★★★★☆ | 低 | **P0** | 量化信号/因子信息含量；KL 散度可度量 drift；互信息可筛选因子；与 PSI 互补；~100 行 |
| 5 | **Directional Change (DC) 算法** | 事件驱动 | ★★★★☆ | 中 | **P0** | A股高波动场景下优于固定时间间隔；捕捉"内在时间"；与现有 EOD 流程兼容；2023 研究显示在 turbulent markets 提升择时 |

### 2.2 推荐实现（P1，中频/低频适用，ROI 中）

| # | 理论 | 类别 | 适用性 | 实现复杂度 | 优先级 | 理由 |
|---|---|---|---|---|---|---|
| 6 | **Copula（Gaussian / Student-t / Clayton）** | 多资产依赖 | ★★★★☆ | 高 | **P1** | A股板块联动建模；尾部依赖性优于线性相关；可用于组合风险加总；与 CVaR 互补；需 scipy.stats |
| 7 | **Heston 随机波动率** | 衍生品定价 | ★★★☆☆ | 高 | **P1** | Heston 模型对 A股期权/权证定价更精确（肥尾+波动率聚集）；但 A股期权品种少；若做期权策略则必要 |
| 8 | **Ornstein-Uhlenbeck 显式过程** | 均值回归 | ★★★★☆ | 低 | **P1** | 显式 OU 过程参数估计（κ/θ/σ）；可校准均值回归速度；与 Pairs Trading 配套；~80 行 |
| 9 | **CPPI（固定比例组合保险）** | 组合保险 | ★★★☆☆ | 低 | **P1** | 动态保本策略；A股熊市保本需求强；与现有风险预算互补；~100 行 |
| 10 | **Carhart 四因子模型（动量+三因子）** | 多因子 | ★★★★☆ | 低 | **P1** | Fama-French + 动量因子；现系统已有 momentum + 三因子；整合即可 |
| 11 | **Post-Modern Portfolio Theory (PMPT) / Sortino** | 组合优化 | ★★★★☆ | 低 | **P1** | 下行风险优于方差；Sortino 比率；A股投资者更厌恶下行；与 MPT 互补 |
| 12 | **Lucas Critique / Rational Expectations** | 经济计量 | ★★★☆☆ | 中 | **P1** | 警示结构关系 vs 简约统计关系；策略上线后参数可能漂移；与 drift 监控互补 |
| 13 | **Behavioral Portfolio Theory / Mental Accounting** | 行为金融 | ★★★☆☆ | 中 | **P1** | A股散户主导，心理账户分层（安全/增值/投机）；可指导多策略分层配置 |
| 14 | **Prospect Theory / Loss Aversion（显式）** | 行为金融 | ★★★☆☆ | 中 | **P1** | 显式参考点+损失厌恶系数 2.25；可校准 A股投资者非对称风险偏好；解释处置效应 |

### 2.3 可选实现（P2，特定场景适用，ROI 中低）

| # | 理论 | 类别 | 适用性 | 实现复杂度 | 优先级 | 理由 |
|---|---|---|---|---|---|---|
| 15 | **Deep Reinforcement Learning (DRL/PPO/A2C/SAC)** | 自适应策略 | ★★★☆☆ | 极高 | **P2** | 自适应策略学习；但训练数据需求大、过拟合风险高、可解释性差；与 LightGBM 互补但非必需；建议 Phase B 后评估 |
| 16 | **Lévy Process / Jump Diffusion / CGMY** | 肥尾建模 | ★★★☆☆ | 高 | **P2** | 跳跃过程建模股灾；但 GARCH+Student-t+EVT 已覆盖肥尾；边际收益递减 |
| 17 | **Fractional Brownian Motion (fBm)** | 长记忆过程 | ★★☆☆☆ | 高 | **P2** | 与 Hurst 配套；但实现复杂、数值不稳定；Hurst 已足够 |
| 18 | **Local Volatility (Dupire)** | 衍生品定价 | ★★☆☆☆ | 高 | **P2** | 从期权表面反推局部波动率；但 A股期权品种少，无足够数据 |
| 19 | **HJM Framework (Heath-Jarrow-Morton)** | 利率衍生品 | ★☆☆☆☆ | 极高 | **P2** | 远期利率曲线演化；A股利率衍生品不发达，不适用 |
| 20 | **CVA / DVA / FVA / XVA** | 交易对手风险 | ★★☆☆☆ | 高 | **P2** | 银行间衍生品定价；A股量化基金一般不涉及 |
| 21 | **FRTB / Basel III 资本** | 监管资本 | ★☆☆☆☆ | 高 | **P2** | 银行监管资本计算；非银机构不适用 |
| 22 | **Ergodic Theory / Non-ergodicity** | 经济理论 | ★★☆☆☆ | 中 | **P2** | 时间平均 vs 集合平均；理论指导意义大但工程化难 |
| 23 | **Girsanov Theorem / Feynman-Kac** | 测度变换/PDE | ★★☆☆☆ | 高 | **P2** | 测度变换工具；理论意义大但工程实现少 |
| 24 | **Backward SDE** | 衍生品定价 | ★☆☆☆☆ | 极高 | **P2** | BSDE 框架；研究前沿，工程化难 |
| 25 | **Johnson's SU-distribution** | 分布建模 | ★★☆☆☆ | 中 | **P2** | 肥尾分布替代；Student-t 已足够 |
| 26 | **Stable Distribution (Lévy alpha-stable)** | 肥尾建模 | ★★☆☆☆ | 高 | **P2** | Mandelbrot 价格变化分布；无方差，参数估计难 |
| 27 | **Real Options** | 实物期权 | ★★☆☆☆ | 中 | **P2** | 项目估值；与股票交易关系弱 |
| 28 | **Credit Derivatives / CDS** | 信用衍生品 | ★☆☆☆☆ | 高 | **P2** | A股信用衍生品市场不发达 |
| 29 | **Exotic Derivatives** | 衍生品定价 | ★☆☆☆☆ | 高 | **P2** | 异型期权；A股品种少 |
| 30 | **Scenario Optimization** | 优化 | ★★☆☆☆ | 中 | **P2** | 场景优化；与现有凸优化互补 |
| 31 | **Survival Analysis / Hazard Rate** | 信用风险 | ★★☆☆☆ | 中 | **P2** | 退市风险建模可考虑；但样本少 |
| 32 | **Spline Interpolation / Nelson-Siegel** | 收益率曲线 | ★★☆☆☆ | 低 | **P2** | 国债收益率曲线；A股利率敏感度低 |

### 2.4 微观结构（部分已实现，剩余可选）

| # | 理论 | 类别 | 适用性 | 实现复杂度 | 优先级 | 理由 |
|---|---|---|---|---|---|---|
| 33 | **Adverse Selection 模型** | 微观结构 | ★★★☆☆ | 中 | **P1** | 逆向选择成本；A股做市/撮合分析；与 bid-ask 互补 |
| 34 | **Epps Effect** | 微观结构 | ★★☆☆☆ | 中 | **P2** | 高频相关衰减；A股 Tick 数据少 |
| 35 | **Price Discovery 指标** | 微观结构 | ★★★☆☆ | 中 | **P1** | 信息贡献度；Hasbrouck 信息份额 / Gonzalo-Granger 永久-暂时分解 |
| 36 | **Tick Size 影响分析** | 微观结构 | ★★☆☆☆ | 低 | **P2** | A股 0.01 元最小报价单位；可分析流动性影响 |

### 2.5 不推荐实现（NP，不适用 A股或 ROI 极低）

| # | 理论 | 不推荐理由 |
|---|---|---|
| 37 | **Card Counting** | 赌博专用，与金融无关 |
| 38 | **Bank Runs 模型** | 银行挤兑；与股票量化无关 |
| 39 | **OIS Curve / Multi-Curve Framework** | LIBOR 退出后多曲线框架；A股无此需求 |
| 40 | **Nudge Theory / Choice Architecture** | 政策助推；与交易策略无关 |
| 41 | **Financial Modelers' Manifesto** | 行业宣言；非理论 |
| 42 | **Dow Theory** | 100 年前技术分析；已被现代因子投资取代 |
| 43 | **Heat Equation / Crank-Nicolson / Finite Difference** | PDE 数值解；已有 BS 解析解+Monte Carlo，无需 PDE 数值 |
| 44 | **Bisection / Newton / Secant** | 已在 implied_vol_bisection 实现 |

---

## 三、推荐实现 Top 10（按 ROI 排序）

| 排名 | 理论 | 优先级 | 预估工作量 | 价值 |
|---|---|---|---|---|
| 1 | **Co-integration + Pairs Trading** | P0 | 2-3 天 | A股同行业/ETF 套利核心，与现有选股正交 |
| 2 | **Hurst Exponent** | P0 | 0.5 天 | 判断序列持续性，新因子，~50 行 |
| 3 | **Information Theory Entropy / KL / MI** | P0 | 1 天 | 因子信息含量筛选，与 PSI 互补 |
| 4 | **Directional Change 算法** | P0 | 2 天 | 事件驱动择时，高波动场景优势 |
| 5 | **Copula（多资产依赖）** | P1 | 3-4 天 | 板块联动+尾部依赖，组合风险加总 |
| 6 | **Ornstein-Uhlenbeck 显式** | P1 | 1 天 | 均值回归参数校准，与 Pairs 配套 |
| 7 | **CPPI 组合保险** | P1 | 1 天 | 熊市保本策略，~100 行 |
| 8 | **Carhart 四因子** | P1 | 0.5 天 | 现有因子整合即可 |
| 9 | **PMPT / Sortino** | P1 | 0.5 天 | 下行风险度量，A股投资者偏好 |
| 10 | **Prospect Theory 显式** | P1 | 2 天 | 行为金融校准，解释处置效应 |

---

## 四、与自我进化框架的关联

### 4.1 已沉淀到 `cairn/self-evolution-framework.md` §九 的改进点
- P0-1 PSI 阈值校准 ✅ 已完成
- P0-2 §八.3 三个理论方向 ✅ 已完成
- P1-1 Phase B 窗口延长 — 待 08-24 决策日后
- P1-2 Triple-Barrier Labeling ✅ 已完成
- P2 哲学模块整合 — §十已映射，待集成

### 4.2 本审计新增的改进点（建议追加到 §九）
- **P0-3 Co-integration + Pairs Trading**：建议 08-25 后启动，~3 天
- **P0-4 Hurst Exponent 因子**：可立即实现，~0.5 天
- **P0-5 Information Theory 因子筛选**：可立即实现，~1 天
- **P0-6 Directional Change 择时**：建议 08-26 后启动，~2 天
- **P1-3 Copula 板块联动**：Phase B 启动后，~4 天
- **P1-4 OU 显式 + CPPI + Carhart + PMPT**：可批量实现，~3 天

### 4.3 不建议纳入自我进化框架的理论
- DRL（过拟合风险高，与 LightGBM 互补性弱）
- Lévy/fBm/Stable Distribution（GARCH+Student-t+EVT 已覆盖肥尾）
- HJM/CVA/FRTB/Basel III（A股不适用）
- BSDE/Girsanov/Feynman-Kac（理论工具，工程化难）

---

## 五、结论

1. **系统已覆盖 30+ 经典理论**，横跨衡生品定价、风险度量、组合优化、因子投资、执行算法、哲学六大类，覆盖度在 A股量化系统中属于上游水平。
2. **未覆盖理论中 5 个 P0 强烈推荐**：Co-integration+Pairs Trading / Hurst / Information Theory / Directional Change — A股适用性高、ROI 高、与现有框架正交或互补。
3. **9 个 P1 推荐**：Copula / Heston / OU / CPPI / Carhart / PMPT / Lucas Critique / Behavioral Portfolio / Prospect Theory — 中频/低频适用，Phase B 后可批量实现。
4. **18 个 P2 可选**：DRL / Lévy / fBm / Local Vol / HJM / XVA / FRTB / Ergodic / Girsanov / Feynman-Kac / BSDE / Johnson SU / Stable / Real Options / Credit Deriv / Exotic / Scenario Opt / Survival — 特定场景或研究前沿，工程化成本高。
5. **8 个 NP 不推荐**：Card Counting / Bank Runs / OIS / Nudge / Modelers' Manifesto / Dow Theory / Heat Equation / Bisection — 不适用或已实现。
6. **Top 10 推荐**总工作量约 13-16 天，可在 08-25 至 09-10 间分批实现，与 09-02 Wave 9 集成起始日不冲突。

---

## 六、踩坑记录

- contains: wikipedia-truncation
  - Wikipedia 页面超过 200KB 时被截断，需用 Task 工具的 explore 子智能体处理；本次行为金融学页面 223KB 被截断，但已从目录结构获取所需理论清单。
- contains: grep-false-positive
  - `reinforcement|DRL|deep_rl|ppo|a2c|dqn|sac` 模式匹配到 `supported_platforms` 等无关项；需用更精确模式 `reinforcement_learning|deep_reinforcement|PPO|A2C|DQN|SAC|gym\.|stable_baselines`。
- contains: existing-coverage-missed
  - 初次评估时漏掉了 `utils/fineng/pricing/black_scholes.py`（已完整实现 BS 期权定价）；漏掉 `utils/greek_hedge_manager.py`（已实现 Delta 对冲）；漏掉 `utils/stress_test_runner.py`（已实现压力测试）；grep 时需用多模式交叉验证。

---

## 七、引用

- Wikipedia: Algorithmic trading / Mathematical finance / Behavioral economics / Market microstructure / Quantitative analysis (finance)
- López de Prado, *Advances in Financial Machine Learning* (2018) — Triple-Barrier / Purged K-Fold / DSR
- Kahneman & Tversky (1979) — Prospect Theory
- Engle & Granger (1987) — Co-integration
- Johansen (1988) — 协整检验
- Mandelbrot (1963) — Stable Distribution
- Heston (1993) — 随机波动率
- Almgren & Chriss (2000) — 最优执行
- O'Hara (1995) — Market Microstructure Theory
- Peters (2011) — Ergodicity economics