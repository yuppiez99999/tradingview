# 重建主入口文件缺失模块实施计划

## Context（背景）

`量化策略系统_统一入口_v8.6.py` 是项目的统一入口文件（1700 行，未被 git 跟踪），但依赖 **17 个从未在 git 中存在过的"幻影模块"**，导致主文件在 line 130 第一个 import 就崩，完全无法运行。生产实际使用 `v8.3_institutional/daily_workflow.py` 等 v8.4 入口。

排查 F811 时发现此问题。用户选择"从零重建所有缺失模块"，目标是让主入口文件能直接运行（`--help` + 模式可执行/优雅降级）。

**缺失模块清单**（17 个模块，30 个名字）：
- 简单工具类：utils/console_encoding, utils/env_loader, utils/stress_test, utils/report_archiver
- 配置基础设施：quant_modules/core（8 个类）, quant_modules/data_layer, quant_modules/connectors, utils/config_hub
- 引擎类：engine/managers, engine/rebalance
- ML 类：utils/ml_predictor, utils/ml_enhanced_trainer
- 其他：fast_backtest, backtest_engine, portfolio_config, 大宗商品基本面综合, utils/wind_data_provider
- cli/modes 依赖：core/context, utils/cli_helpers, cli/__init__

**已有可复用实现**（7 个，用 shim re-export）：
- `utils/config_manager.py` → ConfigManager（完整 4 级优先级实现）
- `utils/fineng/path_simulator.py` → archive_report
- `utils/alpha/fast_backtest.py` → run_fast_backtest
- `utils/wt_backtest_engine.py` → BacktestEngine
- `utils/universe/portfolio_builder.py` → PortfolioConfig
- `lgb_trainer/trainer.py` → run_enhanced_training
- 主文件本地定义的 13 个 helper 函数（line 375-712）

## 实施方案（8 阶段，按依赖顺序）

### 阶段 1：简单工具模块（4 文件，~120 行）
> 无外部依赖，可独立实现

**utils/console_encoding.py**
- `setup_utf8_console()` — Windows 控制台 UTF-8 设置（sys.stdout.reconfigure(encoding='utf-8') + chcp 65001）

**utils/env_loader.py**
- `load_dotenv()` — .env 文件加载（手写简易解析，不依赖 python-dotenv：读取 BASE_DIR/.env，split('=')，os.environ.setdefault）

**utils/stress_test.py**
- `HARD_STOP_MAX_DRAWDOWN = 0.15`（与 project_memory 的 MAX_DRAWDOWN_LIMIT=15% 对齐）
- `TAIL_HEDGE_THRESHOLD = 0.05`（5% 尾部对冲触发）
- `generate_stress_report()` — 调用 utils/stress_test 已有逻辑或返回占位报告

**utils/report_archiver.py**
- `archive_report(base_dir, archive_name, report)` — 归档到 `base_dir/每日报告归档/YYYY-MM-DD/`，写入文件返回路径

### 阶段 2：Shim re-export 模块（3 文件，~15 行）
> re-export 已有实现，消除 import 断裂

**fast_backtest.py**（根目录）
```python
from utils.alpha.fast_backtest import run_fast_backtest  # noqa: F401
```

**backtest_engine.py**（根目录）
```python
from utils.wt_backtest_engine import BacktestEngine  # noqa: F401
```

**portfolio_config.py**（根目录）
```python
from utils.universe.portfolio_builder import PortfolioConfig  # noqa: F401
```

### 阶段 3：quant_modules 配置基础设施（3 文件，~350 行）
> 核心基础设施，被主文件 line 167-177 直接依赖

**quant_modules/__init__.py** — 空包初始化

**quant_modules/core.py** — 8 个类/函数：
- `ConfigError(Exception)` / `DataSourceError(Exception)` — 异常类
- `ConfigManager` — re-export `utils.config_manager` 的实现（或包装）
- `GracefulFallback` — `register_fallback(exc_type, handler)` + `handle(exc)` 降级管理
- `ModuleLoader` — `load(module_name, method_map)` 懒加载器，返回 dict-like（加载失败返回 {}）
- `ProgressIndicator(title, total_steps)` — `.update(step, msg)` / `.complete(msg)` 进度指示
- `StrategyRegistry` — `register_hypothesis(id, dict)` / `list_hypotheses()` 策略注册
- `load_portfolio_config()` — 调用 `utils.config_manager.get_config('portfolio')` 返回 dict

**quant_modules/data_layer.py**
- `DataConnectorManager` — `get_active_connector()` / `get_data_source_label()` / `register()` 连接器管理（可用 utils 已有的数据源实现）

**quant_modules/connectors.py**
- `register_all_connectors(manager)` — 注册 iFinD/Wind/Sina/本地缓存连接器，返回 int（注册数量）

### 阶段 4：engine 引擎模块（2 文件，~250 行）
> 主文件 line 163-166 依赖

**engine/__init__.py** — 空包初始化

**engine/managers.py**
- `ETFFundFlowMonitor(data_connector_manager=None)` — `.analyze_fund_flow()` / `.detect_signals()` / `.get_investment_suggestion()` / `.generate_report()`，属性 `.flow_data`

**engine/rebalance.py**
- `ExcelDrivenRebalancingEngineV4(strategy_registry=None)` — `.load_all()` / `.build_trade_orders()` / `.generate_report()` / `.sync_to_stop_loss_monitor()`，属性 `.complete_plan` / `.batch_plan` / `.is_loaded`

### 阶段 5：ML 模块（2 文件，~300 行）
> 主文件 line 289-303 依赖

**utils/ml_predictor.py**
- `MLModelPredictor` — LightGBM 预测器（load/generate_signals）
- `EnhancedPredictor` — 四维优化预测器（auto_discover_and_load/generate_trading_signals/get_model_info）
- `run_ml_signal_scan(data_dir, model_dir, threshold)` — 信号扫描

**utils/ml_enhanced_trainer.py**
- re-export `lgb_trainer.trainer.run_enhanced_training`
- `EnhancedFeatureEngineer` / `EnhancedMLTrainer` — 特征工程 + 训练器

### 阶段 6：其他配置模块（2 文件，~120 行）

**utils/config_hub.py**
- `ConfigHub(config_dir)` — `.get_all_asset_codes()` / `.check_and_reload()` / `.get_summary()` 统一配置中心（包装 utils.config_manager）

**utils/wind_data_provider.py**
- `WindDataProvider` — Wind 数据源（可调用 utils 已有的 wind 相关实现）
- `get_wind_provider()` — 单例获取

### 阶段 7：cli/modes 依赖修复（3 文件，~60 行）
> 让 cli/modes 包恢复可用

**cli/__init__.py** — 空包初始化

**core/__init__.py + core/context.py**
- 独立提供 7 个名字（不依赖主文件，避免循环 import）：
  - `BASE_DIR` = 项目根（os.path 计算）
  - `logger` = `logging.getLogger('quant')`
  - `strategy_registry` = `quant_modules.core.StrategyRegistry()`
  - `ProgressIndicator` = re-export from quant_modules.core
  - `ML_ENHANCED_TRAINER_AVAILABLE` = 尝试导入
  - `get_ai_coordinator` = re-export from utils.ai_coordinator
  - `get_archive_dir` / `connector_manager` = 实例化或占位

**utils/cli_helpers.py**
- re-export 主文件本地定义的 8 个 helper（write_report_file/archive_report/get_ml_signal_section/get_stock_name/get_portfolio_quotes/get_etf_flow_data/log_execution_summary/get_archive_dir）
- 实现：将这些 helper 的实现移到 utils/cli_helpers.py，主文件改为 import（消除重复定义）

### 阶段 8：大宗商品模块（1 文件，~30 行）
> 主文件 line 938-942 从 `../03_投研与策略生成` 导入

**大宗商品基本面综合.py**（位于 `../03_投研与策略生成/` 或主文件同目录）
- `get_copper_fundamentals()` — 铜基本面数据（如源不存在，提供占位返回空 dict + warning）

## 关键设计决策

1. **core/context.py 独立实现**（不 re-export 主文件）— 避免循环 import：core.context ← cli.modes ← 主文件
2. **ModuleLoader 优雅降级** — `load()` 失败返回空 dict，主文件 `auto_trading.get('AutoTradingSystem')` 返回 None，走"模块不可用"分支
3. **shim 优先** — 7 个已有实现用 re-export，不重复造轮子
4. **utils/cli_helpers.py 集中 helper** — 主文件本地定义的 8 个 helper 移到此处，主文件 + cli/modes 共享，消除重复

## 验证

```bash
# 1. 语法验证
py -3.8 -m py_compile "量化策略系统_统一入口_v8.6.py"

# 2. import 验证（主文件能加载）
py -3.8 -c "import sys; sys.path.insert(0,'.'); from cli.modes import run_daily_workflow; print('OK')"

# 3. --help 验证
py -3.8 "量化策略系统_统一入口_v8.6.py" --help

# 4. 简单模式验证（优雅降级）
py -3.8 "量化策略系统_统一入口_v8.6.py" --check

# 5. ruff 门禁
py -3.8 -m ruff check "量化策略系统_统一入口_v8.6.py" --select F811,F841,T201,BLE001
```

## 工作量估算

| 阶段 | 文件数 | 代码行数 | 复杂度 |
|---|---|---|---|
| 1 简单工具 | 4 | ~120 | 低 |
| 2 Shim re-export | 3 | ~15 | 极低 |
| 3 配置基础设施 | 3 | ~350 | 中 |
| 4 引擎模块 | 2 | ~250 | 中高 |
| 5 ML 模块 | 2 | ~300 | 中高 |
| 6 其他配置 | 2 | ~120 | 中 |
| 7 cli/modes 依赖 | 3 | ~60 | 低 |
| 8 大宗商品 | 1 | ~30 | 低 |
| **合计** | **20** | **~1245** | — |

## 风险与注意事项

1. **ETFFundFlowMonitor / ExcelDrivenRebalancingEngineV4 / ML 类的业务逻辑** — 完整实现需要业务需求。本次基于主文件 + cli/modes 的调用点推断接口，提供可运行实现。如需精确业务行为，后续需对照 v8.3 生产入口补充。
2. **数据源连接器** — `register_all_connectors` 依赖 iFinD/Wind MCP，如环境无 MCP，连接器注册数为 0，主文件走离线模式（已有降级分支）。
3. **utils/cli_helpers.py 迁移** — 将主文件 helper 移到 cli_helpers.py 时，需确保主文件改为 import 且不破坏现有调用。主文件本地定义的 `archive_report` 依赖 `do_archive_report`（来自 utils/report_archiver.py，阶段 1 创建）。
4. **大宗商品模块路径** — `../03_投研与策略生成/` 可能不存在，提供占位实现。
