---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-08-04
updated: 2026-08-18
---

# 每日工作流调度中枢 (15_每日工作流) 路线图

**当前焦点**：8-17 EOD 工作流补跑完成 (2026-08-18) — 9 项诊断全部 [FIXED]，8-17 首个完整日（晨间 7/7 + EOD 20 文件 + 归因报告 + FeedbackLoop ok）。

## 模块里程碑

- [x] 模块级 Project Cairn 初始化 (2026-08-04) — `.cairn/config.yaml` + `AGENTS.md` + `CLAUDE.md` + `cairn/LOG.md` + `cairn/ROADMAP.md` + `cairn/module-map.md`
- [x] LLM 多提供商降级链 + 熔断器（DeepSeek→豆包→GLM→Ollama→HY3→千帆）
- [x] 晨间信息采集 7 项报告编排（`morning_info_runner.py`）
- [x] 早 7 点工作流统一入口（`run_daily_morning.py`）
- [x] 收盘 15:30 五阶段闭环（`run_daily_eod_workflow.py`）
- [x] ML 自动重训（`run_auto_retrain.py`）
- [x] Windows 计划任务注册脚本（morning/eod/retrain）
- [x] EOD 报告缺陷修复 + 晨间 7 项归档补全 (2026-08-17) — 见 `cairn/eod-report-gap.md`
- [x] FeedbackLoop 归因生成接入 EOD + 舆情模块实现 (2026-08-18) — `nlp/sentiment_hub.py` + `pnl_attribution_adapter.py` 路径/字段修复 + `run_phase4_55_attribution` 阶段
- [x] 8-17 EOD 工作流补跑 + FeedbackLoop 参数修复 (2026-08-18) — 20 文件归档，归因报告 total_pnl=20,712.62，FeedbackLoop status=ok

## 待办（按需触发，不预建）

- [ ] 统一 `run_daily_morning.py` 与 `run_daily_eod_workflow.py` 的 Python 解释器解析逻辑（`QUANT_PYTHON` 优先 vs 硬编码 Python311 的差异消除）
- [ ] 评估 `setup_eod_scheduled_task.ps1` 已标注 DEPRECATED（被 `scripts/register_all_tasks_unified.ps1` 替代）是否需要归档
- [ ] 将 `run_auto_retrain.py` 重训触发与上层 `DriftMonitor` 事件对接的现状沉淀为专题文档
- [ ] EOD 报告完整重跑验证（本次 `generate_daily_report.py 2026-08-14` 因网络超时未完成完整重跑，修复逻辑已单元验证）


## 开放问题

1. 本模块与上层项目 Cairn 的边界 — 本模块 `cairn/` 仅记录调度编排层知识，交易逻辑知识仍归属上层 `28-终极量化交易系统8.4/cairn/`，需保持一致不重复。
2. 计划任务版本漂移 — `setup_*_scheduled_task.ps1`（QuantEODWorkflow/QuantMLRetrain/V84_DailyMorningWorkflow）与 `scripts/register_all_tasks_unified.ps1` 存在新旧两套注册方式，建议后续统一到单一入口。
