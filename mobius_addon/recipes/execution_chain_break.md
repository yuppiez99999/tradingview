# 执行链断链审计：订单只生成不撮合 / 只撮合不落盘

## 症状
订单只生成不成交（PENDING 永不变成 FILLED）、组合 Delta/持仓不更新、
下游 PnL 读不到成交文件恒为空、TCA 有日志但无落盘事实源。

## 根因
执行链存在两类独立断点：
- A 只生成不撮合：`generate_*_orders()` / `write_to_trade_plan()` 写计划，
  但系统从未有执行器把订单送入撮合引擎（缺真实 `execute_order` / `place_order`
  / `SimulatedBroker` 等消费者）；
- B 只撮合不落盘：撮合后结果仅进内存 `return dict` 即丢弃，未写可追溯 JSONL，
  下游 PnL/TCA 无事实源可消费。

## 修复
- 审计时从生成器向上游看信号、向下游看消费者，确认存在真实撮合方法；
- A 类补齐撮合层（如 `hedge_order_executor` / `rebalance_order_executor`）；
- B 类补齐落盘层（`FillsStore` JSONL + `fills_pnl_bridge` 边界桥接）。

## 验证命令
检索执行引擎是否含 `execute_order` / `MockBroker` / `simulated_broker` /
`market_order`；跑一次 `--hedge-execute` 确认产出 `hedge_execution_fill_*.json`
且 `positions.json` 的 `active_orders` 变为 FILLED。

## 防复发
决策路径 fail-close 阻断、观测路径 fail-open 降级，均留日志不静默；
接入新事实源优先边界桥接 + 来源标记；契约字段上下游完全对齐。
