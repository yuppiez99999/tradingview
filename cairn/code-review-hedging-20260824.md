# 代码审查明细 — hedging 模块 (2026-08-24)

> 审查对象: `ms_strategy/src/hedging/` (5 文件: beta_hedger/correlation_hedger/hedge_coordinator/tail_risk_hedge/vol_hedger)
> 审查批次: 全量核心模块逐模块审查计划 (plan: quant-system-module-audit-20260824) 第 2/10 模块
> 重点: 对冲订单执行闭环、硬编码行情、过度对冲缩放守恒

## 0. 门禁基线

| 门禁 | 结果 |
|---|---|
| ruff 静态扫描 (F/E9/BLE001/T201) | ✅ All checks passed |
| hedging 覆盖单测 | ✅ 120 passed (test_ms_strategy_coverage 等) |

## 1. 缺陷清单与修复状态

| ID | 严重度 | 文件 | 缺陷 | 状态 |
|---|---|---|---|---|
| HG-4 | MEDIUM | hedge_coordinator.py | 过度对冲缩放时 `estimated_cost` 未缩放，与缩放后 notional/`total_cost` 不一致，下游资金预算失真 | ✅ 已修复 |
| HG-1 | MEDIUM | beta_hedger.py | 硬编码期货价 (IF=3800/IC=5500/IM=5800) 回退无 OFFLINE/stale 标记（违反 B1/Q4） | ✅ 已修复 |
| HG-7 | MEDIUM | vol_hedger.py | VIX 分段边界硬编码 40.0 而非可配置，vix_trigger≠30 时行为异常 | ⏸ 记录(P1) |
| HG-6 | LOW | hedge_coordinator.py | `spot = list(prices.values())[0]` 用任意首持仓价当基准价 | ⏸ 记录(P2) |
| HG-5 | 验证通过 | hedge_coordinator.py | 对冲闭环核实: coordinate 生成决策 → automated_execution_system → hedge_order_executor 撮合，闭环通 | ✅ 无缺陷 |

**汇总**: MEDIUM×3, LOW×1；已修复 2 项, 记录 2 项 (HG-7 P1 配置化, HG-6 P2)。

## 2. 修复详情

### HG-4 — 过度对冲缩放守恒
缩放循环 `_scale_orders()` 补 `estimated_cost` 缩放。触发 C901 (coordinate 复杂度 16>15) → 提取 `_scale_orders` 静态方法 (16 行条件分支聚合为 helper，符合 code-simplification)。

### HG-1 — 硬编码期货价 OFFLINE 护栏
`beta_hedger._resolve_futures_price` 回退硬编码价时加 `logger.warning("[OFFLINE_ONLY] ... 非实时, 对冲手数可能失真")`。符合 memory B1/Q4 stale 标记要求。

### 存量清理
`beta_hedger.py` L86 `zip(df["a"], df["m"])` 缺 `strict=False` (B905 存量) → 修复 (改到该文件即清存量)。

## 3. 验证快照

| 验证 | 结果 |
|---|---|
| HG-4 _scale_orders 直接验证 | notional 100→50, contracts 10→5, estimated_cost 5→2.5 (修复前恒5) |
| HG-4 coordinate 触发缩放 | orders 产出正常, 各订单 cost/notional 守恒 |
| HG-1 降级价 | compute_hedge 高 beta 产生 SHORT_FUTURES/DOWNGRADE 指令, contracts>0 |
| 回归测试 | 4 passed (test_hedging_audit_regression_20260824.py) |
| hedging+覆盖单测 | 120 passed |
| ruff_incremental_gate | ✅ 4 文件无新增违规 |

## 4. 防复发 / 契约

- 回归测试覆盖 HG-1/HG-4。
- **待办**: HG-7 (vol_hedger VIX 分段阈值配置化), HG-6 (spot 基准价语义) 排入后续批次。

## 5. 关联记忆/经验

- HG-1 印证 memory 52548113 B1 (硬编码期货行情须 OFFLINE 护栏)。
- HG-4 呼应 memory 91312400 (组合再平衡/对冲缩放须字段守恒)。
- 缩放类逻辑"金额/数量/成本"字段必须同步缩放，否则单订单与聚合对不上——与 memory 契约字段对齐原则一致。
