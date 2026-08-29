---
type: process_protocol
status: active
authoring_mode: ai_generated
created: 2026-08-19
updated: 2026-08-19
related:
  - cairn/code-review-sop.md
  - cairn/dev-workflow-automation.md
---

# MMR 评审规约 (multi-model-review 集成协议)

> 本文档规约 multi-model-review (mmr) 在本系统的集成方式、职责边界与知识闭环。
> 与 `cairn/code-review-sop.md` 互补: ocr 管代码, mmr 管文档/数据结论 + ocr 候选裁判。

## 一、架构定位: 三层级联

| 层级 | 工具 | 触发 | 模型 | 职责 | 阻断? |
|------|------|------|------|------|-------|
| L1 | quality-gate.yml | PR push | 静态 | ruff/print/workspace/CI | 阻断 |
| L2 | ocr-review.yml | PR `.py` 变更 | GLM-4.5-air 单模型 | 代码发现 | 评论 |
| L3 | mmr-judge.yml | L2 完成 + high/critical | DeepSeek+GLM 投票 | ocr 候选裁判 | 评论 |
| L4 | mmr-deep.yml | PR `.md`/`reports/*.json` 变更 | DeepSeek+GLM 投票 | 文档+数据结论全评审 | 评论 |

**核心原则**: mmr 不替换 ocr, 而是作为 ocr 的裁判层 + 量化数据结论的专属审查器。

## 二、统一共识层

`utils/alpha/llm/consensus.py` 抽象自 `dual_model_judge.py`, 三处复用:

| 复用方 | models | judge_mode | lens | 场景 |
|--------|--------|------------|------|------|
| dual_model_judge.py | [deepseek, glm] | cross_validate | — | 运行时风控决策 |
| mmr L3 judge | [deepseek, glm] | vote | judge | ocr 候选二次验证 |
| mmr L4 deep | [deepseek, glm] | vote | product/architecture/data-claim | 文档+数据结论评审 |

## 三、量化特化 lens: data-claim

`references/mmr-prompts/data-claim-review.md` 定义量化专用审查清单:

1. 夏普比率是否经 DSR 调整
2. 回测是否使用 CPCV
3. 收益率是否经噪声注入稳定性测试
4. 样本外/内差异是否合理 (过拟合信号)
5. 因子 IC/IR 的 t 值是否 > 2
6. 结论是否与 cairn/ 知识专题矛盾

**适用制品**: `reports/eod_guard_report_*.json` / `reports/quarterly_review_*.json` / `reports/stress_test_*.json` / `reports/rebalance_execution_orders_*.json`

## 四、知识闭环 (cairn 联动)

```
mmr confirmed 结论
       |
       +-> docs/CODE_REVIEW_BACKLOG.md     (统一看板新增条目, ID 前缀 MMR-)
       |
       +-> cairn/LOG.md 顶部追加           (摘要 + 指针, <=20 行)
       |
       +-> cairn/<topic>.md 原位更新       (若涉及已有知识专题)
           回测结论审查 -> cairn/backtest-standards.md
           风控结论审查 -> cairn/risk-architecture.md
           架构评审     -> cairn/architecture-map.md
```

**dismissed 结论不沉淀** (避免噪声污染知识库), 仅记录在 `reports/mmr_reviews/` 产物中。

## 五、产物归档

| 层级 | 目录 | 保留 |
|------|------|------|
| L3 judge | `reports/mmr_reviews/judge/` | 30 天 (artifact) |
| L4 docs | `reports/mmr_reviews/docs/` | 30 天 (artifact) |
| L4 data | `reports/mmr_reviews/data/` | 30 天 (artifact) |

## 六、成本控制

| 场景 | 频次 | 层级 | Token |
|------|------|------|-------|
| PR 改 .py (无 high) | 高频 | L1+L2 | ~$0.001 |
| PR 改 .py (有 high) | 中频 | L1+L2+L3 | ~$0.01 |
| PR 改 docs/ | 低频 | L1+L4 | ~$0.05 |
| PR 改 reports/ | 周度 | L1+L4(data-claim) | ~$0.08 |
| **月度总计** | | | **~$15-20** |

**降本关键**: L3 仅对 L2 的 high/critical 触发; L4 仅对 .md/.json 变更触发。

## 七、边界约束 (不做什么)

1. 不替换 ocr — 代码审查主业留在 ocr
2. 不阻断 PR — mmr 仅评论, 不设 exit 1
3. 不重复 dual_model_judge.py — consensus.py 抽象其模式, 可选复用不强制
4. 不 nightly 全量 — 文档量远小于代码, PR 触发 + 手动按需即可
5. 不评审 `cairn/Reference/` — 外部原始输入, 仅追加, 不审查

## 八、Secrets 配置

| Secret | 用途 | 来源 |
|--------|------|------|
| `OCR_LLM_AUTH_TOKEN` | GLM API key | 复用 ocr 已有配置 |
| `DEEPSEEK_API_KEY` | DeepSeek API key | 新增 (dual_model_judge.py 已用过 DeepSeek, 配置已存在本地) |

## 九、落地清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `utils/alpha/llm/consensus.py` | 新增 | 统一共识层 |
| `scripts/mmr_filter_candidates.py` | 新增 | L3 候选过滤 |
| `scripts/mmr_update_backlog.py` | 新增 | 看板更新 |
| `.github/workflows/mmr-judge.yml` | 新增 | L3 级联 judge |
| `.github/workflows/mmr-deep.yml` | 新增 | L4 全评审 |
| `references/mmr-prompts/data-claim-review.md` | 新增 | 量化特化 prompt |
| `cairn/mmr-review-protocol.md` | 本文件 | 评审规约 |

## 十、contains 标签

- contains: mmr-integration, multi-model-review, judge-voting, data-claim-lens, ocr-cascade

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [Agent 直接审查兜底方法论（外部 LLM 额度耗尽时）](code-review-agent-fallback-20260810.md) (相似度 20%)
- [GLM 4.5-air LLM 驱动代码审查方法论（open-code-review + GLM 4.5-air）](code-review-glm45-llm-scan.md) (相似度 17%)
- [代码审查 + 修复批次 标准作业流程 (SOP)](code-review-sop.md) (相似度 8%)
- [新代码审查 bug 模式与根因（2026-08-17）](code-review-newcode-bug-patterns-20260817.md) (相似度 7%)
- [open-code-review 代码审查报告（核心模块）](code-quality-review-open-code-review.md) (相似度 7%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
