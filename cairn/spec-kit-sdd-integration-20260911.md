# spec-kit（SDD）× 28 仓治理集成方案（2026-09-11）

> **适用**：把 `github/spec-kit`（本仓主 fork：`yuppiez99999/spec-kit`，MIT）的规格驱动开发（SDD）工作流接入 v8.7 仓库，且**不破坏** ROADMAP 单一事实源、四层门禁与三端同步器。
> **性质**：流程/工具链层改造。spec-kit **不进入** `requirements*.txt` 运行时依赖树——不违反「2027 年前不引入新生产依赖」长期决策（该决策禁的是生产依赖，流程工具属开发工装，且用的是自有 fork）。
> **棕地铁律**：只对新需求启用 SDD，存量 8 万文件不回溯补 spec。

---

## 0. 决策摘要

| 项 | 结论 |
|---|---|
| 定位 | spec-kit = 需求→规格→任务→收敛的**前端工作流**；ROADMAP 仍是任务注册与发布控制的**权威后端** |
| 事实源 | `specs/` 只做"单个需求的详细规格"，数值口径一律引用 `cairn/ROADMAP.md` CURRENT STATE，**禁止复制** |
| 产物落位 | `specs/<ROADMAP-TaskID>-<slug>/`（git 跟踪，单特性单目录） |
| 宪法策略 | constitution 薄索引化——指向 `AGENTS.md`/`CLAUDE.md`/`cairn/`，不重写既有纪律 |
| 门禁融合 | `/speckit.converge` 的完成判据 = 现有四门禁全绿（ruff 增量 + pytest + industrial_grade_check + assert_data_validity） |
| 代理挂载 | Claude Code 走 `.claude/commands/speckit.*.md`；CodeBuddy 走 `.codebuddy/skills/speckit-*/SKILL.md`；共享同一 `specs/` |
| 试点 | ROADMAP 内 1 个 Q4 任务，2 个 sprint 评估期 |
| 回滚 | Kill Criteria 触发即降级为"纯模板库"或归档（见 §8） |

---

## 1. 目标与非目标

**目标**
1. 把「完成声明≠完成」纪律标准化成可机读产物（spec/tasks 在代理间可交接）。
2. 新需求先写验收判据（= 门禁名 + 期望值 + 复现命令），再写代码——结构性掐死「缺数据/空集合 = 通过」的假 PASS。
3. 双代理（Claude Code / CodeBuddy / 云端 NPC）对同一需求消费同一份 spec，减少口径漂移。

**非目标**
- 不替代 ROADMAP / cairn/LOG.md 的治理职能。
- 不对存量代码做全量 specify（棕地只增量）。
- 不引入任何运行时依赖、不新建数据库/服务。

---

## 2. 架构与数据流

```
cairn/ROADMAP.md ───(Release → Stream → Gate → Task, TaskID)──┐
   CURRENT STATE yaml（资金/目标/窗口唯一权威）                  │
                                                              ▼
                        specs/<TaskID>-<slug>/{spec.md, plan.md, tasks.md}
                                          │ 验收判据: 门禁名+期望值+复现命令
                                          ▼
                        /speckit.implement ──小步──> /speckit.converge
                                          │
                                          ▼
                 scripts/speckit_converge_gate.py（薄包装, §5）
                   ├─ ruff_incremental_gate.py（改动文件列表）
                   ├─ pytest（tasks.md 声明的测试范围）
                   ├─ industrial_grade_check.py
                   └─ assert_data_validity.py
                                          │ 全绿
                                          ▼
                 ROADMAP 任务状态更新 + cairn/LOG.md append + 三端同步器
```

**关键不变量**：箭头方向单向。spec 消费 ROADMAP 的口径，绝不反向——ROADMAP 永不从 spec 读数值。

---

## 3. 目录与产物契约

### 3.1 新增目录

```
specs/                          # 顶层, git 跟踪, 单特性单目录
  <TaskID>-<slug>/
    spec.md                     # 需求 + 用户故事 + 验收判据
    plan.md                     # 技术计划（引用真实模块路径）
    tasks.md                    # 任务清单（复选框 + 每项验收判据）
    implementation-notes.md     # converge 产出（门禁输出摘录）
.speckit/                       # specify CLI 的模板定制（presets 本地覆盖）
```

命名示例：`specs/G1-qmt-live-order-wiring/`。TaskID 前缀保证 `sync_upgrade_status.py` 风格的扫描可交叉核对（未来可扩展）。

### 3.2 产物契约（模板必填字段）

`spec.md` 必含：
- `roadmap_ref:` 指向 ROADMAP 的 Task/Stream（**无 TaskID 的 spec 不合规范**）
- 验收判据表：每条 = {门禁名或测试名, 期望值, 复现命令}——即「打勾三要素」的机读版
- 风险与回滚触发条件

`tasks.md` 必含：
- 每个任务项标注类型：`feat` / `fix` / `refactor`
- **`fix` 类任务强制附「修复前会失败」的回归测试路径**（对齐 DoD 铁律）
- 完成打勾必须带：文件+日期 / 实证值 / 复现命令

### 3.3 不落进 specs/ 的东西

资金口径、绩效目标、发布窗口、版本号——全部只存在于 ROADMAP。spec 里出现这些数值即违规（评审时用 `grep -nE '(200|300|0000)' specs/` 粗筛，人工确认是否为引用）。

---

## 4. 宪法（constitution）薄索引策略

**不生成大而全的 constitution.md 重写既有纪律**（AGENTS.md / CLAUDE.md / CODEBUDDY.md 已是事实上的 constitution，重复维护必然漂移）。`/speckit.constitution` 产出的 `.speckit/constitution.md` 只保留两类内容：

1. **指针区**（显式引用，不复制）：
   - 开发纪律 → `AGENTS.md`、`CLAUDE.md`
   - 资金/风控口径 → `cairn/ROADMAP.md` §CURRENT STATE（唯一权威）
   - 合并/门禁流程 → `cairn/merge-and-gate-playbook-20260911.md`
   - 完成定义（DoD）→ `docs/CODE_REVIEW_PROCESS.md` §4.2
2. **SDD 特有增量**（现有文档没有的）：
   - 规格先于代码；无 spec 不动主链路代码
   - spec 引用而非复制口径数值
   - fix 任务必须带"修复前会失败"回归测试
   - converge 未全绿不得更新 ROADMAP 任务状态为 done

---

## 5. 门禁融合（本方案的核心差异化）

### 5.1 新增 `scripts/speckit_converge_gate.py`（薄包装，fail-closed）

按序执行，任一失败即 exit 1 并要求把失败输出贴进 `implementation-notes.md`：

| 序 | 门禁 | 入参 |
|---|---|---|
| 1 | `scripts/ruff_incremental_gate.py` | 本次改动的 .py 文件列表（CLI 传参，同 pre-commit 用法） |
| 2 | `pytest` | tasks.md 声明的测试范围（默认 `tests/unit/ -k <spec 相关>`） |
| 3 | `scripts/industrial_grade_check.py` | 无参（全量检查） |
| 4 | `scripts/assert_data_validity.py` | 无参（数据非零断言） |

实现要求（对齐既有工程坑位教训）：
- 顶部显式 `sys.path.insert(0, PROJECT_ROOT)`（cron/CLI 入口坑）
- Windows 控制台输出预置 `PYTHONIOENCODING=utf-8`，禁 `¥` 等非 GBK 字符（用 RMB/CNY）
- 不写任何生产目录；只读 `reports/` 产物 + stdout
- 门禁自身同标准审：脚本须自证「缺产物 = FAIL」而非 skip（fail-closed，防空转）

### 5.2 命令模板改造

从 fork 中移植 `/speckit.implement` 与 `/speckit.converge` 模板时，在"完成"一节硬编码：
> 完成的唯一定义 = `python scripts/speckit_converge_gate.py <files...>` exit 0，且输出已摘录进 `implementation-notes.md`。禁止在门禁未跑的情况下声明任务完成。

---

## 6. 三代理挂载

> **2026-09-11 试用修正**：spec-kit **原生支持 CodeBuddy 集成**（`specify init --integration codebuddy`，本机无 CodeBuddy CLI 二进制时须加 `--ignore-agent-tools`），产物即 `.codebuddy/commands/speckit.*.md` **10 个命令文件**（含 SHA256 manifest），无需手写 SKILL.md 转换。下表已按实测修正。

| 代理 | 位置 | 形式 |
|---|---|---|
| CodeBuddy | `.codebuddy/commands/speckit.{specify,plan,tasks,implement,converge,clarify,analyze,checklist,taskstoissues}.md` | **原生集成**，`specify init --integration codebuddy --script ps --ignore-agent-tools` 直出，微调 §5.2 |
| Claude Code | `.claude/commands/speckit.*.md` | `--integration claude` 同源产物（本机 Claude Code 可用） |
| 云端 NPC（cnb 线） | 同 Claude Code 布局 | 三端同步器分发；NPC 只读 spec 产出实现，**不创建 spec**（spec 生成权留在本端，防并发冲突） |

**spec 生成权单点**：只有本端（Claude Code/CodeBuddy 交互会话）允许写 `spec.md`/`plan.md`；云端 NPC 与三端同步器对这两个文件只读。避免同步冲突与双头编辑。

**门禁接线的官方扩展点（试用发现）**：spec-kit 内置 `.specify/extensions.yml` 钩子机制，`hooks.before_converge` 可注册**强制钩子**（`optional: false` 时代理必须真实执行并等待结果才继续 converge）。`speckit_converge_gate.py` 应注册为强制钩子而非只写在命令模板里——模板可能被忽略，钩子是命令文件里硬编码检查的（converge 命令 L17-49 逐一核查 extensions.yml 并要求真实调用）。

---

## 7. 与三端同步器 / git 共存

- **同步器冲突规则扩展**（`scripts/sync_cnb_to_github.py`，当前白名单仅 `cairn/LOG.md` 取并集）：
  - `specs/**/tasks.md` 复选框冲突 → **取并集**（勾选状态 OR；append-only 语义同 LOG.md）
  - `spec.md` / `plan.md` 冲突 → **不自动解决**，走人工裁决（fail-closed，退出码 1 挂起）
- 提交遵循既有惯例：`git add specs/<具体文件>`，防夹带；提交前对 `.py` 做 `\r\n→\n` 规范化（同 daily_trade_executor 教训）。
- pre-commit 三道门对 `.md` 无 DTZ005/mypy 影响；新增 `scripts/speckit_converge_gate.py` 会进 ruff 增量门范围（正常纳管，不是豁免项）。
- 仓库自动提交在跑 → **安装/试用阶段不直接在 28 仓 `specify init --force`**，先在 scratch 目录试（§9.1）。

---

## 8. 试点计划与 Kill Criteria

### 试点（2 个 sprint 评估期）

1. **选样**：从 ROADMAP `§CURRENT QUARTER` 挑 1 个未开工的 Q4 任务（候选：G1 QMT 真实下单接线 Phase 4 前置演练 / ETF 期权子组合影子接入新步骤），建 `specs/<TaskID>-<slug>/`。
2. **全流程**：specify → plan → tasks → implement → converge（每个环节产物落盘）。
3. **对照组**：同期不经过 SDD 的常规任务照旧流程走，2 sprint 后对比：返工次数 / 门禁拦截率 / "完成声明≠完成"事故数。

### Kill Criteria（触发即降级，对齐 release-governance 治理）

| 触发条件 | 动作 |
|---|---|
| spec 与 ROADMAP 出现数值/口径冲突 ≥2 次 | 移除 specs/ 目录，SDD 降级为"纯模板用途"（模板写 cairn 文档） |
| 连续 2 个 spec 的 converge 产物未被实际消费（门禁没跑 / tasks 没核销） | 归档整个接入，cairn/LOG.md 记教训 |
| 2 sprint 无可度量改善（返工/拦截率持平） | 归档，回退现有 ROADMAP+cairn 直驱流程 |
| 维护成本感知超载（同步冲突 > 每周 2 次） | 收窄为仅本端使用，NPC 线摘除 |

---

## 9. 实施清单（按序执行）

### 9.1 安装与隔离试用（不改 28 仓）——✅ 2026-09-11 已执行，结论如下

```powershell
# uv 隔离安装（不污染 .venv）——已执行成功
uv tool install specify-cli --from git+https://github.com/yuppiez99999/spec-kit.git
# → specify-cli 1.0.7.dev0 (fork HEAD c173bf1), 16 依赖, 隔离工具环境

# scratch 试用——已执行成功（28 仓零触碰）
specify init _speckit_trial --integration codebuddy --script ps --non-interactive --ignore-agent-tools
# 注意: CodeBuddy 无 CLI 二进制（IDE 型）→ 必须 --ignore-agent-tools; 走 127.0.0.1:7897 代理装包
```

**产物审阅结论（`e:\各种PY程序\_speckit_trial\`）**：

| 审阅项 | 结论 |
|---|---|
| CodeBuddy 集成 | **原生**：`.codebuddy/commands/` 10 个命令 md + SHA256 manifest（`codebuddy.manifest.json`）。无需手写 SKILL.md，§6 已修正 |
| Windows 支持 | **原生 PowerShell**：`.specify/scripts/powershell/` 6 个 ps1（create-new-feature / setup-plan / setup-tasks 等），`--script ps` 生效，无 bash 依赖 |
| 模板可定制性 | **完全可改**：spec/plan/tasks/constitution/checklist 5 个模板全是纯 markdown 占位符，可按 §3.2/§4 契约直接改写（加 roadmap_ref、验收判据表、打勾三要素） |
| converge 命令质量 | **与本仓治理哲学高度对齐**：① spec/plan/tasks 是"唯一意图源"；② **APPEND-ONLY**——只允许向 tasks.md 追加 Convergence 段，禁改 spec/plan、禁重排任务（同 LOG.md 铁律）；③ 全满足时 tasks.md **字节级不动**并报干净（防空转污染）；④ 明确"不是 diff 工具，不碰 git 历史" |
| 门禁扩展点 | **发现官方钩子机制**：`.specify/extensions.yml` 的 `hooks.before_converge` 支持强制钩子（`optional: false`），converge 命令文件内硬编码逐项核查并要求代理真实执行——`speckit_converge_gate.py` 应走此机制（§5/§6 已更新） |
| 净余量 | 脚手架合计 ~29 文件 / ~200KB，全在 `.codebuddy/commands/` + `.specify/`，**与 28 仓现有目录零冲突**；`.specify/.gitignore` 已含（可按需调整跟踪范围） |

**审阅判定：产物结构与本方案假设兼容，无阻塞性缺陷，可进入 §9.2 移植阶段。**

### 9.2 移植与定制（改动全在 28 仓新增文件，零触碰存量）——✅ 2026-09-11 已执行

| # | 交付物 | 落点 | 状态 |
|---|---|---|---|
| 1 | 脚手架（10 命令 + 20 文件） | `.codebuddy/commands/`、`.specify/`（含 ps1 脚本、manifest、workflows） | ✅ 复制自 `_speckit_trial` |
| 2 | Claude 形态 | `.claude/skills/speckit-{specify,plan,tasks,implement,converge,clarify,analyze,checklist,constitution,taskstoissues}/SKILL.md` | ✅ 复制自 `_speckit_trial_claude` |
| 3 | 模板按 §3.2/§4 契约改写 | `.specify/templates/{spec,plan,tasks}-template.md` + `constitution-template.md`（与 `memory/constitution.md` 同步） | ✅ Roadmap Ref 必填 / 验收判据表 / 口径引用节 / 28仓 Task Conventions / 真实路径约定 |
| 4 | 宪法薄索引 | `.specify/memory/constitution.md` | ✅ 指针区（AGENTS/CLAUDE/ROADMAP/playbook/DoD）+ 8 条 SDD 增量原则 |
| 5 | 门禁薄包装 | `scripts/speckit_converge_gate.py` | ✅ fail-closed，`--pytest-args` 必填 |
| 6 | 门禁单测 | `tests/unit/test_speckit_converge_gate.py`（6 用例） | ✅ 26 passed（与同步器单测同轮） |
| 7 | 钩子注册（门禁接线官方扩展点） | `.specify/extensions.yml`：`hooks.after_implement` + `hooks.before_converge` 各 1 个 `optional: false` 钩子 → `speckit.gate` | ✅ 命令文件已原生硬编码核查该文件并要求真实执行 |
| 8 | 双代理 gate 命令 | `.codebuddy/commands/speckit.gate.md`、`.claude/skills/speckit-gate/SKILL.md` | ✅ 收集改动 .py + tasks.md 测试范围 → 跑脚本 → 报 PASS/FAIL，禁软化 |
| 9 | 同步器白名单扩展 | `scripts/sync_cnb_to_github.py`：`_SPECS_TASKS_RE` + `_is_union_allowed()` + `_union_checkboxes()`（勾选 OR） | ✅ 非 tasks.md 的 specs 冲突仍 fail-closed 挂起 |
| 10 | 同步器单测增例 | `tests/unit/test_sync_cnb_to_github_unit.py::TestSpecsTasksUnion`（3 用例） | ✅ 端到端临时仓真跑 git |
| 11 | specs 目录规范 + 试点骨架 | `specs/README.md`、`specs/G1-qmt-live-order-wiring/spec.md` | ✅ 骨架待 `/speckit.specify` 填充 |
| 12 | gitignore 精确例外 | `.gitignore`：`.codebuddy/*` + `!.codebuddy/commands/`；`.claude/*` + `!.claude/skills/` + `.claude/skills/*` + `!.claude/skills/speckit-*/` | ✅ 已实测：spec 产物放行，`.codebuddy/memory`、`.claude/settings.json` 等仍忽略 |

**实测偏差（与计划的差异，均为正向）**：① 三端挂载形态由"手写 SKILL.md"改为**原生产物**（§6 已修正）；② 门禁接线由"改命令模板"升级为 **`extensions.yml` 强制钩子**——命令文件在 Pre-Execution / Mandatory Post-Execution 两处**硬编码**核查该文件，`optional: false` 时要求代理真实执行并等待结果，模板改写不再是必要条件（故未改 vendor 命令文件，保持可随 CLI 刷新）。

**验证记录（2026-09-11，`.venv/Scripts/python.exe`）**：

```
[正向] speckit_converge_gate.py --files <4 文件> --pytest-args "tests/unit/test_speckit_converge_gate.py tests/unit/test_sync_cnb_to_github_unit.py -q"
  → G1 PASS (5 文件, 无新增违规) / G2 PASS 26 passed / G3 PASS / G4 PASS  → RC=0
[负向] --pytest-args "tests/unit/test_zzz_not_exist_xyz.py -q"
  → G1 PASS → G2 FAIL(exit 4, "no tests ran / file not found") → 短路 RC=1，提示摘录 implementation-notes.md
```

### 9.3 验收（本方案自身的 DoD）

- [x] `_speckit_trial` 产物审阅结论落 `cairn/LOG.md`（2026-09-11）
- [~] 命令双代理挂载后，`/speckit.specify` 在两代理各自能产出符合 §3.2 契约的 spec.md —— **流程已跑通**（09-11 按契约产出 §9.4 试点四件套）；**待用户侧在两代理界面各实跑一次做最终确认**
- [x] `speckit_converge_gate.py` 负向验证：CI 同格式实证 → G2 空集合（no tests ran，exit 4）被拦、RC=1 短路（2026-09-11）
- [x] 同步器单测：构造 tasks.md 复选框冲突 → 合并后取并集（`TestSpecsTasksUnion` 3 用例，端到端真跑 git）
- [~] 试点 spec 走完全流程，converge 四门禁全绿输出落 `implementation-notes.md` —— **spec/plan/quickstart/tasks 已产出（09-11）；implement 阶段待做**
- [ ] ROADMAP `§CURRENT QUARTER` 增补一行 SDD 试点登记（TaskID + specs 路径指针）——**刻意后置**：ROADMAP 是口径事实源；试点产物已就位，待 implement 跑通后一次写入（避免与在途改动相交）

---

### 9.4 试点产出（2026-09-11，`specs/G1-qmt-live-order-wiring/`）

| 文件 | 大小 | 内容要点 |
|---|---|---|
| `spec.md` | 13.1 KB | **现状锚点实证表**（8 项，含 xtquant 未装/终端段未验证的实测证据链）+ 5 个用户场景 + 10 条 FR + **11 条 AC 判据表**（每条含期望值与复现命令）+ 口径引用表（只引用条目名，零数值） |
| `plan.md` | 8.8 KB | 技术上下文（真实路径/依赖/约束）+ **逐条 Constitution Check**（8 条全过）+ 复杂度登记（新增第 2 个验证入口的理由 + 被否决的更简方案）+ AC 映射的 5 阶段 |
| `quickstart.md` | 6.4 KB | 真机操作手册：前置清单 / 根 `system_config.json` 配置 / **实测环境变量两张表**（服务端 8 个 + 客户端 3 个）/ 启动与验证 / 失败处置 / 回滚 |
| `tasks.md` | 6.0 KB | 20 个任务（`[test]`/`[feat]`/`[fix]`/`[docs]` 类型标注）+ **`**测试范围**` 声明行**（= 门禁 G2 入参）+ 先红后绿证据要求 + 依赖顺序 |

**试点过程中对 spec 自身的三次修正（体现契约自洽）**：
1. 按**宪法第 2 条**（spec 不得含资金/绩效/窗口数值）自审 → 删去 3 处日期数值（冻结日/决策截止日/Sprint 启动日），改为纯条目引用；
2. **推翻自己的一个误判**：曾判 `verify_qmt_sim_chain.py`「端口 8765 vs 18765 漂移」→ 复核代码发现 8765 是网关默认（`QMT_RPC_PORT`）、18765 是沙箱刻意避开 → **漂移不成立**，spec 已更正（真实漂移仅 `config/system_config.json` 失效路径，L15/L239 共 2 处）；
3. 记录**先红证据**：`Select-String -Pattern 'config/system_config'` 命中 **2** → 作为 T008 的"修复前会失败"基线。

**显式跳过（非遗漏）**：`research.md`（无算法/模型研究）、`data-model.md`（无新数据实体，沿用既有 Order/Fill/Position 契约）——已在 `plan.md` 声明理由与补写条件。

**下一步**：`/speckit.tasks` 之后的 implement 阶段（Phase 1-3 可离线；Phase 4 需 QMT 客户端 + 模拟账号 + xtquant 或登记豁免）。

---

## 10. 风险登记表

| 风险 | 缓解 |
|---|---|
| 双头治理（specs/ 与 ROADMAP 各说各话） | §2 单向数据流 + §3.3 禁复制数值 + 评审 grep 粗筛 |
| 云端 NPC 并发写 spec | §6 spec 生成权单点在本端 |
| 自动提交撞上移植中的半成品 | 9.2 全部是新增文件，分批 add；不 force init |
| specify CLI Windows 编码 | PYTHONIOENCODING=utf-8；含 `$` 的 PowerShell 命令写 .ps1 再跑（本机惯例） |
| 模板漂移（fork 与上游 spec-kit） | fork 冻结用，不追上游；升级走显式 rebase + 9.3 重新验收 |
| 三端同步冲突面扩大 | tasks.md 并集规则限定复选框行；其余 fail-closed 挂起人工裁决 |

---

*首次成文：2026-09-11。评估期结束后在本文追加「试点结论」一节（保留 / 降级 / 归档 三选一），并同步 ROADMAP。*
