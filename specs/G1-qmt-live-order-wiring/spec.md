# Feature Specification: G1 QMT 真实下单接线（Phase 4 前置演练）

**Feature Branch**: `[G1-qmt-live-order-wiring]`

**Created**: 2026-09-11

**Status**: Draft

**Roadmap Ref**:
- `cairn/ROADMAP.md` → `critical_gates.gates_trio.industrial_grade_check`（C1 = xtquant 未装，物理阻塞，当前 WARN）
- `cairn/ROADMAP.md` → `DECISION NEEDED` 行「C1 WARN（xtquant 未装=物理阻塞）」——**本 feature 的权威决策行**（截止日/处置选项以该行为准，本 spec 不复写）
- `cairn/ROADMAP.md` → `release.production_switch_window` / `release.batch_plan`（发布批次与冻结节点以该条目为准）
- `docs/排期计划总览_20260910.md` → Wave 7 Sprint 2 启动行（**W7.2.1 T15 QMT paper 验证为真实缺口**）
- `docs/vnpy_接入spec_20260905.md` §3.2（vnpy 劝退裁决依据：程序化下单通道已就位，真实缺口 = T15 验证缺口，**非能力缺口**）
- `docs/GAP_ASSESSMENT_v9.1_工业级达标计划_20260806.md` G1 条

**Input**: User description: "把已实现的 QmtBrokerAPI（xtquant 门控）接入主链路，完成 dry_run 影子期接线"

---

## 现状锚点（实测，2026-09-11）

> 本节是**事实基线**，非需求。写需求前必须依据本节，禁止沿用历史报告口径（审计报告会过时）。

| 能力 | 实测状态 | 证据 |
|---|---|---|
| `QmtBrokerAPI`（xtquant 门控） | ✅ 已实现，**门控生效**（未装时构造抛 RuntimeError） | `ms_strategy/src/execution/qmt_broker.py`（L34-40 `XTQUANT_AVAILABLE`、L90-91 构造门控） |
| 云端桥接通道 | ✅ 已实现 | `utils/execution/remote_qmt_broker.py`（`RemoteQmtBroker`）+ `utils/execution/qmt_rpc_server.py` |
| 四重门控装配 | ✅ 已实现，**实盘就绪路径 fail-closed** | `utils/execution/broker_factory.py` `get_broker()` / `is_live_intent()` / `LiveBrokerUnavailableError`（装配失败抛错，不降级） |
| 主链路注入 | ✅ 已接线（**位置已迁移**） | `utils/execution/automated_execution_system.py` L221 `_broker = get_broker()` → L255 `OrderRouter(broker=_broker)`；根 `automated_execution_system.py` 仅为 2.8KB re-export 兼容层 |
| 门控回归测试 | ✅ 已具备"修复前会失败"用例 | `tests/unit/test_p15_live_broker_gate_20260910.py` |
| 执行链**代码正确性**验证 | ✅ **12/12 PASS**（09-11 实测） | `scripts/verify_qmt_sim_chain.py` → `reports/execution/qmt_sim_chain_verification_20260911.md` |
| **`xtquant` 安装** | ❌ **NOT INSTALLED**（C1 WARN 的物理根因） | `python -c "import xtquant"` 实测 |
| **`xtquant → QMT 终端` 端到端 paper 验证** | ❌ **未做 = T15 真实缺口** | 仿真链报告自述「未覆盖 (需真实 QMT 模拟账户)」 |
| 配置事实源 | ⚠️ 根 `system_config.json`（`config/system_config.json` 已于 09-07 合并并删除） | `broker_factory._load_broker_config()` L122-124 注释 + 实测 |
| 切换指引文档漂移 | ⚠️ **实测发现**：`verify_qmt_sim_chain.py` 的报告/模块 docstring 指引仍指向**已删除**的 `config/system_config.json`（该文件 09-07 已合并至根 `system_config.json`），按此指引操作会找不到文件 | 该脚本 L15、L239（`--port 8765` 经核实**正确**，非漂移） |

**结论**：本 feature **不是**开发下单能力（能力已就位），而是 **① 解除物理阻塞（装 xtquant）② 补 T15 真实验证缺口 ③ 修文档漂移**。范围必须严格按此收口，禁止顺带重构执行链。

## User Scenarios & Testing *(mandatory)*

### 主场景

**US-1（P1，T15 本体）**：作为运维者，我需要在**真实 QMT 模拟盘账户**上跑通「连接 → 下单 → 成交回报 → 持仓/资金查询 → 对账」全链路，并得到一份可归档的验证报告，以便在功能冻结窗生效前消解 C1 WARN，而非以"代码看起来接好了"充当验证。

**US-2（P1，防裸实盘）**：作为风控责任人，我需要确认**任何一种门控缺失**（未装 xtquant / `enabled=false` / `dry_run=true` / `TRADING_ENV≠production` / 真实装配失败）下，系统都**不会**真实报单，也**不会**静默降级冒充成功——实盘就绪路径必须硬失败、日常路径必须降级但留痕。

**US-3（P2，灰度就绪）**：作为资金责任人，我需要 7 天影子期（灰度第 1 档）的记录能证明「影子成交 vs 真实模拟盘成交」可逐笔对照，以便按 ROADMAP 灰度铁律逐档升档时用的是证据而非感觉。

**US-4（P2，可复现操作）**：作为接手的执行者（可能不是本人），我需要按文档指引能**一次照做成功**完成真实终端切换，不必猜测路径与端口。

**US-5（P3，故障不静默）**：作为 On-Call，我需要 QMT 终端断线/超时/账号未登时，系统给出明确失败与告警，而不是"订单已提交"的假成功。

### 验收场景（Given/When/Then）

| # | Given | When | Then |
|---|---|---|---|
| AS-1 | xtquant 已装 + QMT 模拟终端已登录 + `enabled=true`/`dry_run=false`/`TRADING_ENV=production` | 对模拟账户下 1 笔限价单 | 返回真实 `order_id`；成交回报落 `FillsStore`；持仓与资金查询与模拟账户一致；报告落盘可归档 |
| AS-2 | 同上但 xtquant 缺失 | 启动主链路 | 抛 `LiveBrokerUnavailableError`，**拒绝启动**，不得降级模拟后继续跑 |
| AS-3 | `dry_run=true` | 全链路跑一遍 | **0 笔真实报单**；影子记录完整；告警通道有 INFO 留痕 |
| AS-4 | QMT 终端未登录 | 下单 | 明确失败 + 告警（不静默），订单不落 `FillsStore` 伪造成交 |
| AS-5 | 影子期 7 天已有记录 | 生成对照 | 影子成交与模拟盘成交可逐笔对齐，差异项列明原因 |

## Requirements *(mandatory)*

**FR-001**：系统 MUST 在 `xtquant` 可用时，经 `broker_factory.get_broker()` 装配真实通道；装配失败 MUST 抛 `LiveBrokerUnavailableError`（fail-closed），**禁止**降级继续（回归由 `test_p15_live_broker_gate_20260910.py` 守护）。

**FR-002**：真实下单 MUST 同时满足四重门控 —— ①`broker.enabled=true` ②`dry_run=false` ③`TRADING_ENV=production` ④通道装配成功。任一缺失 MUST 走 `SimulatedBroker` 并留痕（防裸实盘）。

**FR-003**：`xtquant → QMT 终端` 段 MUST 有端到端验证入口（真实模拟账户），覆盖：连接/登录、下单、成交回报、撤单、持仓查询、资金查询、断线重连；报告 MUST 落盘 `reports/execution/`。

**FR-004**：验证入口 MUST 与 C1 判据联动 —— 验证通过后 `scripts/industrial_grade_check.py` 的 C1 MUST 由 WARN 转 PASS（`enabled=true` 且 `dry_run=false` 时），且**不得**为转 PASS 而放宽 C1 判定逻辑本身。

**FR-005**：`dry_run=true` 影子期 MUST 产出可逐笔对照的记录（影子成交 vs 真实模拟盘成交），供灰度升档取证。

**FR-006**：QMT 终端不可达/未登录/超时 MUST 明确失败并经 `send_alert` 告警（`title` 必填），**禁止**静默吞异常（对齐"决策路径 fail-close、观测路径 fail-open"铁律）。

**FR-007**：切换指引 MUST 与代码事实一致 —— 指向**根** `system_config.json`（非已删除的 `config/system_config.json`），端口/环境变量名与代码实际默认值一致；指引 MUST 可被"一次照做成功"（实测）。

**FR-008**：本 feature MUST NOT 新增第三方依赖（除 QMT 官方 `xtquant` 本体），MUST NOT 触碰研究侧代码，MUST NOT 改变现有门控语义。

**FR-009**：所有新增/修改代码 MUST 通过 `speckit_converge_gate` 四门禁（AC-001）。

**FR-010**：本 feature 的实盘启用 MUST 遵守 ROADMAP 灰度铁律与发布批次（引用 `release.batch_plan`），**不自行提前**。

## 口径引用（28仓铁律）

> **只引用条目名，禁止在本 spec 复制数值。** 数值以 `cairn/ROADMAP.md` §CURRENT STATE 为准（含后续修订）。

| 口径 | 引用位置（不复制值） |
|---|---|
| 资金档位与验收基据（灰度档、账户口径） | `ROADMAP` → `release.performance_targets.accounts` / `acceptance_basis` |
| 生产切换窗与冻结日 | `ROADMAP` → `release.production_switch_window` / `release.batch_plan` |
| 发布阻塞判据（门禁三件套） | `ROADMAP` → `RELEASE GATES` → `G-2`（观察窗自冻结日起算） |
| C1 WARN 处置决策行 | `ROADMAP` → `DECISION NEEDED`「C1 WARN（xtquant 未装=物理阻塞）」 |
| 数据源优先级（若涉及行情回填） | `ROADMAP` 口径 + `MEMORY` 数据源链；**禁止**在本 spec 复述 |

**冲突处理**：若本 spec 与 ROADMAP 冲突，以 ROADMAP 为准并回改本 spec（单向数据流）。

## Success Criteria *(mandatory)*

### 验收判据表（Acceptance Criteria）

| # | 判据（门禁/测试） | 期望值 | 复现命令 |
|---|---|---|---|
| AC-001 | `speckit_converge_gate` 四门禁（G1 ruff 增量 / G2 pytest / G3 industrial_grade / G4 assert_data_validity） | exit 0 | `.venv/Scripts/python.exe scripts/speckit_converge_gate.py --files <改动.py...> --pytest-args "<tasks.md 测试范围>"` |
| AC-002 | `xtquant` **可用**（能力级：`xtdata` + `xttrader` 子模块均可导入）——**非**命名空间级 | `_xtquant_available() is True`，且运行解释器 ≤3.13 | 见下方 AC-002 口径修正 |
| AC-003 | 执行链**代码正确性**验证（既有基线，防回归） | 12/12 PASS，RC=0 | `.venv/Scripts/python.exe scripts/verify_qmt_sim_chain.py` |
| AC-004 | **真实终端段** paper 验证（T15 本体；入口由 plan 定名，预期 `scripts/verify_qmt_paper_chain.py`） | 全项 PASS + 报告落盘 `reports/execution/` | `.venv/Scripts/python.exe scripts/verify_qmt_paper_chain.py --account <模拟资金账号>` |
| AC-005 | C1 判据由 WARN → PASS（`enabled=true` + `dry_run=false`） | C1 = PASS | `.venv/Scripts/python.exe scripts/industrial_grade_check.py` |
| AC-006 | 门控矩阵回归（含"修复前会失败"用例） | 全绿 | `.venv/Scripts/python.exe -m pytest tests/unit/test_p15_live_broker_gate_20260910.py -q` |
| AC-007 | 防裸实盘：`dry_run=true` 全链路 0 真实报单 | 0 笔 + 有留痕 | `TRADING_ENV=production` + `dry_run=true` 跑验证入口，断言真实通道调用数 = 0 |
| AC-008 | 断线不静默：终端不可达 → `LiveBrokerUnavailableError` + 告警 | 抛错且告警记录存在 | `pytest` 用例（mock 终端不可达） |
| AC-009 | 对账：paper 成交 vs broker 回报/持仓 一致 | 0 差异（差异项须列因） | `.venv/Scripts/python.exe scripts/run_trade_reconciliation.py` |
| AC-010 | 文档漂移修正：指引指向根 `system_config.json`（不再出现 `config/system_config.json`），环境变量/端口与代码默认值一致，且按指引实操可一次成功 | 全仓 0 处失效路径 + 实测照做成功 | `Select-String -Path scripts/verify_qmt_sim_chain.py -Pattern 'config/system_config'`（应为空）+ 人工照做 |
| AC-011 | 无新增第三方依赖（`xtquant` 除外） | 依赖清单无新增 | 依赖文件 diff 审查 |

### AC-002 口径修正（2026-09-11 实测，防"门禁假 PASS"）

原口径 `.venv/Scripts/python.exe -c "import xtquant"` 是**假 PASS 陷阱**，已作废：

- `xtquant` 的 wheel 标记为 `py3-none-any` ⇒ **任意** Python 都能 `pip install` 成功；
  但其二进制仅提供 **cp36–cp313**（`datacenter.*.pyd`、`xtpythonclient`）。
- 项目主 venv 为 **Python 3.14.4** ⇒ `import xtquant` **成功**，而
  `from xtquant import xtdata` / `from xtquant.xttrader import XtQuantTrader` **双双 ImportError**。
- 实测对照（同一份 site-packages）：Py3.14 → 顶层 OK / 两个子模块 FAIL；Py3.11 → 三者全 OK。

**修正后的判据**：只认能力级导入（`xtdata` 且 `xttrader` 成功），并附加**运行解释器版本约束（≤3.13）**。
防假绿回归：`tests/unit/test_qmt_paper_chain_gate.py::test_xtquant_check_rejects_namespace_only_install`
（构造"只有 `__init__.py` 的 xtquant" → 必须判为不可用；修复前该用例会失败）。

**架构含义（本 feature 的真实边界）**：broker 侧代码**不能在项目主 venv（Py3.14）内直连 QMT**，
须在 **Python≤3.13** 的解释器中运行（如已在用的独立 `xtquant_env`，Py3.11），
或使用 QMT 客户端自带 Python。此项属**硬约束**，不是可绕过的安装问题。

**复现命令**（Py3.11 对照组，实测三者全 OK）：
`.venv/../xtquant_env/Scripts/python.exe -c "from xtquant import xtdata; from xtquant.xttrader import XtQuantTrader"`

### 完成定义（DoD）

- 每条 AC 均有**实证值 + 复现命令**，打勾须满足三要素（文件 + 日期 + 实证值）；
- 修复类变更附"修复前会失败"回归；门禁类变更附 CI 同格式**负向验证**（AC-007/AC-008 属此类，空集合必须 RC≠0）；
- 未达成项须**显式登记**为豁免（编号 + 理由 + 影响评估），禁止静默跳过。

## Assumptions

- xtquant 未安装期间，既有验证以 `SimulatedBroker` fail-open 降级路径 + 门控单测为准（不裸实盘）；AC-004/AC-005 依赖真机环境（QMT 客户端 + 模拟账号），属**外部前置**。
- `xtquant` 只能从 QMT 官方渠道获取，**不纳入**本仓依赖管理（属终端配套库）。
- 灰度升档节奏以 `ROADMAP` 为准，本 feature 只负责"能证明"，不负责"决定升档"。

## Out of Scope

- 期货端（100 万）开户/接入/验收（ROADMAP 已注明"未排期 → 2027"）；
- 执行链重构、性能优化、C++ 热路径（2027 计划，且触碰会破坏"只增量"原则）；
- 研究侧（`research.*`）任何改动；
- 真实资金切换本身（属生产切换窗独立 Go/No-Go）。

## Dependencies & Risks

| 项 | 说明 | 处置 |
|---|---|---|
| 外部前置 | QMT 客户端 + 模拟账号 + xtquant | 未就位则 AC-004/AC-005 无法完成，**必须显式登记豁免**而非默认通过 |
| 物理阻塞 | xtquant 未装 → C1 WARN | 截止日与 `DECISION NEEDED` 行一致（不在此复写日期） |
| 假成功风险 | "代码验证 12/12 PASS" 被误当作"T15 已完成" | 本 spec §现状锚点已显式区分**代码正确性**与**真实终端验证**；AC-004 独立于 AC-003 |
| 文档漂移 | 切换指引指向已删除路径 | 由 FR-007 / AC-010 收口 |
| 时间窗 | Sprint 2 启动 → Stage B 功能冻结窗（节点见 ROADMAP） | 若外部前置延迟，须提前登记豁免，不得挤占冻结窗前窗口 |
