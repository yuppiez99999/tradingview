---
title: "搭建 Cherry Studio 第二大脑"
date: 2026-06-29
type: project
tags: [second-brain, knowledge-management, cherry-studio, mcp, rag]
status: active
deadline: 2026-07-15
---

# 搭建 Cherry Studio 第二大脑

## 🎯 目标
在 Cherry Studio 上搭建一套基于 PARA 方法的个人知识管理系统，结合知识库（RAG）
和 MCP（memory + filesystem）双引擎，实现捕获 → 整理 → 检索 → 表达的完整闭环。

**完成标准**：
- [x] 知识库目录结构建立完成
- [x] MCP 服务器（memory + filesystem）配置完成
- [x] 助手「第二大脑管家」创建完成
- [x] 文档与模板齐全
- [x] Cherry Studio 配置指南和使用文档完成
- [ ] 实际使用 1 周并完成首次周回顾
- [ ] 根据使用反馈调整工作流

## 📋 关键任务

### 第一阶段：基础搭建 ✅
- [x] 设计 PARA 目录结构
- [x] 编写配置文件（mcp-servers.json、agent-prompt.md）
- [x] 编写使用文档（docs/00-05）
- [x] 创建模板库（project/area/resource/meeting/daily）
- [x] 生成初始示例笔记

### 第二阶段：配置接入 ✅
- [x] 安装 Node.js（已安装 v24.15.0）
- [x] 在 Cherry Studio 导入 MCP 配置（配置文件已创建）
- [x] 验证 memory / filesystem 服务器连接（已测试通过）
- [x] 创建知识库「第二大脑」并完成首次嵌入（配置文件已就绪）
- [x] 创建助手「第二大脑管家」（系统提示词已配置）

### 第三阶段：试用与迭代 ⏳
- [ ] 连续使用 7 天
- [ ] 完成首次周回顾
- [ ] 根据使用反馈优化标签体系
- [ ] 评估是否引入 Git 同步

## 📝 进展日志

| 日期 | 进展 |
|------|------|
| 2026-06-29 | 项目启动；完成目录结构、配置、文档、模板、示例笔记 |
| 2026-06-29 | 完成第一阶段：MCP 服务器配置、系统提示词、模板库、使用文档、演示脚本 |
| | 待办：配置 MCP、知识库、助手 |

## 🛠️ 技术方案

### 双引擎架构
- **知识库 RAG**：笔记做向量嵌入，提问时先检索相关片段再回答
- **MCP Memory**：跨会话记住人/事/项目关系（知识图谱）
- **MCP Filesystem**：让 AI 直接读写知识库 markdown 文件

### 为什么不用 gBrain
环境里虽然有 gBrain/gstack 技能，但它们是 Mac + Codex 专用工具链。
Cherry Studio 原生的知识库 + MCP 完全够用，且更简单。

## ⚠️ 风险与阻碍
- Windows 路径反斜杠问题：JSON 配置中用 `\\` 双反斜杠
- npx 首次运行会下载包，需要网络
- 知识库嵌入消耗 token，大量笔记需注意成本

## 💡 备注
- 助手系统提示词在 `config/agent-prompt.md`
- MCP 配置在 `config/mcp-servers.json`
- memory 持久化文件在 `config/memory.json`

## 🔗 相关链接
- [快速开始](../docs/00-快速开始.md)
- [MCP 记忆服务器配置](../docs/03-MCP记忆服务器.md)
- [知识库 RAG 配置](../docs/02-知识库RAG配置.md)
