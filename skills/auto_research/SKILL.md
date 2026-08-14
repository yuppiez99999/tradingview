---
name: auto_research
description: 自动化因子研究 pipeline，实现「假设生成 → G15 回测 → S1-S7 门禁 → DSR 验证 → 入库决策」闭环。适用于批量候选因子筛选、新因子入库前评估、因子衰减监控与自动退役场景。
key words: 因子研究，自动迭代，S1-S7 门禁，DSR 验证，因子入库，因子退役
---

# AutoResearch Skill

## 技能定位

本技能在 G15 事件驱动回测 + AlphaFactorLibrary + HonestValidation 三件套之上，实现因子「发现 → 评估 → 门禁 → 入库 → 监控 → 退役」自动闭环。默认 Shadow / dry_run 模式，不直接修改生产因子库。

## 核心闭环

```
FactorGenerator  →  list[FactorCandidate]
       ↓
FactorEvaluator  →  FactorEvaluationResult
  (AlphaFactorLibrary + G15 EventDrivenEngine + HonestValidation)
       ↓
FactorGate (S1-S7)  →  pass / reject
       ↓
FactorRegistry  →  register / retire
       ↓
FactorDecayMonitor  →  自动退役 (ICIR<阈值持续 N 月)
```

## S1-S7 门禁定义

| 阶段 | 名称 | 阈值 | 类型 |
| :--- | :--- | :--- | :--- |
| S1 | effective IC | ≥ 0.03 | 离线 |
| S2 | effective ICIR | ≥ 0.30 | 离线 |
| S3 | 多空夏普 | ≥ 1.0 | 离线 |
| S4 | 与现有因子相关性 | < 0.7 | 离线 |
| S5 | 基准组合夏普边际改善 | ≥ 0.05 | 离线 |
| S6 | 纸交易 | ≥ 63 天 (3 月) | 长期跟踪 |
| S7 | 小资金 5-10% 灰度 | ≥ 63 天 (3 月) | 长期跟踪 |

## 执行流程

### Step 1：构造研究上下文

准备 `ResearchContext`，包含行情数据、基本面数据、已注册活跃因子清单、基准组合权益曲线。上下文跨 Generator/Evaluator/Gate/Registry 共享。

### Step 2：生成候选因子

调用 `FactorGenerator.generate(context)` 生成候选列表。默认实现 `ExpressionFactorGenerator` 基于表达式模板批量生成；可替换为 `LLMFactorGenerator`（Phase D 衔接）或 `MiningFactorGenerator`。

### Step 3：逐个评估 + 门禁

对每个候选因子：
1. `FactorEvaluator.evaluate(candidate, context)` 产出 `FactorEvaluationResult`（含 Tear Sheet + Engine Summary + Honest Validation）
2. 串联 S1-S5 门禁，首次失败即停止该候选
3. 全部门禁通过 + 非 dry_run → `FactorRegistry.register`

### Step 4：DSR 验证（诚实回测三件套）

通过 S1-S5 的因子进入 HonestValidation：
- **CPCV**：组合清洗交叉验证 → 多路径 Sharpe 分布（CV < 0.5）
- **DSR**：Deflated Sharpe Ratio → 多重检验修正（DSR ≥ 0.95）
- **Noise**：噪音注入稳定性测试 → 过拟合检验

三件套联合判定 `is_honest = DSR.is_pass AND Noise.is_stable AND CPCV.CV < 0.5`。

### Step 5：入库决策

- `dry_run=True`（默认）：仅记录日志，不写入生产因子库
- `dry_run=False`：需显式构造 config 并经人工审批，`FactorRegistry.register` 实际写入

### Step 6：衰减监控 → 自动退役

`monitor_and_retire(active_factors, decay_signals)`：当因子 ICIR < 阈值（默认 0.2）持续 N 月（默认 6 月）时自动退役。

## 用法示例

```python
from ai_decision.auto_research_skill import (
    AutoResearchSkill, AutoResearchConfig, ResearchContext, InMemoryFactorRegistry,
)
from ai_decision.auto_research_defaults import (
    ExpressionFactorGenerator, StandardFactorEvaluator,
    S1EffectiveICGate, S2EffectiveICIRGate, S3LongShortSharpeGate,
    S4OrthogonalGate, S5BacktestIncrementGate,
)

skill = AutoResearchSkill(
    generator=ExpressionFactorGenerator(),
    evaluator=StandardFactorEvaluator(),
    gates=[S1EffectiveICGate(), S2EffectiveICIRGate(), S3LongShortSharpeGate(),
           S4OrthogonalGate(), S5BacktestIncrementGate()],
    registry=InMemoryFactorRegistry(),
    config=AutoResearchConfig(dry_run=True),
)

context = ResearchContext(
    price_data={...},
    active_factors=["MOM_60D", "VOL_20D"],
    baseline_equity_curve=[...],
)

iteration = skill.run_iteration(context)
print(f"候选 {len(iteration.candidates_generated)} / 入库 {len(iteration.promoted_factors)}")

retired = skill.monitor_and_retire(
    active_factors=skill._registry.list_active(),
    decay_signals={"MOM_60D": 0.15},  # ICIR < 0.2 → 退役
)
```

## 质量要求

1. **Shadow 铁律**：`dry_run=True` 时 `FactorRegistry.register` 仅记录日志，不写入生产。切换 `dry_run=False` 需显式构造 config 并经人工审批。
2. **门禁不可跳过**：S1-S5 离线门禁全部通过才可注册；S6/S7 长期跟踪门禁通过才可上实盘。
3. **诚实回测**：DSR 验证不可跳过，防止多重检验过拟合。
4. **不可变性**：所有数据类 `frozen=True`（GateStatus 除外，因 S6/S7 为长期跟踪态）。
5. **可替换性**：Generator/Evaluator/Gate/Registry 均为 ABC，支持自定义实现。

## 依赖前置

- G15 事件驱动回测（W6.3 Sprint 3，2026-08-12 完成）
- AlphaFactorLibrary 13+ 大类 117 因子（W6.1 Sprint 1）
- HonestValidation 三件套（W6.6.2）
- 因子表达式引擎（W6.6.1）
- LLM 智能进化 Phase D（D1-D4，2026-08-12 完成，可选衔接）
