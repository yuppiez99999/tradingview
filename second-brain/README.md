# 第二大脑 (Second Brain) — Cherry Studio 知识管理系统

基于 **PARA 方法**（Projects / Areas / Resources / Archives）构建的个人知识管理系统，
运行在 Cherry Studio 之上，结合 **知识库（RAG）** + **MCP 记忆服务器** 双引擎。

---

## 📁 目录结构

```
second-brain/
├── README.md                  # 本文件 —— 项目总览
├── 0-Inbox/                   # 📥 收件箱：所有新内容入口（待整理）
├── 1-Projects/                # 📌 项目：有明确目标和截止日期
├── 2-Areas/                   # 🌐 领域：长期维护的责任范围
│   ├── 健康/  财务/  职业/  技能/
├── 3-Resources/               # 📚 资源：感兴趣的主题参考资料
│   ├── 技术笔记/  读书笔记/  文章剪藏/  工具清单/
├── 4-Archives/                # 🗄️ 归档：已完成或不再活跃
├── Daily/                     # 📅 每日笔记
├── Templates/                 # 📋 模板库
├── config/
│   ├── mcp-servers.json       # ⭐ Cherry Studio MCP 服务器配置
│   ├── memory.json            # MCP memory 服务器持久记忆（自动生成）
│   └── agent-prompt.md        # ⭐ 第二大脑助手系统提示词
└── docs/
    ├── 00-快速开始.md          # ⭐ 新手必读
    ├── 01-PARA分类法.md
    ├── 02-知识库RAG配置.md
    ├── 03-MCP记忆服务器.md
    ├── 04-日常工作流.md
    └── 05-元数据与标签标准.md
```

## 🚀 三步快速开始

1. **配置 MCP 记忆服务器** → 见 [`docs/03-MCP记忆服务器.md`](docs/03-MCP记忆服务器.md)
2. **配置知识库 RAG** → 见 [`docs/02-知识库RAG配置.md`](docs/02-知识库RAG配置.md)
3. **导入助手提示词** → 见 [`config/agent-prompt.md`](config/agent-prompt.md)

完全零基础？直接看 [`docs/00-快速开始.md`](docs/00-快速开始.md)。

## 🧠 核心理念（CODE 法则）

| 概念 | 说明 | 对应位置 |
|------|------|----------|
| **捕获 Capture** | 所有灵感、剪藏、想法先丢进 Inbox，不分类 | `0-Inbox/` |
| **整理 Organize** | 定期把 Inbox 内容分到 PARA 四类 | `1/2/3/` |
| **提炼 Distill** | 用 AI 总结、提取要点、建立链接 | AI + 模板 |
| **表达 Express** | 把知识转化为输出（写作、决策、分享）| 任意 |

详细工作流见 [`docs/04-日常工作流.md`](docs/04-日常工作流.md)。

## 🔧 技术栈

- **Cherry Studio**：AI 对话客户端，提供 RAG 知识库 + MCP 集成
- **MCP Memory Server**：跨会话持久记忆（知识图谱）
- **MCP Filesystem Server**：让 AI 读写本知识库文件
- **Markdown + Frontmatter**：纯文本笔记，可被任意工具打开、Git 同步

## 📊 统计

- 创建日期：2026-06-29
- 笔记格式：Markdown + YAML frontmatter
- 同步建议：可用 Git 私有仓库做跨设备同步
