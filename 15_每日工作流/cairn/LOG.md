# 每日工作流调度中枢 (15_每日工作流) Cairn 日志

本文件按反向时间顺序记录本模块的实质性进展 — 最新条目在顶部，紧接本行下方。每条保持简短 — 仅摘要 + 指针；结论沉淀到 `cairn/<topic>.md`。

## 2026-08-26 · morning_info_runner 两阶段并行化

- **改动**: `morning_info_runner.py` run_all() 从串行改为两阶段并行 — 任务1-6用`ThreadPoolExecutor(max_workers=4)`并行, 任务7(大宗商品扫描, 依赖任务1的json+任务4的舆情)在阶段2串行
- **新增**: `_run_task()` 辅助函数封装异常处理, 供线程池调用; `max_workers`参数暴露到run_all签名(默认4)
- **预期**: 盘前准备时间缩短40-60% (I/O密集型任务在GIL下仍可从线程并行受益)
- **指针**: `morning_info_runner.py:794-852` · 上层 `cairn/daily-workflow-parallel-sentiment-20260826.md`

## 2026-08-22 · sentiment_hub 接入 Wind MCP 新闻扫描

- `nlp/sentiment_hub.py` 新增 `_fetch_wind_news_alerts` + `_render_news_alerts_section` — 对每只持仓标的调 `tools.wind_mcp_fetcher.wind_search_news(top_k=5)` 抓新闻，用 CRITICAL_NEGATIVE/POSITIVE_KEYWORDS 命中检测，报告新增"五、新闻舆情预警 (Wind MCP)"章节。
- 三条降级路径: SENTIMENT_HUB_USE_WIND_NEWS=0 关闭 / Wind MCP 不可用显示提示 / 可用时列出命中表。fail-safe, 不崩溃报告生成。
- 本模块 `task_sentiment` (morning_info_runner.py:154) 调 `sentiment_hub.run_all()` 自动受益, 无需改动; `run_daily_morning.py` 用 os.environ.copy() 透传环境变量, 无需改动。
- 修正 `task_sentiment` ImportError 占位文案 (原"模块尚未实现"已过时)。
- ruff.toml 给 `nlp/sentiment_hub.py` 加 BLE001 豁免 (fail-safe 降级同类)。
- 验证: 8-22 报告扫描 26 标的 → 18 负面 + 26 正面命中, 归档到 每日报告归档/2026-08-22/。

## 2026-08-18 · 补跑 8-17 EOD 工作流 + FeedbackLoop 参数修复

- 补跑 8-17 EOD 工作流（`run_daily_eod_workflow.py --date 2026-08-17`），9 成功 3 失败。
- 8-17 归档 20 文件（晨间 7/7 + EOD 报告 + 归因报告 + 其他），首个完整日。
- 8-17 归因报告生成：total_pnl=20,712.62，alpha=4,142.52，beta=16,298.69，18 因子。
- 8-17 Shadow 数据收集：daily_return=+0.7571%，26/26 标的；状态同步 16 条记录，累计收益 +1.58%。
- 修复 `utils/evolution/eod_feedback_integration.py:310` `EvolutionMemory` 参数名 `file_path`→`memory_path`；FeedbackLoop 修复后 status=ok, guard_passed=True。
- 详见：`cairn/eod-report-gap.md` #1/#9。

## 2026-08-18 · FeedbackLoop 归因生成接入 EOD + 舆情模块实现

- 实现 `nlp/sentiment_hub.py`（205 行）— `run_all()` 基于持仓生成舆情综合日报（26 标的监控清单）+ 动力煤舆情日报，晨间舆情任务走真实实现返回 True。
- 修复 `utils/evolution/pnl_attribution_adapter.py:load_from_report` 路径不匹配 — candidates 加 `reports/pnl_attribution/pnl_attribution_*.json`；字段名 fallback `style_factor_attributions`→`style_factors`、`contribution_to_pnl`→`contribution`。
- 新增 `run_daily_eod_workflow.py:run_phase4_55_attribution` 阶段（4.5/4.5b/4.7/4.8 之后、4.6 之前）— 从 `config/positions.json` + `daily_returns.jsonl` + EOD 报告 `hedge_pnl` 生成归因报告，复用 `v8.3_institutional/daily_workflow.py:2428-2443` 简化估算逻辑。
- 验证：8-14 归因报告已生成（26 持仓，total_pnl=-8688.85，18 因子）；FeedbackLoop 消费 status=ok, n_factors=18（原 degraded, n_factors=0）。
- 详见：`cairn/eod-report-gap.md` §2.5。

## 2026-08-17 · EOD 报告缺陷修复 + 晨间 7 项归档补全

- 诊断 9 项报告缺失/缺陷，沉淀到 `cairn/eod-report-gap.md`。
- P0 修复 `reporting/hedge_analyzer.py:analyze_hedge_positions_plan` — 从 `hedge_positions.active_orders` 提取 5 项真实头寸（2 Covered Call + 3 Put），总 Beta 降低 0.163，总名义 140.87 万（原全 0）。
- P0 修复 `generate_daily_report.py` §6.1 Beta 总结条件分支（不再误判"对冲运行良好"）+ §7.6 version fallback（v3，原 unknown）+ `_derive_risk_disclosure` 从 positions.json 派生 5 项风险披露（原全空）。
- P1 修复 `utils/execution/daily_build_and_hedge.py` 日志 0 字节 — FileHandler 加 `delay=True` + main 入口加启动日志。
- P3 修复 `morning_info_runner.py` 晨间 3 项 import 路径失效：康波 `macro.kondratiev`→`utils.kondratiev_cycle`、ETF `engine.etf_flow`→`utils.etf_flow_monitor`（并重写用 `get_all_etf_fund_flows`）；sys.path 补 `PROJECT_ROOT`；舆情 `nlp.sentiment_hub` 不存在，生成占位报告。
- 补跑 8-17 晨间信息采集，7/7 项归档到 `每日报告归档/2026-08-17/`（10 文件）。
- 详见：`cairn/eod-report-gap.md`。

## 2026-08-04 · Project Cairn 模块级初始化

- 为本模块（`15_每日工作流`，生产调度中枢）初始化作用域化 Project Cairn 实例。
- 创建 `.cairn/config.yaml`（模块级作用域配置，标记 `parent_cairn: true` 关联上层项目 Cairn）、`AGENTS.md`（模块协作规则与导航，≤60 行）、`CLAUDE.md`（单行 `@AGENTS.md` 存根）、`cairn/LOG.md`（本文件）、`cairn/ROADMAP.md`、`cairn/module-map.md`（模块组成/职责边界/入口/数据流）。
- 历史迁移模式：`start_fresh`；毕业 provider 暂缓（首次毕业时连接知识库）。
- 模块职责定位：编排层而非交易逻辑层；真实逻辑位于上层 `utils/`、`lgb_trainer/`、`ai_decision/`、`v8.3_institutional/`。
- 详见：`cairn/module-map.md`、`AGENTS.md`、`.cairn/config.yaml`。
