---
type: project_topic
status: resolved
authoring_mode: ai_generated
created: 2026-08-06
updated: 2026-08-06
contains: data-pipeline, alpha-signals, drift-integrator, hedge-execution, dry-run-trap, dual-code-path, diagnosis-methodology
related:
  - cairn/LOG.md
  - cairn/code-review-lessons-v8.4.md
  - cairn/code-quality-review-wave6-20260806.md
  - cairn/bug_fix_tracker.md
  - docs/自我升级计划完成进度及后续工程_20260806.md
  - docs/runbooks/HEDGE_ORDER_EXECUTOR_RUNBOOK.md
---

# 数据修复经验沉淀：EOD 管道数据断链诊断与修复（2026-08-06）

> 本文沉淀 2026-08-06 数据完整性核查中发现的 **4 类数据断链模式**、**根因诊断方法论**与**防复发机制**。核心目的是让同类数据缺失在后续 EOD 运行中**不再复发**——比"补一个文件"更重要的是"建立数据完整性校验链"。
> **状态**: 4 个数据缺失问题已于同日全部修复完成。

## 一、最重要的教训（Top 3）

### 1. 双代码路径导致数据产出盲区——最隐蔽的断链

EOD 管道有两个 alpha 信号产出路径：`AlphaPipeline.run()`（保存 `alpha_signals_*.json`）和 `institutional_pipeline_runner._real_alpha_signals()`（不保存）。两者产出相同数据但走不同代码路径，后者缺少前者的保存逻辑，导致 DriftShadowIntegrator 永远读不到预测数据。

**铁律**：
- 同一数据产出的所有代码路径必须有**等价的数据落盘行为**，不能只在一个路径保存
- 下游消费者（如 DriftShadowIntegrator）依赖的文件名契约必须在**所有产出路径**中满足
- 新增下游消费者时，审计所有上游产出路径是否覆盖该数据契约
- **数据契约即接口**：`reports/pipeline/alpha_signals_*.json` 是 DriftShadowIntegrator 的接口，任何产出 alpha 信号的路径都必须写入此文件

### 2. dry-run 陷阱——运行了但没落盘

`hedge_order_executor.py` 在 08-06 创建并运行了，TCA fills 日志（`reports/tca/fills_*.jsonl`）有 5 笔成交记录，但成交回报 JSON（`hedge_execution_fill_*.json`）未落盘、`positions.json` 未更新。根因是执行器以 dry-run 模式运行——代码 L432-435 显示 dry-run 不落盘不更新持仓。

**铁律**：
- TCA 日志 ≠ 成交回报：TCA 是执行后归因的审计日志，成交回报是数据契约文件。两者必须同时产出
- dry-run 模式必须在**日志和返回值中显式标记**（`status: "DRY_RUN"` vs `status: "FILLED"`），不能仅靠"文件不存在"来推断
- EOD 管道挂接执行器时，必须确认运行模式（dry-run vs live），不能假设默认是 live
- **诊断方法**：当 TCA 有记录但成交回报缺失时，首先检查执行器的 dry_run 标志

### 3. 诊断报告可能过时——代码在持续演进

08-06 上午生成的"期权对冲未执行根因诊断报告"结论是"系统缺少执行器"，但下午发现 `hedge_order_executor.py` 已创建并运行。诊断报告的结论在代码演进后过时，但报告本身未更新。

**铁律**：
- 诊断报告必须标注**诊断时间戳**和**代码版本快照**，读者据此判断结论是否仍有效
- 修复问题前必须**重新验证诊断结论**——代码可能在诊断后已被修改
- 根因诊断应区分"架构缺失"（需要新建模块）和"运行时配置错误"（模块存在但运行模式不对），两者修复方案完全不同
- **验证优先于文档**：先运行代码确认当前状态，再读诊断报告理解背景

---

## 二、4 个数据断链的根因与修复

### 断链 1：Alpha 信号产出缺失

**现象**: `reports/pipeline/` 目录下 08-06 没有 `alpha_signals_20260806*.json`，最新文件是 08-05 21:40 的测试数据（`model_version=v9_test`）。

**根因**: `institutional_pipeline_runner.py` 的 `_step_signal_fusion()` 调用 `_real_alpha_signals()` 产出信号字典后直接传入 `signal_fusion.fuse()`，**不保存到文件**。而 `AlphaPipeline.run()`（L104）的 `_save_signal_report()` 才有保存逻辑。两个代码路径产出相同数据但只有一个保存。

**影响链**: alpha_signals 缺失 → DriftShadowIntegrator `_load_latest_predictions()` 返回 None → `current_predictions=None` → DelayedLabelTracker 无预测记录 → `observed=0/0` → IC/IC_IR 无法计算 → 08-20 决策日 DriftMonitor/Public/Private 评估基于无效数据。

**修复**: 在 `_step_signal_fusion()` 中新增 `_save_alpha_signals_report()` 调用（L631），将 alpha 信号保存为 `reports/pipeline/alpha_signals_{timestamp}.json`。手动补生成 08-06 报告（6 个标的）。

**验证**: 重新运行 DriftShadowIntegrator，`observed` 从 0/0 变为 6/6，`observation_rate=1.0`。

### 断链 2：DriftShadowIntegrator observed=0/0

**现象**: `reports/drift/integration_2026-08-06.json` 显示 `n_predictions=0, n_observed=0, ic=0.0`。

**根因**: 不是 DriftShadowIntegrator CLI 未传入 `current_predictions`（LOG 诊断的结论有误）。实际上 CLI L778 已经调用 `_load_latest_predictions()` 并传入。真正原因是 `_load_latest_predictions()` 返回 None，因为 alpha_signals 文件不存在（断链 1）。

**教训**: LOG 2026-08-06 的 DriftShadowIntegrator 诊断条目说"CLI 未传入 current_predictions"，但源码核查发现 CLI L778 实际已传入。**诊断结论必须与源码交叉验证**，不能仅凭日志推断。

**修复**: 修复断链 1 后，重新运行 `drift_shadow_integrator --date 2026-08-06`。

**验证**: `integration_2026-08-06.json` 更新为 `n_predictions=6, n_observed=6`。

### 断链 3：期权成交回报未落盘

**现象**: TCA fills 日志有 5 笔成交记录，但 `hedge_execution_fill_2026-08-06.json` 不存在，`positions.json` 的 `active_orders` 全部 PENDING。

**根因**: `hedge_order_executor.py` 以 dry-run 模式运行（L432-435 dry-run 分支不落盘不更新持仓）。TCA 归因（L395-408）在非 dry-run 时才调用，但 TCA fills 日志由 OptionsSimBroker 内部写入，不受 dry_run 标志控制——因此 TCA 有记录但成交回报缺失。

**修复**: 以非 dry-run 模式重新运行 `hedge_order_executor.py --date 2026-08-06`。

**验证**: 5 笔订单全部 FILLED，成交回报落盘到 3 个位置（`reports/` + `v8.3_institutional/reports/` + `每日报告归档/`），`positions.json` 的 5 笔订单 status 更新为 FILLED，`actual_positions` 记录 5 笔成交明细。组合 Beta 从 0.5186 降至 0.3558。

### 断链 4：VolRegimeWeighter 每日报告缺失

**现象**: `reports/volatility/` 只有 `daily_run_report_2026-08-05.md`，没有 08-06 的报告。权重建议 JSON 已产出但 EOD 链路 16:05 的报告生成步骤未执行。

**根因**: 08-05 的报告是手动基于后台进程日志解析生成的，不是自动产出。08-06 没有运行 `--live` 盘中监控（或运行了但日志未解析）。

**修复**: 基于 `vol_regime_weights_2026-08-06.json` 生成简化版报告。

**教训**: 依赖手动步骤产出的报告不可靠。EOD 管道应自动生成 VolRegime 每日报告，而不是依赖人工解析日志。

---

## 三、根因诊断方法论

### 3.1 数据完整性核查的 5 步法

今日实践验证有效的数据完整性核查流程：

第一步，**列出所有应产出的数据文件**：按 EOD 管道阶段顺序列出每个阶段应产出的文件（盈亏报告、风控报告、交易计划、Shadow 样本、VolRegime 权重、Drift 集成、TCA fills、alpha_signals 等）。

第二步，**逐一验证文件是否存在**：用 `search_file` 或 `dir` 命令检查每个文件是否存在，标记缺失项。

第三步，**对缺失项追溯上游代码路径**：搜索产出该文件的代码（`search_content` 查找文件名模式），确认代码路径是否被 EOD 管道调用。

第四步，**区分"代码缺失"与"运行时问题"**：代码存在但文件缺失 → 运行时问题（dry-run、异常吞掉、路径错误）；代码不存在 → 架构缺失（需要新建模块）。

第五步，**修复后重新运行并验证**：修复后重新执行相关步骤，确认缺失文件已产出且内容正确。

### 3.2 诊断报告的时效性管理

诊断报告（如"期权对冲未执行根因诊断报告"）是某个时间点的快照，代码演进后结论可能过时。管理原则：

- 诊断报告必须标注诊断时间戳和代码版本（git commit 或文件修改时间）
- 修复前重新验证诊断结论：运行代码确认当前状态，而非仅读报告
- 诊断报告区分"架构缺失"（需新建模块）和"运行时问题"（模块存在但配置不对）
- 修复完成后在诊断报告中追加"修复验证"章节，标注实际修复方案与诊断结论的差异

### 3.3 双代码路径的审计方法

同一数据产出的多个代码路径是数据断链的高发区。审计方法：

- 搜索下游消费者依赖的文件名模式（如 `alpha_signals_*.json`），找到所有写入该文件的代码路径
- 对比每个路径的写入逻辑是否等价（都调用相同的保存函数，或都实现等价的写入逻辑）
- 在 EOD 管道的每个阶段完成后，添加数据完整性断言：预期文件是否存在、内容是否非空
- 新增下游消费者时，审计所有上游产出路径是否覆盖新消费者的数据契约

---

## 四、防复发机制

### 4.1 EOD 数据完整性校验清单

EOD 管道完成后应自动校验以下数据文件（建议在 `phase5_archive` 之前新增 `phase4_8_data_integrity_check`）：

| 数据文件 | 产出阶段 | 校验条件 |
|----------|----------|----------|
| `每日报告归档/{date}/daily_pnl_report_{date}.md` | phase1 | 文件存在且含"综合净盈亏" |
| `每日报告归档/{date}/eod_guard_report_{date}.md` | phase4 | 四 Guard 全通过 |
| `每日报告归档/{date}/trade_plan_{next_date}.*` | phase2 | 含 execution_plan |
| `reports/pipeline/alpha_signals_{date}_*.json` | phase3 | signals 字典非空 |
| `reports/drift/integration_{date}.json` | phase4.7 | n_predictions > 0 |
| `reports/shadow/{date}_dsr.json` | phase4.5 | samples > 0 |
| `reports/evolution/daily_briefing{date}.md` | 16:10 | 观察期天数递增 |
| `reports/evolution/vol_regime_weights_{date}.json` | 16:05 | regime 字段存在 |
| `reports/volatility/daily_run_report_{date}.md` | 16:05 | 文件存在 |
| `reports/hedge_execution_fill_{date}.json`（有期权订单时） | hedge phase | orders 非空且 status=FILLED |
| `reports/tca/fills_{date}.jsonl`（有成交时） | hedge phase | 行数 > 0 |

### 4.2 代码路径覆盖检查清单

新增下游消费者或修改上游产出路径时，检查：

- [ ] 搜索下游依赖的文件名模式，找到所有写入路径
- [ ] 每个写入路径是否调用等价的保存逻辑
- [ ] EOD 管道是否调用了正确的写入路径（不是仅测试路径）
- [ ] dry-run 模式是否在返回值和日志中显式标记
- [ ] 下游消费者是否能区分"文件不存在"和"文件为空"

### 4.3 环境层面：Python site.py GBK 编码问题

今日发现 `C:\Program Files\Python38\lib\site.py` 在加载 user site-packages 时因 `.pth` 文件含非 ASCII 字符触发 `UnicodeDecodeError: 'gbk' codec can't decode byte 0xa7`。这阻塞了所有直接调用 `python` 的命令。

**临时绕过**: 设置 `PYTHONUSERBASE=C:\NUL` 禁用 user site-packages。
**根本修复**: 找到并清理 user site-packages 目录中含非 ASCII 字符的 `.pth` 文件，或设置 `PYTHONUTF8=1` 环境变量强制 UTF-8 模式。

---

## 五、修复涉及的文件清单

| 文件 | 修改类型 | 内容 |
|------|----------|------|
| `institutional_pipeline_runner.py` | 代码新增 | `_save_alpha_signals_report()` 方法 + L631 调用 |
| `reports/pipeline/alpha_signals_20260806_160000.json` | 数据补生成 | 6 个标的的 alpha 信号 |
| `reports/drift/integration_2026-08-06.json` | 数据更新 | observed 0/0 → 6/6 |
| `reports/hedge_execution_fill_2026-08-06.json` | 数据新生成 | 5 笔期权成交回报 |
| `v8.3_institutional/reports/hedge_execution_fill_2026-08-06.json` | 数据新生成 | 同上（v8.3 路径） |
| `每日报告归档/2026-08-06/对冲执行单_20260806.json` | 数据新生成 | 归档副本 |
| `config/positions.json` | 数据更新 | 5 笔订单 PENDING → FILLED + actual_positions |
| `reports/volatility/daily_run_report_2026-08-06.md` | 报告补生成 | VolRegime 每日运行报告 |

---

## 六、与已有经验沉淀的关系

| 文档 | 关系 |
|------|------|
| `cairn/code-review-lessons-v8.4.md` | 上游：08-05 代码审查经验，本文是 08-06 数据修复经验，同属"防复发机制"系列 |
| `cairn/code-quality-review-wave6-20260806.md` | 同日：Wave6 代码审查修复 7 个缺陷，本文修复 4 个数据断链，互补 |
| `cairn/bug_fix_tracker.md` | 追踪表：本文的修复项应追加到此表 |
| `docs/自我升级计划完成进度及后续工程_20260806.md` | 上游规划：U8/U9 升级项的修复实践，本文是执行经验沉淀 |

---

## 修订记录

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-08-06 | v1.0 | 初版：4 个数据断链的根因分析、修复方案、诊断方法论、防复发机制 |
