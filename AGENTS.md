# 终极量化交易系统 v8.6.14 协作规则

> 本项目使用 Project Cairn 组织项目知识：`AGENTS.md` 是规则与导航入口，`cairn/` 是项目知识/状态层。
> 同目录下的 `CLAUDE.md` 应仅包含一行 `@AGENTS.md`，让 Claude Code 读取相同规则；Codex 直接读取本文件。

## 项目一句话定位

A股量化交易系统 — 多因子选股、LightGBM增强训练、策略回测、风控管理、全栈数据采集与Alpha研究

> 本文件由 `cairn init` 生成，已填入本项目自身的定位与 provider 配置；其他项目应先运行自己的 init 再复用。

## 初始化配置

- 毕业 provider(s)：暂缓对接（首次毕业时连接知识库）
- 知识库索引：尚未配置
- 毕业目标：尚未配置

## 进入项目后的阅读顺序

1. 先读本文件（AGENTS.md）。
2. 若 `cairn/ROADMAP.md` 存在，读路线图、当前焦点与开放问题（ROADMAP 为可选；最小化初始化的项目可能没有）。
3. 读 `cairn/LOG.md` 最近条目（最新在顶部），了解近期进展与关键决策。
4. 按当前任务需要读相关 `cairn/` 知识专题文档。
5. 若需探索代码库，优先使用 code-review-graph MCP 工具（见 `cairn/code-review-graph-guide.md`）而非直接 Grep/文件扫描。

## 文档职责

| 文件 | 职责 | 维护方式 |
|---|---|---|
| `AGENTS.md`（根目录） | 规则与导航 | 极少变动，≤ 60 行 |
| `CLAUDE.md`（根目录） | 单行 `@AGENTS.md` 存根 | 写入一次，之后不动 |
| `cairn/ROADMAP.md` | 路线图与进度 | 原位更新，保持简洁 |
| `cairn/LOG.md` | 按时间顺序日志 | 新条目追加在顶部（最新在前），每条 ≤ 20 行，仅摘要 + 指针 |
| `cairn/<topic>.md` | 知识专题文档（当前真相） | 原位更新；踩坑记录放在正文 section 中，用 `contains` 打标签；修订需附 LOG 指针 |
| `cairn/Reference/` | 外部原始输入 | 按需创建；仅追加 |
| `cairn/Cited.md` | 知识库引用列表 | 仅指针，不复制原文 |

## 外部资源 / GitHub 生态集成索引

> 高价值开源项目筛选与接入规划，已纳入 `docs/` 体系（与 `docs/GitHub生态集成可行性研究_2026-08-06.md`、`docs/GitHub生态集成升级计划_2026-08-06.md` 同族归档）。

| 文件 | 职责 |
|---|---|
| `docs/高价值GitHub项目清单_20260809.md` | 29 个高价值 GitHub 项目筛选清单（按量化/数据源/舆情/Agent/垂直场景分类） |
| `docs/高价值GitHub项目清单.json` | 上述清单的结构化单一事实源（由 `高价值GitHub项目清单.xlsx` 导出，可版本化、可被代码引用） |
| `docs/高价值项目接入落地指南_20260809.md` | 把清单项目逐一映射到 28 系统真实模块的可执行接入方案（含 vnpy/duckdb/openbb/情感信号/langchain 等） |
| `高价值GitHub项目清单.xlsx`（根目录，源数据） | 清单原始数据，保持不动；JSON 为其派生事实源 |

> 其他一切仅在具体信号触发时创建（需要记录决策、解决了踩坑、目标跨越多次会话），不预建空壳。工程类资产（合约/配置/规约等由代码或流程消费的）不受本系统管理，属于代码树而非 `cairn/`。

## 冲突仲裁规则

- 优先级：**知识专题文档 > LOG 历史**；规则层面冲突由本文件裁定。
- 业务/设计结论以 `cairn/` 知识专题文档中的最新记录为准，不以较早的 LOG 条目为准。

## 知识库消费反射

- 在执行其可复用内核（任何产生或依赖的结论）够格毕业的工作之前，先查阅本项目的 `cairn/` 知识专题文档；尚未连接外部知识库（provider 暂缓 — 见上方"初始化配置"），外部索引检查与 `cairn/Cited.md` 引用在连接后激活。

## 文档协作规则

- 在修改之前，先判断用户想要的是"讨论/建议"还是"直接改文档"；当用户说"先看看/先评估"时，先给分析 — 不要直接改写正式文档。
- 修正过往判断时，追加更正注记；不要静默覆盖。
- 不要把未确认的判断写成已定论的事实。

## 知识沉淀规则

- 每个实质性推进步骤后，在 `cairn/LOG.md` 顶部追加一条（摘要 + 指针）；让结论沉淀为 `cairn/` 知识专题文档。
- 跨项目可复用的经验通过毕业机制沉淀（provider 暂缓 — 见上方"初始化配置"）。
- 开发工作流自动化（Wave 0）：Claude Code Hooks（`.claude/settings.json`）+ pre-commit `forbid-p0-risk` 自动执行质量检查与 cairn 上下文加载；claude-mem 与 cairn 协调原则为"**结论走 cairn，过程走 claude-mem**"；详见 `cairn/dev-workflow-automation.md`。

## Agent-Skills 适配层

- 通用工程 skill（Addy Osmani 的 24 个）已装于 `~/.codebuddy/skills/agent-skills/`。
- 项目级桥接与量化专属 DoD：`skills/AGENT_SKILLS_ADAPTER.md`（把通用 skill 映射到本系统门禁脚本与领域铁律，勿改写通用 skill 本体）。
