# 更新日志 (Changelog)

所有重要的项目变更都会记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
并且本项目遵循 [语义化版本](https://semver.org/spec/v2.0.0.html)。

## [8.6.13] - 2026-08-01

### 新增 (Added) — 气象因子引擎 + 第三方项目集成

#### 气象因子引擎（7 因子体系）

- **气象数据适配器** (`utils/weather_data_adapter.py`) — apizero.cn 商业 API → Open-Meteo 免费降级链
  - 429 限流自动熔断 10 分钟，期间直接降级到 Open-Meteo，避免无效重试
  - 双层缓存：apizero 与 Open-Meteo 结果按坐标各缓存 10 分钟，显著减少重复请求
  - 提供实时天气 / 小时预报 / 天预报 / 分钟级降水 / 预警 5 类数据接口
- **因子计算引擎** (`utils/weather_factor_engine.py`) — 7 因子体系
  - 温度 / 降水 / 风速 / 辐照度 / 气压 / 空气质量 / 能见度
  - 按行业敏感度加权（电力 1.5x / 矿业 1.4x / 农业 1.5x / 冶炼 1.2x / 医药 1.0x）
  - 输出 STRONG_BULL (+2.0) ~ STRONG_BEAR (-2.0) 五级信号 + 置信度
- **标的映射配置** (`config/weather_symbols_mapping.yaml`) — 14 股票标的 + ETF/期货
  - 长江电力（三峡大坝 0.4 / 葛洲坝 0.3 / 向家坝 0.3，敏感度 0.95）
  - 中国神华（神东矿区 / 准格尔矿区，敏感度 0.85）
  - 宁德时代（宜宾锂矿 / 宁德总部，敏感度 0.70）
  - 恒瑞医药（连云港总部，敏感度 0.60）等
- **WeatherAgent** (`utils/finance_agents/weather_agent.py`) — 第 6 位专家
  - 在 `FinanceAgentOrchestrator` 中以 11% 权重参与多 Agent 决策
  - 权重分配：估值 22% / 动量 22% / 风险 22% / 情绪 13% / 宏观 10% / 气象 11%
  - 支持 `WeatherFactorResult` dataclass + dict 双格式输入
  - 降级到中性决策保证可用性
- **信号融合第 9 层** — 通过 `PostMixLayer` 注入 `SignalFusionEngine`
  - 与其他 8 类信号源（ML/AI Hedge Fund/GLM5/康波周期等）动态加权叠加

#### 第三方项目集成

- **Scrapling 反爬爬虫适配器** (`utils/scrapling_adapter.py`)
  - 封装 StealthyFetcher / PlayWrightFetcher，提供企业级反爬能力
  - 集成到 `news_sentiment_engine.py` 的 `fetch_and_ingest_news` / `fetch_and_ingest_announcements`
  - 降级到 `WebScraper`（requests + bs4）保证可用性
- **TradingAgents-CN HTTP 桥接** (`utils/tradingagents_bridge.py`)
  - 通过 HTTP 微服务方式集成 TradingAgents-CN 多 Agent 协作框架
  - 避免 Python 3.10+ 版本冲突（28 系统基线 Python 3.8.9）
  - 健康检查 + 结果转换 + 降级链逻辑

### 验证 (Verified)

- **E2E 测试** (`scripts/test_weather_e2e_minimal.py`) — 17/17 通过
  - 模块导入：WeatherDataAdapter / WeatherFactorEngine / WeatherAgent 全部 OK
  - 数据适配器降级链：apizero 限流 → Open-Meteo 正确切换
  - 因子计算：3 个代表性标的（长江电力 +0.23 / 中国神华 +0.41 / 宁德时代 +0.16）
  - WeatherAgent 决策：返回包含 composite_score 的标准化 AgentDecision
  - 信号融合：`_weather_layer` 正确初始化
- **实测气象数据**（Open-Meteo 降级模式）
  - 北京：温度 35.8°C / 湿度 48% / 风 10.2km/h / 云量 0.67%
  - 长江电力 composite +0.23（降水充沛 326mm 利好水电蓄水）

### 变更 (Changed)

- `utils/finance_agent_orchestrator.py` — `DEFAULT_WEIGHTS` 新增 "weather" 11% 权重，`_init_default_agents` 添加 WeatherAgent
- `utils/signal_fusion.py` — 新增 `_weather_layer` PostMixLayer + `weather_signal_weight` 配置
- `utils/weather_data_adapter.py` — 429 限流熔断 + 缓存 TTL 300s → 600s + 超时 15s → 8s

### 清理 (Removed) — 陈旧版本文件

- 删除根目录临时测试输出：`test_report.txt` / `test_results.txt` / `test_output.txt` / `test_all.txt` / `test_all2.txt` / `output.txt` / `pylint_broad_except_baseline.txt` / `pylint_broad_except_full.txt`
- 删除被取代的旧版审计报告：`CODE_QUALITY_CHECK_20260728.md` / `CODE_QUALITY_CHECK_2026-07-29.md` / `HEDGE_FUND_AUDIT_REPORT_20260723.md` / `P0_REPAIR_REPORT_20260723.md` / `P0_1_TOKEN_MIGRATION_REPORT.md` / `量化交易系统v8.4_综合代码审计报告.md` / `量化交易系统v8.4_综合审计报告_终版.md`
- 删除旧版建仓计划：`500万建仓计划_20260706.json` / `500万建仓计划_20260706.md` / `portfolio_return_projection.md`
- 保留最新版 `CODE_QUALITY_PHASE_A_2026-07-30.md` 和 `CODE_REVIEW_REPORT_2026-07-30.md` 作为当前基线

---

## [8.4] - 2026-07-22

### 修复 (Fixed) — 代码质量 P0/P1/P2 修复 (2026-07-22)

#### P0 — 关键修复 (存在资金损失风险)

- **配置漂移修复**: `v8.3_institutional/config/portfolio.yaml` 中 `stock_etf_capital` 从 3M 修正为 4M、`hedge_capital` 从 2M 修正为 1M，与 `configs/portfolio.yaml` (v7.7 权威版) 和 `system_config.json` 保持一致
- **变量覆盖 Bug**: `daily_workflow.py` 中 `HEDGE_FUND_MODULES_READY` 被重复赋值覆盖的问题 — 第一组(Theta/Gamma/KillSwitch)重命名为 `HEDGE_FUND_CORE_READY`
- **裸 except 修复**: `main.py` 中 3 处 `except Exception` 改为具体异常类型 (`OSError`, `yaml.YAMLError`, `ValueError` 等)
- **初始化日志升级**: `daily_workflow.py` 中 8 处模块初始化的 `logger.warning` 改为 `logger.exception`，捕获完整堆栈

#### P1 — 稳定性改进

- **依赖管理统一**: 合并两份 `requirements.txt` 为单一版本，以较高下限为准；新增 `requirements_lock.txt` 生产锁版本和 `requirements_dev.txt` 开发工具
- **安全修复**: `rule_engine.py` 中 `eval()` 调用改为受限求值 — 正则预检 + `__builtins__: {}` 空命名空间，阻止代码注入
- **.env 解析改进**: `main.py` 优先使用 `python-dotenv.load_dotenv()`，保留手动解析作为后备
- **初始化重构**: `daily_workflow.py` 新增 `_safe_init()` 统一初始化方法，消除 10 处重复 try/except

#### P2 — 工程化提升

- **统一测试配置**: `tests/conftest.py` 提供共享 fixture (sample_prices/returns/ohlcv/config)，`v8.3_institutional/tests/conftest.py` 桥接到统一配置
- **架构图**: README.md 添加 Mermaid 流程图 (调度层→环境评估→Alpha信号→风险与执行→数据层→仿真交易)
- **代码质量报告**: `CODE_QUALITY_REPORT.md` 全项目评估 (综合评分 55/100)

---

## [8.5] - 2026-07-24

### 修复 (Fixed) — P0 Bug 修复与模块真实集成

#### P0 — 关键修复

- **Kill Switch 生命周期**: 修复 KillSwitch 导入但从未 arm/check 的 P0 Bug — 在 `run()` 启动时武装，每个阶段后检查，触发即终止工作流
- **导入降级消除**: V75_READY (11个核心模块) 和 HEDGE_FUND_CORE_READY (4个对冲核心) 从 try/except ImportError 降级改为硬性导入 — 核心缺失时系统拒绝启动
- **死代码路径移除**: 移除 `sys.path.insert(0, .../_archive_dead_code)` 导入污染，神华建仓配置改为内置 fallback

#### 新增 (Added) — v8.5 模块真实集成

- **TimeSync + DataPipeline**: 集成到 `phase_check()` — 增强 NTP 时间同步 + 数据管道健康检查
- **VegaMonitor**: 集成到 `phase_hedge_fund()` — 波动率暴露监控，超限自动告警
- **LiquidityMonitor**: 集成到 `phase_v10_risk()` — 流动性评分与执行可行性检查
- **EVTTailRisk**: 集成到 `phase_v10_risk()` — 极值理论 (GPD) 尾部风险 VaR 估计
- **FactorDecayMonitor**: 集成到 `phase_signal()` — 因子 IC 衰减自动检测
- **PurgedKFoldCV**: 集成到 `phase_autolearn()` — 时间序列交叉验证与过拟合检测
- **ShadowAccountSystem**: 集成到 `phase_execute()` — 影子账户成交偏离度跟踪
- **EnvironmentIsolation**: 集成到 `run()` — 启动时研究/生产环境隔离验证

#### 清理 (Removed) — 死代码删除

- 删除孤立文件: `comprehensive_quant_system_v7.py`, `daily_startup.py`, `daily_trade_executor_full.py`, `etf_signal_mapper.py`, `generate_pre_market_summary.py`
- 删除旧版报告: `audit_report_data_pipeline.md`, `audit_report_execution_system.md`, `CODE_QUALITY_REPORT.md`, `core_modules_check_report.md`, `DIRECTORY_STRUCTURE.md`

#### 最终交付 (Final Delivery) — 2026-07-24

- **单元测试覆盖**: `tests/test_v85_modules.py` 覆盖 9 个 v8.5 核心模块 (VegaMonitor/LiquidityMonitor/EVTTailRisk/FactorDecayMonitor/ShadowAccount/PurgedKFoldCV/DataPipeline/TimeSync/EnvironmentIsolation)
- **文档注释更新**: `src/hedging/tail_risk_hedge.py` 补充 v8.5 集成说明 (EVT/Vega/流动性/因子衰减联动)
- **统一启动脚本**: `v8.5_start.bat` (菜单式入口), `quick_start.bat` (一键完整工作流), `run_tests.bat` (测试运行器)
- **评级提升**: D+ (56分) → A- (90分), 提升 34 分 (60.7%)

#### 剩余待办 (Next Iteration)

1. 真实券商对接 (当前仍用 MockBroker)
2. 单元测试覆盖率提升至 80%+
3. bat 脚本统一入口迁移完成 (根目录旧脚本逐步废弃)
4. `tail_risk_hedge.py` 完整单元测试 (当前仅文档注释更新)

#### 评级变化

- 代码集成度: D+ (4.2) → B (7.4)，提升 3.2 分
- 详细报告见: `v8.3_institutional/UPGRADE_V85_FINAL_DELIVERY.md`

### 新增 (Added) — 持仓精准优化 + 2027年化预测

- `research_report_2027_annualized_return_forecast.md` — 2027年年化收益率预测报告，基于量化三维度模型（历史统计+ML信号+因子分解，50/30/20权重）与多情景压力测试（保守/中性/悲观）联合分析
- `research_report_portfolio_improvement_v8.2.md` — 持仓改进前后对比报告，含权重调整、VaR对比、波动率分解、对冲效率分析
- `_optimize_v3.py` — 9项精准调仓执行脚本（黄金翻倍+科技微降+防御增强+对冲增强+现金优化），自动备份原始配置

### 变更 (Changed) — 9项精准调仓

- 黄金ETF(518880)：2.78% → **6.00%**（ρ~0.15唯一真分散器，翻倍增强尾部保护）
- 绿的谐波(688017)：5.00% → 3.00%（vol 70%控极端风险，保留alpha敞口）
- 中际旭创(300308)：2.35% → 1.50%（QLib看空+高波动降配）
- 新能源车ETF(515030)：1.43% → 1.00%（blend=-11.6%全组合最弱信号）
- 中国神华(601088)：2.00% → 3.50%（股息6%+Sharpe 0.915防守增强）
- 银行ETF(512800)：4.36% → 5.50%（股息5%+vol 17%低波防守）
- 上证50ETF(510050)：7.34% → 8.00%（蓝筹底仓微增，提升风险平价效率）
- 国债ETF(511010)：25.00% → 22.00%（释放3pp用于防御端收益增强）
- 创业板ETF(159915)：0.00% → 0.80%（维持成长风格最小覆盖，新增标的）

### 对冲增强 (Hedge Enhancement)

- 510050 Put：10张 → 30张（预算 ¥450K）
- 588080 Put：10张 → 12张（预算 ¥144K）
- 159915 Put：10张 → 12张（预算 ¥120K）
- 510300 Put：5张 → 8张（预算 ¥64K）
- Put总计：50张 → **62张**，总预算：¥560K → **¥778K**，悲观对冲覆盖从18pp提至25pp

### 预测结果 (Forecast Results)

- 量化模型中枢：+8% ~ +13%（加权均值约 +10.5%）
- 中性情景：+6.61%（现货+8% + CC权利金+2.91% - 对冲-0.5% + 现金+0.61%）
- 保守情景：+2.99%
- 悲观情景：-9.31%（最大回撤-27%突破15%红线，核心驱动为AI/半导体暴露约30%+市值）
- 关键风险：科技板块高度暴露（~30%+），黄金分散效应（ρ~0.15）可部分对冲但非完全抵消

### 数据 (Data)

- `config/positions.json` — 已更新至 v8.4 优化版（26标的：12个股+14ETF）
- `config/positions_backup_20260722_103324.json` — 原始配置备份，随时可回滚

## [8.3.1] - 2026-07-21

### 新增 (Added) — PUT引擎去重保护

- `utils/risk_guard_integrator.py` — 新增 `UNDERLYING_CODE_MAP` 六大品种底层代码映射表（510050/588080/159915/510300/510500/512100）
- `utils/risk_guard_integrator.py` — 新增 `_extract_underlying_code()` 类方法，三层代码提取策略（精确匹配→正则数字→最长优先模糊匹配）
- `utils/risk_guard_integrator.py` — 新增 `_deduplicate_put_orders()` Guard4后自动执行，认沽引擎为权威来源，剔除对冲引擎重复PUT，期货不受影响

### 修复 (Fixed)

- 修复对冲执行引擎与认沽保护引擎对同一底层标的生成重复PUT订单的问题（4笔重复：510050/588080/159915/510300）
- 修复模糊匹配中子串误判：`"科创50ETF Put"` 不再因 `"50etf"` 被误识别为 510050（改为最长优先匹配）

### 验证 (Verified)

- 代码提取测试 12/12 通过
- 端到端集成测试通过：去重前 1期货+4期权=5笔 → 去重后 1期货+0期权=1笔
- 认沽保护订单：4笔保持不变
- Python 3.8 语法兼容性通过

## [8.3.0] - 2026-07-21

### 新增 (Added) — 风控守卫强制执行系统

- `utils/hedge_execution_engine.py` (19.9KB) — 对冲信号→实际期货/期权订单桥梁，持仓加权Beta计算+回撤加码联动
- `utils/vol_target_controller.py` (12.2KB) — AQR/Man Group风格波动率目标缩仓控制器，target 12%年化波动率
- `utils/protective_put_engine.py` (19.1KB) — 56万PUT预算自动动用+OTM 5%虚值Put覆盖+到期前5天自动滚仓
- `utils/risk_guard_integrator.py` (17.4KB) — 四Guard联动（回撤→波动率→对冲→认沽）每日EOD强制执行+执行日志
- `utils/master_config_manager.py` (13.0KB) — 三份配置文件→单一事实源，启动时一致性校验
- `backtest_current_portfolio.py` (23.5KB) — 23标的实际持仓2021-2026回测，不达标自动输出调仓建议

### 变更 (Changed)

- `run_daily_eod.py` — 步骤5和6之间新增风控守卫集成调用（步骤5.5），失败降级不阻塞报告

### 修复 (Fixed)

- 修复对冲信号计算后从未转化为实际订单的问题
- 修复波动率控制报告输出但不执行缩仓的问题
- 修复三份计划文件配置漂移问题
- 修复回测标的（茅台/平安）与实际持仓（ETF+科技成长）不匹配问题

### 验证 (Verified)

- Beta暴露：1.052 → 0.30（对冲后）
- IF期货：1手空开 = ¥1,241,520 名义价值
- PUT保护：40张 OTM 5% 虚值Put覆盖四大指数ETF
- 波动率：vol_scale=1.0，当前无需缩仓
- 回撤：Level 0 正常状态
- 所有7个文件语法检查通过
- Python 3.8 导入测试通过

## [8.1] - 2026-07-17

### 新增 (Added) — 全自动交易闭环 + LLM 盘中决策

- ✅ `run_daily_eod.py` — 盘后自动闭环：收盘报告 → 次日计划 → 预生成盘中决策
- ✅ `v7.5_institutional/llm_intraday_decision_engine.py` — LLM 盘中自动决策引擎，每 15 分钟分析持仓/行情/对冲状态并生成买卖建议
- ✅ `apply_llm_decisions_to_plan.py` — 自动将 `daily_pnl_report` 中的 AI 建议灌入次日 `trade_plan`
- ✅ `generate_pre_market_summary.py` — 自动生成开盘前 Markdown 摘要
- ✅ `register_intraday_task.ps1` — 注册 Windows 定时任务 `Quant_LLM_IntradayDecision`，交易日 9:25-15:05 每 15 分钟执行
- ✅ `annual_return_forecast` — 保守/中性/悲观三情景年化收益测算，内置夏普比率与最大回撤预测
- ✅ watchlist 自动维护 — 根据 `daily_pnl_report` 自动生成监控名单，统一止损线 -12%
- ✅ `trade_plan_20260720.json` / `trade_plan_20260721.json` — 实盘交易计划，含现货 26 笔 + 期权 6 笔 + 期货/期权对冲 + LLM 决策
- ✅ `pre_market_summary_20260720.md` — 7/20 开盘前执行摘要

### 变更 (Changed)

- 🔄 `llm_client.py` — LLM 优先模型切换为本地 Ollama `qwen2.5:7b`，六级降级链：Ollama → 腾讯混元 → 百度千帆 → 智谱GLM → 豆包 → DeepSeek
- 🔄 `trade_plan` 结构 — 新增 `llm_overrides`、`annual_return_forecast`、`watchlist`、`llm_intraday_decisions` 字段
- 🔄 日建仓预算 — 20 万 → 15 万，建仓期延长，降低追高风险
- 🔄 止损线 — 组合止损 -12% → -10%，单票止损统一 -10%，监控名单 -12%
- 🔄 IF 期货 — 3 手 → 5 手，提升 Beta 对冲覆盖
- 🔄 Put 保护 — 新增 510050 Put 10 张 + 510300 Put 5 张，尾部风险保护增强

### 验证 (Verified)

- ✅ `run_daily_eod.py` 全流程测试通过：报告生成 → 计划写入 → 盘中决策预生成
- ✅ `llm_intraday_decision_engine.py` mock 模式测试通过：26 持仓 → 5 条有效决策
- ✅ `apply_llm_decisions_to_plan.py` 测试通过：自动注入 IF/ Put/ 建仓顺序调整
- ✅ Windows 定时任务注册成功：`Quant_LLM_IntradayDecision`
- ✅ `trade_plan_20260720.json` JSON 校验通过
- ✅ README 已更新并推送至 GitHub

### 实盘状态

- 总资金：¥5,000,000
- 现货持仓：23 个有效标的，市值 ¥2,238,363
- 现金未建仓：¥2,761,637（建仓进度 44.8%）
- 7/17 现货浮亏：-¥20,609.30（-0.92%）
- 最大回撤：-13.87%
- Covered Call 年化：+9.6%
- 组合年化测算（中性）：+15.2%，夏普 1.90
- 组合年化测算（保守）：+8.3%，夏普 0.83

## [8.0] - 2026-07-14

### 新增 (Added) — 对冲执行单优化

- ✅ 动态 Beta 计算：基于 `positions.json` 实时计算组合加权 Beta
- ✅ 订单去重合并：按（类型、标的、动作）键合并重复订单
- ✅ 配置驱动对冲：从 `hedge_positions` 读取期货手数、期权合约、权利金预算
- ✅ 执行时机管理：期权 09:30-10:00，期货 10:30-11:00
- ✅ 资金预留机制：对冲账户保留 30% 资金作为动态调整空间
- ✅ 现货订单价格修复：从默认 10.0 改为真实市场价格

### 变更 (Changed)

- 🔄 `hedge_execution_orders.py` — 升级为配置驱动 + 订单去重
- 🔄 对冲策略 — IF 期货 1 手 → 3 手，增加沪深300ETF期权
- 🔄 建仓计划 — 调整防御资产权重，增加科技股配置

## [7.6] - 2026-07-09

### 新增 (Added) — 仓位重建 + 资金流整合

- ✅ 清除原 23 个旧仓位，重建为 20 标的新计划
- ✅ 整合 2026-07-09 ETF 资金流向报告信号
- ✅ 双账户结构：股票ETF账户 300万（进攻）+ 对冲账户 200万（保护）
- ✅ 十五年五规划适配：健康中国权重上调，新质生产力降权
- ✅ 固定日预算：每个交易日固定 20 万元建仓
- ✅ 高价股保护：100 股成本超过当日预算 50% 时自动跳过

## [7.5.1] - 2026-07-06

### 新增 (Added) — 7/6 自动交易建仓与全自动运行

- ✅ 7/6 建仓计划文件
- ✅ 交易计划格式对齐 `daily_workflow.py`
- ✅ 交易日自动运行：盘前 07:00 / 盘后 15:30
- ✅ 干跑验证通过

### 变更 (Changed)

- 🔄 `README.md` — 新增“7/6 自动建仓已就绪”状态说明
- 🔄 资金配置更新：股票 47% + 对冲 40% + 现金 13%

### 验证 (Verified)

- ✅ `daily_workflow.py` 读取 `trade_plan_20260706.json` 正常
- ✅ Windows 任务计划已注册并查询成功
- ✅ README 已更新并推送至 GitHub

## [7.5.0] - 2026-07-05

### 新增 (Added) — 机构级实盘交易系统

- ✅ 风险预算：Risk Parity + Improved Kelly Criterion + 三级回撤防御
- ✅ 三联对冲：Beta/Vol/Correlation 三类对冲实时联动
- ✅ 智能执行：Iceberg + TWAP/VWAP/POV + 滑点熔断 + NTP 时间同步
- ✅ 回测严谨性：Walk-Forward + 三段极端行情压力测试
- ✅ 全模块自动调度：盘前 Wind 校准 + v5 优化 + 每日工作流；盘后 黑天鹅测试 + 汇总报告
- ✅ Qlib 信号集成：本地 LightGBM 深度学习信号生成，36 个技术指标特征，自动注入 SignalFusion 并映射为订单调整（加仓 30% / 减仓 50% / 跳过）
- ✅ 订单智能调整：Qlib 信号直接参与 Phase 5/6，强看多自动加仓、中性维持、看空减仓或跳过，实现信号到执行的闭环

## [Unreleased]

### 规划中
- 实现投资组合优化（增加国际资产配置）
- 实现风险平价优化（动态风险调整）
- 增强实时监控功能（5分钟频率）
- 添加更多压力测试场景
- 优化机器学习预测模型

## [7.4.0] - 2026-07-04

### 新增 (Added) — v7.4 个股分档建仓执行系统

- ✅ 建仓计划生成器 (`generate_shenhua_build_plan.py`)
- ✅ 建仓执行器 (`shenhua_build_executor.py`)
- ✅ 工作流集成（条件导入，优雅降级）

### 变更 (Changed)

- 🔄 `trading_workflow.py` — 升级为 v7.4
- 🔄 `README.md` — 更新为 v7.4 版本

### 验证 (Verified)

- ✅ 5种场景测试全部通过
- ✅ 工作流端到端测试：`phase_shenhua_build()` 返回 `ok=True`
- ✅ 100万资金建仓计划验证：26,700股，平均成本37.28元
- ✅ 条件导入测试：HAS_V74_MODULES=True 时正常加载，=False 时优雅降级

### 设计说明

**研报→JSON→订单全自动转化**：
传统建仓依赖人工阅读研报、手动计算档位和股数，效率低且易出错。v7.4 将研报建仓策略结构化为 JSON 计划文件，由执行器根据实时价格自动判断当前应执行的档位和订单数量，实现"研报→JSON→订单"的全自动转化。

**非阻塞设计**：
建仓阶段作为盘后第 11 阶段自动执行，无计划文件或模块不可用时自动跳过，不影响主交易流程。通过 `HAS_V74_MODULES` 标志实现条件导入，与 v7.1.2/v7.3 的条件导入机制保持一致。

## [7.1.2] - 2026-07-04

### 新增 (Added) — v7.1.2 模块反向同步（来源: ZCodeProject）

- ✅ 黑天鹅极端行情优化器 (`black_swan_optimizer.py` v7.2)
- ✅ 决策护栏层（4个模块，整合自 daily_stock_analysis 项目）
- ✅ 工作流集成（条件导入，优雅降级）
- ✅ 研究文档

### 变更 (Changed)

- 🔄 `trading_workflow.py` — 覆盖为 v7.1.2 版本
- 🔄 `live_trading_workflow.py` — 覆盖为 v7.1.2 版本
- 🔄 `README.md` — 更新为 v7.1.2 版本

### 验证 (Verified)

- ✅ 语法检查通过：7个文件 py_compile 全部 OK
- ✅ 导入测试通过：5个核心模块全部 OK
- ✅ 实例化测试通过：BlackSwanOptimizer 6大组件全部 ON
- ✅ v7.1.2 集成代码验证：HAS_V712_MODULES=True，5个模块全部可访问
- ✅ 基础工作流测试：`--check-today` 正常运行

### 已知限制 (Known Limitations)

- ⚠️ 丢失 safe_float 防御代码（28系统原版有，ZCodeProject版本无）
  - 风险等级：中（ZCodeProject 有 30 个 try-except 块兜底）
  - 影响：实时交易场景异常数据可能跳过订单而非优雅降级
  - 恢复方案：可从 .bak.20260704_0615 备份手动合并 safe_float 代码

## [5.10.0] - 2026-07-03

### 新增 (Added)
- ✅ 多层次对冲策略实现
- ✅ 智能对冲触发机制
- ✅ 动态资金管理器
- ✅ 增强风险管理器
- ✅ 自动化执行系统
  - 智能订单路由
  - 异常处理机制
  
- ✅ 策略优化器
  - 多策略整合
  - 回测验证
  - 实时优化
  - 性能评估
  
- ✅ 配置管理系统
  - 动态参数配置
  - 风险参数设置
  - 执行配置管理
  - 配置备份和恢复
  
- ✅ 监控仪表板
  - Web界面监控
  - 命令行监控
  - 实时状态显示
  - 风险警报系统
  
- ✅ 系统部署脚本
  - 自动化部署
  - 环境配置
  - 依赖检查
  - 一键启动

### 改进 (Improved)
- 🔄 系统架构优化
  - 模块化设计
  - 清晰的接口定义
  - 错误处理机制
  
- 🔄 数据处理优化
  - 数据缓存机制
  - 数据验证
  - 性能优化
  
- 🔄 日志系统
  - 多级日志
  - 文件和终端输出
  - 日志轮转
  
- 🔄 用户界面
  - 简化的启动方式
  - 直观的监控界面
  - 详细的错误信息

### 修复 (Fixed)
- 🐛 修复了模块导入问题
- 🐛 修复了数据获取超时问题
- 🐛 修复了内存泄漏问题
- 🐛 修复了并发访问问题

### 文档 (Documentation)
- 📝 完善的用户指南
- 📝 详细的项目文档
- 📝 目录结构说明
- 📝 部署指南

## [5.9.0] - 2026-06-15

### 新增
- 基本框架搭建
- 核心策略模块
- 数据提供器
- 风险指标计算

### 改进
- 代码结构优化
- 错误处理机制
- 日志系统

### 修复
- 内存使用优化
- 并发处理问题

## [5.8.0] - 2026-06-01

### 新增
- 项目初始化
- 基础架构设计

---

## 版本说明

### 版本号格式
- 主版本号：重大功能变更
- 次版本号：新功能添加
- 修订号：问题修复

### 发布周期
- 重大版本：每季度发布
- 功能版本：每月发布
- 修复版本：按需发布

### 兼容性说明
- 重大版本可能引入不兼容的变更
- 功能版本保持向后兼容
- 修复版本完全向后兼容

### 贡献指南
如需贡献代码，请遵循：
1. Fork 项目
2. 创建功能分支
3. 提交变更
4. 推送到分支
5. 创建 Pull Request

---

**注意：** 此项目仍在积极开发中，可能会有重大变更。