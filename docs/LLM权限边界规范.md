# LLM 权限边界规范（AI 只读 / 建议 / 报告铁律）

> 创建: 2026-09-03 | 状态: v1 生效（[功能] AUTO-2 交付物）
> ROADMAP 编号: AUTO-2 / 关联决策门 `ai_decision/decision_gate.py`
> 安全基线: `安全审计报告_v8.6_20260824.md` MEDIUM-2B（AI/LLM 间接提示注入）
> 维护口径: 本文档是「LLM→执行」边界唯一的权威规范。凡与本文冲突的运行形态一律以本规范为准。

## 0. 一句话铁律

**LLM 在本系统内永远处于「建议 / 报告 / 只读」位——LLM 输出不得绕过硬风控直接触发真实下单。任何新增调用链都必须把 LLM 输出挡在 `decision_gate` 硬风控门之前，而非之后。**

安全依据（实证，2026-08-24）：
- `--ai-decision` 模式仅生成建议 + 打印 + 报告 + 审计，不触执行层。
- `--ai-hedge` 模式仅打印 + 存 JSON 报告，无下单调用。
- `daily_trade_executor.py` 独立生成交易指令，**不消费** AI/LLM 决策输出。
- 自动执行路径已集成 kill-switch（`automated_execution_system.py` P0-2 熔断检查）。

> 即：AI 注入最多污染「建议 / 报告」内容，不能自动触发下单；残留风险仅为人误信被注入建议而手动下单（社会工程层面）。

---

## 1. 三条 LLM 运行路径及其性质

本系统消费 LLM 输出的入口收敛为三条主路径，**性质均为「建议 / 报告 / 只读」，均不得直连执行**。

### 路径 A — CLI 建议通道：`--ai-decision` / `--ai-hedge`
- 入口：`cli/modes/ai_decision.py`、`cli/modes/ai_hedge_mode.py`
- 性质：**纯建议**。产出结构化建议 + 终端打印 + 落 JSON/Markdown 报告 + 审计记录。
- 约束：`ai_decision.py` 明确打印「AI决策完成 - 请人工审核后再执行交易」；`ai_hedge_mode.py` 仅 `print_trading_output` + 存报告。
- 允许动作：写报告、写审计、打印。**禁止**：调 broker / 生成可执行订单指令。

### 路径 B — 决策链通道：`ai_decision` 包（五 Agent + 辩论 + 共识）
- 入口：`ai_decision/orchestrator.py` → `debate_engine` / `consensus_aggregator`
- 链路：`run_decision()` → **`decision_gate.run_hard_risk()` 硬风控** → `apply_mode()` 写执行态。
- 性质：**唯一允许产出「执行态」的 LLM 链，但必须经 `decision_gate` 硬风控拦截**。
- 默认运行模式：`shadow`（仅写审计，不产可执行指令，不触下单）；仅 `auto` 模式在阈值 + 升级规则全过后才可放行。

### 路径 C — 量化报告 / 研究通道：报告生成与复盘
- 入口：`generate_daily_report.py`、`hn_daily_report.py`、`quant_modules/ai_hedge_fund/*`、`ai_decision/eod_review.py` 等
- 性质：**只读研究**。消费新闻 / 行情 / 研报，产出综述、观点、因子注释、复盘文本。
- 约束：报告类 LLM 输出**永不进入订单指令流**。

> **判定规则**：新增调用若语义上是「读完给出观点 / 建议 / 复盘」，归路径 A/C；若语义上是「参与生成交易决策动作 buy/sell/hold」，归路径 B 并必须挂到 `decision_gate`。

---

## 2. `decision_gate.py` — 硬风控门（LLM 独立于风控）

文件：`ai_decision/decision_gate.py`

**铁律：硬风控永远独立于 AI 运行，不受 AI 输出影响。**

### 2.1 五项硬否决（`run_hard_risk`，任一失败即 veto，不执行）
1. **黑名单**：标的在黑名单 → veto。
2. **RiskAgent 一票否决**（来自五 Agent 共识）。
3. **涨跌停处理**：涨停不可买 / 跌停不可卖。
4. **单笔金额上限**：`> 净值 max_single_pct`（默认 2%）→ veto。
5. **日内累计上限**：`> 净值 max_daily_pct`（默认 10%）→ veto。

### 2.2 运行模式（`apply_mode`，决定最终执行态）
| 模式 | 是否执行 | 语义 |
|---|---|---|
| `shadow`（默认） | ❌ 不执行 | 仅写审计日志；不产可执行指令、不触发下单 |
| `paper` | ❌ 不执行 | 产出模拟指令，不触发 broker |
| `auto` | 仅 buy/sell + 无升级时 ✅ | 经阈值 + 升级规则后放行执行 |

### 2.3 auto 模式的升级规则（谨慎）
- 置信度 `< min_confidence`（默认 0.7）→ 升级人工。
- Judge 裁决类型非 `AUTO`/`FAST` → 升级人工。
- 单笔 / 日内累计超上限 → 升级人工。
- 任何硬风控失败 → **veto，不执行**。
- 未知模式一律按 `shadow` 处理（fail-safe）。
- P0 修复：auto 模式仅 `buy`/`sell` 决策可 `executed=True`，`hold`/`veto` 决策禁止执行。

---

## 3. 防「LLM→执行」直连的边界红线

以下任何一条出现，均视为**边界破坏**，属检测门禁重点告警对象：

1. LLM provider 输出（`chat` / `chat_deep` / `run_debate` / 20 Agent 观点等）**绕过 `decision_gate` 直接**进入订单构造 / broker / execute。
2. 报告 / 建议类路径（A/C）中出现下单指令构造调用。
3. 决策链（B）中在 `decision_gate` veto 后仍强行 `executed=True`。
4. LLM 决策直接作为 `daily_trade_executor` / `OrderRouter` / 任一 broker 网关的输入（当前正确形态是独立生成指令，不消费 AI 输出）。

---

## 4. CI 检测门禁（检测性，非阻断）

> 目的：防未来新增「LLM→执行」直连回归。**检测告警不阻断合并**，交由人工判定。

- 脚本：`scripts/check_llm_exec_boundary.py`（AST 扫描，纯 stdlib）。
- CI 接线：`.github/workflows/quality-gate.yml` 新增步骤（见 §5）。
- 扫描范围：`ai_decision/`、`utils/`、`quant_modules/ai_hedge_fund/`、`15_每日工作流/`、`cli/modes/`。
- 告警口径：
  - **WARN（建议人工核查）**：同一文件 / 相邻作用域内出现「LLM 调用 + broker/下单类调用」共现，疑似边界模糊。
  - **WARN**：报告路径文件包含下单 / broker 相关 import。
- 非阻断：检测发现告警时仅输出 WARNING 并 `exit 0`，不做 fail-closed。

---

## 5. 验收与门禁

- 文档：本文件即为规范交付物，路径 `docs/LLM权限边界规范.md`。
- 脚本：`python scripts/check_llm_exec_boundary.py` 可独立运行，返回 0；`--json out.json` 可机器消费。
- 引用完整性：`scripts/ci_integrity_check.py` 能解析到脚本存在。
- ROADMAP AUTO-2 验收：ruff 检查 yaml/md 引用完整性通过。
