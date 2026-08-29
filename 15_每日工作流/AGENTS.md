# 每日工作流调度中枢 (15_每日工作流) 协作规则

> 本模块使用 Project Cairn 组织模块知识：本文件（AGENTS.md）是规则与导航入口，`cairn/` 是模块知识/状态层。
> 本模块是上层项目 `28-终极量化交易系统8.4` 的生产调度中枢，遵循上层项目 AGENTS.md 的全局约定（数据源优先级、不可变性、代码质量等）。本文件仅补充模块级约定。
> 同目录 `CLAUDE.md` 应仅包含一行 `@AGENTS.md`；Codex 直接读取本文件。

## 模块一句话定位

量化交易系统的生产调度中枢 — 编排晨间信息采集、收盘五阶段闭环、ML 自动重训、LLM 多提供商降级调用与 Windows 计划任务。

## 模块组成

| 文件 | 职责 |
|---|---|
| `llm_client.py` | LLM 多提供商降级客户端（DeepSeek→豆包→GLM→Ollama→HY3→千帆）+ 熔断器 + GPU 自动检测 |
| `morning_info_runner.py` | 晨间信息采集（7 项报告：行情/康波/ETF/舆情/CNEMC/iFinD/棉花） |
| `run_daily_morning.py` | 每日早 7 点统一入口（`--phase info/calibrate/plan/report/all`） |
| `run_daily_eod_workflow.py` | 每日 15:30 收盘五阶段闭环（报告→计划→LLM→四Guard→归档→审核） |
| `run_eod_audit.py` | EOD 收盘审核（数据质量+盘中决策+告警+交易计划可信度） |
| `run_auto_retrain.py` | ML 模型自动重训（增量/全量/指定标的） |
| `run_daily_morning.bat` / `run_eod_workflow.bat` | 批处理启动器 |
| `setup_*_scheduled_task.ps1` | Windows 计划任务注册（morning/eod/retrain） |

## 模块职责边界

- 本模块是**编排层**，不直接实现交易逻辑。真实逻辑位于上层项目的 `utils/`、`lgb_trainer/`、`ai_decision/`、`v8.3_institutional/`。
- 跨目录复用：复用上层 `15_每日工作流/`（晨间行情/CNEMC）、`11_量化策略/`（ETF 资金流）、模块内 `v8.3_institutional/`（康波/舆情/sentiment_hub）。
- 所有报告归档到上层项目 `每日报告归档/YYYY-MM-DD/`，日志到上层项目 `logs/`。

## 进入本模块后的阅读顺序

1. 先读本文件（AGENTS.md）。
2. 读 `cairn/ROADMAP.md`（若存在）了解本模块焦点与开放问题。
3. 读 `cairn/LOG.md` 最近条目（最新在顶部）。
4. 按任务需要读相关 `cairn/` 知识专题文档。

## 文档职责（沿用项目 Cairn 约定）

| 文件 | 职责 | 维护方式 |
|---|---|---|
| `AGENTS.md` | 规则与导航 | 极少变动，≤ 60 行 |
| `CLAUDE.md` | 单行 `@AGENTS.md` 存根 | 写入一次，之后不动 |
| `cairn/LOG.md` | 按时间顺序日志 | 新条目追加在顶部，每条 ≤ 20 行，仅摘要 + 指针 |
| `cairn/ROADMAP.md` | 路线图与进度 | 原位更新，保持简洁 |
| `cairn/<topic>.md` | 知识专题文档（当前真相） | 原位更新；踩坑用 `contains` 打标签 |
| `cairn/Cited.md` | 知识库引用列表 | 仅指针，不复制原文 |

> 其他一切仅在具体信号触发时创建（决策记录/踩坑/跨会话目标），不预建空壳。

## 关键约定与踩坑要点

- **编码**：Windows 下脚本内强制 `sys.stdout.reconfigure(encoding='utf-8')`，避免 GBK 乱码。
- **解释器**：优先 `QUANT_PYTHON` 环境变量或当前解释器，不硬编码 Python 路径（`run_daily_eod_workflow.py` 已做 C8 修复；`run_daily_morning.py` 仍硬编码 Python311 为历史遗留，改动前确认）。
- **子进程**：EOD 工作流用 `CREATE_NEW_PROCESS_GROUP` 建独立进程组，超时后 `taskkill /T` 彻底清理孙进程。
- **交易日判断**：委托 `utils.trade_calendar.is_trading_day()`（统一实现，支持节假日），本模块不自行维护日历。
- **LLM 降级链**：所有 AI 调用走 `llm_client.chat/chat_deep`，勿直连单提供商；`chat` 降级链 DeepSeek 优先 + 熔断器。
- **知识沉淀**：每个实质性推进后，在 `cairn/LOG.md` 顶部追加一条；结论沉淀为 `cairn/` 专题文档。

## 冲突仲裁

- 优先级：**知识专题文档 > LOG 历史**；规则层面冲突由本文件裁定。
- 与上层项目全局规则冲突时，以上层项目 `28-终极量化交易系统8.4/AGENTS.md` 为准。

## 环境变量

```
QUANT_PYTHON=...                # Python 解释器 (优先于硬编码路径)
DEEPSEEK_API_KEY=...            # LLM 主提供商
WIND_API_KEY=...                # Wind MCP 数据源
TRADING_ENV=shadow|production   # 默认 shadow
```
