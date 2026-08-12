---
type: project_topic
status: resolved
authoring_mode: ai_generated
created: 2026-08-11
updated: 2026-08-11
contains: code-review, quality-gate, regression, fail-closed, negative-test, baseline, plan-dod
related:
  - cairn/industrial-grade-anti-regression-framework.md
  - cairn/code-review-lessons-v8.4.md
  - docs/UNIFIED_UPGRADE_PLAN_20260810.md
  - docs/CODE_REVIEW_PROCESS.md
---

# 代码审查质量门禁经验沉淀：为什么多次审查仍有 bug

> **状态**: 2026-08-11 复盘总结，已同步到统一升级计划 G16
> **核心结论**: 审查是必要但不充分的质量手段。没有自动化门禁、负向测试、动态验证和 fail-closed 原则支撑的审查，只能 catching 表面问题，无法拦截架构级和数据链路级的沉默失败。

---

## 一、根因分析：为什么"改了又改，改了还错"

### 1. 计划只记录"意图"，没有记录"质量契约"

| 现状 | 后果 |
|------|------|
| 计划写的是"修什么 bug / 加什么功能" | DoD 只定义"改完了"，没定义"验证闭环了" |
| 没有强制"修复后必须跑什么验证" | 修复者凭自觉，容易漏跑或跑错 |
| 没有"负向测试"要求 | 门禁看起来接好了，实际没在拦 |

**铁律**：每项代码变更的 DoD 必须包含：
- 修复前会失败的回归测试（回滚必红 / 修复必绿）
- 门禁同格式负向验证（CI 用正斜杠，本地用反斜杠，必须归一化）
- 基线重冻结确认（ruff / coverage 基线必须随修复重跑）

### 2. 增量修复引入回归，且未立即发现

| 场景 | 后果 |
|------|------|
| 修复 M-1 引入 E501 长行 | 被门禁抓出，证明门禁有效，但本可避免 |
| 修复后不立即跑门禁 | 新 bug 混入，下一轮审查才发现 |
| 增量门禁不扫存量 | 存量问题潜伏，nightly 全量缺失 |

**铁律**：每轮修复批次结束前，必须**立即**跑：
1. `industrial_grade_check.py`
2. `assert_data_validity.py`
3. `engineering_debt_gate.py`
4. `ruff_baseline_gen.py`（若新增/改动文件）

### 3. 审查依赖"静态阅读"，而非"动态验证"

| 问题 | 表现 |
|------|------|
| 行号失效 | 审查报告行号随代码漂移，修复时改错位置或漏改 |
| 假阳性/假阴性 | `check_dangling_refs.py` 曾把路径变更误报为悬挂引用 |
| 未跑负向测试 | 门禁类代码必须用与 CI 完全一致的入参做负向测试 |

**铁律**：
- 审查报告中的行号**必须用工具重新定位**，不可直接按行号编辑
- 门禁类变更必须做**CI 同格式负向测试**（如正斜杠路径 vs Windows 反斜杠）
- 关键 bug 必须**亲自读源码验证**，不可只信子代理输出

### 4. 架构层面的"沉默失败"

| 模式 | 表现 | 案例 |
|------|------|------|
| except 静默吞异常 | 表面正常，实际功能缺失 | `HedgeCoordinator` 导入路径错误被 fail-safe 降级 |
| fail-open 降级过度 | 为保可用性降级到 Mock，但未显式告警 | `SimulatedBroker` 静默替代真实券商 |
| 研究/生产未隔离 | `import research.*` 直接进入生产代码 | 带来不可控依赖 |

**铁律**：
- fail-safe 必须区分**告警与失败**，用独立通道（`send_alert`）而非 `logger.warning`
- 降级必须**显式告警**，不可静默
- 生产代码**绝对禁止** `import research.*`

### 5. 数据链路断链隐蔽

| 模式 | 表现 | 后果 |
|------|------|------|
| 只生成不落盘 | 订单生成后无消费者撮合 | 订单永远停留在 PENDING |
| 只撮合不落盘 | 成交回报只进内存 return dict 即丢弃 | PnL/TCA 无事实源 |
| dry-run 陷阱 | TCA 有记录 ≠ 成交已执行 | 诊断时误判为已执行 |

**铁律**：
- 成交/持仓/fills 必须有**单一落盘真相源**
- 接入新事实源优先**边界桥接 + 来源标记**，不改老模块内脏
- 契约字段必须上下游完全对齐

### 6. 环境与编码陷阱

| 问题 | 表现 | 修复 |
|------|------|------|
| Windows GBK 编码 | `print(¥)` 触发 `UnicodeEncodeError` | 改用 RMB/CNY 或设 `PYTHONIOENCODING=utf-8` |
| 基线不一致 | ruff 基线冻结后未随修复重跑 | 每轮修复后重跑 `ruff_baseline_gen.py` |
| OpenBLAS 内存分配失败 | 线程栈内存不足导致 EOD 崩溃 | 设 `OPENBLAS_NUM_THREADS=1` |

---

## 二、可复制的"防复发"流程模板

### 修复单的 DoD（Definition of Done）

每项代码变更必须满足以下条件才能标记为"完成"：

```markdown
## 修复单 DoD 检查清单

### 代码变更
- [ ] 变更已提交，commit message 带 `[Gx]` 或 `[Ux]` 标记
- [ ] 变更文件已通过 `ruff check` 和 `mypy`
- [ ] 无新增硬编码路径/凭证/行情

### 回归测试
- [ ] 修复前会失败的回归测试已写（回滚必红）
- [ ] 回归测试已跑且 PASS（修复必绿）
- [ ] 测试断言符合引擎真实行为（先读引擎逻辑再写断言）

### 门禁验证
- [ ] `industrial_grade_check.py` 跑完，无新增 FAIL
- [ ] `assert_data_validity.py` 跑完，无新增 FAIL
- [ ] `engineering_debt_gate.py` 跑完，无新增 RED
- [ ] 门禁类变更已做**CI 同格式负向测试**（如正斜杠路径）

### 基线一致性
- [ ] 若新增/改动 Python 文件，已重跑 `ruff_baseline_gen.py`
- [ ] 若新增测试，已重跑 coverage 并确认基线无漂移

### 知识沉淀
- [ ] 若修复了新的 bug 模式，已更新 `cairn/code-review-lessons-v8.4.md` 对应条目
- [ ] 若涉及数据链路/执行链，已更新 `cairn/data-integrity-fix-lessons-*.md`
```

### 审查会话的 DoD

每次代码审查（含 AI 审查）必须满足：

```markdown
## 审查会话 DoD 检查清单

### 审查前
- [ ] 确定审查范围（文件列表）和重点（按资金风险排序：交易执行 > 风控 > 回测 > 基础层）
- [ ] 确认审查工具版本（ocr / ruff / bandit）与 CI 一致

### 审查中
- [ ] 关键 bug 亲自读源码验证，不只信子代理输出
- [ ] 区分"模块本身质量好"与"调用链是软控制"
- [ ] 记录每条缺陷的**真实影响**（资金损失 / 数据失真 / 崩溃 / 沉默失败）

### 审查后
- [ ] 缺陷分级（CRITICAL / HIGH / MEDIUM / LOW）且可溯源
- [ ] 每条缺陷附带**修复验证方法**（命令 + 预期结果）
- [ ] 区分"已修复"与"需修复"——已修复的须有回归测试证明
- [ ] 更新 `cairn/code-review-lessons-v8.4.md` 的 bug 模式清单
```

### 计划模板的 DoD 强制列

计划文档中每项任务必须包含：

```markdown
| 任务 | 负责人 | 验证命令 | 预期结果 | 回滚条件 |
|------|--------|----------|----------|----------|
| 修复 M-1 | xxx | `pytest tests/test_m1.py -v` | 3 个回归测试 PASS | 门禁 FAIL 立即回滚 |
| 接入 QMT | xxx | `python scripts/industrial_grade_check.py` | C1 从 WARN 变为 PASS | dry_run 异常立即回滚 |
```

---

## 三、已沉淀到统一升级计划的 G16

> **来源**: 2026-08-11 复盘，"为什么多次审查仍有 bug"
> **状态**: 已纳入 `docs/UNIFIED_UPGRADE_PLAN_20260810.md` G16
> **验收标准**: 修复单 DoD 和审查会话 DoD 被 100% 执行，且门禁三件套无回归

**G16 核心要求**：
1. 每项代码变更必须附带修复前会失败的回归测试 + 门禁同格式负向验证
2. 每轮修复批次结束前必须重跑 ruff 基线、跑 industrial_grade_check、跑 assert_data_validity
3. 计划模板增加"质量门禁"列：每项任务下面强制列出"验证命令"和"预期结果"
4. 门禁类变更必须 CI 同格式负向测试
5. 建立修复单 DoD 和审查会话 DoD 强制检查清单

---

## 四、相关文档

- 统一升级计划：`docs/UNIFIED_UPGRADE_PLAN_20260810.md`（G16）
- 代码审查经验：`cairn/code-review-lessons-v8.4.md`
- 工业级防复发机制：`cairn/industrial-grade-anti-regression-framework.md`
- 数据链路修复教训：`cairn/data-integrity-fix-lessons-20260806.md`
- 执行闭环修复教训：`cairn/fills-driven-pnl-lessons-20260808.md`
- 代码审查修复批次：`cairn/code-review-fix-batch-20260808.md`
- EOD 操作教训：`cairn/eod-operations-lessons-20260807.md`
