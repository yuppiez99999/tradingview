# AI 模型分工方案 — 国内外模型最优配置(终极版)

> **目标**: 用满 GitHub Copilot Max + 国内模型不限预算,把 v9.1 量化系统开发到最完美
> **生成时间**: 2026-09-11
> **依据**: 对系统 136+ 核心模块、配置、回测引擎、AI 对冲基金、风控链的深度扫描
> **合并来源**: `AI模型分工方案_国内外模型最优配置.md` + `CopilotMax_国内模型_开发分工报告_20260911.md`

---

## 目录

- [1. 核心分工原则](#1-核心分工原则)
- [2. 模型武器库(能力矩阵)](#2-模型武器库能力矩阵)
- [3. 系统模块规模盘点](#3-系统模块规模盘点)
- [4. 逐模块分配矩阵(完整执行清单)](#4-逐模块分配矩阵完整执行清单)
- [5. VS Code 多模型自动切换配置(可直接使用)](#5-vs-code-多模型自动切换配置可直接使用)
- [6. 验证策略与质量门](#6-验证策略与质量门)
- [7. 冲突解决机制](#7-冲突解决机制)
- [8. 成本-质量权衡矩阵](#8-成本-质量权衡矩阵)
- [9. 分阶段推进计划(Wave)](#9-分阶段推进计划wave)
- [10. 禁忌(违反即返工)](#10-禁忌违反即返工)
- [11. Prompt 模板库](#11-prompt-模板库)
- [12. 需核实项(落地前确认)](#12-需核实项落地前确认)
- [附录: 一句话总结](#附录-一句话总结)

---

## 1. 核心分工原则

### 1.1 能力对比

| 维度 | 国外模型 (Claude Sonnet 4 / GPT-5 via Copilot Max) | 国内模型 (Qwen-Max / GLM-5 / DeepSeek-V4) |
|------|-----------------------------------------------|----------------------------------------|
| **强项** | 复杂架构设计、设计模式、通用算法、代码重构、LangGraph 编排、英文文档 | 中国市场监管规则、Wind 接口细节、中文注释/文档、政策语义(十五五/社保)、A 股特有逻辑 |
| **弱项** | A 股特有规则(涨跌停/复权/交割)、中文政策语义、Wind MCP 细节 | 复杂多 agent 编排、大规模重构、泛化架构 |

### 1.2 五条决策规则

1. **正确性致命度优先**: 涉及"真金白银 / 一刀毙命"的代码(执行器、风控门、回测内核、因子表达式求值)一律国外模型主笔 + 双审
2. **跨文件复杂度优先**: 需要同时改 ≥5 个文件、或依赖 100K+ 行上下文的重构,交给 Claude Opus / Gemini(长上下文)
3. **英文生态优先国外**: 对接 Wind/TDX/AKShare/qlib/stumpy/pyfolio/empyrical 等西方库与 SDK 时,国外模型对 API 与惯用法更准
4. **中文语义 / 体量优先国内**: A 股规则、合规、中文报告、中文 UI、批量脚手架、实时信号——国内模型更贴领域且无限预算可放量
5. **国外收口质量门**: 国内模型生成的"关键路径代码"必须过一道国外模型 review(Claude Opus 安全审计 / GPT-5.1 单测补全),否则不入主干

---

## 2. 模型武器库(能力矩阵)

> **数据来源**: GitHub Copilot 模型选择器实际可用清单(2026-09-11 核对)
> **标注**: ⭐ = 系统推荐主力; 🧠 = 代码专用; ⚡ = 快速低成本; 📚 = 长上下文

### 2.1 国外模型(GitHub Copilot Max — 原生接入 VS Code)

#### 第一梯队:复杂架构/安全审计(资金相关,必须用)

| 模型 | 核心优势 | 在本系统的最佳用场 | 优先级 |
|------|---------|------------------|--------|
| **Anthropic Claude Opus 4.8** ⭐ | 最强推理、超长上下文、Agent 工具调用、安全审计最强 | 交易执行器、风控门、管道重构、安全审计、最终 review | P0 |
| **Anthropic Claude Opus 5** ⭐ | 最新一代 Opus,推理与代码质量再升级 | 同上,与 4.8 互为备选,关键路径双审 | P0 |
| **Anthropic Claude Sonnet 5** ⭐ | 新一代 Sonnet,均衡高效 | 日常主力编码(替代 4.6),架构重构、回测引擎 | P0 |
| **OpenAI GPT-5.6 Sol** ⭐ | 最新 GPT 旗舰,算法与数值正确性 | 因子表达式引擎、回测数值内核、LightGBM 训练、定价公式 | P0 |
| **OpenAI GPT-5.6 Terra** ⭐ | GPT 多模态旗舰,长上下文 | 通读 108KB 管道/63KB 执行器、跨仓重构 | P0 |

#### 第二梯队:代码专用/快速迭代(高频使用)

| 模型 | 核心优势 | 在本系统的最佳用场 | 优先级 |
|------|---------|------------------|--------|
| **OpenAI GPT-5.4** 🧠 | 高质量代码生成,技术债修复 | 因子批量实现、utils 胶水代码、scripts 自动化 | P1 |
| **OpenAI GPT-5.2-Codex** 🧠 | OpenAI 代码专用模型 | 纯代码任务、函数实现、单元测试生成 | P1 |
| **OpenAI GPT-5.3-Codex** 🧠 | Codex 最新版,代码理解力强 | 同上,与 5.2-Codex 互为备选 | P1 |
| **Microsoft MAI-Code-1.1-Flash** 🧠 | 微软代码专用,快速高质量 | 批量代码生成、函数补全、轻量重构 | P1 |
| **Anthropic Claude Sonnet 4.6** | 上一代 Sonnet,稳定可靠 | 日常编码主力,与 Sonnet 5 互为备选 | P1 |
| **Anthropic Claude Sonnet 4** | 成熟稳定,广泛验证 | 备选,当新型号限流时回退 | P2 |

#### 第三梯队:快速低成本/长上下文(批量任务)

| 模型 | 核心优势 | 在本系统的最佳用场 | 优先级 |
|------|---------|------------------|--------|
| **Anthropic Claude Haiku 4.5** ⚡ | 极快、极便宜、质量尚可 | 批量注释、日志生成、简单函数、文档字符串 | P2 |
| **OpenAI GPT-5 mini** ⚡ | 快速便宜,简单任务 | 简单函数、变量命名、单行补全 | P2 |
| **OpenAI GPT-5.4 mini** ⚡ | 新版 mini,更快 | 同上 | P2 |
| **Google Gemini 3.5 Flash** 📚 | 长上下文、快速 | 通读大文件、跨文件理解 | P2 |
| **Google Gemini 3.6 Flash** 📚 | 更新版本 | 同上 | P2 |
| **Google Gemini 3.7 Flash** 📚 | 更新版本 | 同上 | P2 |
| **Google Gemini 3.8 Flash** 📚 | 最新 Flash,长上下文+速度 | 通读 242K utils、qlib 75K 代码理解 | P1 |
| **xAI Grok 4.5** | 快速、推理强 | 备选,主力限流时兜底 | P2 |
| **xAI Grok 4.6** | 更新版本 | 同上 | P2 |
| **Kimi K2.7 Code** 🧠 | 月之暗面代码专用 | 中文代码注释、中文变量命名 | P2 |
| **Kimi K3** 📚 | 超长上下文 128K+ | 通读大型文件、长文档代码理解 | P1 |

#### 前沿探索型号(下一代能力,选择性使用)

| 模型 | 核心优势 | 在本系统的最佳用场 | 优先级 | 使用建议 |
|------|---------|------------------|--------|---------|
| **OpenAI GPT-6 Astra** 🚀 | 下一代 GPT,推理与代码能力再跃升 | 复杂架构探索、下一代功能原型、跨仓深度重构预研 | P1(实验性) | Preview 版,用于**非关键路径探索**;关键任务仍用 GPT-5.6 Sol;稳定后升级 |
| **Anthropic Claude Fable 5.1** 🚀 | Anthropic 新型号,创意与叙事能力强 | 因子假设头脑风暴、策略故事叙述、中文诊脉书风格报告、研究笔记 | P1(实验性) | 定位偏创意,用于**研究创新与文档**;生产代码待社区验证后再升级 |
| **OpenAI GPT-5.6 Luna** | GPT 多模态变体 | 图表理解、UI 设计辅助、可视化调试 | P2 | 辅助角色,非主力 |

#### 特殊型号

| 模型 | 核心优势 | 在本系统的最佳用场 | 优先级 |
|------|---------|------------------|--------|
| **Anthropic Claude Opus 4.8 (fast mode)** ⚡ | Opus 速度提升版 | 需要 Opus 质量但赶速度时 | P1 |

### 2.2 国内模型(BYOK,OpenAI 兼容端点,不限预算)

| 模型 | 核心优势 | 在本系统的最佳用场 | 优先级 |
|------|---------|------------------|--------|
| **智谱 GLM-5.3 / 5.4** ⭐ | 工程智能体最强(工具调用/长链路编排)、中文金融语义好;系统已原生集成 | AI 决策(GLM-5 盘中)、AI Hedge Fund 20 分析师 LangGraph、复杂 Agent 编排 | P0 |
| **DeepSeek V4 Pro / V3.2** ⭐ | 通用代码质量高、吞吐大、成本极低 | 因子批量实现、research 批量研究、scripts 自动化、utils 胶水代码 | P0 |
| **DeepSeek-R1 / R2**(推理) ⭐ | 数学与逻辑深度思考 | 新因子假设→数学表达、收益归因、定价公式推导、回测方法论 | P0 |
| **阿里 Qwen3-Max / 3.5** ⭐ | 长上下文 + 中英均衡 + 中文好 | 中文 UI 页、中文报告模板、宏观十五五/社保 ETF、跨大文件中文重构 | P0 |
| **字节 豆包(Doubao Seed)** ⚡ | 快、便宜、中文流畅 | 高吞吐量脚手架、日志/监控、中文运维脚本、批量注释与文档 | P1 |
| **Kimi(Moonshot K3)** 📚 | 超长上下文窗口 | 通读 utils(242K)/qlib(75K)、长文档代码理解、大文件归因 | P1 |

### 2.3 选型决策树

```
开始编码任务
    │
    ├─ 资金安全相关?(执行器/风控/回测内核)
    │   └─ 是 → Claude Opus 4.8 / Opus 5 主笔 + GPT-5.6 Sol 双审
    │
    ├─ 跨文件重构?(≥5 文件 或 100K+ 上下文)
    │   └─ 是 → Claude Opus 4.8 / GPT-5.6 Terra
    │
    ├─ 数值/算法正确性?(因子/定价/回测)
    │   └─ 是 → GPT-5.6 Sol / GPT-5.4
    │
    ├─ 中文语义重?(政策/报告/UI/文档)
    │   └─ 是 → Qwen3-Max / GLM-5.3
    │   │
    │   └─ 创意/叙事/诊脉书风格? → Claude Fable 5.1 🚀
    │
    ├─ 批量/快速/低成本?(脚本/注释/测试)
    │   └─ 是 → DeepSeek V4 / Claude Haiku 4.5 / GPT-5 mini
    │
    ├─ 前沿探索?(架构预研/下一代原型)
    │   └─ 是 → GPT-6 Astra 🚀 (Preview,非关键路径)
    │
    └─ 日常编码 → Claude Sonnet 5 / Sonnet 4.6
```

---

## 3. 系统模块规模盘点

来自 `项目架构图_v8.7.architecture.json` + 全仓 LOC 统计(共 **2,181 个 .py / ≈740K 行**):

| 子系统 | 代码行 | 文件数 | 性质 |
|---|---:|---:|---|
| utils(工具/胶水/LLM 路由) | 242K | 552 | 量大、杂 |
| tests(测试) | 242K | 644 | 量大、质量关键 |
| qlib(vendored 依赖) | 75K | 335 | **第三方,勿重写** |
| scripts(自动化) | 57K | 203 | 量大、运维向 |
| ms_strategy | 25K | 84 | 策略实现主体 |
| quant_modules | 22K | 73 | 因子/模块主体 |
| ai_decision | 9K | 23 | AI 对冲基金 LangGraph |
| backtests | 5.4K | 16 | 数值正确性敏感 |
| cli(统一入口) | 5.8K | 45 | 多模式编排 |
| lgb_trainer | 4.2K | 11 | ML 正确性敏感 |
| data | 4.2K | 9 | 采集/降级链 |
| external | 8.2K | 34 | 外部 SDK 对接 |
| ui(Streamlit 17 页) | 6.9K | 32 | 中文 UI |
| reporting | 2.9K | 8 | 中文报告 |

---

## 4. 逐模块分配矩阵(完整执行清单)

> 标注: **[外]**=Copilot Max 国外模型主笔;**[国]**=国内模型主笔;**首选 → 备选**

### 4.1 国外模型主导(资金安全 + 架构复杂 + 英文生态)

| # | 模块 / 文件 | 规模 | 首选模型 | 备选 | 验收标准 |
|---|------------|------|---------|------|---------|
| 1 | **executor**(实盘指令生成) | 1.1K | **[外] Claude Opus 4.5** | GPT-5.1 | 安全审计通过 + 单测覆盖 100% |
| 2 | **institutional_pipeline_runner**(机构管道) | 108K | **[外] Gemini 2.5 Pro**→Opus | GPT-5.1 | 拆分为 ≤5KB/文件 |
| 3 | **daily_trade_executor**(每日交易执行) | 63K | **[外] Claude Opus 4.5** | GPT-5.1 | 通过风控门测试 |
| 4 | **run_daily_eod**(风控四 Guard 链) | 5.8K | **[外] Claude Opus 4.5** | GPT-5.1 | KillSwitch 触发测试通过 |
| 5 | **utils/hedge_engine**(多指数 Beta 对冲) | 3.2K | **[外] GPT-5.1** | Claude Opus | Beta 计算单元测试通过 |
| 6 | **utils/hedge_rebalance_integrator**(五阶段联动) | 2.8K | **[外] Claude Opus 4.5** | GPT-5.1 | 状态机转换测试通过 |
| 7 | **scripts/run_200w_etf_backtest**(Phase 2 回测) | 4.5K | **[外] GPT-5.1** | Claude Opus | 数值正确性验证通过 |
| 8 | **ms_strategy/src/alpha**(GTJA191 因子) | 8.2K | **[外] GPT-5.1** | DeepSeek-R1 | IC/IR 计算验证通过 |
| 9 | **utils/ml_enhanced_trainer**(LightGBM 训练) | 3.8K | **[外] GPT-5.1** | Claude Opus | 模型精度达标 |
| 10 | **quant_modules/data_layer**(P0-P6 降级链) | 2.1K | **[外] Claude Opus 4.5** | GPT-5.1 | 降级场景全覆盖测试 |

### 4.2 国内模型主导(中文语义 + 体量 + 实时)

| # | 模块 / 文件 | 规模 | 首选模型 | 备选 | 验收标准 |
|---|------------|------|---------|------|---------|
| 11 | **utils/glm5_decision_engine**(GLM-5 决策引擎) | 1.9K | **[国] GLM-5.3** | GLM-5.4 | 盘中决策延迟 <500ms |
| 12 | **utils/glm5_client**(多模型路由) | 1.2K | **[国] GLM-5.3** | GLM-5.4 | 路由准确率 100% |
| 13 | **quant_modules/ai_hedge_fund**(20 分析师 LangGraph) | 9K | **[国] GLM-5.3** | DeepSeek-V4 | 分析师编排无死锁 |
| 14 | **utils/kondratiev_cycle**(康波周期) | 1.5K | **[国] Qwen3-Max** | GLM-5.3 | 周期阶段判定准确 |
| 15 | **utils/five_year_plan**(十五五评分) | 1.1K | **[国] Qwen3-Max** | GLM-5.3 | 政策对齐评分合理 |
| 16 | **utils/social_security_etf**(社保 ETF 追踪) | 1.3K | **[国] Qwen3-Max** | GLM-5.3 | 国家队信号识别准确 |
| 17 | **tools/wind_mcp_fetcher**(Wind 取数) | 2.4K | **[国] DeepSeek-V4** | GLM-5.3 | 数据完整率 100% |
| 18 | **utils/option_data_fetcher**(期权数据) | 1.8K | **[国] DeepSeek-V4** | GLM-5.3 | σ 代理链准确 |
| 19 | **ui/**(Streamlit 14 页面板) | 6.9K | **[国] Qwen3-Max** | 豆包 | 中文显示无乱码 |
| 20 | **reporting/**(中文报告) | 2.9K | **[国] Qwen3-Max** | GLM-5.3 | 报告格式规范 |
| 21 | **scripts/**(批量自动化) | 57K | **[国] DeepSeek-V4** | 豆包 | 脚本可执行率 100% |
| 22 | **tests/**(批量测试) | 242K | **[国] DeepSeek-V4** | 豆包 | 覆盖率 ≥80% |

### 4.3 混合协作(国内外各干一半,交叉审查)

| # | 模块 / 文件 | 国外职责 | 国内职责 | 验收标准 |
|---|------------|---------|---------|---------|
| 23 | **config/portfolio_200w_etf_v91.yaml** | 结构段(权重/熔断/期权参数) | 政策语义段(十五五/社保/康波注释) | 配置加载无错误 |
| 24 | **utils/**(242K 胶水代码) | 架构重构 + 接口定义 | 批量实现 + 中文注释 | 接口一致性通过 |
| 25 | **quant_modules/**(因子模块) | 核心算法 + 数值逻辑 | 批量因子实现 + 中文文档 | IC 计算验证通过 |
| 26 | **backtests/**(回测脚本) | 数值内核 + 方法论 | 批量回测 + 中文报告 | 回测结果可复现 |
| 27 | **cli/**(统一入口) | 多模式编排 + 架构 | 命令实现 + 中文帮助 | 所有模式可运行 |
| 28 | **cairn 知识层 / AGENTS.md** | 架构文档(精确) | 中文沉淀(流畅) | 文档与代码一致 |

---

## 5. VS Code 多模型自动切换配置(可直接使用)

### 5.1 安装插件

| 插件 | 用途 | 优先级 | 安装方式 |
|------|------|--------|---------|
| **GitHub Copilot** | 国外模型主力编码 | P0 | VS Code 扩展市场 |
| **GitHub Copilot Chat** | Agent Mode + 对话 | P0 | VS Code 扩展市场 |
| **Continue.dev** | 国产模型接入 + 按路径路由 | P0 | VS Code 扩展市场 |
| **GitLens** | 代码溯源 + 模型产出标记 | P1 | VS Code 扩展市场 |
| **Error Lens** | 实时错误高亮 | P1 | VS Code 扩展市场 |

### 5.2 完整 settings.json(复制即用)

```jsonc
// .vscode/settings.json — AI 模型分工终极配置
// 生成时间: 2026-09-11
// 适用: 28-终极量化交易系统 v8.7

{
  // ═══════════════════════════════════════════════════════════════
  // 第一部分: GitHub Copilot (国外模型主力)
  // ═══════════════════════════════════════════════════════════════
  
  // 主力模型选择
  "github.copilot.selectedModel": "claude-sonnet-4",
  
  // 启用 Agent Mode
  "github.copilot.chat.agent.enabled": true,
  
  // 启用代码补全
  "github.copilot.editor.enableAutoCompletions": true,
  
  // ═══════════════════════════════════════════════════════════════
  // 第二部分: Continue.dev (国产模型接入)
  // ═══════════════════════════════════════════════════════════════
  
  "continue.enableTabAutocomplete": true,
  "continue.enableCodeLens": true,
  
  // 国产模型配置(OpenAI 兼容端点)
  "continue.models": [
    {
      "title": "GLM-5.3",
      "provider": "openai",
      "model": "glm-5.3",
      "apiBase": "https://open.bigmodel.cn/api/paas/v4",
      "apiKey": "${env:GLM_API_KEY}",
      "contextLength": 128000,
      "systemMessage": "你是量化交易系统开发专家,精通 A 股规则、Wind 接口、中文金融语义。"
    },
    {
      "title": "DeepSeek-V4",
      "provider": "openai",
      "model": "deepseek-chat",
      "apiBase": "https://api.deepseek.com/v1",
      "apiKey": "${env:DEEPSEEK_API_KEY}",
      "contextLength": 64000,
      "systemMessage": "你是高效代码生成专家,擅长批量实现、脚本自动化、测试生成。"
    },
    {
      "title": "DeepSeek-R1",
      "provider": "openai",
      "model": "deepseek-reasoner",
      "apiBase": "https://api.deepseek.com/v1",
      "apiKey": "${env:DEEPSEEK_API_KEY}",
      "contextLength": 64000,
      "systemMessage": "你是数学与逻辑推理专家,擅长因子公式推导、收益归因、定价模型。"
    },
    {
      "title": "Qwen3-Max",
      "provider": "openai",
      "model": "qwen-max-latest",
      "apiBase": "https://dashscope.aliyuncs.com/compatible-mode/v1",
      "apiKey": "${env:DASHSCOPE_API_KEY}",
      "contextLength": 32768,
      "systemMessage": "你是中文金融文档专家,擅长中文报告、政策解读、UI 文案。"
    },
    {
      "title": "Doubao-Seed",
      "provider": "openai",
      "model": "doubao-seed-1-6-250615",
      "apiBase": "https://ark.cn-beijing.volces.com/api/v3",
      "apiKey": "${env:VOLC_API_KEY}",
      "contextLength": 32768,
      "systemMessage": "你是高效脚手架生成专家,擅长批量代码、日志监控、运维脚本。"
    },
    {
      "title": "Kimi-K3",
      "provider": "openai",
      "model": "moonshot-v1-128k",
      "apiBase": "https://api.moonshot.cn/v1",
      "apiKey": "${env:MOONSHOT_API_KEY}",
      "contextLength": 128000,
      "systemMessage": "你是长文档理解专家,擅长通读大型代码库、跨文件重构、代码归因。"
    }
  ],
  
  // ═══════════════════════════════════════════════════════════════
  // 第三部分: 按文件路径自动切换模型(核心规则)
  // ═══════════════════════════════════════════════════════════════
  
  "continue.rules": [
    // ── 资金三件套 → 国外模型(最高优先级) ──
    {"path": "executor/**", "model": "claude-sonnet-4", "autoSelect": true},
    {"path": "run_daily_eod.py", "model": "claude-sonnet-4", "autoSelect": true},
    {"path": "utils/hedge_engine.py", "model": "claude-sonnet-4", "autoSelect": true},
    {"path": "utils/hedge_rebalance_integrator.py", "model": "claude-sonnet-4", "autoSelect": true},
    {"path": "scripts/run_200w_etf_backtest.py", "model": "claude-sonnet-4", "autoSelect": true},
    
    // ── 架构重构 → 国外长上下文 ──
    {"path": "institutional_pipeline_runner.py", "model": "claude-sonnet-4", "autoSelect": true},
    {"path": "daily_trade_executor.py", "model": "claude-sonnet-4", "autoSelect": true},
    {"path": "quant_modules/data_layer.py", "model": "claude-sonnet-4", "autoSelect": true},
    
    // ── 已绑定 GLM-5 → 继续用 ──
    {"path": "utils/glm5_decision_engine.py", "model": "GLM-5.3", "autoSelect": true},
    {"path": "utils/glm5_client.py", "model": "GLM-5.3", "autoSelect": true},
    {"path": "utils/glm5_*.py", "model": "GLM-5.3", "autoSelect": true},
    
    // ── AI Hedge Fund → GLM-5 ──
    {"path": "quant_modules/ai_hedge_fund/**", "model": "GLM-5.3", "autoSelect": true},
    
    // ── 中文政策语义 → Qwen3-Max ──
    {"path": "utils/kondratiev_cycle.py", "model": "Qwen3-Max", "autoSelect": true},
    {"path": "utils/five_year_plan.py", "model": "Qwen3-Max", "autoSelect": true},
    {"path": "utils/social_security_etf.py", "model": "Qwen3-Max", "autoSelect": true},
    
    // ── Wind/期权接口 → DeepSeek-V4 ──
    {"path": "tools/wind_mcp_fetcher.py", "model": "DeepSeek-V4", "autoSelect": true},
    {"path": "utils/option_data_fetcher.py", "model": "DeepSeek-V4", "autoSelect": true},
    
    // ── 中文 UI/报告 → Qwen3-Max ──
    {"path": "ui/**", "model": "Qwen3-Max", "autoSelect": true},
    {"path": "reporting/**", "model": "Qwen3-Max", "autoSelect": true},
    
    // ── 批量脚本 → DeepSeek-V4(高吞吐低成本) ──
    {"path": "scripts/**", "model": "DeepSeek-V4", "autoSelect": true},
    {"path": "tests/**", "model": "DeepSeek-V4", "autoSelect": true},
    
    // ── 文档 → Qwen3-Max ──
    {"path": "docs/**", "model": "Qwen3-Max", "autoSelect": true},
    {"path": "*.md", "model": "Qwen3-Max", "autoSelect": true},
    
    // ── 因子数学推导 → DeepSeek-R1 ──
    {"path": "ms_strategy/src/alpha/**", "model": "DeepSeek-R1", "autoSelect": true},
    
    // ── 长文档理解 → Kimi-K3 ──
    {"path": "qlib/**", "model": "Kimi-K3", "autoSelect": true}
  ],
  
  // ═══════════════════════════════════════════════════════════════
  // 第四部分: 质量门配置
  // ═══════════════════════════════════════════════════════════════
  
  // 启用代码操作
  "continue.enableCodeActions": true,
  
  // 显示代码建议
  "continue.showInlineCodeSuggestions": true,
  
  // 自动接受建议(谨慎启用)
  "continue.acceptSuggestionOnEnter": "off"
}
```

### 5.3 环境变量配置(.env)

```bash
# .env — API Keys(不要提交到 Git)
# GitHub Copilot 通过 VS Code 登录自动获取,无需 key

# 国产模型 API Keys
GLM_API_KEY=your_glm_api_key_here
DEEPSEEK_API_KEY=your_deepseek_api_key_here
DASHSCOPE_API_KEY=your_dashscope_api_key_here
VOLC_API_KEY=your_volcengine_api_key_here
MOONSHOT_API_KEY=your_moonshot_api_key_here
```

### 5.4 .gitignore 补充

```gitignore
# API Keys
.env
.env.local

# VS Code 个人配置(保留 settings.json,忽略个人配置)
.vscode/settings.json.local
```

### 5.5 copilot-instructions.md(自动对齐模型职责)

```markdown
# .github/copilot-instructions.md

## 模型分工规则

### 资金三件套(必须国外模型)
- `executor/**` → Claude Opus 4.5
- `run_daily_eod.py` → Claude Opus 4.5
- `utils/hedge_engine.py` → GPT-5.1

### 国内模型主导
- `utils/glm5_*.py` → GLM-5.3(已绑定)
- `utils/kondratiev_cycle.py` → Qwen3-Max
- `utils/five_year_plan.py` → Qwen3-Max
- `utils/social_security_etf.py` → Qwen3-Max
- `tools/wind_mcp_fetcher.py` → DeepSeek-V4
- `ui/**` → Qwen3-Max
- `scripts/**` → DeepSeek-V4

### 质量门
- 关键路径(#1/#2/#3/#4/#7)PR 必须附 Claude Opus 安全审计摘要 + GPT-5.1 单测覆盖说明
- 国内模型产出的关键文件,提交前用 Copilot(Max)跑一轮 `/review`
```

---

## 6. 验证策略与质量门

### 6.1 模型产出验证方法

| 模型类型 | 验证方法 | 工具 | 通过标准 |
|---------|---------|------|---------|
| 国外模型(架构/风控) | 安全审计 + 单测补全 | Claude Opus `/review` + pytest | 零高危漏洞 + 覆盖率 ≥80% |
| 国内模型(批量/中文) | 边界测试 + 集成测试 | DeepSeek 自检 + GitHub Actions | 所有边界用例通过 |
| 混合产出 | 接口一致性检查 | mypy + 自定义 lint | 类型无错误 + lint 零警告 |

### 6.2 关键路径 PR 检查清单

```markdown
## PR 检查清单(关键路径专用)

- [ ] Claude Opus 安全审计摘要已附加
- [ ] GPT-5.1 单测覆盖说明已附加
- [ ] 单测覆盖率 ≥80%
- [ ] 无 mypy 类型错误
- [ ] 无 ruff lint 警告
- [ ] 资金安全边界测试通过
- [ ] 回测结果可复现(如涉及)
```

---

## 7. 冲突解决机制

### 7.1 冲突裁决链

```
国内模型产出 → 国外模型 review
                    ↓
              若分歧
                    ↓
         ┌─────────────────────┐
         │ 资金安全相关?        │
         └─────────────────────┘
           ↓           ↓
          是          否
           ↓           ↓
    以国外模型为准   双模型会审
                    (Claude + GPT)
                        ↓
                    仍分歧
                        ↓
                    人工裁决
```

### 7.2 常见冲突场景

| 场景 | 国内模型方案 | 国外模型方案 | 裁决 |
|------|------------|------------|------|
| 降级链实现 | 快速硬编码 | 抽象策略模式 | 国外(架构重要) |
| 中文报告格式 | 自由发挥 | 严格模板 | 国内(中文优势) |
| 因子命名 | 拼音缩写 | 英文驼峰 | 国外(可维护性) |
| 涨跌停处理 | 经验值 | 配置化 | 国外(灵活性) |

---

## 8. 成本-质量权衡矩阵

| 任务类型 | 推荐模型 | 理由 | 可接受替代 | 预估成本 |
|---------|---------|------|-----------|---------|
| 资金安全关键 | Claude Opus 4.5 | 安全审计最强 | GPT-5.2(次选) | $$$ |
| 数值正确性 | GPT-5.1 | 数学推导最准 | Claude Opus | $$$ |
| 架构重构 | Gemini 2.5 Pro | 100 万上下文 | Claude Opus | $$$ |
| 批量脚本 | DeepSeek V4 | 成本极低、吞吐大 | 豆包(更快) | $ |
| 中文报告 | Qwen3-Max | 中文流畅 | GLM-5.3 | $$ |
| 长文档理解 | Kimi-K3 | 128K 上下文 | Gemini | $$ |
| 因子推导 | DeepSeek-R1 | 数学推理强 | GPT-5.1 | $$ |

### 8.1 预算分配建议

| 资源 | 用途 | 预估用量 | 月预算参考 |
|------|------|---------|-----------|
| **GitHub Copilot Max** | 架构/风控/回测/审计 | 70% 代码行 | $20/月(固定) |
| **Claude Opus 4.5** | 安全审计/最终 review | 10% 关键代码 | ~$50(按量) |
| **GPT-5.1** | 数值内核/单测 | 10% | ~$30(按量) |
| **DeepSeek-V4** | 批量脚本/测试 | 5% | ~$5(极低) |
| **Qwen3-Max** | 中文报告/UI | 3% | ~$10(按量) |
| **GLM-5.3** | AI 决策/已绑定 | 2% | ~$5(按量) |

---

## 9. 分阶段推进计划(Wave)

| 阶段 | 时间 | 重点 | Owner | 交付 | 验收标准 |
|------|------|------|-------|------|---------|
| **Wave A: 止血加固** | 第 1-2 周 | 风控/执行器/回测 | 高级开发 | 资金三件套 + 回测内核通过安全审计 | 零高危漏洞 |
| **Wave B: 架构重构** | 第 3-5 周 | 管道通读重构 | 架构师 | 108KB 管道拆分为 ≤5KB/文件 | 文件可维护 |
| **Wave C: 因子与 AI** | 第 6-7 周 | 表达式引擎/因子批量 | 量化研究员 | alpha 产能提升 3x | IC 计算验证通过 |
| **Wave D: 数据与报告** | 第 8 周 | 降级链/中文报告 | 全栈开发 | 全链路中文语义闭环 | 报告自动化 |
| **Wave E: 测试与审计** | 持续 | 关键单测/批量测试/全仓审计 | QA | 覆盖率 ≥80% | 质量达标 |

---

## 10. 禁忌(违反即返工)

| # | 禁忌 | 原因 | 后果 |
|---|------|------|------|
| 1 | ❌ 用国外模型写十五五/社保/康波模块 | 政策语义理解偏差,输出"正确但不对味" | 返工重写 |
| 2 | ❌ 用国内模型写 LangGraph 多 Agent 编排 | 复杂状态机容易逻辑漏洞 | 系统崩溃 |
| 3 | ❌ 用国外模型处理 Wind MCP 接口细节 | 复权/分钟协议/错误码会漏细节 | 数据错误 |
| 4 | ❌ 用国内模型做大规模重构(108KB 大文件) | 泛化架构能力不如国外 | 技术债增加 |
| 5 | ❌ 全用一个模型不分 | 浪费预算,且各模型有明确长短 | 质量不达标 |
| 6 | ❌ 国内模型产出直接合入主干(未经国外 review) | 资金安全风险 | 资金损失 |
| 7 | ❌ IV 高位追买 Put | 成本指数级上升 | 保护成本失控 |
| 8 | ❌ 裸卖认购(不备兑) | 无限亏损风险 | 爆仓 |

---

## 11. Prompt 模板库

### 11.1 Claude Opus 安全审计模板

```
你是量化交易系统安全审计专家。请对以下代码进行安全审计:

## 代码
{code}

## 审计重点
1. 资金安全: 是否有溢出、精度丢失、边界条件遗漏
2. 风控门: KillSwitch/Drawdown/VolTarget/Hedge 是否可靠
3. 密钥管理: API Key 是否硬编码
4. 并发安全: 是否有竞态条件
5. 错误处理: 异常是否被正确捕获

## 输出格式
- 高危问题(必须修复): 
- 中危问题(建议修复): 
- 低危问题(可选优化): 
- 整体评分(1-10):
```

### 11.2 GPT-5.1 单测生成模板

```
你是测试专家。请为以下代码生成 pytest 单测:

## 代码
{code}

## 要求
1. 覆盖所有正常路径
2. 覆盖所有边界条件
3. 覆盖所有异常路径
4. 使用 pytest parametrize
5. 覆盖率目标 ≥80%

## 输出
完整的测试文件,包含 fixtures 和测试用例。
```

### 11.3 DeepSeek 批量因子实现模板

```
你是因子开发专家。请批量实现以下因子:

## 因子列表
{factor_list}

## 要求
1. 每个因子是一个独立函数
2. 函数签名: def factor_name(prices: pd.DataFrame) -> pd.Series
3. 包含中文 docstring
4. 处理 NaN 和 Inf
5. 性能优化: 避免循环,使用向量化

## 输出
完整的因子实现文件。
```

### 11.4 GLM-5 中文报告生成模板

```
你是中文金融报告专家。请生成以下报告:

## 数据
{data}

## 要求
1. 使用专业金融术语
2. 中文流畅,符合国内阅读习惯
3. 包含关键指标表格
4. 给出明确的投资建议
5. 风险提示

## 输出
Markdown 格式的中文报告。
```

---

## 12. 需核实项(落地前确认)

| # | 核实项 | 临时兜底方案 | 优先级 |
|---|--------|------------|--------|
| 1 | Copilot Max 当前可选模型清单 | 在 VS Code 模型选择器确认;若无 Claude Opus,用 GPT-5.2 替代 | P0 |
| 2 | 原生 Copilot BYOK 配置键名 | 直接用 Cline/Roo Code 兜底(已在 5.2 推荐) | P0 |
| 3 | 国产模型最新型号与端点 | 各厂商控制台核对 modelId 与 baseUrl(表中为常见值) | P1 |
| 4 | qlib 是否仍需 vendored | 若可改为 pip 依赖,删除 75K vendored 代码(Wave B 决策) | P2 |
| 5 | Wind MCP 期权链接口 | 当前 σ 代理链用 rv_proxy,真实 IV 链接入后升级 | P2 |

---

## 附录: 一句话总结

> **国外模型(Copilot Max)主攻:架构/编排/回测/UI/Agent/通用算法/安全审计 — 占 70% 代码。**
> **国内模型(Qwen/GLM/DeepSeek)主攻:Wind 接口/政策语义/中文报告/A 股规则/社保康波/批量脚本 — 占 30% 代码 + 全部中文产出。**
> 两者通过 VS Code + Continue.dev 按文件路径自动切换,国外模型收最后一道质量门。
> **别用国外模型写十五五政策,别用国内模型写 LangGraph 多 Agent,别让国内模型产出未经 review 就合入主干 — 这是实测最优解。**

---

## 附录: 系统模块完整分工表(速查)

| 目录/文件 | 模型 | 备注 |
|-----------|------|------|
| `executor/**` | 国外 Claude Opus | 资金安全最高危 |
| `run_daily_eod.py` | 国外 Claude Opus | 风控四 Guard |
| `utils/hedge_engine.py` | 国外 GPT-5.1 | Beta 对冲 |
| `utils/hedge_rebalance_integrator.py` | 国外 Claude Opus | 五阶段联动 |
| `scripts/run_200w_etf_backtest.py` | 国外 GPT-5.1 | Phase 2 回测 |
| `ms_strategy/src/alpha/` | 国外 GPT-5.1 | GTJA191 因子 |
| `quant_modules/data_layer.py` | 国外 Claude Opus | P0-P6 降级链 |
| `institutional_pipeline_runner.py` | 国外 Gemini→Opus | 108KB 重构 |
| `daily_trade_executor.py` | 国外 Claude Opus | 63KB 执行器 |
| `utils/ml_enhanced_trainer.py` | 国外 GPT-5.1 | LightGBM |
| `.github/workflows/` | 国外 | CI/CD |
| `utils/glm5_*.py` | 国内 GLM-5.3 | 已绑定 |
| `quant_modules/ai_hedge_fund/` | 国内 GLM-5.3 | 20 分析师 |
| `utils/kondratiev_cycle.py` | 国内 Qwen3-Max | 康波+十五五 |
| `utils/five_year_plan.py` | 国内 Qwen3-Max | 政策评分 |
| `utils/social_security_etf.py` | 国内 Qwen3-Max | 社保 ETF |
| `tools/wind_mcp_fetcher.py` | 国内 DeepSeek-V4 | Wind 接口 |
| `utils/option_data_fetcher.py` | 国内 DeepSeek-V4 | σ 代理链 |
| `ui/**` | 国内 Qwen3-Max | Streamlit 14 页 |
| `reporting/**` | 国内 Qwen3-Max | 中文报告 |
| `scripts/**` | 国内 DeepSeek-V4 | 批量自动化 |
| `tests/**` | 国内 DeepSeek-V4 | 批量测试 |
| `config/portfolio_200w_etf_v91.yaml` | 混合 | 结构国外/政策国内 |
| `utils/`(242K) | 混合 | 架构国外/实现国内 |

---

*本方案基于对 28-终极量化交易系统 v8.6 全量代码扫描,直接对应实际文件路径。*
*合并自: `AI模型分工方案_国内外模型最优配置.md` + `CopilotMax_国内模型_开发分工报告_20260911.md`*
*更新时间: 2026-09-11*
| executor(实盘指令) | 1.1K | 2 | **资金相关,最高危** |