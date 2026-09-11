# Implementation Plan: G1 QMT 真实下单接线（Phase 4 前置演练）

**Branch**: `[G1-qmt-live-order-wiring]` | **Date**: 2026-09-11 | **Spec**: [`spec.md`](./spec.md) | **Roadmap Ref**: `G1-qmt-live-order-wiring`（同 spec.md）

**Input**: Feature specification from `specs/G1-qmt-live-order-wiring/spec.md`

## Summary

**问题**：下单能力**已就位**（`broker_factory` 四重门控 + `QmtBrokerAPI`/`RemoteQmtBroker` + 主链路注入 + 仿真链 12/12 PASS），但 ROADMAP 判定的真实缺口是 **W7.2.1 T15「QMT paper 验证未完成」**——`xtquant → QMT 终端` 段从未在真实模拟账户上端到端跑过，且 `xtquant` 未安装（C1 WARN 的物理根因）。

**技术路线**：
1. **解除物理阻塞**：安装 QMT 官方 `xtquant`（终端配套库，不入本仓依赖树），保持 `enabled=true` + `dry_run=false` 时 C1 由 WARN 转 PASS；
2. **补 T15 验证入口**：新增 `scripts/verify_qmt_paper_chain.py`，在**真实 QMT 模拟账户**上覆盖「连接/登录 → 下单 → 成交回报 → 撤单 → 持仓 → 资金 → 断线重连 → 对账」，报告落盘 `reports/execution/`；
3. **加固"不静默"边界**：新增负向回归（终端不可达 / 门控缺失 / `dry_run=true` 下真实通道调用数必须为 0）；
4. **修文档漂移**：仿真链报告的切换指引仍指向已删除的 `config/system_config.json` 且端口与脚本实际值不一致 → 以 `quickstart.md` 为准并回改指引文本。

**不做**：不重构执行链、不改门控语义、不放宽 C1 判定逻辑（FR-004/FR-008）。

## Technical Context

**Language/Version**: Python（`.venv`；ruff `target-version=py38` 口径 —— 运行时不得用 3.9+ 语法）

**Primary Dependencies**: 现有依赖树（`requirements-core.txt` 等）；**唯一新增 = QMT 官方 `xtquant`**（终端配套库，非本仓依赖，见 FR-008）；**2027 前禁止新增其他生产依赖**

**Storage**: 文件/JSON —— 报告落 `reports/execution/`；成交落 `reports/fills/`（`FillsStore`）；配置事实源 = **根 `system_config.json`**（`config/system_config.json` 已废弃删除，勿再引用）

**Testing**: pytest —— `tests/unit/` 默认；`tests/integration/` 需 `--run-integration`；**勿用 `tests/e2e/`**（祖先目录关键字 → 天生 skip = 静默绿）

**Lint/门禁**: ruff（`ruff.toml`）+ `scripts/ruff_incremental_gate.py`（增量零新增）+ pre-commit 三道门（DTZ005 / mypy 基线 / 单测不写生产目录）

**Performance Goals**: N/A（验证入口为一次性运行，无热路径要求；单次真实验证含终端交互，允许分钟级）

**Constraints**:
- **决策路径 fail-close / 观测路径 fail-open**：实盘就绪路径装配失败必须抛 `LiveBrokerUnavailableError`（`test_p15_live_broker_gate_20260910.py` 既有契约，不得回退）；
- **禁止触碰**：`research.*`、`scripts/industrial_grade_check.py` 的 C1 判定逻辑、现有门控语义；
- **宿主行数护栏**：若需改 `daily_trade_executor.py` 相关（本 feature 预期不需要），受 1500 行结构护栏约束；
- **外部前置**：QMT 客户端 + 模拟账号 + `xtquant` 属真机环境，未就位时 AC-004/AC-005 必须**显式登记豁免**（禁止默认通过）。

**Scale/Scope**: 新增 1 个验证脚本 + 1 个测试文件 + 1 份操作手册；修改面 ≤ 2 文件（其中 1 处仅改报告文本）

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| # | 原则 | 本 plan 的符合性 |
|---|---|---|
| 1 | 规格先于代码 | ✅ 先出 `spec.md`（含 Roadmap Ref + 验收判据表），再出本 plan，实现阶段才开始改代码 |
| 2 | 单向数据流（spec 不得含资金/绩效/窗口数值） | ✅ 已按此**回改 spec**：删去 3 处日期数值（冻结日/决策截止日/Sprint 启动日），改为纯条目引用（`release.batch_plan` / `DECISION NEEDED` 行 / 排期表行名） |
| 3 | fix 类必须附"修复前会失败"回归 | ✅ 文档漂移修复（FR-007/AC-010）与"不静默"加固（FR-006）按先红后绿；测试路径写入 `tasks.md` |
| 4 | converge 未全绿不得声明完成 | ✅ AC-001 固定为 `speckit_converge_gate` exit 0，输出摘录进 `implementation-notes.md` |
| 5 | 棕地只增量 | ✅ 零重构、零删除；仅新增文件 + 2 处最小修改 |
| 6 | 完成声明≠完成（打勾三要素） | ✅ 每条 AC 均要求"文件 + 日期 + 实证值 + 复现命令" |
| 7 | fail-closed | ✅ AC-007/AC-008 是**负向**判据（真实通道调用数必须 = 0、不可达必须抛错）；AC-004 缺外部前置只能**登记豁免**，不得 skip 通过 |
| 8 | 执行链判据（成交须成落盘事实源被消费） | ✅ AC-009 对账 + AC-004 要求成交进 `FillsStore` 并与模拟账户持仓/资金一致，不止"订单已提交" |

**结论**：无违规，无需 Complexity Tracking 豁免（见下节唯一新增项的理由）。

## Project Structure

### Documentation (this feature)

```text
specs/G1-qmt-live-order-wiring/
├── spec.md              # 已完成（含现状锚点 + 验收判据表）
├── plan.md              # 本文件
├── quickstart.md        # Phase 1 输出：真机操作手册（FR-007/AC-010 的收口载体）
├── research.md          # 本次跳过 —— 理由：无算法/模型研究（外部依赖获取路径见 quickstart）
├── data-model.md        # 本次跳过 —— 理由：无新数据实体，沿用既有 Order/Fill/Position 契约
└── tasks.md             # Phase 2 输出（/speckit.tasks 生成，本 plan 不产出）
```

> `research.md` / `data-model.md` 跳过属**显式声明**（非遗漏）：本 feature 为接线/验证型，不引入算法与实体；若后续新增契约字段，须补 `data-model.md`。

### Source Code (repository root)

```text
scripts/verify_qmt_paper_chain.py        # 新增：真实终端段验证入口（FR-003 / AC-004）
tests/unit/test_qmt_paper_chain_gate.py  # 新增：门控/负向回归（FR-001/002/006 / AC-007/008）
scripts/verify_qmt_sim_chain.py          # 最小修改：仅修报告尾部切换指引文本（FR-007 / AC-010）
utils/execution/broker_factory.py        # 条件性微调（仅在确有缺口时；禁止改门控语义）
reports/execution/                       # 报告落盘（既有目录）
```

**只读参考（不修改）**：`ms_strategy/src/execution/qmt_broker.py`、`utils/execution/remote_qmt_broker.py`、`utils/execution/qmt_rpc_server.py`、`scripts/industrial_grade_check.py`、`scripts/run_trade_reconciliation.py`、`tests/unit/test_p15_live_broker_gate_20260910.py`

**Structure Decision**：**新增独立入口而非改造既有仿真链脚本**。理由：`verify_qmt_sim_chain.py` 的设计前提是"不依赖 QMT 终端/xtquant"，其内部**主动绕过**终端层（`gateway.broker = sim`、`gateway.ensure_connected = lambda: True`）；若在其上叠加真实验证，必须弱化这些替身，会破坏其"零依赖可复现"的既有价值（该脚本是 CI/无 QMT 环境的基线，AC-003 依赖它）。故按"新增 + 引用同一套后端契约"处理，两个入口共享 `RemoteQmtBroker`/`broker_factory` 装配路径，避免第二套实现。

## Complexity Tracking

> Constitution Check 无违规。以下为唯一新增顶层文件的理由登记（非豁免，为可追溯）。

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| 新增 `scripts/verify_qmt_paper_chain.py`（第 2 个 QMT 验证入口） | 真实终端段验证与"零依赖代码链验证"是**互斥前提**（前者需终端在线，后者需绕过终端） | 改造 `verify_qmt_sim_chain.py` 会让其失去无 QMT 环境下的可复现性（AC-003 基线靠它），且掺入外部依赖后 CI 无法跑 → 违反"门禁可复现"铁律 |

## 实施阶段与 AC 映射（供 `/speckit.tasks` 展开）

| 阶段 | 内容 | 对应 AC | 可离线完成 |
|---|---|---|---|
| P1 | 门控/负向回归先行（先红后绿）：终端不可达抛错、`dry_run=true` 真实通道调用数 = 0 | AC-006/007/008 | ✅ |
| P2 | 新增真实终端段验证入口（结构先就位，终端未在线时**明确报"前置未满足"并 exit≠0**，不得静默 pass） | AC-004 | 部分（结构可离线，实跑需真机） |
| P3 | 文档漂移修复 + `quickstart.md` 真机照做指引 | AC-010 | ✅ |
| P4 | 真机执行：装 `xtquant` → 模拟账户跑通 → 报告落盘 → C1 转 PASS | AC-002/004/005/009 | ❌ 需外部前置 |
| — | 四门禁收敛 | AC-001 | ✅ |
