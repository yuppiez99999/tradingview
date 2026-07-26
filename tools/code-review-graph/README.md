# code-review-graph 集成说明（v2.3.7）

> **集成批次**：GitHub 周榜热门项目深度集成（2026-07-26 第二批）
> **集成来源**：[tirth8205/code-review-graph](https://github.com/tirth8205/code-review-graph) (+4,791 ⭐ / 周)
> **风险等级**：极低（仅开发时使用，不影响实盘交易）
> **可逆性**：`uv tool uninstall code-review-graph` + 删除本目录即可完全回滚

---

## 一、是什么

**code-review-graph** 是一个本地优先的代码智能图谱工具：
- 持久化增量知识图谱（tree-sitter 解析 + SQLite 存储）
- MCP server，可被 Claude Code / Cursor / Trae 等调用
- 提供 `query_graph_tool` / `detect_changes_tool` / `get_impact_radius_tool` 等工具
- 替代 Grep/Glob 做结构化代码探索，省 token、给上下文

## 二、安装（已完成）

### 2.1 通过 uv tool 隔离安装（不污染 Python 3.8.9 主环境）

```powershell
# 配置代理（如需）
$env:HTTPS_PROXY = "http://127.0.0.1:7897"
$env:HTTP_PROXY = "http://127.0.0.1:7897"

# 安装
uv tool install code-review-graph

# 验证
code-review-graph --version
# 期望输出: code-review-graph 2.3.7
```

### 2.2 注册到 Claude Code（已完成）

```powershell
# 在项目根目录执行（已自动配置 .mcp.json）
code-review-graph install --platform claude-code -y --no-hooks
```

生成的配置：

```json
// .mcp.json
{
  "mcpServers": {
    "code-review-graph": {
      "command": "uvx",
      "args": ["code-review-graph", "serve"],
      "cwd": "<项目根目录>",
      "type": "stdio"
    }
  }
}
```

### 2.3 构建知识图谱

```powershell
# 全量构建（首次执行，项目 5575 个 Python 文件约需 3-10 分钟）
code-review-graph build

# 增量更新（后续执行，只处理变更文件）
code-review-graph update

# 查看图谱统计
code-review-graph status
```

## 三、使用方法

### 3.1 CLI 命令

| 命令 | 用途 |
|------|------|
| `code-review-graph query <pattern>` | 查询图谱关系（callers_of / callees_of / imports_of / tests_for） |
| `code-review-graph impact <file>` | 分析变更影响半径 |
| `code-review-graph search <keyword>` | 按名称/关键词搜索节点 |
| `code-review-graph architecture` | 查看架构总览 |
| `code-review-graph dead-code` | 查找无调用方的死代码 |
| `code-review-graph visualize` | 生成交互式 HTML 图谱 |
| `code-review-graph wiki` | 从社区结构生成 markdown wiki |

### 3.2 MCP 工具（Claude Code / Trae 自动调用）

| 工具 | 使用场景 |
|------|---------|
| `semantic_search_nodes_tool` | 替代 Grep 查找函数/类 |
| `query_graph_tool` | 追踪 callers / callees / imports / tests |
| `detect_changes_tool` | 代码审查时给风险评分 |
| `get_review_context_tool` | 获取审查上下文（token 高效） |
| `get_impact_radius_tool` | 变更影响半径分析 |
| `get_affected_flows_tool` | 受影响的执行路径 |
| `get_architecture_overview_tool` | 架构总览 |
| `list_communities_tool` | 模块社区列表 |
| `refactor_tool` | 重构规划（重命名、死代码） |

### 3.3 典型工作流

```powershell
# 1. 探索陌生模块（替代 Grep）
code-review-graph query "callers_of:SignalFusionEngine.fuse"

# 2. 代码审查前
code-review-graph detect-changes
# 输出: 风险评分 + 受影响节点 + 建议检查的测试

# 3. 重构前
code-review-graph impact utils/signal_fusion.py
# 输出: blast radius + 所有调用方

# 4. 查找死代码
code-review-graph dead-code --lang python
```

## 四、Trae-CN 平台兼容

由于 Trae-CN 基于 Claude Code 内核，`.mcp.json` 配置同样生效。
若需要在 `.trae-cn/skills/` 下也保留 skill 文件，可手动复制：

```powershell
Copy-Item -Path ".claude\skills\code-review-graph" -Destination ".trae-cn\skills\code-review-graph" -Recurse -Force
```

## 五、回滚步骤

```powershell
# 1. 卸载 uv tool
uv tool uninstall code-review-graph

# 2. 删除项目内集成文件
Remove-Item -Recurse -Force tools/code-review-graph
Remove-Item -Force .mcp.json              # 仅当无其他 MCP server 时
Remove-Item -Force CLAUDE.md              # 仅当无其他 CLAUDE 内容时
Remove-Item -Recurse -Force .code-review-graph   # 图谱数据

# 3. 手动清理 .gitignore 末尾 "# Added by code-review-graph" 段
```

## 六、与既有工具的协作

| 既有工具 | 关系 | 说明 |
|---------|------|------|
| `graphify` (47.7MB graph.json) | 互补 | graphify 偏向一次性全局快照，code-review-graph 支持增量更新和 MCP 实时查询 |
| `codebase-memory-mcp` (273MB) | 互补 | codebase-memory 偏向语义检索/记忆，code-review-graph 偏向结构化关系/调用图 |
| `pytest` 测试金字塔 | 协作 | `query pattern="tests_for"` 可快速定位某模块的测试覆盖 |

## 七、注意事项

1. **首次 build 耗时较长**：5575 个 Python 文件，首次 build 约 3-10 分钟，请耐心等待
2. **磁盘占用**：图谱数据约 50-200MB，存于 `.code-review-graph/`（已 gitignore）
3. **代理环境**：安装时需配置 `HTTPS_PROXY=http://127.0.0.1:7897`
4. **不影响生产**：本工具仅用于开发时代码探索，不进入交易决策链路
5. **Python 3.8.9 主环境兼容**：通过 `uv tool` 隔离安装，使用独立 Python 3.11+ 环境，不污染主环境

---

**集成日期**：2026-07-26
**版本**：v2.3.7
**集成者**：自动化集成脚本
