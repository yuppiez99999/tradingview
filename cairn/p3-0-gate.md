# P3.0 影子账户闭环门禁 (2026-08-26)

> 任务: ETF Phase 3 影子账户消费 DTE-1 build fills + NAV 回算端到端验证
> 状态: ✅ **全 PASS 2026-09-02 (提前)** — ③ 数据积累达标 (5 交易日/194 笔), P3.1 前置门禁解除; Phase 3 已按纯 S12 启动 (见 `cairn/ROADMAP.md` §Phase 3)
> ROADMAP: P3.0 (09-03~09-05), P3.1 前置门禁

## 0. 结论

**影子账户消费 strategy=build fills → trade_log → holdings → NAV 链路已打通**。
FillsStore / fills_pnl_bridge 加 `strategies` 可选参数 (向后兼容), ShadowAccount 新增 `consume_fills_from_store` 方法, 验证脚本 + 10 测试全绿。
当前 ①② 验证 PASS (4 笔 build fills 消费通), ③ 数据积累不足 (1 交易日/4 笔, 需 ≥5 交易日), 需 daily_trade_executor 继续运行积累。

## 1. 背景

- DTE-1 (2026-08-24): `daily_trade_executor._record_build_fill` 落盘 strategy=build fills 到 FillsStore
- 但影子账户 4 个模块均不引用 FillsStore, `ShadowAccount.trade_log` 恒空 (NAV 全靠外部注入或行情估值)
- `fills_pnl_bridge.augment_market_prices` 消费 fills 但不按 strategy 过滤, build 成交价覆盖 rebalance 的 close (口径污染)
- P3.0 门禁: 三件验证通过才允许 P3.1 启动

## 2. 实现 (5 项改动)

### 2.1 FillsStore 加 strategy 过滤 (`utils/execution/fills_store.py`)
- `load_day(date, strategies=None)` — None=全部 (兼容), 指定则过滤
- `latest_avg_price_by_symbol(date, strategies=None)` — 透传
- `realized_pnl(date, strategies=None)` — 透传
- 模块级 `load_day(date, strategies=None)` — 透传

### 2.2 fills_pnl_bridge 透传 (`utils/execution/fills_pnl_bridge.py`)
- `augment_market_prices(market_prices, date, strategies=None)` — 透传给 FillsStore
- `realized_pnl(date, strategies=None)` — 透传

### 2.3 ShadowAccount.consume_fills_from_store (`shadow_account_system.py`)
- `consume_fills_from_store(dates, strategies=("build",))` — 逐日读 FillsStore → 填 trade_log → 构建 cumulative holdings + cash → 逐日 NAV → record_daily_nav
- fail-open: FillsStore 不可用返回零值, 不抛异常
- 已终止账户: 返回零值, 不消费
- NAV 算法: `nav = (cash + sum(holdings[sym] * latest_price)) / initial_capital`

### 2.4 验证脚本 (`scripts/verify_p3_0_gate.py`)
- ① `verify_1_shadow_consume_fills` — ShadowAccount.consume_fills_from_store, 检查 fills_count > 0
- ② `verify_2_daily_pnl_filtered` — FillsStore.latest_avg_price_by_symbol(strategies=...) 取到 build 成交均价
- ③ `verify_3_data_accumulation` — 统计有 strategy=build 成交的交易日数 >= 5
- 退出码: 0=全 PASS (P3.1 可启动), 1=任一 FAIL

### 2.5 测试 (`tests/unit/test_p3_0_gate.py`)
- 10 用例: consume_fills basic/empty/no_match/strategy_filter/sell/terminated + FillsStore 过滤 + bridge 透传
- 全绿

## 3. 验证快照 (2026-08-26)

```
python scripts/verify_p3_0_gate.py --weeks 2

  [PASS] ① shadow 消费 fills: fills=4, trade_log=4, nav=1.000000, holdings=1 只
  [PASS] ② daily_pnl 过滤消费: strategies=('build',) 取到成交均价的交易日 = 1, 覆盖标的总数 = 1
  [FAIL] ③ 数据积累: 有 strategy=('build',) 成交的交易日 = 1 (要求 >= 5), 总记录 = 4
```

- ① nav=1.000000 是正确的: 只有 BUY 无 SELL 时, 市值=成本, NAV 恒等于 1.0 (NAV 变化需价格变动或 SELL)
- ③ FAIL 是预期的: 需 daily_trade_executor 继续运行积累 ≥5 个交易日的 build fills

## 4. 后续

- **09-03~09-09**: daily_trade_executor 每日运行, 积累 strategy=build fills
- **09-09 后**: 重跑 `python scripts/verify_p3_0_gate.py --weeks 2`, 预期 ③ PASS
- **P3.0 端到端 PASS**: P3.1 可启动 (影子账户配置)
- **可选**: 归档 build fills 到 `models/` 或独立目录 (当前 `reports/fills/` 已工作)

## 5. 关联

- DTE-1: `cairn/dte1-build-fills-store-20260824.md` (build fills 落盘事实源)
- ROADMAP: P3.0 (09-03~09-05) 门禁
- 代码: `utils/execution/fills_store.py` + `fills_pnl_bridge.py` + `shadow_account_system.py` + `scripts/verify_p3_0_gate.py`
- 测试: `tests/unit/test_p3_0_gate.py` (10 用例) + `test_fills_pnl_bridge_unit.py` (4 断言更新) + `test_dte1_fills_store_20260824.py` (回归通过)

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [DTE-1 建仓接入 FillsStore 事实源 (2026-08-24)](dte1-build-fills-store-20260824.md) (相似度 34%)
- [影子观察期真实性核查 (2026-08-24)](shadow-realness-audit-20260824.md) (相似度 24%)
- [运维部署 + 灰度发布 (2026-08-24)](ops-gray-release-20260824.md) (相似度 24%)
- [成交回报驱动 PnL：G2/G4 执行闭环补齐经验沉淀（2026-08-08）](fills-driven-pnl-lessons-20260808.md) (相似度 15%)
- [代码审查明细 — daily_trade_executor (2026-08-24)](code-review-daily-executor-20260824.md) (相似度 14%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
