# Agent-Skills 集成与适配（2026-08-24）

> 把 Addy Osmani 的通用工程 skill 库桥接到本量化系统。适配真相源 = `skills/AGENT_SKILLS_ADAPTER.md`。

## 0. 来源与安装

- 仓库：`https://github.com/yuppiez99999/agent-skills.git`（原版 `addyosmani/agent-skills` v0.6.7 标准镜像，fork 未私改）
- 本地克隆：`e:\各种PY程序\agent-skills\`（仅留作参考，不直接使用）
- 安装位置：`C:\Users\Administrator\.codebuddy\skills\agent-skills\`（24 skill + references + agents + docs）
- 适配层：`28-终极量化交易系统8.4/skills/AGENT_SKILLS_ADAPTER.md`（项目级，勿改通用 skill 本体）

## 1. 代码审查结论（安装前已审，安全可放心用）

- **安全 ✅**：全仓无硬编码密钥、无 `eval`/`base64 -d` 反混淆、无数据外传、无 `os.system` 裸执行。所有 `curl` 仅用于 WebFetch 缓存的 HTTP 304 校验。
- **Hook 安全 ✅**：`session-start.sh` 仅注入 meta-skill 文本；`sdd-cache-pre.sh` 带 ETag/Last-Modified 校验的 URL 缓存；`simplify-ignore.sh` 占位符临时隐藏样板块（会话结束还原）。三者均 fail-open 降级，用 `printf` 防 shell 注入。
- **质量 ✅**：24 skill 各为独立目录 + `SKILL.md`（`name`/`description`/`license` frontmatter）；`validate-skills.js`+`skill-lint.js` 可校验；维护活跃。
- **使用须知 ⚠**：
  - hook 仅适配 Claude Code/Codex/Cursor，CodeBuddy 不自动执行 hook（无害），须"手动调用 skill + 适配层"。
  - `CLAUDE.md`/`AGENTS.md`/`plugin.json` 是仓库自用文件，**勿复制进本项目**（已用独立 ADAPTER 文档替代）。
  - hook 依赖 `jq`/`shasum`（Windows 缺则自动降级跳过）。

## 2. 门禁命令速查（适配层 §1 核心，替代通用 skill 的抽象"跑测试"）

> 根目录执行；Windows 加 `PYTHONUTF8=1` 或 `py -X utf8` 绕过 GBK 坑。

| 通用 skill 要求 | 本系统命令 | 退出码 |
|---|---|---|
| run tests (收集) | `pytest tests/ -q --co` | 0=OK (4888 tests) |
| run tests | `pytest tests/ -q` | 0=OK |
| lint | `python scripts/ruff_incremental_gate.py <文件...>` | 1=拦截 |
| type | `mypy --config-file mypy.ini` (`PYTHONUTF8=1`) | — |
| security | `bandit -r realtime_monitor/ -c bandit.yaml` | 0=OK |
| 质量门禁 | `python scripts/engineering_debt_gate.py` | 0/1/2=G/Y/R |
| 数据门禁 | `python scripts/assert_data_validity.py` | 0=OK |
| 发布门禁 | `py -X utf8 scripts/v87_release_gate.py --all` | — |
| pre-commit | `git config core.hooksPath githooks` | — |

**改动 P0 文件后必跑门禁三件套**：`ruff_incremental_gate.py` + `engineering_debt_gate.py` + `assert_data_validity.py`。

## 3. 量化专属 DoD（适配层 §3，声明完成前必过）

1. **执行闭环**：信号→订单→撮合→成交回报→归因 完整，无"只生成不撮合"/"只撮合不落盘"断链；成交落盘为可追溯事实源被 PnL/TCA 消费。
2. **门禁三件套**：全绿或仅已知 WARN。
3. **失败友好**：决策路径 fail-close、观测路径 fail-open、无静默 except；告警走 `utils/notify.send_alert(content=...)` 独立通道。
4. **数据与偏差**：回测无前视/幸存者偏差（实际披露日、复权、逐日快照）。
5. **Windows/编码**：不输出 `¥`（用 RMB/CNY）；不裸 `python`（用 `.venv`）。
6. **实盘纪律**：真实下单保持 `dry_run=true` 直到 Phase 4 灰度；上线前须 shadow ≥2 周、偏离回测>30% 拒绝。

## 4. 反理性化补充（适配层 §4）

- "TCA 日志有记录 = 成交已执行" → dry-run 下 fills JSON 不落盘、positions 不更新。
- "诊断报告说缺执行器" → 须重跑代码验证当前状态（报告可能过时）。
- "门禁看起来接好了" → 须用与 CI 同格式做负向测试。
- "小改动不用跑门禁" → 增量门禁不扫存量，改动文件必过 `ruff_incremental_gate`。

## 5. 后续可扩展

- 为高频 skill（`code-review-and-quality`/`shipping-and-launch`/`test-driven-development`）写量化专属检查清单脚本。
- 把 `security-and-hardening` 的 OWASP LLM Top 10 映射到 `realtime_monitor` SSL 校验、密钥管理现状。
- 若需 hook 自动注入，可把 `session-start.sh` 思路移植到 CodeBuddy 的 automation/integration 机制（当前不需要）。

## 6. 执行闭环审查要点（2026-08-24 实战沉淀）

用 `code-review-and-quality` 审查执行闭环类模块（`hedge_order_executor`/`rebalance_order_executor`）发现的关键判断：
- **不能只看"有没有撮合+落盘"**，还要看**消费方是否只吃自己的订单**。FillsStore 是全局按日聚合的事实源，任何单一策略消费者（rebalance/hedge）都必须按 `rec["strategy"]` 过滤，否则会把当日对冲/建仓/自检（D9 assertion_test）等其他策略成交误写进 positions.json，污染真实持仓口径（缺陷 E）。
- **dry-run 语义**：`rebalance_order_executor` 的 dry_run 只控制最后一步（是否回写 positions），撮合+落盘 FillsStore 始终发生（观测路径 fail-open），这是有意的设计；但审查时须确认 dry-run 不会污染真实状态。
- **门禁会拦自己引入的复杂度**：添加内联过滤逻辑触发 C901（复杂度 15→16）被 ruff_incremental_gate 拦截，须提取独立辅助函数降复杂度。改代码后必须跑门禁再宣布完成。
- 已修复：缺陷 D（stress_test_runner 静默回退模拟持仓 fail-open → fail-close）、缺陷 E（rebalance 消费方按 strategy 过滤）。

### 全量逐模块审查沉淀 (2026-08-24, 9 模块 33 缺陷)

大文件审查方法论：
- **用 code-explorer subagent 追跨文件调用链**，比人工逐行高效且准（本计划 4 个大文件全用 subagent，准确定位 DTE-1 建仓断链、UE-1 实盘门控缺失）。
- **主链路大文件回归测试用"契约逻辑等价复刻"**：不 import 真实类（有模块级副作用/重依赖），复刻方法逻辑验证契约行为，避免收集 error。
- **区分"真闭环" vs "账本自我记账"**：DTE-1 修正——daily_trade_executor 建仓执行注释声明 SimulatedBroker 但从未 import，实为纯内部算术记账，不落 FillsStore。审查执行闭环须确认"撮合方法是否存在且被调用"，而非只看注释/文档声明。
- **实盘入口必须默认 dry_run 或 TRADING_ENV=production 双签**：--live/--hedge-execute/--rebalance-execute 当前默认即真实执行（虽为模拟撮合无真钱风险），接 QMT 后危险。审查实盘保护时检查每个下单 handler 的默认 dry_run 值与门控。
- **数据降级四要素**：mock 快照要有 source 标记（有）、消费方要检查（常漏）、无兜底价要跳过非用假价、回退硬编码价要显式 stale 告警。
- **风控 fail-open 是红线**：WT 风控异常、止损管理器不可用 → 必须 fail-close 保守阻断或返回标记项告警可见，禁止静默放行/跳过。

已修复 20 缺陷（含 DTE-2/3/4/7、PI-2、UE-2/3、LW-1 等），13 项 P1/P2 结构待办见 docs/CODE_REVIEW_COMPREHENSIVE_20260824.md。
