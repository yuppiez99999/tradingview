---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-08-07
updated: 2026-08-07
---

# TDAM Phase 0b 数据导入经验沉淀（2026-08-07）

> cairn → TDAM 单向同步链路首次跑通：29 wiki skill + 114 chat_memory 全部导入成功。
> 关联：`docs/计划同步对齐_20260807.md`、`scripts/tdam/import_cairn_to_tdam.py`、`TDAM_cairn_对接方案.md`。

## 一、成果

- **29 个 wiki skill** 导入成功，可检索（`search_memory('Alpha 因子','skill')` 等 5 关键词全命中）
- **114 条 chat_memory conversation** 导入成功，可检索
- 新增导入脚本 `scripts/tdam/import_cairn_to_tdam.py`（幂等、--dry-run/--asset/--limit/--offset 可断点续传）
- 端到端验收：skill 29 个 + 多关键词 BM25 检索全通过

## 二、修复的 3 个 tdam_client 真实缺陷（"假成功"类静默失败）

| 缺陷 | 位置 | 影响 | 修复 |
|------|------|------|------|
| **响应解析只查 HTTP 状态码、忽略 body.code** | `_call_with_circuit_breaker` | TDAM 业务错误(42203/50001/40001)在 HTTP 200 的 body.code，被误判成功 → 导入"假成功"、数据未持久化 | 解析 body.code，非 0 抛 `TDAMAPIError` |
| **缺 team_id/agent_id 环境变量配置** | `TDAMConfig.__post_init__` | TDAM team/agent 用系统生成的带前缀 id(`team-xxx`/`agt-xxx`)，默认 `default` 不匹配 → skill 写 team/agent not found | 支持 `TDAM_TEAM_ID`/`TDAM_AGENT_ID` 环境变量 |
| **search_memory 对 conversation/chat_memory 漏传 agent_id** | `search_memory` | L0-L3 严格 isolation 缺 agent_id → 查不到已写入的对话记忆 | conversation/chat_memory/atomic 补传 agent_id |

## 三、关键经验（对"静默失败"主题的再印证）

1. **TDAM 业务错误在 HTTP 200 的 body.code**：客户端必须解析 body.code 而非仅 HTTP 状态码，否则"假成功"掩盖真实失败——与系统反复强调要杜绝的静默失败模式一致。
2. **skill 是 agent-scoped**：必须先用 `/v3/meta/agent/create` 创建 agent（需 `x-tdai-user-key` header，非 `Authorization: Bearer`），绑定真实 team_id/agent_id 后 skill 才能写入。
3. **conversation 自动登记 agent**，但 **skill 要求 agent 预存在**——两类写路径的 agent 依赖不同。
4. **TDAM skill name 必须与 content 内 frontmatter.name 一致**（否则 40001 INVALID_FRONTMATTER），且 **name 全局唯一**（否则 42201 SKILL_NAME_DUPLICATE）。
5. **`/v3/meta/*` 鉴权用 `x-tdai-user-key` header**，`/v3/skill/*` 用 `Authorization: Bearer`——两类接口鉴权机制不同。

## 四、当前配置

- team_id: `team-n5phx61z0a`（`v3/meta/team/create` 系统生成）
- agent_id: `agt-n5ppojc8rj`（`v3/meta/agent/create` 生成，绑定上述 team）
- 通过环境变量 `TDAM_TEAM_ID` / `TDAM_AGENT_ID` 注入，需在盘后作业运行环境固化

## 五、后续待办

- 盘后增量导入：观察期内每日增量、观察期后每周全量（对齐 TDAM_cairn_对接方案）
- embeddingService 未启用（health=false），当前仅 BM25；如需语义检索需配置 embedding
- 早期误用 `team=default` 导入的残留数据可清理

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [EOD 运维经验沉淀：OpenBLAS 内存修复 + U9 端到端验证 + D7 断言增强（2026-08-07）](eod-operations-lessons-20260807.md) (相似度 10%)
- [第三方项目集成专题 — 2026-08-21](third-party-integration-20260821.md) (相似度 8%)
- [代码质量提升外部资源评估](code-quality-external-resources-20260821.md) (相似度 8%)
- [Agent-Skills 集成与适配（2026-08-24）](agent-skills-integration.md) (相似度 7%)
- [代码质量修复批次 2026-08-18：ruff 高危规则清零](code-review-ruff-fix-batch-20260818.md) (相似度 7%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
