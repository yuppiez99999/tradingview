---
title: 16_↔28 TrendCast 集成一期落地 + EOD 链路三处新发现
date: 2026-09-09
status: 一期已落地; F-2/F-3 已按用户拍板解决 (2026-09-09); 仅 F-1 legacy 断链待处理
related:
  - 16_金融市场预测模型/为28终极量化交易系统提供策略决策依据_设计方案_20260909.md
  - utils/reporting/trendcast_card.py
  - utils/execution/rebalance_execution_orders.py
---

# 09-09 晚间：TrendCast 一期落地与 EOD 链路新发现

## 1. 已落地（提交 `9d953128` / `e7b63e8c`）

| 环节 | 实现 | 验证 |
|------|------|------|
| 客户端 | `trendcast_client.py`（health + portfolio_summary，全 fail-open） | 10 passed |
| 审计 | `trendcast_audit.py`（JSONL + 可插拔 price_source 用 28 真实行情回溯） | 同上 |
| 接线 | `daily_runner` 步骤 2.5 拉 28 持仓 → 26 标的 78 条预测落盘 | 18:51 实跑 OK（2.1s） |
| 快照 | `logs/trendcast/signals_YYYY-MM-DD.json` | 2026-09-09 已生成 14KB |
| 卡片 | `utils/reporting/trendcast_card.py` 共享实现，EOD（`generate_daily_report.py`）+ legacy（daily_runner）共用 | 今日真实日报已带卡片；二次调用幂等返回 False |

纪律未破：观测 fail-open（快照缺/解析失败只跳过）、决策 fail-close（概率恒 50% 打警告，禁止用于打分/下单）；一期不改任何下单/调仓。

## 2. 新发现（待决）

### F-1 `daily_runner.py` 步骤 3 断链（legacy 入口）

- 现象：`from daily_report import generate_daily_report` → `ModuleNotFoundError: No module named 'daily_report'`。根目录已无 `daily_report.py`（仅存 `utils/reporting/daily_report_generator.py`，接口完全不同：输入 `ReportInput(trade_date/capital/phases_state...)`、返回 `ReportResult`，属 daily_workflow 语义）。
- 判定：`daily_runner.py` 不在 EOD 主链路（真实日报由 `generate_daily_report.py` 产出，经 `15_每日工作流/run_daily_eod_workflow.py` 阶段一调用，落 `v8.3_institutional/reports/daily_pnl_report_{date}.md`）。即**legacy 入口的接口漂移断链**，非生产阻塞。
- 待决：① 删除/标记 legacy 并指向真实入口；② 或重写步骤 3 调 `generate_daily_report.main()`。倾向 ①（避免第三条日报路径）。

### F-2 国债 ETF 权重口径冲突（DECISION NEEDED）

- 实测：511010.SH 占证券组合 51.46%，`max_single_weight=0.15` 判违规；但 `TARGET_ALLOCATION["国债"]=0.22`、`tools/add_treasury_etf.py` 目标 25% —— **个券上限与风格目标先天冲突**。
- 已修（技术侧，`19a04337`）：同一标的的 mw 减仓单与风格单不再叠加（原来会卖 8400 股 → 权重跌到约 8.3%，两个目标都没达成还多付一次冲击成本）；现按「SELL 取股数最大 / BUY 取最小」合并，目标不一致打 `needs_decision` 标记。回放 20260909：11 单 → 10 单，511010 仅卖 7000 股。
- **已解决（2026-09-09 用户拍板 = R-9，提交 `4aa26607`）**：国债/货基类豁免个券 15% 上限。
  - `config/risk.yaml` → `thresholds.max_weight_by_style: {国债: 0.30}`（硬上限，贯通 Guard6 违规判定 + Guard7 `generate_max_weight_reduction_orders`；缺省空 → 行为不变）。
  - `TARGET_ALLOCATION["国债"]` 0.22 → 0.25，与 `tools/add_treasury_etf.py` 目标对齐，消除第三套口径。
  - 净效果：511010 由「风格单减到 25%」单笔完成，不再与「个券单减到 15%」冲突（此前两单叠加会把权重砸到约 8.3%）。

### F-3 16_ 服务未常驻

- TrendCast 快照依赖 `python main.py serve`（:8800）在盘前已启动。当前为手动启停，未挂计划任务 → 次日若未起服务，步骤 2.5 fail-open 跳过，卡片静默缺失（无告警）。
- **已解决（2026-09-09）**：`scripts/register_trendcast_service_task_20260909.ps1` 注册两个计划任务，均已生效：
  - `TrendCast_Service_Start_8800`：周一至周五 08:30，`E:\Python38\python.exe main.py serve`（工作目录 `16_金融市场预测模型`），SYSTEM / LogonType=5 后台，`MultipleInstances=2`（已在跑则不重复起），`ExecutionTimeLimit=PT12H`。
  - `TrendCast_Service_Stop_8800`：周一至周五 20:00，按 8800 监听进程 kill，避免常驻残留。
  - 脚本幂等（`RegisterTaskDefinition` flags=6 UPDATE），重跑安全；需管理员权限。
- 遗留观察项：服务未起时步骤 2.5 为 fail-open 静默跳过 —— 后续应加「连续 N 日无快照」告警（未做）。

## 3. 复现命令

```powershell
cd 'e:\各种PY程序\16_金融市场预测模型'; python main.py serve        # :8800, 3 模型加载
cd 'e:\各种PY程序\28-终极量化交易系统8.4'; .\.venv\Scripts\python.exe daily_runner.py --skip-download --skip-backtest --no-ai
.\.venv\Scripts\python.exe -m pytest tests/unit/test_trendcast_card.py tests/unit/test_rebalance_order_merge.py -q
```
