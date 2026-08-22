# 第三方项目批量集成专题 — 2026-08-22

> 将 `10_第三方项目/` 中 6 个高价值项目分批融合式接入主系统 v8.6.14 的完整经验沉淀。
> 接入原则：保留主系统现有优势（20 大师分析师 + Wind/TDX/AKShare 数据链 + LangGraph 工作流），
> 引入第三方工程化能力，不替换。
> LOG 指针: `cairn/LOG.md` 2026-08-22 条目。

## 1. 选型与优先级

| 优先级 | 项目 | 接入方式 | 产出位置 |
|--------|------|---------|---------|
| P0-1 | TradingAgents-src | 代码移植（6阶段） | `quant_modules/ai_hedge_fund/` 多provider+checkpoint+3debator+结构化+日志 |
| P0-2 | Vibe-Trading | 代码移植 | `utils/correlation_matrix.py` + `risk_xray.py` + `correlation_regime.py` |
| P1-1 | timesfm | pip 封装 | `utils/timesfm_forecast.py` 零样本时序预测 |
| P1-2 | ai-berkshire | 代码+Skill移植 | `utils/value_investing/` 7工具+4大师prompt |
| P2-1 | FinceptTerminal | 文档索引 | `docs/data_source_catalog/` 8份数据源目录 |
| P2-2 | AERS | Skill索引 | `docs/empirical_research_skills/` 8个实证研究prompt |
| P2-3 | unsloth | pip 封装 | `utils/llm_finetune.py` GLM-5/豆包微调加速 |
| P2-4 | supply_chain_risk | 代码+模型移植 | `utils/supply_chain_risk/` 双领域风险评分 |
| P3 | awesome-trading | 参考文档 | `docs/awesome_systematic_trading_reference.md` |

## 2. 接入策略分类（可复用决策树）

```
第三方项目
├── 有 Python 源码？
│   ├── 零/浅依赖（仅 stdlib/pandas/numpy/sklearn）
│   │   └── 直接复制 → 改导入路径 → 创建 __init__.py 导出 API
│   ├── 深依赖（langchain/dataflows/torch）
│   │   └── 借鉴设计而非全量移植；只移植零依赖子模块
│   └── pip 包（已发布 PyPI）
│       └── 创建封装层（懒加载+优雅降级）→ pyproject optional-dependencies
├── C++/桌面应用（无 Python 源码）
│   └── 提取文档/数据源目录 → docs/ 参考索引
├── Skill prompt 库（.md prompt 模板）
│   └── 精选与量化相关的子集 → docs/ prompt 索引
└── awesome-list（纯资源目录）
    └── 复制 README → docs/ 参考文档
```

## 3. 各项目接入详情

### 3.1 TradingAgents-src（P0-1，6阶段）

**增量价值**: 多provider LLM（Bedrock/OpenAI-compatible）+ LangGraph checkpoint + 3debator 风控辩论 + 结构化输出 + 决策日志

**关键发现**:
- `llm_clients/` 12文件全包内相对导入，无外部依赖 → 可直接复制
- `agents/utils/agent_utils.py` 依赖 `dataflows`（主系统没有）→ 不可移植
- `schemas.py`/`rating.py`/`agent_states.py`/`structured.py`/`memory.py` 零/浅依赖 → 可移植
- 主系统 `debate_layer.py`（732行）已实现 bull/bear 对抗辩论 → 只需补 3debator 风控

**产出**:
- `ai_hedge_fund/llm_clients/` — 12文件多provider LLM抽象
- `ai_hedge_fund/graph/checkpointer.py` — SQLite checkpoint（适配版，内联 safe_ticker_component）
- `ai_hedge_fund/graph/reflection.py` — 决策反思
- `ai_hedge_fund/risk_debate_layer.py` — 3debator 风控辩论层
- `ai_hedge_fund/agents/schemas.py` + `utils/rating.py` + `utils/agent_states.py` + `utils/structured.py` + `utils/trading_memory.py`
- `orchestrator.py` 改造：checkpoint_enabled + 决策日志 + risk_debate 节点
- `llm/models.py` 改造：ModelProvider 加 BEDROCK/OPENAI_COMPATIBLE

**feature-flag**: `AI_HEDGE_RISK_DEBATE_DISABLED` 可关 3debator；`AI_HEDGE_MEMORY_LOG_PATH` 配置日志路径

### 3.2 Vibe-Trading（P0-2）

**增量价值**: 跨资产相关性矩阵 + 风险透视（集中度/波动率/回撤/尾部/VaR）+ regime 状态机

**关键发现**:
- `correlation.py` 无内部依赖 → 可直接移植
- `risk_xray.py` 只依赖 `_json_safe` → 内联后可移植
- `regime.py` 依赖 correlation 内部函数 → 改导入路径后可移植

**产出**: `utils/correlation_matrix.py`（11KB）+ `utils/risk_xray.py`（14.4KB）+ `utils/correlation_regime.py`（8.9KB）

### 3.3 timesfm（P1-1）

**增量价值**: TimesFM 2.5 零样本时序预测（Google 研究，pip 包）

**接入**: 创建 `utils/timesfm_forecast.py` 封装层（懒加载单例 + is_available() 检测），pyproject 加 `timesfm = ["timesfm[torch]>=2.0.0"]` optional 依赖

**验证**: is_available()=True，4 函数就绪

### 3.4 ai-berkshire（P1-2）

**增量价值**: 巴菲特/芒格/段永平/李录价值投资决策工具集

**关键发现**: `financial_rigor.py` 零外部依赖（仅 stdlib decimal/json/math/argparse，451行精确 Decimal 引擎）

**产出**: `utils/value_investing/` — 7工具（financial_rigor/report_audit/stock_screener/momentum_backtest×2/morningstar_fair_value/ashare_data）+ 4 Skill prompt + `__init__.py`（PEP 562 懒加载）

### 3.5 FinceptTerminal（P2-1）

**增量价值**: 100+ 数据源目录文档（C++桌面应用本体不可移植）

**接入**: 复制 8 份数据源 markdown 到 `docs/data_source_catalog/` + 创建 README 索引

### 3.6 AERS（P2-2）

**增量价值**: Stanford 实证研究 1150+ skill，精选与量化策略验证最相关的 8 个

**选取标准**: 因果推断（DiD/IV/RDD/synthetic control）+ 面板回归 + ML因果 + 完整实证工作流

**产出**: `docs/empirical_research_skills/` — 8 个 SKILL.md prompt + README 索引

### 3.7 unsloth（P2-3）

**增量价值**: LLM 训练加速 2x-5x + 50% VRAM 节省，支持 GLM4 MoE/Qwen2/Qwen3

**接入**: 创建 `utils/llm_finetune.py` 封装层（FastModelWrapper + finetune_glm5/finetune_doubao + prepare_trading_sft_dataset），pyproject 加 `llm-finetune = ["unsloth>=2025.8.0"]`

**验证**: is_unsloth_available()=True，prepare_trading_sft_dataset 正确生成 system/user/assistant 三消息

### 3.8 supply_chain_risk（P2-4）

**增量价值**: 金融风控+能源成本双领域供应商风险评估（预训练模型，即装即用）

**接入**: 复制 predict.py + train.py + models/（3.2MB pkl）到 `utils/supply_chain_risk/` + `__init__.py` 导出 evaluate_supplier/evaluate_batch/load_engine

**验证**: evaluate_supplier(banking, 高质量) → score=84.0, decision=✅通过

### 3.9 awesome-systematic-trading（P3）

**接入**: 复制 README_zh.md（75KB）到 `docs/awesome_systematic_trading_reference.md`

## 4. 可复用工程经验

### 4.1 融合式接入原则

> 保留主系统现有优势 + 引入第三方工程化能力，不替换。

具体体现：
- TradingAgents 接入不替换主系统 20 大师分析师，只增加 provider/checkpoint/3debator
- Vibe-Trading 接入不替换主系统 PyPortfolioOpt，只增加相关性/regime 能力
- unsloth 接入不替换 GLM5Client，只提供微调加速通道

### 4.2 依赖深度分级

| 依赖深度 | 策略 | 示例 |
|----------|------|------|
| 零依赖（仅 stdlib） | 直接复制 | ai-berkshire financial_rigor.py |
| 浅依赖（pandas/numpy/sklearn） | 直接复制 + __init__.py | supply_chain_risk |
| 中依赖（需改导入路径） | 复制 + 改路径 | Vibe-Trading regime.py |
| 深依赖（dataflows/langchain） | 借鉴设计，只移植零依赖子模块 | TradingAgents schemas.py |
| pip 包 | 封装层 + optional-deps | timesfm/unsloth |

### 4.3 feature-flag 模式

所有新功能默认开启但可环境变量关闭，向后兼容：
- `AI_HEDGE_RISK_DEBATE_DISABLED` — 关 3debator 风控
- `AI_HEDGE_MEMORY_LOG_PATH` — 配置决策日志路径
- `LLM_FINETUNE_DISABLED` — 关 unsloth
- `checkpoint_enabled` 参数 — LangGraph checkpoint 开关

### 4.4 PEP 562 懒加载

`utils/value_investing/__init__.py` 使用 PEP 562 `__getattr__` 实现子模块懒加载，
首次访问时才 import，避免导入时副作用。

### 4.5 pyproject optional-dependencies 分组

```toml
[project.optional-dependencies]
ai-hedge = [...]           # TradingAgents LLM providers
ai-hedge-bedrock = [...]   # Bedrock 额外依赖
timesfm = [...]            # TimesFM 时序预测
llm-finetune = [...]       # unsloth 训练加速
```

安装：`pip install -e .[ai-hedge,timesfm,llm-finetune]`

## 5. 踩坑记录

### 5.1 TradingAgents dataflows 依赖

**问题**: `agents/utils/agent_utils.py` + `*_tools.py` 依赖 `dataflows` 包（主系统没有）
**解决**: 不移植这些文件，只移植零/浅依赖的 schemas/rating/agent_states/structured/memory
**教训**: 移植前先 grep 依赖链，按依赖深度分级

### 5.2 unsloth triton 编译警告

**问题**: Windows 上 triton 编译 CUDA kernel 失败（Python.h not found）
**影响**: 不影响封装层逻辑，is_unsloth_available()=True，仅 GPU kernel 编译问题
**解决**: 封装层验证通过，实际训练需 Linux/CUDA 环境

### 5.3 PowerShell && 不支持

**问题**: Windows PowerShell 5.1 不支持 `&&` 语句分隔符
**解决**: 改用 `;` + `if ($?) { ... }` 条件执行

## 6. 与主系统现有模块的协同

| 主系统模块 | 新接入能力 | 协同方式 |
|-----------|-----------|---------|
| `ai_hedge_fund/orchestrator.py` | checkpoint + 3debator + 决策日志 | 直接集成到 workflow |
| `utils/signal_fusion.py` | correlation/regime 信号 | 可作为信号源注册 |
| `utils/alpha_factor/` | ml_causal + panel_data prompt | 因子因果效应验证 |
| `utils/hedge_rebalance_backtest.py` | did_analysis prompt | 对冲政策事件研究 |
| `utils/glm5_client.py` | unsloth 微调加速 | 微调后模型替换推理路径 |
| `utils/kondratiev_cycle.py` | empirical_analysis_full prompt | 周期假设完整检验 |
| 持仓标的（中际旭创/海光信息等） | supply_chain_risk 评分 | 供应链风险作为额外风险因子 |

## 7. 后续建议

1. **TradingAgents dataflows**: 如需完整 TradingAgents 能力，可考虑安装 dataflows 包
2. **Vibe-Trading optimizers**: 主系统已有 PyPortfolioOpt + black_litterman，Vibe optimizers 暂不移植
3. **AERS 全库**: 如需更多实证研究 skill（贝叶斯/复现/文献综述），可从 AERS 原仓库按需补充
4. **unsloth 实际训练**: 需 Linux + CUDA GPU 环境，Windows 仅可做封装层验证
5. **supply_chain_risk 集成**: 可在 `daily_trade_executor.py` 中调用 evaluate_supplier 为持仓标的生成供应链风险评分