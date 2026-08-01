# 量化交易系统 v8.4 — 修复 Bug 和优化代码提示词

> **用途**: 粘贴到 Claude Code / Cursor / Trae 等 AI Coding 工具，快速启动修复/优化任务
> **项目根目录**: `E:\各种PY程序\28-终极量化交易系统8.4`
> **生成日期**: 2026-08-01

---

## ⚡ 快捷指令（直接复制使用）

### 紧急修复（立即执行）

```
# BUG-01~07 已修复，请确认修复状态
请读取以下文件并验证 CRITICAL bug 是否已正确修复：
- institutional_pipeline_runner.py（BUG-01 回撤符号/06 KillSwitch/05 trades同步）
- utils/risk_metrics.py（BUG-03 VaR fail-closed）
- utils/risk_constraints.py（BUG-04 板块压缩循环收敛）
- v8.3_institutional/src/bridges/wind_mcp.py（BUG-07 Wind MCP 桥接）
- v8.3_institutional/src/data/futures_prices.py（BUG-07 iFinD 路径探测）

如发现未修复，执行修复并生成验证报告。
```

### Phase B-1 剩余任务

```
# B1.3 修复 MAX_DRAWDOWN_LIMIT 不一致
项目中有 6 处 MAX_DRAWDOWN_LIMIT 硬编码，其中 utils/quant_neutral_runner.py:99 为 0.08，其余为 0.15：
- utils/quant_neutral_runner.py:99
- alpha_hedge_engine.py:39
- research/annual_return_forecast.py:47
等 6 处

任务：
1. 在 config/risk_params.yaml 中新增 max_drawdown_limit: 0.15
2. 在 utils/config_manager.py（或类似配置管理模块）中新增读取方法
3. 将 6 处硬编码替换为配置读取
4. 验证：ruff 通过 + 单元测试不回归

# B1.4 B1.5 B1.6 已完成（2026-07-30）
已删除 generate_daily_report.py 2 副本、daily_trade_executor.py ms_strategy 副本、autolearn_trainer.py 副本。
```

### Phase B-2 性能优化

```
# B2.1 新增并发工具
在 utils/concurrency.py 新增 run_io_batch() 通用并发 helper：
def run_io_batch(items, fn, *, max_workers=8, timeout=30, fail_default=None, progress_cb=None)
覆盖：成功/超时/降级/进度回调

# B2.2 institutional_pipeline_runner 4 方法并发化
以下 4 个方法存在串行 for symbol in self.ctx.symbols N+1 瓶颈：
- _real_market_regime_signals
- _real_style_rotation_signals
- _real_micro_regime_signals
- _real_cross_market_signals
+ _historical_cache 未命中时未回填

任务：
1. 用 run_io_batch 并发化上述 4 个方法
2. cache 未命中后立即回填到 _historical_cache

# B2.3 _preload_historical_data 符号去重 + 并发
符号列表 self.ctx.symbols / POSITION_SYMBOLS / _CROSS_MARKET_PROXY_SYMBOLS 存在大量重复。
任务：合并去重后用 run_io_batch(max_workers=8, timeout=120) 并发加载

# B2.4 daily_hedge_update 两循环并发化
daily_hedge_update.py 两处 for 循环（K线+实时行情）串行。
任务：用 run_io_batch 并发化

# B2.5 daily_trade_executor _load_prediction_prices 改为单次扫描
O(N×M) 重复 json.load。
任务：一次性加载所有 daily_pnl_report_*.json 构建内存索引 O(M)
```

### Phase B-3 God Class 拆分

```
# B3.1 DailyWorkflow God Class 拆分（8943行→多个 <400行文件）
⚠️ B3.1.1 已完成（语法修复，13个phase文件 py_compile 通过）
⚠️ B3.1.4 待完成（14个 phase 文件 import + self→workflow 替换）

当前状态：phases/ 目录 13 个 phase 文件语法正确，但：
- 内部未定义符号（json/BASE_DIR/HedgeCoordinator/math/datetime/MockBroker）
- self. 引用未替换为 workflow
- workflow_orchestrator.py import 断裂

待执行步骤（B3.1.2~B3.1.8）：
1. 创建 config/workflow_config.py 迁移 WorkflowConfig/CircuitLevel
2. 创建 _globals.py 集中导出模块级常量
3. 修复 14 个 phase 文件 import + self→workflow 替换
4. 修复 workflow_orchestrator.py 断裂 import + 函数名
5. 统一 phase 函数签名为 (workflow) -> bool
6. 让 daily_workflow.py 变 thin wrapper 调用 phases/
7. E2E 测试验证

# B3.2 PortfolioAnalyzer（2200行→4个<400行子模块）
拆为：
- reporting/pnl_calculator.py
- reporting/hedge_analyzer.py
- ai/recommendation_generator.py
- reporting/markdown_renderer.py

# B3.3 HedgeEngine（1851行→5个<400行子模块）
拆为：
- data/futures_prices.py
- risk/portfolio_risk.py
- hedging/futures_hedger.py
- hedging/options_hedger.py
- risk/correlation.py

# B3.4 LLMRouter（1110行→6个<300行provider）
拆为：
- llm/providers/deepseek.py
- llm/providers/glm.py
- llm/providers/doubao.py
- llm/providers/siliconflow.py
- llm/providers/ollama.py
- llm/providers/omniroute.py
删除：omni_route_client.py, ai_decision/providers.py, v8.3_institutional/src/ai/llm_client.py, generate_daily_report.py:_call_deepseek

# B3.5 lgb_enhanced_trainer.py（2440行→多个<400行子模块）
拆为：
- ml/features/{mean_reversion,regime_aware,industry_relative,capital_flow,cross_market,sentiment}.py
- ml/data/ohlcv_loader.py
- ml/trainers/lgb_enhanced.py
- ml/evaluation/metrics.py
- ml/reporting/comparison_report.py
```

### Phase B-4 工具函数集中化

```
# B4.1 抽取无代理 Session 工厂
8+ 处独立创建 no-proxy Session（_SINA_SESSION/_DEEPSEEK_SESSION）。
新建 utils/http_session.py::make_no_proxy_session(name)，8+ 处统一调用。

# B4.2 抽取 STYLE_BETA_PROXY 字典
5 处独立定义（hedge_quantity_calculator.py:43、today_hedge_decision.py:72 等）。
新建 utils/risk/style_beta.py::STYLE_BETA_PROXY + get_style_beta(style)。

# B4.3 抽取 _safe_urlopen URL安全检查
4 处独立定义。
新建 utils/http_security.py::_safe_urlopen。

# B4.4 统一 DEEPSEEK_BASE_URL 格式
4 处独立定义且格式不一致。
统一到 utils/alpha/llm_router.py 一处定义。

# B4.5 抽取风控参数到配置
DAILY_AMOUNT_LIMIT/PRICE_PROTECTION_PCT/DAILY_LOSS_STOP_PCT/PORTFOLIO_DRAWDOWN_STOP_PCT。
新建 config/trade_execution.yaml，ConfigManager 读取。

# B4.6 抽取 INDEX_WEIGHTS/INDEX_FUTURES_SPECS
hedge_engine_v59.py:25-97 硬编码。
移到 config/index_specs.yaml。
```

### Phase C-1 测试覆盖

```
# C1.x 单元测试补齐（关键模块 0%→50%+ 覆盖率）
需新增测试文件：
- tests/unit/test_daily_workflow_unit.py（≥40%）
- tests/unit/test_daily_trade_executor_unit.py（≥60%，覆盖金额校验/防除零/止损止盈）
- tests/unit/test_automated_execution_system_unit.py（≥50%）
- tests/unit/test_hedge_engine_v59_unit.py（≥50%，覆盖Beta/期货/期权/相关性）
- tests/unit/test_hedge_rebalance_v59_unit.py（≥50%）
- tests/unit/test_signal_fusion_v59_unit.py（≥60%，覆盖Spearman IC/IC_IR/动态权重）
- tests/unit/test_stop_loss_monitor_unit.py（≥60%，覆盖波动率调整止损）
- tests/unit/test_daily_build_and_hedge_unit.py（≥50%）
- tests/unit/test_data_provider_unit.py（≥50%，覆盖多源降级）
- tests/unit/test_trade_plan_validator_unit.py（≥60%）
```

### Phase C-2 CI 流水线

```
# C2.1 修正覆盖率阈值
.coveragerc / pytest.ini / ci.yml 当前 --cov-fail-under=60，实际仅 21.95%。
改为 --cov-fail-under=22 + 启用趋势检测（下降>2%阻断）

# C2.2 CI 新增 ruff + bandit + vulture
.github/workflows/quality.yml 新增 quality job

# C2.3 集成 codecov
codecov/codecov-action@v4

# C2.4 矩阵测试 Python 3.11 + 3.12

# C2.5 修复 v8.3_institutional/tests/ 未被 pytest 收集
pytest.ini 新增 v8.3_institutional/tests 到 testpaths
```

### Phase C-3 数据契约

```
# C3.1~C3.5 JSON Schema 定义
tests/schemas/ 目录下新增 5 个 schema：
- positions.schema.json
- trade_plan.schema.json
- hedge_decision.schema.json
- system_config.schema.json
- daily_pnl_report.schema.json
```

---

## 📂 关键文件路径速查

| 用途 | 路径 |
|------|------|
| 主 Pipeline | `institutional_pipeline_runner.py` |
| 日内执行器 | `daily_trade_executor.py` |
| 风控指标 | `utils/risk_metrics.py` |
| 风控约束 | `utils/risk_constraints.py` |
| KillSwitch | `utils/kill_switch.py` |
| 日报生成 | `generate_daily_report.py` |
| 日度对冲 | `daily_build_and_hedge.py` |
| 动态风控 | `daily_hedge_update.py` |
| HedgeEngine | `v8.3_institutional/src/hedging/hedge_engine_v59.py` |
| LLMRouter | `utils/alpha/llm_router.py` |
| LightGBM训练 | `lgb_enhanced_trainer.py` |
| 配置目录 | `config/` |
| 测试目录 | `tests/` |
| phases目录 | `v8.3_institutional/daily_workflow/phases/` |
| 持仓加载 | `utils/positions_loader.py`（新增共享模块） |
| 并发工具 | `utils/concurrency.py` |

## 🔧 常用验证命令

```bash
# 语法检查
python -m py_compile <file.py>

# ruff 检查
cd E:\各种PY程序\28-终极量化交易系统8.4
ruff check .

# 测试覆盖
pytest --cov=. --cov-report=term-missing --cov-fail-under=22

# smoke 测试
pytest tests/smoke/ -v

# E2E 测试
pytest tests/e2e/ -v

# mypy 检查
mypy . --config-file=mypy.ini
```

## 📋 已完成状态一览

| 任务 | 状态 | 完成日期 |
|------|------|---------|
| BUG-01 回撤熔断器符号 | ✅ | 2026-07-31 |
| BUG-03 VaR fail-closed | ✅ | 2026-07-31 |
| BUG-04 板块压缩循环收敛 | ✅ | 2026-07-31 |
| BUG-05 trades 同步重建 | ✅ | 2026-07-31 |
| BUG-06 KillSwitch fail-closed | ✅ | 2026-07-31 |
| BUG-07 Wind MCP + iFinD | ✅ | 2026-07-31 |
| B1.1 phase_*.py 语法错误 | ✅ | 2026-07-30 |
| B1.2 is_trading_day 统一 | ✅ | 2026-07-30 |
| B1.4 删除 generate_daily_report 副本 | ✅ | 2026-07-30 |
| B1.5 删除 daily_trade_executor 副本 | ✅ | 2026-07-30 |
| B1.6 合并 autolearn_trainer 副本 | ✅ | 2026-07-30 |
| B1.7 抽取 load_positions() 共享 | ✅ | 2026-07-30 |
| B3.1.1 清理 dead code + 语法修复 | ✅ | 2026-07-30 |
| **B1.3 MAX_DRAWDOWN_LIMIT 统一** | 🔲 待执行 | - |
| **B2.1~B2.5 性能优化** | 🔲 待执行 | - |
| **B3.1.2~B3.1.8 DailyWorkflow 拆分** | 🔲 待执行 | - |
| **B3.2~B3.5 God Class 拆分** | 🔲 待执行 | - |
| **B4.1~B4.6 工具函数集中化** | 🔲 待执行 | - |
| **C1.1~C1.10 测试覆盖** | 🔲 待执行 | - |
| **C2.1~C2.5 CI 流水线** | 🔲 待执行 | - |
| **C3.1~C3.5 数据契约** | 🔲 待执行 | - |

## 💡 使用建议

1. **单次任务**：直接复制对应「## ⚡ 快捷指令」区块粘贴给 AI
2. **批量任务**：复制整个提示词文件，AI 自动识别优先级执行
3. **每次修改前**：先读取对应 STATUS.md 了解当前状态
4. **每次修改后**：执行 `ruff check` + `pytest tests/smoke/ -v` 验证
5. **God Class 拆分**：涉及核心交易逻辑，务必每次拆分后执行 E2E 测试

---

*生成：2026-08-01 | 来源：docs/优化_2026-07-30/OPTIMIZATION_PLAN.md + docs/BUG_CHECK_REPORT_2026-07-31.md*
