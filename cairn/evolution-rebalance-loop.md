# 自我进化迭代再平衡闭环（Evolution-Rebalance Loop）

> 知识专题文档 · 创建 2026-08-21 · CodeArts
> 状态：4处断裂已补齐，闭环已打通，待 Feature Flag 启用后端到端验证

## 一、问题背景

系统具备"自我进化"能力（进化侧闭环）和"再平衡"能力（再平衡侧完整），但两者是**独立完整但未连接**的双孤岛架构，未实现"自我进化驱动再平衡→再平衡反馈进化"的迭代闭环。

### 1.1 进化侧自闭环（已打通）

```
EOD phase4_7: DriftShadowIntegrator 漂移检测 + IC 计算
EOD phase4_55: PnL 归因报告
EOD phase4_6: FeedbackLoop → factor_weights.json 因子权重更新
```

### 1.2 再平衡侧（独立运行）

```
HedgeRebalanceIntegrator.run_full_workflow() 五阶段联动
ETFOptionHedgeRebalancer.run_daily_rebalance() 六阶段
```

### 1.3 四处断裂

| 断裂 | 证据 | 后果 |
|------|------|------|
| 1. 因子权重→再平衡 | `check_rebalance` 用 `portfolio_config`，不读 `factor_weights.json` | 进化权重不影响再平衡 |
| 2. V2→生产路径 | `EvolutionOrchestratorV2.run_cycle()` 仅测试调用 | V2 是实验室代码 |
| 3. evolution_action→决策 | 再平衡 L660 先完成，进化 L685 后运行（时序倒置） | 进化结果仅审计 |
| 4. drift→再平衡触发 | drift 触发 retrain 非再平衡 | 漂移不触发再平衡 |

## 二、设计决策

### 2.1 核心原则

1. **优雅降级**：进化数据不可用时回退到原有逻辑，不阻断主链路
2. **乘子约束**：因子权重乘子限制 `[0.5, 2.0]`，防止单次进化调整极端值导致再平衡风暴
3. **fail-safe**：所有进化相关调用 try/except 包裹，失败降级到 logger.warning
4. **向后兼容**：新增功能通过 Feature Flag / 可选参数控制，不破坏现有接口

### 2.2 为什么用乘子而非绝对权重

进化框架 FeedbackLoop 更新 `factor_weights.json` 产出的是**相对调整因子**（如某因子近期表现好→乘子 1.2，表现差→0.8），而非绝对目标权重。再平衡引擎的 `target_weight` 来自 `portfolio.yaml` 配置，用乘子相乘保留配置基准、仅做增量调整，避免进化框架直接覆盖配置导致失控。

### 2.3 为什么进化前置于再平衡

原设计时序倒置：再平衡先完成 → 进化后运行 → 进化结果仅存 `plan.evolution_action` 审计字段。修正后：进化前置 → `weight_adjustments` 应用到 `target_weights` → 再平衡消费调整后权重。这样进化结果**实际影响**再平衡订单，而非仅记录。

## 三、实现细节

### 3.1 断裂1：因子权重→再平衡

**文件**: `utils/hedge_rebalance_integrator.py`

新增 `_load_evolution_factor_weights()` 方法：
- 读取 `config/factor_weights.json`
- 返回 `{code: weight_multiplier}` 字典
- 乘子限制 `[0.5, 2.0]` 过滤极端值
- 失败返回空字典（优雅降级）

`check_rebalance()` 修改：
```python
evolution_weights = self._load_evolution_factor_weights()
# ... 在循环中 ...
if code in evolution_weights:
    target_weight = target_weight * evolution_weights[code]
```

### 3.2 断裂2：V2→生产路径

**文件**: `15_每日工作流/run_daily_eod_workflow.py`

新增 `run_phase4_9_evolution_cycle()` 函数：
- 实例化 `EvolutionOrchestratorV2`
- 检查 `orchestrator.enabled`（Feature Flag）
- 调用 `run_cycle()` 感知→决策→行动→学习
- fail-safe：异常不中断 EOD 主流程

主流程插入位置：phase4_6 FeedbackLoop 之后、phase5 归档之前。

### 3.3 断裂3：时序倒置修正

**文件**: `etf_option_hedge_rebalancer.py`

`run_daily_rebalance()` 重构为6阶段：
- Phase 4/6: 期权对冲决策
- **Phase 5/6: 自我进化前置**（漂移检测 → 进化编排 → `weight_adjustments` 应用到 `target_weights`）
- Phase 6/6: 阈值再平衡（消费调整后权重）

进化权重应用逻辑：
```python
weight_adjustments = plan.evolution_action.get("weight_adjustments", {})
for code, multiplier in weight_adjustments.items():
    if code in target_weights and 0.5 <= multiplier <= 2.0:
        target_weights[code] *= float(multiplier)
```

### 3.4 断裂4：drift→再平衡触发

**文件**: `utils/alpha/drift_monitor.py`

`__init__` 新增 `rebalance_callback` 可选参数。
`_check_retrain_trigger()` 修改：漂移触发时同时调用 `rebalance_callback(alerts)`，不仅触发模型重训，还触发再平衡。

## 四、闭环链路（已打通）

```
EOD 工作流
  ↓ phase4_7: 漂移检测 (DriftMonitor)
  ↓     └─ 漂移触发 → retrain_callback + rebalance_callback [断裂4]
  ↓ phase4_6: FeedbackLoop → factor_weights.json
  ↓ phase4_9: V2.run_cycle() 进化编排 [断裂2]
  ↓     └─ 进化决策 → factor_weights.json
  ↓
ETF期权对冲再平衡 (run_daily_rebalance)
  ↓ Phase 5/6: 进化前置 — drift + evolution_orchestrator [断裂3]
  ↓     └─ weight_adjustments → target_weights
  ↓ Phase 6/6: check_rebalance(target_weights)
  ↓     └─ _load_evolution_factor_weights() × target_weight [断裂1]
  ↓ → 再平衡订单执行
```

## 五、验证结果

- **ruff**: 4个修改文件 `All checks passed` ✅
- **pytest**: 229 passed / 5 failed（5个失败经 `git stash` 验证为修改前已存在的配置漂移，非本次回归）
- **向后兼容**: `factor_weights.json` 不存在时降级，`rebalance_callback=None` 时不触发

## 六、踩坑记录

### 6.1 `# allow-print` 非 ruff noqa 语法

**踩坑**: `etf_option_hedge_rebalancer.py` 的 `__main__` 块 print 加 `# allow-print` 注释，ruff 不识别（这是 pre-commit hook 的自定义语法）。改用标准 `# noqa: T201` 解决。

**教训**: AGENTS.md 中 `# allow-print` 是 `scripts/pre_commit_check.py` 的 P0 门禁语法，ruff 需用 `# noqa: T201`。

### 6.2 测试失败需区分回归 vs 配置漂移

**踩坑**: 修改后 `test_etf_option_hedge_rebalancer_unit.py` 5 个失败，初看像回归。用 `git stash` 验证发现修改前已失败（`target_annual_return` 0.08→0.095、`raw_breach_count` 6→3 等配置漂移）。

**教训**: 修改核心模块后，必须用 `git stash` 对比基线区分"我的回归" vs "已有漂移"。

## 七、后续工作

1. **~~Feature Flag 启用~~**: `USE_EVOLUTION_ORCHESTRATOR` / `USE_DRIFT_DETECTOR` 已注册，Shadow 验证完成 ✅
2. **~~端到端测试~~**: 23 单元 + 7 E2E + 18 Shadow E2E = 48 测试全部通过 ✅
3. **~~`weight_adjustments` 字段对接~~**: `CycleResult` 已含字段 + `_derive_weight_adjustments()` 已实现 ✅
4. **~~再平衡反馈进化~~**: 再平衡执行结果回写 `daily_returns.jsonl`，下一轮 `collect_metrics` 读取再平衡后收益 ✅
5. **监控指标**: 新增闭环健康度指标（进化触发率/再平衡调整幅度/闭环延迟）

## 八、Shadow 验证结果（阶段2 ✅）

### 8.1 Flag 注册一致性修复

`USE_DRIFT_DETECTOR` 原未在 `config/feature_flags.yaml` 注册（仅靠环境变量回退），已补齐注册：
- `default: false`（HC-1 不破坏基线）
- `requires_dual_sign: true`（与 `USE_EVOLUTION_ORCHESTRATOR` 一致，防误启用）

### 8.2 Shadow 验证 E2E 测试（18 场景）

**文件**: `tests/e2e/test_evolution_rebalance_shadow_e2e.py`

| 场景 | 验证内容 | 结果 |
|------|---------|------|
| 1. 进化驱动再平衡 | weight_adjustments→target_weights 调整→Shadow 记录 | ✅ |
| 2. Flag 禁用降级 | CYCLE_STATUS_DISABLED + 空权重 + 原始逻辑 | ✅ |
| 3. 乘子约束 | [0.5, 2.0] 边界 + 越界过滤 + clamp | ✅ |
| 4. fail-safe | V2 异常→空字典 + Shadow 不受影响 | ✅ |
| 5. 完整闭环 | V2→to_dict→weight_adjustments→再平衡→Shadow | ✅ |
| 6. 指标对比 | 有/无进化调整 final_nav 差异 + 方向一致性 | ✅ |

### 8.3 关键发现

- `ShadowAccountAdapter.run_shadow()` 的 `final_nav` 是**比率**（从 1.0 开始），非绝对金额
- `USE_DRIFT_DETECTOR` 与 `USE_DRIFT_MONITOR` 是不同 Flag：前者控制 KS/PSI 检测+再平衡回调，后者控制漂移监控器本身
- V2 orchestrator 的 L2 路由目前仅注释"影子验证由 ShadowAccountAdapter 完成"，未直接调用 adapter（待阶段4增强）

## 九、再平衡→进化反馈链（阶段3 ✅）

### 9.1 缺口本质

原闭环是"进化→再平衡"单向。再平衡执行后结果不回写 `daily_returns.jsonl`，下一轮进化 `collect_metrics` 仅读到市场行情收益，无法评估再平衡效果。

### 9.2 实现

**文件**: `etf_option_hedge_rebalancer.py`

新增 `_write_rebalance_feedback_to_shadow(plan, positions, prices)` 方法：
- 计算再平衡后组合日收益：`Σ(target_weight × daily_return) / Σ|target_weight|`
- 空持仓降级：`estimated_annual_return / 252` 日化估算
- 增量更新 `reports/shadow/daily_returns.jsonl`（同日期去重，与 ShadowRealDataFeeder 格式一致）
- 新增标记字段：`rebalance_executed` / `rebalance_orders_count` / `evolution_applied`
- fail-safe：异常仅 `logger.warning`，不影响再平衡主流程

在 `run_daily_rebalance()` 的 `return plan` 前调用。

### 9.3 闭环链路（双向已打通）

```
进化→再平衡 (阶段1):
    V2.run_cycle() → weight_adjustments → factor_weights.json → check_rebalance()

再平衡→进化 (阶段3):
    run_daily_rebalance() → _write_rebalance_feedback_to_shadow()
    → daily_returns.jsonl (source=rebalance_feedback_v86)
    → 次日 collect_metrics() 读取再平衡后收益
    → evaluate_current() 评估再平衡效果
    → route_proposal() 生成新一轮进化决策
```

### 9.4 测试覆盖（22 测试）

| 层级 | 文件 | 测试数 | 覆盖 |
|------|------|--------|------|
| unit | `test_rebalance_feedback_chain_unit.py` | 14 | 回写/增量更新/fail-safe/collect_metrics读取/格式兼容 |
| E2E | `test_rebalance_feedback_chain_e2e.py` | 8 | 完整闭环/多日累积/覆盖语义/反馈链闭合 |

### 9.5 关键设计决策

- **覆盖语义**：同日期再平衡回写覆盖市场行情记录（`source` 从 `w13a_real_market_feed` → `rebalance_feedback_v86`），下一轮进化评估再平衡后收益而非原始市场收益
- **格式兼容**：回写记录包含 ShadowRealDataFeeder 的所有必需字段 + 3个扩展标记字段，`collect_metrics` 只读 `date` + `daily_return` 不受影响
- **None 防御**：positions 值为 None 时 `isinstance(pos, dict)` 检查跳过，避免 AttributeError

## 十一、L2 影子验证 ShadowAccountAdapter 集成 + 闭环健康度指标（阶段4）

### 11.1 问题背景

V2 编排器 `_route_l2()` 原实现仅注释"实际影子验证由 ShadowAccountAdapter 完成 (本编排器只做路由)"，但从未实际调用 adapter — L2 提案通过 Guard 后直接标记 `executed`（假设影子验证通过）。这意味着 DSR 未达标或 fail-fast 触发时不会自动 rollback，违反 L2 "影子验证 + 自动 Promote/Rollback" 的设计意图。

### 11.2 L2 路由重构

`_route_l2()` 现在的完整流程：

```
Guard 通过 → Memory.record(pending)
  → _get_shadow_adapter() (懒加载)
    → adapter=None → 降级假设通过 (DEGRADED)
  → _load_shadow_daily_returns() 从 daily_returns.jsonl 读取
    → 样本 < MIN_SAMPLES_FOR_DSR → 降级假设通过 (DEGRADED)
  → adapter.run_shadow(daily_returns, dates)
    → fail_fast_triggered → rollback (ROLLED_BACK)
  → adapter.get_metrics()
    → DSR ≥ l2_dsr_threshold → promote (SUCCESS, executed=True)
    → DSR < l2_dsr_threshold → rollback (ROLLED_BACK, executed=False)
  → 异常 → 降级假设通过 (DEGRADED)
```

### 11.3 新增 API

| 组件 | 位置 | 用途 |
|------|------|------|
| `CYCLE_STATUS_ROLLED_BACK` | `orchestrator.py` 常量 | L2 影子验证未通过状态 |
| `DEFAULT_L2_DSR_THRESHOLD` | `orchestrator.py` 常量 | DSR 阈值默认 0.5 |
| `shadow_adapter` 参数 | `__init__` | ShadowAccountAdapter 实例（懒加载） |
| `l2_dsr_threshold` 参数 | `__init__` | DSR 阈值（可配置） |
| `_get_shadow_adapter()` | 懒加载方法 | 加载 ShadowAccountAdapter，失败返回 None |
| `_load_shadow_daily_returns()` | 辅助方法 | 从 `daily_returns.jsonl` 读取 (returns, dates) |
| `get_loop_health_metrics()` | 公共方法 | 闭环健康度指标快照 |

### 11.4 闭环健康度指标

`get_loop_health_metrics()` 返回：

| 指标 | 类型 | 含义 |
|------|------|------|
| `total_cycles` | int | 总循环次数 |
| `evolution_trigger_count` | int | 触发进化动作次数 |
| `evolution_trigger_rate` | float | 进化触发率 (trigger / total) |
| `l1_count` | int | L1 自动修复次数 |
| `l2_promote_count` | int | L2 影子验证通过次数 |
| `l2_rollback_count` | int | L2 影子验证回滚次数 |
| `l2_promote_rate` | float | L2 promote 率 (promote / (promote + rollback)) |
| `l3_pending_count` | int | L3 人工审批待审次数 |
| `avg_latency_ms` | float | 平均循环延迟 (ms) |
| `last_latency_ms` | float | 最近循环延迟 (ms) |
| `avg_weight_adjustment_magnitude` | float | 平均权重调整幅度 avg(\|multiplier - 1.0\|) |

### 11.5 fail-safe 策略

所有 L2 影子验证相关调用均 try/except 包裹，失败时降级假设通过（不阻塞进化）：

| 场景 | 行为 | 状态 |
|------|------|------|
| adapter 不可用 | 降级假设通过 | DEGRADED, executed=True |
| 样本不足 | 降级假设通过 | DEGRADED, executed=True |
| run_shadow 异常 | 降级假设通过 | DEGRADED, executed=True |
| get_metrics 异常 | 降级假设通过 | DEGRADED, executed=True |
| fail-fast 触发 | 自动回滚 | ROLLED_BACK, executed=False |
| DSR < 阈值 | 自动回滚 | ROLLED_BACK, executed=False |
| DSR ≥ 阈值 | 自动晋升 | SUCCESS, executed=True |

### 11.6 测试覆盖（41 测试）

| 层级 | 文件 | 测试数 | 覆盖 |
|------|------|--------|------|
| unit | `test_l2_shadow_integration_unit.py` | 27 | adapter懒加载/returns读取/L2路由promote/rollback/fail-fast/降级/健康度指标 |
| E2E | `test_l2_shadow_verification_e2e.py` | 14 | 真实adapter完整闭环/DSR边界/再平衡反馈链读取/多循环健康度 |

### 11.7 关键设计决策

- **DSR 阈值可配置**：`l2_dsr_threshold` 默认 0.5，可通过 `__init__` 参数调整
- **降级不阻塞**：所有 fail-safe 路径均 `executed=True`（假设通过），避免影子验证问题阻塞进化流程
- **rollback 不执行**：`CYCLE_STATUS_ROLLED_BACK` 时 `executed=False`，提案不应用，Memory 记录 `rolled_back`
- **健康度内嵌**：指标追踪在 orchestrator 实例上（`_loop_health` dict），不引入外部依赖
- **L2 计数双追踪**：`_route_l2` 直接增量 + `_update_loop_health` 基于 `result.status` 追踪（两条路径均覆盖）

## 十二、生产灰度发布（阶段5）

### 12.1 灰度发布策略

三阶段渐进式启用 `USE_EVOLUTION_ORCHESTRATOR`：

| 阶段 | 流量比例 | 观察期 | 推进条件 |
|------|----------|--------|----------|
| Stage 1 | 10% | 3 日 | 健康度达标 |
| Stage 2 | 50% | 3 日 | 健康度达标 |
| Stage 3 | 100% | 持续 | — |

灰度判断：`hash(date) % 100 < rollout_percent` — 确定性哈希，同一日期结果一致。

### 12.2 灰度发布管理器

`scripts/gradual_rollout_manager.py`：

| API | 用途 |
|-----|------|
| `should_run_on_date(date, percent)` | 灰度判断 |
| `should_run_today()` | 今日是否启用 |
| `advance_stage(status)` | 推进到下一阶段（需观察期满+健康度达标） |
| `rollback(status, reason)` | 紧急回滚（禁用 Flag + 状态标记） |
| `check_health(status)` | 健康度检查（3 项阈值） |
| `load_status() / save_status()` | 状态持久化 `reports/evolution/rollout_status.json` |

CLI: `--check` / `--advance` / `--rollback` / `--auto`

### 12.3 orchestrator 灰度集成

`run_cycle()` 在 Feature Flag 检查后新增 `_check_rollout_eligible()`：
- 灰度命中 → 正常执行
- 灰度未命中 → `CYCLE_STATUS_DISABLED` (reason="rollout_percent_excluded")
- 灰度管理器不可用 → 不阻塞（容错返回 True，由 Feature Flag 控制）

### 12.4 健康度阈值

| 指标 | 阈值 | 含义 |
|------|------|------|
| `l2_promote_rate` | ≥ 0.3 | L2 影子验证通过率不低于 30% |
| `avg_latency_ms` | ≤ 5000 | 平均循环延迟不超过 5 秒 |
| `evolution_trigger_rate` | ≥ 0.1 | 进化触发率不低于 10% |
| `total_cycles` | ≥ 3 | 至少 3 次循环才检查 |

### 12.5 监控集成

`run_evolution_eval.py` `collect_progress_snapshot()` 新增：
- `loop_health`: `EvolutionOrchestratorV2.get_loop_health_metrics()` 快照
- `rollout`: `RolloutStatus.to_dict()` 灰度发布状态

`print_progress_summary()` 输出：
```
[闭环健康] cycles=10, trigger_rate=0.80, l2_promote=7, l2_rollback=1, promote_rate=0.88, avg_latency=120.5ms
[灰度发布] stage=STAGE_2_50PCT, percent=50%, start=2026-08-20
```

### 12.6 测试覆盖（29 测试）

| 类别 | 测试数 | 覆盖 |
|------|--------|------|
| 灰度判断 | 5 | 0%/100%/确定性/分布近似/不同日期 |
| 状态序列化 | 3 | 默认/round-trip/无效阶段 |
| 状态持久化 | 2 | save-load/不存在 |
| 健康度检查 | 5 | 不可用/样本不足/promote低/延迟高/全通过 |
| 阶段推进 | 5 | 从NOT_STARTED/PAUSED/ROLLED_BACK/最大/观察未满 |
| 回滚 | 2 | 状态设置/历史记录 |
| 观察天数 | 2 | 无开始日期/3天 |
| 今日灰度 | 2 | 0%→false/100%→true |
| orchestrator集成 | 3 | 管理器不可用/未命中DISABLED/命中正常 |

### 12.7 安全约束

- **HC-1**: 每阶段推进需前阶段健康度达标（3 项阈值全过）
- **HC-2**: 任何阶段可一键回滚（`--rollback` CLI + `disable()` Flag）
- **HC-3**: 所有状态变更写入 `reports/evolution/rollout_status.json` 审计
- **HC-4**: 灰度管理器不可用时不阻塞（容错 True，由 Feature Flag 兜底）
- **HC-5**: Feature Flag 双签启用 + 单签禁用

## 十三、剩余缺口补全计划（Wave 7-ERL 子轨道）

> 创建 2026-08-22 · CodeArts
> 状态：Sprint 1 (ER-1.1/1.2/1.3) 全部提前完成 2026-08-27; Sprint 2-3 待启动
> 背景：阶段 1-5 完成策略级进化→再平衡闭环后，调查发现 4 个剩余缺口阻碍"完成进化后能自我再平衡"全链路自动化

### 13.1 缺口分析

| 缺口 | 现状 | 影响 | 证据 |
|------|------|------|------|
| **G1** 模型训练→再平衡联动 | `autolearn_trainer.py`/`lgb_enhanced_trainer.py`/`lgb_tscv_trainer.py`/`ml_enhanced` 训练后无回调 | 训练成果无法自动应用到组合，需手动运行 `python etf_option_hedge_rebalancer.py` | 训练器无 `rebalance`/`trigger`/`callback` 关键词 |
| **G2** 漂移→再平衡回调生产启用 | `DriftMonitor.rebalance_callback` 参数已存在（`drift_monitor.py:121`）但生产实例化未传入 | 漂移仅触发重训，不触发再平衡 | `etf_option_hedge_rebalancer.py:296` 等处 `DriftMonitor()` 不传 `rebalance_callback` |
| **G3** institutional pipeline 集成 | `institutional_pipeline_runner.py` 无 evolution/rebalance phase | 机构管道与进化再平衡脱节 | 管道步骤 Step 1-7 无 evolution/rebalance |
| **G4** 灰度发布推进 | `USE_EVOLUTION_ORCHESTRATOR` 默认 false，阶段5灰度进行中（STAGE_2_50PCT） | 闭环未全量生产启用 | `feature_flags.yaml:9` |

### 13.2 排期（09-05 ~ 12-31，3 Sprint）

> 与 Wave 7 Sprint 1-4 并行，避开 ETF期权对冲 Phase 4 灰度窗口（10-06~11-05）实盘验证资源争用

#### Sprint 1（09-05 ~ 09-26）：训练→再平衡联动 + 漂移回调启用

| 任务 | 时间 | 内容 | 关联缺口 |
|------|------|------|---------|
| ER-1.1 | 09-05~09-12 | 训练器新增 `post_train_callback` 钩子（`autolearn_trainer.py`/`lgb_enhanced_trainer.py`/`lgb_tscv_trainer.py`），fail-safe 降级 ✅ DONE 2026-08-27 | G1 |
| ER-1.2 | 09-13~09-19 | 训练→进化→再平衡串联：训练完成 → `EvolutionOrchestratorV2.run_cycle()` → `run_daily_rebalance()`，受 `USE_EVOLUTION_ORCHESTRATOR` 控制 ✅ DONE 2026-08-27 | G1 |
| ER-1.3 | 09-20~09-26 | 漂移→再平衡回调生产启用：`etf_option_hedge_rebalancer.py:296` 等处实例化 `DriftMonitor` 时传入 `rebalance_callback`，受 `USE_DRIFT_DETECTOR` 控制 ✅ DONE 2026-08-27 | G2 |

#### Sprint 2（09-27 ~ 10-31）：institutional pipeline 集成

| 任务 | 时间 | 内容 | 关联缺口 |
|------|------|------|---------|
| ER-2.1 | 09-27~10-10 | `institutional_pipeline_runner.py` 新增 `phase_evolution`（Step 4.6），调用 `EvolutionOrchestratorV2` ✅ 代码就绪 2026-08-27 | G3 |
| ER-2.2 | 10-11~10-24 | 新增 `phase_rebalance`（Step 6.5），调用 `etf_option_hedge_rebalancer.run_daily_rebalance()` ✅ 代码就绪 2026-08-27 | G3 |
| ER-2.3 | 10-25~10-31 | 管道编排确认：data→factors→**evolution**→signals→**rebalance**→execution→report，Feature Flag 控制 ✅ DONE 2026-08-27 | G3 |

#### Sprint 3（11-01 ~ 12-31）：灰度发布 + 端到端验证

| 任务 | 时间 | 内容 | 关联缺口 |
|------|------|------|---------|
| ER-3.1 | 11-01~11-14 | 灰度 Stage 1 (10%)，观察 `evolution_trigger_rate`/`l2_promote_rate`/`avg_weight_adjustment_magnitude` | G4 |
| ER-3.2 | 11-15~11-30 | 灰度 Stage 2 (50%)，健康度达标后推进 | G4 |
| ER-3.3 | 12-01~12-15 | 灰度 Stage 3 (100%)，全量启用 | G4 |
| ER-3.4 | 12-16~12-31 | 端到端验证 + 知识沉淀（更新本文档 + LOG） | 全部 |

### 13.3 验收标准

- [x] 训练完成→再平衡自动触发（无需人工干预） ✅ ER-1.2 `train_rebalance_bridge.py`
- [x] 漂移→再平衡回调生产生效（`DriftMonitor` 实例化传入 `rebalance_callback`） ✅ ER-1.3
- [x] `institutional_pipeline --phase all` 含 evolution + rebalance 阶段 ✅ ER-2.1/2.2/2.3 代码就绪
- [ ] 灰度 100% 健康度达标（`l2_promote_rate` ≥ 0.3 / `evolution_trigger_rate` ≥ 0.1 / `avg_latency_ms` ≤ 5000）
- [ ] 全链路测试覆盖（单元 + E2E + Shadow）

### 13.4 设计原则

1. **优雅降级**：所有新增联动 fail-safe，回调异常不阻断训练/管道主流程
2. **Feature Flag 控制**：G1/G2 受 `USE_EVOLUTION_ORCHESTRATOR`/`USE_DRIFT_DETECTOR` 控制，关闭时行为不变
3. **乘子约束**：训练→进化产出的 `weight_adjustments` 仍限 [0.5, 2.0]（与 §2.1 一致）
4. **向后兼容**：`post_train_callback` 默认 None，不传时训练行为不变
5. **与 Wave 7 协调**：Sprint 1-2 不侵入生产链路（纯新增 phase + 回调），Sprint 3 灰度与 Wave 7 Sprint 3-4 实盘验证窗口错开

### 13.5 与既有排期协调

| Wave 7-ERL 任务 | 时间窗口 | 与 Wave 7 重叠 | 协调措施 |
|----------------|---------|--------------|---------|
| ER-1.x | 09-05~09-26 | Wave 7 Sprint 1 后半 + Sprint 2 前半 | 纯新增钩子/回调，不侵入既有任务 |
| ER-2.x | 09-27~10-31 | Wave 7 Sprint 2 后半 + Sprint 3 前半 | 管道新增 phase，与 W7.2.x 实盘验证错开 |
| ER-3.x | 11-01~12-31 | Wave 7 Sprint 3 后半 + Sprint 4 | 灰度发布复用 `gradual_rollout_manager.py`，与 v8.7 发布同步 |

## 十四、指针

- 本文档: `cairn/evolution-rebalance-loop.md`
- ER-1.1 钩子: `autolearn_trainer.py:invoke_post_train_callback` (训练后回调入口)
- ER-1.2 串联桥接: `utils/evolution/train_rebalance_bridge.py:make_train_evolution_rebalance_callback` (训练→进化→再平衡串联)
- ER-1.3 漂移回调: `etf_option_hedge_rebalancer.py:_make_drift_rebalance_callback`
- 断裂2实现: `15_每日工作流/run_daily_eod_workflow.py:run_phase4_9_evolution_cycle`
- 断裂3实现: `etf_option_hedge_rebalancer.py:run_daily_rebalance` Phase 5/6
- 断裂4实现: `utils/alpha/drift_monitor.py:__init__` rebalance_callback + `_check_retrain_trigger`
- 反馈链实现: `etf_option_hedge_rebalancer.py:_write_rebalance_feedback_to_shadow`
- L2 影子验证: `utils/evolution/orchestrator.py:_route_l2` (ShadowAccountAdapter 集成)
- 闭环健康度: `utils/evolution/orchestrator.py:get_loop_health_metrics`
- 灰度发布管理器: `scripts/gradual_rollout_manager.py`
- 灰度比例检查: `utils/evolution/orchestrator.py:_check_rollout_eligible`
- 监控集成: `scripts/run_evolution_eval.py:collect_progress_snapshot` (loop_health + rollout)
- 进化框架文档: `cairn/self-evolution-framework.md`
- ETF期权对冲模型: `cairn/etf-option-hedge-model.md`
- 代码质量基线: `cairn/code-quality-industrial-gap-20260819.md`

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [自我进化框架](self-evolution-framework.md) (相似度 19%)
- [A股ETF + 期权对冲 + 自我再平衡子模型](etf-option-hedge-model.md) (相似度 13%)
- [GitHub 高价值项目集成策略（Wave 6）](github-integration-wave6.md) (相似度 12%)
- [运维部署 + 灰度发布 (2026-08-24)](ops-gray-release-20260824.md) (相似度 9%)
- [代码架构与模块导航](architecture-map.md) (相似度 9%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
