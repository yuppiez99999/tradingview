# GAP_ANALYSIS 自我进化框架已有模块缺口分析

> **任务**：T0.1 深度盘点已有模块缺口
> **创建日期**：2026-08-02
> **分析方法**：逐模块源码阅读 + 未实现标记扫描 + 架构文档 §6 API 对照
> **关键结论**：8 个模块中 **7 个基本完整可直接启用**，仅 2 处真实缺口

---

## 1. 总览

| 模块 | 未实现标记 | 真实缺口 | 成熟度 | 可直接启用 |
|------|-----------|----------|--------|------------|
| `strategy_evaluator.py` | 0 | 0 | ✅ 完整 | ✅ 是 |
| `evolution_orchestrator.py` | 0 | 1（L1/L2/L3 路由） | ⚠️ 观察期模式完整，进化路由缺失 | ⚠️ 观察期模式可启用 |
| `drift_monitor.py` | 0 | 0 | ✅ 完整 | ✅ 是 |
| `auto_retrain_scheduler.py` | 2 | 1（_load_trained_model） | ⚠️ 近完整 | ⚠️ 需补 1 处 |
| `mlops_pipeline.py` | 4 | 0（4 个 pass 是容错） | ✅ 完整 | ✅ 是 |
| `model_registry.py` | 1 | 0（... 是 docstring） | ✅ 完整 | ✅ 是 |
| `shadow_account_adapter.py` | 0 | 0 | ✅ 完整 | ✅ 是 |
| `ab_testing.py` | 0 | 0 | ✅ 完整 | ✅ 是 |

**结论**：原计划 Phase 1 的 T1.6（补全 StrategyEvaluator）和 Phase 3 的 T3.2（补全 ABTest）可**取消或大幅缩减**。

---

## 2. 逐模块详细分析

### 2.1 strategy_evaluator.py — ✅ 完整，无需补全

| 架构文档要求 | 实现状态 | 位置 |
|--------------|----------|------|
| ScoreReport（Public/Private 分离） | ✅ L105 `class ScoreReport` | 含 public_score/private_score/reward_hacking_risk/recommendation |
| evaluate() 主入口 | ✅ L221 `def evaluate()` | 接收 daily_returns + signal_history |
| _compute_public_score() | ✅ L296 | 绝对收益 + 风险调整 |
| _compute_private_score() | ✅ L348 | 稳定性 + 稳健性 + 反作弊 + 复杂度 |
| _compute_reward_hacking_risk() | ✅ L457 | 接入 PITChecker |
| _check_pit_violations() | ✅ L500 | 6 维度未来函数检测 |
| _make_recommendation() | ✅ L575 | promote/rollback/continue |
| _build_degraded_report() | ✅ L798 | 空数据/小样本容错 |
| evaluate_from_jsonl() | ✅ L823 | 从 daily_returns.jsonl 读取 |
| evaluate_from_shadow() | ✅ L873 | 从影子账户读取 |
| Feature Flag 控制 | ✅ L202 `_check_feature_flag` | USE_STRATEGY_EVALUATOR |

**缺口**：无。架构文档 §6.1 的所有 API 均已实现。

### 2.2 evolution_orchestrator.py — ⚠️ 观察期完整，进化路由缺失

| 架构文档要求 | 实现状态 | 说明 |
|--------------|----------|------|
| MetricsSnapshot 数据结构 | ✅ L96 | 含 to_dict() |
| OrchestratorStatus 数据结构 | ✅ L121 | 含观察期状态 |
| DecisionRecord 数据结构 | ✅ L150 | 含 Public/Private Score |
| get_status() | ✅ L273 | |
| collect_metrics() | ✅ L285 | 只读收集 |
| evaluate_current() | ✅ L357 | 调用 StrategyEvaluator |
| log_decision() | ✅ L446 | 持久化到 decisions.jsonl |
| run_observation_cycle() | ✅ L519 | collect → evaluate → log 完整闭环 |
| 观察期管理（HC-4） | ✅ L579 | 观察期内强制 evaluate_only |
| **L1/L2/L3 三层路由** | ❌ **缺失** | 当前只有 evaluate_only，无进化动作路由 |
| **接入 EvolutionGuard** | ❌ **缺失** | 无 Guard 检查 |
| **接入 EvolutionMemory** | ❌ **缺失** | 无 Memory 审计 |
| **L3 人工审批闸门** | ❌ **缺失** | 无人工审批路由 |
| Feature Flag 控制 | ✅ L254 | USE_EVOLUTION_ORCHESTRATOR |

**缺口**：4 项，均为 Phase 3 范围（T3.1）。
**当前可用**：观察期模式可直接启用（只读评估，不触发动作），符合 P0 阶段需求。

### 2.3 drift_monitor.py — ✅ 完整，无需补全

| 架构文档要求 | 实现状态 | 位置 |
|--------------|----------|------|
| DriftMonitor 类 | ✅ L94 | 4 维度检测（IC/ADWIN/KS/PSI） |
| update_ic() | ✅ L163 | IC 衰减检测 |
| update_adwin() | ✅ L176 | ADWIN 概念漂移 |
| check_feature_drift() | ✅ L189 | 特征分布漂移 |
| check_all() | ✅ L202 | 全维度检测 |
| should_retrain() | ✅ L283 | 触发重训判断 |
| start/stop_monitoring() | ✅ L296/L316 | 后台监控循环 |
| SimModeDriftMonitor 类 | ✅ L708 | 仿真模式（含 compute_psi/compute_feature_drift/compute_prediction_drift） |
| DriftReport + DriftSeverity | ✅ L445/L428 | 报告结构 |
| Feature Flag 控制 | ✅ | USE_DRIFT_DETECTOR |

**缺口**：无。4 维度漂移检测全部实现。

### 2.4 auto_retrain_scheduler.py — ⚠️ 1 处真实 TODO

| 架构文档要求 | 实现状态 | 位置 |
|--------------|----------|------|
| AutoRetrainScheduler 类 | ✅ L109 | |
| RetrainTrigger 枚举 | ✅ L66 | 事件/定时/手动 |
| RetrainStatus 枚举 | ✅ L75 | |
| RetrainTask 数据结构 | ✅ L86 | 含 to_dict() |
| trigger_retrain() | ✅ L266 | 触发重训 |
| _run_training() | ✅ L330 | 训练流程 |
| _execute_training_script() | ✅ L373 | 执行训练脚本 |
| **_load_trained_model()** | ❌ **TODO L418** | `# TODO: 实际接入时从训练脚本输出加载模型` |
| _register_model() | ✅ L428 | 注册到 ModelRegistry |
| _start_ab_test() | ✅ L468 | 启动 A/B 测试 |
| start/stop() | ✅ L505/L529 | 调度器循环 |
| Feature Flag 控制 | ✅ | USE_AUTO_RETRAIN |

**缺口**：1 处（L418 `_load_trained_model` TODO）。
**影响**：重训后无法加载新模型，阻塞 AutoRetrain 的完整闭环。
**工作量**：S（< 4h）— 从训练脚本输出路径加载模型文件。
**阻塞**：阻塞 Phase 0 的 T0.4（DriftMonitor 触发重训链路），但不阻塞仅监控模式。

### 2.5 mlops_pipeline.py — ✅ 完整（4 个 pass 是容错，非缺口）

| 架构文档要求 | 实现状态 | 位置 |
|--------------|----------|------|
| MLOpsPipeline 类 | ✅ L46 | Facade 编排 |
| start/stop() | ✅ L179/L200 | |
| register_and_test() | ✅ L220 | 注册 + A/B 测试 |
| _on_drift_trigger() | ✅ L304 | 漂移触发回调 |
| get_status() | ✅ L329 | 含子模块状态收集 |
| _log_event() | ✅ L365 | 事件日志持久化 |
| get_pipeline() 单例 | ✅ L392 | |
| initialize_pipeline() | ✅ L400 | |
| Feature Flag 控制 | ✅ L99 | USE_MLOPS_PIPELINE |

**关于 4 个 pass（L343/L348/L355/L362）**：这些是 `get_status()` 中收集子模块状态的 `except Exception: pass` 容错处理，标注了 `# P2 模块 fail-safe`，是合理的降级模式，**不是未实现缺口**。

**缺口**：无。

### 2.6 model_registry.py — ✅ 完整（... 是 docstring，非缺口）

| 架构文档要求 | 实现状态 | 位置 |
|--------------|----------|------|
| ModelRegistry 类 | ✅ L123 | 双后端（MLflow + 本地） |
| ModelStage 枚举 | ✅ L71 | NONE/STAGING/PRODUCTION/ARCHIVED |
| ModelVersion 数据结构 | ✅ L93 | 含 to_dict/from_dict |
| register_model() | ✅ L245 | |
| transition_stage() | ✅ L350 | 阶段转换合法性检查 |
| promote_model() | ✅ L426 | |
| archive_model() | ✅ L430 | |
| load_model() | ✅ L505 | |
| _is_valid_transition() | ✅ L434 | 阶段转换合法性 |
| MLflow 集成 | ✅ L182 | 优先 MLflow，兜底本地 |

**关于 L138 的 `...`**：这是 docstring 中目录结构示例的一部分（`version_2/` 下的 `...` 表示省略），**不是代码占位符**。

**缺口**：无。

### 2.7 shadow_account_adapter.py — ✅ 完整，无需补全

| 架构文档要求 | 实现状态 | 位置 |
|--------------|----------|------|
| ShadowAccountAdapter 类 | ✅ L141 | |
| ShadowMetrics 数据结构 | ✅ L101 | |
| RunShadowResult 数据结构 | ✅ L124 | |
| run_shadow() | ✅ L214 | 影子账户运行 |
| get_metrics() | ✅ L300 | |
| compute_dsr() | ✅ L348 | DSR 计算 |
| compute_sharpe_cv() | ✅ L376 | Sharpe CV |
| FailFast 机制 | ✅ L91 | FailFastTriggeredError |
| _create_shadow_account() | ✅ L423 | |
| create_default_adapter() | ✅ L566 | |
| run_shadow_with_returns() | ✅ L580 | |

**缺口**：无。

### 2.8 ab_testing.py — ✅ 完整，无需补全

| 架构文档要求 | 实现状态 | 位置 |
|--------------|----------|------|
| ABTestFramework 类 | ✅ L203 | |
| ABTestConfig 数据结构 | ✅ L100 | 含 to_dict/from_dict |
| ABTestResult 数据结构 | ✅ L138 | |
| ABTest 数据结构 | ✅ L163 | |
| create_test() | ✅ L268 | 创建 A/B 测试 |
| start/stop_test() | ✅ L296/L314 | |
| assign_group() | ✅ L328 | 分组分配 |
| record_daily_metrics() | ✅ L390 | 记录每日指标 |
| evaluate_test() | ✅ L420 | 评估测试 |
| _t_test() | ✅ L514 | T 检验 |
| _cohens_d() | ✅ L547 | Cohen's d 效应量 |
| _make_recommendation() | ✅ L560 | promote/rollback/continue |
| promote_challenger() | ✅ L598 | |
| rollback_to_champion() | ✅ L637 | |

**缺口**：无。架构文档 §6 要求的 Champion/Challenger + 多维度晋升 + Cohen's d 全部实现。

---

## 3. 真实缺口汇总

仅 2 处真实缺口：

| # | 模块 | 位置 | 缺口 | 工作量 | 阻塞阶段 | 补全方式 |
|---|------|------|------|--------|----------|----------|
| 1 | `auto_retrain_scheduler.py` | L418 | `_load_trained_model` TODO | S (<4h) | T0.4 重训链路 | 从训练脚本输出路径加载模型文件 |
| 2 | `evolution_orchestrator.py` | — | L1/L2/L3 三层路由 + Guard + Memory + 人工闸门 | L (2-3d) | T3.1 进化层 | Phase 3 范围，添加路由逻辑 |

---

## 4. 对 TASK 计划的影响

### 4.1 可取消的任务

| 任务 | 原计划 | 调整 | 理由 |
|------|--------|------|------|
| T1.6 补全 StrategyEvaluator | L (2-3d) | ❌ **取消** | 模块已 100% 完整 |
| T3.2 补全 ABTestFramework | L (2-3d) | ❌ **取消** | 模块已 100% 完整 |

### 4.2 可缩减的任务

| 任务 | 原计划 | 调整 | 理由 |
|------|--------|------|------|
| T0.1 缺口盘点 | M (1d) | ✅ **已完成**（本文档） | 仅 2 处缺口 |
| T0.4 启用 DriftMonitor | M (1d) | ⬇️ **缩减为 S** | 模块完整，仅需启用 Flag |
| T0.5 启用 Evaluator | M (1d) | ⬇️ **缩减为 S** | 模块完整，仅需启用 Flag |
| T3.1 补全 Orchestrator | L (2-3d) | ⬇️ **缩减为 M** | 仅需添加 L1/L2/L3 路由，观察期模式已完整 |

### 4.3 工作量重新估算

| Phase | 原估 | 调整后 | 节省 |
|-------|------|--------|------|
| Phase 0 | 8 人日 | 5 人日 | -3 人日（模块完整，启用即可） |
| Phase 1 | 15 人日 | 11 人日 | -4 人日（T1.6 取消） |
| Phase 2 | 10 人日 | 10 人日 | — |
| Phase 3 | 18 人日 | 13 人日 | -5 人日（T3.2 取消，T3.1 缩减） |
| **合计** | **51 人日** | **39 人日** | **-12 人日（节省 24%）** |

---

## 5. P0 阶段启用建议

基于缺口分析，P0 阶段可立即推进：

| 步骤 | 模块 | 操作 | 风险 | 前置条件 |
|------|------|------|------|----------|
| 1 | DriftMonitor | 启用 `USE_DRIFT_DETECTOR` | 零（仅监控） | T0.2（Flag 配置） |
| 2 | StrategyEvaluator | 启用 `USE_STRATEGY_EVALUATOR` | 零（只读） | T0.2 |
| 3 | EvolutionOrchestrator | 启用 `USE_EVOLUTION_ORCHESTRATOR`（观察期模式） | 零（强制 evaluate_only） | T0.2 |
| 4 | MLOpsPipeline | 启用 `USE_MLOPS_PIPELINE` | 低（Facade，子模块独立 Flag 控制） | T0.2 |
| 5 | AutoRetrainScheduler | ⚠️ 暂缓启用 | 中（L418 TODO 阻塞完整闭环） | 先补全 _load_trained_model |

**建议**：P0 阶段启用步骤 1-4（4 个模块），步骤 5（AutoRetrain）待补全 L418 后启用。

---

**文档版本**：v1.0
**产出者**：T0.1 执行结果
**下一步**：T0.2（补全 Feature Flag 配置）+ T0.3（system_config evolution 段）
