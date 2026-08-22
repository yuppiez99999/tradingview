# deep-research 接入可行性评估

> 创建: 2026-08-21
> 评估对象: ECC `deep-research` skill（多源研究 + 引用合成，依赖 firecrawl/exa MCP）
> 结论: **有条件接入 — exa MCP 已配置，建议在 Claude Code CLI 中作宏观研究辅助，不接入 CodeArts/CI**

## 1. 前置条件核查

| 前置 | 状态 | 证据 |
|---|---|---|
| firecrawl MCP | ❌ 未配置 | 量化系统 `.mcp.json` 无 firecrawl |
| **exa MCP** | ✅ **已配置** | `28-终极量化交易系统8.4/.mcp.json` 含 `"exa": {"type":"http","url":"https://mcp.exa.ai/mcp"}` |
| ECC deep-research skill | ✅ 已安装 | `~/.claude/.agents/skills/deep-research/SKILL.md` |
| exa API key | ⚠️ 未确认 | `.env.example` 无 EXA_API_KEY；exa http MCP 可能需认证 |
| 运行环境 | ⚠️ CodeArts 不直接消费 MCP | 需在 Claude Code CLI 中调用 |

## 2. 量化系统现有宏观研究能力（对照）

| 模块 | 能力 | deep-research 增量 |
|---|---|---|
| `utils/kondratiev_cycle.py` | 康波周期阶段 + 行业配置 | 无（已专业化） |
| `utils/five_year_plan.py` | 十五五规划政策对齐评分 | 可补充政策**多源研究+引用** |
| `utils/social_security_etf.py` | 社保基金 ETF 风格追踪 | 可补充国家队动向**舆情广度** |
| UI 11 大宗商品监控 | 商品价格/趋势/预警 | 可补充商品基本面**多源对照** |
| TrendRadar MCP | 大宗商品舆情 | deep-research 更通用（不限商品） |
| UI 10 宏观综合分析 | 一键三大（康波+十五五+社保ETF） | 可作研究**前置阶段**（收集后再喂给三大模块） |

## 3. 增量价值评估

**有增量空间的场景**（deep-research 擅长的多源研究+引用合成）：
- 十五五规划政策研究：多源收集 7 大战略方向的政策原文+解读+行业影响，合成带引用的报告
- 国家队/社保动向：多源追踪社保基金持仓变动、风格切换、重仓股调整
- 行业轮动研究：康波阶段 × 行业景气 × 政策催化 的多源交叉验证
- 大宗商品基本面：供需/库存/产能/地缘的多源对照（补充 TrendRadar 的舆情维度）

**无增量空间的场景**（量化系统已更专业）：
- 康波周期阶段判定（已有量化模型）
- ETF 资金流信号（已有实时监控）
- 技术面/因子计算（已有 alpha_factor 引擎）

## 4. 接入方案（建议）

**建议: 有条件接入，定位为"宏观研究前置辅助工具"，不接入 CI/CodeArts**

| 项 | 决策 |
|---|---|
| 接入位置 | Claude Code CLI（`~/.claude/` 已装 deep-research skill + exa MCP 已配） |
| 不接入 | CodeArts 当前环境（不消费 MCP）、量化系统 CI（研究工具非代码门禁） |
| 使用方式 | 研究阶段手动调用：在 Claude Code 中对"十五五政策/国家队动向/行业轮动"做 deep-research，产出带引用报告，再作为 `five_year_plan.py` / `social_security_etf.py` 的输入素材 |
| 前置待办 | ① 确认 exa MCP 是否需 API key（访问 https://mcp.exa.ai/mcp 测试）；② 若需，补 `EXA_API_KEY` 到 `.env` |
| 与现有体系关系 | 产出报告归档到 `cairn/Reference/`（外部原始输入，仅追加），不替代任何量化模块 |

## 5. 不接入的理由（反向论证）

- **不接入 CI**: deep-research 是研究工具，非代码质量门禁，放 CI 无意义
- **不接入 CodeArts**: 当前环境不消费 MCP，配置了也不生效
- **不替代量化模块**: 康波/十五五/社保ETF 已有量化模型，deep-research 产出的是**研究素材**而非交易信号
- **避免重复**: TrendRadar 已覆盖大宗商品舆情，deep-research 仅扩展到政策/行业/国家队维度

## 6. 后续动作

| 动作 | 负责 | 状态 |
|---|---|---|
| 确认 exa MCP 认证需求 | 用户（访问 mcp.exa.ai 测试） | ⏳ 待用户 |
| 若需 key，补 EXA_API_KEY | 用户 | ⏳ 待用户 |
| 在 Claude Code CLI 试用 deep-research 做一次十五五政策研究 | 用户/Claude Code | ⏳ 可选 |
| 产出报告归档 cairn/Reference/ | Claude Code | ⏳ 可选 |

本评估归档，后续按需执行，不阻塞当前工作流。