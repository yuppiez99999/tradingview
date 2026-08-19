# ECC 阶段 1: 代码审查重构执行计划

> 本文件是 `ECC方法论量化系统代码规划.md` 的阶段 1 执行细化，聚焦 P0 优先级「代码审查重构 + 缺口修复」，提供可执行的决策完整步骤。
> 阶段 2/3/4 在阶段 1 验收通过后另行展开。

---

## Summary（执行摘要）

**目标**: 按 ECC mle-workflow + coding-standards 方法论，对量化交易系统 8.4 进行代码审查重构，修复 8 个 MLE 工程缺口，使 MLE-01~MLE-12 十二项检查从当前 PASS 2 / 部分 PASS 4 / FAIL 6 → 阶段 1 目标 PASS ≥ 9，且不破坏 HC-1~HC-7 硬约束。

**执行顺序（按依赖关系）**:
1. **GAP-7** 训练管道可复现性（核心，阻塞 GAP-8）
2. **GAP-8** 数据契约（依赖 GAP-7 的 manifest 结构）
3. **GAP-6** drift_monitor 激活（依赖 GAP-7 artifact 版本号）
4. **GAP-3** 覆盖率 CI 集成（独立）
5. **GAP-1** 烟雾测试（独立）
6. **GAP-4** TDD guard（独立）
7. **GAP-5** 智能测试选择（独立）
8. **GAP-2** E2E 验证（依赖 GAP-1）

**预估工时**: 5-7 个工作日（GAP-7/8 各 1 天，GAP-6 半天，GAP-1~5 各 0.5-1 天，验证 1 天）

---

## Current State Analysis（当前状态分析，基于 Phase 1 探索）

### 已有基础（保留）

| 资产 | 路径 | 状态 |
|------|------|------|
| CI 流水线 | `.github/workflows/ci.yml` | ✅ 已有 7 个 job（lint-typecheck / unit-tests / integration-tests / reexport-compat / v9-quick-regression / nightly-regression / ci-summary），windows-latest + Python 3.11 |
| 静态分析 | `mypy.ini` + `.pylintrc` + `scripts/_verify_phase3b_static_analysis.py` | ✅ 72/72 断言通过，渐进式严格模式 |
| 模型注册表骨架 | `utils/alpha/model_registry.py` | ✅ 有 ModelStage 枚举 + register/promote/load API，⚠️ 缺 preprocessing/dataset_uri/schema/safe_loading |
| 双门禁 | `v8.3_institutional/gate_manager.py` | ✅ ReturnExpectationGate + HedgeCompletenessGate 已 fail-closed（live_mode），⚠️ 缺切片指标/latency/cost guardrail |
| V9 基线回归 | `scripts/_run_v9_regression.py` + `tests/regression/test_v9_baseline.py` | ✅ HC-1 PR 合并闸门 |
| drift_monitor 骨架 | `utils/alpha/drift_monitor.py` | ⚠️ Feature Flag `USE_DRIFT_DETECTOR` 存在，默认 no-op |

### 关键缺口（本计划修复）

| 缺口 | 路径 | 现状 |
|------|------|------|
| 训练可复现性 | `research/lgbm_factor_mining.py` | ❌ 无 TrainingConfig dataclass / artifact_name() / manifest.json 落盘 / dataset_uri / config_hash / code_sha |
| 数据契约 | `utils/alpha/data_contract.py` | ❌ 文件不存在 |
| 延迟标签追踪 | `utils/alpha/delayed_label_tracker.py` | ❌ 文件不存在 |
| 烟雾测试 | `scripts/_smoke_runner.py` | ❌ 不存在 |
| TDD guard | `scripts/_tdd_guard.py` | ❌ 不存在 |
| 智能测试选择 | `scripts/_select_tests_by_diff.py` | ❌ 不存在 |
| 覆盖率趋势 | `scripts/_check_coverage_trend.py` | ❌ 不存在 |
| 可复现性测试 | `tests/unit/test_lgbm_reproducibility.py` | ❌ 不存在 |
| CI smoke job | `.github/workflows/ci.yml` | ❌ unit-tests job 无 `--cov --cov-fail-under` |
| CI security job | `.github/workflows/ci.yml` | ❌ 无 security-tests / tdd-guard job |
| Drift runbook | `docs/runbooks/MODEL_DRIFT_RUNBOOK.md` | ❌ 不存在 |
| 审查报告 | `docs/ecc_audit/AUDIT_MLE_WORKFLOW_GAP.md` / `AUDIT_CODE_SMELL.md` | ❌ 不存在 |

---

## Proposed Changes（变更明细）

### GAP-7: 训练管道可复现性（P0 核心）

**Why**: MLE-04「Training is reproducible from code, config, data version, and seed」当前 FAIL；MLE-09「Model artifact carries version, config, dataset reference, and preprocessing」当前 FAIL。这是阶段 1 最核心的修复，阻塞 GAP-8 数据契约。

**Files**:

1. **新增** `research/lgbm_reproducibility.py`（独立模块，避免污染 `lgbm_factor_mining.py` 主流程）
   - `TrainingConfig` dataclass（frozen=True，字段：model_name / seed / dataset_uri / dataset_sha256 / config_hash / code_sha / lgb_params / feature_list / label_def / train_split / val_split / test_split / training_env / python_version / lib_versions）
   - `compute_dataset_uri(panel: pd.DataFrame) -> str` — 计算 panel 的 sha256 作为 dataset_uri
   - `compute_config_hash(config: TrainingConfig) -> str` — 序列化后 sha256
   - `compute_code_sha(file_paths: List[Path]) -> str` — 对 `lgbm_factor_mining.py` + `compute_all_factors` 所在文件计算 git blob hash 或文件 sha256
   - `artifact_name(config: TrainingConfig) -> str` — 格式 `{model_name}_v{short_sha}_d{yyyymmdd}`，short_sha = code_sha[:8]
   - `write_manifest(artifact_dir: Path, config: TrainingConfig, metrics: dict) -> Path` — 落盘 `manifest.json`，含 17 字段（Iteration Compact 子集）

2. **修改** `research/lgbm_factor_mining.py`（最小侵入）
   - 训练入口（如 `main()` 或 `train()`）开头构造 `TrainingConfig`
   - 训练完成后调用 `write_manifest(output_dir, config, metrics=feature_importance_dict)`
   - 默认 seed=42（保留），但改为从 TrainingConfig 读取，便于复现测试注入不同 seed
   - 不修改 `compute_all_factors` 因子计算逻辑（避免破坏 V9 基线）

3. **新增** `tests/unit/test_lgbm_reproducibility.py`
   - `test_same_config_same_seed_same_dataset_yields_same_importance` — 同 config+seed+dataset 重跑两次，importance top-10 一致率 100%
   - `test_different_seed_yields_different_artifact_name` — seed=42 vs seed=43，artifact_name 不同
   - `test_manifest_contains_all_required_fields` — 17 字段齐全校验
   - `test_dataset_uri_changes_with_data` — panel 加一行噪声，dataset_uri 变化
   - `test_config_hash_stable_for_same_config` — 同 config 两次构造，hash 一致

**Acceptance**:
```bash
python -m pytest tests/unit/test_lgbm_reproducibility.py -v
# 同 config+seed+dataset 重跑, importance 排序 top-10 一致率 100%
python -m pytest tests/regression/test_v9_baseline.py -v
# V9 基线回归不退化 (HC-1)
```

**Risk Mitigation**:
- 不修改因子计算逻辑 → V9 基线不破
- TrainingConfig 用 `dataclass(frozen=True)` → 不可变，符合 coding-standards
- manifest.json 落盘失败不阻断训练（warn_only，记录 logger.warning）

---

### GAP-8: 数据契约（P0，依赖 GAP-7）

**Why**: MLE-02「Data contract defines entity grain, label timing, feature timing, snapshot/version」当前 FAIL。数据契约是 MLE 工程的基石，防止训练/服务特征不一致。

**Files**:

1. **新增** `utils/alpha/data_contract.py`
   - `DataContract` dataclass（frozen=True，字段：entity_grain / label_def / feature_schema / point_in_time_rules / split_policy / required_columns / allowed_nulls / pii_policy / version / change_policy）
   - `FeatureSchema` dataclass（feature_name / dtype / source / timing / null_policy / value_range）
   - `DataContract.validate(panel: pd.DataFrame, mode: str = "warn_only") -> ValidationResult`
     - 检查项：必填列存在 / 类型匹配 / null 比例 / value_range / point-in-time 切片正确性
     - `mode="warn_only"`（默认，7 天观察期）→ 失败仅 logger.warning
     - `mode="enforce"` → 失败 raise `DataContractViolationError`
   - `validate_point_in_time(panel: pd.DataFrame, current_date: datetime) -> bool` — 扫描 panel 是否有未来信息泄漏
   - `V9_DEFAULT_CONTRACT = DataContract(...)` — 预定义 V9 LGB 模型的数据契约（entity=(symbol,date), label=forward_return_5d, label_delay=5d, ...）

2. **新增** `docs/contracts/DATA_CONTRACT.md`
   - V9 LGB 模型的数据契约实例（11 字段：Entity Grain / Label / Feature Schema 表 / Point-in-Time Join / Split Policy / Required Columns / Allowed Nulls / PII / Dataset Version / Validation / Change Policy）
   - 引用 `utils/alpha/data_contract.py:V9_DEFAULT_CONTRACT` 作为 source of truth

3. **修改** `research/lgbm_factor_mining.py`
   - 训练入口在构造 TrainingConfig 后调用 `V9_DEFAULT_CONTRACT.validate(panel, mode="warn_only")`
   - 校验结果记入 manifest.json 的 `contract_validation` 字段

4. **新增** `tests/unit/test_data_contract.py`
   - `test_v9_contract_validates_clean_panel` — 干净 panel 通过
   - `test_missing_required_column_warns` — 缺 close 列 → warn_only 不抛，enforce 抛
   - `test_null_ratio_exceeds_threshold_warns` — MOM_252D null > 5% → 告警
   - `test_point_in_time_violation_detected` — panel 含未来 close → 检测到
   - `test_contract_versioning_immutable` — frozen dataclass 不可修改

**Acceptance**:
```bash
python -m pytest tests/unit/test_data_contract.py -v
python -c "from utils.alpha.data_contract import V9_DEFAULT_CONTRACT; V9_DEFAULT_CONTRACT.validate(clean_panel, mode='warn_only')"
# V9 基线回归不退化
python -m pytest tests/regression/test_v9_baseline.py -v
```

**Decisions**:
- 默认 `warn_only=True`（7 天观察期后切 enforce，本计划不切，留待阶段 2 决策）
- 不强制修改 `pipeline_orchestrator.py`（避免破坏 V9 基线），仅在 `lgbm_factor_mining.py` 入口校验

---

### GAP-6: drift_monitor 激活 + 延迟标签追踪（P1）

**Why**: MLE-10「Monitoring covers system health, feature drift, prediction drift, and delayed labels」当前 FAIL。模型上线后无监控是工业级 ML 系统的硬伤。

**Files**:

1. **修改** `utils/alpha/drift_monitor.py`
   - 默认 `USE_DRIFT_DETECTOR=False` 保留（HC-1 不破坏）
   - 当 `sim_mode=True` 或 `USE_DRIFT_DETECTOR=True` 时激活
   - 新增 `DriftSeverity` 枚举（LOW / MEDIUM / HIGH / CRITICAL）
   - 新增 `DriftReport` dataclass（frozen=True，字段：timestamp / model_name / model_version / feature_name / drift_score / severity / baseline_mean / current_mean / owner / runbook_url）
   - 新增 `compute_feature_drift(baseline: pd.Series, current: pd.Series) -> DriftReport` — KS 检验 + PSI
   - 新增 `compute_prediction_drift(baseline: np.ndarray, current: np.ndarray) -> DriftReport`
   - 新增 `DriftMonitor.run_daily_check(model_name: str, current_panel: pd.DataFrame) -> List[DriftReport]`
   - 告警 owner 定义：从 `config/alert_owners.yaml` 读取（新增配置文件）

2. **新增** `utils/alpha/delayed_label_tracker.py`
   - `DelayedLabelTracker` 类：追踪 t 日预测的 t+5 日 label 是否已观测
   - `record_prediction(date, symbol, predicted_score, model_version) -> None`
   - `check_label_observability(date) -> List[PredictionRecord]` — 检查哪些预测的 label 已可观测
   - `compute_delayed_metrics(model_version: str) -> Dict[str, float]` — 计算 IC / IC_IR / RankIC（基于已观测的延迟 label）

3. **新增** `config/alert_owners.yaml`
   - 每个 model_name 对应 owner / slack_channel / email / oncall_rotation
   - V9 LGB 的 owner 默认为"量化负责人"

4. **新增** `docs/runbooks/MODEL_DRIFT_RUNBOOK.md`
   - 触发条件：drift_score > 0.2 (KS) 或 PSI > 0.1
   - 排查步骤：1) 检查数据源；2) 检查特征分布；3) 检查 regime 切换；4) 决定是否触发回滚
   - 回滚流程：引用 V9 Iteration Compact 的 Rollback 字段

5. **新增** `tests/unit/test_drift_monitor_sim_mode.py`
   - `test_drift_monitor_disabled_by_default` — Flag False 时 no-op
   - `test_drift_monitor_active_in_sim_mode` — sim_mode=True 时激活
   - `test_feature_drift_detection` — 构造 drift panel，drift_score > 阈值
   - `test_delayed_label_tracker_records_predictions`
   - `test_delayed_label_tracker_computes_metrics_after_5d`

**Acceptance**:
```bash
python -m pytest tests/unit/test_drift_monitor_sim_mode.py -v
# 在 sim_mode 跑 1 天 (可通过手动触发 daily_workflow --phase post-market)
# 确认产出 reports/drift/drift_report_{date}.json (即使为空也证明管道可用)
```

---

### GAP-3: 覆盖率 CI 集成（P1，独立）

**Why**: 当前 CI 的 unit-tests job 无 `--cov --cov-fail-under`，覆盖率未作为 PR 闸门。ECC tdd-workflow 要求 80%（本阶段先到 60%，阶段 2 升 70%，阶段 3 后 80%）。

**Files**:

1. **修改** `.github/workflows/ci.yml` — unit-tests job
   - 安装依赖追加：`pip install pytest-cov`
   - 运行命令改为：
     ```bash
     python -m pytest tests/unit -v --tb=short -n auto -m "not slow" \
       --cov=utils --cov=v8.3_institutional/src \
       --cov-report=term-missing --cov-report=xml:coverage.xml \
       --cov-fail-under=60
     ```
   - 新增 step：`Upload coverage to Codecov`（可选，需 CODECOV_TOKEN secret）
   - 新增 step：`Upload coverage artifact`（upload-artifact）

2. **新增** `scripts/_check_coverage_trend.py`
   - 读取 `coverage.xml`，对比上次 commit 的 `coverage_baseline.json`
   - 输出：当前覆盖率 / 上次覆盖率 / 趋势（↑/↓）/ 模块明细
   - 阈值：下降 > 2% 时 exit 1（防止覆盖率退化）

3. **修改** `.coveragerc`
   - `fail_under = 60`（本阶段）/ 阶段 2 升 70
   - `omit` 排除 `tests/*` / `scripts/_*.py` / `setup.py`

**Acceptance**:
```bash
# 本地验证
python -m pytest tests/unit --cov=utils --cov=v8.3_institutional/src --cov-fail-under=60 --cov-report=term-missing
python scripts/_check_coverage_trend.py
# CI 验证: PR 中 unit-tests job 显示覆盖率, 低于 60% 阻断合并
```

---

### GAP-1: 烟雾测试（P1，独立）

**Why**: 缺自动化烟雾测试，CI 无法快速验证关键模块可导入、关键 API 可调用。

**Files**:

1. **新增** `scripts/_smoke_runner.py`
   - 导入烟雾：`import utils.alpha.mlops_pipeline / utils.config_manager / v8.3_institutional.gate_manager / research.lgbm_factor_mining` 等核心模块
   - API 烟雾：调用关键 API 的"空载"版本（如 `ConfigManager().get_config("feature_flags")` 返回非 None）
   - 配置烟雾：读取 `config/gate_thresholds.json` / `config/feature_flags.yaml` 不抛异常
   - 输出：`SMOKE TEST REPORT`（每项 PASS/FAIL + 耗时）
   - 总耗时 < 60s，CI 友好

2. **新增** `tests/smoke/` 目录 + `tests/smoke/test_import_smoke.py` + `test_api_smoke.py` + `test_config_smoke.py`
   - 每个文件 5-10 个断言，验证核心路径

3. **修改** `.github/workflows/ci.yml`
   - 新增 job `smoke-tests`（在 lint-typecheck 之后、unit-tests 之前并行）
   - timeout 5 分钟
   - 失败阻断合并（加入 ci-summary 的 needs 列表）

**Acceptance**:
```bash
python scripts/_smoke_runner.py
# < 60s 跑通, 所有项 PASS
# CI: smoke-tests job 在 PR 中显示, 失败阻断合并
```

---

### GAP-4: TDD guard（P1，独立）

**Why**: 新增 `utils/foo.py` 无对应 `tests/unit/test_foo.py`，CI 不阻断，违反 TDD 原则。

**Files**:

1. **新增** `scripts/_tdd_guard.py`
   - 扫描 `git diff --name-only origin/main...HEAD` 中的 `utils/*.py` 和 `v8.3_institutional/src/*.py` 新增文件
   - 排除：`_*.py`（私有脚本）/ `test_*.py`（自身是测试）/ `__init__.py` / `tools/*` / `scripts/*`
   - 对每个新增生产代码文件，检查 `tests/unit/test_{module_name}.py` 是否存在
   - 缺失则输出：`TDD GUARD FAILED: utils/foo.py 缺少 tests/unit/test_foo.py`
   - exit code: 0=通过, 1=失败

2. **新增** `.github/workflows/tdd-guard.yml`
   - PR 触发，runs-on windows-latest
   - 步骤：checkout + setup-python + `python scripts/_tdd_guard.py`
   - 失败阻断合并（required status check）

3. **修改** `.github/workflows/ci.yml` 的 ci-summary job
   - `needs` 追加 `tdd-guard`（使其成为合并闸门之一）

**Acceptance**:
```bash
# 本地验证
python scripts/_tdd_guard.py
# CI: 在 PR 中新增 utils/foo.py (无对应 test) → tdd-guard job 阻断合并
```

---

### GAP-5: 智能测试选择（P2，独立）

**Why**: 全量 unit-tests 浪费 CI 时间，应基于 git diff + AST import graph 只跑受影响测试。

**Files**:

1. **新增** `scripts/_select_tests_by_diff.py`
   - 输入：`--base origin/main --head HEAD`
   - 用 `git diff --name-only` 取变更文件
   - 用 AST 解析每个变更文件的 `import` 语句，构建依赖图（utils.X 依赖 utils.Y）
   - 反向追溯：变更 `utils/alpha/drift_monitor.py` → 找到 `tests/unit/test_drift_monitor_*.py` + 依赖 `drift_monitor` 的其他模块的测试
   - 输出：`SELECTED_TESTS=tests/unit/test_drift_monitor_sim_mode.py tests/unit/test_mlops_pipeline_facade.py ...`
   - 退化策略：依赖图构建失败 → 返回全量测试（fail-open，不阻断 CI）

2. **修改** `.github/workflows/ci.yml` 的 unit-tests job
   - 新增 step：`Select affected tests`
     ```bash
     $selected = python scripts/_select_tests_by_diff.py --base origin/main --head HEAD
     Write-Host "Selected tests: $selected"
     echo "SELECTED_TESTS=$selected" >> $env:GITHUB_ENV
     ```
   - 修改 Run unit tests step：
     ```bash
     python -m pytest $env:SELECTED_TESTS -v --tb=short -n auto -m "not slow" --cov=...
     ```
   - 退化：`SELECTED_TESTS` 为空时跑全量

3. **保留** nightly-regression job 跑全量（兜底）

**Acceptance**:
```bash
# 本地验证
python scripts/_select_tests_by_diff.py --base HEAD~1 --head HEAD
# 修改 utils/alpha/drift_monitor.py 一行注释, 应输出 tests/unit/test_drift_monitor_*.py
# CI: unit-tests job 时间下降 >= 30% (对比修改前)
```

---

### GAP-2: E2E 验证（P2，依赖 GAP-1）

**Why**: E2E 测试不完整，无法验证完整工作流链路。

**Files**:

1. **修改** `.github/workflows/ci.yml` 的 nightly-regression job
   - 在 `Run full test suite` step 之前新增：
     ```bash
     python -m pytest tests/e2e -v -m "e2e and not slow" --tb=short
     ```
   - 失败不阻断 PR（nightly 触发，仅告警）

2. **新增** `tests/e2e/test_eod_full_chain_e2e.py`（若已有则扩充）
   - `test_pre_market_to_post_market_chain` — 模拟盘前→盘中→盘后→报告完整链路
   - `test_dual_gate_enforcement` — 双门禁 fail-closed 触发
   - `test_kill_switch_activation` — kill_switch 激活路径
   - `test_sim_mode_vs_live_mode_branch` — sim_mode 走 SimExecutionEngine，live_mode 走实盘路径

3. **新增** `pytest.ini` marker
   - `e2e`：E2E 测试标记
   - `contract`：数据契约测试标记（GAP-8 用）
   - `reproducibility`：可复现性测试标记（GAP-7 用）

**Acceptance**:
```bash
python -m pytest tests/e2e -v -m "e2e and not slow"
# nightly job 跑通 8 个 e2e 用例
```

---

### 审查报告产出（贯穿阶段 1）

**Files**:

1. **新增** `docs/ecc_audit/AUDIT_MLE_WORKFLOW_GAP.md`
   - MLE-01~MLE-12 十二项检查逐项打分
   - 每项：检查项 / 量化项目落地映射 / 修复前状态 / 修复后状态 / 证据文件:行号
   - 阶段 1 完成时填表：PASS ≥ 9

2. **新增** `docs/ecc_audit/AUDIT_CODE_SMELL.md`
   - coding-standards 三类 code smell 扫描结果
   - 类别 1：可读性（命名 / 注释 / 格式）
   - 类别 2：可维护性（DRY / YAGNI / 复杂度）
   - 类别 3：安全性（错误处理 / 不可变性 / 类型安全）
   - 每类：扫描工具（ruff/pylint/mypy） / 发现项 / 修复建议 / 已修复状态

**Acceptance**:
- 8 个 GAP 全部修复后，AUDIT_MLE_WORKFLOW_GAP.md 中 12 项 ≥ 9 项 PASS
- AUDIT_CODE_SMELL.md 三类 code smell 已扫描并修复 P0/P1 项

---

## Assumptions & Decisions（假设与决策）

### 假设
1. Python 3.11 是 CI 环境标准（`.github/workflows/ci.yml` env.PYTHON_VERSION='3.11'），不再兼容 3.8（vibe_trading_adapter 内部仍保留 3.8 兼容补丁，但新代码用 3.11 语法）
2. CI runs-on windows-latest，所有 shell 命令用 PowerShell 语法
3. 现有 `tests/regression/test_v9_baseline.py` 通过，是 HC-1 的 PR 合并闸门
4. `gate_manager.py` 双门禁已 fail-closed（live_mode），本计划不修改双门禁核心逻辑，仅追加切片/latency/cost guardrail（在阶段 3 G3/G4 门禁时实施，不在阶段 1）

### 决策
1. **GAP-7 优先 GAP-8**: 训练可复现性是数据契约的前置（manifest.json 的 dataset_uri 字段需由 GAP-7 计算）
2. **不修改 compute_all_factors 因子计算逻辑**: 避免破坏 V9 基线（HC-1），仅在训练入口注入 TrainingConfig + manifest 落盘
3. **数据契约默认 warn_only**: 7 天观察期后再切 enforce，避免 fail-closed 阻断生产
4. **drift_monitor 激活范围**: 仅 sim_mode 激活，live_mode 仍由 Feature Flag 控制（HC-1 不破坏）
5. **覆盖率 threshold 60%**: 本阶段先到 60%，阶段 2 升 70%，阶段 3 后 80%（渐进式，符合项目 Phase 3-B 节奏）
6. **TDD guard 排除私有脚本**: `_*.py` / `scripts/test_*.py` / `tools/*` 不强制对应 test
7. **智能测试选择 fail-open**: 依赖图构建失败 → 返回全量测试，不阻断 CI
8. **审查报告产出贯穿阶段 1**: 不作为独立 GAP，每个 GAP 修复后更新对应 MLE 检查项状态
9. **新增模块优先用 dataclass(frozen=True)**: 符合 coding-standards 不可变优先原则
10. **CI 新增 job 全部加入 ci-summary 的 needs**: 确保成为合并闸门

### 不做的事（Out of Scope）
- ❌ 修改 V9 LGB 模型本身（超参 / 特征 / 标签定义）
- ❌ 修改 `pipeline_orchestrator.py` 主流程（避免破坏 V9 基线）
- ❌ 实施 G3/G4 切片门禁和 latency 门禁（留待阶段 3）
- ❌ 把覆盖率直接提到 80%（渐进式，本阶段 60%）
- ❌ 切换数据契约到 enforce 模式（留待阶段 2 7 天观察期后）
- ❌ 修改双门禁阈值（保留 V9 基线参数）

---

## Verification Steps（验证步骤）

### 阶段 1 完成时的端到端验证

```bash
# 1. V9 基线回归不退化 (HC-1)
python -m pytest tests/regression/test_v9_baseline.py -v

# 2. 可复现性测试 (GAP-7)
python -m pytest tests/unit/test_lgbm_reproducibility.py -v -m reproducibility
# 同 config+seed+dataset 重跑, importance top-10 一致率 100%

# 3. 数据契约校验 (GAP-8)
python -m pytest tests/unit/test_data_contract.py -v -m contract
python -c "from utils.alpha.data_contract import V9_DEFAULT_CONTRACT; V9_DEFAULT_CONTRACT.validate(clean_panel, mode='warn_only')"

# 4. drift_monitor 激活 (GAP-6)
python -m pytest tests/unit/test_drift_monitor_sim_mode.py -v

# 5. 烟雾测试 (GAP-1)
python scripts/_smoke_runner.py
# < 60s 跑通

# 6. 覆盖率 (GAP-3)
python -m pytest tests/unit --cov=utils --cov=v8.3_institutional/src --cov-fail-under=60 --cov-report=term-missing
python scripts/_check_coverage_trend.py

# 7. TDD guard (GAP-4)
python scripts/_tdd_guard.py

# 8. 智能测试选择 (GAP-5)
python scripts/_select_tests_by_diff.py --base HEAD~1 --head HEAD

# 9. E2E (GAP-2)
python -m pytest tests/e2e -v -m "e2e and not slow"

# 10. CI 全绿 (最终闸门)
# 推送 PR, 确认 7+ 个 job 全部 success:
# lint-typecheck / unit-tests (含覆盖率) / integration-tests / reexport-compat /
# v9-quick-regression / smoke-tests / tdd-guard / ci-summary

# 11. 审查报告 (GAP-7 完成后填表)
# docs/ecc_audit/AUDIT_MLE_WORKFLOW_GAP.md 中 12 项 >= 9 项 PASS
```

### 阶段 1 验收清单

- [ ] V9 基线回归通过（HC-1 不破）
- [ ] `research/lgbm_reproducibility.py` 含 TrainingConfig + artifact_name + write_manifest
- [ ] `lgbm_factor_mining.py` 训练入口调用 write_manifest
- [ ] `models/registry/lgbm_factor_mining/<sha>/manifest.json` 落盘
- [ ] `utils/alpha/data_contract.py` 含 DataContract + V9_DEFAULT_CONTRACT + validate
- [ ] `docs/contracts/DATA_CONTRACT.md` 完成 V9 实例
- [ ] `utils/alpha/drift_monitor.py` sim_mode 激活
- [ ] `utils/alpha/delayed_label_tracker.py` 实现完整
- [ ] `docs/runbooks/MODEL_DRIFT_RUNBOOK.md` 完成
- [ ] `config/alert_owners.yaml` 完成
- [ ] `scripts/_smoke_runner.py` / `_check_coverage_trend.py` / `_tdd_guard.py` / `_select_tests_by_diff.py` 完成
- [ ] `.github/workflows/ci.yml` 含 smoke-tests / coverage / tdd-guard job
- [ ] `.github/workflows/tdd-guard.yml` 完成
- [ ] `tests/unit/test_lgbm_reproducibility.py` / `test_data_contract.py` / `test_drift_monitor_sim_mode.py` 完成
- [ ] `pytest.ini` 含 e2e / contract / reproducibility marker
- [ ] `docs/ecc_audit/AUDIT_MLE_WORKFLOW_GAP.md` 12 项 ≥ 9 PASS
- [ ] `docs/ecc_audit/AUDIT_CODE_SMELL.md` 三类扫描完成
- [ ] CI 全绿（PR 中所有 job success）
- [ ] `mypy utils/` 错误数不增加

---

## 风险与回滚

| 风险 | 概率 | 影响 | 缓解 | 回滚 |
|------|------|------|------|------|
| GAP-7 修改 lgbm_factor_mining.py 破坏 V9 基线 | 中 | 高 | 不改 compute_all_factors 逻辑；新增独立 lgbm_reproducibility.py；PR 必跑 V9 回归 | revert lgbm_factor_mining.py 改动，保留 lgbm_reproducibility.py |
| GAP-8 数据契约 fail-closed 阻断生产 | 低 | 高 | 默认 warn_only=True，7 天观察期 | warn_only 改回，或删除 enforce 分支 |
| GAP-6 激活 drift_monitor 致定时任务异常 | 中 | 高 | 仅 sim_mode 激活；live_mode 仍由 Flag 控制 | 回退 PR，GAP-6 改为文档先行 |
| GAP-3 覆盖率 60% 阻断过多 PR | 高 | 中 | 先以 55% 为 threshold，2 周后升 60% | threshold 降回 55% 或删除 --cov-fail-under |
| GAP-4 TDD guard 误报 | 高 | 中 | 排除 _*.py / scripts/test_*.py / tools/* | guard 改为告警不阻断 |
| GAP-5 智能测试选择漏选 | 中 | 高 | 保留 nightly 全量兜底；PR 标签 `full-tests` 强制全量 | 关闭智能选择，跑全量 |
| GAP-1 烟雾测试误报阻断 CI | 中 | 中 | smoke 测试只验证导入和 API 可调用，不做业务断言 | smoke-tests 改为告警 |

---

## 实施顺序与里程碑

| 顺序 | GAP | 工时 | 里程碑 | 验收命令 |
|------|-----|------|--------|---------|
| 1 | GAP-7 | 1.5d | `research/lgbm_reproducibility.py` + manifest 落盘 + 测试通过 | `pytest tests/unit/test_lgbm_reproducibility.py -v` |
| 2 | GAP-8 | 1d | `utils/alpha/data_contract.py` + V9 实例 + 测试通过 | `pytest tests/unit/test_data_contract.py -v` |
| 3 | GAP-6 | 0.5d | drift_monitor 激活 + 延迟标签追踪 + runbook | `pytest tests/unit/test_drift_monitor_sim_mode.py -v` |
| 4 | GAP-3 | 0.5d | CI coverage job + 趋势脚本 | `pytest --cov-fail-under=60` |
| 5 | GAP-1 | 0.5d | 烟雾测试 + CI smoke job | `python scripts/_smoke_runner.py` |
| 6 | GAP-4 | 0.5d | TDD guard + workflow | `python scripts/_tdd_guard.py` |
| 7 | GAP-5 | 0.5d | 智能测试选择 + CI 接入 | `python scripts/_select_tests_by_diff.py --base HEAD~1 --head HEAD` |
| 8 | GAP-2 | 0.5d | E2E 扩充 + nightly 接入 | `pytest tests/e2e -v -m "e2e and not slow"` |
| 9 | 审查报告 | 0.5d | AUDIT_MLE_WORKFLOW_GAP + AUDIT_CODE_SMELL | 12 项 ≥ 9 PASS |
| 10 | 端到端验证 | 0.5d | CI 全绿 + V9 回归通过 | 推送 PR，所有 job success |

**总工时**: 6.5 个工作日（含端到端验证 0.5d）

---

## 后续阶段衔接（阶段 1 完成后）

- **阶段 2（测试与验证闭环）**: 依赖阶段 1 的 GAP-1/3/4/5 + verify_loop.py 落地，把覆盖率升到 70%，bandit 安全扫描接入
- **阶段 3（ECC 工作流规范文档）**: 依赖阶段 1 的 GAP-7/8 manifest/contract 结构，产出 Iteration Compact 模板 + 晋升门禁 G3/G4 实施
- **阶段 4（ECC skills/rules 引入）**: 依赖阶段 2 的 verify_loop.py 验证命令，做 SKILL.md Python 化适配
