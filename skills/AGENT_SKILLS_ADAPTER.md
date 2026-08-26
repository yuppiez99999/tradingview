# Agent-Skills 量化系统适配层 (AGENT_SKILLS_ADAPTER)

> 本文件把 `~/.codebuddy/skills/agent-skills/`（Addy Osmani 的 24 个通用工程 skill）
> 桥接到本量化交易系统的**既有门禁脚本**与**领域铁律**。
> 通用 skill 是只读参考，不要改写它们；本文件是项目级适配真相源。

## 0. 核心原则

- **门禁脚本是事实源，不是建议**：通用 skill 说"run tests / run lint"，在本系统
  必须调用下面的具体脚本，并以其退出码为准。
- **失败友好 (fail-close / fail-open) 铁律**：决策路径（下单、建仓、风控）必须
  fail-close 阻断；观测路径（告警、报告、归因）必须 fail-open 降级。任何"静默
  吞咽 except"都是缺陷。
- **执行闭环铁律**：信号→组合→执行→成交回报→归因 全链路可追溯。只生成不撮合、
  只撮合不落盘 都是断链（见 memory ID 20413815 / 33885711）。
- **研究/生产隔离**：`utils/`、`ms_strategy/` 等生产模块禁止 `import research.*`
  （engineering_debt_gate T4）。
- **无前视偏差 / 无幸存者偏差**：回测用实际披露日、逐日成分股快照、复权标准化
  （memory ID 39589741）。

## 1. 门禁命令速查表（替代 skill 里的抽象"跑测试"）

所有命令在 `28-终极量化交易系统8.4/` 根目录执行。Windows 加 `PYTHONUTF8=1`
或 `py -X utf8` 绕过 GBK 编码坑（memory ID 56602418）。

| 通用 skill 要求 | 本系统实际命令 | 退出码 |
|---|---|---|
| run tests | `pytest tests/ -q --co` (仅收集，4888 tests 0 errors) | 0=OK |
| run tests (执行) | `pytest tests/ -q` | 0=OK |
| lint / format | `python scripts/ruff_incremental_gate.py <改动文件...>` | 1=拦截 |
| type check | `mypy --config-file mypy.ini` (需 `PYTHONUTF8=1`) | — |
| security audit | `bandit -r realtime_monitor/ -c bandit.yaml` | 0=OK |
| full quality gate | `python scripts/engineering_debt_gate.py` | 0/1/2 = GREEN/YELLOW/RED |
| data integrity gate | `python scripts/assert_data_validity.py` | 0=OK |
| release gate | `py -X utf8 scripts/v87_release_gate.py --all` | — |
| pre-commit | `git config core.hooksPath githooks` 或 `python scripts/pre_commit_check.py` | — |

**改动任何 P0 文件后必跑**：`ruff_incremental_gate.py` + `engineering_debt_gate.py`
+ `assert_data_validity.py`（门禁三件套，memory ID 55922757）。

## 2. 24 个 Skill → 量化适配映射

### Define（定义）
- `interview-me`：盘前问清"交易什么/为何现在/风控预算多少"；不要跳过信号的经济直觉。
- `idea-refine`：每个新因子/策略必须先有经济或行为金融解释（数据挖掘因子直接拒，
  memory ID 52796540 Stage 1）。
- `spec-driven-development`：新模块 spec 必须写明数据链路（来源→清洗→特征→服务）、
  失败模式、是否进主实盘链路。

### Plan（规划）
- `planning-and-task-breakdown`：任务拆解必须标注是否触碰**执行闭环**或**门禁三件套**；
  触碰者优先并配回归测试。

### Build（构建）
- `incremental-implementation`：先跑门禁三件套再宣布完成；绝不在生产路径留 `print()`
  （P0 文件 T201 门禁，紧急豁免行尾 `# allow-print`）。
- `test-driven-development`：回测/执行类改动必须附"回滚必红/修复必绿"的回归测试
  （memory ID 87513358 DoD）。
- `context-engineering`：高频路径禁止把 `research.*` 拉进上下文；生产只引生产模块。
- `source-driven-development`：查证 `automated_execution_system.py` /
  `institutional_pipeline_runner.py` 的真实调用链，勿信过时诊断报告
  （memory ID 24715792：诊断报告可能过时，须重跑代码验证）。
- `doubt-driven-development`：对是否实盘、是否裸下单保持最高怀疑；QMT 真实下单仍
  `dry_run=true` 直到 Phase 4（memory ID 23032726）。
- `frontend-ui-engineering`：仅限 `ui/` Streamlit 面板；暗色主题 `.streamlet/config.toml`
  已固定，勿改 base 色板。
- `api-and-interface-design`：内部模块用 JSON 契约（如 `hedge_execution_fill_{date}.json`
  兼容 `daily_pnl` 数据契约）；契约字段必须上下游完全对齐。

### Verify（验证）
- `browser-testing-with-devtools`：仅 UI 调试用；默认不跑（规则：仅用户显式要求时）。
- `debugging-and-error-recovery`：先区分"代码缺失 vs 运行时问题"；EOD 5 步核查法
  （列出应产出文件→逐一验证→追溯上游→区分缺失/运行→重跑验证，memory ID 24715792）。

### Review（审查）
- `code-review-and-quality`：用**五轴审查**但每条结论须由门禁脚本佐证；重点查
  ① 静默 fail-safe（应改独立 send_alert 通道，memory ID 52548113 B4）
  ② 执行断链 ③ 前视偏差 ④ 裸 `print`(P0)。
- `code-simplification`：简化不得破坏执行闭环或落盘契约；删 dead code 前先跑
  `archive_dead_code.py` 确认无悬挂引用（memory ID 55922757 check_dangling_refs）。
- `security-and-hardening`：密钥走环境变量（无硬编码 `WIND_API_KEY`/`IFIND_TOKEN`）；
  `realtime_monitor` 的 SSL 用 `certifi.where()` 非 `verify=False`（memory ID 52548113 B2/B3）。
- `performance-optimization`：滑点分层（大盘 2-5bp / 小盘 10-30bp，memory ID 43466822）；
  OpenBLAS 内存不足设 `OPENBLAS_NUM_THREADS=1`（memory ID 87658369）。

### Ship（发布）
- `git-workflow-and-versioning`：commit message 带 `[Ux]/[Gx]/[Px]` 标记供
  `sync_upgrade_status.py` 扫描（memory ID 55922757）。
- `ci-cd-and-automation`：CI 用 `ci.yml` 的 `incremental-static` job；PR 未过不得合并。
- `deprecation-and-migration`：下线因子/模块走退役标准（连续 6 月 ICIR<0.2 等，
  memory ID 58293104），先 `check_dangling_refs` 再删。
- `documentation-and-adrs`：架构决策写入 `cairn/<topic>.md`；AGENTS.md ≤ 60 行。
- `observability-and-instrumentation`：关键路径用 `utils/notify` 五秒告警；
  `send_alert(content=...)` 参数名是 `content` 非 `message`（memory ID 55501465 G8）。
- `shipping-and-launch`：见 §3 量化发布门禁（替代通用 Web 发布清单）。
- `using-agent-skills`：路由层，无系统特定改造。

## 3. 量化专属「完成定义」(DoD) — 替代 references/definition-of-done.md

任何改动在声明完成前，除通用 DoD（正确/质量/集成/文档）外，**必须**满足：

### 执行闭环（最高优先）
- [ ] 若改动涉及订单：信号→订单→撮合→成交回报→归因 链路完整，无"只生成不撮合"
      或"只撮合不落盘"断链。
- [ ] 新增成交必须落盘为可追溯事实源（JSONL/fills_store），并被下游 PnL/TCA 消费。

### 门禁三件套（必跑，全绿或仅已知 WARN）
- [ ] `ruff_incremental_gate.py <改动文件>` 无新增阻断
- [ ] `engineering_debt_gate.py` 退出码 ≤ 1（GREEN/YELLOW）
- [ ] `assert_data_validity.py` 全部断言 PASS（无 D1/D2 类 actual_pnl=0、observed=0）

### 失败友好
- [ ] 决策路径 fail-close；观测路径 fail-open；无静默 `except` 吞错。
- [ ] 告警通过 `utils/notify.send_alert` 独立通道，非 `logger.warning` 伪装。

### 数据与偏差
- [ ] 回测无前视/幸存者偏差（实际披露日、复权、逐日快照）。
- [ ] 数据流每个环节有降级路径，永不因数据源不可用而崩溃。

### Windows / 编码护栏
- [ ] 不输出 `¥` 等 GBK 不可编码字符（用 RMB/CNY 或 `PYTHONUTF8=1`）。
- [ ] 不裸 `python`（用 `.venv/Scripts/python.exe` 避免 site-packages GBK 坑）。

### 实盘纪律（若触及主链路）
- [ ] 真实下单保持 `dry_run=true` 直到 Phase 4 灰度；绝不裸实盘。
- [ ] 改动经影子账户跟踪 ≥ 2 周，绩效偏离回测 > 30% 则拒绝上线。

## 4. 反理性化补充（叠加通用 skill 的 Red Flags）

| 理性化 | 现实 |
|---|---|
| "TCA 日志有记录 = 成交已执行" | dry-run 下 fills JSON 不落盘、positions 不更新（memory ID 24715792 #2） |
| "诊断报告说缺执行器" | 下午可能已创建该脚本，须重跑代码验证当前状态 |
| "门禁看起来接好了" | 须用与 CI 同格式做负向测试，否则"真在拦"存疑（memory ID 45433063） |
| "先上生产再补监控" | 观测路径 fail-open 降级，但上线前必须有告警通道 |
| "小改动不用跑门禁" | 增量门禁不扫存量；每次改动文件都过 `ruff_incremental_gate` |

---
本适配层由 agent-skills 安装（2026-08-24）时生成，随门禁脚本演进同步更新。
