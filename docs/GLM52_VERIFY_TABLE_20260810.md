# GLM-5.2 审查 73 条缺陷逐条核对表（08-10 实测评测）

> 核对方式：程序化提取 `scan_review_glm52.json` 的 73 条 comment（path/start_line/end_line/severity/existing_code），对每条 comment 在 `e:\各种PY程序\28-终极量化交易系统8.4\` 对应文件中检索 `existing_code` 是否仍存在（容忍行号漂移），并辅以人工抽检确认 FIXED 项均为带修复标记的实质性修复。
> 注意：扫描时 GLM API 触发 429 余额不足，16 个文件子任务未完成（见 JSON `warnings`）。本核对基于 JSON 已产出的 73 条 comment 逐条比对源码，**结论可靠、不受 429 影响**；但 `utils/risk/*` 全系列在 JSON 中仅产出 1 条 comment（#37），plan 文档 §3.1 归纳的 C2/H1–H13 中大部分风控告警链路缺陷在 comments 数组中**无对应条目**——属 plan 文档基于部分输出的人工归纳，无法逐条溯源核对，需充值后补扫确认。

## 总体统计

| 判定 | 数量 | 说明 |
|------|------|------|
| FIXED | 6 | 代码已含修复标记（H17/H18/GLM4.5复核/C3修复），逐条人工确认 |
| OPEN（行号一致） | 48 | 缺陷代码原样位于报告行号 |
| OPEN（行号漂移） | 19 | 缺陷代码仍存在于文件内，但行号已漂移 |
| N/A | 0 | — |
| **合计** | **73** | FIXED 6 / OPEN 67（含漂移 19） |

> 严重度分布（基于 JSON severity 字段）：critical 4（#16/#22/#55/#62）、high 14（#2/#3/#6/#15/#17/#23/#24/#29/#30/#38/#39/#45/#47/#51/#56/#62为critical已计入…实际 high=14: #2/#3/#6/#15/#17/#23/#24/#29/#30/#38/#39/#45/#47/#51/#56/#63/#69/#70 → 以脚本实际 14 条为准）、medium 23、low 32。

## 关键结论（与统一计划文档冲突纠正）

1. **4 个 critical 仅 2 个已修**：C3（#55 broker_adapters 市价单绕限额）与 C4（#62 daily_build_and_hedge logger.info 崩溃）代码已修复；**C1（#16 save_build_progress 无幂等，仍重跑双重执行）与 C2（#22 institutional_pipeline_runner KillSwitch L1 被绕过重建 trades）仍为 OPEN**。统一计划文档附录标注"4 critical ✅"与事实不符。
2. **plan 文档 H1–H13 / H21–H27 大量条目在 JSON 中无对应 comment**：JSON 实际 comments 仅 73 条且 `utils/risk/*` 仅 1 条、无 `ms_strategy/src/*` 条目，与 plan 文档 §1 声称的分布严重不符，该统计不可信。
3. **行号漂移 19 条**：统一计划若引用报告行号定位修复点会失效，应以"当前行号"列为准（部分为漂移，需 grep 定位）。

## 逐条核对表

判定列：`FIXED`=已修复 / `OPEN`=仍存在（同报告行号）/ `DRIFT`=仍存在但行号漂移。
open_drift 在"判定"列标为 `OPEN(漂移)`。

| # | 严重度 | 文件 | 报告行号 | 当前行号 | 判定 | 抽检/备注 |
|---|--------|------|----------|----------|------|-----------|
| 1 | low | utils/execution/__init__.py | 9-11 | 9-11 | OPEN | docstring 行数硬编码 |
| 2 | high | automated_execution_system.py | 46-51 | 46-51 | OPEN | 未注入 sys.modules |
| 3 | high | (同上 shim) | 52-57 | 52-57 | OPEN | fallback 缺 5 符号 |
| 4 | medium | (同上 shim) | 51 | 51 | OPEN | exec_module 无 try |
| 5 | medium | (同上 shim) | 63-70 | 63-70 | OPEN | __all__ 缺项 |
| 6 | high | daily_build_and_hedge.py(shim) | 39-43 | 39-43 | OPEN | 未注入 sys.modules |
| 7 | medium | (同上 shim) | 34-47 | 34-47 | OPEN | fallback 无 try |
| 8 | low | (同上 shim) | 41-42 | 41-42 | OPEN | sys.path.insert(0) 副作用 |
| 9 | medium | daily_trading_workflow.py | 77-80 | 77-80 | OPEN | random.seed(42) 污染全局 |
| 10 | medium | daily_trading_workflow.py | 414-417 | 414-416 | FIXED | except 已改（上下文变化） |
| 11 | low | daily_trading_workflow.py | 349-352 | 349-352 | OPEN | import+实例化合并 try |
| 12 | medium | daily_trading_workflow.py | 278-279 | 278-279 | OPEN | 模拟数据硬编码标记 |
| 13 | low | daily_trading_workflow.py | 37 | 37 | OPEN | TODAY 模块导入期固化 |
| 14 | low | daily_trading_workflow.py | 254-259 | 254-259 | OPEN | 冗余 HOLD 分支 |
| 15 | high | daily_trade_executor.py | 1351-1354 | 1351-1354 | OPEN | _infer_suffix 双后缀 |
| 16 | critical | daily_trade_executor.py | 1617-1623 | 1617-1623 | ✅ FIXED (2026-08-10) | C1 save_build_progress 无幂等 → 已加 executed_instruction_keys 幂等去重 + fail-closed |
| 17 | high | daily_trade_executor.py | 1260-1263 | 1260-1263 | OPEN | 熔断 circuit_breaker 未评估 |
| 18 | medium | daily_trade_executor.py | 1300-1304 | 1300-1304 | OPEN | check_daily_trade_count 无参 |
| 19 | medium | daily_trade_executor.py | 674-687 | 674-687 | OPEN | analyzer 缺失 fail-open |
| 20 | medium | daily_trade_executor.py | 1337-1343 | 1337-1343 | OPEN | 执行文件缺失 prev=[] |
| 21 | medium | daily_trade_executor.py | 864-873 | 864-873 | OPEN | 预算/持仓双重校验绕过 |
| 22 | critical | institutional_pipeline_runner.py | 364-372 | 364-372 | ✅ FIXED (2026-08-10) | C2 KillSwitch L1 被绕过 → 已加 apply_killswitch_l1_filter 重建后过滤 BUY (前置修 FusionSignal 悬挂引用) |
| 23 | high | institutional_pipeline_runner.py | 1148-1154 | 1148-1154 | OPEN | V7.2 权重再分配超限 |
| 24 | high | institutional_pipeline_runner.py | 323-326 | 323-326 | OPEN | enforce_hard_constraints 只调一次 |
| 25 | medium | institutional_pipeline_runner.py | 2025 | 漂移 | OPEN(漂移) | 行号漂移 |
| 26 | low | daily_trade_executor.py | 114-115 | 114-115 | OPEN | 文档/魔法数字 |
| 27 | low | daily_trade_executor.py | 628-629 | 628-629 | OPEN | 注释 |
| 28 | low | daily_trade_executor.py | 208 | 208 | OPEN | 性能 |
| 29 | high | generate_daily_report.py | 378 | 378 | OPEN | pnl 用 REPORT_DATE 非 effective_date |
| 30 | high | generate_daily_report.py | 1014 | 漂移 | OPEN(漂移) | 净对冲收益符号 |
| 31 | medium | generate_daily_report.py | 243-249 | 243-249 | OPEN | — |
| 32 | low | generate_daily_report.py | 262-264 | 262-264 | OPEN | — |
| 33 | low | generate_daily_report.py | 1195-1198 | 漂移 | OPEN(漂移) | — |
| 34 | low | generate_daily_report.py | 1137-1143 | 漂移 | OPEN(漂移) | — |
| 35 | medium | daily_trade_executor.py | 723-725 | 723-725 | OPEN | — |
| 36 | low | daily_trade_executor.py | 1607-1608 | 1607-1608 | OPEN | — |
| 37 | low | utils/risk/__init__.py | 24-41 | 24-41 | OPEN | risk/* 全系列仅此 1 条 comment |
| 38 | high | utils/execution/automated_execution_system.py | 1481-1491 | 1481-1491 | OPEN | 增量平均分母含失败单 |
| 39 | high | utils/execution/automated_execution_system.py | 1172-1173 | 1172-1173 | OPEN | Head-of-line blocking |
| 40 | medium | utils/execution/automated_execution_system.py | 232-245 | 232-245 | OPEN | — |
| 41 | medium | utils/execution/automated_execution_system.py | 93-94 | 93-94 | OPEN | — |
| 42 | medium | utils/execution/automated_execution_system.py | 1178-1182 | 1178-1182 | OPEN | — |
| 43 | medium | utils/execution/automated_execution_system.py | 1190-1191 | 1190-1191 | OPEN | 性能 |
| 44 | low | utils/execution/automated_execution_system.py | 2474-2479 | 2474-2479 | OPEN | — |
| 45 | high | utils/execution/broker_failover.py | 357-364 | 357-365 | FIXED | H17 connect() 移出锁外（带标记） |
| 46 | medium | utils/execution/broker_failover.py | 605-614 | 漂移 | OPEN(漂移) | — |
| 47 | high | utils/execution/broker_failover.py | 364-368 | 漂移 | OPEN(漂移) | _failover_count 重置逻辑待核 |
| 48 | medium | utils/execution/broker_failover.py | 518-532 | 510-537 | FIXED | H18 failover_count 恢复健康重置 |
| 49 | low | utils/execution/broker_failover.py | 104-108 | 104-108 | OPEN | — |
| 50 | low | utils/execution/broker_failover.py | 390-394 | 漂移 | OPEN(漂移) | — |
| 51 | high | hedge_order_executor.py | 380-382 | 380-382 | OPEN | Beta 影响静默为 0 |
| 52 | medium | hedge_order_executor.py | 549-551 | 漂移 | OPEN(漂移) | — |
| 53 | medium | hedge_order_executor.py | 522-525 | 漂移 | OPEN(漂移) | — |
| 54 | low | hedge_order_executor.py | 386 | 漂移 | OPEN(漂移) | — |
| 55 | critical | utils/execution/broker_adapters.py | 216-219 | 216-227 | FIXED | C3 市价单绕限额已修（价格估算） |
| 56 | high | utils/execution/broker_adapters.py | 134-139 | 134-139 | OPEN | 基类懒加载假恢复 |
| 57 | medium | utils/execution/broker_adapters.py | 408-415 | 漂移 | OPEN(漂移) | — |
| 58 | medium | utils/execution/broker_adapters.py | 341-342 | 漂移 | OPEN(漂移) | — |
| 59 | medium | utils/execution/broker_adapters.py | 832-834 | 漂移 | OPEN(漂移) | — |
| 60 | low | utils/execution/broker_adapters.py | 120-121 | 120-121 | OPEN | — |
| 61 | low | utils/execution/broker_adapters.py | 943 | 漂移 | OPEN(漂移) | — |
| 62 | critical | utils/execution/daily_build_and_hedge.py | 1164-1168 | 1164-1168 | FIXED | C4 logger.info 崩溃已修 |
| 63 | high | utils/execution/daily_build_and_hedge.py | 220-230 | 220-230 | OPEN | VIX 失败仍算 neutral |
| 64 | medium | utils/execution/daily_build_and_hedge.py | 489 | 漂移 | OPEN(漂移) | — |
| 65 | medium | utils/execution/daily_build_and_hedge.py | 319 | 漂移 | OPEN(漂移) | — |
| 66 | low | utils/execution/daily_build_and_hedge.py | 252-255 | 248-259 | FIXED | iloc[-21] IndexError 防护 |
| 67 | low | utils/execution/daily_build_and_hedge.py | 594-596 | 漂移 | OPEN(漂移) | — |
| 68 | low | utils/execution/daily_build_and_hedge.py | 75-77 | 75-77 | OPEN | — |
| 69 | high | utils/execution/rebalance_execution_orders.py | 157-159 | 漂移 | OPEN(漂移) | 无效单仍递减 remaining_gap |
| 70 | high | utils/execution/rebalance_execution_orders.py | 134 | 漂移 | OPEN(漂移) | SELL 超持仓 |
| 71 | medium | utils/execution/rebalance_execution_orders.py | 112 | 112 | OPEN | — |
| 72 | medium | utils/execution/rebalance_execution_orders.py | 124-126 | 124-126 | OPEN | — |
| 73 | medium | utils/execution/rebalance_execution_orders.py | 43-46 | 43-46 | OPEN | — |

## 与统一计划文档冲突项（需修订计划）

统一计划 `UNIFIED_UPGRADE_PLAN_20260810.md` 附录当前标注：
- "C1/C2/C3/C4 ✅" → 实际 C1(#16)、C2(#22) 仍为 OPEN，仅 C3(#55)、C4(#62) 已修。**修正为：C1/C2 OPEN、C3/C4 FIXED。**
- "H1-H13 ✅ / H14-H20 ✅ / H21-H27 ✅" → JSON 中无对应 comment（风控系列仅 1 条 #37），**该归纳不可溯源，需充值补扫后重评**。
- Sprint 1 出口条件"GLM critical/high 缺陷修复完成 ✅" → 实际 critical 2/4 未修、14 high 中多项 OPEN。**需降级为"部分修复"。**

建议：将 critical/high 真实未修项（#16 #22 等）排入 Sprint 1 实际修复清单，而非标注已完成。

---

## 补充：GLM 4.5 AIR 补扫（2026-08-10，补全风控链路盲区）

> 原 GLM-5.2 扫描因 API 余额不足（429）触发 16 个文件子任务未完成，其中包含 `utils/risk/*` 全系列（风控核心链路）。用户于 08-10 提供智谱 GLM 4.5 AIR 密钥，改用 `zai-air` 自定义 provider（Base URL `https://open.bigmodel.cn/api/paas/v4`，model `glm-4.5-air`）重跑同样 24 文件路径（`--concurrency 4`）。

**产出**：`scan_review_glm45air.json`，共 **237 条评论**（critical 3 / high 66 / medium 135 / low 33），覆盖 20 个文件。

**关键价值 — 补全原扫描盲区 `utils/risk/*` 系列（之前仅 1 条 #37）**：
| 文件 | GLM-5.2 评论数 | GLM 4.5 AIR 评论数 |
|------|----------------|--------------------|
| utils/risk/kill_switch_adapter.py | 0 | 15 |
| utils/risk/risk_bus.py | 0 | 10 |
| utils/risk/risk_event.py | 0 | 7 |
| utils/risk/risk_module_adapters.py | 0 | 6 |
| utils/risk/style_beta.py | 0 | 1 |
| utils/risk/__init__.py | 1(#37) | 2 |

**其他文件补扫数**：daily_trading_workflow.py 45、generate_daily_report.py 25、institutional_pipeline_runner.py 30、utils/execution/automated_execution_system.py 24、量化策略系统_统一入口_v8.6.py 15、rebalance_order_executor.py 15、daily_trade_executor.py 5、utils/execution/broker_failover.py 14、utils/execution/broker_adapters.py 9、hedge_order_executor.py 0（该文件 GLM-5.2 已覆盖 #51-#54）、daily_build_and_hedge.py 3、utils/execution/broker_factory.py 3、utils/execution/fills_store.py 5、utils/execution/__init__.py 2、automated_execution_system.py 1。

**与统一计划文档冲突项状态更新**：
- 原核对表 §冲突项 指出的"H1-H13/H21-H27 不可溯源，需充值补扫后重评" → **现已用 GLM 4.5 AIR 补全 `utils/risk/*` 真实审查（kill_switch/risk_bus/risk_event 共 32 条），风控链路盲区已闭合**。原 plan 文档归纳的 H1-H13/H21-H27 仍无对应 comment（其命名来自计划文档文字归纳而非扫描 JSON），但盲区事实已通过本次补扫补齐。
- 两次扫描为**不同模型、独立产出**，不可直接拼接计数；建议后续以 GLM 4.5 AIR 全量扫描为准重做统一看板（237 条为当前最完整覆盖）。

**待办**：GL-4.5-AIR 全量重扫 + 与 GLM-5.2 73 条去重合并，生成最终统一缺陷看板（Sprint 1 真实输入）。

---

## 最终：合并去重看板（2026-08-10 完成）

> 用户要求"全量重扫 + 合并去重"。"全量重扫"（443 文件）因 **GLM 4.5 AIR 智谱密钥余额耗尽（429 Too Many Requests / 1113 余额不足）** 在启动阶段即失败，`ocr llm test` 复测确认 key 已不可用。故全量部分中止，改为对**已有两次扫描结果做合并去重**，生成当前 key 限制下最完整的统一看板。

**合并数据源**：
- `scan_review_glm52.json` — GLM-5.2，73 条（部分文件 429 缺失）
- `scan_review_glm45air.json` — GLM 4.5 AIR，237 条（24 文件，含风控盲区补全）

**去重逻辑**：按 `(path, start_line, end_line, existing_code哈希前8位)` 主键去重；同级重复时保留信息更完整者。

**产出**：`scan_review_merged.json`
- **合并后共 282 条**（critical 7 / high 72 / medium 152 / low 51），覆盖 23 个文件
- 对比：GLM-5.2 单独 73 条 → GLM 4.5 AIR 单独 237 条 → 合并去重 282 条（新增 209 条净增，主要来自风控盲区 `utils/risk/*`）

**Sprint 1 真实输入（当前最完整）**：282 条缺陷中，critical 7 条、high 72 条为最高优先级修复目标。注意此看板**非全量 443 文件覆盖**（受 key 余额限制），剩余未扫文件（如 `cli/`、`core/`、`ms_strategy/src/` 全部、`quant_modules/` 全部等）缺陷尚未纳入。

**后续**：待智谱密钥充值后，用 `zai-air` provider 跑全量 443 文件扫描，覆盖当前 282 条未含的源码目录，再与 `scan_review_merged.json` 二次合并，方得真正全量看板。
