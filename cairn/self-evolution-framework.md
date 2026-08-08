---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-08-02
updated: 2026-08-05
contains: self-evolution-framework, feature-flags, drift-monitor, strategy-evaluator, auto-retrain-scheduler, feedback-loop, vol-regime-weighter
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
