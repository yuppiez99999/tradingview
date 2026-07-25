# TASK — Vibe-Trading 因子分析项目（6A 阶段 3：Atomize）

> 原子化任务清单：8 个任务，按 P0->P1->P2 优先级排序，含输入/输出/验收标准。
> 创建日期：2026-07-25

## 任务总览

| ID | 优先级 | 任务 | 依赖 | 预计 |
|----|--------|------|------|------|
| T1 | P0 | DSRValidator (Gate 3) | - | 4h |
| T2 | P0 | RegimeConditioner | - | 4h |
| T3 | P0 | ShadowAccount | T1,T2 | 6h |
| T4 | P1 | FactorCommittee | T1,T2,T3 | 6h |
| T5 | P1 | PipelineOrchestrator | T1-T4 | 4h |
| T6 | P2 | CapacityAnalyzer | - | 3h |
| T7 | P2 | FactorKillSwitch | T1 | 3h |
| T8 | - | 端到端集成测试 | T1-T7 | 4h |

---

## T1. DSRValidator（Gate 3 防过拟合）P0

输入：factor_returns_series (List[float]), n_trials (int, 已测试因子总数)
输出：DSRResult {dsr_value, n_trials, is_overfit, threshold}
验收标准：
- 实现 Bailey & Lopez de Prado 2014 DSR 公式
- n_trials < 5 时返回 is_overfit=True
- 单元测试：已知 Sharpe 下 DSR 计算正确
- 位置：research/vibe_trading_factor_analysis/validators/dsr_validator.py

## T2. RegimeConditioner（E3 Regime条件化）P0

输入：factor_values_history, benchmark_returns (510300), forward_returns_history
输出：RegimeResult {per_regime_ic_ir, min_regime_ic_ir, pass, regime_tag}
验收标准：
- 4 regime 划分（bull/bear/choppy/rebound）基于 510300 MA60
- 每 regime 计算 IC_IR
- 全 regime IC_IR > 0.2 -> pass；否则 regime_tagged
- 直击 project_memory V6.2 bull regime 失败根因
- 位置：research/vibe_trading_factor_analysis/validators/regime_conditioner.py


## T3. ShadowAccount（Stage 7 影子账户）P0

输入：factor (CandidateFactor), price_data (60日), portfolio_config
输出：ShadowResult {live_dsr, max_drawdown, daily_pnl, pass}
验收标准：
- 模拟 60 日纸面交易，每日记录 PnL
- 60 日满复算 DSR（调用 T1）
- live_DSR > 0 AND max_drawdown < 15% -> pass
- 依赖：T1(DSR), T2(Regime)
- 位置：research/vibe_trading_factor_analysis/shadow/shadow_account.py

## T4. FactorCommittee（Stage 8 多Agent决策治理）P1

输入：factor + 全 Gate 报告 + ShadowResult
输出：CommitteeVerdict {scores, avg_score, vetoes, approved, rationale}
验收标准：
- 5 专家 Agent：AlphaAgent/RiskAgent/ExecutionAgent/EconomicAgent/CapacityAgent
- ChairAgent 聚合：avg_score >= 7 且无否决 -> approved
- 各 Agent 独立评分（0-10）+ 否决权
- 审计日志：记录每 Agent 评分与理由
- 依赖：T1,T2,T3
- 位置：research/vibe_trading_factor_analysis/committee/factor_committee.py


## T5. PipelineOrchestrator（8级流水线编排）P1

输入：price_data, fundamentals, portfolio_config
输出：PipelineResult {stages_passed, final_status, audit_trail}
验收标准：
- 状态机：candidate -> g1 -> g2 -> g3 -> g4 -> enhanced -> shadow -> committee -> approved/rejected
- 每步状态持久化到 reports/{batch_id}/pipeline_state.json
- 失败降级：单步失败不阻断主流程，记录失败原因
- 依赖：T1-T4
- 位置：research/vibe_trading_factor_analysis/pipeline/pipeline_orchestrator.py

## T6. CapacityAnalyzer（E1 容量分析）P2

输入：factor_values, adv_data, float_mcap, portfolio_value
输出：CapacityResult {capacity_usd, pass, participation_ratio}
验收标准：
- capacity_usd = ADV_median * 5% * (1 - turnover_penalty)
- capacity_usd >= portfolio_value * 2% -> pass
- 位置：research/vibe_trading_factor_analysis/validators/capacity_analyzer.py

## T7. FactorKillSwitch（E5 实盘防线）P2

输入：approved_factor, daily_ic_stream
输出：KillSwitchStatus {status, consecutive_fail_days, auto_disabled}
验收标准：
- 连续 5 日 IC < 0.02 -> degraded
- 连续 10 日 IC < 0 -> auto_disabled
- 依赖：T1
- 位置：research/vibe_trading_factor_analysis/safety/factor_kill_switch.py

## T8. 端到端集成测试

输入：13 个候选因子 + 真实历史数据
输出：首因子准入全流程演示报告
验收标准：
- 至少 1 个因子通过全 8 级流水线
- pipeline_audit.json 完整记录每步
- 烟雾测试通过：python tests/_e2e_test.py
- 依赖：T1-T7

---

## 执行顺序（关键路径）

T1, T2 (并行) -> T3 -> T4 -> T5 -> T8
T6, T7 可与 T3-T5 并行

## 完成定义（DoD）

- 8 个任务全部完成且单元测试通过
- 至少 1 个候选因子完成全流程准入演示
- ARCHITECTURE 与实现一致
- 无 P0 级 bug 遗留
