# DTE-1 建仓接入 FillsStore 事实源 (2026-08-24)

> 工作线 A: 执行链完整化 — DTE-1 建仓接入撮合链 (首步: 落盘 FillsStore)
> 审查工具: code-review-and-quality 五轴审查
> 状态: 已实现并验证 (首步闭环)

## 0. 结论

**daily_trade_executor 建仓成交已接入 FillsStore 事实源 (strategy="build"),
使"账本自我记账"成为可追溯成交, 供 PnL/TCA/影子账户消费。**
与 rebalance (strategy=rebalance) / hedge (strategy=hedge) 执行器一致。

## 1. 背景 (断链确认)

- daily_trade_executor 文档 (L20) 声称"模拟执行 (SimulatedBroker)", 但审查确认**从未 import SimulatedBroker/get_broker/OrderRouter**, 建仓执行是**纯内部算术记账** (L1636 `_execute_single_instruction` 计算成交价/费用/持仓更新), **不落盘 FillsStore**。
- 后果: 建仓成交不可追溯, PnL/TCA/影子账户读不到建仓 fills, 审计口径断裂。

## 2. 五轴审查

| 轴 | 结论 |
|---|---|
| **Correctness** | ✅ 仅记录 FILLED 成交 (qty>0 且 fill_price>0), SKIPPED/异常不落盘; 落盘在持仓更新后调用, 数据一致 |
| **Readability** | ✅ 提取 `_record_build_fill` helper, 不内联到 `_execute_single_instruction` (防 C901 复杂度) |
| **Architecture** | ✅ 复用 FillsStore 事实源, 与 rebalance/hedge 一致; strategy="build" 区分策略 |
| **Security** | ✅ 无密钥/注入; symbol 从指令字典取, 无外部输入 |
| **Performance** | ✅ 单笔落盘, 无性能问题 |

## 3. 实现

### daily_trade_executor.py
- 新增 import: `FillsStore`
- 新增 `_record_build_fill(inst, result, target_date_str)`: FILLED 成交落盘
  (symbol/side/qty/fill_price/broker=SimulatedBroker/is_live=False/strategy=build/source=sim_route/meta=费用)
- `_execute_single_instruction` FILLED 返回前调用 `_record_build_fill` (观测路径 fail-open)

## 4. 验证快照

| 验证 | 结果 |
|---|---|
| FILLED 落盘 | strategy=build, 写入 FillsStore |
| SKIPPED 不落盘 | 返回 False |
| qty=0 不落盘 | 返回 False |
| 回归测试 | 3 passed (test_dte1_fills_store_20260824.py) |
| ruff_incremental_gate | ✅ 3 文件无新增违规 |

## 5. 后续 (DTE-1 完整闭环)

- **影子账户消费**: 影子账户 (工作线 C 核查: trade_log 空) 可读 strategy="build" 的 fills 作为真实成交, 解决影子观察期无真实撮合问题。
- **broker 撮合链**: 若需真实下单, 建仓执行应改用 get_broker() 返回的 broker (当前 SimulatedBroker), 而非内部算术。此步待 QMT 影子期 (G1) 时对接。
- **daily_pnl 消费**: 确认 daily_pnl 能读 strategy="build" 的 fills (与 hedge/rebalance 对齐)。

## 6. 关联

- 本轮审查 DTE-1 (CRITICAL) 首步落地: 建仓成交可追溯。
- 影子观察期 (工作线 C) 的 trade_log 空问题, 可通过消费 strategy="build" fills 部分缓解。
