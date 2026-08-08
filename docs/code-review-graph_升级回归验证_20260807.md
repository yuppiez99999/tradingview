# code-review-graph 升级回归验证报告（2026-08-07）

> github_trending 升级计划 Phase 0。执行人：AI 辅助。
> 关联：`cairn/code-review-graph-guide.md`（guide，8 个查询工具）、`docs/github_trending_高价值统计与升级计划_20260807.md`。

## 一、版本与升级结论

| 项 | 值 |
|----|----|
| 当前安装版本 | **2.3.7**（PyPI 最新，无更高版本） |
| 升级动作 | **无需升级包**（已是最新 release） |
| 图谱增量更新 | ✅ 执行 `code-review-graph update`：1291 文件更新、FTS 索引重建 8819 行 |
| 更新后图谱 | 8819 节点、92531 边、641 文件；Last updated 2026-08-07 16:52；commit 49f4330 |

**结论**：Phase 0「升级到最新 release」已满足（2.3.7 = latest）；实际执行了**图谱数据同步 + 8 项查询回归验证**。

## 二、8 项查询能力回归验证

| # | guide 工具 | CLI 命令 | 验证样本 | 结果 |
|---|-----------|----------|----------|------|
| 1 | detect_changes | `impact --files ...` | `hedge_execution_orders.py`+`utils/tdam_client.py` | ✅ 26 直接变更节点 + 15 受影响 + 3 附加文件 |
| 2 | get_review_context | `query file_summary` | `hedge_execution_orders.py` | ✅ 26 节点（文件+函数） |
| 3 | get_impact_radius | `impact --depth 2` | 同上 | ✅ 2 跳内 15 节点 |
| 4 | get_affected_flows | `flows --sort criticality` | 全局 | ✅ 5 执行流，首个 main 流 criticality 0.7831 |
| 5 | query_graph | `query callers_of/imports_of` | `hedge_engine`→20 候选（ambiguous 消歧）；`hedge_execution_orders.py` imports_of→12 | ✅ 需用短文件名/qualified_name，绝对路径不被接受（用法注意） |
| 6 | semantic_search | `search --kind Class` | `tdam` | ⚠️ 返回 0（见"发现"一节） |
| 7 | get_architecture | `architecture` + `communities` | 全局 | ✅ 50 社区（v8-3-institutional 2707 节点等）、0 警告 |
| 8 | refactor | `refactor suggest/dead_code` | `dead-code`→362 死符号；`refactor suggest`→362 建议 | ✅ |

**8 项中 7 项正常**，1 项（search）受"未跟踪文件不入图"影响返回 0（非命令缺陷）。

## 三、重要发现：图谱对未跟踪文件有覆盖盲区

**现象**：`tdam_client.py`、`scripts/tdam/import_cairn_to_tdam.py`、`export_cairn_for_tdam.py` 均为 git 未跟踪文件（`git status` 显示 `??`），`query file_summary` 对它们均返回 0。

**根因**：code-review-graph 的增量更新（`update`）**基于 git 变更**（git diff），未跟踪的新文件不参与增量扫描，因此不进图谱。已跟踪文件（如 `hedge_execution_orders.py`）正常入图。

**影响**：
- review 新增模块（未 commit）时，`semantic_search`/`file_summary` 查不到，依赖分析有盲区
- 对本次新增的 `scripts/tdam/*`、`utils/tdam_client.py` 的图级分析不可用

**缓解建议**：
1. 新增文件后先 `git add`（或 commit）再 `update`，即可入图
2. 或对新增模块用 `query file_summary` 前的 `--repo` 全量重建（代价高）
3. 若需立即索引新增文件，可 `git add -N <file>`（intent-to-add）后 `update`

## 四、使用注意（回归中发现）

1. `query callers_of/imports_of` 的 target 用**短文件名**（如 `hedge_engine.py`）或 qualified_name，**不要用绝对路径**（返回 not_found）
2. `query <name>` 匹配含歧义时返回 `ambiguous` + 候选列表，需用 qualified_name 消歧
3. `search` 默认 search_mode="none"，对子串匹配不友好；按 `--kind` 限定后仍需精确名

## 五、后续

- 本次未新增代码，图谱同步即可支撑现有 review
- 新增文件入图问题列入待办，建议在 `docs/github_trending_高价值统计与升级计划_20260807.md` Phase 0 后续补充
- loopx / pdf-inspector / skills / cloudflare / TencentMemory 按原排期（W34 起）
