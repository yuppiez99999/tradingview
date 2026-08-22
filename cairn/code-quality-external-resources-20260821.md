---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-08-21
updated: 2026-08-21
related:
  - cairn/code-quality-wave3.md
  - cairn/code-quality-industrial-gap-20260819.md
  - cairn/code-quality-review-open-code-review.md
---

# 代码质量提升外部资源评估

> **结论**: 量化系统代码质量体系已业界领先，外部资源增量价值有限。真正瓶颈在已有工具的落地执行（CI 失效 / 工作区未收敛 / 大文件未拆分），而非缺少工具。
> **完整报告**: `docs/代码质量提升资源分析_20260821.md`
> **排期计划**: `docs/代码质量提升排期计划_20260821.md`

## 一、外部资源增量价值排序

### P0 — 高增量价值

| 资源 | 增量 | 接入方式 |
|------|------|----------|
| `open-code-review` 固化 | LLM 审查从手动→每 PR 自动+夜间全量 | 补全 `ocr-review.yml` 缺失脚本 |
| 修复 CI 6 缺失脚本 | 让已有完善 CI 配置实际生效 | 创建缺失脚本或移除引用 |

### P1 — 中等增量价值

| 资源 | 增量 | 接入方式 |
|------|------|----------|
| `.skills/addyosmani-code-review-and-quality` | 五轴审查补充 Q1-Q7 通用工程视角 | 作为 code-review-graph 补充模板 |
| `.skills/code-refactor` | Fowler 43 条重构规则库 | 导入 `scripts/refactor_rules.json` |
| `.skills/diagnosing-bugs` | 6 阶段调试纪律 | 作为 `debug-issue` 流程模板 |

### P2 — 低增量价值（不建议接入）

- **ECC 代码质量 Skill**: 根据 `ecc-python-rules-crosscheck_20260821.md`，是量化系统 CI 的极弱子集，无任何遗漏
- **ECC production-audit / plankton-* / agent-architecture-audit**: 通用框架，量化系统已有更专业的量化专项审查
- **FinClaw（1031 Skill）**: 实际内容很少（仅 .env.example），名不副实

## 二、真正应该做的事

| 优先级 | 行动 | 类别 |
|--------|------|------|
| P0 | 修复 CI 6 缺失脚本 | 落地执行 |
| P0 | open-code-review 固化为 CI 门禁 | 工具固化 |
| P0 | 收敛工作区 915 未提交文件 | 落地执行 |
| P1 | 拆分 daily_workflow.py（6230→多个<800） | 落地执行 |
| P1 | mypy strict 扩展到 utils/ | 落地执行 |
| P1 | 接入 addyosmani 五轴 + code-refactor 规则 | 外部接入 |
| P2 | ruff 违规 169→0 | 落地执行 |
| P2 | 覆盖率 ~50%→80%+ | 落地执行 |

## 三、与现有计划的关系

- 与 `UNIFIED_UPGRADE_PLAN_20260810.md` Sprint 1-6 部分重叠，本报告聚焦代码质量
- 与 `OPTIMAL_PLAN_20260811.md` 阶段 A 对应（CI 修复）
- 与 `高价值项目集成排期计划_20260811.md` 不重叠（功能增强 vs 代码质量）
- 与 `ECC赋能量化系统工作计划_20260821.md` 结论一致，本报告补充 `.skills` 评估

## 四、踩坑记录

- **ECC 价值误判风险**: ECC 有 64 Agent + 261 Skill，数量庞大容易产生"接入即提升"的错觉。实际经 `ecc-python-rules-crosscheck_20260821.md` 逐条比对，ECC python rules 是量化系统 CI 的极弱子集。**教训**: 评估外部资源必须逐条比对已有体系，不能被数量迷惑。
- **FinClaw 名不副实**: 宣传 1031 Skill，实际内容极少。**教训**: GitHub 项目 stars/描述不可信，必须实际读取内容。
- **CI 配置≠CI 运行**: 量化系统有 7 个 CI workflow，但 6 个脚本缺失导致实际失效。**教训**: 配置存在不等于实际运行，必须验证 CI 实际执行状态。