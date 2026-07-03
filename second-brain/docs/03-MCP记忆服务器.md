# MCP 记忆服务器配置

> MCP Memory Server 让 AI **跨会话记住**你告诉它的事，形成持久记忆。

---

## 什么是 MCP Memory？

普通 AI 对话：关掉会话，AI 就忘了一切。

MCP Memory：你告诉 AI 的事存入本地 JSON 文件（知识图谱），下次新会话自动加载。
AI 记住的是**实体**（人、项目、概念）和**关系**（张三→负责→项目A），不是原始对话。

---

## 前置条件

确认本机已安装 **Node.js**：
- 下载：https://nodejs.org → LTS 版本
- 验证：命令行运行 `node --version` 和 `npx --version` 都能输出版本号

---

## 配置步骤

### 方式一：JSON 一键导入（推荐）

1. Cherry Studio → 左下角 **设置 ⚙️** → **MCP 服务器**
2. 切换到 **编辑 JSON** 模式
3. 把 `config/mcp-servers.json` 的内容整体粘贴进去
4. 保存
5. 等待状态变为 **绿色 ✓ 已连接**

### 方式二：手动逐个添加

#### 添加 Memory 服务器

1. 点 **+ 添加服务器**
2. 名称：`memory`
3. 命令：`npx`
4. 参数：`-y`（第一行），`@modelcontextprotocol/server-memory`（第二行）
5. 环境变量：
   - 键：`MEMORY_FILE_PATH`
   - 值：`C:\Users\Administrator\ZCodeProject\second-brain\config\memory.json`
6. 保存

#### 添加 Filesystem 服务器

1. 点 **+ 添加服务器**
2. 名称：`filesystem`
3. 命令：`npx`
4. 参数：`-y`（第一行），`@modelcontextprotocol/server-filesystem`（第二行），`C:\Users\Administrator\ZCodeProject\second-brain`（第三行）
5. 保存

---

## 在对话中启用

MCP 服务器配好后，还需要在对话中**打开工具开关**：

- 新建对话 → 底部工具栏 → 找到 `memory` 和 `filesystem` → 点亮开关
- 或在助手设置里**默认启用**这两个 MCP 工具

---

## Memory 能做什么

| 操作 | 说明 | 示例 |
|------|------|------|
| 创建实体 | 记住人/项目/概念 | "记住：张三是我的同事" |
| 创建关系 | 记住实体间的关系 | "张三负责后端团队" |
| 搜索实体 | 查找之前记住的内容 | "张三是谁？" |
| 列出所有 | 看看 AI 记住了什么 | "列出你记住的所有人" |
| 删除 | 删除错误的记忆 | "忘掉 XXX" |

## Filesystem 能做什么

| 操作 | 说明 |
|------|------|
| 读取文件 | 读取知识库中的笔记内容 |
| 创建文件 | 新建笔记（带模板） |
| 修改文件 | 更新已有笔记 |
| 列出目录 | 浏览知识库结构 |
| 搜索文件 | 按名称查找笔记 |

---

## 验证是否正常

在对话中（确保 MCP 工具已启用）测试：

**测试 Memory：**
> "记住：我正在用 Cherry Studio 搭建第二大脑，今天是 2026-06-29"

然后**新开一个会话**问：
> "我最近在做什么？"
> → AI 应该能回答你在搭建第二大脑

**测试 Filesystem：**
> "列出 second-brain 目录下的所有文件"
> → AI 应该能看到你的知识库结构

---

## 常见问题

**Q: memory 服务器连接失败**
A: 在命令行手动运行 `npx -y @modelcontextprotocol/server-memory`，查看是否报错。
常见原因：Node.js 未安装、网络问题导致 npm 下载慢。

**Q: 新会话记不住上一会话的内容**
A: 确认 `MEMORY_FILE_PATH` 环境变量指向了正确的 `memory.json` 文件路径。
可以用文件管理器打开该路径，看文件大小是否在增长（有内容时会有几 KB）。

**Q: filesystem 服务器报权限错误**
A: 确认参数中的目录路径正确，且没有拼写错误。Windows 路径用双反斜杠 `\\`。

**Q: 两个 MCP 都显示已连接但 AI 不调用**
A: 在对话底部工具栏检查 MCP 工具开关是否已点亮。有时需要新建对话才能生效。

## 相关链接

- [00-快速开始](00-快速开始.md)
- [02-知识库RAG配置](02-知识库RAG配置.md) — Memory 的搭档
- [04-日常工作流](04-日常工作流.md) — 日常使用方式
