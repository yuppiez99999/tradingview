# ECC MLE-Workflow 缺口审查报告

> 审查日期: 2026-07-29
> 审查范围: ECC (Everything Claude Code) mle-workflow 方法论在量化交易系统 8.4 的落地
> 审查人: ECC 阶段 1 代码审查重构

---

## 1. 审查概述

基于 ECC `mle-workflow` 核心技能（数据契约 / 可复现训练 / 晋升门禁 / 监控与回滚），对量化交易系统 v8.4 的 ML 工程实践进行缺口审查，识别出 **8 个 GAP**，已在 ECC 阶段 1 中逐项修复。

### 1.1 审查维度

| 维度 | ECC 标准 | 审查前状态 | GAP |
|------|----------|-----------|-----|
| 快速验证 | 烟雾测试 < 60s | ❌ 无 | GAP-1 |
| 端到端验证 | E2E 真实数据链路 | ⚠️ 不足 | GAP-2 |
| 覆盖率门禁 | CI 覆盖率 + 趋势 | ❌ 无 | GAP-3 |
| TDD 守卫 | 新增代码必有测试 | ❌ 无 | GAP-4 |
| 智能测试选择 | 只跑受影响测试 | ❌ 无 | GAP-5 |
| 模型漂移监控 | KS/PSI + 延迟标签 | ⚠️ 部分 | GAP-6 |
| 训练可复现性 | config+seed+dataset 一致 | ❌ 无 | GAP-7 |
| 数据契约 | 字段/类型/null/PIT 校验 | ❌ 无 | GAP-8 |

---

## 2. 缺口详情与修复状态

### GAP-1: 烟雾测试 (已完成 ✅)

**问题**: CI 无法在 60s 内验证"系统能否启动"——单元测试全 mock，但模块 import 时就崩（`sys.stdout` 副作用、`dataclass slots` 不兼容、`yaml` 路径漂移）会导致明早 7:00 `v84_PreMarket` 任务失败。

**交付物**:
- `scripts/_smoke_runner.py` — 18 项三类烟雾测试 (import / API / config)
- `tests/smoke/test_import_smoke.py` + `test_api_smoke.py` + `test_config_smoke.py` — 23 项 pytest 烟雾测试
- `.github/workflows/ci.yml` — smoke-tests job (5min timeout, 阻断合并)

**验证结果**: 18/18 PASS, 2159ms

**设计原则**:
- 只验"能跑", 不验"跑对" (业务正确性交单元测试)
- 每项 < 5s, 总耗时 < 60s (CI 友好)
- 失败立即 exit 1 (阻断合并)

---

### GAP-2: E2E 端到端验证 (待实施 ⏳)

**问题**: `tests/e2e/` 仅 4 个测试, 覆盖不足; 缺少真实历史数据的完整链路验证 (数据加载 → 因子计算 → 信号生成 → 组合构建 → 回测 → 归因)。

**计划交付物**:
- `tests/e2e/test_full_pipeline_e2e.py` — 完整因子流水线 E2E
- `tests/e2e/test_shadow_account_lifecycle_e2e.py` — 影子账户 14 天生命周期模拟
- `pytest.ini` — `e2e` marker 已就绪
- `.github/workflows/ci.yml` — nightly-regression job 接入 e2e

**当前状态**: `pytest.ini` 已有 `e2e` marker, nightly-regression job 已跑 `pytest tests` (含 e2e), 但 e2e 测试用例数量不足。

---

### GAP-3: 覆盖率 CI 集成 (已完成 ✅)

**问题**: 无覆盖率门禁, PR 可能引入未测试代码而不自知。

**交付物**:
- `.github/workflows/ci.yml` — unit-tests job 加入 `--cov=utils --cov=v8.3_institutional/src --cov-fail-under=60`
- `scripts/_check_coverage_trend.py` — 覆盖率趋势检测 (下降 > 2% 阻断)
- `.coveragerc` — 排除非生产代码 (__init__.py / migrations / __pycache__)

**验证结果**: 覆盖率趋势检测逻辑正确, 下降 > 2% 时 exit 1

**渐进式策略**:
- 当前: `--cov-fail-under=60` (60% 基线)
- 阶段 2: 升至 70%
- 阶段 3: 升至 80%

---

### GAP-4: TDD 守卫 (已完成 ✅)

**问题**: 新增生产代码无对应测试, 测试债务持续累积 (110 个生产文件缺测试)。

**交付物**:
- `scripts/_tdd_guard.py` — 扫描 git diff, 检查新增 `.py` 文件是否有对应 `test_{module}.py`
- `.github/workflows/tdd-guard.yml` — PR 触发的独立 CI job
- `.github/workflows/ci.yml` — ci-summary 依赖 tdd-guard

**验证结果**: 上次提交 (变更 daily_workflow.py) PASS

**规则**:
- 监控目录: `utils/`, `v8.3_institutional/src/`
- 排除: `_*.py` (私有脚本) / `test_*.py` (自身是测试) / `__init__.py` / `scripts/` / `tools/`
- 绕过: commit message 中加 `[skip-tdd-guard]` (不推荐)

---

### GAP-5: 智能测试选择 (已完成 ✅)

**问题**: 每次 PR 都跑全量 68 个单元测试, 反馈慢 (20min)。应只跑受变更影响的测试。

**交付物**:
- `scripts/_select_tests_by_diff.py` — 基于 AST 依赖图的智能测试选择
- `tests/unit/test_select_tests_by_diff.py` — 48 个测试 (45 单元 + 3 集成)
- `.github/workflows/ci.yml` — unit-tests job 接入 `Select affected tests` 步骤

**验证结果**:
- 上次提交 (变更 daily_workflow.py) → 正确选中 1 个测试 `test_phase_hedge_sim_branch.py`
- 扫描耗时 470ms (< 3s 目标)
- 48/48 测试 PASS

**核心算法**:
1. AST 解析所有 `tests/**/*.py` 的 import 语句
2. 构建 `{被依赖模块: [测试文件...]}` 反向索引
3. 变更文件 → `filepath_to_module_variants()` 获取所有可能模块名 (多 sys.path 根配置)
4. 反向索引查找 → 受影响测试
5. fail-safe: 基础设施变更 / 找不到受影响测试 / 异常 → 全量

**模块名变体** (关键设计):
- `utils/alpha/drift_monitor.py` → `{utils.alpha.drift_monitor, alpha.drift_monitor, drift_monitor}`
- 解决 sys.path 多根配置下的 import 路径不一致问题

---

### GAP-6: 模型漂移监控 (已完成 ✅)

**问题**: V9 LGB 模型上线后无漂移检测, 特征分布变化 / IC 衰减无法及时发现。

**交付物**:
- `utils/alpha/drift_monitor.py` — 扩展 `SimModeDriftMonitor` 类 + `DriftReport` 数据类 + `compute_psi()` / `compute_feature_drift()` / `compute_prediction_drift()`
- `utils/alpha/delayed_label_tracker.py` — `DelayedLabelTracker` 类, 记录预测 → 等待标签 → 计算 IC/IC_IR
- `tests/unit/test_drift_monitor_sim_mode.py` — 49 个测试

**验证结果**: 49/49 PASS

**指标**:
- KS 统计量 (Kolmogorov-Smirnov) — 特征分布差异
- PSI (Population Stability Index) — 群体稳定性
- IC / Rank IC / IC_IR — 预测能力追踪

**严重等级**: LOW (< 0.1) / MEDIUM (0.1-0.25) / HIGH (0.25-0.5) / CRITICAL (> 0.5)

**sim_mode 集成**: `SimModeDriftMonitor(sim_mode=True)` 激活后, `run_daily_check()` 批量检查所有特征, 报告持久化至 `reports/drift/`

---

### GAP-7: 训练可复现性 (已完成 ✅)

**问题**: V9 LGB 训练无 manifest, 同 config+seed+dataset 重跑结果不一致无法排查。

**交付物**:
- `research/lgbm_reproducibility.py` — `TrainingConfig` (frozen dataclass) + `artifact_name()` + manifest 落盘
- `research/lgbm_factor_mining.py` — 注入可复现性逻辑
- `tests/unit/test_lgbm_reproducibility.py` — 36 个测试

**验证结果**: 36/36 PASS

**核心机制**:
- `config_hash` = SHA256(model_name + seed + dataset_hash + hyperparams)
- `artifact_name` = `{model_name}_v{config_hash[:8]}_d{date}`
- manifest.json 落盘至 `models/{artifact_name}/manifest.json`
- `verify_reproducibility()` 对比 config_hash + code_sha + dataset_hash

---

### GAP-8: 数据契约 (已完成 ✅)

**问题**: 训练数据与服务数据 schema 不一致, 导致训练/服务特征偏移 (feature skew)。

**交付物**:
- `utils/alpha/data_contract.py` — `DataContract` (frozen) + `FeatureSchema` + `ValidationResult` + `V9_DEFAULT_CONTRACT`
- `tests/unit/test_data_contract.py` — 27 个测试

**验证结果**: 27/27 PASS

**校验维度**:
1. 必填列存在性 (symbol / date / close / open / high / low / volume)
2. 特征 schema (dtype / null 比例 / value_range)
3. 标签定义 (forward_return_5d)
4. entity grain 唯一性 (symbol + date 主键)
5. point-in-time 切片 (防特征泄漏)

**模式**:
- `warn_only` (默认, 7 天观察期): 失败仅 logger.warning
- `enforce`: 失败 raise `DataContractViolationError`

---

## 3. 完成度总览

| GAP | 状态 | 测试数 | 验证结果 |
|-----|------|--------|---------|
| GAP-1 烟雾测试 | ✅ 完成 | 18 + 23 = 41 | 18/18 PASS (2.1s) |
| GAP-2 E2E 验证 | ⏳ 待实施 | 4 (现有) | — |
| GAP-3 覆盖率门禁 | ✅ 完成 | — | 趋势检测逻辑正确 |
| GAP-4 TDD 守卫 | ✅ 完成 | — | PASS |
| GAP-5 智能测试选择 | ✅ 完成 | 48 | 48/48 PASS (1.9s) |
| GAP-6 漂移监控 | ✅ 完成 | 49 | 49/49 PASS |
| GAP-7 训练可复现性 | ✅ 完成 | 36 | 36/36 PASS |
| GAP-8 数据契约 | ✅ 完成 | 27 | 27/27 PASS |

**总计**: 7/8 GAP 完成, 201 个新增测试, 端到端验证 181/181 PASS + V9 基线 25/25 PASS

---

## 4. CI 流水线架构 (ECC 阶段 1 后)

```
PR/push 触发 (6 个并行 job, 阻断合并):
  1. lint-typecheck        : pylint + mypy (Phase 3-B 渐进式严格)
  2. smoke-tests (GAP-1)   : 18 项烟雾测试 (< 60s)
  3. unit-tests            : 智能选择受影响测试 (GAP-5) + 覆盖率门禁 (GAP-3)
  4. integration-tests     : 集成测试 (含 chaos test)
  5. reexport-compat       : re-export 兼容性 (HC-1 V9 基线保护)
  6. v9-quick-regression   : V9 基线快速回归 Layer 1-4 (< 30s, HC-1)

PR 触发 (独立 job):
  7. tdd-guard (GAP-4)     : 新增生产代码测试检查

nightly 触发 (02:00 UTC):
  8. nightly-regression    : V9 完整回测 Layer 5 + 全量测试
```

---

## 5. 后续建议

### 5.1 短期 (ECC 阶段 2)
- **GAP-2 E2E 验证**: 扩充 `tests/e2e/` 至 10+ 测试, 覆盖完整因子流水线 + 影子账户生命周期
- **覆盖率提升**: 从 60% 升至 70%, 补齐 110 个缺测试的生产文件
- **漂移监控线上化**: 7 天观察期后, 将 `SimModeDriftMonitor` 从 `warn_only` 切换到 `enforce`

### 5.2 中期 (ECC 阶段 3)
- **Iteration Compact 模板**: 引入 ECC 式 PR 评审工件 (17 字段: 目标 / 利益相关者 / 成功指标 / 风险 / 回滚计划)
- **模型晋升门禁**: shadow → canary → production 的自动化晋升流水线
- **特征仓库**: 统一特征计算 + 存储 + 服务, 消除训练/服务特征偏移

### 5.3 长期
- **A/B 测试框架**: 影子账户 vs 生产账户的统计显著性检验
- **因果推断**: 因子 → 收益的因果链路验证 (不仅是相关性)
- **在线学习**: 增量训练 + 漂触发的自动重训练闭环
