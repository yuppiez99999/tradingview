# 终极量化交易系统 v8.7

> A股量化交易系统 — 多因子选股 · LightGBM增强训练 · 策略回测 · 风控管理 · 全栈数据采集 · Alpha研究
> 500万实盘部署 | 全自动交易闭环 | 年化≥8% 且最大回撤<15% | 双LLM决策 | 多源数据融合 | 风控守卫强制执行 | P0自检系统 | 数据契约测试 | 气象因子引擎 | GTJA191因子对标 | GNN供应链产业链因子 | 自我进化框架 | VolRegimeWeighter | MVSK高阶矩优化 | ETF期权对冲再平衡

**作者**：yuppiez99999
**实盘状态**：✅ 已部署（2026-07-28）
**生产基线**：Python 3.14.4（junction `C:\QuantSys`），兼容 Python 3.9+
**当前阶段**：v8.7 Sprint 1 冲刺中（3/4 门禁达标，目标 2026-12-31 发布）
**最近更新**：2026-08-21 — 代码质量修复(F401×6/BLE001×5/T201×2/F841×1/F541×1 + 硬编码路径×2) ruff 223→211 + ETF期权对冲S2/S3集成(TimesFM预测器+价值纪律层) + 过期文件清理(20个) + Phase 1完成 + Phase B观察期达标B1自动启用 + v8.7三门禁D9覆盖率0.833达标 + 执行层自动闭环 + MVSK高阶矩优化生产就绪

---

## 核心特性

### 机构级量化架构
- **双账户结构**：500万总资金（现货400万 + 对冲100万），已实盘部署
- **风险预算驱动**：Risk Parity + Kelly公式动态分配建仓预算
- **三联对冲引擎**：Beta / Vol / Correlation 三类对冲实时联动 + 尾部风险保护
- **完整风控体系**：个股止损 + 组合回撤四级防御 + Walk-Forward回测验证
- **硬性风险约束**：单标的10%上限、单板块25%上限、组合日度VaR95 1.5%

### AI增强决策
- **双LLM架构**：快速模式 Qwen2.5 7B（~22秒）+ 深度思考 DeepSeek-R1 14B（~1-3分钟）
- **深度思考触发**：5种场景自动切换深度模型（组合止损/多股止损/ETF加仓/对冲偏离/大幅盈亏）
- **六级降级链**：Ollama → 腾讯混元 → 百度千帆 → 智谱GLM → 豆包 → DeepSeek
- **双模型自我判断**（v8.7新增）：DeepSeek + GLM-5.2 独立判断 → 交叉验证 → 共识决策，双失败优雅降级到规则兜底
- **新闻情感分析**：实时抓取东方财富/巨潮资讯/新浪财经公告与研报
- **价格预测**：TimesFM零样本 + TensorFlow LSTM + ARIMA 三级降级

### 完全自动化
- **无人化交易闭环**：盘前自动生成 → 自动确认 → 盘后自动执行 → 自动生成下一日计划
- **执行层自动闭环**（v8.7新增）：盈亏→风控→改计划→对冲增减→再平衡 全自动闭环，RiskGuardIntegrator 8-Guard链自动识别国债集中度52.7%>15%触发L3减仓7100股+再平衡19单
- **Windows任务调度**：07:05盘前 / 09:30早盘 / 14:00午盘 / 21:00夜盘 / 15:30盘后 + 盘中每15分钟LLM决策
- **十五五规划对齐**：2026-2030五年阶段管理，2030-12-31强制清仓

---

## v8.7 最新进展（2026-08-21）

### ETF期权对冲再平衡子模型 Phase 1 ✅
独立200万纯ETF子组合（14 ETF/100%纯ETF）+ ETF期权对冲（4标的认沽保护）+ 自我再平衡（五阶段）。

- `config/etf_option_subportfolio.yaml` — 子组合配置（宽基60% + 行业25% + 防御15%）
- `etf_option_hedge_rebalancer.py` — 编排器（复用6个现有模块：protective_put_engine/broad_based_etf_policy/portfolio_optimizer/drawdown_breaker/kill_switch）
- `tests/unit/test_etf_option_hedge_rebalancer_unit.py` — 31/31 passed
- **压力测试**：裸敞口6/6突破 → 对冲后3/6突破（2020疫情11.9% / 2022俄乌12.7% / 2024地产13.5% 已保护到15%以内）
- **排期**：Phase 2(08-21~09-05回测验证) → Phase 3(09-06~10-05 shadow 30天) → Phase 4(10-06~11-05灰度5%→25%) → Phase 5(11-06~12-31全量+年度报告)
- 详见 `cairn/etf-option-hedge-model.md`

### v8.7 三门禁冲刺（D9/D10/D11）
| 门禁 | 状态 | 当前值 | 目标 |
|------|------|--------|------|
| D9 覆盖率 Sprint4 | ✅ 达标 | line_rate=0.833, branch_rate=0.7605 | ≥0.80 |
| D10 超大文件拆分 | ❌ 待拆 | 2620+2691行 | ≤2000行 |
| D11 PhaseB shadow 7天稳定 | ⏳ 进行中 | 0/7天 | 7天稳定 |
| v8.7 汇总判定 | ❌ BLOCK | 三门禁未全达标 | 全PASS放行 |

- `V87GateSummary` frozen dataclass + `check_v87_release_gate_summary()` 聚合判定
- CI `quality-gate.yml` 追加 Engineering debt gate 步骤（退出码2阻断合并）
- 详见 `docs/v87_release_notes_20260820.md`

### Phase B 观察期达标 + B1 自动启用 ✅
- **观察期达标**：`daily_returns.jsonl` 21条（07-23~08-20），真实样本21/20 ✅，累计-0.5812%
- **调度器自动推进**：`phase_b_progressive_enabler.py --check` 触发状态机推进 — stage: waiting_observation → **drift_monitor**
- **修正后时间线**：08-20 B1启用 ✅ → 08-23 B2(abtest shadow) → 08-26 B3(auto_retrain) → 08-29 B4(orchestrator) → 09-01前完成Wave 2

### 代码质量工业级修复（ruff 1139→211，81%降幅）
- **Phase A1+A2**：torch collection修复 + 测试回归修复（13,959全收集）
- **Phase A3**：35个核心模块类型注解批量补齐（ANN 908→397）
- **Phase A4**：ruff风格清理3步走（628→434，<500达标）
- **Wave 1**：安全+收敛+ANN（ruff 434→206，bandit清零）
- **Wave 2**：C901豁免+P3清理（ruff 206→169，<300达标）
- **08-21修复**：F401×6 + F541×1 + F841×1 + BLE001×5 + T201×2 + 硬编码路径×2（ruff 223→211，commit c8bf9ca8）
- **对标**：Two Sigma/Citadel工业级12维度，详见 `cairn/code-quality-industrial-gap-20260819.md`

### Phase B 可观测性闭环
- **structlog接入**：`StructuredLogger` 升级为structlog JSON后端 + 标准logging回退 + bind()上下文绑定
- **pydantic事件Schema**：OrderEvent / RiskEvent / ExecutionEvent / PipelineEvent
- **pytest-benchmark**：6个关键路径性能基准全通过（KillSwitch 2,327 Kops/s，Event Schema 268-308 Kops/s）

### Phase C 战略级
- **OpenTelemetry分布式追踪**：`utils/observability/tracing.py` — trace_order/trace_risk/trace_pipeline span埋点（OTel 1.44.0）
- **mypy strict推进**：observability全strict + risk启用disallow_any_generics

### MVSK高阶矩优化 P1-P4 ✅ 生产就绪
- **P1**（08-17）：`risk_budget_optimizer.py` 扩展偏度/峰度目标，8/8+31/31测试passed
- **P2**（08-17）：BL后验μ + MVSK样本外夏普-0.923显著优于MV-1.272，BL与MVSK强互补
- **P3**（08-17）：378日训练窗口扫描发现临界点，**始终BL+MVSK(378)最优夏普+0.418**，无需regime切换
- **P4**（08-17）：995日跨周期回测，BL+MVSK(378) **4/4段跑赢BL+MV**（Δ夏普+0.22），γ_s=0.1泛化成功
- **最终策略**：BL+MVSK(378, γ_s=0.1, γ_k=0.1)，P5生产接入排期09-05~11-12
- 详见 `cairn/mvsk-higher-moment-optimization.md`

### 自我进化框架升级
- **PSI阈值校准**（P0-1）：`drift_shadow_integrator.py:calibrate_psi_thresholds` 从骨架升级为真实校准逻辑
- **Lyapunov稳定性**（P0-2a）：`LyapunovStabilityMeter` — V=w_ic×|IC-target|²+w_ret×|ret-target|²+w_drift×drift²
- **反馈相位分析**（P0-2b）：`FeedbackPhaseAnalyzer` — 相位裕度=π-总延迟×角频率
- **变异选择平衡**（P0-2c）：`VariationSelectionBalancer` — B=变异×通过率/(变异+选择)，B∈[0.1,0.5]健康
- **Triple-Barrier**（Day3-4）：`utils/backtest/triple_barrier.py`（~380行），18/18测试全绿
- 详见 `cairn/self-evolution-framework.md`

### 经典理论覆盖度审计
- **已覆盖30+经典理论**：BS/Monte Carlo/Greeks/GARCH/VaR/CVaR/EVT/MPT/BL/Kelly/Bayesian/Fama-French/Triple-Barrier/Purged K-Fold/DSR/VWAP/TWAP/Almgren-Chriss/控制论/反身性/反脆弱/康波/Lyapunov/变异选择
- **P0推荐待实现5个**：Co-integration+Pairs Trading / Hurst Exponent / Information Theory Entropy / Directional Change
- 详见 `cairn/classic-theory-coverage-20260819.md`

---

## 快速开始

### 1. 环境准备

```bash
git clone <repo-url>
cd 28-终极量化交易系统8.4
pip install -r requirements.txt          # Python 3.9+
pip install -r requirements_dev.txt       # 开发工具（可选）
```

### 2. 环境变量配置

复制 `.env.example` 为 `.env`，填入密钥：

```ini
WIND_API_KEY=...          # Wind MCP（P1数据源）
IFIND_TOKEN=...           # iFinD（★已从核心降级链剔除，仅新闻/研究独立功能需要）
TS_TOKEN=...              # Tushare（国内期货/CPI）
VOLCENGINE_API_KEY=...    # 豆包 LLM
DEEPSEEK_API_KEY=...      # DeepSeek（信号计算）
GLM_API_KEY=...           # 智谱 GLM-5.2（合规审计+双模型判断）
MOONSHOT_API_KEY=...      # Kimi3（研报多模态）
CLAUDE_API_KEY=...        # Claude（深度推理/风控）
OPENAI_API_KEY=...        # GPT（盘中研判）
APIZERO_API_KEY=...       # APIZero（气象API + probe脚本）
OLLAMA_MODELS=...         # Ollama 模型路径
LOG_LEVEL=INFO
```

### 3. 系统自检

```bash
python scripts/run_p0_startup_check.py --strict    # P0严格自检（盘前最终核查）
```

### 4. 首次运行

```bash
python institutional_pipeline_runner.py --mode smoke                                  # 烟雾测试
python institutional_pipeline_runner.py --mode backtest --symbols 600519 000858       # 回测
python institutional_pipeline_runner.py --mode live                                   # 生产
```

---

## 主入口与 CLI 命令

| 入口 | 说明 |
|------|------|
| `institutional_pipeline_runner.py` | 机构级闭环运行器（数据门控→Alpha评估→信号融合→组合优化→风险预算→执行路由） |
| `15_每日工作流/run_daily_eod_workflow.py` | 盘后工作流（收盘报告→风控守卫→次日计划→预生成盘中决策） |
| `live_scheduler.py` | 实时调度器（交易日09:25-15:05每15分钟触发LLM盘中决策） |
| `etf_option_hedge_rebalancer.py` | ★v8.7 ETF期权对冲再平衡编排器（五阶段日度再平衡） |
| `daily_trade_executor.py` | 交易计划执行 |
| `stop_loss_monitor.py` | 止损监控 |
| `signal_monitor.py` | 信号监控 |
| `build_plan_executor.py` | 建仓计划执行 |
| `generate_daily_report.py` | 日报生成 |

```bash
# 盘后工作流
python 15_每日工作流/run_daily_eod_workflow.py                    # 完整
python 15_每日工作流/run_daily_eod_workflow.py --phase report     # 仅报告
python 15_每日工作流/run_daily_eod_workflow.py --phase plan       # 仅次日计划

# 实时调度
python live_scheduler.py                   # 启动调度器
python live_scheduler.py --once            # 单次执行

# Phase B 渐进启用（v8.7）
python scripts/phase_b_progressive_enabler.py --check      # 检查观察期状态
python scripts/phase_b_progressive_enabler.py --advance    # 手动推进阶段
```

---

## 因子体系（12大类）

### 12大类因子分类（11大类对标国泰君安GTJA191 + 第12大类LeadLag图因子）

```
utils/alpha_factor/
├── base.py                  # 基础数据结构 + 预处理（去极值/标准化/中性化/正交化）
├── library.py               # 因子库聚合入口（12大类统一调度 + 跨类正交化后处理）
├── alpha_factor_library.py  # 向后兼容 shim
├── value.py                 # 价值类（EP/DP/BP/SP/CFP）
├── growth.py                # 成长类（营收/利润/资产增长率）
├── quality.py               # 质量类（ROE/ROA/毛利率）
├── leverage.py              # 杠杆类（资产负债率/权益乘数）
├── operation.py             # 运营类（资产周转率/存货周转率）
├── price_volume.py          # 量价类（动量/低波/规模/流动性）
├── technical.py             # 技术类（GTJA191量价因子集成）
├── expectation.py           # 预期类（分析师预期/微结构）
├── graph.py                 # 第12大类 LeadLag（GNN供应链产业链5因子）
├── gat_factor_torch.py      # GAT注意力层（torch自动微分）
└── gate1_validation.py      # Gate 1门禁验证（跨时间窗IC/ICIR）
```

### GTJA191因子集成

```python
from utils.alpha_factor.library import AlphaFactorLibrary, DEFAULT_GTJA

# DEFAULT_GTJA 精选9个短周期量价因子
# gtja191_004 / gtja191_018 / gtja191_030 / gtja191_044 / gtja191_054
# gtja191_084 / gtja191_092 / gtja191_148 / gtja191_178

library = AlphaFactorLibrary()
result = library.compute(data, factor_names=DEFAULT_GTJA)
```

### 三级共线解决方案（|ρ|>0.99 完全共线对 12→0）

| 级别 | 方法 | 适用场景 | 典型应用 |
|------|------|----------|----------|
| L1 | 基础窗口正交化 | 多窗口因子共线 | `LIQ_TURNOVER_60D` 对 20D 拋差化 |
| L2 | 双重残差化 | 跨公式等价共线 | `SIZE_CUBIC` 对 `log(mcap)` 正交化 |
| L3 | 非单调变换 | 线性关系共线 | `SIZE_NON_LINEAR` 改为中盘V型得分 |

---

## 数据源优先级

全局统一标准，所有模块必须遵循以下降级链，不可跳级（★iFinD已从核心降级链剔除）：

| 优先级 | 数据源 | 说明 | 认证 |
|--------|--------|------|------|
| P0 | Wind数据终端 | 主数据源，WindPy原生客户端 | WindPy授权 |
| P1 | Wind MCP | 强制回退，analytics_data/stock_data/fund_data | `WIND_API_KEY` |
| P2 | 通达信(pytdx) | 免费直连，TCP 7709端口，仅A股 | 无需 |
| P3 | AKShare/baostock | 免费回退，A股/期货/指数 | 无需 |
| P4 | 新浪财经API | 免费实时行情兜底 | 无需 |
| P5 | 本地缓存 | Parquet/JSON缓存 | 无需 |
| P6 | 预定义价格 | 保证系统永不崩溃 | 无需 |

**强制规则**：Wind不可用时必须尝试Wind MCP，不可直接跳到通达信。

---

## 风控体系

### 四模块联动风控链

每日EOD后强制执行：回撤检查 → 波动率控制 → 对冲执行 → 认沽保护。

| 模块 | 文件 | 职责 |
|------|------|------|
| 回撤熔断器 | `utils/drawdown_breaker.py` | 四级回撤防御（L1预警→L4全面停止） |
| 波动率目标缩仓 | `utils/vol_target_controller.py` | AQR/Man Group风格Vol Targeting，realized vol>12%自动缩仓 |
| 对冲执行引擎 | `utils/hedge_execution_engine.py` | 对冲信号→IF期货+ETF期权订单，动态Beta计算 |
| 认沽期权保护 | `utils/protective_put_engine.py` | ¥77.8万Put预算，OTM 5%虚值覆盖四大指数ETF |
| Kill Switch | `utils/kill_switch.py` | L1/L2/L3三级熔断，触发即终止工作流 |
| 风险守卫集成 | `utils/risk_guard_integrator.py` | 8-Guard联动 + 执行日志 |

### 硬性风险约束（`utils/risk_constraints.py`）

- 单标的硬上限：10%
- 单一板块硬上限：25%
- 组合日度VaR95硬上限：1.5%
- 单票日度VaR95硬上限：0.8%
- 对冲暴露上限：`HEDGE_EXPOSURE_CAP = 0.40`
- 换手率预算：`TURNOVER_BUDGET = 0.20`

---

## 回测协议

### 目标函数
```
J = Sortino + 0.5 × Calmar - λ‖w‖²
```

### Walk-Forward配置
- 训练窗口：24月 / 测试窗口：3月 / 步长：3月

### 必过压力测试

| 事件 | 区间 | 跌幅 |
|------|------|------|
| 全球金融危机 | 2008-09-15 ~ 2009-03-09 | S&P -56% |
| A股股灾 | 2015-06-12 ~ 2015-08-26 | 上证 -43% |
| 熔断机制 | 2016-01-04 ~ 2016-01-28 | 4天2次熔断 |
| 中美贸易战 | 2018-03-22 ~ 2018-10-29 | 上证 -25% |
| COVID闪崩 | 2020-02-19 ~ 2020-03-23 | — |
| Luna崩盘 | 2022-05-01 ~ 2022-05-12 | — |
| 全球债券大屠杀 | 2022-01-01 ~ 2022-10-24 | 股债双杀 |
| 日元Carry Trade | 2024-08-01 ~ 2024-08-05 | — |

### CRO Gate（上线前必过）
- [ ] Walk-Forward 5窗口拼接 Sortino ≥ 1.0
- [ ] 八段压力测试 Max DD < 15%
- [ ] Deflated Sharpe Ratio ≥ 0.95
- [ ] 无未来函数（PIT检查通过）
- [ ] NTP漂移 < 50ms 持续7个交易日
- [ ] 滑点熔断在历史回放中正确触发
- [ ] CRO签字

---

## 系统自我升级

### 1. 自我进化框架（EvolutionOrchestrator）
核心实现：`utils/alpha/evolution_orchestrator.py`
- **观察期**：14天 + 最小评估样本20天，未达样本前不触发自动变更
- **双链路架构**：盘中`AutoTradingSystem`每30s只读`VolRegimeWeighter`建议；EOD`EvolutionOrchestrator`产出完整进化报告
- **产物落盘**：决策日志与进度快照自动持久化至`reports/evolution/`
- **定时运行**：`v84_EvolutionEval`任务每天16:05自动执行

### 2. 统一升级计划（UNIFIED_UPGRADE_PLAN）
- **周期**：2026-08-11 ~ 2026-12-31（8个Sprint + 实盘准入）
- **里程碑**：08-22中期工业级达标 → 09-30全面达标 → 10-31工程基础层就位 → 12-31实盘准入
- **门禁驱动**：所有状态以门禁三件套实测值为准

### 3. Phase B 渐进启用（v8.7进行中）
- [x] B1 (08-20) `USE_DRIFT_DETECTOR=true` 仅告警 ✅
- [ ] B2 (08-23) `USE_FEEDBACK_LOOP` 自动接入
- [ ] B3 (08-26) `USE_AUTO_RETRAIN=true` + 降级护栏
- [ ] B4 (08-29) `USE_MLOPS_PIPELINE=true` 完整外层循环

---

## 项目结构

```
28-终极量化交易系统8.4/
├── institutional_pipeline_runner.py         # 主入口 — 机构级闭环运行器
├── etf_option_hedge_rebalancer.py           # ★v8.7 ETF期权对冲再平衡编排器
├── 15_每日工作流/run_daily_eod_workflow.py   # 盘后工作流入口
├── live_scheduler.py                        # 实时调度器
├── daily_trade_executor.py                  # 交易计划执行
├── lgb_enhanced_trainer.py                  # LightGBM增强训练器
├── alpha_hedge_engine.py                    # Alpha对冲引擎
│
├── utils/                                   # 核心工具模块（100+模块）
│   ├── alpha_factor/                        # 因子库包（12大类）
│   ├── alpha/                               # Alpha信号与LLM路由
│   │   ├── llm/                             # LLM提供商
│   │   ├── evolution_orchestrator.py        # 自我进化框架
│   │   ├── vol_regime_weighter.py           # 波动率Regime权重建议器
│   │   ├── drift_monitor.py                 # 漂移监控
│   │   └── theoretical_metrics.py           # ★v8.7 Lyapunov/相位/变异平衡
│   ├── observability/                       # ★v8.7 可观测性（structlog+pydantic Schema+OTel）
│   ├── attribution/                         # 业绩归因
│   ├── execution/                           # 执行引擎
│   ├── finance_agents/                      # AI分析师（宏观/动量/风险/情绪/价值/气象）
│   ├── risk/                                # 风险模块（kill_switch/risk_bus）
│   ├── risk_constraints.py                  # 硬性风险约束
│   ├── risk_budget_engine.py                # 风险预算引擎（MVSK高阶矩优化）
│   ├── drawdown_breaker.py                  # 回撤熔断器
│   ├── hedge_execution_engine.py            # 对冲执行引擎
│   ├── protective_put_engine.py             # 认沽期权保护引擎
│   ├── signal_fusion.py                     # 多源信号融合（9层，含气象因子）
│   ├── weather_data_adapter.py              # 气象数据适配器
│   ├── weather_factor_engine.py             # 气象因子计算引擎（7因子体系）
│   ├── supply_chain_graph.py                # 供应链关系图谱
│   ├── graph_data_source.py                 # 图数据源
│   ├── kill_switch.py                       # Kill Switch三级熔断
│   └── ...
│
├── v8.3_institutional/                      # 机构级基础设施与日度工作流
│   ├── daily_workflow/                      # 日度工作流（14阶段）
│   ├── hexin_broker/                        # 同花顺券商接口
│   └── data_pipeline/                       # 数据管道
│
├── ui/                                      # Streamlit可视化面板（14页）
├── ai_decision/                             # AI决策模块
├── ms_strategy/                             # 多策略框架
│   └── factors/gtja191_factors.py           # GTJA191因子纯Python实现（21因子）
│
├── tests/                                   # 测试套件
│   ├── unit/                                # 单元测试（13,399 passed）
│   ├── integration/                         # 集成测试
│   ├── e2e/                                 # 端到端测试
│   ├── perf/                                # ★v8.7 性能基准测试
│   ├── test_data_contracts.py               # 数据契约测试
│   └── test_regression_bugfixes.py          # 回归测试套件
│
├── scripts/                                 # 工具脚本
│   ├── run_p0_startup_check.py              # P0启动自检
│   ├── phase_b_progressive_enabler.py       # ★v8.7 Phase B渐进启用
│   ├── engineering_debt_gate.py             # ★v8.7 工程债门禁（D9/D10/D11）
│   └── ...
│
├── config/                                  # 全局配置
│   ├── positions.json                       # 持仓状态
│   ├── portfolio.yaml                       # 组合配置
│   ├── etf_option_subportfolio.yaml         # ★v8.7 ETF期权子组合配置
│   └── weather_symbols_mapping.yaml         # 气象因子标的地理映射
│
├── cairn/                                   # Project Cairn 知识管理
├── docs/                                    # 文档
├── requirements.txt                         # 生产依赖
├── ruff.toml                                # Ruff配置
├── bandit.yaml                              # Bandit安全扫描配置
├── mypy.ini                                 # mypy配置
├── pytest.ini                               # pytest配置
├── .env.example                             # 环境变量模板
└── CHANGELOG.md                             # 更新日志
```

---

## 开发工作流

### 代码质量门禁

项目集成 ruff + bandit + vulture + mypy + pylint 五位一体静态分析：

```bash
ruff check .                                                    # 代码风格（v8.7: 169违规，<300达标）
bandit -c bandit.yaml -lll -ii -r utils/ v8.3_institutional/src/  # 安全扫描（中危即FAIL）
vulture . --min-confidence 80                                   # 死代码检测
mypy institutional_pipeline_runner.py                          # 类型检查
pre-commit run --all-files                                      # Pre-commit钩子
```

### v8.7 工程债门禁（D9/D10/D11）

```bash
python scripts/engineering_debt_gate.py                         # 工程债门禁检查
python scripts/_check_coverage_trend.py --min-line-rate 0.80    # D9覆盖率门禁
python scripts/phase_b_progressive_enabler.py --check           # D11 Phase B shadow门禁
```

### 测试

```bash
# 两层测试策略
pytest tests/test_data_contracts.py -v -m contract              # 快测层（<1s）
pytest tests/test_regression_bugfixes.py -v -m "regression and not integration"  # 单元回归
pytest tests/test_regression_bugfixes.py -v -m "integration"    # 慢测层（~10s）

# 全量测试
pytest tests/unit/                                              # 单元测试（13,399 passed）
pytest tests/e2e/                                               # 端到端测试
pytest tests/perf/                                              # ★v8.7 性能基准

# 覆盖率
pytest --cov=. --cov-report=html                                # v8.7: line_rate=0.833
```

### P0启动自检钩子

```bash
# 安装
git config core.hooksPath githooks

# 手动测试
python scripts/pre_commit_check.py
python scripts/run_p0_startup_check.py --skip-datasource

# 紧急跳过（不推荐）
SKIP_P0_CHECK=1 git commit -m "hotfix: critical issue"
```

---

## 双机部署架构（Mac研究 + Windows云实盘）

> 完整文档见 `docs/ARCHITECTURE_Mac研究_Windows云实盘.md`

### 适用场景
MacBook（Apple Silicon）做研究/训练/回测，Windows云服务器做实盘下单，两机通过Tailscale + syncthing自动同步。

### P0自检双模式

| 检查维度 | Windows实盘模式 | Mac研究模式 |
|---------|---------|---------|
| C1关键文件 | 9个全检 | 7个（跳过hedge/risk_guard） |
| C2环境变量 | WIND_API_KEY必需 | 降级为可选 |
| C3数据源 | Wind+TDX+AKShare | 仅AKShare+yfinance |
| C7子模块 | HedgeExecutionEngine必需 | 跳过 |

### 云平台选择

| 平台 | 适用性 | 推荐度 |
|------|------|------|
| **阿里云ECS（华东2-上海）** | ✅ Windows Server 2022/固定IP/券商白名单 | 🥇 首选 |
| 腾讯云CVM（华南） | ✅ 同上，华南券商延迟更低 | 🥈 备选 |

**推荐配置**：阿里云`ecs.g7.xlarge`（4核16G）+ 100GB ESSD + 固定公网IP，**月费约¥315**

### 快速部署

```bash
# Mac端一键配置
chmod +x scripts/deploy/mac_setup.sh && ./scripts/deploy/mac_setup.sh

# Windows云服务器一键配置（管理员PowerShell）
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\deploy\windows_setup.ps1

# 日常使用（Mac远程触发）
quant-remote status      # 查询实盘状态
quant-remote premarket   # 触发盘前工作流
quant-remote eod         # 触发盘后
```

---

## 文档索引

| 文档 | 说明 |
|------|------|
| [CHANGELOG.md](CHANGELOG.md) | 版本更新日志（v8.6.14/v8.6.13详细变更） |
| [docs/v87_release_notes_20260820.md](docs/v87_release_notes_20260820.md) | ★v8.7 Release Notes（验收清单草案） |
| [cairn/ROADMAP.md](cairn/ROADMAP.md) | 路线图与进度 |
| [cairn/LOG.md](cairn/LOG.md) | 按时间顺序日志（最新在顶部） |
| [cairn/self-evolution-framework.md](cairn/self-evolution-framework.md) | 自我进化框架设计文档 |
| [cairn/etf-option-hedge-model.md](cairn/etf-option-hedge-model.md) | ★v8.7 ETF期权对冲子模型 |
| [cairn/mvsk-higher-moment-optimization.md](cairn/mvsk-higher-moment-optimization.md) | MVSK高阶矩优化 |
| [cairn/code-quality-industrial-gap-20260819.md](cairn/code-quality-industrial-gap-20260819.md) | 代码质量工业级差距审计 |
| [cairn/classic-theory-coverage-20260819.md](cairn/classic-theory-coverage-20260819.md) | 经典理论覆盖度审计 |
| [cairn/gnn-supply-chain-factor.md](cairn/gnn-supply-chain-factor.md) | GNN供应链产业链因子 |
| [.env.example](.env.example) | 环境变量模板 |
| [docs/](docs/) | 文档目录（含归档） |

---

## 关键约束

- **API密钥**：禁止硬编码，必须使用环境变量
- **数据真实性**：实时数据采集优先，历史数据兜底需标注日期，绝不使用模拟数据生成最新报告
- **降级原则**：始终优雅降级，不因上层数据源不可用而崩溃；`except: pass`必须附"降级语义"注释
- **风险控制**：所有风控guard检查默认False（fail-safe），防止静默失效
- **不可变性**：始终创建新对象，绝不原地修改（DataFrame用`.assign()`而非直接赋值）
- **配置一致性**：三份配置文件（portfolio.yaml / positions.json / system_config.json）必须保持一致
- **因子正交性**：因子库新增因子必须通过共线性检查，|ρ|>0.99须做正交化或残差化处理

---

## 版本历史

| 版本 | 日期 | 关键变更 |
|------|------|----------|
| **v8.7** | 2026-08-21 ~ 12-31 | ETF期权对冲子模型Phase1+S2/S3集成(TimesFM+价值纪律层) + Phase B观察期达标B1启用 + 三门禁D9覆盖率0.833 + 代码质量ruff 1139→211(08-21修复F401/BLE001/T201/硬编码路径) + 执行层自动闭环+双模型判断 + MVSK P1-P4生产就绪 + 可观测性structlog+pydantic+OTel + 经典理论覆盖度审计 + 过期文件清理(20个) |
| v8.6.15 | 2026-08-05 | U1-U5升级（时序IC/ICIR + 涨跌停/停牌 + 复权因子 + E2E测试）+ VolRegimeWeighter + 自我进化框架 + EOD阶段4.5B Shadow状态同步 |
| v8.6.14 | 2026-08-02 | 因子库对标GTJA191（11大类 + 共线修复12→0对）+ daily_trade_executor双Bug修复 + 安全合规加固 + 174处except:pass补降级注释 |
| v8.6.13 | 2026-08-01 | 气象因子引擎（7因子体系 + apizero→Open-Meteo降级链 + WeatherAgent第6位专家）+ Scrapling反爬 + TradingAgents-CN桥接 |
| v8.6.12 | 2026-07-31 | P0启动自检系统 + 数据契约测试 + 回归测试套件 + 两层测试策略 + pre-commit钩子 |
| v8.5 | 2026-07-24 | P0 Bug修复 + v8.5模块真实集成（9个核心模块） |
| v8.4及更早 | 2026-07-22之前 | （已归档，详见CHANGELOG.md和历史提交记录） |

> 详细变更见 [CHANGELOG.md](CHANGELOG.md)

---

## Git工作流（Conventional Commits）

```
feat(pipeline): 新增 V9 regime-specific 双模型
fix(risk): 修复回撤熔断器符号约定冲突
refactor(core): 提取信号融合公共函数
docs(readme): 更新 README 至 v8.7
```
