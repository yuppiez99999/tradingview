# Implementation Notes: G1 QMT 真实下单接线

**日期**: 2026-09-11 | **Spec**: [`spec.md`](./spec.md) | **Plan**: [`plan.md`](./plan.md) | **Tasks**: [`tasks.md`](./tasks.md)

**范围**: 本次交付 = `tasks.md` **Phase 1–3**（可离线部分）+ Phase 5 留痕。
**Phase 4（真机执行）未做** —— 外部前置（QMT 客户端 + 模拟账号 + `xtquant`）未就位，相关 AC **登记豁免**（见 §3），**未默认打勾**。

---

## 1. 逐条 AC 实证（打勾三要素：文件 + 日期 + 实证值 + 复现命令）

| AC | 结论 | 实证值 | 复现命令 |
|---|---|---|---|
| AC-001 四门禁收敛 | ✅ **达成** | `ALL PASS —— converge 收敛成立 (exit 0)`；G1 `4 文件, 无新增违规` / G2 `32 passed` / G3 PASS / G4 PASS | `scripts/speckit_converge_gate.py --files scripts/verify_qmt_paper_chain.py scripts/verify_qmt_sim_chain.py tests/unit/test_qmt_paper_chain_gate.py --pytest-args "tests/unit/test_qmt_paper_chain_gate.py tests/unit/test_p15_live_broker_gate_20260910.py"` |
| AC-002 `xtquant` 可导入 | ⏳ **豁免** | `NOT INSTALLED`（真机前置） | `.venv/Scripts/python.exe -c "import xtquant"` |
| AC-003 代码正确性基线 | ✅ **达成** | `12/12 通过 — 执行链路验证 PASS`，`RC=0` | `.venv/Scripts/python.exe scripts/verify_qmt_sim_chain.py` |
| AC-004 真实终端段验证 | ⏳ **豁免**（入口已交付） | 入口交付 + 前置未满足时 `RC=2` 并列出 4 条原因（**非静默通过**）；真机全项 PASS 待 Phase 4 | `.venv/Scripts/python.exe scripts/verify_qmt_paper_chain.py --preflight-only` |
| AC-005 C1 WARN→PASS | ⏳ **豁免** | 依赖 AC-002/AC-004 | `.venv/Scripts/python.exe scripts/industrial_grade_check.py` |
| AC-006 门控矩阵回归 | ✅ **达成** | `test_p15_live_broker_gate_20260910.py` 全绿（含既有"修复前会失败"用例） | `.venv/Scripts/python.exe -m pytest tests/unit/test_p15_live_broker_gate_20260910.py -q` |
| AC-007 防裸实盘 0 真实报单 | ✅ **达成** | `test_dry_run_zero_real_channel_calls_with_shadow_trace`：真实通道 `init/connect` 调用数 = **0**，装配 `SimulatedBroker`，且影子留痕（alert 含 `dry_run`） | `.venv/Scripts/python.exe -m pytest tests/unit/test_qmt_paper_chain_gate.py -q -k dry_run` |
| AC-008 断线不静默 | ✅ **达成** | 2 用例断言抛 `LiveBrokerUnavailableError` 且异常文本含 `fail-closed`；**绝不降级**模拟继续 | `.venv/Scripts/python.exe -m pytest tests/unit/test_qmt_paper_chain_gate.py -q -k unreachable` |
| AC-009 成交对账 | ⏳ **豁免** | 链内 T8 对账逻辑已实现（成交 vs 持仓）；真机 0 差异待 Phase 4 | `.venv/Scripts/python.exe scripts/run_trade_reconciliation.py` |
| AC-010 文档漂移修正 | ✅ **达成** | `Select-String 'config/system_config'` 命中 **2 → 0**；新增 2 条防复发回归；另修正 1 处**新发现**漂移（见 §2） | `Select-String -Path scripts/verify_qmt_sim_chain.py -Pattern 'config/system_config'`（空） |
| AC-011 无新增第三方依赖 | ✅ **达成** | 仅新增/修改文件，**未触碰任何依赖清单** | `git status --porcelain` 审查 |

## 2. 先红后绿证据

| 变更 | 先红（修复前会失败） | 后绿 |
|---|---|---|
| T001–T004 门控回归（新增） | 首轮 `4 failed, 26 passed` —— 4 个红色全部来自"验证入口未交付"（T004 本体），证明用例**真的在拦**而非空转 | `32 passed` |
| T008 文档漂移（`[fix]`） | `Select-String 'config/system_config'` 命中 **2**（L15/L239） | 命中 **0**；且仿真链仍 `12/12 PASS` |
| T008 附带：**DTZ005 门禁拦下提交**（"门禁在拦"的又一实证） | 提交被 pre-commit **拦下（RC=1）**：该文件 **3 处既有** `datetime.now()`（L76/L221/L251，**非本次引入**）→ DTZ005 按"**暂存整体**"校验，一次文本修复也无法单独提交 | 按项目惯例改 `datetime.now(CN_TZ)` + `from utils.datetime_utils import CN_TZ`；`ruff --select DTZ005` → `All checks passed!`；复跑 `12/12 PASS`。⚠️ 这**偏离了 T008"只改文本"约束**，已在 `tasks.md` 披露 |
| T009 防复发回归 | 与 T008 同源（上述 2 处命中即"会失败"的输入） | 新增 2 用例全绿 |
| 新发现：broker 注释漂移 | `QMT_ACCOUNT`/`QMT_PASSWORD` 全仓**代码读取 0 处**（grep 21 命中中 0 条为这两个名字）→ 按注释操作会配错变量 | 注释改为 `QMT_ACCOUNT_ID`/`QMT_PATH`/`QMT_SESSION_ID`/`QMT_RPC_TOKEN`（**仅改注释值，键名未动**） |
| 门禁自身负向验证 | G1 首跑 `FAIL`（`F841 all_pass` 未使用变量）→ **门禁确实在拦新增违规** | 修复后 `PASS 3 文件, 无新增违规` |
| **AC-002 假 PASS 陷阱**（Phase 4 勘查时发现） | 主 venv **Py3.14** 下 `import xtquant` **成功** ⇒ 原判据会判 `INSTALLED`（**假绿**），但 `xtdata`/`xttrader` 双双 `ImportError`；对照实验：`find_spec(xtquant)` → `True` | 判据改**能力级** → 前置自检 `RC=2` 并明示"Py≥3.14 假成功"；新增回归 `test_xtquant_check_rejects_namespace_only_install`（修复前该用例必红） |

## 3. 豁免登记（可机读口径）

```
豁免项: AC-004, AC-005, AC-009 (T013-T016)
原因:   QMT 客户端 (全机无 userdata_mini) + 模拟资金账号属真机外部前置, 当前离线环境不具备
影响:   T15 本体(真实终端段端到端)未取得实证; C1 判据仍为 WARN
未豁免: AC-004 的"前置未满足必须明确失败"部分已实现并实测(RC=2), 防"空集合=通过"
触发条件解除后: 执行 tasks.md Phase 4 (T013-T016), 并把报告路径回填本文件

AC-002 已由"豁免"升级为"硬架构约束"(2026-09-11 实测):
    状态: 前置换为"能力级可用 + 解释器 ≤3.13", 判据已落地并纳入防假绿回归
    证据: Py3.11(xtquant_env) 三者全 OK; Py3.14(主 venv) 顶层假成功 / 子模块全 FAIL
    约束: broker 侧代码须在 Python≤3.13 解释器中运行, 不得在项目主 venv(Py3.14)内直连 QMT
    未做: 不向 .venv 安装 xtquant —— 装上也只会制造假绿, 且属新增生产依赖
```

## 4. 两次自我推翻（诚实记录）

1. **端口漂移误判**（spec 阶段）：曾判 `verify_qmt_sim_chain.py` 的 `8765` 与 `18765` 不一致 → 复核代码后**不成立**（`8765` 是网关默认 `QMT_RPC_PORT`，`18765` 是沙箱刻意避开），已在 spec 更正。
2. **broker 双源误判**（本次）：`system_config.json` 出现两处 `"broker"`（L118/L283），一度疑为同名遮蔽（类比 `kill_switch` 事件）→ 实测 `api_config/broker`（作用域不同）与根 `/broker`，**无遮蔽**，假设不成立。

## 5. 交付物清单

| 文件 | 动作 |
|---|---|
| `scripts/verify_qmt_paper_chain.py` | **新增**（T006/T007）——真实终端段验证入口，含前置自检与 8 步链路 |
| `tests/unit/test_qmt_paper_chain_gate.py` | **新增**（T001–T004/T009）——9 用例门控与防复发回归 |
| `scripts/verify_qmt_sim_chain.py` | **修改**（T008）——仅 2 处文本：失效配置路径 + 补新入口与指引指针 |
| `system_config.json` | **修改**（T011）——仅 `broker.comment` 值 |
| `specs/G1-qmt-live-order-wiring/implementation-notes.md` | **新增**（本文件） |

## 6. 遗留 / 下一步

### Phase 4 进展（2026-09-11 推进记录）

| 任务 | 状态 | 说明 |
|---|---|---|
| T012 装 `xtquant` | ✅ 完成（结论与原假设不同） | 本机**已有** Py3.11 环境 `xtquant_env`（`xtquant 250807.1.2`）；主 venv Py3.14 **装也无用**（二进制仅 cp36–cp313）→ 判据改能力级，**不向 `.venv` 安装** |
| T013 跑 paper 链 | ⛔ **阻塞**（环境侧已通） | ✅ 已解：Py3.11 解释器**可导入**本仓 `utils.execution.broker_factory`（实测 `[OK]`）；⛔ 仍缺 **QMT 客户端 + 模拟资金账号** |
| T014 对账 | ⛔ 阻塞 | 依赖 T013 产生真实成交 |
| T015 C1 转 PASS | ⛔ 阻塞 | 依赖 T013/T014 |
| T016 影子取证 | ⛔ 阻塞 | 同上 |

**剩余阻塞项（全部为外部物理前置，非代码问题）**：

1. **QMT 客户端未安装** —— 全盘（C/D/E，depth≤4）无 `userdata_mini`；需券商渠道获取并**登录模拟账户**。
2. **模拟资金账号未配** —— `QMT_ACCOUNT_ID` 等环境变量全空。
3. （已解）~~Python 版本 / 依赖可见性~~ —— 见上表 T013 行。

**解除后执行**：`quickstart.md` §0 前置逐项核对 → §执行步骤 3（用 Py3.11 解释器）→ 4/5 步 → 报告路径回填本文 §1。

### 其他未做

- `cairn/ROADMAP.md` CURRENT QUARTER 登记（等 implement 全量跑通一次写入；C1 仍为 WARN，状态未变更故暂不写）。
- 三端同步推送（`scripts/sync_cnb_to_github.py`）—— 涉及 push 远端，待用户确认。
