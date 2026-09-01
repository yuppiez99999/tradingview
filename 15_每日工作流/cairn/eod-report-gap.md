---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-08-17
updated: 2026-08-17
contains: eod-report-gap, hedge-render-mismatch, empty-hedge-log, morning-missing-3, archive-missing
related:
  - ./LOG.md
  - ./ROADMAP.md
  - ./module-map.md
  - ../../generate_daily_report.py
  - ../../config/positions.json
  - ../../daily_build_and_hedge.py
---

# EOD 报告与归档缺失诊断 (2026-08-17)

> 2026-08-17 排查发现的多类报告缺失/内容缺陷诊断。每项含根因定位与修复指针。修复完成后在对应条目标注 `[FIXED YYYY-MM-DD]`。

## 一、诊断总表

| # | 缺失项 | 现状 | 严重度 | 根因指针 | 状态 |
|---|---|---|---|---|---|
| 1 | 8-17 归档目录缺失 | `每日报告归档/2026-08-17/` 不存在；晨间 7 点/EOD 均无归档 | 高 | 见 §2.1 | [FIXED 2026-08-18] 晨间 7/7 + EOD 20 文件归档 |
| 2 | 8-14 对冲头寸渲染全 0 | `daily_pnl_report` 4.3 表 6 类工具全 0，与 positions.json 配置矛盾 | 高 | 见 §2.2 | [FIXED 2026-08-17] |
| 3 | 8-14 Beta 总结自相矛盾 | 6.1 写"Beta 从 1.052 降至 ~1.052"，目标 Beta=0.300 实际未降 | 高 | 见 §2.3 | [FIXED 2026-08-17] |
| 4 | 8-14 预测版本 unknown | 7.6 收益率预测"预测版本: unknown" | 中 | 见 §2.4 | [FIXED 2026-08-17] |
| 5 | 8-14 风险披露全空 | 集中度/波动率/对冲覆盖/政策/流动性全 "-" | 中 | 见 §2.4 | [FIXED 2026-08-17] |
| 6 | 8-14 FeedbackLoop 降级 | 阶段 4.6 未找到归因报告，n_factors=0 | 中 | 见 §2.5 | [FIXED 2026-08-18] 归因生成接入 EOD + 路径+字段名修复 |
| 7 | daily_build_hedge 日志持续空 | 8-14/8-16/8-17 `daily_build_hedge_*.log` 均 0 字节 | 高 | 见 §2.6 | [FIXED 2026-08-17] delay=True + 启动日志 |
| 8 | 晨间 7 项缺 3 项 | 8-13/8-11 仅 4 项（CNEMC/iFinD/行情/棉花），缺康波/ETF/舆情 | 中 | 见 §2.7 | [FIXED 2026-08-17] 康波+ETF 修复；[FIXED 2026-08-18] 舆情模块实现 |
| 9 | 晨间与 EOD 归档分离 | 无任何一天同时有完整晨间 7 项 + EOD 五阶段归档 | 中 | 见 §2.8 | [FIXED 2026-08-18] 8-17 首个完整日（晨间 7/7 + EOD 20 文件） |

## 二、根因定位与修复指针

### 2.1 8-17 归档目录缺失

- **现象**：今天 2026-08-17（周一交易日），`每日报告归档/2026-08-17/` 不存在。`logs/` 仅有 `daily_build_hedge_20260817.log`（0 字节，20:06:18）和 `quant_system.log`（signal_fusion 在跑，20:35 仍在活动）。无 `daily_eod_20260817.log`、无晨间归档。
- **根因假设**：晨间 7 点计划任务未触发（或触发后未归档）；EOD 工作流今日未跑。`quant_system.log` 显示 signal_fusion 单独在跑（可能为交互式调试或单元测试），非完整工作流。
- **修复指针**：
  1. 查 Windows 计划任务：`schtasks /query /tn V84_DailyMorningWorkflow` / `schtasks /query /tn QuantEODWorkflow` 确认任务状态。
  2. 补跑晨间：`python 15_每日工作流/morning_info_runner.py`（无视交易日，直接采集归档）。
  3. 补跑 EOD：`python 15_每日工作流/run_daily_eod_workflow.py`（今日 15:30 已过，可手动补跑，会生成 `每日报告归档/2026-08-17/`）。

### 2.2 8-14 对冲头寸渲染全 0（最关键）

- **现象**：`daily_pnl_report_2026-08-14.md` §4.3 表 6 类工具（covered_call_overlay / risk_reversal_collar / vega_event_driven / budget_summary / active_orders / last_hedge_execution）的 `目标手数`、`目标Beta降低`、`估算名义价值`、`估算成本` 全部为 0。AI 建议却写"建议增加期货对冲合约数量"。
- **根因定位**：`config/positions.json` 的 `hedge_positions` 字段**配置完整**：
  - `covered_call_overlay.enabled=true`，510300+510050 备兑开仓，`estimated_monthly_income=15000`
  - `risk_reversal_collar.put_protection`：510050 Put 20张 + 510300 Put 10张 + 159915 Put 10张，`premium_budget` 合计 350000
  - `budget_summary.total_hedge_capital=1000000`，`active_budget.active_subtotal=600000`
  - `active_orders.status=PENDING_EXECUTION`，date=2026-08-04
  - **结论**：数据源完整，但 `generate_daily_report.py` 渲染 §4.3 表时未正确读取/映射 `hedge_positions` 字段 → **报告生成器与 positions.json 字段契约不一致**。
- **修复指针**：
  1. 在 `generate_daily_report.py` 中定位渲染 §4.3 表的函数（搜索 `covered_call_overlay` / `期货期权计划头寸` / `目标手数`）。
  2. 检查字段映射：报告列名 `目标手数` 对应 positions.json 的 `target_contracts`（Put）或 `allocation_amount`（Call）；`目标Beta降低` 对应字段待确认；`估算名义价值`/`估算成本` 需从 `estimated_monthly_premium`/`premium_budget` 派生。
  3. 修复后重跑：`python generate_daily_report.py --date 2026-08-14`，验证 §4.3 表非全 0。

### 2.3 8-14 Beta 总结自相矛盾

- **现象**：§6.1 写"✅ 对冲策略运行良好：Beta 从 1.052 降至目标区间 ~1.052"，但 §4.2 显示 `目标 Beta=0.300`、`已实现 Beta 降低 ~0.00`。实际 Beta 未降（1.052→1.052），与"运行良好"矛盾。
- **根因假设**：§6.1 总结文本是硬编码模板或基于错误条件分支（`已实现 Beta 降低 ~0.00` 时不应输出"运行良好"）。
- **修复指针**：
  1. 在 `generate_daily_report.py` 中定位 §6.1 对冲总结文本生成逻辑（搜索 `对冲策略运行良好` / `Beta 从`）。
  2. 条件分支应为：`已实现 Beta 降低 >= 0.1` 才输出"运行良好"，否则输出"对冲未生效/待执行"。
  3. 同步修复 §4.2 `净对冲收益` 计算：当前 `-¥9,406.60`（= -现货盈利），逻辑应为 `现货盈亏 + 期货盈亏 - 对冲成本`，期货盈亏=0 时净对冲收益应为 0 而非负现货盈利。

### 2.4 预测版本 unknown + 风险披露全空

- **现象**：§7.6 `预测版本: unknown`；§7.6 风险披露表 5 项（集中度/波动率/对冲覆盖/政策/流动性）全 "-"。
- **根因假设**：`generate_daily_report.py` 调用收益率预测模块时未传入版本标识；风险披露函数未实现或返回空 dict。
- **修复指针**：
  1. 搜索 `预测版本` / `prediction_version` / `unknown`，定位版本字段来源。
  2. 搜索 `集中度风险` / `风险披露`，确认是否有 `build_risk_disclosure()` 类函数，若返回空则补实现。
  3. 风险披露可从现有指标派生：集中度=最大持仓权重、波动率=§5 波动率、对冲覆盖=§4.2 已实现 Beta 降低。

### 2.5 FeedbackLoop 降级

- **现象**：`eod_workflow_summary_2026-08-14.json` phase4_6_feedback_loop: `status=degraded, attribution_found=false, n_factors=0`。日志：`FeedbackLoop (降级): 未找到归因报告: 2026-08-14 (尝试了 3 个路径)`。
- **根因（2026-08-18 完整定位）**：
  1. **路径不匹配**：`PnLAttributionAdapter.load_from_report` 只在 `reports/attribution/` 找 `daily_*.json`/`factor_attribution_*.json`/`*.json`，但归因报告实际在 `reports/pnl_attribution/pnl_attribution_*.json` — 目录+文件名都不匹配。
  2. **字段名不匹配**：适配器找 `style_factor_attributions`/`contribution_to_pnl`，但 pnl_attribution 报告用 `style_factors`/`contribution`。
  3. **归因报告未生成**：8-14 无归因报告（最新 8-13），EOD 工作流未接入归因报告生成。
- **修复（2026-08-18）**：
  1. `utils/evolution/pnl_attribution_adapter.py:load_from_report` candidates 加 `reports/pnl_attribution/pnl_attribution_*.json` 等 3 个路径。
  2. `_from_factor_attribution_dict` 加 fallback：`style_factor_attributions`→`style_factors`，`contribution_to_pnl`→`contribution`。
  3. `15_每日工作流/run_daily_eod_workflow.py` 新增 `run_phase4_55_attribution` 阶段（在 4.5/4.5b/4.7/4.8 之后、4.6 之前），从 `config/positions.json` + `daily_returns.jsonl` + EOD 报告 `hedge_pnl` 生成归因报告，复用 `v8.3_institutional/daily_workflow.py:2428-2443` 简化估算逻辑。
- **验证**：8-14 归因报告已生成（26 持仓，total_pnl=-8688.85，alpha=-1737.77，beta=-7222.49，18 因子）；FeedbackLoop 消费 status=ok, n_factors=18。

### 2.6 daily_build_hedge 日志持续空（0 字节）

- **现象**：`logs/daily_build_hedge_20260814.log`、`daily_build_hedge_20260816.log`、`daily_build_hedge_20260817.log` 均为 0 字节。脚本名实际为 `daily_build_and_hedge.py`（根目录 + `utils/execution/`），日志名 `daily_build_hedge_*.log` 与脚本名不匹配（少了 `and_`）。
- **根因假设**：
  1. 脚本被调用但 `stdout` 未重定向到该日志文件，或脚本在输出前异常退出（`sys.stdout.reconfigure` 前崩溃）。
  2. 脚本名与日志名不一致，可能调用方用错误脚本名或日志路径配置错误。
- **修复指针**：
  1. 读 `daily_build_and_hedge.py`（根目录版本）入口，确认 `if __name__ == '__main__'` 是否有输出、是否早期 `return`/`sys.exit`。
  2. 搜索调用方：`grep -r "daily_build_hedge" --include=*.py` 找谁创建这个日志文件名。
  3. 确认 `utils/execution/daily_build_and_hedge.py` 与根目录版本的差异，统一入口。

### 2.7 晨间 7 项缺 3 项（康波/ETF/舆情）

- **现象**：8-13、8-11 归档仅 4 项（`air_quality_cnemc_*` / `iFinD自动标的研判报告_*` / `morning_market_data_*` / `晨间行情摘要_*` / `棉花的加仓方案_*` / `空气质量CNEMC日报_*`），缺康波周期分析、实时 ETF 资金流向、舆情综合 3 项。`module-map.md` §三列出晨间 7 项应全部归档。
- **根因假设**：`morning_info_runner.py` 中康波/ETF/舆情 3 项任务执行失败但未中断流程（异常被吞），或归档逻辑遗漏这 3 项。
- **修复指针**：
  1. 读 `15_每日工作流/morning_info_runner.py`，定位 7 项任务调度逻辑（搜索 `kondratiev` / `etf_flow` / `sentiment_hub`）。
  2. 确认每项任务是否有 try/except 吞异常，失败时是否记录到日志。
  3. 手动单跑 3 项验证：`python -c "from macro.kondratiev import KondratievCycleAnalyzer; ..."` 等。
  4. 修复后补跑：`python 15_每日工作流/morning_info_runner.py`。

### 2.8 晨间与 EOD 归档分离

- **现象**：8-13/8-11 仅晨间归档，8-14 仅 EOD 归档，无任何一天同时有完整晨间 7 项 + EOD 五阶段。
- **根因假设**：晨间工作流（`run_daily_morning.py`）与 EOD 工作流（`run_daily_eod_workflow.py`）归档到同一 `每日报告归档/YYYY-MM-DD/` 目录，但两者独立运行，可能只跑了一个。8-14 是周四交易日，应既有晨间又有 EOD，但 8-14 目录无晨间文件 → 晨间 7 点任务 8-14 未触发。
- **修复指针**：
  1. 确认 8-14 晨间计划任务是否执行：查 `logs/` 是否有 8-14 早 7 点的晨间日志。
  2. 若晨间任务未触发，查 `setup_morning_scheduled_task.ps1` 注册的任务状态。
  3. 长期方案：EOD 工作流开始时检查当日晨间归档是否存在，缺失则先补跑晨间信息采集。

## 三、修复优先级与执行顺序

1. **P0（阻断决策）**：#2 对冲头寸渲染全 0 + #3 Beta 矛盾 — 报告内容错误会误导交易决策，必须先修。
2. **P1（数据完整性）**：#7 daily_build_hedge 空日志 + #1 8-17 归档缺失 — 影响今日数据可用性。
3. **P2（报告质量）**：#4 预测版本/风险披露 + #6 FeedbackLoop — 报告不完整但不阻断。
4. **P3（流程健壮性）**：#8 晨间缺 3 项 + #9 归档分离 — 长期改进。

## 四、contains 标签索引

- `contains/hedge-render-mismatch`：§2.2 报告生成器与 positions.json 字段契约不一致，对冲头寸渲染全 0。
- `contains/empty-hedge-log`：§2.6 daily_build_hedge 日志持续 0 字节，脚本名与日志名不匹配。
- `contains/morning-missing-3`：§2.7 晨间 7 项中康波/ETF/舆情 3 项未归档。
- `contains/archive-missing`：§2.1 8-17 交易日无归档目录。
