# Tasks: [FEATURE NAME]

**Input**: Design documents from `/specs/[###-feature-name]/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: The examples below include test tasks. Tests are OPTIONAL - only include them if explicitly requested in the feature specification.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## 28仓 Task Conventions（必读）

- **类型标注**：每个任务描述开头标 `[feat]` / `[fix]` / `[refactor]`。
- **fix 类强制回归**：`[fix]` 任务必须先写"修复前会失败"的回归测试（先红后绿），回归用例路径写进任务描述。
- **打勾三要素**：勾选 `[x]` 时，行尾必须带 `（文件+日期 / 实证值 / 复现命令）` 摘要，缺一不算完成。
- **完成定义**：任务完成 = `scripts/speckit_converge_gate.py` exit 0（`--files` 含本任务改动，`--pytest-args` 取本 feature 声明的测试范围）。
- **测试范围声明**：本文件顶部必须有 `**测试范围**: <pytest 参数>` 一行（converge 门禁 G2 的入参来源；缺此行 = converge 拒跑）。

**测试范围**: [pytest 参数，如 `tests/unit/test_xxx.py -q`]

## Path Conventions（28仓真实路径）

- **生产代码**：根级包 `utils/`（核心工具与门禁）、`scripts/`（治理与运维脚本）、`executor/`、`quant_modules/`、`reporting/` 等；新功能优先挂现有扩展点（因子 `@register_factor`、券商 `register_broker_adapter`、数据源 `manager.register`、CLI `dispatcher.register`、告警 `send_alert`）。
- **测试**：`tests/unit/`（默认执行）；`tests/integration/`（需 `--run-integration`）；**勿把用例放进 `tests/e2e/`**（conftest 关键字含祖先目录名 ⇒ 天生 skip，挂标记 = 静默绿）。
- **不新增顶层目录**；大文件拆解走 mixin 拆分（宿主 1500 行护栏，见 `daily_trade_executor.py`）。

<!--
  ============================================================================
  IMPORTANT: The tasks below are SAMPLE TASKS for illustration purposes only.

  The /speckit.tasks command MUST replace these with actual tasks based on:
  - User stories from spec.md (with their priorities P1, P2, P3...)
  - Feature requirements from plan.md
  - Entities from data-model.md
  - Endpoints from contracts/

  Tasks MUST be organized by user story so each story can be:
  - Implemented independently
  - Tested independently
  - Delivered as an MVP increment

  DO NOT keep these sample tasks in the generated tasks.md file.
  ============================================================================
-->

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [ ] T001 [feat] Create/extend module per implementation plan in `utils/` or `scripts/`
- [ ] T002 [P] [feat] [US1] Wire into existing extension point (see Path Conventions)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [ ] T003 [feat] Foundational contract fields (上下游契约字段必须完全对齐 — 28仓高频断链根因)
- [ ] T004 [fix] [US1] Regression test that FAILS before the fix: `tests/unit/test_[name].py`（先红后绿）

**Checkpoint**: Foundation ready - user story implementation can now begin

---

## Phase 3: User Story 1 - [Title] (Priority: P1) 🎯 MVP

**Goal**: [Brief description of what this story delivers]

**Independent Test**: [How to verify this story works on its own]

### Implementation for User Story 1

- [ ] T005 [P] [US1] [feat] Implement [component] in `utils/[module].py`
- [ ] T006 [US1] [feat] Wire downstream consumer（执行链判据：落盘事实源被 PnL/TCA 消费）

**Checkpoint**: User Story 1 fully functional and testable independently

---

[Add more user story phases as needed, following the same pattern]

---

## Phase N: Polish & Cross-Cutting Concerns

- [ ] TXXX [P] [feat] Documentation updates in `docs/` / `cairn/`
- [ ] TXXX [feat] Run convergence gate and excerpt output into `specs/<feature>/implementation-notes.md`

---

## Dependencies & Execution Order

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3+)**: Depend on Foundational; can proceed in parallel (if staffed)
- Within each story: regression tests BEFORE implementation (fix 类)

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Verify tests fail before implementing (fix 类)
- Commit after each task or logical group（`git add <具体文件>` 防夹带）
- Stop at any checkpoint to validate story independently
