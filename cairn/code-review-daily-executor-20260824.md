# 代码审查明细 — daily_trade_executor (2026-08-24)

> 审查对象: `daily_trade_executor.py` (1631 行, 实盘交易指令生成/执行)
> 审查批次: 全量量化逐模块审查计划 (plan: quant-system-module-audit-20260824) 第 7/10 模块
> 审查工具: code-explorer subagent 追调用链
> 重点: 指令生成→confirm→执行闭环/资金计算/风控 fail-open

## 0. 门禁基线

| 门禁 | 结果 |
|---|---|
| ruff 静态扫描 | ✅ All checks passed |

## 1. 缺陷清单与修复状态

| ID | 严重度 | 文件 | 缺陷 | 状态 |
|---|---|---|---|---|
| DTE-1 | CRITICAL | daily_trade_executor.py | 建仓指令**自包含算术模拟记账**, 从不进入 SimulatedBroker/OrderRouter/broker_factory 撮合链, 持仓更新不落 FillsStore (注释声明 SimulatedBroker 但从未 import) | ⏸ 记录(P1 架构级, 待接入撮合链) |
| DTE-2 | HIGH | daily_trade_executor.py | WT 风控分析异常时 fail-open `return None` → "风控崩溃=无风控" | ✅ 已修复(fail-close 保守阻断) |
| DTE-3 | HIGH | daily_trade_executor.py | stop_loss_manager 不可用时静默返回 [] → 止损风控降级无人察觉 | ✅ 已修复(返回标记项告警可见) |
| DTE-4 | HIGH | daily_trade_executor.py | 行情缺失静默回退硬编码价/10.0 假价, 无 stale 标记, 按假价分配资金 | ✅ 已修复(显式 STALE 告警 + 无兜底跳过) |
| DTE-7 | MEDIUM/HIGH | daily_trade_executor.py | `total_built_after` 重复累加当日成交额 (进度口径虚增) | ✅ 已修复(直接读 total_built) |
| DTE-6 | HIGH | daily_trade_executor.py | `--auto-confirm` + post-market-auto 自动确认次日指令, 绕过人工确认 | ⏸ 记录(P1 配置加固) |
| DTE-5 | HIGH | daily_trade_executor.py | 21 个硬编码历史价 DEFAULT_PRICES 陈旧价参与分配 | ⏸ 记录(P2, DTE-4 已缓解) |
| DTE-8 | HIGH | daily_trade_executor.py | 只读 trade_plan 从不写回, 与下游 live_scheduler/hedge 双轨不同步 | ⏸ 记录(P1 架构) |
| DTE-9 | MEDIUM | daily_trade_executor.py | 预测信号失败静默降级 NEUTRAL 继续下单 | ⏸ 记录(P2) |

**汇总**: CRITICAL×1, HIGH×6, MEDIUM×1；已修复 4 项, 记录 4 项。

## 2. 修复详情

### DTE-2 — WT 风控异常 fail-close
`_run_wt_risk_block_check` 异常时返回 `{"status":"blocked","reason":"WT风控分析异常, 保守阻断"}` 而非 `None` 放行。符合 memory「决策路径 fail-close」。

### DTE-3 — 止损管理器降级可见
`_run_stop_loss_check` 在 sl_manager 不可用时返回 `[{"code":"__manager_unavailable","action":"HOLD",...}]` 标记项, 让调用方 L1686 告警可见, 而非静默 `[]`。

### DTE-4 — 行情缺失 stale 标记
`_allocate_position` 行情缺失时: 优先回退 DEFAULT_PRICES 历史价 + `[STALE]` 告警; 连兜底也没有 → 跳过该标的 (返回 None), 不再静默按假价 10.0 分配。

### DTE-7 — total_built_after 重复累加
`daily_records[].total_built_after` 由 `progress["total_built"] + sum(fill_amount)` 改为直接读 `progress["total_built"]` (已在 `_execute_single_instruction` 累加过), 消除虚增。

## 3. 验证快照

| 验证 | 结果 |
|---|---|
| DTE-2 风控异常 | blocked (修复前 None 放行) |
| DTE-3 管理器不可用 | 返回 __manager_unavailable 标记 |
| DTE-4 有兜底 | 用历史价 3.09 |
| DTE-4 无兜底 | 跳过 (None), 不按 10.0 |
| DTE-7 | total_built_after=300 (修复前 600 虚增) |
| 回归测试 | 7 passed |
| ruff_incremental_gate | ✅ 3 文件无新增违规 |

## 4. 关键架构确认 (code-explorer 深挖)

- **DTE-1 修正 memory 认知**: 此前 memory 认为"建仓闭环=daily_trade_executor→SimulatedBroker执行", 实测本文件**从未 import SimulatedBroker/get_broker/OrderRouter**, 建仓执行是**纯内部算术记账**, 不落 FillsStore, 与真实撮合审计口径断链。这是 P1 架构待办(建议接入 broker_factory 撮合链)。

## 5. 待办 (后续批次)

- DTE-1: 建仓执行接入 broker_factory/OrderRouter 撮合链 + FillsStore 落盘 (P1 架构)。
- DTE-6: --auto-confirm 加显式确认护栏。
- DTE-8: 建仓结果写回 trade_plan 消除双轨。

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [风控 fail-close 完整化 (2026-08-24)](risk-failclose-complete-20260824.md) (相似度 35%)
- [DTE-1 建仓接入 FillsStore 事实源 (2026-08-24)](dte1-build-fills-store-20260824.md) (相似度 23%)
- [Agent-Skills 集成与适配（2026-08-24）](agent-skills-integration.md) (相似度 19%)
- [代码审查明细 — 主链路 institutional_pipeline_runner (2026-08-24)](code-review-pipeline-20260824.md) (相似度 17%)
- [P3.0 影子账户闭环门禁 (2026-08-26)](p3-0-gate.md) (相似度 14%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
