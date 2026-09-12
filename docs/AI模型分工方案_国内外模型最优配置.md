# AI 模型分工方案 — 国内外模型最优配置

> **目标**:用满 GitHub Copilot Max + 国内模型不限预算,把 v9.1 量化系统开发到最完美
> **生成时间**: 2026-09-11
> **依据**: 对系统 136+ 核心模块、配置、回测引擎、AI 对冲基金、风控链的深度扫描

---

## 1. 核心分工原则

| 维度 | 国外模型 (Claude Sonnet 4 / GPT-5 via Copilot Max) | 国内模型 (Qwen-Max / GLM-5 / DeepSeek-V4) |
|------|-----------------------------------------------|----------------------------------------|
| **强项** | 复杂架构设计、设计模式、通用算法、代码重构、LangGraph 编排、英文文档 | 中国市场监管规则、Wind 接口细节、中文注释/文档、政策语义(十五五/社保)、A 股特有逻辑 |
| **弱项** | A 股特有规则(涨跌停/复权/交割)、中文政策语义、Wind MCP 细节 | 复杂多 agent 编排、大规模重构、泛化架构 |

---

## 2. 模块级分工清单

### 2.1 国外模型为主 (GitHub Copilot Max — 订阅已买,全力用)

| 模块 | 文件路径 | 为什么给国外 |
|------|---------|-------------|
| **AI Hedge Fund 多 Agent 编排** | `quant_modules/ai_hedge_fund/` (LangGraph 20 位分析师) | LangGraph 状态机 + 多 agent 辩论是国外模型主场,Claude 在复杂编排上显著优于国产 |
| **回测引擎核心算法** | `scripts/run_200w_etf_backtest.py` (Phase 2 四策略对比) | BS 期权定价、日度再平衡复利、Calmar/夏普计算 — 通用金融工程,国外更稳 |
| **Alpha 因子引擎** | `ms_strategy/src/alpha/` (GTJA191 + 11 大类因子) | 因子正交化、IC/IR 衰减、行业中性化 — 标准量化方法论 |
| **Streamlit UI** | `ui/app.py` + `ui/pages/` (14 页面板) | React 式布局、图表交互、暗色主题 — 国外模型 UI 代码质量高一个量级 |
| **数据连接器抽象层** | `quant_modules/data_layer.py` + `connectors.py` | 多源降级链(P0-P6)的优雅抽象 — 设计模式强依赖 |
| **对冲再平衡联动** | `utils/hedge_rebalance_integrator.py` (五阶段联动) | 状态机 + 多目标优化 — 复杂逻辑编排 |
| **风控四 Guard 链** | `run_daily_eod.py` (KillSwitch/Drawdown/VolTarget/Hedge) | 并发安全、熔断器模式 — 国外模型在并发/系统代码上更强 |
| **ML 训练管道** | `utils/ml_enhanced_trainer.py` + `lgb_trainer/` | LightGBM/贝叶斯超参 — 标准 ML 工程 |
| **CI/CD + 质量门禁** | `.github/workflows/` + `scripts/pre_commit_check.py` | ruff/mypy/bandit/pytest — 国外生态原生 |

### 2.2 国内模型为主 (Qwen-Max / GLM-5 / DeepSeek — 不限预算,全力用)

| 模块 | 文件路径 | 为什么给国内 |
|------|---------|-------------|
| **Wind MCP 取数** | `tools/wind_mcp_fetcher.py` | Wind 接口是国产数据源,国内模型更懂 `price_type=1` 前复权、SSE 分钟协议、Wind 错误码语义 |
| **GLM-5 决策引擎** | `utils/glm5_decision_engine.py` + `glm5_client.py` | 系统已原生绑定 GLM-5,多模型路由 — 继续用国产,别换 |
| **康波周期分析** | `utils/kondratiev_cycle.py` | 康波+十五五+行业轮动 — 高度依赖中国政策语义,国产模型理解更深 |
| **十五五规划评分** | `utils/five_year_plan.py` | 政策对齐评分 — 必须国产,国外模型读不懂"国家战略方向" |
| **社保基金 ETF 追踪** | `utils/social_security_etf.py` | 国家队信号 + 社保风格分类 — 纯中国语境 |
| **期权数据缺口修复** | `utils/option_data_fetcher.py` | σ 代理链(rv_proxy → chain_atm_iv → 兜底 0.20) — 国内模型更懂 Wind 期权链现状 |
| **中文注释/文档/报告** | 所有 `*.md` 报告 + 代码注释 | 中文战报、诊脉书风格、GBK 编码兼容 — 国产模型中文输出质量碾压 |
| **A 股规则硬编码** | 涨跌停限制(10%/20%)、T+1、整手 100 份、复权口径 | 这些规则国产模型"天然知道",国外模型会漏细节 |

### 2.3 混合协作 (国内外各干一半,交叉审查)

| 模块 | 文件路径 | 分工方式 |
|------|---------|---------|
| **v9.1 主配置** | `config/portfolio_200w_etf_v91.yaml` | 国内写政策语义段(十五五/社保/康波注释),国外写结构段(权重/熔断/期权参数) |
| **机构管道主入口** | `institutional_pipeline_runner.py` (108KB) | 国外做架构重构拆小,国内补 A 股 EOD 业务逻辑 |
| **每日交易执行** | `daily_trade_executor.py` (63KB) | 国外做执行算法,国内做合规检查(涨跌停/停牌/整手) |
| **对冲引擎** | `utils/hedge_engine.py` (多指数 Beta 加权) | 国外做 Beta 计算+成本过滤,国内做 IC/IM/IF 合约乘数/交割日 |

---

## 3. 预算分配建议

| 资源 | 用途 | 预估用量 |
|------|------|---------|
| **GitHub Copilot Max** (Claude Sonnet 4) | 每日主力编码 — 架构/重构/UI/回测/Agent 编排 | 70% 代码行 |
| **Qwen-Max / 通义千问** | 中文报告生成、政策语义模块、Wind 接口调试 | 15% 代码行 + 全部中文文档 |
| **GLM-5** (系统已绑定) | 盘中实时决策、多模型路由、A 股规则校验 | 10% 代码行 |
| **DeepSeek-V4** | 代码审查(国产视角查漏)、中文注释补全 | 5% 代码行 |

---

## 4. VS Code 多模型路由配置

### 4.1 settings.json

```jsonc
// .vscode/settings.json — 多模型路由配置
{
  // 主力模型: GitHub Copilot (Claude Sonnet 4)
  "github.copilot.selectedModel": "claude-sonnet-4",
  
  // 国产模型通过 Continue.dev 插件接入
  "continue.models": [
    {
      "title": "Qwen-Max-CN",
      "provider": "openai",
      "model": "qwen-max-latest",
      "apiBase": "https://dashscope.aliyuncs.com/compatible-mode/v1",
      "contextLength": 32768
    },
    {
      "title": "GLM-5",
      "provider": "openai",
      "model": "glm-5",
      "apiBase": "https://open.bigmodel.cn/api/paas/v4"
    },
    {
      "title": "DeepSeek-V4",
      "provider": "openai",
      "model": "deepseek-v4",
      "apiBase": "https://api.deepseek.com/v1"
    }
  ],
  
  // 按路径自动切换模型 (核心规则)
  "continue.rules": [
    {"path": "utils/kondratiev_cycle.py", "model": "Qwen-Max-CN"},
    {"path": "utils/five_year_plan.py", "model": "Qwen-Max-CN"},
    {"path": "utils/social_security_etf.py", "model": "Qwen-Max-CN"},
    {"path": "tools/wind_mcp_fetcher.py", "model": "Qwen-Max-CN"},
    {"path": "utils/glm5_*.py", "model": "GLM-5"},
    {"path": "quant_modules/ai_hedge_fund/**", "model": "claude-sonnet-4"},
    {"path": "ui/**", "model": "claude-sonnet-4"},
    {"path": "scripts/run_200w_etf_backtest.py", "model": "claude-sonnet-4"},
    {"path": "ms_strategy/**", "model": "claude-sonnet-4"},
    {"path": "docs/**", "model": "Qwen-Max-CN"}
  ]
}
```

### 4.2 推荐插件组合

| 插件 | 用途 | 优先级 |
|------|------|--------|
| **GitHub Copilot** | 国外模型主力编码 (Claude Sonnet 4) | P0 |
| **Continue.dev** | 国产模型接入 + 按路径路由 | P0 |
| **GitLens** | 代码溯源 + 模型产出标记 | P1 |
| **Error Lens** | 实时错误高亮 (多模型协作时定位快) | P1 |

---

## 5. 禁忌 (违反即返工)

| 禁忌 | 原因 |
|------|------|
| ❌ 用国外模型写十五五/社保/康波模块 | 政策语义理解偏差,输出"正确但不对味" |
| ❌ 用国内模型写 LangGraph 多 Agent 编排 | 复杂状态机容易逻辑漏洞 |
| ❌ 用国外模型处理 Wind MCP 接口细节 | 复权/分钟协议/错误码会漏细节 |
| ❌ 用国内模型做大规模重构 (108KB 大文件) | 泛化架构能力不如国外 |
| ❌ 全用一个模型不分 | 浪费预算,且各模型有明确长短 |

---

## 6. 一句话总结

> **国外模型(Copilot Max)主攻:架构/编排/回测/UI/Agent/通用算法 — 占 70% 代码。**
> **国内模型(Qwen/GLM/DeepSeek)主攻:Wind 接口/政策语义/中文报告/A 股规则/社保康波 — 占 30% 代码 + 全部中文产出。**
> 两者通过 VS Code + Continue.dev 按文件路径自动切换,交叉审查。
> **别用国外模型写十五五政策,别用国内模型写 LangGraph 多 Agent —— 这是实测最优解。**

---

## 附录: 系统模块完整分工表

| 目录/文件 | 模型 | 备注 |
|-----------|------|------|
| `quant_modules/ai_hedge_fund/` | 国外 | LangGraph 20 分析师 |
| `scripts/run_200w_etf_backtest.py` | 国外 | Phase 2 四策略 |
| `ms_strategy/src/alpha/` | 国外 | GTJA191 因子 |
| `ui/**` | 国外 | Streamlit 14 页 |
| `quant_modules/data_layer.py` | 国外 | P0-P6 降级链 |
| `utils/hedge_rebalance_integrator.py` | 国外 | 五阶段联动 |
| `run_daily_eod.py` | 国外 | 四 Guard 链 |
| `utils/ml_enhanced_trainer.py` | 国外 | LightGBM |
| `.github/workflows/` | 国外 | CI/CD |
| `tools/wind_mcp_fetcher.py` | 国内 | Wind 接口 |
| `utils/glm5_*.py` | 国内 | GLM-5 已绑定 |
| `utils/kondratiev_cycle.py` | 国内 | 康波+十五五 |
| `utils/five_year_plan.py` | 国内 | 政策评分 |
| `utils/social_security_etf.py` | 国内 | 社保ETF |
| `utils/option_data_fetcher.py` | 国内 | σ 代理链 |
| `config/portfolio_200w_etf_v91.yaml` | 混合 | 结构国外/政策国内 |
| `institutional_pipeline_runner.py` | 混合 | 架构国外/业务国内 |
| `daily_trade_executor.py` | 混合 | 算法国外/合规国内 |
| `utils/hedge_engine.py` | 混合 | Beta 国外/合约国内 |

---

*本方案基于对 28-终极量化交易系统 v8.6 全量代码扫描,直接对应实际文件路径。*
*更新时间: 2026-09-11*
| **回测报告生成** | `scripts/run_200w_etf_backtest.py` 输出 | 国外做数据表格+指标计算,国内做中文诊脉书风格解读 |