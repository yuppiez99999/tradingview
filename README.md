# 🚀 终极量化交易系统 v8.7

[![Python](https://img.shields.io/badge/Python-3.9%2B-blue)](https://www.python.org)
[![实盘状态](https://img.shields.io/badge/Live%20Trading-Deployed-brightgreen)]()
[![测试](https://img.shields.io/badge/Tests-2979%20PASS-success)]()
[![覆盖率](https://img.shields.io/badge/Coverage-83%25-blueviolet)]()
[![代码质量](https://img.shields.io/badge/Code%20Quality-A--%200%20violations-success)]()
[![License](https://img.shields.io/badge/License-%C2%A9%20Reserved-red)]()

> 💡 **500万实盘已部署** · 全自动交易闭环 · 年化≥8% 且最大回撤<15% · 双LLM决策 · 多源数据融合 · 风控守卫强制执行

> A股量化交易系统 — 多因子选股 · LightGBM增强训练 · 策略回测 · 风控管理 · 全栈数据采集 · Alpha研究
> 500万实盘部署 | 全自动交易闭环 | 年化≥8% 且最大回撤<15% | 双LLM决策 | 多源数据融合 | 风控守卫强制执行 | P0自检系统 | 数据契约测试 | 气象因子引擎 | GTJA191因子对标 | GNN供应链产业链因子 | 自我进化框架 | VolRegimeWeighter | MVSK高阶矩优化 | ETF期权对冲再平衡 | TradingAgents多provider+3debator风控 | Vibe-Trading相关性+regime | TimesFM零样本预测 | unsloth LLM训练加速 | 价值投资决策工具集 | 供应链风险评分

**作者**：yuppiez99999
**版权状态**：© 2026 yuppiez99999 · 保留所有权利 · 禁止商用 · 转载须署名
**实盘状态**：✅ 已部署（2026-07-28）
**生产基线**：Python 3.14.4（junction `C:\QuantSys`），兼容 Python 3.9+
**当前阶段**：v8.7 Sprint 1 冲刺中（D9/D10 达标，D11 进行中 6/7+6/20，目标 2026-12-31 发布）
**最近更新**：2026-08-31 — Wave 12-A 全部完成（stumpy 康波SAX motif / Open-Meteo气象 / RSS舆情 / trafilatura正文 / empyrical+pyfolio绩效）+ 代码质量 A-（ruff/mypy/bandit 全0 + pytest 2979 PASS）+ 架构图 v8.7 + Wind MCP 数据自检

---

## 📑 目录

- [核心特性](#核心特性)
  - [机构级量化架构](#机构级量化架构)
  - [AI增强决策](#ai增强决策)
  - [完全自动化](#完全自动化)
  - [第三方项目融合集成](#第三方项目融合集成v87新增)
- [v8.7 最新进展](#v87-最新进展)
- [快速开始](#快速开始)
- [主入口与 CLI 命令](#主入口与-cli-命令)
- [因子体系](#因子体系12大类)
- [第三方集成模块](#第三方集成模块v87新增)
- [数据源优先级](#数据源优先级)
- [风控体系](#风控体系)
- [回测协议](#回测协议)
- [系统自我升级](#系统自我升级)
- [项目结构](#项目结构)
- [📊 交互式架构图](项目架构图_v8.7.html)
- [开发工作流](#开发工作流)
- [双机部署架构](#双机部署架构mac研究--windows云实盘)

---

## 核心特性

### 机构级量化架构
- **双账户结构**：500万总资金（现货400万 + 对冲100万），已实盘部署
- **风险预算驱动**：Risk Parity + Kelly公式动态分配建仓预算
- **三联对冲引擎**：Beta / Vol / Correlation 三类对冲实时联动 + 尾部风险保护
- **完整风控体系**：个股止损 + 组合回撤四级防御 + Walk-Forward回测验证
- **硬性风险约束**：单标的10%上限、单板块25%上限、组合日度VaR95 1.5%

### AI增强决策
- **双LLM架构**：快速模式 Qwen2.5 7B（~22秒）+ 深度思考 DeepSeek-R1 14B（~1-3分钟）
- **深度思考触发**：5种场景自动切换深度模型（组合止损/多股止损/ETF加仓/对冲偏离/大幅盈亏）
- **三级降级链**：Ollama → 智谱GLM → DeepSeek
- **双模型自我判断**（v8.7）：DeepSeek + GLM-5.2 独立判断 → 交叉验证 → 共识决策
- **TradingAgents多provider LLM**（v8.7新增）：Bedrock / OpenAI-compatible / 14 provider统一抽象 + LangGraph SQLite checkpoint + 3debator风控辩论（aggressive/conservative/neutral）+ 结构化5级评级输出 + 决策日志持久化
- **unsloth LLM训练加速**（v8.7新增）：GLM-5/豆包/Qwen 微调 2x-5x加速 + 50%VRAM节省，支持GLM4 MoE
- **新闻情感分析**：实时抓取东方财富/巨潮资讯/新浪财经公告与研报
- **价格预测**：TimesFM零样本 + TensorFlow LSTM + ARIMA 三级降级

### 完全自动化
- **无人化交易闭环**：盘前自动生成 → 自动确认 → 盘后自动执行 → 自动生成下一日计划
- **执行层自动闭环**（v8.7）：盈亏→风控→改计划→对冲增减→再平衡 全自动闭环，RiskGuardIntegrator 8-Guard链自动识别国债集中度52.7%>15%触发L3减仓7100股+再平衡19单
- **Windows任务调度**：07:05盘前 / 09:30早盘 / 14:00午盘 / 21:00夜盘 / 15:30盘后 + 盘中每15分钟LLM决策
- **十五五规划对齐**：2026-2030五年阶段管理，2030-12-31强制清仓

### 第三方项目融合集成（v8.7新增）
融合式接入9子项，保留主系统现有优势，引入第三方工程化能力：
- **Vibe-Trading 风险透视**：跨资产相关性矩阵 + 集中度/波动率/回撤/尾部VaR风险透视 + regime状态机
- **TimesFM 2.5 零样本预测**：Google时序基础模型，价格/收益率零样本预测+分位区间
- **ai-berkshire 价值投资工具集**：巴菲特/芒格/段永平/李录4大师方法论 + 7工具（精确Decimal金融验证/报告审计/股票筛选/动量回测/晨星公允价值/A股数据）
- **supply_chain_risk 供应链风险评分**：金融风控+能源成本双领域预训练模型（3.2MB），即装即用
- **AERS 实证研究Skill**：Stanford 1150+ skill库精选8个（因果推断/面板回归/ML因果/DiD/IV/RDD）
- **FinceptTerminal 数据源目录**：100+数据源参考文档（中国/经济/政府/市场/区域/卫星/专项/美股）

---

## v8.7 最新进展

### 第三方项目批量集成（2026-08-22）✅
从 `10_第三方项目/` 筛选6个高价值项目，融合式接入主系统v8.6.14，全部完成。

| 项目 | 接入方式 | 产出 |
|------|---------|------|
| **TradingAgents** (P0-1, 6阶段) | 代码移植 | `ai_hedge_fund/` 多provider LLM + LangGraph checkpoint + 3debator风控 + 结构化输出 + 决策日志 |
| **Vibe-Trading** (P0-2) | 代码移植 | `utils/correlation_matrix.py` + `risk_xray.py` + `correlation_regime.py` |
| **timesfm** (P1-1) | pip封装 | `utils/timesfm_forecast.py` 零样本时序预测 |
| **ai-berkshire** (P1-2) | 代码+Skill移植 | `utils/value_investing/` 7工具+4大师prompt |
| **FinceptTerminal** (P2-1) | 文档索引 | `docs/data_source_catalog/` 8份数据源目录 |
| **AERS** (P2-2) | Skill索引 | `docs/empirical_research_skills/` 8个实证研究prompt |
| **unsloth** (P2-3) | pip封装 | `utils/llm_finetune.py` GLM-5/豆包微调加速 |
| **supply_chain_risk** (P2-4) | 代码+模型移植 | `utils/supply_chain_risk/` 双领域风险评分 |
| **awesome-trading** (P3) | 参考文档 | `docs/awesome_systematic_trading_reference.md` |

- **pyproject**: 新增 ai-hedge / ai-hedge-bedrock / timesfm / llm-finetune 4个optional-dependencies组
- **feature-flag**: `AI_HEDGE_RISK_DEBATE_DISABLED` / `LLM_FINETUNE_DISABLED` / `checkpoint_enabled` 向后兼容
- 详见 `cairn/third-party-integration-batch-20260822.md`

### ETF期权对冲再平衡子模型 Phase 1 ✅
独立200万纯ETF子组合（14 ETF/100%纯ETF）+ ETF期权对冲（4标的认沽保护）+ 自我再平衡（五阶段）。

- `config/etf_option_subportfolio.yaml` — 子组合配置（宽基60% + 行业25% + 防御15%）
- `etf_option_hedge_rebalancer.py` — 编排器（复用6个现有模块）
- `tests/unit/test_etf_option_hedge_rebalancer_unit.py` — 31/31 passed
- **压力测试**：裸敞口6/6突破 → 对冲后3/6突破（2020疫情11.9% / 2022俄乌12.7% / 2024地产13.5% 已保护到15%以内）
- 详见 `cairn/etf-option-hedge-model.md`

### v8.7 三门禁冲刺（D9/D10/D11）
| 门禁 | 状态 | 当前值 | 目标 |
|------|------|--------|------|
| D9 覆盖率 Sprint4 | ✅ 达标 | line_rate=0.833, branch_rate=0.7605 | ≥0.80 |
| D10 超大文件拆分 | ✅ 达标 | institutional_pipeline_runner 1744行 + automated_execution_system 1860行 | ≤2000行 |
| D11 PhaseB shadow 7天稳定 | ⏳ 进行中 | 6/7天 + 6/20样本（真实达标日 09-19） | 7天稳定 + 20样本 |
| v8.7 汇总判定 | ⏳ 待D11 | D9✅ D10✅ D11待积累 | 全PASS放行 |

### Wave 12-A 全部完成（2026-08-30，提前8天）✅
主表 123 个 GitHub 高价值项目筛选 15 个集成项，12-A 工具降本轨道 5/5 完成，110 测试全 PASS：

| 项目 | 实现 | 验收 |
|------|------|------|
| **stumpy 康波模式** | `utils/kondratiev_cycle.py` SAX motif 发现（3 方法 + numpy 降级） | 模式匹配 3 个，25 测试 |
| **Open-Meteo 气象** | `utils/macro_weather.py` 免费无需 key | 温度/降水/ENSO 可查，28 测试 |
| **feedparser RSS** | `utils/rss_feed_fetcher.py` 7 个财经源 | ≥5 源，21 测试 |
| **trafilatura 正文** | `utils/web_content_extractor.py` 正文提取 | 准确率 >90%，16 测试 |
| **empyrical+pyfolio 绩效** | `reporting/performance_report.py` 标准绩效指标 + HTML | 20 测试 |

> 12-B（发布后功能集成：Kronos/Quarto/DuckDB/OpenBB/vectorbt/PyOD/RD-Agent/vnpy，~29 人天）排期 2027-01-04~03-21，详见 `docs/github_integration_plan_wave12_20260830.md`

### 代码质量 A-（2026-08-31）✅
- **ruff 35→0** / **bandit 1High+5Medium→0** / **mypy 42→0**（含 wt_backtest_engine 9 错误修复）
- **pytest 2979 passed**（9 个预存失败已修复：t57 日期断言 CST + tf_price_predictor DLL 降级）
- 报告：`docs/code_quality_fix_report_20260831.md`

### 架构图 v8.7（2026-08-31）
- `项目架构图_v8.7.html` + `项目架构图_v8.7.architecture.json`（archify v2.16.0 渲染，交互式 SVG）
- 新增 AI Hedge Fund / 宏观分析 / 报告生成组件 + Wave 12-A 视图

### Phase B 观察期达标 + B1 自动启用 ✅
- **观察期达标**：`daily_returns.jsonl` 21条（07-23~08-20），真实样本21/20 ✅
- **调度器自动推进**：`phase_b_progressive_enabler.py --check` 触发状态机推进 — stage: waiting_observation → **drift_monitor**

### 代码质量工业级修复（ruff 1139→211，81%降幅）
- **Phase A1-A4 + Wave 1-2**：torch collection修复 + 类型注解批量补齐 + ruff风格清理
- **08-21修复**：F401×6 + F541×1 + F841×1 + BLE001×5 + T201×2 + 硬编码路径×2
- **对标**：Two Sigma/Citadel工业级12维度，详见 `cairn/code-quality-industrial-gap-20260819.md`

### MVSK高阶矩优化 P1-P4 ✅ 生产就绪
- **P3**：378日训练窗口扫描发现临界点，**始终BL+MVSK(378)最优夏普+0.418**
- **P4**：995日跨周期回测，BL+MVSK(378) **4/4段跑赢BL+MV**（Δ夏普+0.22）
- **最终策略**：BL+MVSK(378, γ_s=0.1, γ_k=0.1)，详见 `cairn/mvsk-higher-moment-optimization.md`

### 自我进化框架升级
- **Lyapunov稳定性** + **反馈相位分析** + **变异选择平衡** + **Triple-Barrier**
- 详见 `cairn/self-evolution-framework.md`

### 经典理论覆盖度审计
- **已覆盖30+经典理论**：BS/Monte Carlo/Greeks/GARCH/VaR/CVaR/EVT/MPT/BL/Kelly/Bayesian/Fama-French/Triple-Barrier/Purged K-Fold/DSR/VWAP/TWAP/Almgren-Chriss/控制论/反身性/反脆弱/康波/Lyapunov/变异选择
- 详见 `cairn/classic-theory-coverage-20260819.md`

---

## 快速开始

### 1. 环境准备

```bash
git clone <repo-url>
cd 28-终极量化交易系统8.4
pip install -r requirements.txt          # Python 3.9+
pip install -r requirements_dev.txt       # 开发工具（可选）

# 可选：第三方项目增强能力（按需安装）
pip install -e .[timesfm]                 # TimesFM 零样本时序预测
pip install -e .[llm-finetune]            # unsloth LLM训练加速（需CUDA GPU）
pip install -e .[ai-hedge]                # TradingAgents 多provider LLM
pip install -e .[ai-hedge-bedrock]        # Bedrock 额外依赖
```

### 2. 环境变量配置

复制 `.env.example` 为 `.env`，填入密钥：

```ini
WIND_API_KEY=...          # Wind MCP（P1数据源）
TS_TOKEN=...              # Tushare（国内期货/CPI）
VOLCENGINE_API_KEY=...    # 豆包 LLM
DEEPSEEK_API_KEY=...      # DeepSeek（信号计算）
GLM_API_KEY=...           # 智谱 GLM-5.2（合规审计+双模型判断）
MOONSHOT_API_KEY=...      # Kimi3（研报多模态）
CLAUDE_API_KEY=...        # Claude（深度推理/风控）
OPENAI_API_KEY=...        # GPT（盘中研判）
OLLAMA_MODELS=...         # Ollama 模型路径
LOG_LEVEL=INFO
```

### 3. 系统自检

```bash
python scripts/run_p0_startup_check.py --strict    # P0严格自检（盘前最终核查）
```

### 4. 首次运行

```bash
python institutional_pipeline_runner.py --mode smoke                                  # 烟雾测试
python institutional_pipeline_runner.py --mode backtest --symbols 600519 000858       # 回测
python institutional_pipeline_runner.py --mode live                                   # 生产
```

---

## 主入口与 CLI 命令

| 入口 | 说明 |
|------|------|
| `institutional_pipeline_runner.py` | 机构级闭环运行器（数据门控→Alpha评估→信号融合→组合优化→风险预算→执行路由） |
| `15_每日工作流/run_daily_eod_workflow.py` | 盘后工作流（收盘报告→风控守卫→次日计划→预生成盘中决策） |
| `live_scheduler.py` | 实时调度器（交易日09:25-15:05每15分钟触发LLM盘中决策） |
| `etf_option_hedge_rebalancer.py` | ★v8.7 ETF期权对冲再平衡编排器（五阶段日度再平衡） |
| `daily_trade_executor.py` | 交易计划执行 |
| `stop_loss_monitor.py` | 止损监控 |
| `signal_monitor.py` | 信号监控 |
| `build_plan_executor.py` | 建仓计划执行 |
| `generate_daily_report.py` | 日报生成 |

```bash
# 盘后工作流
python 15_每日工作流/run_daily_eod_workflow.py                    # 完整
python 15_每日工作流/run_daily_eod_workflow.py --phase report     # 仅报告
python 15_每日工作流/run_daily_eod_workflow.py --phase plan       # 仅次日计划

# 实时调度
python live_scheduler.py                   # 启动调度器
python live_scheduler.py --once            # 单次执行

# Phase B 渐进启用（v8.7）
python scripts/phase_b_progressive_enabler.py --check      # 检查观察期状态
python scripts/phase_b_progressive_enabler.py --advance    # 手动推进阶段
```

---

## 因子体系（12大类）

### 12大类因子分类（11大类对标国泰君安GTJA191 + 第12大类LeadLag图因子）

```
utils/alpha_factor/
├── base.py                  # 基础数据结构 + 预处理（去极值/标准化/中性化/正交化）
├── library.py               # 因子库聚合入口（12大类统一调度 + 跨类正交化后处理）
├── value.py                 # 价值类（EP/DP/BP/SP/CFP）
├── growth.py                # 成长类（营收/利润/资产增长率）
├── quality.py               # 质量类（ROE/ROA/毛利率）
├── leverage.py              # 杠杆类（资产负债率/权益乘数）
├── operation.py             # 运营类（资产周转率/存货周转率）
├── price_volume.py          # 量价类（动量/低波/规模/流动性）
├── technical.py             # 技术类（GTJA191量价因子集成）
├── expectation.py           # 预期类（分析师预期/微结构）
├── graph.py                 # 第12大类 LeadLag（GNN供应链产业链5因子）
├── gat_factor_torch.py      # GAT注意力层（torch自动微分）
└── gate1_validation.py      # Gate 1门禁验证（跨时间窗IC/ICIR）
```

### 三级共线解决方案（|ρ|>0.99 完全共线对 12→0）

| 级别 | 方法 | 适用场景 |
|------|------|----------|
| L1 | 基础窗口正交化 | 多窗口因子共线 |
| L2 | 双重残差化 | 跨公式等价共线 |
| L3 | 非单调变换 | 线性关系共线 |

---

## 第三方集成模块（v8.7新增）

### AI Hedge Fund 增强（TradingAgents）
```
quant_modules/ai_hedge_fund/
├── llm_clients/                    # 🆕 12文件多provider LLM抽象
├── graph/
│   ├── checkpointer.py             # 🆕 SQLite checkpoint（适配版）
│   └── reflection.py               # 🆕 决策反思
├── risk_debate_layer.py            # 🆕 3debator 风控辩论层
├── agents/schemas.py               # 🆕 5级评级+结构化输出
├── utils/
│   ├── rating.py                   # 🆕 parse_rating
│   ├── agent_states.py             # 🆕 InvestDebateState/RiskDebateState
│   ├── structured.py               # 🆕 结构化输出helper
│   └── trading_memory.py           # 🆕 决策日志
└── orchestrator.py                 # ✏️ 加checkpoint+决策日志+risk_debate
```

### 风险透视与预测（Vibe-Trading + TimesFM）
```
utils/
├── correlation_matrix.py           # 🆕 跨资产相关性+市场推断
├── risk_xray.py                    # 🆕 风险透视（集中度/波动率/回撤/尾部VaR）
├── correlation_regime.py           # 🆕 regime状态机
├── timesfm_forecast.py             # 🆕 TimesFM 2.5零样本时序预测
└── llm_finetune.py                 # 🆕 unsloth LLM训练加速封装
```

### 价值投资与供应链（ai-berkshire + supply_chain_risk）
```
utils/
├── value_investing/                # 🆕 价值投资决策工具集
│   ├── financial_rigor.py          #   精确Decimal金融验证（451行，零依赖）
│   ├── report_audit.py             #   报告审计
│   ├── stock_screener.py           #   股票筛选器
│   ├── momentum_backtest.py/_v2.py #   动量回测
│   ├── morningstar_fair_value.py   #   晨星公允价值
│   ├── ashare_data.py              #   A股数据
│   └── skills/                     #   4大师方法论prompt
└── supply_chain_risk/              # 🆕 供应链风险评分（预训练模型）
    ├── predict.py                  #   推理服务
    ├── train.py                    #   训练脚本
    └── models/                     #   3.2MB预训练模型
```

### 实证研究与数据源参考
```
docs/
├── empirical_research_skills/      # 🆕 AERS 8个实证研究Skill
│   ├── empirical_analysis_full.md  #   完整实证分析工作流
│   ├── causal_inference_mixtape.md #   因果推断10法
│   ├── ml_causal.md                #   ML+因果推断
│   ├── panel_data.md               #   面板数据分析
│   ├── did_analysis.md             #   双重差分
│   ├── iv_estimation.md            #   工具变量
│   ├── rdd_analysis.md             #   断点回归
│   └── pyfixest_econometrics.md    #   Python高性能固定效应
├── data_source_catalog/            # 🆕 FinceptTerminal 100+数据源目录
│   └── *.md                        #   中国/经济/政府/市场/区域/卫星/专项/美股
└── awesome_systematic_trading_reference.md  # 🆕 系统化交易参考资源
```

---

## 数据源优先级

全局统一标准，所有模块必须遵循以下降级链，不可跳级：

| 优先级 | 数据源 | 说明 | 认证 |
|--------|--------|------|------|
| P0 | Wind数据终端 | 主数据源，WindPy原生客户端 | WindPy授权 |
| P1 | Wind MCP | 强制回退，HTTP直连+CLI双路径（`tools/wind_mcp_fetcher.py` v8.6.14） | `WIND_API_KEY` |
| P2 | 通达信(pytdx) | 免费直连，TCP 7709端口，仅A股 | 无需 |
| P3 | AKShare/baostock | 免费回退，A股/期货/指数 | 无需 |
| P4 | 新浪财经API | 免费实时行情兜底 | 无需 |
| P5 | 本地缓存 | Parquet/JSON缓存 | 无需 |
| P6 | 预定义价格 | 保证系统永不崩溃 | 无需 |

**强制规则**：Wind不可用时必须尝试Wind MCP，不可直接跳到通达信。

---

## 风控体系

### 四模块联动风控链

每日EOD后强制执行：回撤检查 → 波动率控制 → 对冲执行 → 认沽保护。

| 模块 | 文件 | 职责 |
|------|------|------|
| 回撤熔断器 | `utils/drawdown_breaker.py` | 四级回撤防御（L1预警→L4全面停止） |
| 波动率目标缩仓 | `utils/vol_target_controller.py` | AQR/Man Group风格Vol Targeting |
| 对冲执行引擎 | `utils/hedge_execution_engine.py` | 对冲信号→IF期货+ETF期权订单 |
| 认沽期权保护 | `utils/protective_put_engine.py` | ¥77.8万Put预算，OTM 5%虚值覆盖 |
| Kill Switch | `utils/kill_switch.py` | L1/L2/L3三级熔断 |
| 风险守卫集成 | `utils/risk_guard_integrator.py` | 8-Guard联动 + 执行日志 |
| 3debator风控辩论 | `quant_modules/ai_hedge_fund/risk_debate_layer.py` | 🆕 aggressive/conservative/neutral三方辩论 |

### 硬性风险约束（`utils/risk_constraints.py`）

- 单标的硬上限：10% / 单一板块硬上限：25% / 组合日度VaR95硬上限：1.5%
- 单票日度VaR95硬上限：0.8% / 对冲暴露上限：0.40 / 换手率预算：0.20

---

## 回测协议

### 目标函数
```
J = Sortino + 0.5 × Calmar - λ‖w‖²
```

### Walk-Forward配置
- 训练窗口：24月 / 测试窗口：3月 / 步长：3月

### 必过压力测试

| 事件 | 区间 | 跌幅 |
|------|------|------|
| 全球金融危机 | 2008-09-15 ~ 2009-03-09 | S&P -56% |
| A股股灾 | 2015-06-12 ~ 2015-08-26 | 上证 -43% |
| 熔断机制 | 2016-01-04 ~ 2016-01-28 | 4天2次熔断 |
| 中美贸易战 | 2018-03-22 ~ 2018-10-29 | 上证 -25% |
| COVID闪崩 | 2020-02-19 ~ 2020-03-23 | — |
| Luna崩盘 | 2022-05-01 ~ 2022-05-12 | — |
| 全球债券大屠杀 | 2022-01-01 ~ 2022-10-24 | 股债双杀 |
| 日元Carry Trade | 2024-08-01 ~ 2024-08-05 | — |

### CRO Gate（上线前必过）
- [ ] Walk-Forward 5窗口拼接 Sortino ≥ 1.0
- [ ] 八段压力测试 Max DD < 15%
- [ ] Deflated Sharpe Ratio ≥ 0.95
- [ ] 无未来函数（PIT检查通过）
- [ ] NTP漂移 < 50ms 持续7个交易日
- [ ] 滑点熔断在历史回放中正确触发
- [ ] CRO签字

---

## 系统自我升级

### 1. 自我进化框架（EvolutionOrchestrator）
核心实现：`utils/alpha/evolution_orchestrator.py`
- **观察期**：14天 + 最小评估样本20天，未达样本前不触发自动变更
- **双链路架构**：盘中`AutoTradingSystem`每30s只读`VolRegimeWeighter`建议；EOD`EvolutionOrchestrator`产出完整进化报告
- **产物落盘**：决策日志与进度快照自动持久化至`reports/evolution/`

### 2. Phase B 渐进启用（v8.7进行中）
- [x] B1 (08-20) `USE_DRIFT_DETECTOR=true` 仅告警 ✅
- [ ] B2 (08-23) `USE_FEEDBACK_LOOP` 自动接入
- [ ] B3 (08-26) `USE_AUTO_RETRAIN=true` + 降级护栏
- [ ] B4 (08-29) `USE_MLOPS_PIPELINE=true` 完整外层循环

---

## 项目结构

```
28-终极量化交易系统8.4/
├── institutional_pipeline_runner.py         # 主入口 — 机构级闭环运行器
├── 量化策略系统_统一入口_v8.6.py            # ★统一 CLI 入口（35+ 模式）
├── etf_option_hedge_rebalancer.py           # ★v8.7 ETF期权对冲再平衡编排器
├── 15_每日工作流/run_daily_eod_workflow.py   # 盘后工作流入口
├── live_scheduler.py                        # 实时调度器
├── daily_trade_executor.py                  # 交易计划执行
├── lgb_enhanced_trainer.py                  # LightGBM增强训练器
│
├── utils/                                   # 核心工具模块（150+模块 + 36子目录）
│   ├── alpha_factor/                        # 因子库包（11大类 + GTJA191）
│   ├── alpha/                               # Alpha信号与LLM路由
│   │   ├── evolution_orchestrator.py        # 自我进化框架
│   │   ├── vol_regime_weighter.py           # 波动率Regime权重建议器
│   │   └── theoretical_metrics.py           # Lyapunov/相位/变异平衡
│   ├── kondratiev_cycle.py                  # 🆕 康波周期 v2.0（SAX motif，Wave12-A）
│   ├── macro_weather.py                     # 🆕 Open-Meteo 气象（Wave12-A）
│   ├── rss_feed_fetcher.py                  # 🆕 RSS 财经源（Wave12-A）
│   ├── web_content_extractor.py             # 🆕 trafilatura 正文提取（Wave12-A）
│   ├── hedge_engine.py                      # 多指数Beta加权对冲引擎
│   ├── hedge_rebalance_integrator.py        # 五阶段联动决策引擎
│   ├── observability/                       # 可观测性（structlog+pydantic+OTel）
│   ├── correlation_matrix.py                # 🆕 跨资产相关性+市场推断
│   ├── risk_xray.py                         # 🆕 风险透视
│   ├── correlation_regime.py                # 🆕 regime状态机
│   ├── timesfm_forecast.py                  # 🆕 TimesFM零样本时序预测
│   ├── llm_finetune.py                      # 🆕 unsloth LLM训练加速
│   ├── value_investing/                     # 🆕 价值投资决策工具集（7工具+4prompt）
│   ├── supply_chain_risk/                   # 🆕 供应链风险评分（预训练模型）
│   ├── risk_constraints.py                  # 硬性风险约束
│   ├── risk_budget_engine.py                # 风险预算引擎（MVSK高阶矩优化）
│   ├── signal_fusion.py                     # 多源信号融合（9层，含气象因子）
│   ├── kill_switch.py                       # Kill Switch三级熔断
│   └── ...
│
├── quant_modules/ai_hedge_fund/             # AI Hedge Fund（20分析师+LangGraph）
│   ├── llm_clients/                         # 🆕 多provider LLM抽象（12文件）
│   ├── graph/                               # 🆕 LangGraph checkpoint+反思
│   ├── risk_debate_layer.py                 # 🆕 3debator风控辩论
│   ├── agents/                              # 🆕 结构化输出+评级
│   └── orchestrator.py                      # 编排器（checkpoint+决策日志+risk_debate）
│
├── v8.3_institutional/                      # 机构级基础设施与日度工作流
├── ui/                                      # Streamlit可视化面板（17页+投研平台）
├── ms_strategy/                             # 多策略框架（alpha/backtest/execution/hedging/risk）
├── reporting/performance_report.py          # 🆕 empyrical+pyfolio 绩效报告（Wave12-A）
├── tools/wind_mcp_fetcher.py                # Wind MCP P1（HTTP直连+CLI）
├── tests/                                   # 测试套件（2979 unit passed）
├── scripts/                                 # 工具脚本
├── config/                                  # 全局配置
├── cairn/                                   # Project Cairn 知识管理
├── docs/                                    # 文档
│   ├── empirical_research_skills/           # 🆕 AERS实证研究Skill（8个）
│   ├── data_source_catalog/                 # 🆕 FinceptTerminal数据源目录（8份）
│   ├── github_integration_plan_wave12_20260830.md  # 🆕 Wave 12 排期
│   └── awesome_systematic_trading_reference.md  # 🆕 系统化交易参考
├── 项目架构图_v8.7.html                     # 🆕 架构图（archify 渲染）
├── requirements.txt                         # 生产依赖
├── pyproject.toml                           # 项目配置+optional-dependencies
├── ruff.toml                                # Ruff配置
└── CHANGELOG.md                             # 更新日志
```

---

## 开发工作流

### 代码质量门禁

```bash
ruff check .                                                    # 代码风格（v8.7: 211违规）
bandit -c bandit.yaml -lll -ii -r utils/ v8.3_institutional/src/  # 安全扫描
mypy institutional_pipeline_runner.py                          # 类型检查
pre-commit run --all-files                                      # Pre-commit钩子
```

### 测试

```bash
pytest tests/test_data_contracts.py -v -m contract              # 快测层（<1s）
pytest tests/test_regression_bugfixes.py -v -m "regression and not integration"  # 单元回归
pytest tests/unit/                                              # 单元测试（2979 passed, 0 our-failures）
pytest tests/e2e/                                               # 端到端测试
pytest tests/perf/                                              # 性能基准
pytest --cov=. --cov-report=html                                # 覆盖率（line_rate=0.833）
```

### P0启动自检钩子

```bash
git config core.hooksPath githooks          # 安装
python scripts/run_p0_startup_check.py --strict    # P0严格自检
```

---

## 双机部署架构（Mac研究 + Windows云实盘）

> 完整文档见 `docs/ARCHITECTURE_Mac研究_Windows云实盘.md`

| 平台 | 适用性 | 推荐度 |
|------|------|------|
| **阿里云ECS（华东2-上海）** | ✅ Windows Server 2022/固定IP/券商白名单 | 🥇 首选 |
| 腾讯云CVM（华南） | ✅ 同上，华南券商延迟更低 | 🥈 备选 |

**推荐配置**：阿里云`ecs.g7.xlarge`（4核16G）+ 100GB ESSD + 固定公网IP，**月费约¥315**

```bash
# Mac端一键配置
chmod +x scripts/deploy/mac_setup.sh && ./scripts/deploy/mac_setup.sh

# Windows云服务器一键配置
Set-ExecutionPolicy -Scope Process Bypass; .\scripts\deploy\windows_setup.ps1

# 日常使用（Mac远程触发）
quant-remote status      # 查询实盘状态
quant-remote premarket   # 触发盘前工作流
quant-remote eod         # 触发盘后
```

---

## 文档索引

| 文档 | 说明 |
|------|------|
| [CHANGELOG.md](CHANGELOG.md) | 版本更新日志 |
| [cairn/ROADMAP.md](cairn/ROADMAP.md) | 路线图与进度 |
| [cairn/LOG.md](cairn/LOG.md) | 按时间顺序日志（最新在顶部） |
| [cairn/third-party-integration-batch-20260822.md](cairn/third-party-integration-batch-20260822.md) | 🆕 第三方项目批量集成经验 |
| [cairn/self-evolution-framework.md](cairn/self-evolution-framework.md) | 自我进化框架设计文档 |
| [cairn/etf-option-hedge-model.md](cairn/etf-option-hedge-model.md) | ETF期权对冲子模型 |
| [cairn/mvsk-higher-moment-optimization.md](cairn/mvsk-higher-moment-optimization.md) | MVSK高阶矩优化 |
| [cairn/code-quality-industrial-gap-20260819.md](cairn/code-quality-industrial-gap-20260819.md) | 代码质量工业级差距审计 |
| [cairn/classic-theory-coverage-20260819.md](cairn/classic-theory-coverage-20260819.md) | 经典理论覆盖度审计 |
| [cairn/gnn-supply-chain-factor.md](cairn/gnn-supply-chain-factor.md) | GNN供应链产业链因子 |
| [docs/empirical_research_skills/](docs/empirical_research_skills/) | 🆕 AERS实证研究Skill索引 |
| [docs/data_source_catalog/](docs/data_source_catalog/) | 🆕 FinceptTerminal数据源目录 |
| [.env.example](.env.example) | 环境变量模板 |

---

## 关键约束

- **API密钥**：禁止硬编码，必须使用环境变量
- **数据真实性**：实时数据采集优先，历史数据兜底需标注日期，绝不使用模拟数据生成最新报告
- **降级原则**：始终优雅降级，不因上层数据源不可用而崩溃；`except: pass`必须附"降级语义"注释
- **风险控制**：所有风控guard检查默认False（fail-safe），防止静默失效
- **不可变性**：始终创建新对象，绝不原地修改（DataFrame用`.assign()`而非直接赋值）
- **配置一致性**：三份配置文件（portfolio.yaml / positions.json / system_config.json）必须保持一致
- **因子正交性**：因子库新增因子必须通过共线性检查，|ρ|>0.99须做正交化或残差化处理

---

## 版本历史

| 版本 | 日期 | 关键变更 |
|------|------|----------|
| **v8.7** | 2026-08-22 ~ 12-31 | 第三方批量集成(TradingAgents+Vibe-Trading+timesfm+ai-berkshire+FinceptTerminal+AERS+unsloth+supply_chain_risk) + ETF期权对冲Phase1 + PhaseB B1启用 + 三门禁D9/D10达标 + MVSK P1-P4生产就绪 + 可观测性 + 经典理论覆盖度审计 + Wave12-A全部完成(stumpy/Open-Meteo/RSS/trafilatura/empyrical) + 代码质量A- + 架构图v8.7 |
| v8.6.15 | 2026-08-05 | U1-U5升级 + VolRegimeWeighter + 自我进化框架 + EOD Shadow状态同步 |
| v8.6.14 | 2026-08-02 | 因子库对标GTJA191 + daily_trade_executor双Bug修复 + 安全合规加固 |
| v8.6.13 | 2026-08-01 | 气象因子引擎 + Scrapling反爬 + TradingAgents-CN桥接 |
| v8.6.12 | 2026-07-31 | P0启动自检系统 + 数据契约测试 + 回归测试套件 + pre-commit钩子 |
| v8.5 | 2026-07-24 | P0 Bug修复 + v8.5模块真实集成（9个核心模块） |

> 详细变更见 [CHANGELOG.md](CHANGELOG.md)

---

## 版权与许可（Copyright & License）

### 著作权声明

**© 2026 yuppiez99999（作者）。保留所有权利。**

本仓库（下称"本项目"）及其全部内容——包括但不限于全部源代码、策略逻辑、算法模型、回测与实盘框架、配置文件、文档、设计图、示例数据及其任何修改与衍生版本——均受《中华人民共和国著作权法》及其他适用知识产权法律法规与国际公约的保护。本项目的一切著作权及其他相关权益均归作者 yuppiez99999 所有。

### 允许的行为

在遵守本声明全部条款的前提下，您被授予**非独占、不可转让、仅供个人使用的有限许可**，允许：

- 出于**个人学习、研究、技术交流**目的查看、下载与阅读本项目；
- 在个人非商业项目中参考本项目，引用时**必须显著署名**并附本仓库地址。

### 明确禁止的行为（未授权）

1. **禁止商业用途**：未经作者事先书面授权，禁止将本项目或其任何组成部分用于任何商业目的，包括但不限于：直接或间接销售、出租、有偿提供服务或咨询、以本项目为基础构建商业产品/策略/服务、将本项目集成到商业软件中、以及利用本项目进行的任何营利性活动。
2. **禁止再分发与公开传播**：未经作者书面许可，不得将本项目整体或部分地重新发布、上传至任何公共仓库、镜像站点、代码托管平台或通过其他方式向公众传播（您本人的私有备份除外）。
3. **禁止删除或篡改标识**：任何允许的复制、转载、引用都必须完整保留本版权声明、作者署名及本仓库链接，不得删除、修改或遮挡任何版权与署名信息，不得以误导性方式暗示本项目与您或任何第三方的关联。
4. **禁止衍生开发后未署名**：基于本项目产生的修改、翻译、衍生作品，其著作权仍受本声明约束，衍生作品必须继续保留原始版权与署名信息，且不得用于商业用途。
5. **禁止非法用途**：禁止将本项目用于任何违反法律法规、监管规定或用于操纵市场、内幕交易等非法活动。

### 其他权益

本项目名称、标志、版式设计及可能构成商标或其他知识产权的标识，其相关权益亦归作者所有。本声明未明确授予的一切权利均为作者保留。作者保留对任何侵权行为依法追究责任（包括但不限于停止侵害、赔偿损失）的权利。

### 免责声明

本项目按"现状"（AS-IS）提供，作者**不提供任何形式的明示或默示担保**，包括但不限于适销性、特定用途适用性与不侵权的担保。本项目仅用于量化技术研究、学习与交流，**不构成任何投资建议、收益承诺或交易邀约**。作者不对任何人因使用、依赖、参考本项目或其任何结果（含策略信号、回测与实盘数据）而遭受的任何直接或间接损失承担责任。**股市有风险，投资需谨慎。**

### 商业授权与合作

如需商业授权、转载授权、合作或其他超出本声明范围的使用，请通过 GitHub 联系作者 yuppiez99999 获取书面授权。

---

## Git工作流（Conventional Commits）

```
feat(pipeline): 新增 V9 regime-specific 双模型
fix(risk): 修复回撤熔断器符号约定冲突
refactor(core): 提取信号融合公共函数
docs(readme): 更新 README 至 v8.7
```
