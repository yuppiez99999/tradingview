# Implementation Plan: [FEATURE]

**Branch**: `[###-feature-name]` | **Date**: [DATE] | **Spec**: [link] | **Roadmap Ref**: [同 spec.md 的 TaskID]

**Input**: Feature specification from `/specs/[###-feature-name]/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command; its definition describes the execution workflow.

## Summary

[Extract from feature spec: primary requirement + technical approach from research]

## Technical Context

<!--
  ACTION REQUIRED: 按本 feature 实际情况替换以下占位符。
  28仓默认值已预填 —— 不确定处标 NEEDS CLARIFICATION，不要编造。
-->

**Language/Version**: Python（`.venv`；ruff `target-version=py38` 口径 —— 运行时不得用 3.9+ 语法）

**Primary Dependencies**: 现有依赖树（`requirements-core.txt` 等）；**2027 前禁止新增生产依赖**（流程/测试依赖须走独立工具环境，如 uv tool）

**Storage**: 文件/JSON（`config/` 权威 + `configs/` 遗留双源，改配置须核对 `utils/config_manager` 的读取回退链）

**Testing**: pytest —— `tests/unit/` 默认；`tests/integration/` 需 `--run-integration`；勿用 `tests/e2e/`（静默绿）

**Lint/门禁**: ruff（`ruff.toml`）+ `scripts/ruff_incremental_gate.py`（增量零新增）+ pre-commit 三道门（DTZ005 / mypy 基线 / 单测不写生产目录）

**Performance Goals**: [本 feature 相关，如 "回测单次 < Ns"，或 N/A]

**Constraints**: [本 feature 相关，如 "不触碰生产目录"、"决策路径 fail-close / 观测路径 fail-open"，或 NEEDS CLARIFICATION]

**Scale/Scope**: [本 feature 相关，或 N/A]

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

对照 `.specify/memory/constitution.md` 八条 SDD 增量原则逐条检查（尤其：fix 回归测试、口径引用、落盘事实源判据）。

## Project Structure

### Documentation (this feature)

```text
specs/[###-feature]/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

<!--
  ACTION REQUIRED: 用本 feature 真实落点替换（28仓无 src/ 布局）。
  示例：
  utils/<module>.py          # 新组件
  scripts/<tool>.py          # 治理/运维入口
  tests/unit/test_<module>.py
  挂现有扩展点优先于新建文件。
-->

```text
[本 feature 的真实文件落点树 — 挂现有扩展点优先]
```

**Structure Decision**: [Document the selected structure and reference the real directories captured above]

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| [e.g., 新顶层脚本] | [current need] | [why existing extension point insufficient] |
