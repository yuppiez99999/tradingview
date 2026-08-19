# ECC 方法论量化交易系统代码规划

## Context

**问题与动机**: 当前量化交易系统 8.4 已具备完整的生产能力（V9 LGB 基线、双门禁、4 个 v84 定时任务、458+ 因子库），但缺乏系统性的 ML 工程方法论支撑：
- 训练管道可复现性未验证（lgbm_factor_mining.py 无 dataset_uri/config_hash/code_sha 记录）
- 数据契约未显式定义（因子计算与训练/服务的特征转换未共享校验）
- drift_monitor/model_registry/mlops_pipeline/ab_testing/auto_retrain_scheduler 等"骨架"模块默认 no-op，未激活
- 测试覆盖率 65.20% < ECC tdd-workflow 要求的 80%，且未集成 CI 门槛
- 缺乏自动化烟雾测试、E2E 验证不完整、TDD 反馈闭环缺失

**意图**: 引入 ECC（Everything Claude Code）方法论（mle-workflow + verification-loop + tdd-workflow + coding-standards 四大核心 skill），按用户优先级「代码审查重构优先」分 4 阶段实施，目标是让量化系统具备工业级 ML 工程能力：可审查工件、可复现训练、可晋升门禁、可回滚、可监控。

**预期结果**: 12 个 MLE 工作流检查项从当前 PASS 2 / 部分 PASS 4 / FAIL 6 → 目标 PASS ≥ 9；覆盖率 65% → 80%；CI 6 阶段验证闭环全绿；4 个 ECC skill + 3 个 rules 安装到项目，让 Claude/Cursor/Trae 在该项目中自动遵循 ECC 方法论。

---

## 阶段划分（按用户优先级）

| 阶段 | 主题 | 优先级 | 估时 | 阻断性 |
|------|------|--------|------|--------|
| 1 | ECC 审查重构 + 缺口修复 | P0 最高 | 5-7 天 | 阻断 2/3/4 |
| 2 | 测试与验证闭环 | P1 | 4-6 天 | 阻断 4 CI 集成 |
| 3 | ECC 工作流规范文档 | P2 | 2-3 天 | 非阻断，与 4 并行 |
| 4 | ECC skills/rules 引入 | P3 | 1-2 天 | 非阻断 |

**核心设计原则**:
- **不破坏 HC-1 ~ HC-7 硬约束**: V9 基线回归、ConfigManager 4 级优先级、Shadow 14 天验证、Feature Flag 默认 False 等既有设计完整保留
- **渐进式收紧**: 沿用项目 Phase 3-B → 3-C → 3-D 节奏，覆盖率 60→70→80，不一次性 strict
- **复用优先**: 阶段 1 是激活已有"骨架"（drift_monitor / model_registry / mlops_pipeline / ab_testing / auto_retrain_scheduler 默认 no-op），而非重写
- **Python 化 ECC**: verification-loop 6 阶段从 Node.js/npm 映射到 mypy/ruff/pylint/pytest/bandit/git diff

---

## 阶段 1: ECC 审查重构 + 缺口修复（P0）

### 输出
1. **审查报告**: `docs/ecc_audit/AUDIT_MLE_WORKFLOW_GAP.md`（MLE-01~MLE-10 十任务逐项打分）、`docs/ecc_audit/AUDIT_CODE_SMELL.md`（coding-standards 三类 code smell 扫描）
2. **8 个缺口修复 PR**（见下方矩阵）
3. **可复现性补丁**: `research/lgbm_factor_mining.py` 添加 `TrainingConfig` dataclass + `artifact_name()` + manifest.json 落盘
4. **数据契约初版**: `docs/contracts/DATA_CONTRACT.md` + `utils/alpha/data_contract.py` 运行时校验器

### 缺口修复矩阵

| GAP | 描述 | 修复文件（相对项目根） | 验收标准 |
|-----|------|----------------------|---------|
| GAP-1 | 缺自动化烟雾测试 | 新增 `scripts/_smoke_runner.py` + 修改 `.github/workflows/ci.yml` 增加 smoke-tests job | `_smoke_test_*.py` 在 CI 跑通且 < 60s |
| GAP-2 | E2E 验证不完整 | 修改 `.github/workflows/ci.yml` nightly job 增加 `pytest tests/e2e -v -m "e2e and not slow"` | 8 个 e2e 用例（除 slow）nightly 跑通 |
| GAP-3 | 覆盖率未集成 CI | unit-tests job 增加 `--cov=utils --cov=v8.3_institutional/src --cov-fail-under=60` + 新增 `scripts/_check_coverage_trend.py` | CI 显示覆盖率，低于 60% 阻断 |
| GAP-4 | 缺 TDD 反馈闭环 | 新增 `.github/workflows/tdd-guard.yml` + `scripts/_tdd_guard.py` | 新增 `utils/foo.py` 必须有 `tests/unit/test_foo.py`，否则 PR 阻断 |
| GAP-5 | 缺智能测试选择 | 新增 `scripts/_select_tests_by_diff.py`（基于 git diff + AST import graph） | 仅运行受影响测试，CI 时间下降 ≥ 30% |
| GAP-6 | 无 drift/延迟标签/告警 | 激活 `utils/alpha/drift_monitor.py`（Feature Flag `USE_DRIFT_DETECTOR`，sim_mode 默认开）+ 新增 `utils/alpha/delayed_label_tracker.py` + `docs/runbooks/MODEL_DRIFT_RUNBOOK.md` | drift_monitor 在 sim_mode 跑 7 天后产出首份 drift 报告 |
| GAP-7 | 训练管道可复现性未验证 | 修改 `research/lgbm_factor_mining.py` 注入 `TrainingConfig` + `artifact_name()` + 写入 `models/registry/lgbm_factor_mining/<sha>/manifest.json` + 新增 `tests/unit/test_lgbm_reproducibility.py` | 同一 config+seed+dataset 重跑，importance 排序 top-10 一致率 100% |
| GAP-8 | 数据契约未显式定义 | 新增 `docs/contracts/DATA_CONTRACT.md` + `utils/alpha/data_contract.py`（运行时校验器，默认 `warn_only=True`，7 天观察期后切 enforce） | `lgbm_factor_mining` 启动时调用 `DataContract.validate(panel)`，字段缺失/类型错即 fail-closed |

### 审查范围（mle-workflow Review Checklist 12 项 + coding-standards Code Smell 3 类）

| 路径 | 审查重点 | 对应 MLE |
|------|---------|---------|
| `research/lgbm_factor_mining.py` | 可复现性(seed/dataset_uri/config_hash)、train/serve 等价、静默吞异常(line 194 `except: continue`)、特征泄漏 | MLE-03/04/07 |
| `v8.3_institutional/predict_annual_return.py` | 静态硬编码 20 标的与 V9 动态注入的契约一致性、Feature Flag 透传 | MLE-01/02 |
| `v8.3_institutional/gate_manager.py` | 补 latency/cost guardrail、切片评估 | MLE-05 |
| `utils/alpha/drift_monitor.py` | 默认 no-op 是否真的安全、告警 owner 是否定义 | MLE-10 |
| `utils/alpha/mlops_pipeline.py` | Facade 容错降级是否会掩盖关键失败 | MLE-08/10 |
| `utils/alpha/model_registry.py` | artifact 是否含 preprocessing、schema 校验、safe loading | MLE-07 |
| `utils/config_manager.py` | 4 级优先级不可绕过(HC-5) | MLE-02 |
| `research/vibe_trading_factor_analysis/pipeline/pipeline_orchestrator.py` | train/serve 等价、point-in-time join | MLE-02/04 |
| `utils/gtja191_factors.py` | 因子计算与训练/服务共享性 | MLE-04 |
| `tests/e2e/test_eod_full_chain_e2e.py` | E2E 覆盖完整性 | MLE-08 |

### 阶段 1 验收
- 所有 8 个 PR 合入 main，CI 全绿
- V9 quick regression 仍通过（HC-1 不破）
- `docs/ecc_audit/AUDIT_MLE_WORKFLOW_GAP.md` 12 项 checklist 中 ≥ 9 项 PASS
- `mypy utils/` 错误数不增加

### 阶段 1 风险与回滚
| 风险 | 概率 | 影响 | 缓解 | 回滚 |
|------|------|------|------|------|
| 激活 drift_monitor 致定时任务异常 | 中 | 高 | Feature Flag `USE_DRIFT_DETECTOR` 透传，仅 sim_mode 开 | 回退 PR，GAP-6 改为文档先行 |
| lgbm 重构破坏 V9 基线回归 | 中 | 高 | 新增 `tests/regression/test_v9_baseline.py` 在 PR 中必跑 | revert `research/lgbm_factor_mining.py`，保留 manifest.json 落盘部分 |
| 数据契约 fail-closed 阻断生产 | 低 | 高 | `DataContract.validate` 默认 warn_only=True，7 天观察期后切 enforce | enforce 改回 warn_only |
| TDD guard 误报（工具脚本无对应测试） | 高 | 中 | `_tdd_guard.py` 排除 `_*.py` / `scripts/test_*.py` / `tools/*` | guard 改为告警不阻断 |

---

## 阶段 2: 测试与验证闭环（P1）

### 输出
1. **verification-loop Python 落地脚本**: `scripts/verify_loop.py`（6 阶段一键执行）
2. **TDD 反馈闭环**: 阶段 1 GAP-4 已建 + 新增 `scripts/_tdd_red_green.py`（本地 RED→GREEN→REFACTOR 助手）
3. **覆盖率提升到 80%**: 补 15+ 个 unit test（优先 `gate_manager` / `mlops_pipeline` / `drift_monitor` / `pipeline_orchestrator`）
4. **智能测试选择**: GAP-5 已建，本阶段接入 CI unit-tests job
5. **验证报告模板**: `docs/ecc_audit/VERIFICATION_REPORT_TEMPLATE.md`

### verification-loop 6 阶段在 Python 落地（本阶段核心交付）

`scripts/verify_loop.py` 的 6 阶段映射:

| Phase | ECC 原(Node.js) | 量化项目(Python) | 命令 | 阻断 |
|-------|------------------|------------------|------|------|
| 1 Build | `npm run build` | **Import smoke**: 确保所有 utils/* 可导入无 ImportError | `python -c "import utils.alpha.mlops_pipeline; import utils.config_manager; ..."` | 是 |
| 2 Types | `npx tsc --noEmit` | **mypy**: 走 `mypy.ini`（沿用 Phase 3-B 渐进式严格） | `python -m mypy --config-file mypy.ini utils/` | 是 |
| 3 Lint | `npm run lint` | **ruff + pylint 双跑**: ruff 全项目，pylint 限 utils/infra\|risk\|execution\|data + v8.3_institutional/src | `ruff check . && python -m pylint --rcfile=.pylintrc utils/infra/ utils/risk/ utils/execution/ utils/data/` | 是 |
| 4 Tests | `npm run test -- --coverage` | **pytest + 覆盖率 + 智能选择**: 目标 80%（渐进 60→70→80） | `python -m pytest tests/unit tests/integration --cov=utils --cov=v8.3_institutional/src --cov-fail-under=60 -n auto -m "not slow" --cov-report=term-missing` | 是 |
| 5 Security | `grep "sk-"` | **bandit + pip-audit + 自定义 secret 扫描**: 扫 `.env*`、硬编码 token、`api_key=` 字面量 | `bandit -r utils/ v8.3_institutional/src/ -ll && pip-audit && python scripts/_scan_secrets.py` | 是（新增） |
| 6 Diff review | `git diff --stat` | **结构化 diff 审查**: 变更文件分类（生产代码/测试/配置/文档）、风险评分、必审清单 | `python scripts/_diff_review.py --base origin/main` | 软告警（供 PR review） |

输出格式（沿用 ECC 模板）:
```
VERIFICATION REPORT
==================
Build:     [PASS/FAIL]
Types:     [PASS/FAIL] (X errors)
Lint:      [PASS/FAIL] (X warnings)
Tests:     [PASS/FAIL] (X/Y passed, Z% coverage)
Security:  [PASS/FAIL] (X issues)
Diff:      [X files changed, risk score: low/med/high]

Overall:   [READY/NOT READY] for PR
```

### 关键文件清单
| 文件路径 | 类型 | 用途 |
|---------|------|------|
| `scripts/verify_loop.py` | 新增 | verification-loop 6 阶段在 Python 项目的执行入口 |
| `scripts/_tdd_red_green.py` | 新增 | 本地 TDD 助手: 生成 test 骨架 → 跑 RED → 实现 → 跑 GREEN |
| `scripts/_scan_secrets.py` | 新增 | 自定义 secret 扫描（.env*、硬编码 token、api_key= 字面量） |
| `scripts/_diff_review.py` | 新增 | 结构化 diff 审查 + 风险评分 + 必审清单 |
| `.github/workflows/ci.yml` | 修改 | unit-tests job 替换为 `--cov --cov-fail-under=60`（渐进到 80%） + 接入智能选择 + 新增 smoke-tests/security-tests job |
| `tests/unit/test_gate_manager.py` | 已存在,扩充 | 补 slice metrics / latency / cost guardrail 测试 |
| `tests/unit/test_lgbm_reproducibility.py` | 阶段 1 已建 | 扩充: 同 seed 不同 dataset_uri → 应 fail |
| `tests/unit/test_data_contract.py` | 新增 | 测试 DataContract 校验器 |
| `tests/unit/test_drift_monitor_sim_mode.py` | 新增 | 激活后行为测试 |
| `tests/unit/test_mlops_pipeline_facade.py` | 新增 | Facade 容错降级测试 |
| `.coveragerc` | 修改 | `fail_under` 60 → 70（本阶段）→ 80（阶段 3 后） |
| `pytest.ini` | 修改 | 新增 marker `contract`（数据契约测试）、`reproducibility`（可复现性测试） |

### 阶段 2 验收
- `python scripts/verify_loop.py` 一键执行，6 阶段全 PASS
- 覆盖率 ≥ 70%（实际值，非 threshold）
- 智能测试选择使 unit-tests job 时间下降 ≥ 30%
- TDD guard 在 PR 模板中显示检查结果
- `tests/e2e/test_eod_full_chain_e2e.py` 8 个用例在 nightly 全绿

### 阶段 2 风险与回滚
| 风险 | 概率 | 影响 | 缓解 | 回滚 |
|------|------|------|------|------|
| 覆盖率提升到 70% 阻断过多 PR | 高 | 中 | 先以 65% 为 threshold，2 周后升 70% | threshold 降回 60% |
| bandit 误报（utils 中合法的 eval） | 中 | 低 | 在 `pyproject.toml` 增加 `[tool.bandit] skips` | bandit 改为告警不阻断 |
| 智能测试选择漏选 | 中 | 高 | 保留全量 nightly 回归兜底；PR 标签 `full-tests` 强制全量 | 关闭智能选择，跑全量 |

---

## 阶段 3: ECC 工作流规范文档（P2）

### 输出
1. **Iteration Compact 模板**: `docs/ecc_templates/ITERATION_COMPACT_TEMPLATE.md`（17 字段空白模板）
2. **Iteration Compact 示例（V9 LGB 场景）**: `docs/ecc_templates/ITERATION_COMPACT_v9_lgb_example.md`
3. **数据契约模板**: `docs/ecc_templates/DATA_CONTRACT_TEMPLATE.md`
4. **晋升门禁标准**: `docs/ecc_templates/PROMOTION_GATES.md`（模型从 sim → live 的 7 道门）
5. **Observation Ledger 模板**: `docs/ecc_templates/OBSERVATION_LEDGER_TEMPLATE.md`
6. **ECC 工作流总览**: `docs/ecc_templates/README_ECC_WORKFLOW.md`
7. **CLAUDE.md 更新**: 顶部增加 ECC 工作流入口指引
8. **CONTRIBUTING.md 新增**: 增加 "新模型/新特征必须先写 Iteration Compact" 条款

### 晋升门禁标准（PROMOTION_GATES.md 核心）

7 道门（对齐 MLE-05 + MLE-09）:

| 门 | 名称 | 通过条件 | 阻断动作 | 对应代码 |
|----|------|---------|---------|---------|
| G1 | 预测契约 | Iteration Compact 已评审 + 数据契约已锁定 | 阻断训练 | `docs/ecc_templates/ITERATION_COMPACT_*.md` |
| G2 | 离线指标 | AUC ≥ 0.82 + 校准误差 ≤ 0.04 + DSR ≥ 8 | 阻断注册 | `utils/alpha/model_registry.py` |
| G3 | 切片指标 | 按行业/市值/波动率分桶，各桶 AUC ≥ 0.75 | 阻断 shadow | 新增 `utils/alpha/slice_evaluator.py` |
| G4 | 延迟/成本 | p95 推理 ≤ 80ms + 单次预测成本 ≤ ¥0.01 | 阻断 shadow | 新增 `utils/alpha/latency_budget.py` |
| G5 | Shadow 14 天 | Sharpe CV ≤ 1.0 + DSR 不退化 + 0 P0 bug | 阻断 canary | `research/vibe_trading_factor_analysis/shadow/` |
| G6 | Canary 7 天 | 实盘小流量（10%资金）Sharpe ≥ V9 × 0.9 | 阻断全量 | `utils/alpha/ab_testing.py` |
| G7 | 双门禁 | `ReturnExpectationGate` + `HedgeCompletenessGate` 全 PASS | 阻断实盘 | `v8.3_institutional/gate_manager.py` |

### 阶段 3 验收
- 6 份模板文档评审通过
- 至少 1 个真实场景使用 Iteration Compact（建议: 下一个 V10 模型迭代）
- `CLAUDE.md` 引用 ECC 工作流入口

### 阶段 3 风险与回滚
| 风险 | 概率 | 影响 | 缓解 | 回滚 |
|------|------|------|------|------|
| 文档过重，团队不采纳 | 高 | 中 | 模板精简到 1 页 PR description 友好 | 退化为 README 章节 |
| 晋升门禁过严阻塞迭代 | 中 | 中 | G3/G4 先 warn_only，30 天后切 enforce | 调低阈值 |

---

## 阶段 4: ECC skills/rules 引入（P3）

### 安装策略：复制 + Python 化适配，不直接 symlink

**原因**: ECC 原文是 Node.js 场景（verification-loop 用 `npm run build`），需替换为 Python 命令；项目已有 8 个 `.claude/skills/`，需保持目录结构一致；不破坏 ECC 源（第三方项目，只读）。

### Python 化适配清单

| Skill | 原文(Node.js) | 替换为(Python/量化) |
|-------|--------------|-------------------|
| `verification-loop` | `npm run build` | `python -c "import utils.alpha.mlops_pipeline"` |
| `verification-loop` | `npx tsc --noEmit` | `python -m mypy --config-file mypy.ini utils/` |
| `verification-loop` | `npm run lint` | `ruff check . && python -m pylint --rcfile=.pylintrc utils/infra/` |
| `verification-loop` | `npm run test -- --coverage` | `python -m pytest tests/unit --cov=utils --cov-fail-under=60` |
| `verification-loop` | `grep "sk-"` | `bandit -r utils/ -ll && python scripts/_scan_secrets.py` |
| `tdd-workflow` | `describe('...', () => {})` | `class TestXxx: def test_yyy(self): ...` |
| `tdd-workflow` | `@testing-library/react` | `pytest fixtures + unittest.mock` |
| `tdd-workflow` | Playwright `page.goto` | `pytest-asyncio + httpx AsyncClient` |
| `coding-standards` | `interface Market {...}` | `@dataclass(frozen=True) class Market: ...` |
| `coding-standards` | `const updatedUser = {...user, name: 'New'}` | `updated = replace(user, name='New')` (dataclasses.replace) |
| `mle-workflow` | 通用 | 保留 + 新增"量化系统适配"章节，引用 `gate_manager.py` / `mlops_pipeline.py` |

### 关键文件清单
| 文件路径 | 类型 | 用途 |
|---------|------|------|
| `.claude/skills/mle-workflow/SKILL.md` | 新增(复制+适配) | MLE 10 任务工作流 |
| `.claude/skills/mle-workflow/agents/openai.yaml` | 新增(复制) | agent 路由配置 |
| `.claude/skills/verification-loop/SKILL.md` | 新增(复制+适配) | 6 阶段验证闭环 |
| `.claude/skills/tdd-workflow/SKILL.md` | 新增(复制+适配) | TDD 80% 覆盖率 |
| `.claude/skills/coding-standards/SKILL.md` | 新增(复制+适配) | KISS/DRY/YAGNI + 不可变优先 |
| `.agents/skills/{4 个}/SKILL.md` | 新增(复制+适配) | 与 .claude/skills 镜像 |
| `.claude/rules/python-quant-guardrails.md` | 新增 | Python 量化项目护栏 |
| `.claude/rules/mle-quant-adapter.md` | 新增 | MLE 工作流量化适配规则 |
| `.claude/rules/verification-python.md` | 新增 | verification-loop Python 化规则 |
| `skills-lock.json` | 修改 | 追加 4 条 skill 记录 |
| `CLAUDE.md` | 修改 | 增加 ECC skill 路由表 |

### CLAUDE.md ECC skill 路由表（追加内容）

```markdown
## ECC Skills 路由

| 用户意图 | 触发 skill |
|---------|-----------|
| "审查 ML 模型/数据契约/可复现性" | mle-workflow |
| "验证代码/build/types/lint/tests/security/diff" | verification-loop |
| "TDD/写测试/覆盖率" | tdd-workflow |
| "代码质量/KISS/DRY/YAGNI/code smell" | coding-standards |
| "审查代码改动" | review-changes (已有) |
| "调试问题" | debug-issue (已有) |
| "探索代码库" | explore-codebase (已有) |
```

### 阶段 4 验收
- `.claude/skills/` 下出现 4 个新 skill 目录，每个含 `SKILL.md` + `agents/openai.yaml`
- `.agents/skills/` 镜像一致
- `.claude/rules/` 下 3 个 .md 文件
- `skills-lock.json` 含 4 条新记录
- `CLAUDE.md` 含 ECC skill 路由表
- 用户输入 "审查 ML 模型" → Claude 自动加载 mle-workflow skill

### 阶段 4 风险与回滚
| 风险 | 概率 | 影响 | 缓解 | 回滚 |
|------|------|------|------|------|
| skill 与项目已有 7 个 skill 冲突 | 低 | 低 | 命名空间隔离（ECC skill 名带 `mle-`/`tdd-`/`verification-`/`coding-standards` 前缀） | 删除新增 4 个 skill |
| Python 化适配引入错误命令 | 中 | 中 | 适配后跑一次 `scripts/verify_loop.py` 验证 | 修正 SKILL.md |

---

## Iteration Compact 实例化（V9 LGB 场景示例）

以下为 `docs/ecc_templates/ITERATION_COMPACT_v9_lgb_example.md` 的内容草案（关键字段）:

- **Goal**: 将 V9 Regime-Specific LGB 模型从 sim_mode 晋升到 live_mode
- **Who cares**: 资金方(年化≥15%/回撤≤10%)、风控方(Sharpe CV≤1.0/|net_delta|<0.05)、运维方(定时任务稳定性)、研究员(可复现性)
- **Decision owner**: 量化负责人
- **User or system action changed by the model**: 每日 15:30 PostMarket 任务调 `predict_annual_return_struct()` 生成次日建仓计划，从静态 20 标的 → V9 LGB 动态选股
- **Success metric**: 年化≥19.62%、回撤≤9.95%、DSR≥8、Sharpe CV≤0.88（均不退化）
- **Guardrail metrics**: p95 推理≤80ms、单次预测成本≤¥0.01、组合 beta≤0.30（对冲后）、|net_delta|<0.05（对冲后）、数据契约校验失败率=0
- **Mistake budget**: 每月允许 FP(误建仓)≤3 次每次损失≤0.5%；每月允许 FN(漏建仓)≤5 次；单日回撤>3% 触发 kill_switch
- **Unacceptable mistakes**: 训练数据泄漏未来信息、模型 artifact 缺失 preprocessing、配置 hash 不一致导致复现失败、双门禁 fail-closed 被绕过
- **Acceptable mistakes**: 单次预测因数据缺失降级到静态 20 标的（有 fallback）、drift 告警误报（7 天观察期内人工核验）、A/B 测试中 shadow 与 live 指标偏差≤10%
- **Assumptions**: A 股交易规则不变(T+1,涨跌停)、IF 期货流动性充足、iFinD/akshare 数据源可用性≥99.5%、V9 回测期(2023-01-01~2026-06-30)代表未来 6 个月
- **Constraints**: HC-1 V9 基线回归是 PR 合并闸门、HC-4 Shadow 14 天验证未通过前不自动晋升、HC-5 ConfigManager 4 级优先级不可绕过、资金规模 500 万(3M 股票 ETF + 2M 期权对冲)
- **Labels and data snapshot**: Entity grain=(symbol,date)、Label=未来 5 日收益率、Label delay=5 个交易日、Feature timing=t 日收盘后计算只用 t-1 及之前数据、Point-in-time join=`df[df.index <= date]` 切片、Split policy=TimeSeriesSplit(n_splits=5)禁随机 split、Dataset snapshot=`data_cache/ohlcv_2023-01-01_2026-06-30.parquet`
- **Baseline**: V8.3 静态 20 标的(年化6.85%/回撤14%/Sharpe0.72)、V9 sim_mode(已跑14天:年化19.62%/回撤9.95%/DSR8/Sharpe CV0.88)
- **Candidate signals**: 动量类(MOM_5D/10D/20D/60D/120D/252D)、波动率类(VOL_5D/20D/60D/120D/252D, DOWNSIDE_VOL, SKEW, KURT)、流动性类(TURNOVER, AMIHUD)、技术指标(MA_DEV, OBV_CHG)、基本面代理(SIZE_PROXY, QUALITY_PROXY)、Regime 调制(因子 × market_regime)
- **Threshold or config plan**: `gate_thresholds.json`(min_annual_return=0.15, max_drawdown=0.10, min_sharpe=1.0, phase_factor=0.70)、`hedge_completeness_gate`(max_portfolio_beta=0.30, max_abs_net_delta=0.05)、LGB 超参(num_leaves=31, learning_rate=0.05, feature_fraction=0.8, bagging_fraction=0.8, seed=42)
- **Eval slices**: 行业(科技/金融/宽基/新能源/医药/资源/AI芯片/半导体设备/超算/金融科技/射频/机器人/光伏/锂钾)、市值(大盘>500亿/中盘100-500亿/小盘<100亿)、波动率(低<20%/中20-40%/高>40%)、Regime(牛市/熊市/震荡市)、时间(2023H1~2025H2 每半年)
- **Known risks**: 数据源切换(akshare→iFinD)致因子漂移、Regime 切换延迟(滞后5日)误判、期权对冲预算不足(2M)在极端行情下 delta 覆盖不全、IF 期货合约换月期间流动性下降
- **Next experiment**: A. 加入 GTJA191 因子(191 个)验证 AUC≥0.02 提升；B. regime 识别改用 HMM 验证 Sharpe CV 改善；C. 仓位优化改用 Black-Litterman 验证回撤改善
- **Rollback or fallback**: 回滚 artifact=`models/registry/v9_lgb/v1/<sha>/model.lgb`；回滚机制=`model_registry.load_model("v9_lgb", stage=ModelStage.PRODUCTION)` 失败时降级到 V8.3 静态 20 标的；回滚触发=实盘连续3日回撤>2% / drift_monitor 触发 Severity.HIGH / `HedgeCompletenessGate.enforce()` 返回 False；回滚执行=`kill_switch.activate(reason="v9_rollback")` → 切回 V8.3 静态 + 平掉所有期权/期货对冲；回滚验证=`tests/regression/test_v9_baseline.py` 跑通 + 双门禁重新校验

---

## 数据契约模板（量化系统专用）

以下为 `docs/ecc_templates/DATA_CONTRACT_TEMPLATE.md` 的核心字段（完整版在阶段 3 产出）:

1. **Entity Grain**: 主键=(symbol, date)，每只标的每个交易日一行
2. **Label Definition**: forward_return_5d = `close[t+5] / close[t] - 1.0`，float64，[-1.0, +inf)，t+5 收盘后可计算，t+6 可观测，label delay=5 个交易日
3. **Feature Schema**: 表格列出 Feature / Type / Source / Timing / Null Policy / Range（如 MOM_5D / float64 / close.pct_change(5) / t 日收盘后 / drop / [-1, 1]）
4. **Point-in-Time Join Rules**: 计算时点 t 的特征时只能用 `df[df.index <= t]` 的数据；`utils/alpha/data_contract.py:validate_point_in_time(panel)` 自动扫描
5. **Split Policy**: Train 2023-01-01~2024-12-31、Validation 2025-01-01~2025-12-31、Test 2026-01-01~2026-06-30、Backtest 2026-07-01~2026-07-28(sim_mode 14 天)；TimeSeriesSplit(n_splits=5)禁随机 split
6. **Required Columns**: 必填 symbol/date/close/open/high/low/volume；可选 amount/turnover/adj_factor；禁止任何未来信息字段（forward_return 除外，作为 label）
7. **Allowed Nulls**: MOM_252D 标的上市不足 252 日时为 null → drop 行；AMIHUD_* volume=0 时为 null → 填 0.0；其他因子 null 比例>5% 触发告警
8. **PII / Sensitive Fields**: 无 PII（量化数据均为公开市场数据）；敏感: 不接除行情外的账户级数据；保留期: 永久（公开数据）
9. **Dataset Version / Snapshot**: Snapshot URI / Snapshot SHA256(待 GAP-7 补) / Code SHA / Config hash
10. **Validation**: 校验器=`utils/alpha/data_contract.py:DataContract.validate(panel)`；校验时机=训练前+服务前；失败动作=默认 `warn_only=True`(7 天观察期后切 `enforce=True`)；校验项=字段存在性/类型/范围/null 比例/point-in-time 切片
11. **Change Policy**: Frozen 后修改必须新建版本(v2)，旧版本保留；破坏性变更需 PR + Iteration Compact 评审；新版本必须能加载旧版本训练的 artifact（向后兼容）

---

## mle-workflow Review Checklist（12 项当前状态）

| # | 检查项 | 量化项目落地映射 | 当前状态 | 目标状态 |
|---|-------|----------------|---------|---------|
| 1 | Prediction contract is explicit and testable | `predict_annual_return_struct()` 返回结构化 dict | 部分 PASS | 补 Iteration Compact |
| 2 | Data contract defines entity grain, label timing, feature timing, snapshot/version | `lgbm_factor_mining.py` 有 entity grain + label timing，无 snapshot/version | FAIL | 阶段 1 GAP-7/8 修复 |
| 3 | Leakage risks were checked against prediction-time availability | `df[df.index <= date]` 切片正确(`lgbm_factor_mining.py:176`) | PASS | 加自动校验 |
| 4 | Training is reproducible from code, config, data version, and seed | seed=42 有，但无 dataset_uri/config_hash/code_sha | FAIL | 阶段 1 GAP-7 修复 |
| 5 | Metrics compare against baseline and current production model | `ReturnExpectationGate` 对齐 V9 基线 | PASS | 加 V8.3 基线对比 |
| 6 | Slice metrics and guardrails are included for high-risk cohorts | 无切片指标 | FAIL | 阶段 3 G3 门禁 |
| 7 | Promotion gates are automated and fail closed | `gate_manager.py` 已 fail-closed(live_mode) | PASS | 加 G2-G6 自动化 |
| 8 | Training and serving transformations are shared or equivalence-tested | `compute_all_factors` 训练/服务同函数，但无等价测试 | 部分 PASS | 加 `test_train_serve_equiv.py` |
| 9 | Model artifact carries version, config, dataset reference, and preprocessing | `model_registry.py` 有版本，无 preprocessing/dataset ref | FAIL | 阶段 1 GAP-7 修复 |
| 10 | Serving path validates inputs and has timeout, fallback, and rollback behavior | 定时任务有 fallback，无 timeout/输入校验 | 部分 PASS | 加 `validate_input()` + timeout |
| 11 | Monitoring covers system health, feature drift, prediction drift, and delayed labels | `drift_monitor.py` 存在但默认 no-op | FAIL | 阶段 1 GAP-6 激活 |
| 12 | Sensitive data is excluded from artifacts, logs, prompts, and examples | 无 PII，但 `account_token` 等需扫描 | 部分 PASS | 阶段 2 Phase 5 安全扫描 |

**当前**: PASS 2, 部分 PASS 4, FAIL 6 → **阶段 1 目标**: PASS ≥ 9

---

## 关键依赖与 sequencing

```
阶段 1 GAP-7(可复现性) ──┐
阶段 1 GAP-8(数据契约) ──┼─→ 阶段 3 数据契约模板
                         │
阶段 1 GAP-6(drift 激活) ─┼─→ 阶段 2 drift_monitor 测试
                         │
阶段 1 GAP-4(TDD guard) ─┼─→ 阶段 2 TDD 反馈闭环
                         │
阶段 1 GAP-1/2/3(烟雾/E2E/覆盖率) ─→ 阶段 2 verification-loop
                         │
阶段 2 verify_loop.py ───┼─→ 阶段 4 skill Python 化适配验证
                         │
阶段 3 模板文档 ─────────┴─→ 阶段 4 rules 引用模板
```

**并行机会**:
- 阶段 3 文档撰写可与阶段 2 测试补全并行
- 阶段 4 skill 复制可与阶段 2 后期并行（适配需等 verify_loop.py 就绪）

---

## 验证策略（端到端测试）

### 阶段 1 验证
1. **V9 基线回归**: `python -m pytest tests/regression/test_v9_baseline.py -v` 全绿
2. **可复现性测试**: `python -m pytest tests/unit/test_lgbm_reproducibility.py -v` 同 config+seed+dataset 重跑 importance top-10 一致率 100%
3. **数据契约校验**: `python -c "from utils.alpha.data_contract import DataContract; DataContract.validate(panel)"` 默认 warn_only 模式跑通
4. **drift_monitor 激活**: 在 sim_mode 跑 1 天，确认产出 drift 报告（即使为空也证明管道可用）
5. **烟雾测试**: `python scripts/_smoke_runner.py` < 60s 跑通

### 阶段 2 验证
1. **verification-loop 一键执行**: `python scripts/verify_loop.py` 输出 6 阶段全 PASS 的 VERIFICATION REPORT
2. **覆盖率验证**: `python -m pytest tests/unit --cov=utils --cov-report=term-missing` 实际覆盖率 ≥ 70%
3. **智能测试选择**: 修改 `utils/alpha/drift_monitor.py` 一行注释，推送 PR，CI 仅跑受影响测试（应包含 `test_drift_monitor_*.py`），CI 时间下降 ≥ 30%
4. **TDD guard**: 新增 `utils/foo.py`（无对应 test），推送 PR，CI 应阻断并提示"缺少 tests/unit/test_foo.py"
5. **安全扫描**: `bandit -r utils/ -ll && pip-audit && python scripts/_scan_secrets.py` 全 PASS

### 阶段 3 验证
1. **Iteration Compact 评审**: 用 V9 LGB 示例文档走一遍评审流程，确认 17 字段齐全且可挑战
2. **数据契约实例化**: 为 V9 模型填一份真实数据契约，跑 `DataContract.validate()` 通过
3. **晋升门禁走查**: 模拟一个模型从 sim → live 的完整流程，确认 7 道门禁触发顺序正确

### 阶段 4 验证
1. **skill 加载**: 在 Claude Code 中打开项目，输入"审查 ML 模型"，确认 mle-workflow skill 自动加载
2. **rules 生效**: 在 Claude Code 中输入"写一个新因子"，确认 coding-standards rules 自动应用（KISS/DRY/YAGNI/不可变优先）
3. **verify_loop.py 验证**: 适配后的 SKILL.md 中所有命令可执行，跑一次 `scripts/verify_loop.py` 全 PASS
4. **skills-lock.json 校验**: `python -c "import json; json.load(open('skills-lock.json'))"` 含 4 条新记录

---

## 关键文件路径汇总

### ECC 源（只读参考）
- `E:\各种PY程序\10_第三方项目\ECC\.agents\skills\mle-workflow\SKILL.md`
- `E:\各种PY程序\10_第三方项目\ECC\.agents\skills\verification-loop\SKILL.md`
- `E:\各种PY程序\10_第三方项目\ECC\.agents\skills\tdd-workflow\SKILL.md`
- `E:\各种PY程序\10_第三方项目\ECC\.agents\skills\coding-standards\SKILL.md`
- `E:\各种PY程序\10_第三方项目\ECC\.claude\rules\everything-claude-code-guardrails.md`

### 量化项目待修改/新增（相对项目根）
**阶段 1**:
- `research/lgbm_factor_mining.py`（GAP-7 可复现性重构核心）
- `utils/alpha/drift_monitor.py`（GAP-6 激活）
- `utils/alpha/data_contract.py`（GAP-8 新增）
- `utils/alpha/delayed_label_tracker.py`（GAP-6 新增）
- `scripts/_smoke_runner.py` / `_select_tests_by_diff.py` / `_tdd_guard.py` / `_check_coverage_trend.py`（新增）
- `.github/workflows/ci.yml` / `.github/workflows/tdd-guard.yml`（修改/新增）
- `docs/ecc_audit/AUDIT_MLE_WORKFLOW_GAP.md` / `AUDIT_CODE_SMELL.md`（新增）
- `docs/runbooks/MODEL_DRIFT_RUNBOOK.md`（新增）
- `docs/contracts/DATA_CONTRACT.md`（新增）
- `tests/unit/test_lgbm_reproducibility.py` / `tests/regression/test_v9_baseline.py`（新增）

**阶段 2**:
- `scripts/verify_loop.py` / `_tdd_red_green.py` / `_scan_secrets.py` / `_diff_review.py`（新增）
- `tests/unit/test_gate_manager.py`（扩充）/ `test_data_contract.py` / `test_drift_monitor_sim_mode.py` / `test_mlops_pipeline_facade.py`（新增）
- `.coveragerc` / `pytest.ini` / `mypy.ini` / `ruff.toml` / `.pre-commit-config.yaml`（修改）
- `docs/ecc_audit/VERIFICATION_REPORT_TEMPLATE.md`（新增）

**阶段 3**:
- `docs/ecc_templates/ITERATION_COMPACT_TEMPLATE.md` / `ITERATION_COMPACT_v9_lgb_example.md` / `DATA_CONTRACT_TEMPLATE.md` / `PROMOTION_GATES.md` / `OBSERVATION_LEDGER_TEMPLATE.md` / `README_ECC_WORKFLOW.md`（新增）
- `CLAUDE.md`（修改）/ `CONTRIBUTING.md`（新增）

**阶段 4**:
- `.claude/skills/{mle-workflow,verification-loop,tdd-workflow,coding-standards}/SKILL.md` + `agents/openai.yaml`（新增）
- `.agents/skills/{4 个}/`（镜像新增）
- `.claude/rules/{python-quant-guardrails,mle-quant-adapter,verification-python}.md`（新增）
- `skills-lock.json` / `CLAUDE.md`（修改）

---

## 总估时

12-18 个工作日（阶段 1: 5-7 天, 阶段 2: 4-6 天, 阶段 3: 2-3 天, 阶段 4: 1-2 天，可部分并行）
