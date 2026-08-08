# T0.1 缺口盘点 — 自我进化框架技术债务缺口分析

**盘点日期：2026-08-02**  
**来源：PROGRESS_REPORT_2026-08-02 + 全代码库审计**  
**阶段：Phase 0 — 现状盘点与 Feature Flag**

---

## 一、已完成的缺口 (G5 — 已消除)

| 编号 | 描述 | 原级别 | 处理结果 |
|------|------|--------|----------|
| G5 | fineng 4 模块扩展 | P1 | 已完成。GARCH/Kalman/EVT/PathSim 全部交付 + 阴影验证器通过烟雾测试 |

---

## 二、本次修复的缺口

| 编号 | 描述 | 修复日期 | 修复内容 |
|------|------|----------|----------|
| G2 | Qlib LGBModel 不可导入 | 2026-08-02 | 重写 `qlib/contrib/model/__init__.py`：变量预初始化、异常类型分离 (ModuleNotFoundError/ImportError/通用)、结构化日志替代 print、all_model_classes 过滤 None |
| G3 | `_load_trained_model` 占位 TODO | 2026-08-02 | 完整实现三态处理：成功/失败/版本不兼容，支持 stdout 解析路径 + mlruns 自动发现 + pickle 安全加载 |
| G1 | DataProvider 未接真实行情 | 2026-08-02 | 添加 `CrossSourceValidator` 类 + `run_cross_source_validation()` 方法，支持多源交叉校验、偏差检测、历史相关性分析 |

---

## 三、剩余活跃缺口

### G4: MLOps 三件套 Flag 未启用 (P1)

**状态：等待观察期结束**

三个阶段 B 渐进启用计划：
- B1 (08-13 → 08-16): USE_DRIFT_DETECTOR=true，仅告警
- B2 (08-16 → 08-19): USE_FEEDBACK_LOOP 自动接入
- B3 (08-19 → 08-22): USE_AUTO_RETRAIN=true + 降级护栏
- B4 (08-22 → 08-31): USE_MLOPS_PIPELINE=true，完整外层循环

启用脚本已交付：`scripts/phase_b_progressive_enabler.py --auto`

### G6: LLM 智能进化未接入 (P2)

**状态：基础设施就绪，等待 Phase D**

`utils/alpha/llm/` 下 router/providers/audit 已搭建，策略 Ideation 生成逻辑未实现。计划时间：2026-09 → 10-31。

---

## 四、Feature Flag 完整性审计 (T0.2)

### configs/feature_flags.yaml 注册表 — 所有 5 个核心 Flag 已注册

| Flag | 注册状态 | 描述 | 双签要求 | 回滚秒数 |
|------|----------|------|----------|----------|
| USE_STRATEGY_EVALUATOR | 已注册 | 策略多维评估器 | dual_signature | 30 |
| USE_EVOLUTION_ORCHESTRATOR | 已注册 | 自我进化编排器 | dual_signature | 30 |
| USE_DRIFT_DETECTOR | 已注册 | 漂移检测器 | dual_signature | 30 |
| USE_AUTO_RETRAIN | 已注册 | 自动重训调度器 | dual_signature | 60 |
| USE_MLOPS_PIPELINE | 已注册 | MLOps 流水线 | dual_signature | 60 |

### 扩展 Flag (已注册，但不属于原始 5 个核心）

| Flag | 状态 |
|------|------|
| USE_FEEDBACK_LOOP | 已注册 (T2.1 闭环) |
| USE_FINENG_GARCH | 已注册 (Phase 4) |
| USE_FINENG_KALMAN_BETA | 已注册 (Phase 4) |
| USE_FINENG_EVT | 已注册 (Phase 4) |
| USE_FINENG_PATH_SIM | 已注册 (Phase 4) |

### 审计轨迹基础设施

`utils/infra/feature_flags.py` 提供完整框架：
- 启用：双签 (发起人 + 风控)
- 禁用：单签 (任何风控人员)
- 覆盖：`reports/flag_overrides/{flag_name}.json` (紧急回滚)
- 审计：`reports/flag_audit/{flag_name}.jsonl` (所有变更可追溯)
- 热加载：`FeatureFlags.reload()` 无需重启

---

## 五、代码覆盖盲区

以下模块在测试覆盖报告中未找到对应单测文件，标记为覆盖盲区：

| 模块 | 路径 | 类型 | 风险 |
|------|------|------|------|
| AutoRetrainScheduler | `utils/alpha/auto_retrain_scheduler.py` | 生产 | 中 — G3 刚修复 `_load_trained_model`，需补充集成测试 |
| CrossSourceValidator | `utils/data_provider.py` (新增) | 生产 | 低 — 只读校验，不写入生产路径 |
| ObservationTracker | `scripts/observation_tracker.py` | 监控 | 低 — 只读观察，但 daily_briefing 输出影响决策 |

---

## 六、原则合规性审计

对照 ADR-003 五条原则逐项检查：

| 原则 | 合规 | 说明 |
|------|------|------|
| 1. 默认值铁律 | 通过 | 所有 Flag default=false |
| 2. 启用双签 | 通过 | requires=dual_signature |
| 3. 禁用单签 | 通过 | disable() 单签 |
| 4. 审计可追溯 | 通过 | JSONL 审计日志 |
| 5. ConfigManager 4 级优先级 | 通过 | get_config() + 目录覆盖 |

全量审计结论：**5/5 原则全部合规，无违规项。**

---

## 七、后续行动项

| 优先级 | 行动 | 截止日期 | 依赖 |
|--------|------|----------|------|
| P0 | G4 Stage B 渐进启用 | 2026-08-13 | 观察期 14/14 天 |
| P1 | G3 集成测试 — 验证三态降级路径 | 2026-08-09 | G3 代码已交付 |
| P1 | G1 CrossSourceValidator 端到端 dry-run | 2026-08-09 | 真实数据源可用性 |
| P2 | 覆盖盲区补充单测 | 2026-08-16 | — |
| P2 | G6 LLM Ideation 设计文档 | 2026-09-01 | — |

---

*由 T0.1 缺口盘点自动生成，下次更新：观察期届满时*
