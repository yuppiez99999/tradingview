---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-08-02
updated: 2026-08-19
contains: self-evolution-framework, feature-flags, drift-monitor, strategy-evaluator, auto-retrain-scheduler, feedback-loop, vol-regime-weighter, cybernetics-theory, feasibility-assessment, improvement-backlog
related:
  - cairn/backtest-standards.md
  - cairn/risk-architecture.md
  - cairn/alpha-factor-system.md
  - cairn/model-training.md
---

# 自我进化框架

> 记录项目自我进化框架的设计、实施进展和决策点。框架通过 Feature Flag 体系、DriftMonitor + StrategyEvaluator、AutoRetrainScheduler、FeedbackLoop 实现策略的自动检测→诊断→进化闭环。详细进展和缺口分析见 `docs/自我进化框架/`（10 个文件）。

## 一、框架总览

自我进化框架的目标是实现策略的自动漂移检测、自动重训、A/B 验证和自动上线，做到无人工干预的策略持续进化。当前处于 **Phase 0 观察期**（2026-07-23 → 2026-08-13），Feature Flag 体系已部署但所有写操作均在只读对照模式。

核心闭环：
```
每日数据摄入 → DriftMonitor 漂移检测 → StrategyEvaluator 多维评分
    → AutoRetrainScheduler 重训触发 → FeedbackLoop 回测验证
    → Feature Flag 灰度发布 → Shadow 账户验证 → 全量上线
```

## 二、Phase 规划与进度

| Phase | 内容 | 状态 | 说明 |
|-------|------|------|------|
| Phase 0 | Feature Flag 基础设施 + 观察期 | 运行中（观察期第 12 天, 08-13 满期但建议延至 08-20） | T0.1/T0.2/T0.3 ✅；T0.4/T0.5/T0.6 🔄 DriftMonitor + StrategyEvaluator 已运行产出 reports；W1.3a/b/c 全部 ✅ |
| Phase 1 | DriftMonitor 生产接入 | 核心完成（T1.6 已交付） | T1.1-T1.7 全部 ✅；StrategyEvaluator 字段完整 (public/private 分离 + reward_hacking_risk + 小样本容错) |
| Phase 2 | AutoRetrainScheduler 自动重训 | 部分完成 | 核心逻辑已实现，Feature Flag 控制启用 |
| Phase 3 | AutoFactorFactory 自动因子生成 | 全部完成 | T3.1-T3.5 ✅，全链路联调验收通过 |
| Phase 4 | FeedbackLoop 全闭环 + 金融工程内核 | 部分完成 | T4.1/T4.2/T4.3/T4.4/T4.5/T4.6 ✅；T4.7 ✅ DONE 08-04 (FINENG_ACCEPTANCE_REPORT.md 已补写) |
| Phase 4.5 | 工程稳定性加固 | 进行中 | 当前焦点：W1.3 真实数据接入 (W1.3a/b/c 全部 ✅) → 08-20 决策日；LiveScheduler 稳定性、错误处理根治 |

**任务完成率**：24/29（83%）；分布: 24 ✅ DONE + 4 🔄 IN_PROGRESS + 1 ⬜ TODO（T4.7 验收报告未补），详见 `docs/自我进化框架/TASK_自我进化框架.md` v1.3。

### W1.3 子任务进度（真实数据接入，Wave 1 核心）— 全部完成

| 子任务 | 时间窗口 | 状态 | 说明 |
|-------|---------|------|------|
| W1.3a | 08-04~08-06 | ✅ DONE 08-03 (提前) | ShadowRealDataFeeder: 真实 daily_returns.jsonl 接入 + 77 测试 93.95% + EOD 阶段四点五 + EvolutionEval 兜底 |
| W1.3b | 08-07~08-10 | ✅ DONE 08-03 (提前) | DriftShadowIntegrator: DriftMonitor ↔ DelayedLabelTracker 桥接 + 46 测试 + EOD 阶段四点七 + EvolutionEval 兜底 + 端到端验证 PASS |
| W1.3c | 08-11~08-12 | ✅ DONE 08-03 (提前) | StrategyEvaluator 真实评分: 3/3 验证 PASS (Public/Private 分离 + Flag 透传 + 只读行为). public=0.0 vs private=0.4716, reward_hacking_risk=0.0, 样本 5 条不足 |

## 三、Feature Flag 三层保护体系

`utils/infra/feature_flags.py` 提供新功能的渐进式启用能力：

```
Layer 1: Feature Flag 标记 → 代码级开关，默认 False
Layer 2: 只读对照模式 → flag 启用后先以 shadow 模式运行，写操作不生效
Layer 3: 灰度发布 → 小流量 (10%) → 中流量 (50%) → 全量 (100%)
```

**当前活跃 Flag**（截至 08-02）：
- `USE_DRIFT_DETECTOR` — DriftMonitor 是否启用（只读对照模式）
- `USE_MODEL_REGISTRY` — MLflow Model Registry 是否启用（默认 False，本地文件兜底）
- `USE_ADAPTIVE_OPTIMIZE` — 漂移触发的超参自适应搜索（默认 False）
- `USE_SELF_EVOLUTION` — 自我进化总开关（False，08-13 观察期满后评估开启）

**关键约束**：Phase 0 期间所有 Feature Flag 的写操作（模型更新、策略切换、自动重训）均被限制，仅运行只读对照和数据收集。

## 四、核心组件

### DriftMonitor（漂移检测器）

`utils/alpha/drift_monitor.py` + `ms_strategy/src/ml/drift_detector.py`

五维度漂移检测：

| 检测器 | 方法 | 阈值 |
|--------|------|------|
| IC 衰减 | 连续 N 日 IC < 阈值 | 连续 5 日 < 0.02 |
| ADWIN 概念漂移 | 自适应窗口均值差异检验 | delta=0.002 |
| KS 检验 | 特征分布偏移 | p < 0.05 |
| PSI（人口稳定性指数） | 预测分布偏移 | > 0.25 |
| OOS Performance Gap | IS IC - OOS IC 差距 | > 3% WARNING, > 5% CRITICAL |

SimModeDriftMonitor（GAP-6）：KS 检验 + PSI 双指标，支持面板批量检查，产出 DriftReport 持久化到 `reports/drift/`。

### StrategyEvaluator（策略评估器）

`live_scheduler.py` 中的 `strategy_evaluation` 模块（N2-N6），每日收盘后执行：

- **N2**：策略多维评分（composite_score），含 return_metrics + diversification_metrics
- **N3**：计算并记录当日 IC
- **N4**：漂移触发的超参自适应搜索（`USE_ADAPTIVE_OPTIMIZE` 控制）
- **N5**：退化告警记录到 SkillManager
- **N6**：日内表现评分 + 退化标记

### AutoRetrainScheduler

`utils/alpha/auto_retrain_scheduler.py` + `15_每日工作流/run_auto_retrain.py`

重训触发条件：
1. 定时：每月 1 日 06:00 自动执行
2. 事件：DriftMonitor 检测到概念漂移时回调触发
3. 手动：`python run_auto_retrain.py [--force] [--symbols]`

重训安全机制：重训前自动备份、备份失败跳过、重训后自动验证新旧模型对比、生成 Markdown 报告归档。

### FeedbackLoop（反馈闭环）

`utils/pipeline/backtest_gate.py` 实现回测验证链：

四道闸门：Walk-Forward 回测 → Deflated Sharpe Ratio (DSR > 1.0) → 四大压力场景测试 → CRO Gate
验收标准：IC > 0.03, DSR > 1.0, 最大回撤 < 15%

`utils/purged_kfold.py` 的 Purged K-Fold 先验检测防止过拟合进入反馈环。

### VolRegimeWeighter（波动率 Regime 权重建议器）

`utils/alpha/vol_regime_weighter.py` — Phase 0 只读建议模式（2026-08-05 新增）

自我进化框架"根据市场波动情况动态调整权重大小"能力的核心实现。按 VIX/realized_vol 将市场分为 bull/neutral/bear/crisis 四档，输出 8 类风格大类（科技/新能源/医药/金融/宽基/资源/防御/现金）的权重调整建议。

**Regime 四档**（与 `portfolio.yaml` dynamic_hedge_policy 完全对齐）：

| Regime | VIX 阈值 | hedge_ratio | 进攻类倍数 | 防御类倍数 |
|--------|---------|------------|-----------|-----------|
| bull | <20 | 20% | 科技×1.20 | 防御×0.90 / 现金×0.50 |
| neutral | 20-30 | 40% | 全 1.0（中性） | 全 1.0 |
| bear | 30-40 | 75% | 科技×0.60 | 防御×1.30 / 现金×2.00 |
| crisis | ≥40 | 90% | 科技×0.30 | 防御×1.50 / 现金×3.00 |

**核心流程**：sense_regime（VIX+RV 双指标一致性校验，不一致取更保守档；回撤>12% 至少 bear）→ compute_weights（4×8 权重矩阵）→ enforce_constraints（单标的≤8%、单一风格≤30%、现金≥5%、总和=1.0、差额归现金）→ emit_suggestion（写 `reports/evolution/vol_regime_weights_YYYY-MM-DD.json`）。

**设计要点**：
- 复用 `VolTargetController.calc_realized_vol`，不重写 EWMA 逻辑
- 复用 `EvolutionOrchestrator.log_decision` 审计链（通过 `extra_payload` 参数注入）
- Feature Flag `USE_VOL_REGIME_WEIGHTER` 默认 False，双签启用；Flag 关闭时返回 `WeightSuggestion.noop()`
- Phase 0 不修改 `portfolio.yaml`，`apply_to_portfolio`（Phase 1）和 `backtest`（Phase 2）抛 `NotImplementedError` 预留

**集成**：`EvolutionOrchestrator.run_observation_cycle` 末尾自动调用（L580-600），失败不阻塞主流程；regime 识别结果同时反馈到对冲策略（aligned_hedge_ratio 对齐 dynamic_hedge_policy）。

**验证**：46 测试全通过（单元 + 端到端）+ 手动验证 4 档 Regime 识别正确 + 约束总和均为 1.0。

## 五、08-13 关键决策点（建议延至 08-20）

观察期满日（原定 2026-08-13, 因样本不足建议延至 08-20）需要回答三个核心问题：

1. **Phase 0 出口达成与否**：Shadow 样本是否足够（≥20 真实数据点）、观察期内是否有异常漂移事件、DriftMonitor 告警是否可解释且可操作
    - **当前状态 (08-03)**: ⚠️ 部分达成 — 样本 5 条 <20, 但机制健康 + 告警可解释
2. **Public/Private 分离性是否健康**：DriftMonitor 在 Private（未公开）数据上的检测结果是否与 Public 一致、是否存在数据窥探引发的假阴性
    - **当前状态 (08-03)**: ✅ 健康 — W1.3c 验证 PASS, public=0.0 vs private=0.4716, reward_hacking_risk=0.0
3. **DriftMonitor 是否误报**：5 个检测维度中是否有产生持续性噪音的维度、误报率是否在可接受范围（<5%）
    - **当前状态 (08-03)**: 🔄 待验证 — sim_mode 无误报, 待真实 panel 接入后统计

**决策建议 (08-03 预填)**: 推荐选项 B 延长观察期至样本达标 (≥20 条), 新决策日 08-20. 详见 `docs/自我进化框架/OBSERVATION_PERIOD_DECISION.md` §5.4.

三项全部通过 → 解除 Feature Flag 只读限制 → 启动 Stage B 渐进启用（先 AutoRetrainScheduler 小流量、再 DriftMonitor 生产接入、最后 FeedbackLoop 自动重训→上线闭环）。

## 六、当前开放问题

1. Shadow 数据是否足够 — 若 08-13 时 Shadow 样本 < 120 天，观察期是否延长还是基于现有数据做决策
2. DriftMonitor 的 PSI 阈值校准 — PSI > 0.25 在低频因子场景中是否过严
3. AutoFactorFactory 依赖 — 因子库 v8.6.14 的 20 个 GTJA191 因子需逐个验证 Rank IC 和 ICIR 后才能进入 FactorFactory
4. LiveScheduler 稳定性 — 盘中实时调度需在观察期内证明 100% 零崩溃
5. Phase 4 收尾 — ✅ 已完成 (2026-08-04): T4.2 `utils/theta_engine.py` 三处调用点切换到 `utils/fineng/pricing/black_scholes.py` 统一内核; T4.7 `docs/自我进化框架/FINENG_ACCEPTANCE_REPORT.md` 已补写, 3/4 模块通过 Walk-Forward 闸门
6. W1.3 真实数据接入 — W1.3a/b/c 全部 ✅ DONE (2026-08-03 提前完成). ShadowRealDataFeeder + DriftShadowIntegrator + StrategyEvaluator 3/3 验证 PASS. 当前样本 5 条不足, 距 20 条最小评估样本差 15 天. 决策建议选项 B 延长观察期至 08-20, 详见 `docs/自我进化框架/OBSERVATION_PERIOD_DECISION.md`

## 七、与 Cairn 体系的整合

此文档创建于 2026-08-02 知识审计中发现的"自我进化框架完全不在 cairn 体系中"问题。此前框架的 10 个 docs/文件单独存在，现在通过本文档建立桥接。后续自我进化框架的重大进展应在此文档更新，并在 `cairn/LOG.md` 中记录变更指针。

## 八、理论依据：《控制论与科学方法论》（金观涛）概念映射

> 来源：金观涛《控制论与科学方法论》（2026-08-19 PDF 解析），10 个核心概念映射到本框架的工程实现。该书是本自我进化框架的理论原点 — 框架的"自动检测→诊断→进化闭环"本质是控制论负反馈调节在量化交易领域的工程化。

### 8.1 概念映射表

| 控制论概念 | 书中要义 | 框架对应组件 | 工程映射 |
|-----------|---------|-------------|---------|
| **负反馈调节** | 系统输出与目标偏差→反馈→控制动作→偏差收敛 | DriftMonitor + AutoRetrainScheduler | 漂移检测（偏差度量）→ 重训触发（控制动作）→ 模型更新（偏差收敛） |
| **超稳定系统** | 对抗结构扰动后能回到目标轨道的系统 | 自我进化框架整体目标 | 策略在市场 Regime 切换（结构扰动）后通过自动重训+灰度发布回到稳定盈利轨道 |
| **黑箱认识论** | 仅通过输入输出关系认识系统，不假设内部机制 | FeedbackLoop 回测验证 | Walk-Forward 回测只看策略输入（因子）→输出（收益/IC），不假设市场内部机制 |
| **反馈过度/振荡** | 反馈信号过强或延迟导致系统振荡失稳 | Purged K-Fold + DSR 闸门 | 防止过拟合信号进入反馈环；Deflated Sharpe Ratio > 1.0 剔除多重检验假阳性 |
| **变异与选择** | 系统通过随机变异+环境选择产生适应性进化 | AutoFactorFactory（Phase 3 已完成） | 因子自动生成（变异）→ 回测筛选（选择）→ 保留有效因子进入下一轮迭代 |
| **稳态与亚稳态** | 系统可处于多个稳态，扰动可触发稳态迁移 | VolRegimeWeighter | VIX/realized_vol 识别市场 Regime（bull/neutral/bear/crisis 四档稳态）→ 权重矩阵切换 |
| **可观察性** | 系统内部状态可由外部输出推断 | StrategyEvaluator | 多维评分（composite_score + IC + 退化告警）使策略内部状态可观测、可诊断 |
| **因果与相关性混淆** | 观察到的相关性不蕴含因果性 | Public/Private 分离（W1.3c） | Private IC=0.4716 vs Public IC=0.0 分离健康 → 防止数据窥探产生的假因果 |
| **适应性预期** | 主体根据历史反馈调整预期，预期又影响系统 | Feature Flag 灰度发布 | 10%→50%→100% 渐进启用，每阶段根据 Shadow 账户反馈调整下一阶段预期 |
| **结构稳定性** | 结构在扰动下保持拓扑性质不变 | FeedbackLoop 四道闸门 | Walk-Forward + DSR + 压力场景 + CRO Gate 多维度验证策略鲁棒性不随扰动崩塌 |

### 8.2 理论指导下的设计决策

1. **为何选择负反馈而非正反馈**：正反馈（追涨杀跌）在控制论中对应失稳/发散，本框架的 DriftMonitor→重训是负反馈（偏差→修正→收敛），确保策略进化不发散。

2. **为何 Feature Flag 三层而非单层开关**：对应控制论的"渐进耦合"原则 — Layer 1（标记）是可观察性，Layer 2（只读对照）是可验证性，Layer 3（灰度）是适应性预期。跳过任一层即丧失对扰动的可控性。

3. **为何观察期 ≥20 样本**：控制论中反馈控制要求采样数 ≥系统自由度。策略评估的自由度（5 个漂移维度 + return + IC + diversification ≈ 8）→ 最小样本 ~20 才能统计显著。

4. **为何 Public/Private 分离是健康性核心指标**：控制论中"可观察性分解"要求系统内部状态可由外部输出重构。Public/Private IC 分离 → 内部状态（Private）不可由公开数据（Public）推断 → 无数据窥探 → 因果推断有效。

### 8.3 后续理论指导方向

> **2026-08-19 更新：三个方向均已实现**，代码在 `utils/alpha/theoretical_metrics.py`，23 个单元测试全绿。以下为实现前的原始描述（保留作为设计依据）。

- **超稳定性的定量度量** ✅ DONE 2026-08-19：`LyapunovStabilityMeter` — Lyapunov 函数 V = w_ic×|IC-IC_target|² + w_ret×|return-return_target|² + w_drift×drift_score²；离散 Lyapunov 指数 λ = log(V(t+1)/V(t))；λ < 0 渐近稳定，λ > 0 失稳。作为 Phase B B1 退出指标。
- **反馈延迟的相位分析** ✅ DONE 2026-08-19：`FeedbackPhaseAnalyzer` — 相位裕度 = π - 总延迟 × 角频率；相位裕度 > 0 反馈收敛，< 0 振荡风险。检测延迟 + 重训延迟 + 验证延迟 = 总延迟。
- **变异速率与选择压力平衡** ✅ DONE 2026-08-19：`VariationSelectionBalancer` — 平衡指数 B = 变异速率 × 通过率 / (变异速率 + 选择压力)；B ∈ [0.1, 0.5] 健康，B < 0.1 枯竭，B > 0.5 膨胀。

**指针**：`cairn/Reference/控制论与科学方法论_金观涛.pdf`（原始 PDF）；本节为 2026-08-19 解析后沉淀。

## 九、自我升级计划可行性评估与改进建议（2026-08-19）

> 基于沉淀知识（§八控制论映射 + `cairn/recommended-reading-20260819.md` 18 本书 + `cairn/philosophy-trading-mapping-20260819.md` 哲学映射 + `cairn/Reference/douban-book-summaries-20260819.md` 豆瓣详情 + `cairn/test-health-20260819.md` 测试健康度）对本框架自我升级计划的可行性评估和改进建议。

### 9.1 可行性结论：整体可行

| 维度 | 证据 | 来源 |
|------|------|------|
| 闭环机制完整 | DriftMonitor→StrategyEvaluator→AutoRetrainScheduler→FeedbackLoop 24/29 任务完成 | §二 |
| Feature Flag 三层保护 | Layer 1 标记 + Layer 2 只读对照 + Layer 3 灰度，渐进耦合 | §三 + §八.2 决策 2 |
| Phase 0 健康性 | Public/Private IC 分离健康（0.0 vs 0.4716），reward_hacking_risk=0.0 | §五 + W1.3c |
| 测试基础 | 13195 passed / 0 failed / 100% | `cairn/test-health-20260819.md` §一 |
| 控制论理论支撑 | 10 概念映射 + 4 设计决策有理论依据 | §八.1 + §八.2 |
| 哲学思想已编码 | ~5000+ 行实现，~200+ 测试 | `cairn/philosophy-trading-mapping-20260819.md` §一 |
| 时间窗口释放 | Wave 6 提前完成，释放 107+ 天 | `cairn/ROADMAP.md` Wave 6 |

### 9.2 六个需要改进的地方

#### 改进 1：§八.3 三个理论方向未实现 — Phase B "盲启用"风险 ✅ DONE 2026-08-19

> **已实现**：`utils/alpha/theoretical_metrics.py` 模块包含三个度量器，23 个单元测试全绿。详见 §八.3 更新标记。

| 方向 | 当前状态 | 风险 | 建议时机 |
|------|---------|------|---------|
| Lyapunov 稳定性定量度量 | 仅定性目标"回到稳定盈利轨道" | Phase B 启用后无法量化判断系统是否超稳定 | B1 启用前实现，作为 B1 退出指标 |
| 反馈延迟相位分析 | DriftMonitor→重训→验证 1-3 日延迟未分析 | 可能导致反馈相位差→振荡（控制论经典失稳模式） | B2 启用前完成相位分析 |
| 变异速率与选择压力平衡 | AutoFactorFactory 生成速率 vs 回测门禁未平衡 | 因子库可能膨胀或枯竭 | B3 启用前建立平衡公式 |

**优先级**：高 — 直接影响 Phase B 启用的安全性和可观测性。

#### 改进 2：Phase B 渐进启用窗口偏短 — 与反馈延迟矛盾

ROADMAP Wave 2 的 Phase B 排期：B1-B4 每步 3-4 天。但 §八.3 指出反馈延迟 1-3 日，意味着 3-4 天窗口仅能观察到 1-2 个反馈周期，不足以判断反馈是否收敛。

**改进建议**：B1-B4 每步延长至 **5-7 天**（至少 2 个完整反馈周期），或增加中间检查点。总时长从 ~16 天延长至 ~24 天，结束日从 09-05 顺延至 09-13，仍在 Wave 7 Sprint 1 窗口内。

**改进后排期**：

| 阶段 | 原排期 | 改进排期 | 内容 |
|------|--------|---------|------|
| B1 | 08-20→23 (3天) | 08-24→30 (7天) | USE_DRIFT_DETECTOR=true 仅告警 + Lyapunov 度量实现 |
| B2 | 08-23→26 (3天) | 08-31→09-06 (7天) | USE_FEEDBACK_LOOP 自动接入 + 相位分析完成 |
| B3 | 08-26→29 (3天) | 09-07→13 (7天) | USE_AUTO_RETRAIN=true + 降级护栏 + 变异/选择平衡 |
| B4 | 08-29→09-05 (4天) | 09-14→20 (7天) | USE_MLOPS_PIPELINE=true 完整外层循环 |

#### 改进 3：López de Prado 基础标注方法未覆盖 — AutoResearch Skill 短板

豆瓣书籍 `douban-book-summaries §二.1` 显示 AFML（8.8 分）的 **Triple-Barrier Labeling**（第 3 章）和 **Meta-Labeling** 是 López de Prado 方法论的基础标注方法。当前标为"v8.7 候选"（`recommended-reading §九`），但：

- AutoResearch Skill（Wave 7 Sprint 4）要真正自动化因子研究，缺少 Triple-Barrier Labeling 会产生短板
- 当前回测标注仅用固定阈值，无法适应波动率变化的市场环境

**改进建议**：将 Triple-Barrier Labeling 从"v8.7 候选"提升到 **Wave 7 Sprint 2**（09-13~10-12），与 AutoResearch Skill 开发同步。预估 2-3 天工作量（`utils/backtest/triple_barrier.py` + 测试）。

**优先级**：中 — 不阻塞 Phase B，但影响 AutoResearch Skill 的研究质量。

#### 改进 4：PSI 阈值校准未完成 — 开放问题 #2 ✅ DONE 2026-08-19

> **已实现**：`drift_shadow_integrator.py:calibrate_psi_thresholds` 方法从骨架升级为真实校准逻辑 — rolling window PSI 序列计算 + 按因子频率分组（日频/周频/月频不同窗口）+ 百分位阈值 + 工业标准 2x 上限保护。46 个现有测试全绿。

**改进建议**：在 08-24 观察期决策日前，用已积累的 12 条真实样本 + 历史回测数据校准 PSI 阈值。建议按因子频率分组校准（日频/周频/月频不同阈值）。

**优先级**：高 — 08-24 前必须完成，否则 B1 误报。

#### 改进 5：哲学模块与自我进化闭环整合度不明

`philosophy-trading-mapping §一` 显示 10+ 哲学思想已编码（~5000+ 行），但文档未说明这些模块是否参与了自我进化闭环：

- `SorosReflexivityEngine` 是否作为 DriftMonitor 的输入信号？
- `nassim_taleb.py` 的反脆弱评分是否影响 AutoRetrainScheduler 的重训触发？
- `HypothesisVerifier` 的证伪状态是否反馈到 Feature Flag 灰度发布决策？

**改进建议**：明确哲学模块在自我进化闭环中的角色。建议在本框架文档新增 §十"哲学模块整合映射"，标注每个哲学模块在闭环中的位置（检测/诊断/进化/验证）。

**优先级**：中 — 不阻塞 Phase B，但影响闭环的信号丰富度。

#### 改进 6：ruff 剩余 2719 个违规 — 关键模块类型注解缺失

`test-health §一`：ruff 剩余 2719 个违规，其中 **ANN 类型注解缺失 1181 个**。Phase B 启用后，DriftMonitor / StrategyEvaluator / AutoRetrainScheduler 的接口如果缺乏类型注解，会增加运行时错误风险。

**改进建议**：Phase B 启用前，优先清理三个核心模块的类型注解：
- `utils/alpha/drift_monitor.py`
- `live_scheduler.py`
- `utils/alpha/auto_retrain_scheduler.py`

预估 ~50 个 ANN 违规。其余 1131 个非核心模块违规可延后。

**优先级**：低 — 不阻塞 Phase B，但影响代码可维护性。

### 9.3 改进优先级排序与时间线

| 优先级 | 改进项 | 建议完成时间 | 阻塞对象 | 状态 |
|--------|--------|------------|---------|------|
| **P0 高** | 改进 4：PSI 阈值校准 | 08-24 前 | B1 启用 | ✅ DONE 2026-08-19 |
| **P0 高** | 改进 1：§八.3 三个理论方向 | B1-B3 启用前分步完成 | Phase B 安全性 | ✅ DONE 2026-08-19 |
| **P1 中** | 改进 2：Phase B 窗口延长 | 08-24 决策后调整排期 | Phase B 反馈收敛判断 | ⬜ 待决策 |
| **P1 中** | 改进 3：Triple-Barrier Labeling | Wave 7 Sprint 2 (09-13~10-12) | AutoResearch Skill | ⬜ 待排期 |
| **P2 中** | 改进 5：哲学模块整合映射 | Phase B 启用后 | 闭环信号丰富度 | ⬜ 待实现 |
| **P3 低** | 改进 6：核心模块类型注解 | Phase B 启用前 | 代码可维护性 | ⬜ 待实现 |

### 9.4 与现有计划的关系

**不改变 Phase 0-4 整体架构**：改进建议是对 Phase B 渐进启用流程的精化和补强，不8.6.14 → 08-24 决策 → Phase B → v8.7 的主线不变。

**时间窗口影响**：
- Phase B 结束日从 09-05 顺延至 09-20（+15 天），仍在 Wave 7 Sprint 1（08-13~10-12）窗口内
- Triple-Barrier Labeling 纳入 Wave 7 Sprint 2，不额外占用窗口
- v8.7 发布日 12-31 不变

**与推荐书目结论一致**：`recommended-reading §十` 结论"不需要修改 ROADMAP"仍然成立 — 改进建议是对自我进化框架内部流程的精化，不新增 ROADMAP 大任务。

**指针**：本节为 2026-08-19 基于沉淀知识的评估沉淀。关联文档：`cairn/recommended-reading-20260819.md` §九/§十 + `cairn/philosophy-trading-mapping-20260819.md` + `cairn/Reference/douban-book-summaries-20260819.md` + `cairn/test-health-20260819.md`。

## 十、哲学模块在自我进化闭环中的整合映射（2026-08-19）

> `cairn/philosophy-trading-mapping-20260819.md` 记录了 10+ 哲学思想的工程实现（~5000+ 行代码），但未说明这些模块在自我进化闭环中的角色。本节明确每个哲学模块在闭环四阶段（检测→诊断→进化→验证）中的位置。

### 10.1 闭环四阶段定义

| 阶段 | 核心组件 | 职责 |
|------|---------|------|
| **检测** | DriftMonitor | 感知市场状态变化（漂移/Regime 切换） |
| **诊断** | StrategyEvaluator | 评估策略健康度（IC/收益/退化） |
| **进化** | AutoRetrainScheduler + AutoFactorFactory | 触发重训/生成新因子 |
| **验证** | FeedbackLoop | 回测验证 + 灰度发布 |

### 10.2 哲学模块整合映射

| 哲学模块 | 哲学来源 | 闭环阶段 | 整合方式 | 当前状态 |
|---------|---------|---------|---------|---------|
| `SorosReflexivityEngine` | 索罗斯反身性 | **检测** | 反身性评分作为 DriftMonitor 的前置信号 — reflexivity > 0.7 预示 Regime 逆转 | 🔄 待集成 |
| `nassim_taleb.py` | 塔勒布反脆弱 | **诊断** | antifragile/convexity 评分影响 StrategyEvaluator 的 composite_score | 🔄 待集成 |
| `HypothesisVerifier` | 波普尔证伪主义 | **验证** | falsified 状态反馈到 FeedbackLoop 闸门 — 证伪的策略不进入灰度发布 | ✅ 已集成 |
| `FirstPrinciplesAnalyzer` | 第一性原理 | **进化** | 价值驱动因素分解指导 AutoFactorFactory 的因子设计方向 | 🔄 待集成 |
| `BuffettMungerModel` | 巴菲特/芒格 | **诊断** | 护城河/安全边际评分作为 StrategyEvaluator 的长期质量维度 | 🔄 待集成 |
| `DalioEconomicMachine` | 达利奥经济机器 | **检测** | 债务周期阶段识别作为 DriftMonitor 的宏观 Regime 输入 | 🔄 待集成 |
| `MomentumReversalEngine` | 老子"反者道之动" | **检测** | 均值回归信号作为漂移检测的补充 — 极端动量预示回归 | 🔄 待集成 |
| 风控六件套 T09-T14 | 孙子"先为不可胜" | **验证** | PreTradeGuard + CircuitBreaker + KillSwitch 作为 FeedbackLoop 的安全闸门 | ✅ 已集成 |
| `GradualRolloutOrchestrator` (T18) | 王阳明"知行合一" | **验证** | PAPER→SHADOW→PARALLEL→FULL 四阶段灰度发布 = 知行合一的工程实现 | ✅ 已集成 |
| `KondratievCycleAnalyzer` | 康德拉季耶夫 | **检测** | 长波周期阶段作为 DriftMonitor 的低频 Regime 背景 | 🔄 待集成 |

### 10.3 整合状态汇总

- **✅ 已集成 (3/10)**：HypothesisVerifier (验证) + 风控六件套 (验证) + GradualRolloutOrchestrator (验证)
- **🔄 待集成 (7/10)**：7 个哲学模块已实现但未接入闭环 — 主要在**检测**阶段（4 个）和**诊断**阶段（2 个）和**进化**阶段（1 个）

### 10.4 整合优先级与建议

| 优先级 | 模块 | 阶段 | 建议时机 | 预估工作量 |
|--------|------|------|---------|-----------|
| **P1** | `DalioEconomicMachine` → 检测 | DriftMonitor 宏观 Regime 输入 | Phase B B1 启用时 | 1 天 |
| **P1** | `SorosReflexivityEngine` → 检测 | DriftMonitor 前置信号 | Phase B B1 启用时 | 1 天 |
| **P2** | `nassim_taleb.py` → 诊断 | StrategyEvaluator composite_score | Phase B B2 启用时 | 0.5 天 |
| **P2** | `BuffettMungerModel` → 诊断 | StrategyEvaluator 长期质量维度 | Phase B B2 启用时 | 0.5 天 |
| **P2** | `MomentumReversalEngine` → 检测 | DriftMonitor 漂移补充 | Phase B B1 启用时 | 0.5 天 |
| **P3** | `KondratievCycleAnalyzer` → 检测 | DriftMonitor 低频背景 | Phase B B3 启用时 | 0.5 天 |
| **P3** | `FirstPrinciplesAnalyzer` → 进化 | AutoFactorFactory 设计方向 | Phase B B3 启用时 | 1 天 |

**总预估工作量**：~5 天，可在 Phase B B1-B3 启用期间分步完成，不阻塞主线。

### 10.5 与 §九 改进 5 的关系

本节是 §九.2 改进 5"哲学模块与自我进化闭环整合度不明"的落实。改进 5 标记为 P2 优先级，本节明确了整合路径和优先级，为 Phase B 启用后的分步集成提供执行计划。

**指针**：`cairn/philosophy-trading-mapping-20260819.md` §一（哲学模块代码位置）+ 本节 §十（闭环整合映射）。
