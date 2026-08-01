# 28-终极量化交易系统8.4 优化执行计划

**生成日期**：2026-07-30
**前置**：基于 Phase A/B/C 已建立的代码质量门禁（见 [CODE_QUALITY_PHASE_A_2026-07-30.md](../../CODE_QUALITY_PHASE_A_2026-07-30.md) 与 [CODE_REVIEW_REPORT_2026-07-30.md](../../CODE_REVIEW_REPORT_2026-07-30.md)）
**扫描范围**：根目录核心模块 + `v8.3_institutional/src/` + `utils/` + `ai_decision/` + 配置/测试/CI
**执行方式**：先生成计划 → 用户审批 → 按 P0→P1→P2 逐步执行，每步独立验证

---

## 一、扫描结果摘要

| 维度 | 关键发现 |
|------|---------|
| **性能瓶颈** | 5 处串行 N+1 网络循环（institutional_pipeline_runner 单文件 5 处）；2 处 N×M 重复文件 IO；3 处 iterrows 全表遍历；1 处 cache 未回填导致 4× 重复拉取 |
| **架构债务** | 5 个 God Class（最大 8943 行/98 方法）；3 组完全复制文件副本（5710+2580+autolearn）；22 处 `load_positions()` 重复；5+ 处 LLM 客户端重复（URL 格式不一致） |
| **功能性 Bug** | `phase_hedge.py:23` 语法错误导致 phase 拆分未完成；`is_trading_day` 2 处简陋实现（节假日误判）；`MAX_DRAWDOWN_LIMIT` 6 处硬编码含 1 处 0.08 不一致 |
| **测试覆盖** | 实际 21.95% vs CI 阈值 60%（形同虚设）；Top 10 关键模块 0 测试；`v8.3_institutional/tests/` 12 个测试文件未被 pytest 收集；参数化测试仅 6.7% |
| **CI 缺口** | 本地 pre-commit 与 CI 工具集几乎不重叠；CI 缺 ruff/bandit/vulture；无 codecov 上传；无矩阵测试；TDD Guard 仅检查测试文件存在性 |
| **数据契约** | 仅 ML panel 有契约；`positions.json`/`trade_plan_*.json`/`hedge_decision_*.json` 等 10 类核心 JSON 文件全部无 Schema |

**核心结论**：项目已建立完整的代码质量门禁基础设施（Phase A），但存在**5 个 God Object、3 组完全复制副本、5 处性能 N+1 瓶颈、3 个功能性 bug**，需要进入 Phase B（架构重构）+ Phase C（测试与 CI 补齐）。

---

## 二、优化目标

| 指标 | 当前 | 目标 | 验收方式 |
|------|------|------|---------|
| 最大单文件行数 | 8943 行（daily_workflow.py） | ≤ 800 行 | `wc -l` + ruff |
| God Class 数 | 5（>800 行） | 0 | ruff + 人工审查 |
| 重复代码块 | 22+8+5+4+5+3+2+4 = 53 处 | ≤ 5 处 | grep + 人工审查 |
| 关键模块测试覆盖 | 0%（Top 10 模块无测试） | ≥ 60% | pytest --cov |
| 全项目测试覆盖率 | 21.95% | ≥ 50% | pytest --cov + codecov |
| CI 工具覆盖 | ruff/bandit/vulture 缺失 | 全部上 CI | .github/workflows/quality.yml |
| 数据契约 JSON Schema | 0 类 | 5 类（positions/trade_plan/hedge_decision/system_config/daily_pnl） | tests/unit/test_data_contract.py |
| 性能瓶颈 | 5 处 N+1 + 2 处 N×M | 0 处 | pytest-benchmark 回归 |
| 功能性 Bug | 3 处 | 0 处 | 单元测试 + 集成测试 |

---

## 三、执行计划（按优先级分批）

### Phase B-1: 紧急修复（P0 功能性 Bug + 副本删除）

> **目标**：消除阻塞性 bug 与最大量的重复代码（预计 10000+ 行）
> **预计任务数**：7 个
> **风险**：低（删除副本 + 修复明显 bug）

| # | 任务 | 涉及文件 | 输入 | 输出 | 验收标准 |
|---|------|---------|------|------|---------|
| **B1.1** | 修复 `phase_hedge.py:23` 语法错误 | `v8.3_institutional/daily_workflow/phases/phase_hedge.py` | 错误签名 `def phase_hedge(workflow)self) -> Dict[str, Any]:` | 修正为 `def phase_hedge(self) -> Dict[str, Any]:` 或 `(workflow)` | `python -c "import phase_hedge"` 成功；py_compile 通过 |
| **B1.2** | 修复 `is_trading_day` 简陋实现 | `daily_trade_executor.py:85-87`、`ms_strategy/scripts/daily_trade_executor.py:104-108` | `def is_trading_day(d): return d.weekday() < 5` | 删除本地实现，统一 `from utils.trade_calendar import is_trading_day` | 国庆/春节单元测试通过；ruff 通过 |
| **B1.3** | 修复 `MAX_DRAWDOWN_LIMIT` 不一致 | `utils/quant_neutral_runner.py:99`（0.08）、`alpha_hedge_engine.py:39`、`research/annual_return_forecast.py:47` 等 6 处 | 5 处硬编码 0.15 + 1 处 0.08 | 抽取到 `config/risk_params.yaml::max_drawdown_limit: 0.15`，通过 ConfigManager 统一读取 | 6 处统一调用；ruff 通过；现有测试不回归 |
| **B1.4** | 删除 `generate_daily_report.py` 2 副本 | `v8.3_institutional/generate_daily_report.py`（1885 行）、`ms_strategy/scripts/generate_daily_report.py`（1625 行） | 3 份完全复制副本 | 保留根目录版，删除 2 副本；其他文件改 import 路径 | 根目录版可独立运行；ruff 通过；smoke test 通过 |
| **B1.5** | 删除 `daily_trade_executor.py` 副本 | `ms_strategy/scripts/daily_trade_executor.py`（1280 行） | 2 份完全复制副本 | 保留根目录版，删除 ms_strategy 副本；改 import 路径 | 根目录版可独立运行；ruff 通过 |
| **B1.6** | 合并 `autolearn_trainer.py` 副本 | `ms_strategy/training/autolearn_trainer.py`、`v8.3_institutional/autolearn_trainer.py` | 2 份副本 | 统一到 `ml/trainers/autolearn_trainer.py`；2 处 import 改向 | py_compile 通过；smoke test 通过 |
| **B1.7** | 抽取 `load_positions()` 共享函数 | 22 处独立定义 | 各处实现略有差异（path 参数/默认值） | 新建 `utils/positions_io.py::load_positions(path=DEFAULT_POSITIONS_PATH)`；22 处统一 import | 22 处调用方均改为新函数；smoke test 通过；ruff 通过 |

---

### Phase B-2: 性能优化（P0 N+1 瓶颈）

> **目标**：消除回测/pipeline 关键路径的串行 N+1 瓶颈
> **预计任务数**：5 个
> **风险**：中（涉及网络并发，需 timeout/降级机制）

| # | 任务 | 涉及文件 | 输入 | 输出 | 验收标准 |
|---|------|---------|------|------|---------|
| **B2.1** | 新增 `utils/concurrency.run_io_batch()` 通用并发 helper | `utils/concurrency.py` | 各模块重复实现 ThreadPoolExecutor 模板 | 新增 `run_io_batch(items, fn, *, max_workers=8, timeout=30, fail_default=None, progress_cb=None)` | 单元测试覆盖：成功/超时/降级/进度回调；ruff 通过 |
| **B2.2** | `institutional_pipeline_runner._real_*_signals` 4 方法并发化 + cache 回填 | `institutional_pipeline_runner.py:1620/1763/1798/1890` + `511-546` | 4 方法各 `for symbol in self.ctx.symbols` 串行；`_historical_cache` 未命中不回填 | 用 `run_io_batch` 并发拉取；cache 未命中后立即回填 | 单次 pipeline 耗时降 3-5×；smoke test 通过；功能等价 |
| **B2.3** | `_preload_historical_data` 符号合并去重 + 并发 | `institutional_pipeline_runner.py:1192-1220` | 50+ 次 5y 历史串行加载 | 合并 `symbols`/`POSITION_SYMBOLS`/`_CROSS_MARKET_PROXY_SYMBOLS` 去重后 `run_io_batch(max_workers=8, timeout=120)` | 启动期从分钟级降到 10 秒级；功能等价 |
| **B2.4** | `daily_hedge_update` 两循环并发化 | `daily_hedge_update.py:88-98, 132-148` | 25 标的 K 线 + 实时行情串行 | 用 `run_io_batch` 并发 | 耗时降 5-8×；功能等价 |
| **B2.5** | `daily_trade_executor._load_prediction_prices` 改单次扫描 | `daily_trade_executor.py:386-460` | O(N×M) 重复 json.load | 一次性加载所有 daily_pnl_report_*.json，构建 `{symbol: [prices]}` 内存索引 | O(N×M) → O(M)；功能等价；smoke test 通过 |

---

### Phase B-3: God Object 拆分（P0 架构债务）

> **目标**：将 5 个 God Class 拆为职责单一的子模块，单文件 ≤ 800 行
> **预计任务数**：5 个（每个 God Class 1 个）
> **风险**：高（涉及核心交易逻辑，需充分测试覆盖）

| # | 任务 | 涉及文件 | 当前 | 目标 | 验收标准 |
|---|------|---------|------|------|---------|
| **B3.1** | 拆分 `DailyWorkflow` God Class | `v8.3_institutional/daily_workflow.py`（8943 行/98 方法） | 1 个 God Class 含 10 Phase | 完成 `phases/phase_xxx.py` 拆分，每 phase 独立文件 < 400 行 | 每 phase 可独立 import；E2E `test_eod_full_chain_e2e.py` 通过；smoke test 通过 |

> **B3.1 执行状态 (2026-07-30 更新)**: 拆分为 8 个子任务, 本批次完成 B3.1.1 清理
>
> - ✅ **B3.1.1** 清理 dead code: 删除根 `daily_workflow.py` 9 行外壳 + `create_workflow_structure.py` + `extract_single.py` (3 个文件均无人使用)
> - ⚠️ phases/ 目录已存在但不可运行 (30+ 未导入依赖 + 6 个文件 `self.` bug + 3 处断裂 import), 在 `phases/__init__.py` 标注警告
> - 🔲 B3.1.2: 创建 `config/workflow_config.py` 迁移 WorkflowConfig/CircuitLevel
> - 🔲 B3.1.3: 创建 `_globals.py` 集中导出模块级常量
> - 🔲 B3.1.4: 修复 14 个 phase 文件 import + `self→workflow` 替换
> - 🔲 B3.1.5: 修复 `workflow_orchestrator.py` 断裂 import + 函数名
> - 🔲 B3.1.6: 统一 phase 函数签名为 `(workflow) -> bool`
> - 🔲 B3.1.7: 让 `daily_workflow.py` 变 thin wrapper 调用 phases/
> - 🔲 B3.1.8: E2E 测试验证
>
> **风险说明**: 8943 行 God Class 涉及核心交易逻辑, B3.1.2-B3.1.8 需分多次执行, 每步需 E2E 回归
| **B3.2** | 拆分 `PortfolioAnalyzer` God Class | `generate_daily_report.py`（2200 行/21 方法） | 1 个 God Class 含 PnL/对冲/AI 推荐/Markdown | 拆为 `reporting/pnl_calculator.py`、`reporting/hedge_analyzer.py`、`ai/recommendation_generator.py`、`reporting/markdown_renderer.py` | 单文件 ≤ 400 行；smoke test 通过；ruff 通过 |
| **B3.3** | 拆分 `HedgeEngine` God Class | `v8.3_institutional/src/hedging/hedge_engine_v59.py`（1851 行/20 方法） | 1 个 God Class 含数据源/Beta/期货/期权 | 拆为 `data/futures_prices.py`、`risk/portfolio_risk.py`、`hedging/futures_hedger.py`、`hedging/options_hedger.py`、`risk/correlation.py` | 单文件 ≤ 400 行；现有 hedge 测试通过 |
| **B3.4** | 拆分 `LLMRouter` God Class + 统一 LLM 调用层 | `utils/alpha/llm_router.py`（1110 行/26 方法）+ 5 处重复实现 | 6 个 `_call_xxx` 方法 + 5 处重复 | 6 个 Provider 拆到 `llm/providers/{deepseek,glm,doubao,siliconflow,ollama,omniroute}.py`；删除 `omni_route_client.py`、`ai_decision/providers.py`、`v8.3_institutional/src/ai/llm_client.py`、`generate_daily_report.py:_call_deepseek` | 单文件 ≤ 300 行；`test_llm_router.py` 通过 |
| **B3.5** | 拆分 `lgb_enhanced_trainer.py` | `lgb_enhanced_trainer.py`（2440 行/27 函数） | 1 个文件含特征工程/训练/评估/报告 | 拆为 `ml/features/{mean_reversion,regime_aware,industry_relative,capital_flow,cross_market,sentiment}.py`、`ml/data/ohlcv_loader.py`、`ml/trainers/lgb_enhanced.py`、`ml/evaluation/metrics.py`、`ml/reporting/comparison_report.py` | 单文件 ≤ 400 行；`test_lgbm_reproducibility.py` 通过 |

---

### Phase B-4: 散落工具函数与配置集中化（P1）

> **目标**：消除剩余散落工具函数与硬编码配置
> **预计任务数**：6 个
> **风险**：低（抽取 + 替换调用方）

| # | 任务 | 涉及文件 | 输入 | 输出 | 验收标准 |
|---|------|---------|------|------|---------|
| **B4.1** | 抽取无代理 Session 工厂 | 8+ 处 `_SINA_SESSION`/`_DEEPSEEK_SESSION`/`_SESSION.trust_env=False` | 各处独立创建 Session | 新建 `utils/http_session.py::make_no_proxy_session(name)`；8+ 处统一调用 | ruff 通过；smoke test 通过 |
| **B4.2** | 抽取 `style_beta_proxy` 字典 | 5 处（`hedge_quantity_calculator.py:43`、`today_hedge_decision.py:72`、`utils/execution/automated_execution_system.py:1746` 等） | 5 处独立硬编码字典 | 新建 `utils/risk/style_beta.py::STYLE_BETA_PROXY` + `get_style_beta(style)` 函数 | 5 处统一调用；ruff 通过 |
| **B4.3** | 抽取 `_safe_urlopen` URL 安全检查 | 4 处（`utils/alpha/llm_router.py:94`、`utils/alpha/omni_route_client.py:76` 等） | 4 处独立定义 | 新建 `utils/http_security.py::_safe_urlopen`；4 处统一调用（与 B3.4 配合） | bandit B310 仍为 0；ruff 通过 |
| **B4.4** | 统一 `DEEPSEEK_BASE_URL` 格式 | 4 处定义（URL 格式不一致） | 4 处独立定义且格式不一 | 统一到 `utils/alpha/llm_router.py` 一处定义（与 B3.4 配合） | 4 处删除；smoke test 通过 |
| **B4.5** | 抽取风控参数到配置 | 3 处 `DAILY_AMOUNT_LIMIT`/`PRICE_PROTECTION_PCT`/`DAILY_LOSS_STOP_PCT`/`PORTFOLIO_DRAWDOWN_STOP_PCT` | 3 处硬编码 | 新建 `config/trade_execution.yaml`，ConfigManager 读取 | 3 处统一调用；smoke test 通过 |
| **B4.6** | 抽取 `INDEX_WEIGHTS_CSI300/CSI500` 与 `INDEX_FUTURES_SPECS` | `v8.3_institutional/src/hedging/hedge_engine_v59.py:25-97` | 硬编码常量 | 移到 `config/index_specs.yaml`（与 B3.3 配合） | smoke test 通过；ruff 通过 |

---

### Phase C-1: 测试覆盖补齐（P0 关键模块）

> **目标**：补齐 Top 10 关键模块的单元测试，覆盖率从 21.95% → 50%+
> **预计任务数**：10 个（每个关键模块 1 个）
> **风险**：低（新增测试，不改生产代码）

| # | 任务 | 涉及模块 | 当前覆盖率 | 目标覆盖率 | 验收标准 |
|---|------|---------|-----------|-----------|---------|
| **C1.1** | 补 `daily_workflow.py` 单元测试 | `v8.3_institutional/daily_workflow.py` | 0%（仅 E2E） | ≥ 40% | 新增 `tests/unit/test_daily_workflow_unit.py`；覆盖 10 个 phase 入口 |
| **C1.2** | 补 `daily_trade_executor.py` 单元测试 | `daily_trade_executor.py` | 0% | ≥ 60% | 新增 `tests/unit/test_daily_trade_executor_unit.py`；覆盖金额校验/防除零/止损止盈 |
| **C1.3** | 补 `automated_execution_system.py` 单元测试 | `utils/execution/automated_execution_system.py`（982 行） | 0% | ≥ 50% | 新增 `tests/unit/test_automated_execution_system_unit.py` |
| **C1.4** | 补 `hedge_engine_v59.py` 单元测试 | `v8.3_institutional/src/hedging/hedge_engine_v59.py`（910 行） | 0% | ≥ 50% | 新增 `tests/unit/test_hedge_engine_v59_unit.py`；覆盖 Beta/期货/期权/相关性 |
| **C1.5** | 补 `hedge_rebalance_v59.py` 单元测试 | `v8.3_institutional/src/hedging/hedge_rebalance_v59.py`（690 行） | 0% | ≥ 50% | 新增 `tests/unit/test_hedge_rebalance_v59_unit.py` |
| **C1.6** | 补 `signal_fusion_v59.py` 单元测试 | `v8.3_institutional/src/signals/signal_fusion_v59.py`（456 行） | 0% | ≥ 60% | 新增 `tests/unit/test_signal_fusion_v59_unit.py`；覆盖 Spearman IC/IC_IR/动态权重 |
| **C1.7** | 补 `stop_loss_monitor.py` 单元测试 | `stop_loss_monitor.py` | 0% | ≥ 60% | 新增 `tests/unit/test_stop_loss_monitor_unit.py`；覆盖波动率调整止损 |
| **C1.8** | 补 `daily_build_and_hedge.py` 单元测试 | `daily_build_and_hedge.py` + `utils/execution/daily_build_and_hedge.py` | 0% | ≥ 50% | 新增 `tests/unit/test_daily_build_and_hedge_unit.py` |
| **C1.9** | 补 `data_provider.py` 单元测试 | `utils/data_provider.py`（777 行） | 0% | ≥ 50% | 新增 `tests/unit/test_data_provider_unit.py`；覆盖多源降级 |
| **C1.10** | 补 `trade_plan_validator.py` 单元测试 | `utils/trade_plan_validator.py` | 0% | ≥ 60% | 新增 `tests/unit/test_trade_plan_validator_unit.py` |

---

### Phase C-2: CI 流水线补齐（P0 自动化）

> **目标**：CI 与本地 pre-commit 工具集对齐，补 codecov 与矩阵测试
> **预计任务数**：5 个
> **风险**：低（仅改 CI 配置）

| # | 任务 | 涉及文件 | 当前 | 目标 | 验收标准 |
|---|------|---------|------|------|---------|
| **C2.1** | 修正 CI 覆盖率阈值 | `.coveragerc` / `pytest.ini` / `ci.yml` | `--cov-fail-under=60` vs 实际 21.95% | 降至 `--cov-fail-under=22`（基线+1）+ 启用 `_check_coverage_trend.py` 下降 > 2% 阻断 | CI unit-tests job 通过；趋势检测生效 |
| **C2.2** | CI 增加 ruff + bandit + vulture | `.github/workflows/quality.yml`（新建） | 本地有 CI 无 | 新增 `quality.yml` job：ruff check + bandit -lll -ii + vulture --min-confidence 80 | CI 运行 ruff/bandit/vulture；HIGH=0；vulture 信息性 |
| **C2.3** | 集成 codecov | `.github/workflows/quality.yml` | 仅 upload-artifact | 新增 `codecov/codecov-action@v4`；PR 评论覆盖率变化 | PR 评论显示覆盖率变化；趋势可视化 |
| **C2.4** | 矩阵测试（Python 3.11 + 3.12） | `.github/workflows/quality.yml` | 仅 3.11 | 矩阵 `['3.11', '3.12']` | 两个版本 CI 通过 |
| **C2.5** | 修复 `v8.3_institutional/tests/` 未收集 | `pytest.ini` | `testpaths` 遗漏 | 新增 `v8.3_institutional/tests` 到 testpaths | pytest 收集 12 个新测试 |

---

### Phase C-3: 数据契约测试（P0 关键 JSON Schema）

> **目标**：为 5 类核心 JSON 文件定义 Schema，防止字段漂移引发 P0-D/P0-E 类 bug
> **预计任务数**：5 个
> **风险**：低（新增测试，不改生产代码）

| # | 任务 | 涉及 JSON | 输出 | 验收标准 |
|---|------|----------|------|---------|
| **C3.1** | `positions.json` Schema | 持仓状态文件 | `tests/schemas/positions.schema.json` + `test_data_contract_positions.py` | Schema 校验通过；P0-D 类 bug 回归测试 |
| **C3.2** | `trade_plan_*.json` Schema | 交易计划文件 | `tests/schemas/trade_plan.schema.json` + 测试 | Schema 校验通过 |
| **C3.3** | `hedge_decision_*.json` Schema | 对冲决策文件 | `tests/schemas/hedge_decision.schema.json` + 测试 | Schema 校验通过 |
| **C3.4** | `system_config.json` Schema | 系统全局配置 | `tests/schemas/system_config.schema.json` + 测试 | Schema 校验通过 |
| **C3.5** | `daily_pnl_report_*.json` Schema | 收盘报告 | `tests/schemas/daily_pnl_report.schema.json` + 测试 | Schema 校验通过；3 种格式兼容 |

---

### Phase C-4: 性能基准回归（P1 防退化）

> **目标**：为关键路径建立 pytest-benchmark 基准，防止性能退化
> **预计任务数**：3 个
> **风险**：低（仅新增基准测试）

| # | 任务 | 涉及模块 | 输出 | 验收标准 |
|---|------|---------|------|---------|
| **C4.1** | `_step_data_gate` 基准 | `institutional_pipeline_runner.py:366` | `tests/benchmark/test_data_gate_benchmark.py` | 基准时间 < X 秒；PR 退化 > 10% 阻断 |
| **C4.2** | `_preload_historical_data` 基准 | `institutional_pipeline_runner.py:1192` | `tests/benchmark/test_preload_historical_benchmark.py` | 基准时间 < X 秒 |
| **C4.3** | `_real_alpha_signals` 基准 | `institutional_pipeline_runner.py:1620` | `tests/benchmark/test_alpha_signals_benchmark.py` | 基准时间 < X 秒 |

---

## 四、执行顺序与依赖关系

```
Phase B-1 (紧急修复 + 副本删除)
  ├─ B1.1 phase_hedge.py 语法错误（独立）
  ├─ B1.2 is_trading_day 简陋实现（独立）
  ├─ B1.3 MAX_DRAWDOWN_LIMIT 不一致（独立）
  ├─ B1.4 删除 generate_daily_report.py 副本（独立）
  ├─ B1.5 删除 daily_trade_executor.py 副本（独立）
  ├─ B1.6 合并 autolearn_trainer.py 副本（独立）
  └─ B1.7 抽取 load_positions()（独立）

Phase B-2 (性能优化)
  ├─ B2.1 run_io_batch helper（独立，先行）
  ├─ B2.2 _real_*_signals 并发（依赖 B2.1）
  ├─ B2.3 _preload_historical_data 并发（依赖 B2.1）
  ├─ B2.4 daily_hedge_update 并发（依赖 B2.1）
  └─ B2.5 _load_prediction_prices 单次扫描（独立）

Phase B-3 (God Object 拆分)
  ├─ B3.1 DailyWorkflow 拆分（依赖 B1.1）
  ├─ B3.2 PortfolioAnalyzer 拆分（依赖 B1.4）
  ├─ B3.3 HedgeEngine 拆分（独立）
  ├─ B3.4 LLMRouter 拆分 + 统一 LLM 调用层（独立，包含 B4.3/B4.4）
  └─ B3.5 lgb_enhanced_trainer 拆分（独立）

Phase B-4 (散落工具函数与配置集中化)
  ├─ B4.1 无代理 Session 工厂（独立）
  ├─ B4.2 style_beta_proxy 抽取（独立）
  ├─ B4.3 _safe_urlopen 抽取（与 B3.4 合并）
  ├─ B4.4 DEEPSEEK_BASE_URL 统一（与 B3.4 合并）
  ├─ B4.5 风控参数到配置（依赖 B1.3）
  └─ B4.6 INDEX_WEIGHTS/SPECS 抽取（与 B3.3 合并）

Phase C-1 (测试覆盖补齐)
  └─ C1.1-C1.10（建议在对应 B-3 拆分完成后启动，避免测试反复改）

Phase C-2 (CI 流水线补齐)
  ├─ C2.1 修正覆盖率阈值（独立，先行）
  ├─ C2.2 quality.yml 新建（独立）
  ├─ C2.3 codecov 集成（依赖 C2.2）
  ├─ C2.4 矩阵测试（依赖 C2.2）
  └─ C2.5 pytest.ini 修复（独立）

Phase C-3 (数据契约测试)
  └─ C3.1-C3.5（独立，可并行启动）

Phase C-4 (性能基准回归)
  └─ C4.1-C4.3（依赖 B2.1-B2.5 完成）
```

---

## 五、风险与缓解

| 风险 | 影响范围 | 缓解措施 |
|------|---------|---------|
| God Object 拆分破坏现有交易逻辑 | 高（B3.1/B3.2/B3.3 涉及核心交易） | ① 每步先补集成测试再拆分；② 保留旧文件作为 re-export 兼容外壳（项目已有此模式）；③ 每步 git commit + 回滚预案 |
| 副本删除导致 import 路径断裂 | 中（B1.4/B1.5/B1.6） | ① 删除前 grep 全项目 import；② 保留兼容外壳（re-export 模式）；③ smoke test 验证 |
| 性能并发化引入死锁/资源竞争 | 中（B2.1-B2.5） | ① `run_io_batch` 内置 timeout + 降级；② 单元测试覆盖异常路径；③ benchmark 回归 |
| 测试覆盖率提升耗时长 | 低（C1.x） | ① 优先 P0 关键模块；② 使用 `@pytest.mark.parametrize` 提升效率；③ 配合 mock 隔离外部依赖 |
| CI 阈值收紧阻断现有提交 | 中（C2.1） | ① 渐进式收紧：22% → 30% → 50%；② 趋势检测替代硬阈值；③ bandit/vulture 设为信息性 |

---

## 六、验收与回归

每个任务完成后必须通过以下验证：

| 验证项 | 工具 | 命令 |
|--------|------|------|
| 编译通过 | py_compile | `python -m py_compile <file>` |
| ruff 通过 | ruff | `ruff check <file>` |
| bandit HIGH=0 | bandit | `bandit -c bandit.yaml -r <file> -lll -ii` |
| smoke test 通过 | pytest | `pytest tests/smoke -m smoke` |
| 现有测试不回归 | pytest | `pytest tests/unit tests/integration -x` |
| 关键路径 E2E | pytest | `pytest tests/e2e -m e2e` |

每完成一个 Phase（B-1/B-2/B-3/B-4/C-1/C-2/C-3/C-4）后，生成阶段性报告并提交 git commit。

---

## 七、执行统计

| Phase | 任务数 | 优先级 | 预计影响范围 |
|-------|--------|--------|------------|
| B-1 紧急修复 + 副本删除 | 7 | P0 | 删除 10000+ 行重复代码；修复 3 处功能性 bug |
| B-2 性能优化 | 5 | P0 | 5 处 N+1 瓶颈消除；pipeline 速度提升 3-8× |
| B-3 God Object 拆分 | 5 | P0 | 5 个 God Class → 20+ 个职责单一模块；单文件 ≤ 800 行 |
| B-4 散落工具集中化 | 6 | P1 | 消除 30+ 处散落定义；统一配置入口 |
| C-1 测试覆盖补齐 | 10 | P0 | 覆盖率 21.95% → 50%+；Top 10 关键模块 0% → 50%+ |
| C-2 CI 流水线补齐 | 5 | P0 | CI 工具全覆盖；codecov 集成；矩阵测试 |
| C-3 数据契约测试 | 5 | P0 | 5 类核心 JSON Schema；防 P0-D/P0-E 类 bug |
| C-4 性能基准回归 | 3 | P1 | 关键路径基准；防退化 |
| **合计** | **46** | — | — |

---

## 八、用户决策点

请审批以下决策：

1. **执行顺序**：是否按 B-1 → B-2 → B-3 → B-4 → C-1 → C-2 → C-3 → C-4 的顺序执行？或希望调整优先级（如先做 C-1 测试补齐再做 B-3 拆分）？
2. **执行粒度**：每个任务是单独 commit 还是按 Phase 合并 commit？
3. **兼容性策略**：拆分 God Class 时是否保留旧文件作为 re-export 兼容外壳（推荐）？还是直接删除（更彻底但风险高）？
4. **测试先行**：是否对 B-3（God Object 拆分）每个任务执行 TDD（先写测试再拆分）？
5. **CI 阈值**：覆盖率阈值是否按 22% → 30% → 50% 渐进式收紧？还是直接设 50% 阻断？
6. **范围裁剪**：是否所有 46 个任务都执行？还是先聚焦 P0（35 个任务）？
7. **回滚预案**：每个任务失败后是否自动 git revert？还是手动决策？

---

**等待用户审批后，将按 B1.1 → B1.2 → ... 顺序逐步执行。每个任务完成后更新本文档的执行状态。**
