# ARCHITECTURE — Vibe-Trading 因子分析项目（6A 阶段 2：Architect）

> 技术架构蓝图：8 级因子流水线 + 7 个核心组件，以世界顶级对冲基金标准设计。
> 创建日期：2026-07-25

## 1. 决策摘要（对冲基金视角）

| 决策 | 选择 | 依据 |
|------|------|------|
| D1 正交性 | |corr| < 0.5 严格 | AQR/Two Sigma；>0.5 即 >25% 共享方差 |
| D2 IC 窗口 | 双窗口 60d告警/120d准入 | Citadel 模式；快衰减检测+稳准入 |
| D3 影子准入 | 必须先过 Gate3(DSR) | Renaissance；多重检验偏差防线 |
| D4 审批 | 多 Agent 委员会 | 5 专家+Chair 各否决权 |
| E1 容量 | ≥组合2% | 因子须能承载资本 |
| E3 Regime | 全regime有效 | 直击 bull regime 失败根因 |
| E5 KillSwitch | 5日IC低于阈值→禁用 | 实盘防线 |

## 2. 8 级因子流水线

```
Vibe-Trading 450+ 候选因子
        │
        ▼
[Stage 0] DataGate          真实OHLCV + 质量校验
        │
        ▼
[Stage 1] Compute            VibeTradingFactorAdapter (13→450+)
        │
        ▼
[Stage 2] Gate1 正交性       |corr|<0.5 ──fail──→ MonitoringPool
        │ pass
        ▼
[Stage 3] Gate2 IC稳定性     60d告警 + 120d准入, IC_IR≥0.3
        │ pass
        ▼
[Stage 4] Gate3 防过拟合     DSR n_trials≥5, DSR>0 ──fail──→ Reject(overfit)
        │ pass
        ▼
[Stage 5] Gate4 经济逻辑     学术依据 + A股适配
        │ pass
        ▼
[Stage 6] Enhancement        CapacityAnalyzer + RegimeConditioner
        │ pass
        ▼
[Stage 7] ShadowAccount      60日纸面交易, DSR复算
        │ pass (live DSR>0)
        ▼
[Stage 8] FactorCommittee    5专家Agent + Chair, 均分≥7 无否决
        │ approved
        ▼
[Production] utils/alpha_factor_library.py (origin标签+审计)
```


## 3. 核心架构原则

1. 纵深防御：4道Gate + 影子账户 + 委员会 = 6道独立防线
2. 多重检验纪律：450+候选下，DSR是唯一可信的过拟合防线（Renaissance信条）
3. Regime感知：所有因子须在 bull/bear/choppy 全regime验证（直击 V6.2 bull regime 失败根因）
4. 只读隔离：候选因子绝不接入交易决策链路，直至委员会批准
5. 可审计可降级：每步持久化 + 失败降级，不阻断主流程

## 4. 核心组件设计

### 4.1 DSRValidator（Gate 3 防过拟合）

职责：计算 Deflated Sharpe Ratio，调整多重检验偏差

公式（Bailey and Lopez de Prado 2014）：
- DSR = Phi_inv(1 - exp(-e * (SR_observed - E[max|SR|]) * sqrt(T)))
- E[max|SR|] = sqrt(2 * ln(n_trials)) * std(SR)  (n_trials = 已测试因子数)
- 通过条件：DSR > 0 且 n_trials >= 5

接口：
- 输入：factor_returns_series, n_trials
- 输出：DSRResult {dsr_value, n_trials, is_overfit, threshold}

位置：research/vibe_trading_factor_analysis/validators/dsr_validator.py


### 4.2 CapacityAnalyzer（E1 容量分析）

职责：估算因子可承载的美元容量

公式：
- capacity_usd = ADV_median * participation_cap(5%) * (1 - turnover_penalty)
- participation_cap = min(5% ADV, 0.05 * float_mcap_daily)
- turnover_penalty = 1 - min(turnover, 0.5)

通过条件：capacity_usd >= portfolio_value * 2%

接口：
- 输入：factor_values, adv_data, float_mcap, portfolio_value
- 输出：CapacityResult {capacity_usd, pass, participation_ratio}

位置：research/vibe_trading_factor_analysis/validators/capacity_analyzer.py

### 4.3 RegimeConditioner（E3 Regime条件化）

职责：测试因子在 bull/bear/choppy/rebound 全 regime 表现

Regime 划分（基于 510300 MA60，对齐 project_memory）：
- bull: 510300 > MA60 且 MA60 上行
- bear: 510300 < MA60 且 MA60 下行
- choppy: 510300 围绕 MA60 波动 +-3%
- rebound: 510300 从 bear 反弹 >5%

通过条件：IC_IR > 0.2 在每个 regime（或 regime_tagged 显式标注仅特定 regime 适用）

接口：
- 输入：factor_values_history, benchmark_returns, forward_returns_history
- 输出：RegimeResult {per_regime_ic_ir, min_regime_ic_ir, pass, regime_tag}

位置：research/vibe_trading_factor_analysis/validators/regime_conditioner.py


### 4.4 ShadowAccount（Stage 7 影子账户）

职责：纸面交易 60 日，复算 DSR，验证实盘信号有效性

流程：
1. 接收通过 Gate1-4 + Enhancement 的因子
2. 模拟每日交易，记录 PnL
3. 60 日满后复算 DSR
4. live DSR > 0 且最大回撤 < 15% -> 通过

通过条件：live_DSR > 0 AND max_drawdown < 15%

接口：
- 输入：factor, price_data (60日), portfolio_config
- 输出：ShadowResult {live_dsr, max_drawdown, daily_pnl, pass}

位置：research/vibe_trading_factor_analysis/shadow/shadow_account.py

### 4.5 FactorCommittee（Stage 8 多Agent决策治理）

职责：5 专家 Agent + Chair 投票审批因子准入

Agent 组成（各 0-10 评分，均有否决权）：
- AlphaAgent：验证 IC/IC_IR/DSR（基于历史）
- RiskAgent：验证正交性 + 风险贡献 + 相关性压力测试
- ExecutionAgent：验证容量/换手率/滑点
- EconomicAgent：验证经济逻辑 + A股适配
- CapacityAgent：验证 regime 全场景有效性
- ChairAgent：聚合评分，avg >= 7 且无否决 -> 批准

通过条件：avg_score >= 7 AND no_veto

接口：
- 输入：factor + 全 Gate 报告 + 影子账户结果
- 输出：CommitteeVerdict {scores, avg_score, vetoes, approved, rationale}

位置：research/vibe_trading_factor_analysis/committee/factor_committee.py


### 4.6 FactorKillSwitch（E5 实盘防线）

职责：实时监控已准入因子，连续 5 日 IC < 阈值 -> 自动禁用

逻辑：
- 每日重算 IC（60日滚动）
- 连续 5 日 IC < 0.02 -> 标记 degraded
- 连续 10 日 IC < 0 -> 自动禁用并通知

位置：research/vibe_trading_factor_analysis/safety/factor_kill_switch.py

### 4.7 FactorPipelineOrchestrator（Stage 0-8 编排）

职责：状态机编排 8 级流水线，持久化每步状态

状态机：candidate -> g1_passed -> g2_passed -> g3_passed -> g4_passed -> enhanced -> shadow_running -> committee_pending -> approved/rejected

接口：
- 输入：price_data, fundamentals, portfolio_config
- 输出：PipelineResult {stages_passed, final_status, audit_trail}

位置：research/vibe_trading_factor_analysis/pipeline/pipeline_orchestrator.py


## 5. 数据流与目录结构

- adapters/ - VibeTradingFactorAdapter (Stage 1)
- validators/ - DSRValidator, CapacityAnalyzer, RegimeConditioner, FactorValidator
- shadow/ - ShadowAccount (Stage 7)
- committee/ - FactorCommittee (Stage 8)
- safety/ - FactorKillSwitch (E5)
- pipeline/ - PipelineOrchestrator
- metadata/ - factor_mapping.json, candidate_factors_catalog.json
- reports/ - 每批次审计报告
- tests/ - 单元测试 + 烟雾测试

## 6. 与现有系统集成边界

- 输入：utils/alpha_factor_library.py（现有因子，正交性比对）
- 输出：通过全流程的因子，经委员会批准后写入 utils/alpha_factor_library.py（带 origin 标签）
- 隔离：候选因子不接入 signal_fusion / portfolio_optimizer / trading
- 审计：每批次生成 reports/{batch_id}/pipeline_audit.json

## 7. 实施优先级

- P0（阶段二必做）：DSRValidator, RegimeConditioner, ShadowAccount
- P1（阶段二应做）：FactorCommittee, PipelineOrchestrator
- P2（阶段二可选）：CapacityAnalyzer, FactorKillSwitch
