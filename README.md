# 终极量化交易系统 v8.6.14

> 500 万实盘部署 | 全自动交易闭环 | 年化 ≥ 8% 且最大回撤 < 15% | 双 LLM 决策 | 多源数据融合 | 风控守卫强制执行 | P0 自检系统 | 数据契约测试 | 气象因子引擎 | GTJA191 因子对标

**作者**：yuppiez99999
**实盘状态**：✅ 已部署（2026-07-28）
**生产基线**：Python 3.14.4（junction `C:\QuantSys`），兼容 Python 3.9+
**最近更新**：2026-08-02 — 因子库对标国泰君安 GTJA191 + 代码质量加固 + 安全合规

---

## 核心特性

### 机构级量化架构
- **双账户结构**：500 万总资金（现货 400 万 + 对冲 100 万），已实盘部署
- **风险预算驱动**：Risk Parity + Kelly 公式动态分配建仓预算
- **三联对冲引擎**：Beta / Vol / Correlation 三类对冲实时联动 + 尾部风险保护
- **完整风控体系**：个股止损 + 组合回撤四级防御 + Walk-Forward 回测验证
- **硬性风险约束**：单标的 10% 上限、单板块 25% 上限、组合日度 VaR95 1.5%

### AI 增强决策
- **双 LLM 架构**：快速模式 Qwen2.5 7B（~22 秒）+ 深度思考 DeepSeek-R1 14B（~1-3 分钟）
- **深度思考触发**：5 种场景自动切换深度模型（组合止损 / 多股止损 / ETF 加仓 / 对冲偏离 / 大幅盈亏）
- **六级降级链**：Ollama → 腾讯混元 → 百度千帆 → 智谱 GLM → 豆包 → DeepSeek
- **新闻情感分析**：实时抓取东方财富 / 巨潮资讯 / 新浪财经公告与研报
- **价格预测**：TimesFM 零样本 + TensorFlow LSTM + ARIMA 三级降级

### 完全自动化
- **无人化交易闭环**：盘前自动生成 → 自动确认 → 盘后自动执行 → 自动生成下一日计划
- **Windows 任务调度**：07:05 盘前 / 09:30 早盘 / 14:00 午盘 / 21:00 夜盘 / 15:30 盘后 + 盘中每 15 分钟 LLM 决策
- **十五五规划对齐**：2026-2030 五年阶段管理，2030-12-31 强制清仓

### V8.6.14 因子库对标 + 代码质量加固（新增）
- **11 大类因子体系**（`utils/alpha_factor/` 包）：Value / Growth / Quality / Leverage / Operation / Momentum / LowVolatility / Size / Liquidity / Technical / Expectation，全面对标国泰君安 GTJA191 因子分类
- **GTJA191 集成**：复用 `ms_strategy.factors.gtja191_factors.GTJA191Factors` 纯 Python 实现（21 因子），`DEFAULT_GTJA` 精选 9 个短周期量价因子，双实现回退（ms_strategy 优先）
- **三级共线解决方案**：①基础窗口正交化 ②双重残差化 ③非单调变换（V 型得分），将 |ρ|>0.99 的完全共线对从 **12 对降至 0 对**
  - `MOM_INDUSTRY_ADJ`：行业内去均值，ρ 从 +1.000 → +0.6214
  - `LIQ_DEPTH`：改为 60 日成交量 CV（无量纲），ρ 从 +1.000 → -0.06
  - `SIZE_NON_LINEAR`：中盘 V 型得分 + 正交化，ρ 从 +0.999 → +0.57
  - `SIZE_CUBIC`：`log(mcap)^3` 对 `log(mcap)` 正交化，ρ 从 +1.000 → +0.15
  - `LIQ_TURNOVER_60D` 对 20D 残差化，ρ 从 +0.9923 → <0.5
- **IC 计算 look-ahead 修复**：`calc_ic` 参数 `forward_days` → `lookback_days`，明确回看/前瞻语义
- **daily_trade_executor 双 Bug 修复**：预算分配公式（旧公式等价 weight×3 过度分配 → 纯权重比例）+ 原子写入顺序（先 progress 后 positions，支持幂等重放）
- **安全合规加固**：3 处硬编码 API Key 移除（probe 脚本改用环境变量 `APIZERO_API_KEY`）+ `.ocr_home/` `.opencode*/` 加入 `.gitignore` 防止 OCR 工具密钥入库
- **异常处理规范化**：174 处 `except: pass` 补"降级语义"注释（`concurrency.py` / `data_quality_monitor.py`），区分合法降级与静默吞错

### V8.6.13 气象因子引擎
- **7 因子体系**：温度 / 降水 / 风速 / 辐照度 / 气压 / 空气质量 / 能见度，覆盖能源/采掘/冶炼/农业/医药五大行业敏感度
- **气象数据适配器**（`utils/weather_data_adapter.py`）：apizero.cn 商业 API → Open-Meteo 免费降级链，429 限流自动熔断 + 10 分钟结果缓存
- **因子计算引擎**（`utils/weather_factor_engine.py`）：14 标的地理坐标映射（三峡大坝/神东矿区/宜宾锂矿等），按行业加权汇总输出 STRONG_BULL ~ STRONG_BEAR 五级信号
- **WeatherAgent 第 6 位专家**：在 `FinanceAgentOrchestrator` 中以 11% 权重参与多 Agent 决策（估值22%/动量22%/风险22%/情绪13%/宏观10%/气象11%）
- **信号融合第 9 层**：通过 `PostMixLayer` 注入 `SignalFusionEngine`，与其他 8 类信号源动态加权叠加
- **标的映射配置**（`config/weather_symbols_mapping.yaml`）：14 股票 + ETF/期货，含气象敏感度（0-1）和关键因子权重

### V9 Regime-Specific LGB + 影子账户
- **V9 生产基线**：Regime-Specific LGB 双模型 — 年化 19.62% / 最大回撤 9.95% / Sharpe 1.315
- **影子账户灰度发布**：10% 资金（¥500,000）灰度运行，三阶段推进（10% → 50% → 100%）
- **Fail-fast 触发器**：单日回撤 > 3% 或 3 日累计回撤 > 5% 立即终止 + latch 锁存

---

## 快速开始

### 1. 环境准备

```bash
# 克隆仓库
git clone <repo-url>
cd 28-终极量化交易系统8.4

# 安装依赖（Python 3.9+）
pip install -r requirements.txt

# 开发工具（可选）
pip install -r requirements_dev.txt
```

### 2. 环境变量配置

复制 `.env.example` 为 `.env`，填入以下密钥：

```ini
WIND_API_KEY=...          # Wind MCP（P1 数据源）
IFIND_TOKEN=...           # iFinD MCP（P2 数据源）
TS_TOKEN=...              # Tushare（国内期货/CPI）
VOLCENGINE_API_KEY=...    # 豆包 LLM
DEEPSEEK_API_KEY=...      # DeepSeek（信号计算）
GLM_API_KEY=...           # 智谱 GLM-5.2（合规审计）
MOONSHOT_API_KEY=...      # Kimi3（研报多模态）
CLAUDE_API_KEY=...        # Claude（深度推理/风控）
OPENAI_API_KEY=...        # GPT（盘中研判）
APIZERO_API_KEY=...       # APIZero（气象 API + probe 脚本）
OLLAMA_MODELS=E:\各种PY程序\10_第三方项目\LLM_Models  # Ollama 模型路径
LOCAL_LLM_MODEL_PATH=...  # 本地 LLM 路径
LOG_LEVEL=INFO
```

### 3. 系统自检

```bash
python system_health_check.py

# P0 严格自检（盘前最终核查）
python scripts/run_p0_startup_check.py --strict
```

### 4. 首次运行

```bash
# 烟雾测试（不实际交易）
python institutional_pipeline_runner.py --mode smoke

# 回测模式
python institutional_pipeline_runner.py --mode backtest --symbols 600519 000858

# 生产模式
python institutional_pipeline_runner.py --mode live
```

---

## 主入口与 CLI 命令

### 主入口：`institutional_pipeline_runner.py`

机构级量化闭环运行器，串联数据门控 → Alpha 评估 → 信号融合 → 组合优化 → 风险预算 → 执行路由完整链路。

```bash
python institutional_pipeline_runner.py --mode smoke                                  # 烟雾测试
python institutional_pipeline_runner.py --mode backtest --symbols 600519 000858       # 回测
python institutional_pipeline_runner.py --mode live                                   # 生产
```

### 日度工作流：`run_daily_eod.py`

盘后自动闭环：收盘报告 → 风控守卫 → 次日计划 → 预生成盘中决策。

```bash
python run_daily_eod.py                    # 完整盘后工作流
python run_daily_eod.py --phase report     # 仅生成报告
python run_daily_eod.py --phase plan       # 仅生成次日计划
```

### 实时监控：`live_scheduler.py`

定时调度器，交易日 09:25-15:05 每 15 分钟触发 LLM 盘中决策。

```bash
python live_scheduler.py                   # 启动调度器
python live_scheduler.py --once            # 单次执行
```

### 其他常用命令

```bash
python system_health_check.py              # 系统健康检查
python daily_trade_executor.py             # 交易计划执行
python stop_loss_monitor.py                # 止损监控
python signal_monitor.py                   # 信号监控
python build_plan_executor.py              # 建仓计划执行
python generate_daily_report.py            # 生成日报
```

---

## 因子体系（v8.6.14 重构）

### 11 大类因子分类（对标国泰君安 GTJA191）

```
utils/alpha_factor/
├── base.py                  # 基础数据结构 + 预处理工具（去极值/标准化/中性化/正交化）
├── library.py               # 因子库聚合入口（11 大类统一调度 + 跨类正交化后处理）
├── alpha_factor_library.py  # 向后兼容 shim（保留旧导入接口）
├── value.py                 # 价值类（EP/DP/BP/SP/CFP）
├── growth.py                # 成长类（营收/利润/资产增长率）
├── quality.py               # 质量类（ROE/ROA/毛利率）
├── leverage.py              # 杠杆类（资产负债率/权益乘数）
├── operation.py             # 运营类（资产周转率/存货周转率）
├── price_volume.py          # 量价类（动量/低波/规模/流动性）
├── technical.py             # 技术类（GTJA191 量价因子集成）
└── expectation.py           # 预期类（分析师预期/微结构）
```

### GTJA191 因子集成

通过 `utils/alpha_factor/technical.py` 集成 `ms_strategy.factors.gtja191_factors.GTJA191Factors` 纯 Python 实现：

```python
from utils.alpha_factor.library import AlphaFactorLibrary, DEFAULT_GTJA

# DEFAULT_GTJA 精选 9 个短周期量价因子
# gtja191_004 / gtja191_018 / gtja191_030 / gtja191_044 / gtja191_054
# gtja191_084 / gtja191_092 / gtja191_148 / gtja191_178

library = AlphaFactorLibrary()
result = library.compute(data, factor_names=DEFAULT_GTJA)
```

### 三级共线解决方案

| 级别 | 方法 | 适用场景 | 典型应用 |
|------|------|----------|----------|
| L1 | 基础窗口正交化 | 多窗口因子共线 | `LIQ_TURNOVER_60D` 对 20D 残差化 |
| L2 | 双重残差化 | 跨公式等价共线 | `SIZE_CUBIC` 对 `log(mcap)` 正交化 |
| L3 | 非单调变换 | 线性关系共线 | `SIZE_NON_LINEAR` 改为中盘 V 型得分 |

**效果**：|ρ|>0.99 的完全共线对从 **12 对降至 0 对**，因子库正交性达到机构级标准。

---

## 数据源优先级

全局统一标准，所有模块必须遵循以下降级链，不可跳级：

| 优先级 | 数据源 | 说明 | 认证 |
|--------|--------|------|------|
| P0 | Wind 数据终端 | 主数据源，WindPy 原生客户端 | WindPy 授权 |
| P1 | Wind MCP | 强制回退，analytics_data / stock_data / fund_data | `WIND_API_KEY` |
| P2 | iFinD MCP | 强制回退，同花顺金融终端 | `IFIND_TOKEN` |
| P2.5 | 通达信 (pytdx) | 免费直连，TCP 7709 端口，仅 A 股 | 无需 |
| P3 | AKShare / baostock | 免费回退，A 股 / 期货 / 指数 | 无需 |
| P4 | 新浪财经 API | 免费实时行情兜底 | 无需 |
| P5 | 本地缓存 | Parquet / JSON 缓存 | 无需 |
| P6 | 预定义价格 | 保证系统永不崩溃 | 无需 |

**强制规则**：Wind 不可用时必须尝试 Wind MCP，不可直接跳到 iFinD。

---

## 项目结构

```
28-终极量化交易系统8.4/
├── institutional_pipeline_runner.py     # 主入口 — 机构级闭环运行器
├── run_daily_eod.py                     # 盘后工作流入口
├── live_scheduler.py                    # 实时调度器
├── daily_trade_executor.py              # 交易计划执行（v8.6.14 Bug 修复）
├── generate_daily_report.py             # 日报生成
├── lgb_enhanced_trainer.py              # LightGBM 增强训练器
├── alpha_hedge_engine.py                # Alpha 对冲引擎
├── stop_loss_monitor.py                 # 止损监控
├── signal_monitor.py                    # 信号监控
├── build_plan_executor.py               # 建仓计划执行
│
├── utils/                               # 核心工具模块（100+ 模块）
│   ├── alpha_factor/                    # ★v8.6.14 因子库包（11 大类，对标 GTJA191）
│   │   ├── base.py                      # 基础数据结构 + 预处理（去极值/标准化/中性化/正交化）
│   │   ├── library.py                   # 因子库聚合入口（11 大类调度 + 跨类正交化后处理）
│   │   ├── alpha_factor_library.py      # 向后兼容 shim
│   │   ├── value.py / growth.py / quality.py / leverage.py / operation.py
│   │   ├── price_volume.py              # 动量/低波/规模/流动性（含共线修复）
│   │   ├── technical.py                 # GTJA191 量价因子集成
│   │   ├── expectation.py               # 预期/微结构
│   │   ├── concurrency.py               # ★v8.6.14 并发安全（except:pass 降级注释）
│   │   └── data_quality_monitor.py      # ★v8.6.14 数据质量监控（except:pass 降级注释）
│   ├── alpha/                           # Alpha 信号与 LLM 路由
│   │   ├── llm/                         # LLM 提供商（DeepSeek/豆包/GLM/Ollama/OmniRoute）
│   │   ├── multi_factor_signal.py       # 多因子信号
│   │   ├── strategy_evaluator.py        # 策略评估器
│   │   └── drift_monitor.py             # 漂移监控
│   ├── attribution/                     # 业绩归因（Brinson/因子）
│   ├── execution/                       # 执行引擎（自动执行/券商适配/再平衡）
│   ├── finance_agents/                  # AI 分析师（宏观/动量/风险/情绪/价值/气象）
│   │   └── weather_agent.py             # ★v8.6.13 气象因子分析 Agent (第 6 位专家)
│   ├── infra/                           # 基础设施（bootstrap/feature_flags）
│   ├── reporting/                       # 报告生成
│   ├── risk/                            # 风险模块（kill_switch/risk_bus）
│   ├── universe/                        # 标的池管理
│   ├── risk_constraints.py              # 硬性风险约束
│   ├── risk_budget_engine.py            # 风险预算引擎
│   ├── drawdown_breaker.py              # 回撤熔断器
│   ├── hedge_execution_engine.py        # 对冲执行引擎
│   ├── protective_put_engine.py         # 认沽期权保护引擎
│   ├── vol_target_controller.py         # 波动率目标缩仓
│   ├── signal_fusion.py                 # 多源信号融合（9 层，含气象因子）
│   ├── weather_data_adapter.py          # ★v8.6.13 气象数据适配器 (apizero→Open-Meteo)
│   ├── weather_factor_engine.py         # ★v8.6.13 气象因子计算引擎 (7 因子体系)
│   ├── scrapling_adapter.py             # ★v8.6.13 高性能反爬爬虫适配器
│   ├── tradingagents_bridge.py          # ★v8.6.13 TradingAgents-CN HTTP 桥接
│   ├── config_manager.py                # 统一配置管理
│   ├── kill_switch.py                   # Kill Switch 三级熔断
│   └── ...
│
├── v8.3_institutional/                  # 机构级基础设施与日度工作流
│   ├── daily_workflow/                  # 日度工作流（phases/）
│   │   ├── core/                        # 工作流编排器
│   │   ├── phases/                      # 14 个阶段（check/market/signal/hedge/execute/report 等）
│   │   └── cli/                         # CLI 参数解析
│   ├── hexin_broker/                    # 同花顺券商接口
│   ├── data_pipeline/                   # 数据管道
│   └── config/                          # 配置文件
│
├── ui/                                  # Streamlit 可视化面板（14 页）
│   ├── app.py                           # 主入口
│   ├── pages/                           # 14 个页面
│   └── components/                      # 公共组件
│
├── ai_decision/                         # AI 决策模块
│   ├── orchestrator.py                  # 决策编排器
│   ├── debate_engine.py                 # 辩论引擎
│   ├── consensus_aggregator.py          # 共识聚合
│   └── execution_bridge.py              # 执行桥接
│
├── ms_strategy/                         # 多策略框架
│   ├── src/alpha/                       # Alpha 因子库
│   ├── src/backtest/                    # 回测引擎
│   ├── src/execution/                   # 执行算法
│   ├── src/hedging/                     # 对冲策略
│   ├── src/risk/                        # 风险管理
│   └── factors/gtja191_factors.py       # ★v8.6.14 GTJA191 因子纯 Python 实现（21 因子）
│
├── tests/                               # 测试套件
│   ├── unit/                            # 单元测试
│   ├── integration/                     # 集成测试
│   ├── e2e/                             # 端到端测试
│   ├── regression/                      # 回归测试
│   ├── smoke/                           # 烟雾测试
│   ├── test_data_contracts.py           # ★v8.6.12 数据契约测试 (JSON Schema 校验)
│   └── test_regression_bugfixes.py       # ★v8.6.12 回归测试套件 (已修复 bug)
│
├── scripts/                             # 工具脚本
│   ├── run_tests.ps1                    # ★v8.6.12 本地分层测试脚本 (fast/slow/contract/regression/all)
│   ├── pre_commit_check.py              # ★v8.6.12 P0 自检 pre-commit 钩子
│   ├── run_p0_startup_check.py          # P0 启动自检命令行入口
│   ├── probe_apizero_deep.py            # ★v8.6.14 APIZero 探测（改用环境变量）
│   ├── probe_weather_api.py             # ★v8.6.14 气象 API 探测（改用环境变量）
│   ├── probe_weather_v2.py              # ★v8.6.14 气象 API v2 探测（改用环境变量）
│   └── ...
├── githooks/                            # ★v8.6.12 Git 钩子
│   ├── pre-commit                       # pre-commit 钩子入口 (P0 自检)
│   └── README.md                        # 钩子使用说明
│
├── tools/                               # 开发工具
├── research/                            # 研究脚本与报告
├── reporting/                           # 报告模块
├── skills/                              # AI Skills
├── docs/                                # 文档
│   └── archive/                         # 归档文档
│
├── config/                              # 全局配置
│   ├── positions.json                   # 持仓状态
│   ├── portfolio.yaml                   # 组合配置
│   ├── weather_symbols_mapping.yaml     # ★v8.6.13 气象因子标的地理映射 (14 标的)
│   └── stop_loss_rules_auto.yaml        # 止损规则
│
├── requirements.txt                     # 生产依赖
├── requirements_dev.txt                 # 开发依赖
├── requirements_lock.txt                # 依赖锁版本
├── ruff.toml                            # Ruff 配置
├── bandit.yaml                          # Bandit 安全扫描配置
├── mypy.ini                             # mypy 配置
├── pytest.ini                           # pytest 配置
├── .pre-commit-config.yaml              # pre-commit 钩子
├── .env.example                         # ★v8.6.14 环境变量模板（含 APIZERO_API_KEY）
├── .gitignore                           # ★v8.6.14 加入 .ocr_home/ .opencode*/
├── CHANGELOG.md                         # 更新日志
└── system_config.json                   # 系统配置
```

---

## 风控体系

### 四模块联动风控链

每日 EOD 后强制执行：回撤检查 → 波动率控制 → 对冲执行 → 认沽保护。

| 模块 | 文件 | 职责 |
|------|------|------|
| 回撤熔断器 | `utils/drawdown_breaker.py` | 四级回撤防御（Level 1 预警 → Level 4 全面停止） |
| 波动率目标缩仓 | `utils/vol_target_controller.py` | AQR / Man Group 风格 Vol Targeting，realized vol > 12% 自动缩仓 |
| 对冲执行引擎 | `utils/hedge_execution_engine.py` | 对冲信号 → IF 期货 + ETF 期权订单，动态 Beta 计算 |
| 认沽期权保护 | `utils/protective_put_engine.py` | ¥77.8 万 Put 预算，OTM 5% 虚值覆盖四大指数 ETF，到期前 5 天滚仓 |
| Kill Switch | `utils/kill_switch.py` | L1/L2/L3 三级熔断，触发即终止工作流 |
| 风险守卫集成 | `utils/risk_guard_integrator.py` | 四 Guard 联动 + 执行日志 |

### 硬性风险约束（`utils/risk_constraints.py`）

- 单标的硬上限：10%（V3 优化：15% → 10%，降低路径依赖风险）
- 单一板块硬上限：25%
- 组合日度 VaR95 硬上限：1.5%
- 单票日度 VaR95 硬上限：0.8%
- 对冲暴露上限：`HEDGE_EXPOSURE_CAP = 0.40`
- 换手率预算：`TURNOVER_BUDGET = 0.20`（接近预算时自动上调再平衡阈值）

---

## 回测协议

### 目标函数

```
J = Sortino + 0.5 × Calmar - λ‖w‖²
```

### Walk-Forward 配置

- 训练窗口：24 月
- 测试窗口：3 月
- 步长：3 月

### 必过压力测试

| 事件 | 区间 | 跌幅 |
|------|------|------|
| 全球金融危机 | 2008-09-15 ~ 2009-03-09 | S&P -56% |
| A 股股灾 | 2015-06-12 ~ 2015-08-26 | 上证 -43% |
| 熔断机制 | 2016-01-04 ~ 2016-01-28 | 4 天 2 次熔断 |
| 中美贸易战 | 2018-03-22 ~ 2018-10-29 | 上证 -25% |
| COVID 闪崩 | 2020-02-19 ~ 2020-03-23 | — |
| Luna 崩盘 | 2022-05-01 ~ 2022-05-12 | — |
| 全球债券大屠杀 | 2022-01-01 ~ 2022-10-24 | 股债双杀 |
| 日元 Carry Trade | 2024-08-01 ~ 2024-08-05 | — |

### CRO Gate（上线前必过）

- [ ] Walk-Forward 5 窗口拼接 Sortino ≥ 1.0
- [ ] 八段压力测试 Max DD < 15%
- [ ] Deflated Sharpe Ratio ≥ 0.95
- [ ] 无未来函数（PIT 检查通过）
- [ ] NTP 漂移 < 50ms 持续 7 个交易日
- [ ] 滑点熔断在历史回放中正确触发
- [ ] CRO 签字

---

## 开发工作流

### 代码质量门禁

项目集成 ruff + bandit + vulture + mypy + pylint 五位一体静态分析：

```bash
# 代码风格检查
ruff check .

# 安全漏洞扫描（HIGH 严重度）
bandit -c bandit.yaml -lll -ii -r utils/ v8.3_institutional/src/

# 死代码检测
vulture . --min-confidence 80

# 类型检查
mypy institutional_pipeline_runner.py

# Pre-commit 钩子 (Python 格式化/静态分析)
pre-commit run --all-files
```

### P0 启动自检钩子（v8.6.12 新增）

Git 提交前自动执行 P0 启动自检，阻止有问题的代码进入仓库。

#### 安装

```bash
# 方式 1: 复制钩子目录到 .git/hooks
cp githooks/pre-commit .git/hooks/pre-commit

# 方式 2: 配置 core.hooksPath (推荐)
git config core.hooksPath githooks
```

#### 工作机制

1. **自动触发**：每次 `git commit` 前自动执行 `python scripts/pre_commit_check.py`
2. **智能跳过**：
   - 暂存区全是 `.md`/`.txt`/`.gitignore` → 跳过
   - 设置环境变量 `SKIP_P0_CHECK=1` → 跳过（紧急提交）
   - 非 git 仓库 → 跳过
3. **执行内容**：调用 `python scripts/run_p0_startup_check.py --skip-datasource`（2 分钟超时）
4. **结果处理**：P0 自检失败（exit≠0）阻止提交

#### 手动测试

```bash
# 直接运行 pre-commit 检查
python scripts/pre_commit_check.py

# 手动测试 P0 自检
python scripts/run_p0_startup_check.py --skip-datasource
```

#### 跳过场景

```bash
# 紧急提交 (不推荐,仅紧急情况)
SKIP_P0_CHECK=1 git commit -m "hotfix: critical issue"
```

### 测试

#### 两层测试策略（v8.6.12 新增）

为平衡 PR 反馈速度与集成验证深度，采用**快测层 + 慢测层**分离策略：

| 层级 | 内容 | 耗时 | CI 阶段 |
|------|------|------|---------|
| **快测层** | 数据契约 + 单元回归 (排除 integration 标记) | < 1s | Unit Tests Job |
| **慢测层** | integration 标记的回归测试 (如 TDX 连接验证) | ~10s | Integration Tests Job |

#### 数据契约测试（`tests/test_data_contracts.py`）

验证关键 JSON 文件（positions.json / trade_plan_*.json / hedge_execution_fill_*.json）的字段 schema、类型和值约束，防止上游变更破坏下游解析器。

```bash
# 运行全部契约测试
pytest tests/test_data_contracts.py -v -m contract

# 仅验证持仓文件
pytest tests/test_data_contracts.py -v -k "positions"
```

#### 回归测试套件（`tests/test_regression_bugfixes.py`）

对已修复的 bug（Wind MCP 路径、SSE 解析、heartbeat 字段名兼容、positions.json schema、sys.path 注入等）编写回归测试，防止同类问题复发。

```bash
# 运行单元回归测试 (快测,排除 integration 标记)
pytest tests/test_regression_bugfixes.py -v -m "regression and not integration"

# 运行 integration 标记的慢测 (如 TDX 连接验证)
pytest tests/test_regression_bugfixes.py -v -m "integration"
```

#### 本地测试脚本（`scripts/run_tests.ps1`）

```powershell
# 快测层 - PR 快速闸门 (<1s)
.\scripts\run_tests.ps1 fast

# 慢测层 - integration 标记测试
.\scripts\run_tests.ps1 slow

# 仅契约测试
.\scripts\run_tests.ps1 contract

# 仅回归测试
.\scripts\run_tests.ps1 regression

# 全量测试
.\scripts\run_tests.ps1 all
```

#### 其他测试

```bash
# 全量测试
pytest

# 单元测试
pytest tests/unit/

# 集成测试
pytest tests/integration/

# 研究模式 (Mac) 自检逻辑测试
pytest tests/test_system_check_mac.py -v

# 端到端测试
pytest tests/e2e/

# 烟雾测试
pytest tests/smoke/

# 覆盖率报告
pytest --cov=. --cov-report=html
```

---

## 双机部署架构 (Mac 研究 + Windows 云实盘)

> 完整文档见 [`docs/ARCHITECTURE_Mac研究_Windows云实盘.md`](docs/ARCHITECTURE_Mac研究_Windows云实盘.md)
> 配套配置: [`config/settings_mac.yaml`](config/settings_mac.yaml) | 部署脚本: [`scripts/deploy/`](scripts/deploy/)

### 适用场景

MacBook (Apple Silicon) 做研究/训练/回测,Windows 云服务器做实盘下单,两机通过 Tailscale + syncthing 自动同步。

### 整体架构

```mermaid
flowchart TB
    subgraph MAC["MacBook M5 Max (本地研究机)"]
        IDE["IDE 开发"]
        RESEARCH["因子研究 / ML 训练"]
        BACKTEST["回测引擎 (walk_forward)"]
        MAC_CHECK["P0 自检 (研究模式)"]
        MAC_DATA["数据源: AKShare + yfinance"]
    end

    subgraph WIN["Windows 云服务器 (实盘执行机)"]
        WIND["Wind 终端 + WindPy"]
        QMT["QMT 实盘下单"]
        THS["同花顺 GUI 自动化"]
        WIN_CHECK["P0 自检 (实盘 + strict)"]
        SCHED["Windows 计划任务"]
    end

    subgraph SYNC["同步层"]
        TAILSCALE["Tailscale 内网"]
        SYNCTHING["syncthing 双向同步"]
        GIT["Git 代码管理"]
    end

    MAC -->|"SSH 远程触发"| WIN
    MAC -->|"Tailscale"| TAILSCALE
    TAILSCALE --> WIN
    MAC -->|"模型/报告"| SYNCTHING
    SYNCTHING -->|"持仓/订单"| MAC
    MAC -->|"代码"| GIT
    GIT --> WIN

    style MAC fill:#e3f2fd,stroke:#1976d2,stroke-width:2px
    style WIN fill:#fff3e0,stroke:#f57c00,stroke-width:2px
    style SYNC fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px
```

### 数据流向

```mermaid
sequenceDiagram
    autonumber
    participant M as Mac (研究)
    participant S as syncthing
    participant W as Windows (实盘)
    participant B as 券商 (QMT/Wind)

    Note over M: T-1 盘后: 研究+训练
    M->>M: 因子研究 + ML 训练
    M->>M: 生成交易计划
    M->>S: 推送 trade_plan + models
    S->>W: 同步到 Windows

    Note over W: T 日盘前: 实盘执行
    W->>W: P0 自检 --strict
    W->>B: Wind 获取行情
    W->>W: 对冲+再平衡计算
    W->>B: QMT 下单
    B-->>W: 成交回报
    W->>S: 推送 positions + 报告
    S->>M: 同步到 Mac

    Note over M: T 日盘后: 复盘
    M->>M: 读取 positions + 报告
    M->>M: 迭代因子/模型
```

### P0 自检双模式

| 检查维度 | Windows 实盘模式 | Mac 研究模式 |
|---------|---------|---------|
| C1 关键文件 | 9 个全检 | 7 个(跳过 hedge_execution_engine / risk_guard_integrator) |
| C2 环境变量 | WIND_API_KEY 必需 | 降级为可选 |
| C3 数据源 | Wind + iFinD + TDX + AKShare | 仅 AKShare + yfinance |
| C7 子模块 | HedgeExecutionEngine 必需 | 跳过(由 Windows 负责) |
| 启用方式 | Windows 默认 | Mac 自动 或 `QUANT_RESEARCH_MODE=1` |

### 云平台选择

| 平台 | 适用性 | 推荐度 |
|------|------|------|
| 超算中心 | ❌ 无 Windows GUI / 非实时 / IP 不固定 | 不推荐(仅适合研究侧大规模训练) |
| **阿里云 ECS (华东2-上海)** | ✅ Windows Server 2022 / 固定 IP / 券商白名单 | 🥇 首选 |
| 腾讯云 CVM (华南) | ✅ 同上,华南券商延迟更低 | 🥈 备选 |

**推荐配置**: 阿里云 `ecs.g7.xlarge` (4核16G) + 100GB ESSD + 固定公网 IP,**月费约 ¥315**

### 快速部署

```bash
# Mac 端一键配置
chmod +x scripts/deploy/mac_setup.sh
./scripts/deploy/mac_setup.sh

# Windows 云服务器一键配置 (管理员 PowerShell)
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\deploy\windows_setup.ps1

# 日常使用 (Mac 远程触发)
quant-remote status      # 查询实盘状态
quant-remote premarket   # 触发盘前工作流
quant-remote eod         # 触发盘后
quant-remote check       # 远程 P0 自检

# macOS 定时触发 (launchd)
./scripts/deploy/launchd_install.sh install   # 安装定时任务
./scripts/deploy/launchd_install.sh list      # 查看已加载任务
./scripts/deploy/launchd_install.sh uninstall # 卸载
```

### 关键文件

| 文件 | 作用 |
|------|------|
| [`config/settings_mac.yaml`](config/settings_mac.yaml) | Mac 研究模式配置(数据源/CLI白名单/安全锁) |
| [`scripts/deploy/mac_setup.sh`](scripts/deploy/mac_setup.sh) | Mac 一键配置脚本 |
| [`scripts/deploy/windows_setup.ps1`](scripts/deploy/windows_setup.ps1) | Windows 云服务器一键配置 |
| [`scripts/deploy/quant-remote.sh`](scripts/deploy/quant-remote.sh) | Mac 远程触发+状态查询 |
| [`scripts/deploy/launchd_install.sh`](scripts/deploy/launchd_install.sh) | macOS launchd 定时任务安装 |
| [`docs/ARCHITECTURE_Mac研究_Windows云实盘.md`](docs/ARCHITECTURE_Mac研究_Windows云实盘.md) | 完整架构文档 + 4 张 mermaid 图 |

> ⚠️ **安全约束**: Mac 上 `disable_live_trading: true`,禁止运行 `--live` / `--hedge-rebalance` / `--ai-decision` 等实盘模式,实盘下单一律走 Windows 云服务器。

### Git 工作流（Conventional Commits）

```
feat(pipeline): 新增 V9 regime-specific 双模型
fix(risk): 修复回撤熔断器符号约定冲突
refactor(core): 提取信号融合公共函数
docs(readme): 更新 README 至 v8.6.14
```

---

## 文档索引

| 文档 | 说明 |
|------|------|
| [CHANGELOG.md](CHANGELOG.md) | 版本更新日志 |
| [USER_GUIDE.md](USER_GUIDE.md) | 用户指南 |
| [CODE_QUALITY_PHASE_A_2026-07-30.md](CODE_QUALITY_PHASE_A_2026-07-30.md) | 代码质量审计报告 |
| [TOKEN_SECURITY_GUIDE.md](TOKEN_SECURITY_GUIDE.md) | Token 安全指南 |
| [.env.example](.env.example) | ★v8.6.14 环境变量模板（含 APIZERO_API_KEY） |
| [utils/alpha_factor/library.py](utils/alpha_factor/library.py) | ★v8.6.14 因子库聚合入口（11 大类） |
| [utils/alpha_factor/technical.py](utils/alpha_factor/technical.py) | ★v8.6.14 GTJA191 因子集成 |
| [config/weather_symbols_mapping.yaml](config/weather_symbols_mapping.yaml) | ★v8.6.13 气象因子标的地理映射 |
| [scripts/test_weather_e2e_minimal.py](scripts/test_weather_e2e_minimal.py) | ★v8.6.13 气象因子 E2E 验证脚本 |
| [docs/](docs/) | 文档目录（含归档） |
| [ms_strategy/cloud_train/README_云端部署.md](ms_strategy/cloud_train/README_云端部署.md) | QLib 云端训练部署指南 |
| [research/vibe_trading_factor_analysis/README.md](research/vibe_trading_factor_analysis/README.md) | Vibe-Trading 因子分析项目 |
| [tools/code-review-graph/README.md](tools/code-review-graph/README.md) | code-review-graph 集成说明 |
| [second-brain/README.md](second-brain/README.md) | 第二大脑知识管理系统 |

---

## 关键约束

- **API 密钥**：禁止硬编码，必须使用环境变量（v8.6.14 已清理 3 处 probe 脚本硬编码）
- **数据真实性**：实时数据采集优先，历史数据兜底需标注日期，绝不使用模拟数据生成最新报告
- **降级原则**：始终优雅降级，不因上层数据源不可用而崩溃；`except: pass` 必须附"降级语义"注释（v8.6.14 已规范化 174 处）
- **风险控制**：所有风控 guard 检查默认 False（fail-safe），防止静默失效
- **不可变性**：始终创建新对象，绝不原地修改（DataFrame 用 `.assign()` 而非直接赋值）
- **配置一致性**：三份配置文件（portfolio.yaml / positions.json / system_config.json）必须保持一致
- **因子正交性**：因子库新增因子必须通过共线性检查，|ρ|>0.99 须做正交化或残差化处理（v8.6.14 三级共线解决方案）

---

## 版本历史

| 版本 | 日期 | 关键变更 |
|------|------|----------|
| v8.6.14 | 2026-08-02 | 因子库对标 GTJA191（11 大类 + 共线修复 12→0 对）+ daily_trade_executor 双 Bug 修复 + 安全合规（probe 脚本去硬编码 / .ocr_home 入 .gitignore）+ 174 处 except:pass 补降级注释 |
| v8.6.13 | 2026-08-01 | 气象因子引擎（7 因子体系 + apizero→Open-Meteo 降级链 + WeatherAgent 第 6 位专家 + 信号融合第 9 层）+ Scrapling 反爬爬虫适配器 + TradingAgents-CN HTTP 桥接 + E2E 验证 17/17 通过 |
| v8.6.12 | 2026-07-31 | P0 启动自检系统 + 数据契约测试 + 回归测试套件 + 两层测试策略 + pre-commit 钩子 |
| v8.5 | 2026-07-24 | P0 Bug 修复 + v8.5 模块真实集成（9 个核心模块） |
| v8.4 | 2026-07-22 | 代码质量 P0/P1/P2 修复 + 配置漂移修复 |
| v8.3.1 | 2026-07-21 | PUT 引擎去重保护 |
| v8.3.0 | 2026-07-21 | 风控守卫强制执行系统 |
| v8.1 | 2026-07-17 | 全自动交易闭环 + LLM 盘中决策 |

### v8.6.14 详细变更

#### 因子库对标国泰君安 GTJA191（`utils/alpha_factor/` 包）

- **11 大类因子体系重构**：原 51 因子单文件（835 行）拆分为 11 大类模块包，各类 < 400 行
  - Value / Growth / Quality / Leverage / Operation / Momentum / LowVolatility / Size / Liquidity / Technical / Expectation
  - `library.py` 聚合入口统一调度，`alpha_factor_library.py` 作为向后兼容 shim 保留旧接口
- **GTJA191 因子集成**（`technical.py`）：复用 `ms_strategy.factors.gtja191_factors.GTJA191Factors` 纯 Python 实现（21 因子）
  - `DEFAULT_GTJA` 精选 9 个短周期量价因子（gtja191_004/018/030/044/054/084/092/148/178）
  - 双实现回退：ms_strategy 优先，utils 版本作为可选扩展
  - 修复 `_id_to_method` 前导零 Bug（生成 `alpha4` 而非 `alpha004`，匹配 ms_strategy 方法名）
  - 修复 `list_available_factors` ID 格式不一致（统一带前导零 `gtja191_004`）

#### 三级共线解决方案（|ρ|>0.99 完全共线对 12→0）

| 因子对 | 原公式 | 原相关性 | 修复方案 | 修复后相关性 |
|--------|--------|----------|----------|--------------|
| MOM_INDUSTRY_ADJ ↔ MOM_60D | 公式相同 | ρ=+1.000 | 行业内去均值（`neutralize_by_industry`） | ρ=+0.6214 |
| LIQ_DEPTH ↔ LIQ_TURNOVER_20D | 公式相同 | ρ=+1.000 | 改为 60 日成交量 CV=std/mean（无量纲） | ρ=-0.06 |
| SIZE_NON_LINEAR ↔ SIZE_LOG_MCAP | 公式线性相关 | ρ=+0.999 | 中盘 V 型得分 `-|log(mcap)-median|` + 正交化 | ρ=+0.57 |
| SIZE_CUBIC ↔ SIZE_LOG_MCAP | `log(mcap)^3` 强线性 | ρ≈+1.0 | `log(mcap)^3` 对 `log(mcap)` 正交化取残差 | ρ=+0.15 |
| LIQ_TURNOVER_60D ↔ LIQ_TURNOVER_20D | 窗口重叠 | ρ=+0.9923 | 60D 对 20D 残差化，保留长期趋势部分 | ρ<0.5 |
| VOL_60D/120D ↔ VOL_20D | 窗口重叠 | ρ>0.95 | 对 VOL_20D 正交化取残差 | ρ<0.5 |
| LIQ_AMIHUD ↔ VOL_20D | 公式含波动率 | ρ>0.85 | 跨类正交化后处理（library.py） | ρ<0.5 |

#### daily_trade_executor.py 双 Bug 修复

- **Bug 1 — 预算分配过度**：`_allocate_position` 中 `remaining_budget * pos["weight"] / 0.05 * 0.15` 等价于 weight×3，过度分配
  - 修复：简化为 `remaining_budget * pos["weight"]`，按纯权重比例分配
- **Bug 2 — 状态写入顺序**：`execute_instructions` 先写 positions 后写 progress，崩溃可能导致状态不一致
  - 修复：调整为先写 progress（带 try-except 容错）后写 positions，支持幂等重放和从 progress 恢复 positions
- **Bug 3 — IC 计算 look-ahead 歧义**：`calc_ic` 参数 `forward_days` 实际使用回看收益
  - 修复：参数名改为 `lookback_days`，添加详细说明区分回看/前瞻收益场景

#### 安全合规加固

- **probe 脚本去硬编码**（3 处）：`probe_apizero_deep.py` / `probe_weather_api.py` / `probe_weather_v2.py` 中硬编码 API Key `"tj_live_..."` 改为 `os.environ.get("APIZERO_API_KEY", "")`，并添加缺失提示
- **.gitignore 安全加固**：新增 `.ocr_home/`（含 deepseek/stepfun 真实 API Key 的 OCR 工具配置目录）+ `.opencode*/`，防止敏感信息误入库
- **.env.example 补全**：新增 `APIZERO_API_KEY=` 占位符，附带 Windows cmd / PowerShell 设置语法提示

#### 异常处理规范化（174 处 except: pass 补降级注释）

为 `concurrency.py`（4 处）和 `data_quality_monitor.py`（6 处）等模块的 `except: pass` 补"降级语义"注释，区分合法降级与静默吞错：

```python
except (TypeError, ValueError):
    # 降级语义: close 无法转为float, 跳过该标的价格范围检查, 不影响其他字段
    pass
```

#### 代码质量标准化（8 类 12 项）

- 移除 `library.py` 未用 `numpy` 导入，相关功能改用 pandas 实现
- 更新 `library.py` docstring 反映跨类正交化后处理逻辑
- `base.py` `orthogonalize` 函数增加别名 `residualize`
- `alpha_factor_library.py` 导入 `DEFAULT_GTJA` 并添加到 `__all__`，保留 `DEFAULT_GTJA_30` 作为别名

### v8.6.13 详细变更

- **气象因子引擎**（7 因子体系）
  - 数据适配器（`utils/weather_data_adapter.py`）：apizero.cn 商业 API → Open-Meteo 免费降级链，429 限流自动熔断 10 分钟 + 双层缓存（apizero/Open-Meteo 各 10 分钟 TTL）
  - 因子计算引擎（`utils/weather_factor_engine.py`）：温度/降水/风速/辐照/气压/AQI/能见度 7 因子，按行业敏感度加权（电力 1.5x/矿业 1.4x/农业 1.5x）输出 STRONG_BULL ~ STRONG_BEAR 五级信号
  - 标的映射（`config/weather_symbols_mapping.yaml`）：14 股票标的（长江电力/中国神华/宁德时代/恒瑞医药等）+ ETF/期货，含地理坐标和气象敏感度
  - WeatherAgent（`utils/finance_agents/weather_agent.py`）：第 6 位专家，11% 权重，支持 dataclass + dict 双格式输入，降级到中性保证可用性
  - 信号融合：通过 `PostMixLayer` 注入 `SignalFusionEngine`，作为第 9 类信号源
- **Scrapling 反爬爬虫适配器**（`utils/scrapling_adapter.py`）
  - 封装 StealthyFetcher/PlayWrightFetcher，集成到 `news_sentiment_engine.py` 提供企业级新闻/公告/研报采集
  - 降级到 `WebScraper`（requests + bs4）保证可用性
- **TradingAgents-CN HTTP 桥接**（`utils/tradingagents_bridge.py`）
  - 通过 HTTP 微服务方式集成 TradingAgents-CN 多 Agent 协作框架（避免 Python 3.10+ 版本冲突）
  - 健康检查 + 结果转换 + 降级链逻辑
- **E2E 验证**（`scripts/test_weather_e2e_minimal.py`）
  - 17/17 测试通过：模块导入 / 数据适配器降级链 / 因子计算（3 标的）/ WeatherAgent 决策 / 信号融合集成
  - apizero 限流场景下正确降级到 Open-Meteo，因子计算功能完整

### v8.6.12 详细变更

- **P0 启动自检系统**（`utils/system_check.py` + `scripts/run_p0_startup_check.py`）
  - 8 大类 40 项检查：文件存在性、环境变量、数据源连通性、配置 Schema、Python 依赖、目录权限、子模块 Smoke、历史数据完整性
  - 严格模式（`--strict`）：WARN FAIL 也算阻止性失败，盘前最终核查
  - 报告归档（`--archive`）：保留最近 30 份基线
- **数据契约测试**（`tests/test_data_contracts.py`）
  - positions.json / trade_plan_*.json / hedge_execution_fill_*.json Schema 校验
  - 字段类型约束 + 值范围约束 + 嵌套结构验证
- **回归测试套件**（`tests/test_regression_bugfixes.py`）
  - Wind MCP 路径查找、SSE 解析、heartbeat 字段名兼容、positions.json schema、sys.path 注入等已修复 bug
  - `@pytest.mark.integration` 标记慢测（TDX 连接验证 ~7s）
- **两层测试策略**
  - 快测层（<1s）：契约 + 单元回归（排除 integration 标记）
  - 慢测层（~10s）：integration 标记的回归测试
  - CI 分离：Unit Tests Job 跑快测，Integration Tests Job 跑慢测
- **Pre-commit 钩子**（`githooks/pre-commit` + `scripts/pre_commit_check.py`）
  - Git 提交前自动执行 P0 启动自检（`--skip-datasource` 加速）
  - 智能跳过：仅文档变更 / SKIP_P0_CHECK=1 / 非 git 仓库
- **本地测试脚本**（`scripts/run_tests.ps1`）
  - 5 种模式：fast / slow / contract / regression / all

详见 [CHANGELOG.md](CHANGELOG.md)。

---

## License

私有项目，未授权不得使用。

**作者**：yuppiez99999
**版本**：v8.6.14
**更新日期**：2026-08-02
