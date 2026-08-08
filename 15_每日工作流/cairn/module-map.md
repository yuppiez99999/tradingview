---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-08-04
updated: 2026-08-04
contains: module-map, entry-points, data-flow, code-navigation
related:
  - ../../AGENTS.md
  - ./LOG.md
  - ./ROADMAP.md
---

# 每日工作流调度中枢 — 模块地图与导航

> 本模块的组成、职责边界、入口点、数据流向与关键约定。用于快速定位调度编排逻辑。真实交易逻辑在上层项目（`utils/`、`lgb_trainer/`、`ai_decision/`、`v8.3_institutional/`），本模块仅做编排。

## 一、模块组成

```
15_每日工作流/
├── llm_client.py                   ★ LLM 多提供商降级客户端
├── morning_info_runner.py          ★ 晨间信息采集 7 项报告
├── run_daily_morning.py            ★ 早 7 点统一入口 (--phase info/calibrate/plan/report/all)
├── run_daily_eod_workflow.py       ★ 收盘 15:30 五阶段闭环
├── run_auto_retrain.py             ★ ML 模型自动重训
├── run_daily_morning.bat           批处理启动器 (早 7 点)
├── run_eod_workflow.bat            批处理启动器 (收盘 15:30)
├── setup_morning_scheduled_task.ps1 计划任务注册 (早 7 点)
├── setup_eod_scheduled_task.ps1     计划任务注册 (已 DEPRECATED, 见下)
├── setup_retrain_scheduled_task.ps1 计划任务注册 (每月 1 日)
├── AGENTS.md                       本模块 Cairn 规则与导航
├── CLAUDE.md                       单行 @AGENTS.md 存根
├── .cairn/config.yaml              模块级 Cairn 配置
└── cairn/                          模块知识库 (LOG/ROADMAP/module-map)
```

## 二、模块职责边界

- 本模块是**编排层**，不实现交易逻辑。所有实质逻辑调用上层项目模块。
- 跨目录复用路径：
  - `BASE_ROOT/15_每日工作流`（晨间行情/CNEMC/DeepSeek 摘要，与模块同名但属上层，注意区分）
  - `BASE_ROOT/11_量化策略`（ETF 资金流 `engine/etf_flow.py`）
  - 模块内 `v8.3_institutional/`（康波周期 `macro/kondratiev.py`、舆情 `nlp/sentiment_hub.py`、`daily_workflow.py`、`generate_daily_trade_plan.py`）
- 输出归档：`PROJECT_ROOT/每日报告归档/YYYY-MM-DD/`；日志：`PROJECT_ROOT/logs/`。

## 三、各脚本功能

### llm_client.py — LLM 客户端

- `chat(prompt, ...)`：多级降级链 DeepSeek→豆包→GLM→Ollama(API)→Ollama(CLI)→HY3→千帆；熔断器（失败 provider 冷却 300s）。
- `chat_deep(prompt, ...)`：深度思考 — DeepSeek R1 (reasoner) → Ollama deepseek-r1:14b → 普通 chat 兜底。
- 自动 GPU 检测：`nvidia-smi` 显存 ≥8GB 时 `OLLAMA_NUM_GPUS=1`，否则 0。
- `.env` 加载自上层项目根目录；无代理 opener 避免系统代理拒绝国内金融 API。

### morning_info_runner.py — 晨间信息采集（7 项）

| 任务 | 源模块 |
|---|---|
| 晨间行情摘要 | `morning_market_fetcher.main()`（上层 15_每日工作流） |
| 康波周期分析 | `macro.kondratiev.KondratievCycleAnalyzer` |
| 实时ETF资金流向 | `engine.etf_flow.ETFRealTimeTracker` |
| 舆情综合+动力煤 | `nlp.sentiment_hub.run_all` |
| CNEMC空气质量 | `cnemc_air_quality_runner` |
| iFinD自动研判 | `research/ifind_auto_analysis.py` |
| 棉花加仓方案归档 | 复制源文件 |

### run_daily_morning.py — 早 7 点入口

- 阶段 0 信息采集（无视交易日）→ 阶段 1 盘前校准 → 阶段 2 交易计划 → 阶段 3 LLM 决策灌入 → 阶段 4 综合报告 → 归档。
- 交易日判断委托 `utils.trade_calendar.is_trading_day()`（统一实现，支持节假日）。

### run_daily_eod_workflow.py — 收盘五阶段闭环

- 阶段 0 年化收益校准 → 阶段 1 收盘报告（DeepSeek AI 建议）→ 阶段 2 次日交易计划 → 阶段 3 LLM 决策灌入 → 阶段 4 四 Guard 风控链 → 阶段 4.5 Shadow 收集 → 阶段 4.6 FeedbackLoop → 阶段 5 归档。

### run_auto_retrain.py — ML 自动重训

- 扫描 `models/lgb_enhanced/`，重训条件：年龄 >30 天 / IC<0 / Sharpe<0；支持 `--force` / `--symbols` / `--dry-run` / `--no-news`。
- 备份失败时跳过重训（防覆盖原始模型）；重训前后 IC/Sharpe 对比验证。

## 四、入口点

| 入口 | 命令 | 用途 |
|---|---|---|
| 早 7 点 | `python run_daily_morning.py --phase all` | 晨间信息+决策流程 |
| 收盘 | `python run_daily_eod_workflow.py` | 收盘五阶段闭环 |
| 重训 | `python run_auto_retrain.py` | ML 模型自动重训 |
| 信息采集 | `python morning_info_runner.py` | 仅晨间信息采集 |

## 五、数据流向

```
外部数据源 (Wind/iFinD/TDX/AKShare/新浪)
    │
    ▼ 上层 utils/data_provider.py (采集+多源校验)
    │
    ▼ 上层 pipeline/data_cleaning.py (清洗+质量门控)
    │
    ▼ 上层 alpha_pipeline.py → lgb_trainer/ → signal_fusion.py (信号)
    │
    ▼ ai_decision/debate_engine.py (多 AI 辩论共识)
    │
    ▼ run_daily_morning / run_daily_eod_workflow (本模块编排)
    │
    ▼ 上层 execution/risk + reporting (执行/风控/报告)
    │
    ▼ 每日报告归档/YYYY-MM-DD/
```

## 六、关键约定与踩坑

- **编码**：Windows 下强制 UTF-8 重配置，避免 GBK 乱码。
- **解释器**：优先 `QUANT_PYTHON` 或当前解释器；`run_daily_morning.py` 硬编码 Python311 为历史遗留，改动前确认。
- **子进程**：EOD 用 `CREATE_NEW_PROCESS_GROUP` 独立进程组，超时 `taskkill /T` 清理孙进程。
- **交易日**：统一委托 `utils.trade_calendar`，不自行维护日历。
- **计划任务新旧并存**：`setup_eod_scheduled_task.ps1` 已 DEPRECATED（被 `scripts/register_all_tasks_unified.ps1` 替代）；`V84_DailyMorningWorkflow` 由 `setup_morning_scheduled_task.ps1` 注册。
- **LLM**：AI 调用走 `llm_client`，勿直连单提供商。

### contains/windows-utf8-console
Windows PowerShell/cmd 默认 GBK 编码会导致 Python 输出乱码，脚本内需 `sys.stdout.reconfigure(encoding='utf-8')`。

### contains/hardcoded-python-path
`run_daily_morning.py` 仍硬编码 `C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe`，与 `run_daily_eod_workflow.py` 的 `QUANT_PYTHON` 优先策略不一致，待统一。

### contains/dual-scheduled-task-registration
`setup_eod_scheduled_task.ps1` 标记 DEPRECATED（2026-07-30 迁移至 `scripts/register_all_tasks_unified.ps1`），但 `setup_morning_scheduled_task.ps1` 与 `setup_retrain_scheduled_task.ps1` 仍在使用，存在新旧两套注册方式并存。
