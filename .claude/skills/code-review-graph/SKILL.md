---
name: code-review-graph
description: 持久化增量代码知识图谱工具（基于 tree-sitter + SQLite）。当需要在大型代码库中探索模块关系、查找调用方/被调用方、分析变更影响半径、查找死代码、做代码审查时使用。优先于 Grep/Glob，节省 token 并提供结构化上下文（callers / callees / imports / tests / dependents）。本项目已配置 MCP server，工具会自动可用。
version: 2.3.7
---

# code-review-graph Skill

## 何时使用

- **探索陌生模块**：查找某函数/类的所有调用方、被调用方、导入关系
- **变更影响分析**：修改某文件前，先查 blast radius（影响半径）
- **代码审查**：审查 PR/commit 前，用 `detect_changes_tool` 获取风险评分
- **查找死代码**：找出没有被任何地方调用/测试的函数和类
- **架构理解**：查看模块社区划分、依赖关系总览
- **重构规划**：重命名前查找所有引用点、判断是否安全

## 何时不用

- 简单的字符串字面量搜索（用 Grep 更快）
- 单文件内部跳转（用 Read 即可）
- 图谱未构建时（先执行 `code-review-graph build`）

## 优先级规则

**本项目已配置 code-review-graph MCP server，调用 MCP 工具优先于 Grep/Glob/Read。**

理由：
1. **省 token**：图谱查询返回结构化关系，不需要读取整个文件
2. **给上下文**：自动提供 callers / callees / tests 等关系
3. **快**：SQLite 索引查询远快于全项目 Grep

## 核心工具

### 探索类

| MCP 工具 | 用途 |
|---------|------|
| `semantic_search_nodes_tool` | 按名称/关键词查找节点（替代 Grep） |
| `query_graph_tool` | 查询关系（callers_of / callees_of / imports_of / tests_for / dependents_of） |
| `get_architecture_overview_tool` | 架构总览 |
| `list_communities_tool` | 列出代码社区（模块聚类） |
| `get_architecture_overview_tool` | 高层架构图 |

### 审查类

| MCP 工具 | 用途 |
|---------|------|
| `detect_changes_tool` | 代码审查时给风险评分 |
| `get_review_context_tool` | 获取审查上下文（token 高效） |
| `get_impact_radius_tool` | 变更影响半径分析 |
| `get_affected_flows_tool` | 受影响的执行路径 |

### 重构类

| MCP 工具 | 用途 |
|---------|------|
| `refactor_tool` | 重构规划（重命名、提取函数） |
| `dead-code` (CLI) | 查找死代码 |

## CLI 命令

```powershell
# 全量构建（首次）
code-review-graph build

# 增量更新（后续）
code-review-graph update

# 状态查看
code-review-graph status

# 查询
code-review-graph query "callers_of:SignalFusionEngine.fuse"
code-review-graph query "callees_of:KillSwitch.check_margin_status"
code-review-graph query "tests_for:utils/signal_fusion.py"

# 影响分析
code-review-graph impact utils/signal_fusion.py

# 死代码
code-review-graph dead-code --lang python

# 可视化
code-review-graph visualize    # 生成 HTML
code-review-graph wiki         # 生成 markdown wiki
```

## 典型工作流

### 场景 1：修改 KillSwitch 前的影响分析

```
1. get_impact_radius_tool(file="utils/kill_switch.py")
   → 返回所有调用方、受影响的测试、风险评分

2. query_graph_tool(pattern="callers_of", node="KillSwitch.check_margin_status")
   → 返回所有调用 check_margin_status 的位置

3. 修改代码

4. detect_changes_tool()
   → 返回本次变更的风险评分、建议检查的测试
```

### 场景 2：探索 SignalFusionEngine

```
1. semantic_search_nodes_tool(query="SignalFusionEngine")
   → 返回所有相关节点

2. query_graph_tool(pattern="callees_of", node="SignalFusionEngine.fuse")
   → 返回 fuse 方法调用的所有方法

3. get_architecture_overview_tool()
   → 查看模块在整体架构中的位置
```

### 场景 3：写测试前的覆盖检查

```
1. query_graph_tool(pattern="tests_for", node="utils/research_distiller.py")
   → 返回已有的测试文件

2. dead-code --lang python
   → 找出没有被测试覆盖的函数
```

## 项目特定提示

- **本项目代码库**：5575 个 Python 文件，约 60MB
- **图谱数据位置**：`.code-review-graph/`（已 gitignore）
- **MCP 配置位置**：`.mcp.json`
- **首次 build 耗时**：约 3-10 分钟
- **增量 update 耗时**：秒级（仅处理变更文件）

## 故障排查

### MCP 工具不可用

1. 检查 `.mcp.json` 是否存在且配置正确
2. 检查 `code-review-graph --version` 是否可执行
3. 重启 Claude Code / Trae-CN 让 MCP 配置生效

### 图谱为空

```powershell
code-review-graph status
# 若 nodes=0, 执行:
code-review-graph build
```

### 查询无结果

```powershell
# 用更宽泛的搜索
code-review-graph search "SignalFusion"

# 查看是否有该节点
code-review-graph query "SignalFusionEngine"
```

## 集成信息

- **集成日期**：2026-07-26
- **集成批次**：GitHub 周榜热门项目深度集成（第二批）
- **来源**：[tirth8205/code-review-graph](https://github.com/tirth8205/code-review-graph) (+4,791 ⭐ / 周)
- **版本**：v2.3.7
- **风险等级**：极低（仅开发时使用，不影响实盘交易）
