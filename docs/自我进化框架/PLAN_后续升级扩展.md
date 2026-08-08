# PLAN_后续升级扩展 — 系统自我进化后续升级扩展计划

> 文档版本：v1.0（2026-08-02 起草）
> 状态：待执行（计划文档，不修改任何现有代码与文档）
> 定位：取代 `.trae/documents/后续规划_自我进化框架.md`（2026-07-29 版）中已过时的部分，与其有效部分（观察期后启用路径 M1-M4、LLM 进化 R1-R4）合并并对齐口径。
> 配套文档：`ARCHITECTURE_自我进化框架.md`、`TASK_自我进化框架.md`、`ACCEPTANCE_REPORT.md`（2026-08-02）、`GAP_ANALYSIS.md`。

---

## 0. 文档目的与适用范围

本文档回答一个问题：在 2026-08-02 全链路联调验收（647 测试通过、HC-1~HC-5 硬约束全部验证通过）与 Phase 4 金融工程内核（fineng T4.1/T4.2 完成、44 测试通过）的成果基础上，Shadow 观察期（当前 5/14 天，预计 2026-08-10 结束）之后，系统应沿什么路线继续升级与扩展？

文档给出分阶段、可验收、可回滚的路线，覆盖四条主线：

- 阶段 A：观察期收尾准备（不碰 V9 基线、不写生产路径）。
- 阶段 B：进化闭环渐进启用（DriftMonitor → ABTest 接入 → 自动重训 → Orchestrator 完整外层循环）。
- 阶段 C：金融工程内核扩展（Kalman 时变 Beta、EVT 尾部监控、路径模拟、M5 里程碑）。
- 阶段 D：智能进化扩展（LLM 策略 Ideation、假设验证、知识沉淀、双层闭环无人值守）。

所有启用动作均受 Feature Flag 治理约束，遵循硬约束 HC-1（默认 False）、HC-3（一键回滚）、HC-4（L3 人工审批）、HC-5（Kill Switch 冻结）。

---

## 1. 现状快照（2026-08-02）

### 1.1 能力矩阵

| 层级 | 模块 | 关键类 / 文件 | 状态 |
|------|------|---------------|------|
| Phase 1 防御层 | 进化记忆、五道防线、L0/L1 自动修复 | `utils/evolution/memory.py`（`EvolutionMemory`）、`guard.py`、`auto_fix_engine.py` | ✅ 已验收 |
| Phase 2 反馈闭环 | 贝叶斯因子权重自适应、PnL 归因、EOD 集成 | `utils/evolution/feedback_loop.py`（贝叶斯权重 +1.20% 收益提升）、`pnl_attribution_adapter.py`、`eod_feedback_integration.py` | ✅ 已验收 |
| Phase 3 进化层 | 三层路由编排、AB 测试、因子工厂、策略生成 | `utils/evolution/orchestrator.py`（`EvolutionOrchestratorV2`）、`utils/alpha/ab_testing.py`、`auto_factor_factory.py`、`strategy_generator.py`（`StrategyGenerator`，8 模板 68 用例） | ✅ 已验收 |
| Phase 4.5 流水线 | 数据清洗→Alpha→回测→执行→风控 | `utils/pipeline/`（9 模块）、`scripts/run_pipeline.py`（96 单测） | ✅ 已验收 |
| Phase 4 金融工程内核 | 统一期权定价 + Greeks + GARCH/Kalman/EVT/PathSim | `utils/fineng/`（pricing/greeks/models/instruments + vol_forecast/kalman_beta/tail_risk_evt/path_simulator）；`black_scholes.py`、`implied_vol.py`、`binomial.py`、`monte_carlo.py`；4 个新模块烟火测试全部通过 (2026-08-02) | ✅ T4.1-T4.6 完成（仅剩 T4.7 影子验收） |
| 组合 Greeks 聚合 | 组合层面 Greeks 汇总 | `utils/fineng/greeks/aggregator.py`（`PortfolioGreeksAggregator`） | ✅ 已建 |

总计：647 + 44 = 691 测试全部通过。

### 1.2 观察期 Feature Flag 状态（来源 `reports/evolution/status.json`）

| Flag | 当前值 | 说明 |
|------|--------|------|
| `USE_STRATEGY_EVALUATOR` | true | 评估器已启用 |
| `USE_EVOLUTION_ORCHESTRATOR` | true | 编排器已启用 |
| `USE_MLOPS_PIPELINE` | false | MLOps 流水线未启用 |
| `USE_DRIFT_DETECTOR` | false | 漂移检测未启用 |
| `USE_AUTO_RETRAIN` | false | 自动重训未启用 |

Blocker：`观察期未满: 5/14 天`（自 2026-07-27 起，预计 2026-08-09/10 结束）。

### 1.3 旧规划（2026-07-29）执行对照

- T1 评估器增强 / T2 对齐验证 / T3 Orchestrator 原型：已被 Phase 1-3 实施覆盖 → 过时。
- T4 数据收集钩子设计 / T5 LLM 调研：部分有效（`utils/alpha/llm/` 基础设施已建，见 §3 G6）。
- M1-M4（观察期后启用路径）：仍有效 → 纳入阶段 B。
- R1-R4（LLM Ideation / 假设验证 / 知识沉淀 / 完整双层闭环）：仍有效 → 纳入阶段 D。

### 1.4 里程碑对照（来源 `TASK_自我进化框架.md` L529-537）

- M1 L2 监控就绪 2026-08-10 ⏳ 待启动
- M2 ✅ 核心组件已完成（AutoFixEngine + Guard + Memory）
- M3 ✅ 已完成（+1.20% 收益提升）
- M4 ✅ 全部完成
- M5 金融工程内核上线 2026-08-10 🚀 核心模块交付 (2026-08-02)，T4.7 影子验证器 + 阶段 B 渐进启用器 + T0.6 观察期追踪器已就绪

---

## 2. 差距与技术债务清单（G1-G6）

| 编号 | 差距 | 级别 | 现状与影响 |
|------|------|------|-----------|
| G1 | DataProvider 未接真实行情（dry_run 空数据） | P1 | 验收报告 §7/§8；观察期内所有闭环在空/模拟数据上运行，真实数据路径未验证 |
| G2 | Qlib 模块存在语法问题（`qlib/contrib/model/__init__.py` 缩进） | P1 | `AlphaPipeline` 已优雅降级回退本地因子，但 Qlib 加速路径未用 |
| G3 | `utils/alpha/auto_retrain_scheduler.py` L413 `_load_trained_model` 为占位 TODO | P1（GAP_ANALYSIS 唯一确认的真实代码缺口） | 自动重训启用前必须补全模型加载逻辑 |
| G4 | MLOps 三件套 flags 未启用（`USE_MLOPS_PIPELINE`/`USE_DRIFT_DETECTOR`/`USE_AUTO_RETRAIN`） | P1 | 需观察期结束后按 M1→M2→M3→M4 渐进启用 |
| G5 | fineng 扩展全部完成：T4.3 GARCH、T4.4 Kalman、T4.5 EVT、T4.6 路径模拟 + T4.7 影子验证器 | ✅ 已交付 | 2026-08-02 全部模块烟雾测试通过，FinengShadowVerifier 滑动窗口验证 3/4 通过；4 个 USE_FINENG_* Feature Flag 已注册 |
| G6 | LLM 智能进化未接入 | P2 | `utils/alpha/llm/`（router/providers/audit/openai_compat/passthrough 已存在），但策略 Ideation/假设验证/知识沉淀（R1-R4）未实现 |

风险登记册已有条目（须在新计划中持续跟踪）：AutoFactorFactory 过拟合（CRO Gate）、GARCH/Kalman 历史过拟合（只读对照 + Walk-Forward 闸门）、EVT 样本不足（仅只读监控、严禁触发器）、模型复杂度蔓延（排除项清单锁定）。

---

## 3. 阶段 A — 观察期收尾准备（8-02 ~ 8-09）

**硬约束**：不修改 V9 基线、不写生产路径、不切换任何 Feature Flag 为 true。所有开发以"只读对照 / 离线验证 / 隔离修复"形式进行。

- **A1 Qlib 隔离修复（G2）**：在独立分支/沙箱中修复 `qlib/contrib/model/__init__.py` 缩进问题，用隔离单测验证 Qlib 加速路径可导入且本地因子降级逻辑不受影响；修复前不得触碰生产导入链。
- **A2 DataProvider 接入方案 + 离线验证（G1）**：设计真实行情接入适配器，明确多源交叉校验、复权标准化、缺失填充策略；用历史离线数据做端到端 dry-run，验证数据管道在无 Flag 切换下可正确吞吐，不实际写入生产。
- **A3 重训模型加载补全（G3）**：实现 `AutoRetrainScheduler._load_trained_model`（L413），补单元测试覆盖成功/失败/版本不匹配三态；保持 `USE_AUTO_RETRAIN=False` 时零副作用。
- **A4 T4.3 GARCH 只读对照开发（G5）**：✅ 已完成 (2026-08-02) — `utils/fineng/vol_forecast.py`，网格搜索 MLE + EWMA 并行对照 + fail-closed 回退。
- **A5 观察期监控增强**：增强 `reports/evolution/daily_briefing_*.md` 与 `status.json` 监控维度，为 8-10 的闭环启用决策提供量化依据（漂移基线、因子权重稳定性、影子 vs 实盘偏差）。

**阶段 A 完成判据**：A1-A4 单测全绿、A5 监控报告连续 5 日稳定、观察期达到 14/14 天且无 Kill Switch 误触发。

---

## 4. 阶段 B — 进化闭环渐进启用（8-10 ~ 8-31）

**前置条件**：观察期通过（14/14 天，无重大偏差），A1-A5 完成。

启用顺序遵循"仅告警 → 自动接入 → 自动执行 + 护栏 → 完整外层循环"的渐进原则，每一步均需双签 + 灰度（10% 资金 3 天 → 50% 1 周 → 全量），并保留 HC-3 一键回滚。

- **B1 `USE_DRIFT_DETECTOR=True`（仅告警）**：启用 `utils/alpha/drift_monitor.py`（`DriftMonitor` / `SimModeDriftMonitor`），初始仅输出漂移告警、不阻断交易；对照 `status.json` 中影子偏差，校准阈值。
- **B2 评估器 → ABTest 自动接入**：打通 `strategy_evaluator.py`（`StrategyEvaluator`）与 `ab_testing.py` 的自动通道，新因子/策略经评估器打分后自动进入 AB 测试桶，无需人工搬运。
- **B3 `USE_AUTO_RETRAIN=True` + 降级护栏（G3/G4）**：启用 `AutoRetrainScheduler`（模型加载逻辑在 A3 已补全）；配套降级护栏——重训失败自动回退上一稳定版本、超容量/超复杂度自动拒绝（排除项清单锁定）。
- **B4 Orchestrator 完整外层循环**：在 `EvolutionOrchestratorV2` 外层挂载周周期进化循环（评估→AB→重训→回滚评审），形成闭环；L3 高风险的策略上线仍走人工审批（HC-4）。

**阶段 B 完成判据**：四个 Flag 全部 true 且灰度全量运行 ≥ 1 周、AB 桶中至少 1 个新因子经闭环自然上线、无 PnL 偏离 >2σ 的回滚事件。新增里程碑 **M6 进化闭环启用 2026-08-31**。

> **2026-08-02 工具就绪**：`scripts/phase_b_progressive_enabler.py` (渐进授权调度器, 4 阶段自动推进) + `scripts/observation_tracker.py` (T0.6 观察期追踪器) 已交付。观察期满后执行 `py scripts/phase_b_progressive_enabler.py --auto` 即可自动按 B1→B2→B3→B4 节奏推进。

---

## 5. 阶段 C — 金融工程内核扩展（2026-08-02 核心模块交付）🚀

> **状态更新**：T4.3 (GARCH)、T4.4 (Kalman Beta)、T4.5 (EVT)、T4.6 (PathSim) 四个模块全部实现并通过烟雾测试，均以纯 Python 标准库 (math + random) 零外部依赖实现，遵循 fineng 包 fail-closed 设计原则。4 个新 Feature Flag (`USE_FINENG_*`) 已注册到 `configs/feature_flags.yaml`。

- **C1 (GARCH 波动率预测)**：`utils/fineng/vol_forecast.py` — 网格搜索 MLE (粗 64 + 细 36 组合)、向前 1 步条件波动率预测、EWMA(λ=0.94) 并行对照、fail-closed 回退。
- **C2 (Kalman 时变 Beta)**：`utils/fineng/kalman_beta.py` — 局部水平模型 + 滤波发散检测 + 软重置、启发式 Q/R 估计、对冲效率回测 (方差下降 ≥5% 触发接入申请)。
- **C3 (EVT 尾部风险监控)**：`utils/fineng/tail_risk_evt.py` — POT + GPD MLE 拟合、Profile Likelihood 95% CI、ES 与经验分位数对照 (差异 >30% 告警)。**严禁接触发器**。
- **C4 (路径蒙特卡洛模拟)**：`utils/fineng/path_simulator.py` — GBM + normal/t/bootstrap、纯 Python Cholesky + Marsaglia-Tsang Gamma、DD 分布 + 历史事件对照。替代 stress_test_runner 单点输出。

**阶段 C 完成判据**：四项只读模块单测全绿 (2026-08-02 已通过烟雾测试)、Walk-Forward 闸门通过、M5 验收报告签署。

---

## 6. 阶段 D — 智能进化扩展（9 ~ 10 月）

复用已建基础设施 `utils/alpha/llm/`（router / providers / audit / openai_compat / passthrough）与 `StrategyGenerator`，不重复造轮子。

- **D1 LLM 策略 Ideation 接入（R1，G6）**：基于 `llm/router.py` 路由 + `StrategyGenerator` 模板，让 LLM 在 Shadow 模式下生成候选因子/策略假设，全部经 `audit.py` 留痕，初始仅产出、不执行。
- **D2 假设验证框架（R2）**：对新假设跑显著性检验（RankIC/ICIR）+ 效应量估计 + 样本外 Purged K-Fold，自动判定是否进入 AB 桶；过拟合高危假设经 CRO Gate 拦截。
- **D3 知识沉淀库（R3）**：新增 `reports/evolution/knowledge_base.jsonl`，沉淀已验证/已证伪的假设与归因结论，反馈给 LLM 作为下一轮 Ideation 上下文。
- **D4 双层闭环无人值守（R4）**：在 D1-D3 基础上，让"LLM 假设生成层"与"B4 进化执行层"形成双层闭环，Shadow 模式连续运行 ≥ 1 周无人工干预、无 Kill Switch 误触发后，再评估生产化。

**阶段 D 完成判据**：D1-D3 模块联调通过、知识库有 ≥ 20 条结构化沉淀、D4 双层闭环 Shadow 运行 1 周达标。新增里程碑 **M7 LLM 智能进化 2026-10-31**。

---

## 7. 任务依赖图

```
A1(Qlib修复) ──┐
A2(DataProvider)├──► [观察期 14/14 天] ──► B1(Drift告警) ──► B2(AB接入)
A3(重训加载) ──┤                                          └─► B3(自动重训) ──► B4(完整闭环) ──► M6
A4(GARCH对照) ──┤                                                                        │
A5(监控增强) ──┘                                                                        │
                                                                                        │
C1(Kalman) ─┐                                                                          │
C2(EVT) ────┼──────────────────────────────────────────────────────────► C4(M5验收 10-30)
C3(路径模拟)┘                                                                          │
                                                                                        │
D1(LLM Ideation) ─► D2(假设验证) ─► D3(知识库) ─► D4(双层闭环) ──────────► M7(10-31)
```

约束：B 系列必须等观察期 + A 系列完成；C 系列可与 B 并行；D 系列可与 C 并行、但 D4 需 B4 已稳定。

---

## 8. Feature Flag 治理与回滚预案

- 所有新增/变更 Flag 默认 False（HC-1），任何 true 切换需双签 + 灰度（10%→50%→全量，各阶段有明确回滚触发条件：PnL 偏离 >2σ 立即回滚）。
- `USE_STRATEGY_EVALUATOR` / `USE_EVOLUTION_ORCHESTRATOR` 当前已 true，阶段 B 仅在此基础上叠加 `USE_DRIFT_DETECTOR` / `USE_AUTO_RETRAIN` / `USE_MLOPS_PIPELINE`。
- HC-5 Kill Switch 冻结优先于任何 Flag：触发即冻结所有开仓权限，Flag 切换无效。
- 回滚路径：单因子回滚（AB 桶剔除）→ 模块回滚（`_load_trained_model` 回退上一稳定版）→ 全局回滚（Flag 复位 False + Kill Switch 确认）。

---

## 9. 风险登记册（新增/延续）

| 风险 | 触发条件 | 缓解措施 |
|------|----------|----------|
| AutoFactorFactory 过拟合 | 增量 IC < 0.01 或样本外衰减 >40% | CRO Gate 拦截，禁止上线 |
| GARCH/Kalman 历史过拟合 | Walk-Forward 闸门未通过 | 仅只读对照，未过闸门不接入 |
| EVT 样本不足 | 尾部样本 < 阈值 | 仅只读监控，严禁触发器 |
| 模型复杂度蔓延 | 并行 Alpha 源 > 25 或排除项清单被突破 | 排除项清单锁定 + 季度审查 |
| 真实数据接入偏差 | G1 未离线验证即切生产 | A2 强制离线 dry-run 通过后才允许 |
| LLM 幻觉生成无效假设 | D1 产出假设 0 通过 D2 验证 | audit.py 全留痕 + D2 自动筛除 |

---

## 10. 验收标准汇总（Checklist）

- [ ] A1：Qlib 隔离修复，单测覆盖导入与降级路径
- [ ] A2：DataProvider 离线 dry-run 端到端通过
- [ ] A3：`_load_trained_model` 三态单测全绿
- [ ] A4：`garch.py` 只读对照，不污染生产路径
- [ ] A5：观察期 14/14 天且监控连续 5 日稳定
- [ ] B1：`USE_DRIFT_DETECTOR=True` 仅告警运行 ≥ 3 天
- [ ] B2：评估器→ABTest 自动通道打通
- [ ] B3：`USE_AUTO_RETRAIN=True` + 降级护栏生效
- [ ] B4：Orchestrator 完整外层循环全量运行 ≥ 1 周（M6）
- [ ] C1：`kalman.py` Walk-Forward 闸门通过
- [ ] C2：EVT 仅只读监控，无触发器
- [ ] C3：路径模拟对照通过
- [ ] C4：M5 验收报告签署（2026-10-30）
- [ ] D1：LLM Ideation Shadow 产出 + audit 留痕
- [ ] D2：假设验证框架自动筛除无效假设
- [ ] D3：知识库 ≥ 20 条结构化沉淀
- [ ] D4：双层闭环 Shadow 运行 1 周达标（M7）

---

## 11. 里程碑表（更新后）

| 里程碑 | 内容 | 目标日期 | 状态 |
|--------|------|----------|------|
| M1 | L2 监控就绪 | 2026-08-10 | ⏳ 待启动（阶段 A5） |
| M2 | 防御层加固（AutoFixEngine + Guard + Memory） | — | ✅ 核心组件已完成 |
| M3 | 反馈闭环（+1.20% 收益提升） | — | ✅ 已完成 |
| M4 | 进化层完成（AutoFactorFactory + Orchestrator + ABTest + StrategyGenerator） | — | ✅ 全部完成 |
| M5 | 金融工程内核上线（GARCH/Kalman/EVT/路径模拟 + Walk-Forward） | 2026-08-10 | 🚀 核心模块交付 (2026-08-02)，待 T4.7 影子验收 |
| M6 | 进化闭环启用（B1-B4 全 Flag true + 全量） | 2026-08-31 | ⬜ 阶段 B |
| M7 | LLM 智能进化（D1-D4 双层闭环 Shadow 达标） | 2026-10-31 | ⬜ 阶段 D |

---

## 12. 与既有文档的边界

- 本文档为**计划层**，不修改 `ARCHITECTURE`/`TASK`/`ACCEPTANCE_REPORT`/`GAP_ANALYSIS`。
- 阶段 A-D 的具体实现、单测、代码变更由对应任务工单（建议拆自本文档 Checklist）承载，不在本文档内编写代码。
- 旧版 `.trae/documents/后续规划_自我进化框架.md` 的 T1-T3 视为已被 Phase 1-3 覆盖，M1-M4/R1-R4 以本文档为准。
