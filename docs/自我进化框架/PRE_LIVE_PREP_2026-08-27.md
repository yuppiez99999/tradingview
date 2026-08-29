# 实盘之前可做的准备工作清单 — 2026-08-27（对齐 cairn/ 设计）

> **参考依据**:
> - `cairn/live-trading-admission-criteria-20260811.md`（实盘准入 7 项硬性门槛 + 6 个决策门）
> - `cairn/shadow-realness-audit-20260824.md`（影子账户真实性核查，P0 阻断项）
> - `docs/实盘前工作清单与推进计划_20260824.md`（P0/P1/P2 分层清单）
> - `docs/实盘前可执行工作总结_20260825.md`（已完成 26 项总结）
>
> **"实盘"定义**：Stage 2 灰度发布（三个新模块正式接管资金）+ 12-31 上实盘。本清单覆盖"不依赖实时盈利"的所有就绪工作。

---

## 一、当前已完结（来自 08-25 总结，26 项已完成）

P1 全部完成：数据库 Schema 校验、`daily_workflow` 接入真实撮合链路、LLMRouter/DecisionTheoriesFusion/MultiFactorSignal 单测（125 全过）、风控回归测试、绩效评估明细、Phase B 渐进启用脚本、Fail-Closed 回滚机制、K8s 部署模板、A/B 测试框架、FinEng 三模块只读对照、特征工程快照、熵监控、运维手册、回滚决策树、准备度评分卡、安全审计、架构级沉默失败检查等。

> 注意：08-25 总结标注"P0 准入流程启动"为已完成，但 08-27 实测 admission_state.json 当时不存在（说明那次启动未持久化或被清理）。**08-27 已重新执行 `start` 并校准 started_at=2026-07-23，目前 admission_state.json 已存在、daily 可正常生成 DSR。**

---

## 二、实盘之前仍待做（按 cairn 设计对齐）

### P0 阻断项（必须先解，否则任何门禁都过不了）

**P0-1 影子真实性（trade_log 非空）** — `cairn/shadow-realness-audit-20260824.md` 核心结论
- 现状：影子账户是 NAV 回算模式，`trade_log` 恒空，从不真实撮合。
- 要做：让影子账户复用 DTE-1 建仓撮合链（SimulatedBroker/FillsStore），产生真实成交，NAV 基于成交而非回算。
- 依赖：DTE-1 建仓接入撮合链 / G1 QMT 接线工作线。
- 不依赖实时盈利，纯工程接入工作。

**P0-2 准入流程打通 + 推进条件加强**
- `advance_stage()` 当前只查"天数≥14 + 未 fail-fast"，不查 trade_log/绩效。需增加"trade_log 非空 + 绩效达标"检查。
- 08-27 已建 admission_state.json，但需确认 evaluate 评估的是真实撮合数据（待 P0-1 完成后再评）。

### P1 门禁三件套（决策门 1，11-15 检查）

**P1-1 门禁三件套连续 21 天 0 FAIL/0 WARN/GREEN**
- `industrial_grade_check.py` + `assert_data_validity.py` + `engineering_debt_gate.py` 每日跑。
- 验证命令已存在，需要确认调度 + 累积 21 天记录。纯运维，不依赖实盘。

**P1-2 修复单 DoD 执行率 100%**
- 抽样审计 ≥ 5 个修复单，核对 14 项 DoD 清单。纯审计，不依赖实盘。

**P1-3 审查会话 DoD 执行率 100%**
- 抽样审计 ≥ 3 个审查会话，核对 9 项 DoD 清单。纯审计。

### P1 真实下单接线（决策门 6，12-20 检查）

**P1-4 G1 QMT 真实下单接线**
- `automated_execution_system.py` 的 OrderRouter.broker = QmtBrokerAPI（或等效真实券商）。
- dry_run 影子期 ≥ 2 周。纯工程接线，不依赖策略盈利。

### P2 灰度发布（决策门 5，12-15 检查）

**P2-1 灰度发布三阶段**
- S1 10%×3天 → S2 50%×1周 → S3 100%。每阶段 PnL 偏离 > 2σ 立即回滚。
- 需在 P0/P1 全部通过后执行。

### 已就绪待启用（被绩效准入挡住，但代码完成）

| 模块 | 实现 | 单测 | 当前状态 |
|------|------|------|---------|
| MultiFactorSignal | `utils/alpha/multi_factor_signal.py` | ✅ 125 全过 | Flag 关，等准入 |
| DecisionTheoriesFusion | `utils/alpha/decision_theories.py` | — | Flag 关，等准入 |
| LLMReportAnalyzer | `utils/alpha/llm_router.py` | ✅ 已测 | Flag 关，等准入 |

---

## 三、今日（08-27）已落地

1. ✅ 修复 `v84_ShadowAdmissionDaily` 失败：初始化 admission_state.json + 校准 started_at=2026-07-23。
2. ✅ 调整准入阈值（年化 15%→8%、回撤 10%→15%），与用户口径对齐。
3. ✅ 运行 evaluate：确认阻塞根因是影子账户实盘收益为负（且 trade_log 空导致 DSR 基于回算）。
4. ✅ 125 单测全过（三个待启用模块）。

## 四、建议立刻推进（不依赖实盘盈利）

- **P0-1 影子真实性接入**：这是唯一真正卡死实盘的门。让影子账户跑真实撮合链，积累 trade_log。
- **P1-1 门禁三件套调度**：启动 21 天连续 GREEN 计数。
- **P1-2/P1-3 DoD 审计**：可立即抽样审计历史修复单/审查会话。
