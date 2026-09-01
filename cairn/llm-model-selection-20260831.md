# LLM 模型选型决策框架（量化任务分工）

> **创建**: 2026-08-31 | **状态**: 决策沉淀（方法论级，非代码改动）
> **关联实现**: `utils/alpha/llm/router.py`（LLMRouter 5-provider fallback）、`utils/glm5_client.py`、`utils/alpha/llm/providers/deepseek.py`、`.env.example`（role 分配）
> **触发背景**: 多模型职责分工经验固化，指导后续 AI 决策链路的模型选型

---

## 一、核心决策框架（四象限）

| 任务场景 | 首选模型 | 理由 | 系统对应 |
|---|---|---|---|
| 复杂量化框架 / 自动化策略 Agent | **GLM-5.3** | 工程智能体能力极强（工具调用/长链路编排/多步代码生成稳定） | `utils/alpha/llm/providers/glm.py`（glm provider） |
| 推导数学公式 / 探索新因子 | **DeepSeek-R1** | 数学与逻辑推理极致（深度思考模式，适合因子假设→数学表达） | `LLMRouter.chat_deep()` → `deepseek-reasoner` |
| 日常策略代码生成 / 盘后批量研究 | **DeepSeek V4 Pro** 或 **GLM-5.3** 均可 | 大吞吐批量任务，两者质量相当，按可用性/成本切换 | deepseek / glm provider |
| 盘中实时信号处理 / 实盘接口 | **DeepSeek-V3** | 兼顾速度与稳定性（低延迟、低抖动，实时链路首选） | `LLMRouter` 主 provider（`deepseek-chat`） |

**一句话概括**: 工程与编排上 GLM 系、深度推理上 R1、实时链路上 V3、批量生产上 V4 Pro/GLM 均可。

---

## 二、与实际系统的对应关系

### 2.1 LLMRouter fallback 链（`utils/alpha/llm/router.py`）

```
omniroute (P0) → deepseek → doubao → glm → siliconflow → ds4 → ollama
```

- **deepseek** = DeepSeek V3 (`deepseek-chat`) — 主 LLM，所有 AI 决策默认通道（对应"盘中实时/实盘接口"）
- **deepseek_reasoner** = DeepSeek R1 (`deepseek-reasoner`) — `chat_deep()` 深度思考模式主通道，备选 `ollama deepseek-r1:14b`（对应"数学推导/新因子探索"）
- **glm** = 智谱 GLM 系列 — 备 2（对应"复杂框架/策略 Agent"）
- **ds4** (DwarfStar) = 本地推理引擎，支持 **GLM 5.2** + **DeepSeek V4 Flash**（feature flag `GLM5_DS4_ENABLED` 默认关闭，shadow 验证中）——V4 系列本地化入口

### 2.2 `.env.example` 角色分配（决策系统内多模型分工）

```
DEEPSEEK_API_KEY  → 信号计算/代码        (role: signal/bull)
GLM_API_KEY       → 合规审计/中文金融    (role: compliance/bear)
CLAUDE_API_KEY    → 深度推理/风控        (role: reasoning/judge)
GPT(OPENAI)       → 盘中研判            (role: intraday, 占位可扩展)
```

> 注：`glm5_client.py` 中 GLM 版号为 5.2；GLM-5.3 为最新工程智能体版本，接入时升级 provider model 名即可。

---

## 三、使用建议

1. **实时/低延迟链路**（盘中信号、下单前置校验）锁定 **DeepSeek-V3**，不要用 R1（思考延迟不可控）也不要切 GLM-5.3（除非已有充分工程验证）。
2. **深度推理**（因子数学表达、公式推导、收益归因模型）显式走 `chat_deep()`，确保命中 `deepseek-reasoner` 而非默认 chat。
3. **批量盘后研究**（因子扫描、代码生成）可轮询 V4 Pro / GLM-5.3，按 provider 健康度（`check_providers()`）与成本动态选择，二者质量等价时优先成本低者。
4. **复杂 Agent 编排**（多步工具调用、状态机）优先 GLM-5.3，其工具遵循与长上下文工程能力在四者中最强。
5. 任何模型更换都经 LLMRouter（fallback 链天然容错），**不要绕过 router 直连 provider**，否则失去降级保护。
