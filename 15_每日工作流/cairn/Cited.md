# 每日工作流调度中枢 — 引用索引

> 本文件记录本模块知识所引用的内部代码与文档。仅指针，不复制原文。跨项目外部引用见上层项目 `28-终极量化交易系统8.4/cairn/Cited.md`。

## 内部代码

1. [llm_client.py](../llm_client.py) — LLM 多提供商降级客户端（chat/chat_deep/熔断器/GPU 检测）
2. [morning_info_runner.py](../morning_info_runner.py) — 晨间信息采集 7 项任务调度
3. [run_daily_morning.py](../run_daily_morning.py) — 早 7 点工作流统一入口
4. [run_daily_eod_workflow.py](../run_daily_eod_workflow.py) — 收盘五阶段闭环
5. [run_auto_retrain.py](../run_auto_retrain.py) — ML 模型自动重训

## 上层项目（编排目标）

1. [28-终极量化交易系统8.4/AGENTS.md](../../AGENTS.md) — 全局协作规则与 Cairn 约定
2. [28-终极量化交易系统8.4/utils/](../..//utils/) — 核心工具库（trade_calendar/data_provider/execution/risk）
3. [28-终极量化交易系统8.4/lgb_trainer/](../..//lgb_trainer/) — LightGBM 增强训练器
4. [28-终极量化交易系统8.4/ai_decision/](../..//ai_decision/) — 多 AI 辩论决策引擎
5. [28-终极量化交易系统8.4/v8.3_institutional/](../..//v8.3_institutional/) — 机构级策略库（daily_workflow/macro/sentiment_hub）
6. [28-终极量化交易系统8.4/scripts/register_all_tasks_unified.ps1](../..//scripts/register_all_tasks_unified.ps1) — 统一计划任务注册（替代 setup_eod_scheduled_task.ps1）
