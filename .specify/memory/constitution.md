# 28仓 Spec Constitution（薄索引版）

> 本文件是 SDD 工作流内的治理约束入口。**不复制**既有纪律，只做指针 + SDD 增量。
> 冲突时权威顺序：`cairn/ROADMAP.md` > `AGENTS.md` / `CLAUDE.md` > 本文件。
> 集成方案：`cairn/spec-kit-sdd-integration-20260911.md`。

## 指针区（既有纪律，显式引用、不重写）

- 开发/审查纪律 → `AGENTS.md`、`CLAUDE.md`
- 资金/风控/绩效口径唯一权威 → `cairn/ROADMAP.md` §CURRENT STATE（spec 中禁止复制数值，只引用条目号）
- 跨线合并与门禁流程 → `cairn/merge-and-gate-playbook-20260911.md`
- 完成定义（DoD：「验证过」而非「改过了」）→ `docs/CODE_REVIEW_PROCESS.md` §4.2
- 环境与编码坑（GBK/venv/junction）→ `AGENTS.md` 及根仓 `.codebuddy/memory/MEMORY.md`

## SDD 增量原则（本仓新增，编号固定）

1. **规格先于代码**：无 spec 不动主链路代码；spec 必须挂 ROADMAP TaskID。
2. **单向数据流**：spec 消费 ROADMAP 口径，ROADMAP 永不从 spec 读数值；spec 内出现资金/绩效/窗口数值即违规。
3. **fix 类任务必须附"修复前会失败"的回归测试**（先红后绿，测试路径写入任务描述）。
4. **converge 未全绿不得声明完成**：完成 = `scripts/speckit_converge_gate.py` exit 0（四门禁：ruff 增量 / pytest 声明范围 / 工业级 / 数据有效性），且输出摘录进 `implementation-notes.md`。
5. **棕地只增量**：存量代码不回溯补 spec；新需求才走 SDD。
6. **完成声明≠完成**：打勾三要素 = 文件+日期 / 实证值 / 复现命令，缺一不算完成。
7. **fail-closed**：门禁缺产物、缺测试范围、缺数据 = FAIL，不是 skip（"缺数据/空集合=通过"是假 PASS 头号来源）。
8. **执行链判据**：成交类改动的验收 = 成交变成可追溯落盘事实源并被 PnL/TCA 消费（不只是订单生成）。

## Governance

- 本 constitution 的修订须同步 `cairn/spec-kit-sdd-integration-20260911.md` 并在 `cairn/LOG.md` 登记。
- Kill Criteria（连续 2 个 spec 未被消费 / 口径冲突 ≥2 次 / 2 sprint 无改善）触发时，本文件随 SDD 接入一并归档。

**Version**: 1.0 | **Ratified**: 2026-09-11 | **Last Amended**: 2026-09-11
