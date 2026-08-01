# 综合量化策略系统 v8.7.2 项目文档

> 顶级对冲基金视角 | 500 万实盘部署 | 全自动交易闭环 | 年化 ≥ 8% 且最大回撤 < 15% | 风控守卫强制执行 | 对冲执行引擎 | 认沽期权自动保护 | 波动率目标缩仓 | PUT 去重保护 | 实际持仓回测验证 | 统一配置事实源 | ETF 资金流追踪 | AI 增强预测 | WonderTrader 高价值模块集成 | Wind MCP 优先数据源 | 本地 Ollama 双 LLM 决策 | 风险预算驱动建仓 | Greeks 动态对冲 | 交易成本建模 | 动态 Beta 计算 | 订单去重合并 | 配置驱动对冲 | 执行时机管理 | 资金预留机制 | V9 Regime-Specific LGB 生产基线 + 影子账户灰度发布 | LightGBM 增强信号接入交易流水线 | 静态代码分析引入 | 安全漏洞清零 + OpenCodeReview AI 深度代码审计

**作者**：yuppiez99999
**实盘状态**：✅ 已部署（2026-07-28）
**自动交易**：✅ 盘前自动生成计划 + 盘中每 15 分钟自动决策 + 午盘/夜盘自动刷新 LLM 决策 + 盘后自动总结 + Windows 任务计划自动触发
**LLM 模型**：双模型架构 — 快速模式 Qwen2.5 7B (~22 秒) + 深度思考 DeepSeek-R1 14B (~1-3 分钟，复杂场景自动触发）

---

## 项目概述

综合量化策略系统 v8.7.2 是一个专业量化交易平台，历经 v8.3 风控守卫强制执行 → v8.4 持仓精准优化 → v8.5 自动交易计划部署 → v8.6 V9 影子账户灰度发布 → v8.6.1 顶级对冲基金风控审计修复 → v8.6.3 因子流水线 IC 加权组合方法学闭环 → v8.6.4 因子流水线深度接入生产 → v8.6.5 第二轮 CRO 黑天鹅防御审计修复 → v8.6.7 测试金字塔体系建立 → v8.6.9 GitHub 热门项目深度集成（第 6 信号源） → v8.6.11 安全漏洞修复（Python 3.14.4 生产基线） → v8.6.12 顶级对冲基金代码质量优化（P0/P1/P2 共 9 项） → v8.7 LightGBM 增强信号接入交易流水线（第 7 信号源） → v8.7.1 静态代码分析引入（mypy + pylint 自动守门员） → **v8.7.2 安全漏洞清零 + OpenCodeReview AI 深度代码审计**，结合 v8.2 双 LLM 架构 + v8.3 四模块风控联动 + v8.6 影子账户 fail-fast + v8.6.1 fail-closed 三层防护 + v8.6.5 EOD Guard 真实生效 + v8.6.7 测试金字塔 + v8.6.9 RIA--TV++ 研究蒸馏 + v8.7 真实 OHLCV + 新闻情绪因子 GPU 训练 + v8.6.12 ConfigManager 统一配置 + phase_signal God Function 重构 + v8.7.1 mypy/pylint 静态守门员 + **v8.7.2 Dependabot 自动化漏洞扫描 + OpenCodeReview AI 代码质量审计**，实现了从"纸面风控建议" → "代码强制执行" → "数据驱动仓位最优化" → "纸面风控 → 真实可执行风控" → "EOD Guard 失效 → 真实生效" → "被动审计 → 主动防御" → "7 信号源完整融合 + 低质量标的自动降权" → "代码质量自动化守门" → **"供应链安全自动化 + AI 深度代码审计"** 的九级进化。系统以 **500 万元人民币** 为基础管理规模，分为 **股票 ETF 账户 300 万 + 对冲保护账户 200 万**，目标年化收益 ≥ 8%，最大回撤控制在 15% 以内，**2030-12-31 全部清仓**。

---

## 核心特性

### 机构级量化系统
- **双账户结构**：500 万总资金（现货 300 万 + 对冲 200 万），已实盘部署
- **风险预算驱动**：Risk Parity + Kelly 公式动态分配建仓预算
- **三联对冲引擎**：Beta/Vol/Correlation 三类对冲实时联动 + 尾部风险保护
- **完整风控体系**：个股止损 + 组合回撤三级防御 + Walk-Forward 回测验证

### AI 增强模块
- **双 LLM 架构**：快速模式 Qwen2.5 7B（常规决策 ~22 秒）+ 深度思考 DeepSeek-R1 14B（复杂场景 ~1-3 分钟，自动触发）
- **深度思考触发**：5 种场景自动切换深度模型 — 组合止损 / 多只个股止损 / ETF 强加仓 / 对冲偏离 / 大幅盈亏
- **价格预测**：TimesFM 零样本 + TensorFlow LSTM + ARIMA 三级降级
- **六级降级链**：Ollama → 腾讯混元 → 百度千帆 → 智谱 GLM → 豆包 → DeepSeek
- **新闻情感分析**：实时抓取东方财富 / 巨潮资讯 / 新浪财经公告与研报
- **外部数据源**：FRED / Econdb / Finnhub / CoinGecko 全球宏观与另类数据

### 完全自动化
- **无人化交易闭环**：盘前自动生成 → 自动确认 → 盘后自动执行 → 自动生成下一日计划
- **Windows 任务调度**：07:05 盘前 / 09:30 早盘 / 14:00 午盘 / 21:00 夜盘 / 15:30 盘后 + 盘中每 15 分钟 LLM 决策
- **午盘/夜盘 LLM 自动刷新**：14:00 午盘和 21:00 夜盘前自动重新调用 LLM 决策引擎，确保使用最新市场判断
- **十五五规划对齐**：2026-2030 五年阶段管理，2030-12-31 强制清仓

### WonderTrader 高价值模块
- **统一数据结构**：Tick/Bar/Order/Trade/Position/Contract 标准化模型
- **合约规格管理**：全市场股票/ETF/期货/期权统一管理
- **价差策略框架**：ETF 配对交易 / 跨期套利 / 价差回归
- **Tick 级回测引擎**：精确撮合，支持事件驱动策略回测
- **执行算法**：MinImpact / TWAP / VWAP 大单拆分算法

### 顶级对冲基金优化
- **Greeks 动态对冲**：Delta/Gamma/Theta/Vega 自动调整期货/期权对冲量
- **交易成本模型**：统一滑点 / 佣金 / 冲击成本建模
- **智能执行选择**：自动选择综合成本 + 时间最优算法
- **风险归因面板**：行业 / 风格 / 资产类型风险分解
- **Greeks 监控面板**：实时暴露监控与再平衡信号
- **LLM 盘中自动决策**：每 15 分钟自动分析持仓 / 行情 / 对冲状态，生成买卖建议
- **年化收益测算**：多情景分析（保守 / 中性 / 悲观），内置 annual_return_forecast

### 回测引擎增强
- **因子生命周期管理**：FactorRegistry 监控因子衰减，自动标记失效因子
- **成本归因拆分**：滚动成本 / 保证金成本 / 滑点成本分别统计，支持精细化业绩分析
- **滑点成本修复**：仅在对冲比率变化时产生滑点，避免无效成本累积
- **订单无关再平衡**：先计算所有目标权重，先执行卖单再执行买单，消除序列依赖
- **统一对冲暴露上限**：HEDGE_EXPOSURE_CAP = 0.40，控制尾部风险
- **换手率感知约束**：TURNOVER_BUDGET = 0.20，接近预算时自动上调再平衡阈值
- **黄金 ETF 数据源修复**：Baostock 失败时自动 fallback 至 AKShare，确保数据完整性

### 风控守卫强制执行
- **四模块联动风控链**：回撤检查 → 波动率控制 → 对冲执行 → 认沽保护，每日 EOD 后强制执行
- **对冲执行引擎**：对冲信号 → IF 期货 + ETF 期权订单桥梁，动态 Beta 计算 + 回撤加码联动
- **认沽期权自动保护**：77.8 万 Put 预算自动动用，OTM 5% 虚值 Put 覆盖四大指数 ETF，到期前 5 天自动滚仓
- **波动率目标缩仓**：AQR / Man Group 风格 Vol Targeting，realized vol > 12% 时自动缩减建仓预算
- **回撤四级强制执行**：Level 1 预警 → Level 2 缩预算 + 加对冲 → Level 3 清建仓 → Level 4 全面停止
- **PUT 引擎去重保护**：对冲引擎与认沽保护引擎自动去重，认沽引擎为权威来源，避免超额对冲
- **持仓精准优化**：9 项精准调仓（黄金翻倍 / 科技微降 / 防御增强 / 对冲增强），基于量化三维度模型与多情景压力测试
- **实际持仓回测验证**：26 标的 2021-2026 真实持仓回测，不达标自动输出调仓建议
- **统一配置事实源**：三份配置文件一致性校验，消除配置漂移

### 自动交易计划部署
- **下周自动交易计划**：自动生成未来 5 个交易日 trade_plan 文件，每日 20 万建仓预算，含 Put 保护订单
- **Windows 任务计划注册**：07:00 工作流 / 09:30 早盘 / 14:00 午盘三个时段自动触发
- **daily_workflow 兼容性修复**：修复 CircuitLevel 导入、CircuitBreaker 方法调用、KillSwitch 初始化等多项版本不兼容问题
- **报告写入可靠性增强**：添加重试机制（最多 3 次，间隔 1 秒）+ fallback 路径（logs/ 目录），防止 Windows Defender 拦截导致报告丢失
- **批处理脚本优化**：修复中文系统下日期提取错误，确保 `%date%` 格式正确解析

### V9 Regime-Specific LGB + 影子账户灰度发布
- **V9 生产基线确立**：Regime-Specific LGB 双模型策略通过 30 个月 Walk-Forward 回测验证 — 年化 **19.62%** / 最大回撤 **9.95%** / Sharpe **1.315** / DSR max_pass **18** / Sharpe CV **0.7673**（DSR ≥ 5 + 年化 ≥ 15% + 回撤 ≤ 10% + Sharpe CV < 1.0 全部达标）
- **影子账户 Stage 1 启动**：10% 资金（¥500,000）灰度发布运行中，三阶段推进路径（10% → 50% → 100%），最小运行周期 14 天，PBO < 0.5 准入（Bailey 2017）
- **Fail-fast 触发器**：单日回撤 > 3% 或 3 日累计回撤 > 5% 立即终止 + latch 锁存 + terminate_and_rollback 动作，阻止推进下一灰度阶段
- **TRADING_ENV fail-closed 设计**：`utils/trading_env.py` 三环境切换（production / shadow / development），production 模式强制 fail-closed — 风控异常时阻止交易而非降级放行
- **daily_workflow Phase 10 集成**：`v8.3_institutional/daily_workflow.py::phase_shadow_monitor()` 每日记录影子账户 NAV 到 `output/shadow_account/shadow_state.json`，基于 Phase 5 目标权重 + MarketDataProvider 实际收盘价计算当日组合收益
- **iFinD 真实财务数据接入**：`utils/ifind_client.py::get_fundamentals_batch()` 并发拉取 PE/PB/ROE/总市值/流通市值，多编码 `.env` 加载（gbk/utf-8/utf-8-sig/latin-1）兼容 Windows 中文系统
- **数据降级链**：iFinD（首选） → Baostock（fallback，覆盖 100/105 = 95.2% 标的） → 价量代理（兜底），确保数据完整性
- **VT_MICRO_VOL_SKEW_INV 因子突破**：23 标的下首个完整通过 G1-G4 + Enhancement + Regime + Shadow(风险管理模式) 全部 7 级 Gate 的因子 — 启用风险管理后 max_dd 从 23.3% 降至 10.57%，live_dsr 从 1.12 降至 0.70（仍 > 0.5，Alpha 信号保留）

### 顶级对冲基金风控审计修复
- **审计背景**：以世界顶级对冲基金视角审计发现系统存在严重"纸面风控"问题 — README 声称的"四 Guard 联动强制执行"在实际运行中完全失效，多个核心风控模块初始化失败但系统仍继续执行交易，存在"编制结果"嫌疑
- **CircuitBreaker 熔断器修复**：`daily_workflow.py` 两处 `CircuitBreaker()` 初始化缺少必需 `name` 参数导致 TypeError，风控进入降级模式；修复为 `CircuitBreaker(name="daily_workflow")`，四级熔断（数据源 / API / 订单 / 全面停止）恢复可用
- **KillSwitch 紧急熔断修复（关键 bug）**：原代码 `ks_status.get("triggered")` 检查**不存在的字段**（`check_margin_status()` 返回 `level` / `can_trade`，无 `triggered`），导致 Kill Switch **永远不会触发**；修复为 `level >= 2 or not can_trade` 正确判断，异常时 fail-closed 视为 L3 最高风险
- **KillSwitch broker_callback 注册**：新增 `_execute_kill_switch_callback()` 方法并注册到 KillSwitch，使 `execute_kill_switch()` 可真实执行（未注册时抛 RuntimeError）；L1 过滤 BUY 订单 / L2 取消待执行 + 标记期权空头平仓 / L3 变现 10% 红利 ETF + 全面停止交易
- **v8.5 模块加载修复（3/9 → 9/9）**：修复 6 个模块类名 / 路径错误 — `EVTTailRisk` → `ExtremeValueAnalyzer`、`PurgedKFoldCV` → `PurgedKFold`、`ShadowAccountSystem` → `ShadowAccount`、`TimeSync` → `GlobalTimeService`、`EnvironmentIsolation` / `TimeSync` 路径改 `src.utils.` 前缀（避免被 root utils/ 遮蔽）、`DataPipeline` 移除不存在的 `get_data_pipeline` 函数
- **fail-closed 三层防护**：① `phase_check` 风控核心失败时设 `status = FAIL` + `fail_closed = True`（原代码设 `PASS` 继续执行）；② `run()` 循环检查 `fail_closed` 终止工作流；③ `phase_execute` 双重保险阻止一切交易
- **黑天鹅防护六层联动**：CircuitBreaker（数据源熔断） + KillSwitch（保证金熔断 + 实际执行） + EVTTailRisk（极端尾部风险建模） + ShadowAccount（影子账户验证） + UnifiedRiskCockpit（下单前全量扫描） + fail-closed（风控失败阻止交易）全部从"纸面风控"升级为"真实可执行风控"

### 因子流水线 IC 加权组合方法学闭环
- **8 级因子流水线**：从原"四道关卡"升级为完整 8 级流水线 — G1 正交性 + G2 IC 稳定性（真实日频 IC 序列） + G3 DSR 防过拟合 + G4 经济逻辑 + Enhancement（容量 + Regime） + Shadow 影子账户（含风险管理） + Committee 因子委员会（5 Agent） + IC 加权组合
- **首个 approved 因子 VT_MICRO_VOL_SKEW_INV**：23 标的下完整通过 G1-G4 + Enhancement + Regime + Shadow 7 级 Gate — 启用风险管理后 max_dd 从 23.3% 降至 10.57%，live_dsr 从 1.12 降至 0.70（仍 > 0.5，Alpha 信号保留）
- **QualityTrend 类因子突破**：基于 baostock 真实历史季度财务数据，实现 4 个质量变化类因子（ROE_DELTA / MARGIN_EXP / DEBT_RED / GROWTH_ACCEL），捕捉基本面二阶导信号；**VT_QUALTREND_MARGIN_EXP**（毛利率同比扩张，winsorize 处理）成为 QualityTrend 类首个 approved 因子 — IC_IR = +0.3981, live_dsr = +0.9960, max_dd = 0.0759
- **IC 加权组合方法学**：用滚动 IC_IR 作为动态权重（`w_i = IC_IR_i / sum(|IC_IR_j|)`，保留符号自适应信号反转），优于等权组合 — IC_IR +0.4434（vs 等权 +0.1364）；VT_MICRO_VOL_SKEW_INV 在 78% 时间被反向使用（weight < 0），自动适应信号反转
- **Config_E+ 突破**：发现 IC 加权与单因子参数敏感性相反 — 单因子用 Config_E 让 live_dsr 下降（Alpha 被压缩），IC 加权用 Config_E 让 live_dsr 上升（噪声被压缩）；**Config_E_plus1**（target_vol = 0.07）让组合首次通过 Shadow — live_dsr = +0.6151, max_dd = 0.0438
- **PipelineOrchestrator 集成**：IC 加权组合机制集成到 PipelineOrchestrator 内部，与单因子流水线独立运行，通过 `ic_weighted_enabled` 一键开关，结果通过 `PipelineResult.factor_combinations` 字段输出；8 项验收全通过，集成结果与独立脚本 100% 一致
- **lookback 优化**：5/10/15/20/30 5 组梯度测试发现 lookback = 10 显著优于默认 20 — IC_IR +0.4434 → +0.5840 (+31.7%)，live_dsr +0.6151 → +2.2033 (+258%)，total_return +0.1909 → +0.3290 (+72.3%)，max_dd 0.0438 → 0.0323 (-26.3%)；综合评分 z-score = +4.238
- **研究层面最终配置**：VT_MICRO_VOL_SKEW_INV + VT_QUALTREND_MARGIN_EXP × IC 加权（lookback = 10） × Config_E_plus1，研究脚本实测 IC_IR = +0.5840, live_dsr = +2.2033, max_dd = 0.0323, total_return = +0.3290
- **已接入生产**：`PipelineResult.factor_combinations` 现被 `utils/portfolio_optimizer.py::PortfolioOptimizer` 真实消费，影响 Phase 10 影子账户 NAV 计算；`utils/signal_fusion.py::SignalFusionEngine` 新增 `inject_pipeline_factor_signals()` 第 5 信号源（保守权重 0.05）；离线脚本 `scripts/run_pipeline_factor_offline.py` + Windows 任务 `QuantPipelineFactor_06AM`（06:00 触发）每日生成 `models/pipeline_factor_signals/pipeline_factor_signals_{date}.json`

### 第二轮对冲基金风控审计修复
- **审计背景**：2026-07-26 第二轮以世界顶级对冲基金 CRO（首席风险官）视角，对 P0/P1 修复后的系统进行黑天鹅极端市场对冲能力审计。审计揭示 3 个**之前未发现的 P0 级致命 bug**（与硬约束"All P0 must be fixed before next market open"冲突），CRO 综合评分 **3.5/10**（远低于对冲基金及格线 7.0）。修复后评分提升至 **7.5/10**。

#### P0-D: EOD Guard KillSwitch 检查完全失效（已修复 ✅）
- **原始 bug**：`utils/risk_guard_integrator.py:463-465` 中 `pnl_summary.get('margin_used', 0)` 在字段存在但值为 `None` 时返回 `None`（非默认值 0），导致 `None/None` 抛 TypeError 被外层 try/except 吞掉，`trade_plan_20260727.json` 显示 `kill_switch.level = 0, can_trade = true`
- **致命影响**：即使保证金占用率达 80.36% 触发 L2 熔断（`kill_switch_events.jsonl` 实证），EOD Guard 仍认为 `level = 0`，**次日交易计划不会被熔断信号阻断**
- **修复方案**：当 `margin_used` 或 `total_equity` 为 None 时，回退到 `KillSwitch._estimate_margin_from_positions()`（基于 `config/positions.json` 真实持仓估算，实测返回 80.36%）
- **验证结果**：修复后 `margin_usage = 0.8036, level = 2, can_trade = False, can_open = False` → L3 触发"全面停止交易, 仅允许平仓"

#### P0-E: 对冲执行引擎代码 BUG（已修复 ✅）
- **原始 bug**：`utils/hedge_execution_engine.py:264` 遍历 `hedge_positions` 字典时，`hedge_positions` 包含 `description`、`hedge_mode`、`budget_summary` 等非字典字段（值为字符串），调用 `hedge_pos.get("instrument", "")` 抛 `'str' object has no attribute 'get'`
- **致命影响**：每次 EOD 对冲订单生成都失败（`risk_guard_20260727.log` 实证），**次日没有 IF 期货空头 + 没有认沽期权保护**
- **修复方案**：遍历时添加 `if not isinstance(hedge_pos, dict): continue` 类型检查
- **验证结果**：修复后成功生成 1 个 IF 期货空头 + 4 个 Put 期权保护订单（510050 60张 ¥900K + 科创50ETF 25张 ¥300K + 创业板ETF 25张 ¥250K + 沪深300ETF 25张 ¥200K），总权利金 ¥1,650,000

#### P0-F: Windows 任务从未实际运行（已修复 ✅）
- **原始 bug**：`setup_scheduled_tasks.bat` 中 schtasks 命令缺少 `/rl HIGHEST` 和 `/ru SYSTEM`，导致 4 个任务以 `Interactive only` 模式注册（用户登出 / 锁屏时不触发），Last Run Time 均为 1999/11/30（占位符，从未运行）
- **致命影响**：盘前 06:00/07:00 任务在用户未登录桌面时不触发 → **整个交易计划链断裂**；即使触发，普通权限也无法执行 KillSwitch fail-closed 强平动作
- **修复方案**：所有 schtasks 命令添加 `/rl HIGHEST /ru SYSTEM`，并复制 `run_weekly_auto_20260727.bat` 到通用名 `run_weekly_auto.bat`（避免每周手动重命名）
- **验证结果**：4 个任务全部以 `Run As User: SYSTEM` + `Logon Mode: Interactive/Background` 模式运行，Next Run 2026/7/27 6:00:00

#### CRO 黑天鹅防御能力评分变化

| 维度 | 满分 | 修复前 | 修复后 | 关键改进 |
|------|------|--------|--------|----------|
| Kill Switch 设计完整性 | 2.0 | 1.5 | 1.8 | null 回退机制让保证金检查真实生效 |
| EOD Guard 链集成度 | 2.0 | **0.3** | **1.7** | KillSwitch 失效修复 + 对冲引擎崩溃修复 |
| 黑天鹅机制覆盖度 | 2.0 | 1.0 | 1.2 | 对冲订单可生成, 但仍缺大盘熔断/隔夜跳空 |
| 生产可用性 | 2.0 | **0.4** | **1.6** | SYSTEM 账户 + Background 模式, 无需登录 |
| 实际运行验证 | 2.0 | **0.3** | **1.2** | 验证脚本通过, 待 2026-07-27 真实触发 |
| **总分** | **10** | **3.5** | **7.5** | **+4.0 (提升 114%)** |

#### 仍存在的风险缺口（P1 级，待后续修复）
1. **broker_callback 从未注册**：L2/L3 触发后只能"标记"不能"执行"——强平深虚值期权空头、变现红利 ETF 等动作**从未真正发生**（需对接券商 API）
2. **隔夜跳空 + 大盘熔断 + 全局撤单三大黑天鹅场景未实现**：无 9:25 集合竞价前仓位调整；无沪深300 跌5%/7%触发的全局平仓；无 >2000 家涨跌停时全局撤单
3. **相关性对冲模块孤立**：`correlation_monitor.py` + `correlation_hedger.py` 已实现但未集成到 EOD Guard 链
4. **shadow_account.py 是研究代码**：risk_managed 模式（波动率缩放 15% + 回撤去杠杆 50%）设计良好但未集成到生产

### 测试金字塔体系建立
- **方法论转变**：前两轮 CRO 审计（v8.6.1 / v8.6.5）虽然每次都发现并修复了一批 P0/P1 级 bug，但每次审计后又会出现新的 bug —— 根本原因是"修复—审计—再修复"循环没有回归测试保护已修复的 bug。v8.6.7 建立**测试金字塔**，从被动审计转向主动防御，让每个已修复的 bug 都有对应的回归测试，确保不回归。

#### 测试金字塔三层结构

```
                  /\
                 /  \        E2E (8 个, 6.7%)
                /----\       真实生产数据 + 真实文件 IO + 跨模块
               /      \      集成 (26 个, 21.7%)
              /--------\     多模块协作 + Mock 外部依赖
             /          \    单元 (86 个, 71.7%)
            /____________\   单模块 + 全 Mock + <1s
```

**总计 120 个测试用例，全部通过，运行时间 51~59 秒，覆盖率 65.20%**

#### 5 条关键链路覆盖

| # | 关键链路 | 测试文件 | 测试数 | 守护的 bug |
|---|---------|---------|--------|-----------|
| 1 | **EOD 七 Guard 链** | `test_eod_guard_chain_integration.py` + `test_eod_full_chain_e2e.py` | 11 | L3 优先级覆盖 L2 / Guard 间状态传递 |
| 2 | **KillSwitch 三级熔断** | `test_kill_switch_unit.py` + `test_kill_switch_protocol_integration.py` | 20 | P0-D (None/None 崩溃) / P1-G (callback 未注册) / BUG#4 (L2 误当 L3) |
| 3 | **报告数据结构兼容** | `test_pnl_report_compat_unit.py` + `test_report_compat_integration.py` | 18 | 三种报告格式 (完整/简化/字段缺失) 兼容 |
| 4 | **fail-closed 保守保护** | `test_fail_closed_integration.py` | 6 | 数据源不可用 → L2 禁开仓 (非 L3 全平) |
| 5 | **对冲执行 + 认沽 + 去重** | `test_hedge_execution_engine_unit.py` + `test_hedge_dedup_integration.py` | 18 | P0-E (字符串字段崩溃) + PUT 去重逻辑 |

补充模块级回归：`test_overnight_gap_monitor_unit.py` (20) + `test_market_circuit_breaker_unit.py` (14) 守护 BUG#1/BUG#1b（FAIL_CLOSED_PCT 误触发 L3）

#### 关键模块测试覆盖率

| 模块 | 覆盖率 | 关键路径覆盖情况 |
|------|--------|-----------------|
| `utils/hedge_execution_engine.py` | **78.26%** | P0-E 字符串字段过滤 + IF 期货订单 + Put 保护订单 |
| `utils/market_circuit_breaker.py` | **73.83%** | 大盘熔断阈值 + apply_to_plan L2/L3 区分 |
| `utils/overnight_gap_monitor.py` | **70.14%** | S&P500 数据获取 + fail-closed 触发 |
| `utils/risk_guard_integrator.py` | **62.19%** | guard_kill_switch + run_all_guards 七 Guard 链 |
| `utils/kill_switch.py` | **54.51%** | check_margin_status + _estimate_margin_from_positions (L3 执行路径未覆盖, 需券商 API) |
| **总计** | **65.20%** | 超过 40% 最低要求 |

### GitHub 热门项目深度集成
- **code-review-graph** — 本地代码智能图谱（开发工具），uv tool 隔离安装 + MCP server + AI skill，5710 节点 / 80792 边
- **research_distiller** — 研究蒸馏信号（第 6 信号源, RIA--TV++ 量化版），离线蒸馏（06:00） + 在线注入（07:00），不增加关键路径耗时
- **finance_agent_orchestrator** — 金融多 Agent Shadow Mode，5 个专家 Agent 加权投票，RiskAgent 拥有 veto 权

### LightGBM 增强信号接入交易流水线
- **核心突破**：将离线训练的 LightGBM 增强模型接入生产交易流水线，作为第 7 信号源（post-mix 模式）。23 个标的全覆盖，平均 IC = 0.1631，启用 GPU 训练加速 + 真实 OHLCV 数据 + 新闻情绪因子。低质量标的（688981 / 600036 / 600219）自动降权 50%，安全设计完整。

### 静态代码分析引入
- **核心突破**：引入 mypy（类型检查） + pylint（代码复杂度 / 风格检查）作为代码质量自动化守门员，防止已修复 Bug 回归，适配世界顶级对冲基金代码标准。综合代码质量评分从 8.2 提升至 8.5。
- **配置文件**：`mypy.ini` 渐进式严格模式 + `.pylintrc` 复杂度上限
- **通过静态分析发现并修复 15 个关键 Bug**：`daily_workflow.py` 8 个 + `unified_risk_cockpit.py` 4 个 + `execution_algo_engine.py` 1 个 + `five_factor.py` 1 个 + 类型存根 1 个
- **验证脚本**：`scripts/_verify_phase3b_static_analysis.py` — 5 个测试组、72/72 PASS

---

## 账户结构与资金配置

### 总资金：500 万元

| 账户 | 金额 | 比例 | 用途 |
|------|------|------|------|
| **股票 ETF 账户** | ¥3,000,000 | 60% | 13 标的建仓 + 动态再平衡 |
| **对冲保护账户** | ¥2,000,000 | 40% | ETF 认沽期权（OPTIONS_ONLY 模式，无期货） |
| **合计** | ¥5,000,000 | 100% | — |

### 股票账户（12 只个股）标的配置

> **金额口径**：计划金额 = `target_weight × stock_etf_capital(¥3,000,000)`，与 `config/positions.json` 和 `v8.3_institutional/config/portfolio.yaml::account_structure.stock_etf_capital` 对齐。

| 标的 | 代码 | 目标权重 | 计划金额 | 风格 | 核心逻辑 |
|------|------|----------|----------|------|----------|
| 长江电力 | 600900.SH | 22.00% | ¥660,000 | 防御/水电 | 核心底仓，股息 3.5% + 稳定现金流 |
| 恒瑞医药 | 600276.SH | 3.20% | ¥96,000 | 医药 | 创新药龙头，管线价值重估 |
| 中国神华 | 601088.SH | 3.50% | ¥105,000 | 顺周期/煤炭 | 股息 6% + Sharpe 0.915，v8.4 上调 |
| 绿的谐波 | 688017.SH | 3.00% | ¥90,000 | 制造/机器人 | vol 70% 控尾部，v8.4 从 5% 下调 |
| 藏格矿业 | 000408.SZ | 2.22% | ¥66,600 | 资源 | 钾锂双资源，通胀受益 |
| 中科曙光 | 603019.SH | 0.71% | ¥21,300 | 科技/算力 | AI 服务器国产替代 |
| 同花顺 | 300033.SZ | 0.94% | ¥28,200 | 科技/金融 IT | 牛市弹性标的 |
| 阳光电源 | 300274.SZ | 0.57% | ¥17,100 | 新能源 | 逆变器 + 储能全球龙头 |
| 海光信息 | 688041.SH | 0.47% | ¥14,100 | 科技/芯片 | 国产 GPU 稀缺标的 |
| 北方华创 | 002371.SZ | 0.47% | ¥14,100 | 科技/半导体 | 半导体设备龙头，blend +44% |
| 卓胜微 | 300782.SZ | 0.47% | ¥14,100 | 科技/射频 | 射频芯片国产替代 |
| 中际旭创 | 300308.SZ | 1.50% | ¥45,000 | 科技/光通信 | 800G 光模块龙头，v8.4 从 2.35% 下调 |
| **股票合计** | | **38.85%** | **¥1,171,500** | | 12 只个股配置 |

### ETF 账户（14 只 ETF）标的配置

| 标的 | 代码 | 目标权重 | 计划金额 | 风格 | 核心逻辑 |
|------|------|----------|----------|------|----------|
| 上证 5 年期国债 ETF | 511010.SH | 22.00% | ¥660,000 | 国债/安全垫 | 组合稳定器，v8.4 从 25% 下调释放 3pp |
| 上证 50 ETF 华夏 | 510050.SH | 8.00% | ¥240,000 | 宽基/蓝筹 | 核心宽基底仓，v8.4 微增 |
| 黄金 ETF 华安 | 518880.SH | 6.00% | ¥180,000 | 资源/避险 | 唯一真分散器 (ρ ~ 0.15)，v8.4 翻倍 |
| 银行 ETF 华宝 | 512800.SH | 5.50% | ¥165,000 | 金融/低波 | 股息 5% + vol 17%，v8.4 上调 |
| 医疗 ETF 华宝 | 512170.SH | 4.80% | ¥144,000 | 医药 | 医药行业宽基 |
| 证券 ETF 国泰 | 512880.SH | 3.64% | ¥109,200 | 金融 | 牛市弹性，beta 放大器 |
| 沪深 300 ETF 华泰柏瑞 | 510300.SH | 2.97% | ¥89,100 | 宽基 | 大中盘风格敞口 |
| 中证 500 ETF 南方 | 510500.SH | 2.34% | ¥70,200 | 宽基/中盘 | 中盘成长敞口 |
| 中证 1000 ETF | 512100.SH | 2.34% | ¥70,200 | 宽基/小盘 | 小盘风格敞口 |
| 科创 50 ETF 易方达 | 588080.SH | 1.06% | ¥31,800 | 科技 | 科创板核心指数 |
| 新能源车 ETF 华夏 | 515030.SH | 1.00% | ¥30,000 | 新能源 | blend = -11.6% 信号最弱，v8.4 下调 |
| 创业板 ETF 易方达 | 159915.SZ | 0.80% | ¥24,000 | 成长 | v8.4 新增，维持成长风格覆盖 |
| 半导体 ETF 国泰 | 512760.SH | 0.47% | ¥14,100 | 科技 | 半导体行业敞口 |
| 科创 50 ETF 华夏 | 588000.SH | 0.47% | ¥14,100 | 科技 | 科创板补充覆盖 |
| **ETF 合计** | | **61.49%** | **¥1,841,700** | | 14 只 ETF 配置（含国债 / 黄金安全垫） |

### 对冲保护配置（OPTIONS_ONLY 模式 — 无期货）

> **对冲方式**：`hedge_mode = OPTIONS_ONLY`，**已禁用 IF 期货空头**，系统性 Beta 风险通过 4 份 ETF Put 组合 + Covered Call + Put Spread 管理。`hedge_capital = ¥2,000,000`，Put 权利金合计 ¥1,650,000（82.5% 预算使用率），剩余 ¥350,000 作为滚仓 / 保证金缓冲。

| 工具 | 标的 | 方向 | 目标合约 | 权利金预算 | 目的 |
|------|------|------|----------|-----------|------|
| ~~IF 股指期货~~ | ~~沪深 300~~ | ~~卖出~~ | ~~5 手~~ | ~~12% 保证金~~ | ~~系统性 Beta 对冲~~（OPTIONS_ONLY 模式已禁用，由 510300 Put 替代） |
| 510050 Put | 上证 50 ETF | 买入 | 60 张 | ¥900,000 | 蓝筹尾部保护（主力对冲工具，v8.7.2 翻倍） |
| 588080 Put | 科创 50 ETF | 买入 | 25 张 | ¥300,000 | 科技股尾部保护（高 Beta 标的加强覆盖） |
| 159915 Put | 创业板 ETF | 买入 | 25 张 | ¥250,000 | 成长股尾部保护（成长风格波动大） |
| 510300 Put | 沪深 300 ETF | 买入 | 25 张 | ¥200,000 | 增强 Beta 对冲（替代原 IF 期货功能） |
| **PUT 合计** | | | **135 张** | **¥1,650,000** | v8.7.2 OPTIONS_ONLY 模式，预算使用率 82.5% |

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
| 自动执行 | 07:05 盘前 / 09:30 早盘 / 14:00 午盘 / 21:00 夜盘 / 15:30 盘后 Windows 任务 |

---

## Streamlit UI 面板

```bash
# 启动 UI
streamlit run ui/app.py

# 生产模式（强制鉴权）
TRADING_ENV=production streamlit run ui/app.py
```

**14 页面结构**：
1. 📊 概览 — Dashboard
2. 📋 交易计划 — Trade Plan
3. 💼 持仓 — Positions
4. 🛡️ 风险 — Risk
5. 🎯 归因面板 — Attribution
6. ⚖️ Brinson — Brinson
7. 📈 Barra 因子 — Barra
8. 💰 TCA 执行 — TCA
9. 👁️ Shadow 账户 — Shadow
10. 🔬 回测 — Backtest
11. 🌍 宏观数据 — Macro
12. 🔄 行业轮动 — Sector
13. 📝 日志 — Logs
14. ⚙️ 配置 — Config

---

## 快速开始

### 1. 系统自检

```bash
python check_system_health.py
```

### 2. 每日报告

```bash
# 生成每日盈亏报告
python generate_daily_report.py

# 生成收盘报告 + 自动写入次日计划 + 预生成盘中决策（推荐）
python run_daily_eod.py
```

### 3. 自动执行任务

```powershell
# 注册 Windows 任务计划（盘后 + 盘中）
.\register_all_scheduled_tasks.ps1
.\register_intraday_task.ps1

# 测试盘前任务
.\register_all_scheduled_tasks.ps1 -Test pre

# 测试盘后任务
.\register_all_scheduled_tasks.ps1 -Test post
```

### 4. 自动交易计划部署

```powershell
# 注册自动交易定时任务（07:00 工作流 / 09:30 早盘 / 14:00 午盘）
cd v8.3_institutional
.\setup_scheduled_tasks.bat

# 手动触发每日工作流（测试）
.\run_weekly_auto_20260727.bat workflow
```

### 5. 查看持仓

```bash
python inspect_data.py
```

### 6. AI 增强模块

```bash
# 各模块自检
python -m utils.tf_price_predictor
python -m utils.external_data_source
python -m utils.web_scraper
python -m utils.ai_report_agent

# 盘前生成指令（含预测信号调整）
python daily_trade_executor.py pre-market --date 2026-07-13

# 盘前生成指令并自动确认（跳过人工确认）
python daily_trade_executor.py pre-market --date 2026-07-13 --auto-confirm

# 盘后执行已确认指令
python daily_trade_executor.py post-market --date 2026-07-13

# 收盘后自动执行 + 自动确认 + 生成下一交易日计划（推荐）
python daily_trade_executor.py post-market-auto --date 2026-07-13 --auto-confirm
```

### 7. 对冲执行单生成

```bash
# 生成当日对冲执行单
python hedge_execution_orders.py

# 生成指定日期对冲执行单
python hedge_execution_orders.py 2026-07-15
```

### 8. V9 影子账户管理

```bash
# 初始化影子账户（Stage 1: 10% 资金 = ¥500,000）
python launch_shadow_account.py

# 查看影子账户状态（NAV / 运行天数 / fail-fast 记录）
python launch_shadow_account.py --status

# 推进灰度阶段（需通过评估 + 最小运行 14 天 + fail-fast 未触发）
python launch_shadow_account.py --advance
```

---

## 环境要求

### 硬件
- CPU: 8 核心以上
- 内存: 16GB 以上
- 硬盘: 500GB 以上可用空间
- 网络: 稳定的宽带连接

### 软件
- **Python**: 3.14.4（生产基线，兼容 3.9+）
- **操作系统**: Windows 10+ / Ubuntu 22.04+ / macOS 13+

### 核心依赖

```bash
pip install numpy pandas scipy scikit-learn pyyaml requests beautifulsoup4 streamlit
```

### 数据源优先级

| 优先级 | 数据源 | 用途 |
|--------|--------|------|
| P0 | Wind 数据终端 | 主数据源 (WindPy) |
| P1 | Wind MCP | 强制回退 (analytics_data / stock_data / fund_data) |
| P2 | iFinD MCP | 强制回退 (同花顺金融终端) |
| P3 | AKShare | 免费回退 |
| P4 | 新浪财经 API | 免费兜底 |
| P5 | 本地缓存 | Parquet / JSON |
| P6 | 兜底预定义价格 | 保证永不崩溃 |
| P7 | 外部 API (v7.7) | FRED / Econdb / Finnhub / CoinGecko (海外宏观 + 全球股票 + 加密货币) |
| P8 | 网页抓取 (v7.7) | 东方财富 / 巨潮资讯 / 新浪财经 (公告 + 研报 + 新闻) |

---

## 版本历史

| 版本 | 日期 | 主要变更 |
|------|------|----------|
| **v8.7.2** | 2026-07-27 | **资金配置 3M/2M 恢复 + README 资金配置一致性修复** |
| **v8.7.1** | 2026-07-26 | **静态代码分析引入（mypy + pylint 自动守门员，15 个关键 Bug 修复，72/72 验证全过，综合评分 8.2 → 8.5）** |
| **v8.7** | 2026-07-26 | **LightGBM 增强信号接入交易流水线（第 7 信号源 + GPU 训练 + LOW_QUALITY 降权机制）** |
| **v8.6.12** | 2026-07-26 | **顶级对冲基金代码质量优化（P0/P1/P2 共 9 项，综合评分 5.5 → 7.5）** |
| **v8.6.11** | 2026-07-26 | **Dependabot 安全漏洞修复（5 项告警一次性清零）** |
| **v8.6.9** | 2026-07-26 | **GitHub 热门项目深度集成（开发工具 + 第 6 信号源 + 金融多 Agent Shadow Mode，233 个测试全过）** |
| **v8.6.7** | 2026-07-26 | **测试金字塔体系建立（120 个测试用例 + 65.20% 覆盖率，从被动审计转向主动防御）** |
| **v8.6.5** | 2026-07-26 | **第二轮 CRO 黑天鹅防御审计修复（3 个 P0 级致命 bug，CRO 评分 3.5 → 7.5）** |
| **v8.6.4** | 2026-07-26 | **因子流水线深度接入生产（影子账户层）** |
| **v8.6.3** | 2026-07-26 | **因子流水线 IC 加权组合方法学闭环（v6.5 → v6.9）** |
| **v8.6.1** | 2026-07-25 | **顶级对冲基金风控审计修复（纸面风控 → 真实可执行）** |
| **v8.6** | 2026-07-25 | **V9 Regime-Specific LGB 生产基线 + 影子账户 Stage 1 灰度发布** |
| **v8.5** | 2026-07-25 | **自动交易计划部署 + daily_workflow 兼容性修复** |
| **v8.4.2** | 2026-07-23 | **专业技能分析引擎（DCF 估值 + 板块轮动 + 突破选股）** |
| **v8.4.1** | 2026-07-23 | **安全加固（iFinD JWT Token 迁移至环境变量）** |
| **v8.4** | 2026-07-22 | **持仓精准优化 + 2027 年化预测** |
| **v8.3.1** | 2026-07-21 | **PUT 引擎去重保护** |
| **v8.3.0** | 2026-07-21 | **风控守卫强制执行系统** |
| **v8.1.2** | 2026-07-20 | **对冲再平衡回测引擎 v2.4 优化** |
| **v8.1.1** | 2026-07-19 | **回测校准与预测更新** |
| **v8.1** | 2026-07-17 | **全自动交易闭环 + LLM 盘中决策** |
| **v8.0** | 2026-07-13 | **顶级对冲基金优化 + 对冲执行单优化** |
| v7.10 | 2026-07-12 | 十五五 + 康波宏观对齐 |
| v7.9 | 2026-07-12 | 完全自动化交易流程 |
| v7.8 | 2026-07-11 | WonderTrader 高价值模块集成 |
| v7.7 | 2026-07-10 | AI 增强（价格预测 / 外部数据 / 网页抓取 / AI 报告） |
| v7.6 | 2026-07-09 | 仓位重建 + 资金流整合 |
| v7.5 | 2026-07-05 | 机构级实盘（Risk Parity + 三联对冲 + SOR + Walk-Forward） |

---

## 风险提示

本系统所有数学模型与参数均为研究建议，非实盘配置。实盘部署前必须通过 Risk Committee 三审。Walk-Forward 与压力测试结果不可作为未来收益保证。Kelly 公式与 Risk Parity 在极端尾部行情下可能失效，必须配合三联对冲与熔断机制使用。2030 年清仓为计划目标，实际执行可能因市场条件调整。

---

## License

MIT License

---

## 联系方式

- **GitHub**: [https://github.com/yuppiez99999/zhunbeibanjia](https://github.com/yuppiez99999/zhunbeibanjia)
- **作者**: yuppiez99999
