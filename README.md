# 综合量化策略系统 v7.7

**顶级对冲基金视角 | 300万股票ETF + 200万对冲账户 | 自动执行 | 2030年清仓 | 年化≥8% 回撤<15% | ETF资金流追踪 | AI增强预测 | Wind MCP 优先数据源**

**作者**：yuppiez99999

---

## 系统概述

综合量化策略系统 v7.7 是一个专业量化交易平台，在 v7.5 机构级实盘基础上完成仓位重建与 AI 增强：清除旧仓位，重新设计 20 标的自动执行计划，并集成基于 TensorFlow/TimesFM 的价格预测、外部宏观经济数据源、网页舆情抓取、AI 自动化报告四大新模块。系统以 **500 万元人民币** 为基础管理规模，分为 **股票ETF账户 300万** 与 **对冲保护账户 200万**，目标年化收益 ≥ 8%，最大回撤控制在 15% 以内，**2030-12-31 全部清仓**。

**v7.7 核心升级（AI 增强 + 多源数据 + 预测信号）**：
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
├── utils/                           # ★ 核心分析模块 (v7.7 新增 4 个 AI 模块)
│   ├── data_provider.py             # ★ 统一数据接口 (v7.7 新增 6 个集成方法)
│   ├── tf_price_predictor.py        # ★ v7.7 价格预测 (TimesFM+TF LSTM+ARIMA)
│   ├── external_data_source.py      # ★ v7.7 外部数据源 (FRED+Finnhub+CoinGecko)
│   ├── web_scraper.py               # ★ v7.7 网页抓取 (东方财富+巨潮+新浪)
│   ├── ai_report_agent.py           # ★ v7.7 AI 报告代理 (豆包→DeepSeek→Ollama)
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

# 盘后执行已确认指令
python daily_trade_executor.py post-market --date 2026-07-13

# 查看建仓进度
python daily_trade_executor.py progress
```

### 7. Python 调用示例

```python
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
```

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

---

**作者**: yuppiez99999
**日期**: 2026-07-10
**版本**: v7.7-ai-enhanced-prediction
