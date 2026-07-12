# 综合量化策略系统 v7.9

**顶级对冲基金视角 | 300万股票ETF + 200万对冲账户 | 自动执行 | 2030年清仓 | 年化≥8% 回撤<15% | ETF资金流追踪 | AI增强预测 | WonderTrader高价值模块集成 | Wind MCP 优先数据源 | 完全自动化交易流程**

**作者**：yuppiez99999

---

## 系统概述

综合量化策略系统 v7.9 是一个专业量化交易平台，在 v7.8 WonderTrader 高价值模块集成基础上新增 **完全自动化交易流程**，实现从盘前计划生成、自动确认到盘后执行的无人化闭环。系统以 **500 万元人民币** 为基础管理规模，分为 **股票ETF账户 300万** 与 **对冲保护账户 200万**，目标年化收益 ≥ 8%，最大回撤控制在 15% 以内，**2030-12-31 全部清仓**。

**v7.9 核心升级（完全自动化交易流程）**：
- **自动确认引擎**：`daily_trade_executor.py --auto-confirm` — 一键跳过人工确认，自动将当日交易计划标记为已确认，支持 `pre-market` 和 `post-market-auto` 模式
- **一个月建仓方案**：每日固定 20 万预算，22 个交易日完成 300 万建仓，消除人工确认瓶颈
- **完全闭环执行**：盘前 07:00 自动生成计划 → 自动确认 → 盘后 15:30 自动执行 → 自动生成下一交易日计划

**v7.8 核心能力保留（WonderTrader 高价值模块集成）**：
- **统一数据结构**：`utils/wt_structs.py` — Tick/Bar/Order/Trade/Position/Contract 标准化数据模型
- **合约规格管理器**：`utils/wt_contracts_manager.py` — 全市场合约规格(股票/ETF/期货/期权)统一管理，替代硬编码
- **价差策略框架**：`utils/wt_spread_strategy.py` — ETF配对交易/跨期套利/价差回归策略
- **组合对冲策略模板**：`utils/wt_hedge_strategy.py` — Beta对冲/尾部风险保护/动态对冲三大策略框架
- **Tick级事件驱动回测引擎**：`utils/wt_tick_engine.py` — Tick/Bar双模式事件驱动回测，支持精确撮合
- **执行算法**：`utils/wt_execution_algo.py` — MinImpact/TWAP/VWAP大单拆分算法
- **风控模块**：`utils/wt_risk_control.py` — 多层次风控（组合资金/通道流量/止损止盈）
- **回测引擎**：`utils/wt_backtest_engine.py` — 轻量级回测引擎，支持ETF信号策略

**v7.7 核心能力保留（AI 增强 + 多源数据 + 预测信号）**：
- **价格预测模块**：`utils/tf_price_predictor.py` — TimesFM 零样本预测 + TensorFlow LSTM + ARIMA 三级降级，支持 T+1/T+5/T+10 预测
- **外部数据源模块**：`utils/external_data_source.py` — 整合 FRED/Econdb/美国财政部/AlphaVantage/Finnhub/CoinGecko 六大免费 API
- **网页抓取模块**：`utils/web_scraper.py` — 基于 Scrapling/BeautifulSoup 抓取东方财富公告/研报、巨潮资讯、新浪财经新闻
- **AI 报告代理**：`utils/ai_report_agent.py` — 复用 `15_每日工作流/llm_client.py` 三级降级链（豆包→DeepSeek→Ollama），自动情感分析、每日报告、信号解读
- **建仓流程 AI 集成**：`daily_trade_executor.py` 在分配金额时根据预测信号动态调整（强看多 +30%、强看空跳过）
- **统一数据接口**：`data_provider.py` 新增 6 个集成方法，统一暴露预测/宏观/情感/AI 报告能力

**v7.6 核心能力保留（仓位重建 + 资金流整合）**：
- **仓位重建**：清除原 23 个旧仓位，重建为 20 标的新计划
- **资金流整合**：将 2026-07-09 ETF 资金流向报告信号直接写入持仓计划（证券ETF 67亿、科创50ETF 57亿、上证50ETF 40亿、银行ETF 23亿、新能源车ETF 11亿、半导体ETF 9亿、医疗ETF 3亿）
- **双账户结构**：股票ETF账户 300万（进攻）+ 对冲账户 200万（保护）
- **十五年五规划适配**：健康中国权重上调（恒瑞 3%→6%、医疗ETF 4%→10%），新质生产力降权（29%→20%）
- **固定日预算**：2026-07-13 起每个交易日固定 20 万元建仓，预计 14 个交易日完成全部建仓
- **高价股保护**：100 股成本超过当日预算 50% 时自动跳过，避免单标的占用过多预算

**v7.5 核心能力保留**：
- **风险预算**：Risk Parity + Improved Kelly Criterion + 三级回撤防御
- **三联对冲**：Beta/Vol/Correlation 三类对冲实时联动
- **智能执行**：Iceberg + TWAP/VWAP/POV + 滑点熔断 + NTP 时间同步
- **回测严谨性**：Walk-Forward + 三段极端行情压力测试
- **Qlib 信号集成**：本地 LightGBM 深度学习信号生成，自动注入 SignalFusion

---

## 账户结构与资金配置

### 总资金：500 万元

| 账户 | 金额 | 比例 | 用途 |
|------|------|------|------|
| **股票ETF账户** | ¥3,000,000 | 60% | 20 标的建仓 + 动态再平衡 |
| **对冲保护账户** | ¥2,000,000 | 40% | IF/IM 期货空头 + ETF 认沽期权 |
| **合计** | ¥5,000,000 | 100% | — |

### 股票ETF账户（300万）标的配置

| 标的 | 代码 | 类型 | 目标权重 | 计划金额 | 风格 | ETF资金流信号 |
|------|------|------|----------|----------|------|---------------|
| 科创50ETF易方达 | 588080 | ETF | 5% | ¥150,000 | 科技 | 强加仓 57亿 |
| 证券ETF国泰 | 512880 | ETF | 5% | ¥150,000 | 金融 | 强加仓 67亿 |
| 上证50ETF华夏 | 510050 | ETF | 6% | ¥180,000 | 宽基 | 加仓 40亿 |
| 银行ETF华宝 | 512800 | ETF | 6% | ¥180,000 | 金融 | 加仓 23亿 |
| 新能源车ETF华夏 | 515030 | ETF | 5% | ¥150,000 | 新能源 | 加仓 11亿 |
| 半导体ETF国泰 | 512760 | ETF | 3% | ¥90,000 | 科技 | 关注 9亿 |
| 医疗ETF华宝 | 512170 | ETF | 10% | ¥300,000 | 医药 | 关注 3亿 |
| 黄金ETF华安 | 518880 | ETF | 5% | ¥150,000 | 资源 | 流入 0.17亿 |
| 海光信息 | 688041 | 个股 | 4% | ¥120,000 | 科技 | 关联科创50 |
| 中际旭创 | 300308 | 个股 | 4% | ¥120,000 | 科技 | 关联半导体 |
| 北方华创 | 002371 | 个股 | 4% | ¥120,000 | 科技 | 关联半导体 |
| 中科曙光 | 603019 | 个股 | 2% | ¥60,000 | 科技 | 关联科创50 |
| 同花顺 | 300033 | 个股 | 4% | ¥120,000 | 科技 | 关联证券 |
| 卓胜微 | 300782 | 个股 | 2% | ¥60,000 | 科技 | 关联半导体 |
| 绿的谐波 | 688017 | 个股 | 3% | ¥90,000 | 制造 | — |
| 阳光电源 | 300274 | 个股 | 4% | ¥120,000 | 新能源 | 关联新能源车 |
| 藏格矿业 | 000408 | 个股 | 4% | ¥120,000 | 资源 | — |
| 中国神华 | 601088 | 个股 | 3% | ¥90,000 | 顺周期 | — |
| 恒瑞医药 | 600276 | 个股 | 6% | ¥180,000 | 医药 | 关联医疗 |
| 长江电力 | 600900 | 个股 | 4% | ¥120,000 | 防御 | — |

### 对冲保护账户（200万）配置

| 工具 | 标的 | 方向 | 目标合约 | 保证金率 | 目的 |
|------|------|------|----------|----------|------|
| IF 股指期货 | 沪深300 | 卖出 | 2 手 | 12% | 系统性 Beta 对冲 |
| IM 股指期货 | 中证1000 | 卖出 | 1 手 | 14% | 中小盘波动对冲 |
| ETF 认沽期权 | 510050 Put | 买入 | 10 张 | — | 尾部风险保护 |

---

## 投资目标与清仓计划

| 指标 | 目标 |
|------|------|
| 总管理规模 | ¥5,000,000 |
| 目标年化收益 | **≥ 8%** |
| 最大回撤控制 | **< 15%** |
| 建仓开始 | 2026-07-10 |
| 强制清仓日期 | **2030-12-31** |
| 数据源 | Wind MCP > iFinD MCP > AKShare > 新浪 HTTP |
| 自动执行 | 盘前 07:00 / 盘后 15:30 Windows 任务 |

---

## 目录结构

```
28-终极量化交易系统7.1/
├── README.md                        # 本文件
├── PROJECT_DOCUMENTATION.md         # 项目文档
├── daily_trade_executor.py          # ★ 每日建仓执行器 (v7.7 集成预测信号)
├── daily_hedge_update.py            # 每日对冲更新
├── hedge_execution_orders.py        # 对冲执行订单
├── hedge_quantity_calculator.py     # 对冲数量计算器
├── config/
│   ├── positions.json               # ★ 实时持仓状态（已清空重建）
│   ├── stop_loss_vol_adjusted.yaml  # 止损规则
│   └── market_returns.json          # 市场收益数据
├── configs/
│   ├── portfolio.yaml               # 组合配置
│   ├── settings.yaml                # 系统全局配置
│   └── institutional_config.yaml    # 机构配置
├── v7.5_institutional/
│   ├── README.md                    # v7.5 模块说明
│   ├── main.py                      # 主入口
│   ├── reports/                     # 收盘盈亏报告 (JSON + MD)
│   └── run_daily.bat                # 日常运行脚本
├── utils/                           # ★ 核心分析模块
│   ├── data_provider.py             # ★ 统一数据接口 (v7.7 新增 6 个集成方法)
│   ├── tf_price_predictor.py        # ★ v7.7 价格预测 (TimesFM+TF LSTM+ARIMA)
│   ├── external_data_source.py      # ★ v7.7 外部数据源 (FRED+Finnhub+CoinGecko)
│   ├── web_scraper.py               # ★ v7.7 网页抓取 (东方财富+巨潮+新浪)
│   ├── ai_report_agent.py           # ★ v7.7 AI 报告代理 (豆包→DeepSeek→Ollama)
│   ├── etf_flow_monitor.py          # ★ ETF资金流实时监控 (v7.8 集成WT)
│   ├── wt_structs.py                # ★ 统一数据结构 (Tick/Bar/Order/Trade/Position/Contract)
│   ├── wt_contracts_manager.py      # ★ 合约规格管理器 (A股+股指期货+ETF)
│   ├── wt_spread_strategy.py        # ★ 价差策略框架 (ETF配对/跨期套利/价差回归)
│   ├── wt_hedge_strategy.py         # ★ 组合对冲策略模板 (Beta/尾部风险/动态)
│   ├── wt_tick_engine.py            # ★ Tick级事件驱动回测引擎 (精确撮合)
│   ├── wt_execution_algo.py         # ★ 执行算法 (MinImpact/TWAP/VWAP)
│   ├── wt_risk_control.py           # ★ 多层次风控 (组合资金/通道流量/止损止盈)
│   ├── wt_backtest_engine.py        # ★ 轻量级回测引擎 (ETF信号策略)
│   ├── akshare_futures.py           # 期货数据
│   ├── ifind_client.py              # iFinD 接口
│   ├── ifind_news_analyzer.py       # iFinD 新闻分析
│   ├── factor_model.py              # 因子模型
│   ├── gtja191_factors.py           # 国泰君安 191 因子
│   ├── enhanced_backtest.py         # 增强回测引擎
│   ├── risk_metrics.py              # 风险指标
│   ├── stop_loss.py                 # 止损逻辑
│   ├── liquidity_risk.py            # 流动性风险
│   ├── qlib_adapter.py              # Qlib 适配器
│   ├── qlib_data_bridge.py          # Qlib 数据桥
│   ├── real_economy_indicator.py     # 实体经济指标
│   ├── trade_calendar.py            # 交易日历
│   ├── data_types.py                # 数据类型
│   └── logger.py                    # 统一日志
├── trade_instructions/              # ★ 每日交易指令目录
│   ├── YYYY-MM-DD_instructions.json # 盘前生成的指令
│   ├── YYYY-MM-DD_instructions.md   # 指令 Markdown 表格
│   ├── YYYY-MM-DD_execution.json    # 盘后执行报告
│   └── build_progress.json          # 建仓进度追踪
├── 每日报告归档/YYYY-MM-DD/         # 每日报告输出
└── reports/                         # 汇总报告
```

---

## 快速开始

### 1. 系统自检

```bash
# 检查系统健康
python check_system_health.py

# 检查核心模块
python test_core_modules.py
```

### 2. 持仓更新

```bash
# 更新持仓价格
python update_position_prices.py
```

### 3. 每日报告

```bash
# 生成每日盈亏报告
python generate_daily_report.py
```

### 4. 自动执行任务

```powershell
# 注册 Windows 任务计划
.\register_all_scheduled_tasks.ps1

# 测试盘前任务
.\register_all_scheduled_tasks.ps1 -Test pre

# 测试盘后任务
.\register_all_scheduled_tasks.ps1 -Test post
```

### 5. 查看持仓

```bash
# 查看当前持仓
python inspect_data.py
```

### 6. AI 增强模块（v7.7 新增）

```bash
# 各模块自检
python -m utils.tf_price_predictor        # 价格预测自检
python -m utils.external_data_source      # 外部数据源自检
python -m utils.web_scraper               # 网页抓取自检
python -m utils.ai_report_agent           # AI 报告代理自检

# 盘前生成指令（含预测信号调整）
python daily_trade_executor.py pre-market --date 2026-07-13

# 盘前生成指令并自动确认（跳过人工确认）
python daily_trade_executor.py pre-market --date 2026-07-13 --auto-confirm

# 盘后执行已确认指令
python daily_trade_executor.py post-market --date 2026-07-13

# 收盘后自动执行 + 自动确认 + 生成下一交易日计划（推荐）
python daily_trade_executor.py post-market-auto --date 2026-07-13 --auto-confirm

# 查看建仓进度
python daily_trade_executor.py progress
```

### 7. WonderTrader 高价值模块（v7.8 新增）

```bash
# 合约管理器自检
python -m utils.wt_contracts_manager

# 价差策略回测
python -m utils.wt_spread_strategy

# 对冲策略模拟
python -m utils.wt_hedge_strategy

# Tick级回测
python -m utils.wt_tick_engine

# ETF资金流监控
python -m utils.etf_flow_monitor
```

### 8. Python 调用示例

```python
# ========== AI 增强模块 ==========

# 价格预测
from utils.tf_price_predictor import PricePredictor
predictor = PricePredictor()
result = predictor.predict("002371", prices, horizon=5)
print(f"方向: {result.direction}, 目标价: {result.target_price}")

# 外部宏观数据
from utils.external_data_source import ExternalDataManager
mgr = ExternalDataManager()
macro = mgr.get_macro_snapshot()  # FRED CPI/PPI/GDP
sentiment = mgr.get_risk_sentiment()  # 加密货币/国债/VIX代理

# 网页抓取
from utils.web_scraper import WebScraper
scraper = WebScraper()
announcements = scraper.fetch_announcements("002371")  # 公告
reports = scraper.fetch_research_reports("688041")     # 研报
news = scraper.fetch_news("半导体")                    # 新闻

# AI 报告代理
from utils.ai_report_agent import AIReportAgent
agent = AIReportAgent()
sentiments = agent.analyze_news_sentiment(news_items)  # 情感分析
report = agent.generate_daily_report(symbols=["002371", "688041"])  # 每日报告
explanation = agent.explain_trade_signals(predictions)  # 信号解读

# 统一接口 (推荐)
from utils.data_provider import (
    get_price_prediction,
    get_external_macro,
    get_risk_sentiment,
    get_news_sentiment,
    get_ai_daily_report,
)
pred = get_price_prediction("002371", horizon=5)
macro = get_external_macro()

# ========== WonderTrader 高价值模块 ==========

# 合约管理器
from utils.wt_contracts_manager import get_contracts_manager
contracts = get_contracts_manager()
if_contract = contracts.get_contract("IF.CFFEX")
print(f"IF合约乘数: {if_contract.contract_multiplier}")
print(f"IF保证金率: {if_contract.margin_rate}")

# 价差策略
from utils.wt_spread_strategy import SpreadDefinition, SpreadCalculator, SpreadBacktester
spread = SpreadDefinition(
    name="SPD.300-50",
    legs=[
        {"code": "510300.SH", "ratio": 1.0, "direction": "BUY"},
        {"code": "510050.SH", "ratio": 1.0, "direction": "SELL"},
    ],
    spread_type="diff",
    description="沪深300ETF - 上证50ETF 价差",
)
prices = {"510300.SH": 4.20, "510050.SH": 2.65}
spread_price = SpreadCalculator.calc_spread_price(spread, prices)
print(f"价差价格: {spread_price}")

# 对冲策略
from utils.wt_hedge_strategy import HedgeContext, BetaHedgeStrategy, TailRiskHedgeStrategy
beta_hedge = BetaHedgeStrategy(config={"target_hedge_ratio": 0.2, "max_hedge_ratio": 0.5})
ctx = HedgeContext(beta_hedge)
metrics = ctx.get_portfolio_metrics(prices={"IF.CFFEX": 3200.0})
result = ctx.adjust_hedge(target_ratio=0.3)

# Tick级回测引擎
from utils.wt_tick_engine import TickBacktestEngine, run_tick_backtest
engine = TickBacktestEngine(initial_capital=1000000.0)
engine.add_strategy("MyStrategy", my_strategy_instance)
engine.run_backtest(ticks)

# 执行算法
from utils.wt_execution_algo import MinImpactExecutor, TWAPExecutor, VWAPExecutor
executor = MinImpactExecutor(max_participation_pct=0.15, min_order_size=100)
splits = executor.calculate_optimal_splits(target_amount=200000.0, ref_price=4.20, avg_daily_volume=1000000)
print(f"拆分为 {len(splits)} 笔")

# 风控模块
from utils.wt_risk_control import RiskControl, StopLossManager, PortfolioRiskAnalyzer
risk_ctrl = RiskControl({"max_daily_loss_pct": 0.05, "max_portfolio_drawdown_pct": 0.15})
stop_loss = StopLossManager(stop_loss_pct=0.08, take_profit_pct=0.15)
risk_analyzer = PortfolioRiskAnalyzer()

# ETF资金流监控
from utils.etf_flow_monitor import ETFRealTimeTracker, refresh_etf_flow_signals
tracker = ETFRealTimeTracker()
summary = tracker.fetch_all_etf_fund_flow()
refresh_etf_flow_signals()  # 更新 positions.json 中的信号
```

---

## WonderTrader 高价值模块详解

### 模块架构

| 模块 | 文件 | 用途 | 参考 WT 组件 |
|------|------|------|-------------|
| 统一数据结构 | `wt_structs.py` | Tick/Bar/Order/Trade/Position/Contract | WTSTructs |
| 合约管理器 | `wt_contracts_manager.py` | 全市场合约规格统一管理 | ContractsManager |
| 价差策略框架 | `wt_spread_strategy.py` | ETF配对/跨期套利/价差回归 | SpreadStrategy + SpreadContext |
| 组合对冲策略 | `wt_hedge_strategy.py` | Beta/尾部风险/动态对冲 | HedgeStrategy + HedgeContext |
| Tick级回测引擎 | `wt_tick_engine.py` | 事件驱动回测 + 精确撮合 | WtBtEngine |
| 执行算法 | `wt_execution_algo.py` | MinImpact/TWAP/VWAP大单拆分 | OrderExecutor |
| 风控模块 | `wt_risk_control.py` | 多层次风控 | RiskControl |
| 回测引擎 | `wt_backtest_engine.py` | ETF信号策略回测 | 轻量回测框架 |

### 核心数据结构

```python
# TickData: 逐笔成交数据
TickData(code, time, price, volume, bid1~bid5, ask1~ask5)

# BarData: K线数据
BarData(code, time, open, high, low, close, volume, turnover)

# OrderData: 订单数据
OrderData(order_id, code, direction, order_type, price, volume, filled_volume)

# TradeData: 成交数据
TradeData(trade_id, order_id, code, price, volume, time)

# PositionData: 持仓数据
PositionData(code, direction, volume, avg_price, last_price)

# ContractData: 合约数据
ContractData(code, exchange, name, contract_multiplier, price_tick, margin_rate)
```

### 价差策略示例

支持三种价差类型：
- **ratio**：比率价差（如 1×IF - 0.7×IC）
- **diff**：差值价差（如 510300 - 510050）
- **weighted**：加权价差（按市值权重）

内置 ETF 配对模板：
- `SPD.300-50`：沪深300ETF vs 上证50ETF
- `SPD.300-500`：沪深300ETF vs 中证500ETF
- `SPD.SCI-TECH`：科创50ETF vs 半导体ETF

### 对冲策略框架

| 策略类型 | 适用场景 | 参数 |
|----------|----------|------|
| BetaHedgeStrategy | 系统性风险对冲 | config={"target_hedge_ratio": 0.2, "max_hedge_ratio": 0.5} |
| TailRiskHedgeStrategy | 尾部风险保护 | config={"target_hedge_ratio": 0.3, "vol_threshold": 0.2} |
| DynamicHedgeStrategy | 动态调整对冲 | config={"target_hedge_ratio": 0.3} |

---

## 宏观战略框架

### 十五五规划对齐

| 十五五方向 | 对应标的 | 角色 |
|-----------|----------|------|
| AI算力基础设施 | 海光信息、中际旭创、中科曙光、科创50ETF | 算力基建核心仓 |
| 高端装备制造 | 北方华创、绿的谐波、铜期货、铝期货 | 设备+精密制造 |
| 半导体与集成电路 | 北方华创、中际旭创、卓胜微、半导体ETF | 国产替代主线 |
| 新能源与储能 | 阳光电源、新能源车ETF、碳酸锂期货 | 双碳+储能 |
| 创新药与高端医疗器械 | 恒瑞医药、医疗ETF | 健康中国旗舰 |
| 战略资源与安全 | 藏格矿业、黄金ETF、黄金期货 | 资源安全+避险 |
| 防御型公用事业 | 长江电力、中国神华、银行ETF、上证50ETF | 高股息底仓 |

### 康波周期配置

| 康波阶段 | 时间 | 配置逻辑 | 权重方向 |
|----------|------|----------|----------|
| 第六轮康波复苏→繁荣转换 | 2025-2030 | 高端制造/科技/资源进攻，防御底仓压舱 | 科技35% + 资源10% + 防御20% + 医药10% + 宽基15% + 新能源10% |

- **复苏/繁荣转换期核心逻辑**：新技术革命资产（AI算力、半导体、机器人）优先；资源品（铜、铝、锂、黄金）进入主动多配；传统顺周期低配。
- **组合映射**：`macro_policy_scoring.combined_score >= 1.15` 时加码 20%，`0.85~1.0` 正常配置，`< 0.85` 缩减 20% 或跳过。

### 期货期权增强（十五五+康波）

| 工具 | 标的 | 方向 | 目的 | 框架对齐 |
|------|------|------|------|----------|
| 铜期货 | CU | 多头 | 十五五高端制造/AI算力电力基建 | 康波繁荣期资源主升浪 |
| 铝期货 | AL | 多头 | 十五五高端制造/轻量化+电力传输 | 新兴制造需求扩张 |
| 碳酸锂期货 | LC | 多头 | 十五五新能源与储能战略资源 | 新能源产业链上游 |
| 黄金期货 | AU | 多头 | 十五五战略资源安全 | 康波萧条末期避险最优 |
| 科创50ETF认沽期权 | 588080 Put | 买入 | 科技成长尾部保护 | 十五五AI算力+半导体 |
| 创业板ETF认沽期权 | 159915 Put | 买入 | 成长风格尾部保护 | 十五五创新生态 |

---

## 风控规则

### 个股止损

| 标的类型 | 止损线 | 触发操作 |
|----------|--------|----------|
| 宽基 ETF | -8% | 减半仓，观察 3 日 |
| 科技成长股 | -10% ~ -15% | 清仓，重新评估 |
| 防御/红利股 | -8% | 减半仓 |
| 黄金 ETF | -8% 减半仓 / -12% 清仓 | 避免误触发 |

### 组合回撤控制

| 回撤幅度 | 操作 |
|----------|------|
| -5% | 预警：检查持仓，准备加仓机会 |
| -8% | 减仓：权益仓位降至 70%，现金提至 30% |
| -10% | 警戒：权益仓位降至 50%，现金提至 50% |
| -15% | 清仓：全部止损，保留现金 |

### WT 风控模块规则

| 规则类型 | 阈值 | 操作 |
|----------|------|------|
| 单笔交易上限 | 总资产 5% | 熔断，拒绝下单 |
| 单标的持仓上限 | 总资产 10% | 禁止加仓 |
| 行业集中度上限 | 总资产 30% | 禁止该行业新单 |
| 日成交额度 | 日预算 | 当日停止交易 |
| 滑点熔断 | 0.5% | 订单重新拆分 |

---

## 环境要求

### 硬件
- CPU: 8 核心以上
- 内存: 16GB 以上
- 硬盘: 500GB 以上可用空间
- 网络: 稳定的宽带连接

### 软件
- **Python**: 3.8+ (兼容至 3.14)
- **操作系统**: Windows 10+ / Ubuntu 22.04+ / macOS 13+

### 核心依赖

```bash
# 基础 (必需)
pip install numpy pandas scipy scikit-learn pyyaml requests beautifulsoup4

# AI 增强 (可选, 缺失时自动降级)
pip install tensorflow          # LSTM 价格预测
pip install timesfm[torch]     # TimesFM 零样本预测 (200M 参数)
pip install scrapling[all]     # 反爬增强 (Cloudflare 绕过)
pip install statsmodels        # ARIMA 统计预测

# 回测增强 (可选)
pip install numba              # Tick级回测加速
pip install plotly             # 可视化
```

### AI 模型降级链

所有 v7.7 新增模块均支持**优雅降级**, 依赖缺失时自动回退, 不影响主流程:

| 模块 | P0 (最优) | P1 (回退) | P2 (兜底) |
|------|-----------|-----------|-----------|
| 价格预测 | TimesFM (200M 参数) | TensorFlow LSTM | ARIMA / 移动平均 |
| 网页抓取 | Scrapling (反爬) | requests + BeautifulSoup | 静默降级 |
| AI 报告 | 豆包 Speed (Ark) | DeepSeek | Ollama 本地 / 规则引擎 |
| 外部数据 | FRED API | Econdb / Treasury | 静默降级 |

---

## 数据源优先级

| 优先级 | 数据源 | 用途 |
|--------|--------|------|
| P0 | Wind 数据终端 | 主数据源 (WindPy) |
| P1 | Wind MCP | 强制回退 (analytics_data/stock_data/fund_data) |
| P2 | iFinD MCP | 强制回退 (同花顺金融终端) |
| P3 | AKShare | 免费回退 |
| P4 | 新浪财经 API | 免费兜底 |
| P5 | 本地缓存 | Parquet/JSON |
| P6 | 兜底预定义价格 | 保证永不崩溃 |
| **P7** | **外部 API (v7.7)** | **FRED/Econdb/Finnhub/CoinGecko (海外宏观+全球股票+加密货币)** |
| **P8** | **网页抓取 (v7.7)** | **东方财富/巨潮资讯/新浪财经 (公告+研报+新闻)** |

---

## 版本历史

| 版本 | 日期 | 主要变更 |
|------|------|----------|
| v7.10 | 2026-07-12 | **十五五+康波宏观对齐**：新增宏观战略框架章节；股票/ETF/期货/期权全面标注十五五与康波对齐说明；`daily_trade_executor.py` 接入 `macro_policy_scoring` 动态调整分配（强对齐+20%、偏弱-20%或跳过）；`hedge_execution_orders.py` 支持读取 `hedge_positions` 生成期货/期权执行单；`config/positions.json` 新增 CU/AL/LC/AU 期货及 588080/159915 ETF 认沽期权 |
| v7.9 | 2026-07-12 | **完全自动化交易流程**：新增 `--auto-confirm` 自动确认引擎；一个月建仓方案（每日固定 20 万预算，22 个交易日完成）；盘前自动生成 → 自动确认 → 盘后自动执行 → 自动生成下一日计划的无人化闭环 |
| v7.8 | 2026-07-11 | **WonderTrader 高价值模块集成**：新增 8 个 WT 风格模块（统一数据结构/合约管理器/价差策略/组合对冲/Tick级回测/执行算法/风控/回测引擎）；ETF资金流监控集成WT数据结构；更新 `utils/__init__.py` 导出全部WT模块 |
| v7.7 | 2026-07-10 | **AI 增强**：新增 4 个模块（价格预测/外部数据源/网页抓取/AI 报告代理）；建仓流程集成预测信号调整分配（强看多 +30%、强看空跳过）；固定日预算 20万/交易日；data_provider 新增 6 个集成方法 |
| v7.6 | 2026-07-09 | 仓位重建：清除旧仓位，重建 20 标的新计划；双账户结构（300万股票ETF + 200万对冲）；整合 2026-07-09 ETF 资金流向报告；2030-12-31 强制清仓目标 |
| v7.5.3 | 2026-07-08 | 7/8 建仓执行完成；修复 daily_workflow.py 中 ntp 属性缺失问题；新增宏观模块目录 |
| v7.5.2 | 2026-07-07 | 顶级对冲基金视角优化：组合权重重构、对冲资本提升至 30%、净 Beta 降至 0.1 |
| v7.5.1 | 2026-07-05 | AI 算力期货观察池：新增锡/铜/铝/银/碳酸锂/多晶硅 6 个核心品种 |
| v7.5 | 2026-07-05 | 机构级实盘：Risk Parity + 三联对冲 + SOR + Walk-Forward + 全模块自动调度 |
| v7.4 | 2026-07-04 | 个股分档建仓执行：基于 PDF 研报数字化建仓流程 |
| v7.3 | 2026-07-04 | 因果验证与稳健性检验：DID/安慰剂/RDD 三大因果验证工具 |
| v7.2 | 2026-07-04 | 黑天鹅极端行情防护：修复 6 项关键不足 |
| v7.1.2 | 2026-07-04 | 第三方整合 v1.0：时段决策护栏、大盘环境护栏、多通道预警服务、语义回测引擎 |
| v7.1.1 | 2026-07-03 | 极端情景应对 v2.1：新增 2000 互联网泡沫压力测试场景 |
| v7.1 | 2026-07-03 | 多维度整合 v3.0：实体经济指标、流动性风控、止损监控、五维因子 |
| v7.0 | 2026-07-03 | 期货+期权双层对冲、保护性看跌阶梯、市场状态自适应 |

---

## 风险提示

本系统所有数学模型与参数均为研究建议，非实盘配置。实盘部署前必须通过 Risk Committee 三审。Walk-Forward 与压力测试结果不可作为未来收益保证。Kelly 公式与 Risk Parity 在极端尾部行情下可能失效，必须配合三联对冲与熔断机制使用。2030 年清仓为计划目标，实际执行可能因市场条件调整。

WonderTrader 模块为纯 Python 实现，未使用 wtpy C++ 核心库。实际性能可能与原版 C++ 实现存在差异，高频交易场景需谨慎评估。

---

**作者**: yuppiez99999
**日期**: 2026-07-12
**版本**: v7.9-auto-confirm