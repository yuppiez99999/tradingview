# Feature Specification: [FEATURE NAME]

**Feature Branch**: `[###-feature-name]`

**Created**: [DATE]

**Status**: Draft

**Roadmap Ref**: `[ROADMAP Task/Stream ID — 必填；无 TaskID 的 spec 不合规范（28仓铁律，见 .specify/memory/constitution.md 原则 2）]`

**Input**: User description: "$ARGUMENTS"

## User Scenarios & Testing *(mandatory)*

<!--
  IMPORTANT: User stories should be PRIORITIZED as user journeys ordered by importance.
  Each user story/journey must be INDEPENDENTLY TESTABLE - meaning if you implement just ONE of them,
  you should still have a viable MVP (Minimum Viable Product) that delivers value.

  Assign priorities (P1, P2, P3, etc.) to each user story, where P1 is the most critical.
  Think of each story as a standalone slice of functionality that can be:
  - Developed independently
  - Tested independently
  - Deployed independently
  - Demonstrated to users independently
-->

### User Story 1 - [Brief Title] (Priority: P1)

[Describe this user journey in plain language]

**Why this priority**: [Explain the value and why it has this priority level]

**Independent Test**: [Describe how this can be tested independently - e.g., "Can be fully tested by [specific action] and delivers [specific value]"]

**Acceptance Scenarios**:

1. **Given** [initial state], **When** [action], **Then** [expected outcome]
2. **Given** [initial state], **When** [action], **Then** [expected outcome]

---

### User Story 2 - [Brief Title] (Priority: P2)

[Describe this user journey in plain language]

**Why this priority**: [Explain the value and why it has this priority level]

**Independent Test**: [Describe how this can be tested independently]

**Acceptance Scenarios**:

1. **Given** [initial state], **When** [action], **Then** [expected outcome]

---

[Add more user stories as needed, each with an assigned priority]

### Edge Cases

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right edge cases.
-->

- What happens when [boundary condition]?
- How does system handle [error scenario]?

## Requirements *(mandatory)*

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right functional requirements.
-->

### Functional Requirements

- **FR-001**: System MUST [specific capability, e.g., "allow users to create accounts"]
- **FR-002**: System MUST [specific capability, e.g., "validate email addresses"]

*Example of marking unclear requirements:*

- **FR-00x**: System MUST [NEEDS CLARIFICATION: ...]

### Key Entities *(include if feature involves data)*

- **[Entity 1]**: [What it represents, key attributes without implementation]
- **[Entity 2]**: [What it represents, relationships to other entities]

## 口径引用（28仓铁律）

> 数值口径（资金/绩效目标/发布窗口/版本号等）**一律引用** `cairn/ROADMAP.md` §CURRENT STATE 的条目号（如 R-10），
> **禁止在本 spec 复制数值** —— spec 消费 ROADMAP，ROADMAP 永不从 spec 读数。
> 引用格式：`[口径] 见 ROADMAP §CURRENT STATE.<条目>`。

## Success Criteria *(mandatory)*

<!--
  ACTION REQUIRED: 28仓定制 —— 成功判据必须是"验收判据表"（机读格式，打勾三要素的机读版）:
  每条 = 门禁名或测试名 + 期望值 + 复现命令。至少包含 AC-001（收敛门禁）。
  期望值如涉及口径，引用 ROADMAP 条目而非写数值。
-->

### 验收判据表（Acceptance Criteria）

| # | 判据（门禁/测试） | 期望值 | 复现命令 |
|---|---|---|---|
| AC-001 | speckit_converge_gate 四门禁 | exit 0 | `.venv/Scripts/python.exe scripts/speckit_converge_gate.py --files <改动.py...> --pytest-args "<本 spec 测试范围>"` |
| AC-002 | [pytest 用例路径] | N passed, 0 failed | `pytest <路径> -q` |
| AC-003 | [fix 类: 修复前会失败的回归用例] | 修复前 FAIL / 修复后 PASS | `pytest <回归用例路径> -q` |
| AC-004 | [数值断言，期望值引用 ROADMAP 条目] | [期望值] | [命令] |

## Assumptions

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right assumptions based on reasonable defaults
  chosen when the feature description did not specify certain details.
-->

- [Assumption about scope boundaries]
- [Dependency on existing system/service]
