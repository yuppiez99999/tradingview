# Copilot Max（国外模型） + 国内模型 开发分工报告

> **项目**：终极量化交易系统 v8.7（A股量化 / 多因子 / LightGBM / 策略回测 / 风控 / 全栈采集 / Alpha 研究）
> **目标**：在「GitHub Copilot Max 国外模型 + 国内模型不限预算」前提下，把系统开发到最完美
> **工具**：VS Code（原生 Copilot Max 驱动国外模型；BYOK 智能体扩展驱动国内模型）
> **日期**：2026-09-11
> **关联知识**：`cairn/llm-model-selection-20260831.md`（系统**运行时**多模型路由）；本报告讲的是**开发时**「谁写哪段代码」

---

## 0. 一句话结论

- **国外模型（Copilot Max）**负责"写错代价最高、跨文件最复杂、正确性是命脉、且重度依赖英文生态"的部分：机构管道、交易执行器、风控门、回测引擎、因子表达式引擎、ML 训练、代码审计/测试评审。
- **国内模型（不限预算，BYOK）**负责"体量大、中文金融语义重、吞吐高、实时链路、本地化"的部分：AI 决策链（GLM-5）、宏观/舆情/中文报告、因子批量实现、脚本自动化、UI 中文页、实时信号代码。
- **黄金法则**：国内模型产出量，国外模型把最后一道关（对关键路径做 review / security audit / 单测补全）。两者都"用满"——国外用在刀刃，国内用在体量。

> ⚠️ 本文所有具体模型版本（Claude Opus 4.5 / GPT-5.1 / Gemini 2.5 Pro / GLM-5.3 / DeepSeek V4 等）为**截至 2026-09 的已知状态**，Copilot Max 可选项与国内模型版本会按月刷新，落地前请在 VS Code 模型选择器与各家控制台核对当前可用型号（见第 10 节"需核实项"）。

---

## 1. 模型武器库（能力矩阵）

### 1.1 国外模型（GitHub Copilot Max 可选项，原生接入 VS Code）

| 模型 | 核心优势 | 在本系统的最佳用场 |
|---|---|---|
| **Claude Opus 4.5**（Anthropic） | 超长上下文、复杂多文件重构、Agent 工具调用、代码评审/安全审计最强 | 交易执行器、风控门、管道重构、统一入口、安全审计、最终 review |
| **GPT-5.1 / 5.2**（OpenAI） | 算法与数值正确性、数学推导、广博知识、单测生成质量高 | 因子表达式引擎、回测数值内核、LightGBM 训练、执行算法（TWAP/VWAP/冰山/POV）、技术债修复 |
| **Gemini 2.5 Pro / 3.0 Pro**（Google） | 100 万+ 上下文窗口、跨超大文件长程一致性、多模态 | 一次性通读 108KB 管道 / 63KB 执行器 / 242K utils、qlib 大型 vendored 代码理解、跨仓重构 |
| （xAI Grok / Mistral 等为备选，非主力） | — | 仅在主力模型限流时兜底 |

### 1.2 国内模型（BYOK，OpenAI 兼容端点，不限预算）

| 模型 | 核心优势 | 在本系统的最佳用场 |
|---|---|---|
| **智谱 GLM-5.3 / 5.4** | 工程智能体最强（工具调用/长链路编排）、中文金融语义好；系统已原生集成 | AI 决策（GLM-5 盘中）、AI Hedge Fund 20 分析师 LangGraph、复杂 Agent 编排 |
| **DeepSeek V4 Pro / V3.2** | 通用代码质量高、吞吐大、成本极低 | 因子批量实现、research 批量研究、scripts 自动化、utils 胶水代码 |
| **DeepSeek-R1 / R2**（推理） | 数学与逻辑深度思考 | 新因子假设→数学表达、收益归因、定价公式推导、回测方法论 |
| **阿里 Qwen3-Max / 3.5** | 长上下文 + 中英均衡 + 中文好 | 中文 UI 页、中文报告模板、宏观十五五/社保 ETF、跨大文件中文重构 |
| **字节 豆包（Doubao Seed）** | 快、便宜、中文流畅 | 高吞吐量脚手架、日志/监控、中文运维脚本、批量注释与文档 |
| **Kimi（Moonshot K2/K3）** | 超长上下文窗口 | 通读 utils(242K)/qlib(75K)、长文档代码理解、大文件归因 |

> 与 `cairn/llm-model-selection-20260831.md` 的呼应：该文档定义的是**系统运行时**路由（GLM 工程 / R1 推导 / V3 实时 / V4 批量）。本报告把同一套"能力画像"映射到**开发时**的"谁生成谁的源码"，逻辑一致、层次不同。

---

## 2. 系统模块规模盘点（分配依据）

来自 `项目架构图_v8.7.architecture.json` + 全仓 LOC 统计（共 **2,181 个 .py / ≈740K 行**）：

| 子系统 | 代码行 | 文件数 | 性质 |
|---|---:|---:|---|
| utils（工具/胶水/LLM 路由） | 242K | 552 | 量大、杂 |
| tests（测试） | 242K | 644 | 量大、质量关键 |
| qlib（vendored 依赖） | 75K | 335 | **第三方，勿重写** |
| scripts（自动化） | 57K | 203 | 量大、运维向 |
| ms_strategy | 25K | 84 | 策略实现主体 |
| quant_modules | 22K | 73 | 因子/模块主体 |
| ai_decision | 9K | 23 | AI 对冲基金 LangGraph |
| backtests | 5.4K | 16 | 数值正确性敏感 |
| cli（统一入口） | 5.8K | 45 | 多模式编排 |
| lgb_trainer | 4.2K | 11 | ML 正确性敏感 |
| data | 4.2K | 9 | 采集/降级链 |
| external | 8.2K | 34 | 外部 SDK 对接 |
| ui（Streamlit 17 页） | 6.9K | 32 | 中文 UI |
| reporting | 2.9K | 8 | 中文报告 |
| executor（实盘指令） | 1.1K | 2 | **资金相关，最高危** |
| 风控/对冲/信号/宏观/research/nlp/… | 其余 | — | 见下 |

---

## 3. 核心分配原则（5 条决策规则）

1. **正确性致命度优先**：涉及"真金白银 / 一刀毙命"的代码（执行器、风控门、回测内核、因子表达式求值）一律国外模型主笔 + GPT/Claude 双审。
2. **跨文件复杂度优先**：需要同时改 ≥5 个文件、或依赖 100K+ 行上下文的重构，交给 Claude Opus / Gemini（长上下文）。
3. **英文生态优先国外**：对接 Wind/TDX/AKShare/qlib/stumpy/pyfolio/empyrical 等西方库与 SDK 时，国外模型对 API 与惯用法更准。
4. **中文语义 / 体量优先国内**：A股规则、合规、中文报告、中文 UI、批量脚手架、实时信号——国内模型更贴领域且无限预算可放量。
5. **国外收口质量门**：国内模型生成的"关键路径代码"必须过一道国外模型 review（Claude Opus 安全审计 / GPT-5.1 单测补全），否则不入主干。

---

## 4. 逐模块分配矩阵（谁写哪部分）

> 标注：**[外]**=Copilot Max 国外模型主笔；**[国]**=国内模型主笔；**首选 → 备选**。

| # | 模块 / 文件 | 规模 | 首选模型 | 备选 | 任务类型 | 理由 |
|---|---|---:|---|---|---|---|
| 1 | **机构管道 pipeline（EOD 108KB）** | 极大 | **[外] Gemini 2.5 Pro**（通读）→ **Claude Opus**（重构） | GPT-5.1 | 重构/修复 | 单文件巨大，需 100 万上下文长程一致性；改错影响全链路 |
| 2 | **交易执行器 executor（63KB 实盘指令）** | 高危 | **[外] Claude Opus 4.5** | GPT-5.1 | 编写+审计 | 资金相关、零容错；Opus 工具调用与安全直觉最强 |
| 3 | **执行算法引擎（TWAP/VWAP/冰山/POV）** | 中 | **[外] GPT-5.1** | Claude Opus | 编写 | 数值/调度算法正确性敏感 |
| 4 | **风控层（止损/回撤/VaR/熔断/Kill Switch）** | 高危 | **[外] Claude Opus + GPT-5.1 双审** | — | 编写+审计 |  catastrophic-if-wrong；必须安全级 review |
| 5 | **统一入口 v8.7（CLI 35+ 模式）** | 5.8K | **[外] Claude Opus**（dispatcher/异常处理） | GPT-5.1 | 编写 | 多模式编排、参数解析鲁棒性 |
| 6 | **Streamlit UI 17 页（暗色主题）** | 6.9K | **[国] Qwen3-Max / GLM-5.3** | 豆包 | 编写 | 中文 UI 文本、批量页面、视觉一致性 |
| 7 | **Alpha 因子引擎（表达式求值/GTJA191）** | 核心 | **[外] GPT-5.1**（求值器正确性） | Claude Opus | 编写+评审 | 表达式引擎算错=因子全废；数学正确性第一 |
| 8 | **因子批量实现（quant_modules 73 + ms_strategy 84）** | 47K | **[国] DeepSeek V4 Pro / GLM-5.3** | 豆包 | 批量编写 | 体量大、模式重复；国内无限预算放量 |
| 9 | **新因子推导（假设→数学）** | — | **[国] DeepSeek-R1**（推理） | GPT-5.1 | 推导 | 深度思考出因子数学表达式 |
| 10 | **信号融合 + SAX motif（stumpy）** | 中 | **[国] DeepSeek-R1**（motif 数学）+ **[外] Claude Opus**（集成） | GPT-5.1 | 混合 | 数学用 R1，集成用 Opus |
| 11 | **AI 决策（GLM-5 盘中）** | 中 | **[国] GLM-5.3** | — | 编写 | 系统本就建于 GLM；中文盘中研判 |
| 12 | **AI Hedge Fund 20 分析师 LangGraph** | 9K | **[国] GLM-5.3**（编排）+ **[外] Claude Opus**（图架构/review） | GPT-5.1 | 混合 | GLM 工具编排最强；图结构由 Opus 收口 |
| 13 | **对冲再平衡 五阶段 v5.9** | 中 | **[外] Claude Opus + GPT-5.1** | — | 编写 | 复杂状态机，跨文件协同 |
| 14 | **数据层 7 级降级链（Wind→TDX→AKShare→sina→缓存→兜底）** | 12K | **[外] Claude/GPT**（Wind/TDX SDK）+ **[国] GLM/Qwen**（AKShare/sina 中文源） | — | 混合 | 西方 SDK 用国外；中文源用国内 |
| 15 | **外部源对接 external（34 文件）** | 8.2K | **[外] Claude Opus** | GPT-5.1 | 编写 | 第三方库 API 惯用法 |
| 16 | **气象/舆情（Open-Meteo/RSS/trafilatura）** | 小 | **[外] GPT-5.1** | — | 编写 | 西方库，英文文档 |
| 17 | **lgb_trainer（LightGBM 训练）** | 4.2K | **[外] GPT-5.1 / Claude Opus** | — | 编写 | ML 正确性敏感 |
| 18 | **qlib（75K vendored）** | 第三方 | **不重写**；读改用 **[外] Gemini / Kimi**（长上下文） | — | 只读理解 | 当依赖；仅打补丁时由长上下文模型通读 |
| 19 | **回测引擎 backtests（5.4K）** | 敏感 | **[外] GPT-5.1**（内核）+ **[国] DeepSeek-R1**（方法论） | Claude Opus | 编写+评审 | 回测有 bug=虚假信心；数值内核必须国外 |
| 20 | **research（14K 研究脚本）** | 中 | **[国] DeepSeek V4 Pro**（批量）+ **[外] GPT-5.1**（方法） | — | 混合 | 量大用国内；方法论用国外 |
| 21 | **reporting + 中文报告 HTML（暗色）** | 2.9K | **[国] GLM-5.3 / Qwen3** | 豆包 | 编写 | 中文金融报告、古典书卷美学排版 |
| 22 | **宏观（康波/十五五/社保 ETF）** | 中 | **[国] GLM-5.3 / Qwen3** | — | 编写 | 中文宏观领域知识 |
| 23 | **nlp 舆情（1.6K）** | 小 | **[国] GLM-5.3 / Qwen3** | — | 编写 | 中文 NLP |
| 24 | **utils（242K 工具/LLM 路由）** | 极大 | 关键（router/llm client/risk util）→ **[外] Claude Opus**；其余胶水 → **[国] DeepSeek V4 / 豆包** | GPT-5.1 | 拆分 | 路由与资金相关用国外；杂项用国内放量 |
| 25 | **scripts（57K 自动化，203 文件）** | 极大 | **[国] 豆包 / DeepSeek V4 Pro** | GLM-5.3 | 批量 | 运维向、高吞吐、中文；无限预算放量 |
| 26 | **tests（242K，644 文件）** | 极大 | 关键路径单测 → **[外] Claude Opus / GPT-5.1**；批量脚手架 → **[国] DeepSeek V4** | — | 混合 | 国外补关键单测+找 bug；国内铺量 |
| 27 | **安全审计 / 代码质量 / 重构** | — | **[外] Claude Opus 4.5** | GPT-5.1 | 审计 | 安全/质量 review 国外模型最强 |
| 28 | **cairn 知识层 / AGENTS.md / 文档** | — | **[外] Claude Opus**（架构文档）+ **[国] GLM-5.3**（中文文档） | — | 混合 | 设计精确用 Opus；中文沉淀用 GLM |

---

## 5. 关键路径特别说明

- **管道（#1）+ 执行器（#2）+ 风控（#4）**是"资金三件套"，必须由国外模型主笔且双模型评审，绝不用国内模型单写后直接合入。
- **回测（#19）**是"信心三件套"之一：回测引擎的数值正确性决定整套策略是否可信，国外模型负责内核，R1 负责方法论辩护。
- **因子表达式引擎（#7）**是"alpha 之源"：求值器一处符号错误会污染全部因子，GPT-5.1 主笔 + Claude 评审。
- **AI 决策（#11/#12）**是系统对外标榜的差异化能力，已深度绑定 GLM-5，保持国内主笔、国外收口图架构，延续既有优势。

---

## 6. VS Code 开发环境配置

### 6.1 国外模型：原生 GitHub Copilot Max

1. 安装 **VS Code**，扩展市场装 **GitHub Copilot** + **GitHub Copilot Chat**（含 Agent Mode）。
2. 订阅 **Copilot Max** 套餐（解锁 Claude Opus / GPT-5.x / Gemini 2.5 Pro 等 premium 模型）。
3. 开启 Agent Mode（`Ctrl+Alt+I` 或在聊天面板切到 Agent），用**模型选择器**按第 4 节矩阵逐任务切换模型。
4. 关键路径任务在 prompt 中显式点名模型职责，例如：_"用 Claude Opus 重构 executor 的实盘指令生成，保持风控门接口不变"_。

### 6.2 国内模型：BYOK 智能体扩展（推荐路径）

原生 Copilot 对"任意国产 OpenAI 兼容端点"的 BYOK 支持有限且随版本变动；**最稳妥、可控、能真正'不限预算'的做法**是用 BYOK 型智能体扩展驱动国产模型：

- 安装 **Cline** 或 **Roo Code**（VS Code 扩展），它们原生支持 OpenAI 兼容 `baseUrl + apiKey + modelId`。
- 在扩展配置中为每个国产模型建一个 profile（示例端点，均为 OpenAI 兼容）：

| 模型 | baseUrl（OpenAI 兼容） | 典型 modelId |
|---|---|---|
| 智谱 GLM-5.3 | `https://open.bigmodel.cn/api/paas/v4` | `glm-5.3` |
| DeepSeek V4 / R1 | `https://api.deepseek.com/v1` | `deepseek-chat` / `deepseek-reasoner` |
| 阿里 Qwen3-Max | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` / `qwen-max` |
| 字节 豆包 | `https://ark.cn-beijing.volces.com/api/v3` | `doubao-seed-xxx` |
| Kimi | `https://api.moonshot.cn/v1` | `moonshot-v1-128k` |

- 工作流：在 Cline/Roo 里选对应 profile 跑"批量/中文/实时"任务；在原生 Copilot（Max）里跑"架构/风控/审计"任务。两者同处一个 VS Code 工作区，按模块切换。

> 注：若坚持用**原生 Copilot BYOK**，其配置键名（`settings.json` 中的 provider 数组）随版本变化，落地前请以当前 VS Code 文档 `code.visualstudio.com/docs/copilot/language-models` 为准（本次因检索限流未能实时核对键名，已在第 10 节标注）。

### 6.3 双轨质量门（workflow 设置建议）

- 在 `.github/copilot-instructions.md` 追加本报告的"分配矩阵"摘要，让 Copilot 每次生成都对齐责任模型。
- 关键路径（#1/#2/#4/#7/#19）的 PR 必须附：**Claude Opus 安全审计摘要 + GPT-5.1 单测覆盖说明**，否则不合并。
- 国内模型产出的关键文件，提交前用 Copilot（Max）跑一轮 _"/review 这段代码，找资金与边界风险"_。

---

## 7. 分阶段推进计划（Wave）

| 阶段 | 重点（模型） | 交付 |
|---|---|---|
| **Wave A：止血与加固** | 风控/执行器/回测（**[外]** Claude+GPT 双审） | 资金三件套 + 回测内核通过安全审计 |
| **Wave B：架构重构** | 管道通读重构（**[外]** Gemini→Opus）、统一入口（Opus） | 巨型文件可维护化 |
| **Wave C：因子与 AI** | 表达式引擎（GPT）、因子批量（**[国]** DeepSeek/GLM）、AI 决策（GLM） | alpha 产能提升 |
| **Wave D：数据与报告** | 降级链（混合）、中文报告/宏观/舆情（**[国]** GLM/Qwen） | 全链路中文语义闭环 |
| **Wave E：测试与审计** | 关键单测（**[外]**）+ 批量测试（**[国]**）+ 全仓审计（Opus） | 覆盖率与质量达标 |

---

## 8. 风险与边界

- **上下文限制**：Copilot Max 单请求上下文有限；通读 utils(242K)/qlib(75K) 用 Gemini/Kimi 长上下文或分片检索，不要整文件塞给 Opus。
- **版本漂移**：Copilot Max 可选项与国产模型版本月度更新，矩阵中的型号名为 2026-09 快照，需定期复核。
- **安全**：执行器/风控/密钥相关代码**绝不**交给未审计的国产云端模型生成后直合；国外模型收口是硬性质量门。
- **成本纪律（虽不限预算）**：国外模型按"刀刃"用，国内无限预算用在"体量"，避免把 57K scripts 也拿 Opus 重写——性价比低且无质量增益。

---

## 9. 结论

把"国外模型用满在正确性致命、跨文件复杂、英文生态、质量收口"四类和"国内模型不限预算用在中文语义、体量、实时、本地化"四类，组合成"**国产放量生产 + 国外把最后一道关**"的双轨开发流，即可在 Copilot Max + 国产不限预算的条件下，把本系统推向最完美状态。分配矩阵（第 4 节）即为逐模块的落地执行清单。

---

## 10. 需核实项（落地前确认）

1. **Copilot Max 当前可选模型清单**：在 VS Code 模型选择器确认 Claude / GPT / Gemini 的具体在售版本号。
2. **原生 Copilot BYOK 配置键名**：以 `code.visualstudio.com/docs/copilot/language-models` 当前文档为准（本次检索限流，未能实时核对 `settings.json` provider 数组字段名）。若原生 BYOK 不稳，按 6.2 用 Cline/Roo 兜底。
3. **国产模型最新型号与端点**：智谱/DeepSeek/阿里/字节/Kimi 控制台核对 modelId 与 baseUrl（表中为常见值）。
4. **qlib 是否仍需 vendored**：若可改为 pip 依赖，删除 75K vendored 代码，释放维护面（属 Wave B 决策）。
