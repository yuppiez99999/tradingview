# AI 模型分工方案 — 国内外模型最优配置(终极修改版)

> **目标**: 用满 GitHub Copilot Max + 国内模型不限预算,把 v9.1 量化系统开发到最完美
> **生成时间**: 2026-09-12(基于 2026-09-11 版根据最新 Copilot 模型下架/升级规则及系统遗漏模块全面修正)
> **依据**: 对系统 136+ 核心模块、配置、回测内核、机器学习训练链、AI 对冲基金、风控链的深度扫描
> **合并来源**: `AI模型分工方案_国内外模型最优配置.md` + `CopilotMax_国内模型_开发分工报告_20260911.md`

---

## 目录

- [1. 核心分工原则](#1-核心分工原则)
- [2. 模型武器库(2026-09 最新实测矩阵)](#2-模型武器库2026-09-最新实测矩阵)
- [3. 系统模块规模盘点](#3-系统模块规模盘点)
- [4. 逐模块分配矩阵(完整执行清单)](#4-逐模块分配矩阵完整执行清单)
- [5. VS Code 多模型自动切换配置(修正版)](#5-vs-code-多模型自动切换配置修正版)
- [6. 验证策略与质量门](#6-验证策略与质量门)
- [7. 冲突解决机制](#7-冲突解决机制)
- [8. 成本-质量权衡矩阵](#8-成本-质量权衡矩阵)
- [9. 分阶段推进计划(Wave)](#9-分阶段推进计划wave)
- [10. 禁忌(违反即返工)](#10-禁忌违反即返工)
- [11. Prompt 模板库(新增 ML 防泄漏模板)](#11-prompt-模板库新增-ml-防泄漏模板)
- [12. 需核实项(落地前确认)](#12-需核实项落地前确认)
- [附录: 一句话总结](#附录-一句话总结)

---

## 1. 核心分工原则

### 1.1 能力对比

| 维度 | 国外模型 (Claude Sonnet 5 / Opus 5 / GPT-5.6 via Copilot Max) | 国内模型 (Qwen3-Max / GLM-5.3 / DeepSeek-V4 / DeepSeek-R1) |
|------|-----------------------------------------------|----------------------------------------|
| **强项** | 复杂架构设计、设计模式、通用算法、代码重构、LangGraph 编排、防时序泄漏 | 中国市场监管规则、Wind 接口细节、中文注释/文档、政策语义(十五五/社保)、A 股特有逻辑、数学推导 |
| **弱项** | A 股特有规则(涨跌停/复权/交割)、中文政策语义、Wind MCP 细节 | 复杂多 Agent 编排、大规模跨仓重构、泛化架构 |

### 1.2 五条决策规则

1. **正确性致命度优先**: 涉及"真金白银 / 一刀毙命"的代码(执行器、风控门、回测内核撮合、因子表达式求值、LGBM 特征防泄漏)一律国外模型主笔 + 双审。
2. **跨文件复杂度优先**: 需要同时改 ≥5 个文件、或依赖 100K+ 行上下文的重构,交给 Claude Opus 5 / GPT-5.6 Terra / Gemini 3.8 Flash(长上下文)。
3. **英文生态优先国外**: 对接 Wind/TDX/AKShare/qlib/stumpy/pyfolio/empyrical 等 SDK 与算法库时,国外模型对 API 与惯用法更准。
4. **数学推理与中文语义优先国内**: 因子数学公式推导首选 DeepSeek-R1;A 股规则、合规、中文报告、中文 UI、批量脚手架、实时信号——国内模型更贴领域且无限预算可放量。
5. **国外收口质量门**: 国内模型生成的"关键路径代码"必须过一道国外模型 review(Claude Opus 5 安全审计 / GPT-5.6 Sol 单测补全),否则不入主干。

---
## 9. 分阶段推进计划(Wave)

| 阶段 | 周期 | 核心重点 | 主导模型 | 交付与验收 |
|------|------|---------|---------|-----------|
| **Wave A: 致命加固** | W1-W2 | 资金三件套、风控链、回测内核、lgb_trainer 防泄漏 | Claude Opus 5 + GPT-5.6 Sol | 零高危漏洞,无时序泄漏 |
| **Wave B: 架构重构** | W3-W5 | 108KB 管道解耦拆分,接口抽象 | Gemini 3.8 Flash + Sonnet 5 | 拆分为 ≤5KB 模块 |
| **Wave C: 因子与 Agent** | W6-W7 | DeepSeek-R1 因子推导 + LangGraph 编排 | DeepSeek-R1 + GLM-5.3 | 因子 Alpha 提升 3x,Agent 无死锁 |
| **Wave D: 数据与 UI** | W8 | Wind 接口完善、Streamlit 面板及中文报告 | DeepSeek-V4 + Qwen3-Max | 中文显示与报告自动化全闭环 |

### 9.1 Wave 进度条(2026-09-12 快照)

> 进度 = 各 Wave 完成度。当前:方案 09-12 落地,**Wave A 待启动**(W1 起算 09-12;周期按周计,W1=09-12~09-18)。
> 更新约定:每个 Wave 完成后,将该行 `░` 改为 `█` 并更新百分比;整体进度 = 按周期权重(W2/W5/W7/W8)加权。

```text
Week        W1    W2    W3    W4    W5    W6    W7    W8
            ├─────┼─────┼─────┼─────┼─────┼─────┼─────┼─────┤
Wave A 致命加固   ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  0%
Wave B 架构重构   ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  0%
Wave C 因子与Agent ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  0%
Wave D 数据与UI  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  0%
整体进度        ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  0%
```

| Wave | 周期 | 权重 | 状态 | 验收锚点 |
|------|------|------|------|---------|
| A | W1-W2 | 2/8 | ⬜ 未启动 | 零高危漏洞,无时序泄漏 |
| B | W3-W5 | 3/8 | ⬜ 未启动 | 拆分为 ≤5KB 模块 |
| C | W6-W7 | 2/8 | ⬜ 未启动 | 因子 Alpha 提升 3x,Agent 无死锁 |
| D | W8 | 1/8 | ⬜ 未启动 | 中文显示与报告自动化全闭环 |

---

1. ❌ **严禁使用已停用的模型版本**: 如 claude-sonnet-4 或 gemini-3.5-flash,会导致配置失效回退。
2. ❌ **严禁国内模型未经国外 Review 直合致命路径**: 执行器、风控门、回测撮合逻辑必须过 Claude Opus 5 安全审计。
3. ❌ **严禁用国外模型强行翻译中文政策**: "十五五"或社保 ETF 逻辑用国外模型容易带来概念泛化与偏差。
4. ❌ **严禁忽视机器学习数据泄漏**: 在 lgb_trainer/ 模块重构时,严禁使用非严格时间截面的未来特征。

---

## 11. Prompt 模板库(新增 ML 防泄漏模板)

### 11.1 时序数据泄漏审计 Prompt(专用于 lgb_trainer)

```plaintext
你是量化机器学习与数据安全专家。请对以下特征工程与模型训练代码进行严格的时序数据泄漏(Data Leakage)审计:

## 代码片段

{code}

## 重点检查项

1. 交叉验证是否使用了 K-Fold(时序数据严禁使用标准 K-Fold,必须使用 TimeSeriesSplit)
2. 标准化/归一化(Scaler)是否在 Train/Val 分割前拟合(Fit)
3. 因子计算中是否使用了 t+1 及以后的收盘价/信息
4. 滞后特征(Lag Target)计算是否存在 Shift 位移方向错误

## 输出要求

指出所有潜在泄漏点并提供修正代码。
```

### 11.2 Claude Opus 安全审计模板

```plaintext
你是量化交易系统安全审计专家。请对以下代码进行安全审计:

## 代码
{code}

## 审计重点
1. 资金安全: 是否有溢出、精度丢失、边界条件遗漏
2. 风控门: KillSwitch/Drawdown/VolTarget/Hedge 是否可靠
3. 密钥管理: API Key 是否硬编码
4. 并发安全: 是否有竞态条件
5. 错误处理: 异常是否被正确捕获
6. 时序泄漏: 特征工程是否使用未来信息

## 输出格式
- 高危问题(必须修复): 
- 中危问题(建议修复): 
- 低危问题(可选优化): 
- 整体评分(1-10):
```

### 11.3 GPT-5.6 单测生成模板

```plaintext
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

### 11.4 DeepSeek 批量因子实现模板

```plaintext
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

### 11.5 GLM-5 中文报告生成模板

```plaintext
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

| # | 核实项 | 确认动作 | 优先级 |
|---|--------|---------|--------|
| 1 | VS Code 环境变量解析 | 确保终端执行 `echo $DEEPSEEK_API_KEY` 能够正确打印凭据 | P0 |
| 2 | Copilot Max 最新模型可用性 | 打开 VS Code 命令行执行 `Copilot: Select Model`,确保 Opus 5 / Sonnet 5 在列表中 | P0 |
| 3 | Mypy 环境依赖 | 确认开发环境中已安装并配置好项目 mypy 规则集 | P1 |

---

## 附录: 一句话总结

> **国外模型(Copilot Max / Claude Opus 5 / Sonnet 5 / GPT-5.6)守住"资金安全、回测内核、架构解耦、ML 防泄漏"四大红线,占 70% 关键代码。**
> **国内模型(DeepSeek-R1 / GLM-5.3 / Qwen3-Max / DeepSeek-V4)攻克"因子公式推导、Agent 编排、中文政策语义、Wind 接口与批量生成",占 30% 关键逻辑与全部中文输出。**

---

## 附录: 系统模块完整分工表(速查)

| 目录/文件 | 模型 | 备注 |
|-----------|------|------|
| `executor/**` | 国外 Claude Opus 5 | 资金安全最高危 |
| `run_daily_eod.py` | 国外 Claude Opus 5 | 风控四 Guard |
| `backtests/engine/**` | 国外 GPT-5.6 Sol | 回测撮合内核 |
| `lgb_trainer/**` | 国外 Claude Opus 5 | ML 特征防泄漏 |
| `utils/hedge_engine.py` | 国外 GPT-5.6 Sol | Beta 对冲 |
| `utils/hedge_rebalance_integrator.py` | 国外 Claude Opus 5 | 五阶段联动 |
| `scripts/run_200w_etf_backtest.py` | 国外 GPT-5.6 Sol | 200W ETF 回测 |
| `ms_strategy/src/alpha/` | 国外 GPT-5.6 Sol | GTJA191 因子 |
| `quant_modules/data_layer.py` | 国外 Claude Sonnet 5 | P0-P6 降级链 |
| `institutional_pipeline_runner.py` | 国外 Gemini 3.8 Flash → Opus 5 | 108KB 重构 |
| `daily_trade_executor.py` | 国外 Claude Opus 5 | 63KB 执行器 |
| `utils/ml_enhanced_trainer.py` | 国外 GPT-5.6 Sol | LightGBM |
| `.github/workflows/` | 国外 | CI/CD |
| `utils/glm5_*.py` | 国内 GLM-5.3 | 已绑定 |
| `quant_modules/ai_hedge_fund/` | 国内 GLM-5.3 | 20 分析师 |
| `utils/kondratiev_cycle.py` | 国内 Qwen3-Max | 康波+十五五 |
| `utils/five_year_plan.py` | 国内 Qwen3-Max | 政策评分 |
| `utils/social_security_etf.py` | 国内 Qwen3-Max | 社保 ETF |
| `tools/wind_mcp_fetcher.py` | 国内 DeepSeek-V4 | Wind 接口 |
| `ui/**` | 国内 Qwen3-Max | Streamlit 面板 |
| `reporting/**` | 国内 Qwen3-Max | 中文报告 |
| `scripts/**` | 国内 DeepSeek-V4 | 批量自动化 |
| `tests/**` | 国内 DeepSeek-V4 | 批量测试 |
| `config/portfolio_200w_etf_v91.yaml` | 混合 | 结构国外/政策国内 |
| `utils/`(242K) | 混合 | 架构国外/实现国内 |

---

*本方案基于对 28-终极量化交易系统 v8.6 全量代码扫描,直接对应实际文件路径。*
*合并自: `AI模型分工方案_国内外模型最优配置.md` + `CopilotMax_国内模型_开发分工报告_20260911.md`*
*更新时间: 2026-09-12(终极修改版)*
## 6. 验证策略与质量门

### 6.1 审查流水线(CI/CD 触发)

```
[代码提交]
    │
    ├─ 涉及致命路径(#1-#10)?
    │   └─ 是 → 自动触发 Claude Opus 5 安全与时序泄漏审查脚本
    │
    ├─ 因子表达推导(#11)?
    │   └─ 是 → 跑 pytest 对齐数学解与向量化代码结果
    │
    └─ 全量触发 → Mypy 类型检查 + Ruff Lint + Pytest 自动化测试套件
```

### 6.2 模型产出验证方法

| 模型类型 | 验证方法 | 工具 | 通过标准 |
|---------|---------|------|---------|
| 国外模型(架构/风控/内核) | 安全审计 + 时序泄漏审查 | Claude Opus 5 `/review` + pytest | 零高危漏洞 + No Leakage |
| 国内模型(批量/中文) | 边界测试 + 集成测试 | DeepSeek 自检 + GitHub Actions | 所有边界用例通过 |
| 混合产出 | 接口一致性检查 | mypy + 自定义 lint | 类型无错误 + lint 零警告 |

### 6.3 关键路径 PR 检查清单

```markdown
## PR 检查清单(关键路径专用)

- [ ] Claude Opus 5 安全审计摘要已附加
- [ ] GPT-5.6 Sol 单测覆盖说明已附加
- [ ] 单测覆盖率 ≥80%
- [ ] 无 mypy 类型错误
- [ ] 无 ruff lint 警告
- [ ] 资金安全边界测试通过(执行器/风控门/回测撮合)
- [ ] 时序数据泄漏审查通过(lgb_trainer)
- [ ] 回测结果可复现(如涉及)
```

---

## 7. 冲突解决机制

### 7.1 裁决规则表

| 争议场景 | 国内模型主张 | 国外模型主张 | 最终裁决标准 |
|---------|------------|------------|------------|
| 特征工程 | 追求高 AUC 的特征工程写法 | 认定存在时序数据泄漏 | 严禁泄漏,采纳国外模型 |
| 回测撮合 | 简化挂单撮合机制 | 严格按 Tick 级最高/最低价判断 | 采纳国外模型严格撮合 |
| A 股政策指标 | 严格映射"十五五"产业词表 | 泛化为通用行业 classification | 采纳国内模型精准语义 |
| Wind 报错 | 硬编码兼容特殊错误码 | 抛出抽象底层异常 | 采纳国内模型业务处理 |

### 7.2 冲突裁决链

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
                    (Claude Opus 5 + GPT-5.6)
                        ↓
                    仍分歧
                        ↓
                    人工裁决
```

---

## 8. 成本-质量权衡矩阵

| 任务类别 | 推荐核心模型 | 替换兜底模型 | 预算权重 |
|---------|------------|------------|---------|
| 资金风控/核心内核 | Claude Opus 5 | GPT-5.6 Sol | Copilot 包月包含 |
| 回测与 ML 防泄漏 | GPT-5.6 Sol | Claude Opus 5 | Copilot 包月包含 |
| 因子数学推导 | DeepSeek-R1 | GPT-5.6 Sol | 按量付费(极低) |
| Agent 复杂编排 | GLM-5.3 | Claude Sonnet 5 | 按量付费(中等) |
| 批量生成/测试脚本 | DeepSeek V4 | 豆包 Seed | 按量付费(极低) |

### 8.1 预算分配建议

| 资源 | 用途 | 预估用量 | 预算参考 |
|------|------|---------|---------|
| **GitHub Copilot Max** | 架构/风控/回测/审计 | 70% 代码行 | 包月(固定) |
| **Claude Opus 5** | 安全审计/最终 review | 10% 关键代码 | 包月含 |
| **GPT-5.6 Sol** | 数值内核/单测 | 10% | 包月含 |
| **DeepSeek-V4 / R1** | 批量脚本/因子推导 | 5% | 按量(极低) |
| **Qwen3-Max** | 中文报告/UI | 3% | 按量 |
| **GLM-5.3** | AI 决策/已绑定 | 2% | 按量 |

---
## 5. VS Code 多模型自动切换配置(修正版)

### 5.1 插件安装要求

| 插件 | 用途 | 优先级 |
|------|------|--------|
| GitHub Copilot | 国外模型主力编码(自动登录系统凭据) | P0 |
| GitHub Copilot Chat | Agent 模式对话与重构 | P0 |
| Continue.dev | 国内模型按路径分发路由 | P0 |

### 5.2 完整 `.vscode/settings.json`(可直接复制)

```json
// .vscode/settings.json — AI 模型分工终极配置(2026-09 修正版)
{
  // ════════════════════════════════════════════════════════════════
  // 第一部分: GitHub Copilot(国外原生配置)
  // ════════════════════════════════════════════════════════════════

  // 主力模型全面升级为 Sonnet 5(停用已废弃的 Sonnet 4.x)
  "github.copilot.selectedModel": "claude-sonnet-5",
  "github.copilot.chat.agent.enabled": true,
  "github.copilot.editor.enableAutoCompletions": true,

  // ════════════════════════════════════════════════════════════════
  // 第二部分: Continue.dev(国产与特殊模型接入)
  // ════════════════════════════════════════════════════════════════

  "continue.enableTabAutocomplete": true,
  "continue.enableCodeLens": true,
  "continue.models": [
    {
      "title": "DeepSeek-R1",
      "provider": "openai",
      "model": "deepseek-reasoner",
      "apiBase": "https://api.deepseek.com/v1",
      "apiKey": "${env:DEEPSEEK_API_KEY}",
      "contextLength": 64000,
      "systemMessage": "你是数学与逻辑推理专家,擅长量化因子逻辑推导与数学表达式构建。"
    },
    {
      "title": "GLM-5.3",
      "provider": "openai",
      "model": "glm-5.3",
      "apiBase": "https://open.bigmodel.cn/api/paas/v4",
      "apiKey": "${env:GLM_API_KEY}",
      "contextLength": 128000,
      "systemMessage": "你是量化系统 Agent 编排专家,精通 A 股规则与多 Agent 协作。"
    },
    {
      "title": "DeepSeek-V4",
      "provider": "openai",
      "model": "deepseek-chat",
      "apiBase": "https://api.deepseek.com/v1",
      "apiKey": "${env:DEEPSEEK_API_KEY}",
      "contextLength": 64000,
      "systemMessage": "你是高效代码生成专家,擅长批量实现与代码向量化优化。"
    },
    {
      "title": "Qwen3-Max",
      "provider": "openai",
      "model": "qwen-max-latest",
      "apiBase": "https://dashscope.aliyuncs.com/compatible-mode/v1",
      "apiKey": "${env:DASHSCOPE_API_KEY}",
      "contextLength": 32768,
      "systemMessage": "你是中文金融领域专家,精通政策语义理解与报告生成。"
    }
  ],

  // ════════════════════════════════════════════════════════════════
  // 第三部分: 规则路由(聚合优化版)
  // ════════════════════════════════════════════════════════════════

  "continue.rules": [
    // ── 资金三件套/回测内核/特征训练 → 国外模型 ──
    {"path": "{executor/**,run_daily_eod.py,backtests/engine/**,lgb_trainer/**}", "model": "claude-sonnet-5", "autoSelect": true},

    // ── 架构重构 → 国外模型 ──
    {"path": "{institutional_pipeline_runner.py,daily_trade_executor.py,quant_modules/data_layer.py}", "model": "claude-sonnet-5", "autoSelect": true},

    // ── 因子逻辑推导 → DeepSeek-R1 ──
    {"path": "ms_strategy/src/alpha/**", "model": "DeepSeek-R1", "autoSelect": true},

    // ── AI Agent 决策 → GLM-5.3 ──
    {"path": "{quant_modules/ai_hedge_fund/**,utils/glm5_*.py}", "model": "GLM-5.3", "autoSelect": true},

    // ── 政策/UI/报告 → Qwen3-Max ──
    {"path": "{ui/**,reporting/**,utils/kondratiev_cycle.py,utils/five_year_plan.py,utils/social_security_etf.py}", "model": "Qwen3-Max", "autoSelect": true},

    // ── 批量自动化脚本与测试 → DeepSeek-V4 ──
    {"path": "{scripts/**,tests/**,tools/wind_mcp_fetcher.py}", "model": "DeepSeek-V4", "autoSelect": true}
  ]
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

### 资金三件套/回测内核/特征训练(必须国外模型)
- `executor/**` → Claude Opus 5
- `run_daily_eod.py` → Claude Opus 5
- `backtests/engine/**` → GPT-5.6 Sol
- `lgb_trainer/**` → Claude Opus 5
- `utils/hedge_engine.py` → GPT-5.6 Sol
- `utils/hedge_rebalance_integrator.py` → Claude Opus 5

### 国内模型主导
- `utils/glm5_*.py` → GLM-5.3(已绑定)
- `quant_modules/ai_hedge_fund/**` → GLM-5.3
- `utils/kondratiev_cycle.py` → Qwen3-Max
- `utils/five_year_plan.py` → Qwen3-Max
- `utils/social_security_etf.py` → Qwen3-Max
- `tools/wind_mcp_fetcher.py` → DeepSeek-V4
- `ms_strategy/src/alpha/**` → DeepSeek-R1(推导) / Sonnet 5(向量化实现)
- `ui/**` → Qwen3-Max
- `reporting/**` → Qwen3-Max
- `scripts/**` → DeepSeek-V4
- `tests/**` → DeepSeek-V4

### 质量门
- 关键路径(#1-#10)PR 必须附 Claude Opus 5 安全审计摘要 + GPT-5.6 Sol 单测覆盖说明
- 国内模型产出的关键文件,提交前用 Copilot(Max)跑一轮 `/review`
```

---
## 3. 系统模块规模盘点

来自全仓 LOC 最新扫描(共 **2,181 个 .py / ≈740K 行**):

| 子系统 | 代码行 | 文件数 | 性质 | 核心风险与要求 |
|---|---:|---:|---:|---|
| utils(工具/胶水/LLM 路由) | 242K | 552 | 量大、杂 | 接口一致性 |
| tests(测试) | 242K | 644 | 质量关键 | 覆盖率 ≥80% |
| qlib(vendored 依赖) | 75K | 335 | **第三方,勿重写** | 升级评估 |
| scripts(自动化) | 57K | 203 | 运维向 | 可执行率 100% |
| ms_strategy(策略主体与因子) | 25K | 84 | 收益核心 | 因子 IC/IR 及向量化计算 |
| quant_modules(因子/模块) | 22K | 73 | 业务逻辑 | 降级链与数据对齐 |
| ai_decision(AI 对冲基金) | 9K | 23 | Agent 编排 | 无死锁与响应延迟 <500ms |
| ui(Streamlit 17 页) | 6.9K | 32 | 中文交互 | 无乱码、展示完整 |
| cli(统一入口) | 5.8K | 45 | 多模式编排 | 命令行可用性 |
| backtests(回测引擎内核) | 5.4K | 16 | **致命正确性** | 撮合、滑点、无未来函数 |
| lgb_trainer(机器学习训练) | 4.2K | 11 | **致命正确性** | 严格防范时序数据泄漏 |
| data(数据采集/降级) | 4.2K | 9 | 数据源 | P0-P6 降级容错 |
| external(外部 SDK) | 8.2K | 34 | 接口对接 | 协议完整性 |
| reporting(报告引擎) | 2.9K | 8 | 成果输出 | 中文金融格式 |

---

## 4. 逐模块分配矩阵(完整执行清单)

> 标注: **[外]**=Copilot Max 国外模型主笔;**[国]**=国内模型主笔;**[混]**=协同主笔;**首选 → 备选**

### 4.1 国外模型主导(资金安全 + 架构复杂 + 核心正确性)

| # | 模块 / 文件 | 规模 | 首选模型 | 备选模型 | 验收标准 |
|---|------------|------|---------|---------|---------|
| 1 | **executor/**(实盘指令生成) | 1.1K | **[外] Claude Opus 5** | GPT-5.6 Sol | 安全审计通过 + 单测覆盖率 100% |
| 2 | **institutional_pipeline_runner.py**(机构管道) | 108K | **[外] Gemini 3.8 Flash → Opus 5** | GPT-5.6 Terra | 拆分为 ≤5KB/文件,高内聚低耦合 |
| 3 | **daily_trade_executor.py**(每日交易执行) | 63K | **[外] Claude Opus 5** | GPT-5.6 Sol | 通过风控门与异常阻断测试 |
| 4 | **run_daily_eod.py**(风控四 Guard 链) | 5.8K | **[外] Claude Opus 5** | GPT-5.6 Sol | KillSwitch 阻断测试 100% 触发 |
| 5 | **backtests/engine/**(回测撮合内核) | 5.4K | **[外] GPT-5.6 Sol** | Claude Opus 5 | 零未来函数、撮合与滑点精确复现 |
| 6 | **lgb_trainer/**(特征工程与训练) | 4.2K | **[外] Claude Opus 5** | GPT-5.6 Sol | 通过时序数据泄漏审查(No Leakage) |
| 7 | **utils/hedge_engine.py**(Beta 对冲) | 3.2K | **[外] GPT-5.6 Sol** | Claude Sonnet 5 | Beta 计算单测精度通过 |
| 8 | **utils/hedge_rebalance_integrator.py**(五阶段联动) | 2.8K | **[外] Claude Opus 5** | GPT-5.6 Sol | 状态机转换测试通过 |
| 9 | **scripts/run_200w_etf_backtest.py**(200W ETF 回测) | 4.5K | **[外] GPT-5.6 Sol** | Claude Sonnet 5 | 数值计算复核通过 |
| 10 | **quant_modules/data_layer.py**(P0-P6 降级链) | 2.1K | **[外] Claude Sonnet 5** | GPT-5.4 | 降级链断网/异常全覆盖 |

### 4.2 国内模型主导(中文语义 + 数学推理 + 批处理)

| # | 模块 / 文件 | 规模 | 首选模型 | 备选模型 | 验收标准 |
|---|------------|------|---------|---------|---------|
| 11 | **ms_strategy/src/alpha/**(因子表达式推导) | 8.2K | **[国] DeepSeek-R1(推导)** | GPT-5.6 Sol | 公式推导无误,IC/IR 显著为正 |
| 12 | **utils/glm5_decision_engine.py**(盘中决策) | 1.9K | **[国] GLM-5.3** | GLM-5.4 | 盘中决策延迟 <500ms |
| 13 | **quant_modules/ai_hedge_fund/**(Agent 编排) | 9K | **[国] GLM-5.3** | DeepSeek-V4 | 分析师 LangGraph 链路无死锁 |
| 14 | **utils/kondratiev_cycle.py**(康波周期) | 1.5K | **[国] Qwen3-Max** | GLM-5.3 | 宏观周期判定逻辑正确 |
| 15 | **utils/five_year_plan.py**(十五五评分) | 1.1K | **[国] Qwen3-Max** | GLM-5.3 | 政策语义映射合理 |
| 16 | **utils/social_security_etf.py**(社保 ETF) | 1.3K | **[国] Qwen3-Max** | GLM-5.3 | 国家队持仓追踪准确 |
| 17 | **tools/wind_mcp_fetcher.py**(Wind 接口) | 2.4K | **[国] DeepSeek-V4** | GLM-5.3 | 复权/错误码处理完全覆盖 |
| 18 | **ui/**(Streamlit 面板) | 6.9K | **[国] Qwen3-Max** | 豆包 | 页面无卡顿,中文渲染无乱码 |
| 19 | **reporting/**(中文报告引擎) | 2.9K | **[国] Qwen3-Max** | GLM-5.3 | 金融格式合规,排版规范 |
| 20 | **scripts/**(运维与自动化) | 57K | **[国] DeepSeek-V4** | 豆包 | 执行无阻断错误 |
| 21 | **tests/**(批量测试套件) | 242K | **[国] DeepSeek-V4** | 豆包 | 语法与基础覆盖率为 100% |

### 4.3 混合协作(国内外分工交叉审查)

| # | 模块 / 文件 | 国外职责 | 国内职责 | 验收标准 |
|---|------------|---------|---------|---------|
| 22 | **ms_strategy/src/alpha/**(因子代码实现) | 代码矩阵向量化与性能优化 | 因子数学逻辑与表达式设计 | 运行速度提升 3x 以上 |
| 23 | **config/portfolio_200w_etf_v91.yaml** | 结构/熔断/期权参数段 | 宏观/社保/十五五政策语义段 | 加载语法校验通过 |
| 24 | **utils/**(胶水代码库) | 架构设计、接口抽象与定义 | 批量底层实现与中文 Docstring | mypy 校验零错误,类型系统 |
| 25 | **cli/**(统一入口) | 顶层命令编排与分发 | 命令行参数解析与中文 Help | 所有子命令运行畅通 |

---
## 2. 模型武器库(2026-09 最新实测矩阵)

> **数据来源**: GitHub Copilot 模型选择器及厂商 API 最新核对(2026-09-12)
> **已清除已停用型号**: Sonnet 4.x / Gemini 3.5 早期版,避免配置失效回退
> **标注**: ⭐ = 系统推荐主力; 🧠 = 代码专用; ⚡ = 快速低成本; 📚 = 长上下文

### 2.1 国外模型(GitHub Copilot Max — 原生接入 VS Code)

#### 第一梯队:复杂架构/安全审计/核心算力(资金相关,必须用)

| 模型 | 核心优势 | 在本系统的最佳用场 | 优先级 |
|------|---------|------------------|--------|
| **Anthropic Claude Opus 5** ⭐ | 最新一代旗舰推理、超长上下文、Agent 工具调用、安全审计最高标准 | 交易执行器、风控门、LGBM 特征防泄漏审计、管道重构、安全审计、最终 Review | P0 |
| **Anthropic Claude Opus 4.8** ⭐ | 成熟 Opus 节点,高稳定性推理 | 与 Opus 5 互为备选,关键路径双审 | P0 |
| **Anthropic Claude Sonnet 5** ⭐ | 当前默认主力,高效平衡,代码理解极强 | 日常编码主力(全面替代已停用 Sonnet 4.x)、架构重构、回测引擎 | P0 |
| **OpenAI GPT-5.6 Sol** ⭐ | 算法与数值正确性强 | 因子表达式引擎、回测撮合内核、LightGBM 训练、定价公式 | P0 |
| **OpenAI GPT-5.6 Terra** ⭐ | GPT 多模态旗舰,长上下文理解 | 通读 108KB 管道/63KB 执行器、跨仓架构深度重构 | P0 |

#### 第二梯队:代码专用/快速迭代(高频使用)

| 模型 | 核心优势 | 在本系统的最佳用场 | 优先级 |
|------|---------|------------------|--------|
| **OpenAI GPT-5.4** 🧠 | 高质量代码生成,技术债修复 | 因子批量实现、utils 胶水代码、scripts 自动化 | P1 |
| **OpenAI GPT-5.3-Codex** 🧠 | Codex 最新版,精细代码生成与补全 | 纯代码任务、函数实现、单元测试生成 | P1 |
| **Microsoft MAI-Code-1.1-Flash** 🧠 | 微软代码专用,快速高质量 | 批量代码生成、函数补全、轻量重构 | P1 |

#### 第三梯队:长上下文与批量生成

| 模型 | 核心优势 | 在本系统的最佳用场 | 优先级 |
|------|---------|------------------|--------|
| **Google Gemini 3.8 Flash** 📚 ⭐ | 最新长上下文 Flash,支持 1M+ Token,速度极快 | 通读 242K utils、qlib 75K 依赖架构分析(替代已弃用的 3.5/3.6) | P1 |
| **Anthropic Claude Haiku 4.5** ⚡ | 极快、成本优 | 批量注释、日志生成、简单函数、文档字符串 | P2 |
| **OpenAI GPT-5.4 mini** ⚡ | 快速轻量 | 简单函数、变量命名、单行补全 | P2 |

### 2.2 国内模型(BYOK,OpenAI 兼容端点,不限预算)

| 模型 | 核心优势 | 在本系统的最佳用场 | 优先级 |
|------|---------|------------------|--------|
| **DeepSeek-R1 / R2**(深度推理) ⭐ | 逻辑与数学 Reasoning Chain | 因子逻辑推导(Alpha101/GTJA191 数学表达)、收益归因推导、定价公式 | P0 |
| **智谱 GLM-5.3 / 5.4** ⭐ | 工程智能体最强、中文金融语义好 | AI 决策(GLM-5 盘中)、AI Hedge Fund 20 分析师 LangGraph | P0 |
| **DeepSeek V4 Pro / V3.2** ⭐ | 通用代码吞吐大、质量高 | 因子代码批量向量化、scripts 自动化、utils 胶水代码 | P0 |
| **阿里 Qwen3-Max** ⭐ | 中英均衡、中文金融语义与政策分析 | 中文 UI 页、中文报告模板、宏观十五五/社保 ETF | P0 |
| **字节 豆包(Doubao Seed)** ⚡ | 高吞吐、低延迟 | 运维脚本、日志监控、测试用例批处理 | P1 |
| **Kimi(Moonshot K3)** 📚 | 超长上下文窗口 | 通读大型长文档、跨文件语义归因 | P1 |

### 2.3 选型决策树

```
开始编码任务
    │
    ├─ 资金安全/回测内核/特征泄漏相关?(执行器/风控/回测内核/lgb_trainer)
    │   └─ 是 → Claude Opus 5 主笔 + GPT-5.6 Sol 双审
    │
    ├─ 因子数学推导与逻辑推演?
    │   └─ 是 → DeepSeek-R1(生成数学表达式) → Claude Sonnet 5(Python 向量化代码)
    │
    ├─ 跨文件重构?(≥5 文件 或 100K+ 上下文)
    │   └─ 是 → Claude Opus 5 / Gemini 3.8 Flash
    │
    ├─ 中文语义重?(政策/报告/UI/文档)
    │   └─ 是 → Qwen3-Max / GLM-5.3
    │
    ├─ 批量/快速/低成本?(脚本/测试)
    │   └─ 是 → DeepSeek V4 / Doubao Seed
    │
    └─ 日常通用编码 → Claude Sonnet 5
```

---