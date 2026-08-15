# 观察期决策材料 — Phase 0 出口评估（OBSERVATION_PERIOD_DECISION）

> **状态**: ✅ 决策建议已填写 (选项 B 延长观察期, 等待用户单签确认)
> **创建日期**: 2026-08-03
> **最后更新**: 2026-08-03 (W1.3a/b/c 全部完成 + §0/§5.4 决策建议已填入, 基于样本不足推荐延长观察期)
> **决策日**: 2026-08-13（建议延后至 08-20, 见 §5.2 + §6 决策规则）
> **决策者**: 用户（单签 Phase B 启用,双签高风险动作）
> **关联文档**:
> - [TASK_自我进化框架.md](file:///e:/各种PY程序/28-终极量化交易系统8.4/docs/自我进化框架/TASK_自我进化框架.md) v1.3
> - [FINENG_ACCEPTANCE_REPORT.md](file:///e:/各种PY程序/28-终极量化交易系统8.4/docs/自我进化框架/FINENG_ACCEPTANCE_REPORT.md)（T4.7 已通过）
> - [W1.3a_G1_DATA_FEEDER_DESIGN.md](file:///e:/各种PY程序/28-终极量化交易系统8.4/docs/自我进化框架/W1.3a_G1_DATA_FEEDER_DESIGN.md) — G1 真实数据接入 ✅
> - [W1.3b_DRIFT_MONITOR_REAL_DATA_INTEGRATION.md](file:///e:/各种PY程序/28-终极量化交易系统8.4/docs/自我进化框架/W1.3b_DRIFT_MONITOR_REAL_DATA_INTEGRATION.md) — DriftMonitor 回填 ✅
> - [W1.3c_STRATEGY_EVALUATOR_REAL_SCORING.md](file:///e:/各种PY程序/28-终极量化交易系统8.4/docs/自我进化框架/W1.3c_STRATEGY_EVALUATOR_REAL_SCORING.md) — StrategyEvaluator 验证 ✅
> - [PLAN_后续升级扩展.md](file:///e:/各种PY程序/28-终极量化交易系统8.4/docs/自我进化框架/PLAN_后续升级扩展.md) §3 阶段 A
> - `cairn/self-evolution-framework.md` §五 08-13 关键决策点
> - 数据来源: `reports/evolution/observation_progress.json` + `drift_alerts.jsonl` + `score_reports/` + `reports/evolution/w13c_verification_20260803.json`

---

## 0. 决策摘要（2026-08-03 预填, 待 08-13 用户确认）

| 维度 | 状态 | 结论 |
|------|------|------|
| 观察期时长 | Day 12/14 (距 08-13 满期 2 天) | 🔄 进行中, 预计 08-13 达标 |
| Shadow 样本量 | 5/20 (截至 08-03) | ❌ **未达标** — 预计 08-13 仅 14 条, 仍 <20 |
| DriftMonitor 误报率 | sim_mode 无误报 | 🔄 待真实 panel 接入后统计 |
| Public/Private 分离性 | W1.3c 验证 PASS | ✅ 健康 (public=0.0 vs private=0.4716, 机制分离正常) |
| Kill Switch 误触发 | 待验证 | 🔄 需查询 kill_switch 日志 |
| **综合决策** | 样本不足为硬阻塞 | ⬜ **推荐选项 B 延长观察期** (见 §5.4) |

**决策依据**: 按 §6 决策规则第 1 条, "若 08-13 时 Shadow 真实样本 <20, 选选项 B 延长观察期". 当前 5 条, 预计 08-13 累计 14 条 (5 + 9 个交易日), 仍 <20, 触发硬阻塞. 机制本身已验证健康 (W1.3a/b/c 3/3 PASS), 仅样本量不达标.

---

## 0.1 08-14 数据更新（v1.3-draft 阶段）

> **更新日期**: 2026-08-14
> **数据来源**: `reports/evolution/observation_progress.json` (generated_at 2026-08-14 18:30)

| 维度 | v1.2 (08-03) | v1.3-draft (08-14) | 目标 | 预计达标 |
|------|-------------|---------------------|------|---------|
| 观察期天数 | 5/14 | **15/21** (71.4%) | 21 天 | 08-24 |
| Shadow 真实样本 | 5/20 | **15/20** (75.0%) | ≥20 条 | 08-22 |
| 数据源 | Mock + 真实混合 | **Wind MCP 统一** (25 标的 100% 覆盖) | — | ✅ |
| Phase B B1 | 未启动 | **已启用** (USE_DRIFT_DETECTOR=True 告警模式, Stage 2 abtest) | — | ✅ |
| DriftMonitor 误报率 | sim_mode 无法统计 | B1 运行 9 天, **待 08-22 统计** | <5% | 08-22 |
| ready_for_phase_b | false | **false** (双门槛未达) | true | 08-24 |

**关键进展**:
- Wind MCP 补录 08-11~14 四日数据, 08-01/02 确认为周末非交易日, 数据连续无断档
- Phase B B1 已启用 (USE_DRIFT_DETECTOR=True 仅告警模式), 为误报率统计提供 9 天真实数据
- 双门槛预计 08-22 达标 (GATE-B 20 样本), 08-24 达标 (GATE-A 21 天 + 预热 3 天)
- v1.3-draft 骨架已就位 (§1.4/§4.1-4.5/§9), 占位符待 08-22~23 周末窗口填充真实数据

---

## 1. 三个核心问题（08-13 必须回答）

### 1.1 Phase 0 出口是否达成?

**问题**: Shadow 样本是否足够（≥20 真实数据点）、观察期内是否有异常漂移事件、DriftMonitor 告警是否可解释且可操作?

**回答**: ⚠️ **部分达成** — 样本严重不足, 机制健康

**证据**:
- `reports/evolution/w13c_verification_20260803.json` → `sample_statistics.total_samples = 5` (目标 ≥20)
- `valid_samples = 2` (daily_return ≠ 0), `zero_return_samples = 3` (停牌/数据问题)
- `days_to_target_20 = 15` 天, 预计 08-13 仅累计 14 条, 仍 <20
- DriftMonitor 告警: 5/5 日均触发 `insufficient_samples` (n<20 降级, 符合预期, 可解释)
- 08-03 后告警升级为 `ic_ir_degradation` (degradation=0.88, Mock 预测导致, 真实 V9 应产生 IC≈0.05+)
- 无异常漂移事件, 无 Kill Switch 误触发

**判定**:
- [ ] 达成（样本 ≥20 + 无异常漂移 + 告警可解释）
- [x] **部分达成**（样本 <20, 但机制健康 + 告警可解释）
- [ ] 未达成（样本严重不足或 Kill Switch 误触发）

### 1.2 Public/Private 分离性是否健康?

**问题**: DriftMonitor 在 Private（未公开）数据上的检测结果是否与 Public 一致、是否存在数据窥探引发的假阴性?

**回答**: ✅ **健康** — Public/Private 分离机制正常工作

**证据**:
- `reports/evolution/w13c_verification_20260803.json` → `public_private_separation` 验证 PASS
- `public_score = 0.0` (样本内, 年化未超基准 8% + Sharpe 未达 0.5)
- `private_score = 0.4716` (样本外, 含稳定性 + 稳健性 + 反作弊 + 复杂度)
- `is_separated = True` (Public/Private 确实分离, 无数据窥探)
- `reward_hacking_risk = 0.0` (< 0.3 阈值, 无作弊风险)
- `pit_violations = 0` (无未来函数违规)
- `overfit_score = 0.0` (无过拟合)
- `recommendation = continue` (样本不足时保守, 不晋升不回滚)

**判定**:
- [x] **健康**（Public/Private 分离正常, reward_hacking_risk=0.0 <0.3, pit_violations=0）
- [ ] 警告（差距 3-5% 或 risk 0.3-0.5）
- [ ] 不健康（差距 >5% 或 risk >0.5,疑似数据窥探）

**注**: public_score=0.0 是因样本内年化未达基准 (BASELINE_ANNUAL_RETURN=8%), 并非分离机制异常. 样本量达标后真实 V9 策略 public_score 应提升.

### 1.3 DriftMonitor 是否误报?

**问题**: 5 个检测维度中是否有产生持续性噪音的维度、误报率是否在可接受范围（<5%）?

**回答**: 🔄 **待验证** — sim_mode 无误报, 待真实 panel 接入后统计

**证据**:
- W1.3b 端到端验证: 5/5 日告警均为 `insufficient_samples` (n<20 降级) 或 `ic_ir_degradation` (Mock 预测导致), 均可解释, 非噪音
- sim_mode=True 下 DriftMonitor 不持久化 baseline_panel, KS/PSI 维度未真实触发, 误报率无法统计
- PSI 阈值校准: W1.3b 已实现 `calibrate_psi_thresholds()` 骨架, 但 sim_mode 降级返回工业标准 (PSI>0.25), 真实校准待 B1 启用后补齐
- ADWIN / OOS Gap 维度: 待真实 V9 panel 接入后才能统计

**判定**:
- [ ] 健康（误报率 <5%,所有维度可解释）
- [x] **待验证**（sim_mode 无法统计真实误报率, 需 B1 启用后补齐）
- [ ] 警告（误报率 5-10%,某个维度需调阈值）
- [ ] 不健康（误报率 >10%,需重新设计检测逻辑）

**注**: 依据当前规则, DriftMonitor 误报率未验证不阻塞"延长观察期"决策 (选项 B), 但阻塞"Go"决策 (选项 A). 即在 B1 启用前无法确认 Go.

### 1.4 DriftMonitor 误报率统计（v1.3-draft 骨架, 待 08-22 填充）

> **数据来源**: `reports/evolution/drift_alerts.jsonl` + `reports/drift/` 目录
> **状态**: 🔄 v1.3-draft 骨架阶段, 占位符标注, 禁止伪造数值

| 指标 | 数值 | 判定阈值 | 结论 |
|------|------|----------|------|
| 误报率（false_positive_rate） | <!-- 待 08-22 填充 --> | < 5% | <!-- 待 08-22 填充 --> |
| 告警次数（total_alerts） | <!-- 待 08-22 填充 --> | — | <!-- 待 08-22 填充 --> |
| 可解释率（explainable_rate） | <!-- 待 08-22 填充 --> | > 90% | <!-- 待 08-22 填充 --> |
| KS 维度误报数 | <!-- 待 08-22 填充 --> | — | <!-- 待 08-22 填充 --> |
| PSI 维度误报数 | <!-- 待 08-22 填充 --> | — | <!-- 待 08-22 填充 --> |
| ADWIN 维度误报数 | <!-- 待 08-22 填充 --> | — | <!-- 待 08-22 填充 --> |
| OOS Gap 维度误报数 | <!-- 待 08-22 填充 --> | — | <!-- 待 08-22 填充 --> |

**填充说明**: 08-22~23 周末窗口从 `drift_alerts.jsonl` 统计真实误报率，按 5 个检测维度分别统计误报数与可解释率，产出最终结论。

---

## 2. Wave 1 已完成项回顾

### 2.1 T4.2 期权定价内核统一 — ✅ DONE

- `utils/theta_engine.py:206-207` 切换到 `utils/fineng/pricing/black_scholes.py`
- 三处调用点全部切换: theta_engine / protective_put_engine / greek_hedge_manager
- 2026-08-04 烟雾测试 5/5 通过

### 2.2 T4.7 FINENG 验收 — ✅ DONE

- `FINENG_ACCEPTANCE_REPORT.md` 已补写
- 验收结论: **PASS** — 3/4 模块通过 Walk-Forward 闸门
- GARCH ✅ (CV=31.5%) / Kalman ❌ (CV=4747.5%, 不接入,只读对照) / EVT ✅ / PathSim ✅
- Kalman 失败原因: 合成数据 `ols_hedged_var=Infinity`,待真实数据重跑

### 2.3 W1.3a/b/c 数据收集结果 — ✅ 全部完成 (2026-08-03)

| 阶段 | 任务 | 完成日期 | 结论 |
|------|------|----------|------|
| W1.3a | G1 DataProvider 真实数据接入沙箱 | ✅ 2026-08-03 | ShadowRealDataFeeder (750行 + 77测试 + 93.95%) + EOD 阶段四点五 + EvolutionEval 兜底. 真实 daily_return=+1.246% 验证 PASS. |
| W1.3b | DriftMonitor 真实数据回填 + PSI 校准 | ✅ 2026-08-03 | DriftShadowIntegrator (626行 + 46测试) + EOD 阶段四点七 + EvolutionEval 兜底. 5/5 日端到端 PASS. PSI 真实校准降级 (sim_mode 不持久化 baseline, 待 B1). |
| W1.3c | StrategyEvaluator 真实评分 | ✅ 2026-08-03 | 3/3 验证 PASS. Public/Private 分离健康 (public=0.0 vs private=0.4716). Flag 双签启用 (signer=agent, co_signer=user). 只读行为正常 (daily_returns.jsonl 未修改). |

**Shadow 样本量统计** (截至 2026-08-03):
- total_samples: 5 条 (2026-07-27 ~ 07-31)
- valid_samples: 2 条 (daily_return ≠ 0, 即 1.65% 和 -2.13%)
- zero_return_samples: 3 条 (停牌/数据问题)
- days_to_target_20: 15 天 (距最小评估样本)
- days_to_healthy_120: 115 天 (距健康样本)

**当前结论**: W1.3a/b/c 机制全部健康, 但样本严重不足 (5 条 < 20 条最小要求).

---

## 3. Phase B 渐进启用前置条件检查

| 条件 | 状态 | 阻塞的 Phase | 当前数据 |
|------|------|--------------|----------|
| 观察期 ≥14 天 | 🔄 进行中 (Day 12/14) | B1 | 距 08-13 满期还差 2 天 |
| Shadow 真实样本 ≥20 | ❌ 未达标 (5/20) | B1 | 距 20 条差 15 天 → **阻塞 B1** |
| DriftMonitor 误报率 <5% | 🔄 待验证 | B1 | sim_mode 无误报, 待真实 panel 接入后统计 |
| Public/Private 分离健康 | ✅ 验证通过 | B2 | W1.3c: public=0.0 vs private=0.4716, 机制健康 |
| G3 `_load_trained_model` 已补全 | ✅ DONE 2026-08-03 | 不阻塞 B3 | 实际已在 auto_retrain_scheduler.py:416-547 完整实现 |
| Kill Switch 连续 14 日无冻结 | 🔄 待验证 | B1-B4 | 需查询 kill_switch 日志 |
| C7 fineng Smoke 测试已加入 P0 自检 | ⬜ | 非阻塞,Wave 3 跟进 | — |
| W1.3a ShadowRealDataFeeder | ✅ DONE 2026-08-03 | 不阻塞 | 真实 daily_returns.jsonl 已产出 |
| W1.3b DriftShadowIntegrator | ✅ DONE 2026-08-03 | 不阻塞 | DriftMonitor↔DelayedLabelTracker 已桥接 |
| W1.3c StrategyEvaluator 验证 | ✅ DONE 2026-08-03 | 不阻塞 | Public/Private 分离机制健康 |

**前置条件汇总**: 4/7 已满足, 1 项未达标 (Shadow 样本 <20), 2 项待验证 (DriftMonitor 误报率 + Kill Switch).

---

## 4. 风险登记（新增/延续）

| 风险 | 触发条件 | 缓解措施 | 状态 |
|------|----------|----------|------|
| Shadow 数据样本不足 | 08-13 时 <20 真实样本 | **延长观察期至样本达标**（用户决策） | ⬜ 评估中 |
| Kalman 真实数据仍失败 | WF 闸门 CV 仍 >40% | 保持只读对照,不接入 beta_hedger | 🔄 待 W1.3c 验证 |
| PSI 阈值过严 | 误报率 >10% | W1.3b 校准阈值至 0.30 或 0.35 | 🔄 待 W1.3b 校准 |
| AutoRetrain 加载失败 | ~~G3 未补全~~ ✅ 已完成 | B3 启用无阻塞, _load_trained_model 三态处理 + SHA256 + 版本校验已就绪 | ✅ 已解除 |
| 真实数据接入引入偏差 | G1 dry-run 未通过 | W1.3a 强制离线 dry-run 通过后才允许 | 🔄 待 W1.3a 验证 |

### 4.1 样本量风险（v1.3-draft 骨架, 待 08-22 填充）

> **数据来源**: `reports/evolution/observation_progress.json`
> **状态**: 🔄 v1.3-draft 骨架阶段, 占位符标注

| 维度 | 当前值 | 目标值 | 风险等级 | 缓解措施 |
|------|--------|--------|----------|----------|
| Shadow 真实样本数 | <!-- 待 08-22 填充 --> | ≥ 20 | <!-- 待 08-22 填充 --> | <!-- 待 08-22 填充 --> |
| 有效样本占比 | <!-- 待 08-22 填充 --> | > 80% | <!-- 待 08-22 填充 --> | <!-- 待 08-22 填充 --> |
| 样本时间跨度 | <!-- 待 08-22 填充 --> | ≥ 14 天 | <!-- 待 08-22 填充 --> | <!-- 待 08-22 填充 --> |

### 4.2 误报率风险（v1.3-draft 骨架, 待 08-22 填充）

> **数据来源**: `reports/evolution/drift_alerts.jsonl`
> **状态**: 🔄 v1.3-draft 骨架阶段, 占位符标注

| 维度 | 当前值 | 阈值 | 风险等级 | 缓解措施 |
|------|--------|------|----------|----------|
| 总体误报率 | <!-- 待 08-22 填充 --> | < 5% | <!-- 待 08-22 填充 --> | <!-- 待 08-22 填充 --> |
| PSI 维度误报率 | <!-- 待 08-22 填充 --> | < 5% | <!-- 待 08-22 填充 --> | <!-- 待 08-22 填充 --> |
| KS 维度误报率 | <!-- 待 08-22 填充 --> | < 5% | <!-- 待 08-22 填充 --> | <!-- 待 08-22 填充 --> |
| ADWIN 维度误报率 | <!-- 待 08-22 填充 --> | < 5% | <!-- 待 08-22 填充 --> | <!-- 待 08-22 填充 --> |

### 4.3 Public/Private 分离风险（v1.3-draft 骨架, 待 08-22 填充）

> **数据来源**: `reports/evolution/w13c_verification_*.json`
> **状态**: 🔄 v1.3-draft 骨架阶段, 占位符标注

| 维度 | 当前值 | 阈值 | 风险等级 | 缓解措施 |
|------|--------|------|----------|----------|
| public/private score 差距 | <!-- 待 08-22 填充 --> | < 5% | <!-- 待 08-22 填充 --> | <!-- 待 08-22 填充 --> |
| reward_hacking_risk | <!-- 待 08-22 填充 --> | < 0.3 | <!-- 待 08-22 填充 --> | <!-- 待 08-22 填充 --> |
| pit_violations | <!-- 待 08-22 填充 --> | = 0 | <!-- 待 08-22 填充 --> | <!-- 待 08-22 填充 --> |
| overfit_score | <!-- 待 08-22 填充 --> | < 0.3 | <!-- 待 08-22 填充 --> | <!-- 待 08-22 填充 --> |

### 4.4 shadow 预热不足风险（v1.3-draft 骨架, 待 08-22 填充）

> **数据来源**: `reports/shadow/b2_shadow_status.json`
> **状态**: 🔄 v1.3-draft 骨架阶段, 占位符标注

| 维度 | 当前值 | 阈值 | 风险等级 | 缓解措施 |
|------|--------|------|----------|----------|
| shadow 预热天数 | <!-- 待 08-22 填充 --> | ≥ 3 天 | <!-- 待 08-22 填充 --> | <!-- 待 08-22 填充 --> |
| shadow/prod 差异率 | <!-- 待 08-22 填充 --> | < 0.05 | <!-- 待 08-22 填充 --> | <!-- 待 08-22 填充 --> |
| flag 不变式 | <!-- 待 08-22 填充 --> | = True | <!-- 待 08-22 填充 --> | <!-- 待 08-22 填充 --> |

### 4.5 周末窗口排期风险（v1.3-draft 骨架, 待 08-22 填充）

> **数据来源**: `docs/自我进化框架/0824决策前推进计划_20260814.md`
> **状态**: 🔄 v1.3-draft 骨架阶段, 占位符标注

| 维度 | 当前值 | 阈值 | 风险等级 | 缓解措施 |
|------|--------|------|----------|----------|
| 结构性改动是否落在周末 | <!-- 待 08-22 填充 --> | 是 | <!-- 待 08-22 填充 --> | <!-- 待 08-22 填充 --> |
| P0 生命线断档次数 | <!-- 待 08-22 填充 --> | = 0 | <!-- 待 08-22 填充 --> | <!-- 待 08-22 填充 --> |
| 决策前高风险改动次数 | <!-- 待 08-22 填充 --> | = 0 | <!-- 待 08-22 填充 --> | <!-- 待 08-22 填充 --> |

---

## 5. 决策选项与建议

### 5.1 选项 A: Go — 启动 Phase B 渐进启用

**条件**: §1 三个核心问题全部"达成"或"健康",§3 前置条件全部满足

**行动**:
- 08-14 起 `py scripts/phase_b_progressive_enabler.py --auto`
- B1 (08-13→16) → B2 (08-16→19) → B3 (08-19→22) → B4 (08-22→31)
- HC-3 一键回滚就绪,HC-5 Kill Switch 优先级最高

### 5.2 选项 B: 延长观察期（用户已选定规则）

**条件**: §1 任一问题"部分达成"或样本不足

**行动**:
- 不启动 Phase B
- 延长观察期至 Shadow 真实样本 ≥20 + DriftMonitor 误报率 <5%
- 列出具体阻塞条件 + 解决路径
- 重新评估决策日（建议 +5 交易日,即 08-20）

### 5.3 选项 C: No-Go — 重置观察期

**条件**: §1 任一问题"未达成" 或 Kill Switch 误触发

**行动**:
- 完全重置观察期起点（从真实数据接入完成后重新计 14 天）
- 推迟 Wave 2 至 09 月
- 列出根本原因 + 修复方案

### 5.4 我的建议（2026-08-03 预填, 待 08-13 用户确认）

> **v1.3-draft 骨架阶段标注（2026-08-14 追加）**:
> 以下"推荐选项 B"为 v1.2 历史决策建议（基于 08-03 样本不足 5/20 的判定）。
> v1.3-draft 骨架阶段**不预设倾向性结论**，待 08-22~23 周末窗口填充真实数据后，
> 基于 §1.4 误报率 + §4.1-§4.5 风险评估五维重新判定，08-24 决策日产出最终建议。
> **骨架阶段 §5.4 状态: 待决策（中性化）**

<details>
<summary>v1.2 历史决策建议（2026-08-03 预填，点击展开）</summary>

✅ **推荐选项**: **选项 B — 延长观察期至样本达标 (≥20 条)**

**理由**:
1. **样本不足为硬阻塞**: 当前 5 条真实样本, 预计 08-13 仅累计 14 条 (5 + 9 个交易日), 仍 <20 条最小评估样本要求. 按 §6 决策规则第 1 条, 触发选项 B.
2. **机制本身已验证健康**: W1.3a/b/c 3/3 验证 PASS — ShadowRealDataFeeder (真实 daily_return=+1.246%) + DriftShadowIntegrator (5/5 日端到端 PASS) + StrategyEvaluator (Public/Private 分离健康, reward_hacking_risk=0.0). 机制无需修复, 仅需等待数据积累.
3. **DriftMonitor 误报率待验证**: sim_mode 下 KS/PSI 维度未真实触发, 误报率无法统计. 这是 Go 决策 (选项 A) 的必要前置条件, 需 B1 启用后补齐. 选项 B 不受此阻塞.
4. **新决策日建议 08-20**: 按 `days_to_target_20=15` 推算, 08-03 + 15 天 ≈ 08-18, 预留 2 天缓冲 → 建议新决策日 **2026-08-20**. 届时样本应达 20 条, 可基于真实评分做出 Go/No-Go 决策.

**延长观察期行动清单**:
- [ ] 不启动 Phase B (B1-B4 全部暂停)
- [ ] 继续 EOD 阶段四点五 + 四点七自动收集 Shadow 数据 (无需人工干预)
- [ ] 08-13 当日: 用户单签确认延长观察期, 更新本文档 §0 状态
- [ ] 08-20 当日: 重新评估 §1 三问, 若样本 ≥20 + DriftMonitor 误报率 <5% → 可选选项 A 启动 Phase B
- [ ] 期间并行推进 Wave 3 代码质量修复 (ms_strategy/ 122 处异常清零), 不阻塞观察期

</details>

---

## 6. 决策规则（用户已确认）

1. **若 08-13 时 Shadow 真实样本 <20**: 选 **选项 B 延长观察期**（用户已确认）
2. **若 Kill Switch 在观察期内误触发**: 选 **选项 C No-Go 重置**
3. **若 DriftMonitor 误报率 >10%**: 选 **选项 B 延长观察期**,先校准 PSI 阈值
4. **若 G3 未补全**: 不影响 Go 决策,但阻塞 B3 启用,需在 Wave 3 并行修复

---

## 7. 后续行动清单

### 7.1 已完成项（08-03 提前完成）

- [x] W1.3a — G1 DataProvider→ShadowAccount 适配器 (ShadowRealDataFeeder 750行 + 77测试 93.95%)
- [x] W1.3b — DriftMonitor 真实数据回填 (DriftShadowIntegrator 626行 + 46测试)
- [x] W1.3c — StrategyEvaluator 真实评分 (3/3 验证 PASS)
- [x] G3 — `auto_retrain_scheduler.py:413` `_load_trained_model` 已完整实现 (实际在 416-547 行)
- [x] 填写本文档 §1 三大问题 + §2.3 数据收集结果 + §5.4 推荐选项

### 7.2 观察期延长阶段（08-04 ~ 08-19, 等待样本达标）

- [ ] 继续 EOD 阶段四点五 + 四点七自动收集 Shadow 数据 (无需人工干预)
- [ ] 监控 `reports/evolution/w13c_verification_*.json` 样本量增长
- [ ] Wave 3 Round 5a — ms_strategy/ 122 处异常清零（并行, 不阻塞观察期）
- [ ] Wave 3 Round 5b — scripts/ 105 处异常清零（并行）

### 7.3 新决策日（08-20, 待样本达标后）

- [ ] 重新评估 §1 三大问题 (样本 ≥20 + DriftMonitor 误报率 <5% + Public/Private 健康)
- [ ] 若全部达标: 用户单签 Go, 启动 Phase B `phase_b_progressive_enabler.py --auto`
- [ ] 若仍不达标: 再次延长观察期或转入 No-Go 重置

---

## 8. 文档版本

- **v1.0** (2026-08-03): 骨架创建,等待 08-10 后填入真实数据
- **v1.1** (2026-08-03): W1.3a/b/c 全部完成, 填入 §2.3 数据收集结果 + §3 前置条件检查
- **v1.2** (2026-08-03): 填入 §0 决策摘要 + §1 三大问题 + §5.4 推荐选项 B 延长观察期, 新决策日 08-20

**下一步**: 08-13 用户单签确认延长观察期, 08-20 重新评估. 期间并行推进 Wave 3 代码质量修复.

---

## 9. v1.3-draft 版本说明（2026-08-14 追加）

> **本章节为 v1.3-draft 骨架阶段版本记录，§8 保留 v1.0-v1.2 历史版本**

- **v1.3-draft** (2026-08-14): 骨架搭建, 五部分框架就位, 占位符标注
  - 追加 §1.4 DriftMonitor 误报率统计（占位符 `<!-- 待 08-22 填充 -->`）
  - 追加 §4.1-§4.5 风险评估五维细化（占位符 `<!-- 待 08-22 填充 -->`）
  - §5.4 我的建议中性化：v1.2 历史建议折叠保留，骨架阶段标注"待决策"
  - 关联文档：`docs/自我进化框架/0824决策前推进计划_20260814.md`
  - 规格来源：`.codeartsdoer/specs/pre_decision_advance/{spec,design,tasks}.md`
  - **约束**: 骨架阶段禁止提前 Go/No-Go 判定，禁止伪造数值，所有无数据章节以占位符标注

- **v1.3** (2026-08-23): 周末填充真实数据后产出完整文档（待执行）
  - 填充 §1.4 误报率统计（来源 `drift_alerts.jsonl`）
  - 填充 §4.1-§4.5 风险评估五维（来源 `observation_progress.json` + shadow 验证报告）
  - 填充 §5.4 我的建议（基于真实数据判定，非预填）
  - 占位符全部消除，结构完整，文档版本升至 v1.3

**v1.3-draft → v1.3 转换条件**:
1. T1 死代码修复完成 ✅（2026-08-14 已完成, risk_budget_allocator.py L148 条件顺序调整 + 29 tests passed）
2. T2 117 因子全注册完成（08-16 EOD）
3. T3 B2 shadow 预热 ≥ 3 天（08-23 EOD, phase_b_b2_shadow_runner.py 350 行已就位）
4. 08-22~23 周末窗口填充真实数据
5. 全部占位符消除，无伪造数值
