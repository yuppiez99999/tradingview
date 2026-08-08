# FactorKillSwitch 每日监控仪表盘

> 交易日期：2026-07-25
> 生成时间：2026-07-25 11:01:07
> 监控窗口：最近 5 日增量更新

## 1. 监控概览

| 项目 | 值 |
|------|-----|
| 交易日期 | 2026-07-25 |
| 监控因子总数 | 67 |
| 现有生产因子 | 51 |
| 候选因子（vibe_trading） | 16 |
| ACTIVE 比例 | 10/67 (14.9%) |
| 当日新触发 | 5 个 |

## 2. 锁定参数（DECISION v1.0）

| 触发条件 | 阈值 | 动作 |
|----------|------|------|
| 连续 5 日 IC < 0.02 | DEGRADED | 仓位减半 |
| 连续 10 日 IC < 0 | DISABLED | 自动禁用 |
| 连续 20 日 IC < 0 | RETIRED | 强制退役（不可恢复） |
| 单日回撤 > 3% | DEGRADED | 仓位减半 |
| 累计回撤 > 8% | DEGRADED | 仓位减至 25% |
| 累计回撤 > 12% | EMERGENCY_EXIT | 全部退出 |

## 3. 状态分布

| 状态 | 因子数 | 占比 |
|------|--------|------|
| degraded | 32 | 47.8% |
| disabled | 17 | 25.4% |
| active | 10 | 14.9% |
| emergency_exit | 5 | 7.5% |
| warned | 3 | 4.5% |


## 4. 按来源 × 状态分布

| 来源 | 状态 | 因子数 |
|------|------|--------|
| existing | degraded | 27 |
| existing | disabled | 11 |
| existing | active | 8 |
| existing | emergency_exit | 4 |
| existing | warned | 1 |
| vibe_trading_candidate | disabled | 6 |
| vibe_trading_candidate | degraded | 5 |
| vibe_trading_candidate | active | 2 |
| vibe_trading_candidate | warned | 2 |
| vibe_trading_candidate | emergency_exit | 1 |


## 5. 当日新触发（仅记录最后一日变化）

| 因子 | 状态转移 | 新仓位 | 触发原因 |
|------|---------|--------|---------|
| `MOM_VOLUME_ADJ` | degraded → disabled | 0.00 | IC<0 连续 10d -> DISABLED |
| `VOL_20D` | degraded → active | 1.00 | IC 恢复 + 无回撤 -> ACTIVE |
| `VOL_60D` | degraded → active | 1.00 | IC 恢复 + 无回撤 -> ACTIVE |
| `VOL_IDIO` | degraded → active | 1.00 | IC 恢复 + 无回撤 -> ACTIVE |
| `LIQ_SPREAD` | degraded → active | 1.00 | IC 恢复 + 无回撤 -> ACTIVE |


## 6. 非活跃因子详情（全量）

| # | 因子 | 来源 | 状态 | 仓位 | 最后 IC | 累计回撤 | 连续低IC日 | 连续负IC日 | 最后触发 |
|---|------|------|------|------|---------|---------|----------|----------|---------|
| 1 | `VT_QUA_COMPOSITE` | vibe_trading_candidate | degraded | 0.50 | +0.0000 | 0.261 | 35d | 0d | IC<0.02 连续 35d -> DEGRADED |
| 2 | `VOL_SKEW` | existing | degraded | 0.25 | +0.2003 | 0.112 | 0d | 0d | cum_dd=0.112 >= 0.08 -> 减至 25% |
| 3 | `LIQ_RSVP` | existing | degraded | 0.00 | +0.4289 | 0.044 | 0d | 0d | cum_dd=0.109 >= 0.08 -> 减至 25% |
| 4 | `VAL_PE` | existing | degraded | 0.50 | +0.0000 | 0.000 | 35d | 0d | IC<0.02 连续 35d -> DEGRADED |
| 5 | `VAL_PB` | existing | degraded | 0.50 | +0.0000 | 0.000 | 35d | 0d | IC<0.02 连续 35d -> DEGRADED |
| 6 | `VAL_PS` | existing | degraded | 0.50 | +0.0000 | 0.000 | 35d | 0d | IC<0.02 连续 35d -> DEGRADED |
| 7 | `VAL_PCF` | existing | degraded | 0.50 | +0.0000 | 0.000 | 35d | 0d | IC<0.02 连续 35d -> DEGRADED |
| 8 | `VAL_EARNINGS_YIELD` | existing | degraded | 0.50 | +0.0000 | 0.000 | 35d | 0d | IC<0.02 连续 35d -> DEGRADED |
| 9 | `VAL_BOOK_YIELD` | existing | degraded | 0.50 | +0.0000 | 0.000 | 35d | 0d | IC<0.02 连续 35d -> DEGRADED |
| 10 | `VAL_DIVIDEND_YIELD` | existing | degraded | 0.50 | +0.0000 | 0.000 | 35d | 0d | IC<0.02 连续 35d -> DEGRADED |
| 11 | `VAL_FCF_YIELD` | existing | degraded | 0.50 | +0.0000 | 0.000 | 35d | 0d | IC<0.02 连续 35d -> DEGRADED |
| 12 | `VAL_EV_EBITDA` | existing | degraded | 0.50 | +0.0000 | 0.000 | 35d | 0d | IC<0.02 连续 35d -> DEGRADED |
| 13 | `VAL_SALES_EV` | existing | degraded | 0.50 | +0.0000 | 0.000 | 35d | 0d | IC<0.02 连续 35d -> DEGRADED |
| 14 | `QUA_ROE` | existing | degraded | 0.50 | +0.0000 | 0.000 | 35d | 0d | IC<0.02 连续 35d -> DEGRADED |
| 15 | `QUA_ROA` | existing | degraded | 0.50 | +0.0000 | 0.000 | 35d | 0d | IC<0.02 连续 35d -> DEGRADED |
| 16 | `QUA_ROIC` | existing | degraded | 0.50 | +0.0000 | 0.000 | 35d | 0d | IC<0.02 连续 35d -> DEGRADED |
| 17 | `QUA_GROSS_MARGIN` | existing | degraded | 0.50 | +0.0000 | 0.000 | 35d | 0d | IC<0.02 连续 35d -> DEGRADED |
| 18 | `QUA_NET_MARGIN` | existing | degraded | 0.50 | +0.0000 | 0.000 | 35d | 0d | IC<0.02 连续 35d -> DEGRADED |
| 19 | `QUA_DEBT_TO_EQUITY` | existing | degraded | 0.50 | +0.0000 | 0.000 | 35d | 0d | IC<0.02 连续 35d -> DEGRADED |
| 20 | `QUA_CURRENT_RATIO` | existing | degraded | 0.50 | +0.0000 | 0.000 | 35d | 0d | IC<0.02 连续 35d -> DEGRADED |
| 21 | `QUA_ACCRUALS` | existing | degraded | 0.50 | +0.0000 | 0.000 | 35d | 0d | IC<0.02 连续 35d -> DEGRADED |
| 22 | `SIZE_LOG_MCAP` | existing | degraded | 0.50 | +0.0000 | 0.000 | 35d | 0d | IC<0.02 连续 35d -> DEGRADED |
| 23 | `SIZE_LOG_NS` | existing | degraded | 0.50 | +0.0000 | 0.000 | 35d | 0d | IC<0.02 连续 35d -> DEGRADED |
| 24 | `SIZE_LOG_REV` | existing | degraded | 0.50 | +0.0000 | 0.000 | 35d | 0d | IC<0.02 连续 35d -> DEGRADED |
| 25 | `SIZE_LOG_ASSETS` | existing | degraded | 0.50 | +0.0000 | 0.000 | 35d | 0d | IC<0.02 连续 35d -> DEGRADED |
| 26 | `SIZE_SMALL_LARGE_RATIO` | existing | degraded | 0.50 | +0.0000 | 0.000 | 35d | 0d | IC<0.02 连续 35d -> DEGRADED |
| 27 | `SIZE_NON_LINEAR` | existing | degraded | 0.50 | +0.0000 | 0.000 | 35d | 0d | IC<0.02 连续 35d -> DEGRADED |
| 28 | `SIZE_CUBIC` | existing | degraded | 0.50 | +0.0000 | 0.000 | 35d | 0d | IC<0.02 连续 35d -> DEGRADED |
| 29 | `VT_VAL_COMPOSITE` | vibe_trading_candidate | degraded | 0.50 | +0.0000 | 0.000 | 35d | 0d | IC<0.02 连续 35d -> DEGRADED |
| 30 | `VT_VAL_EARNINGS_YIELD_SCALED` | vibe_trading_candidate | degraded | 0.50 | +0.0000 | 0.000 | 35d | 0d | IC<0.02 连续 35d -> DEGRADED |
| 31 | `VT_SIZE_LOG_NORMALIZED` | vibe_trading_candidate | degraded | 0.50 | +0.0000 | 0.000 | 35d | 0d | IC<0.02 连续 35d -> DEGRADED |
| 32 | `VT_GROWTH_COMPOSITE` | vibe_trading_candidate | degraded | 0.50 | +0.0000 | 0.000 | 35d | 0d | IC<0.02 连续 35d -> DEGRADED |
| 33 | `MOM_REVERSAL_20D` | existing | disabled | 0.00 | -0.4500 | 0.484 | 15d | 14d | IC<0 连续 14d -> DISABLED |
| 34 | `MOM_12_1M` | existing | disabled | 0.00 | -0.3691 | 0.451 | 15d | 15d | IC<0 连续 15d -> DISABLED |
| 35 | `VT_REV_OVERREACTION` | vibe_trading_candidate | disabled | 0.00 | -0.1703 | 0.420 | 14d | 13d | IC<0 连续 13d -> DISABLED |
| 36 | `MOM_252D` | existing | disabled | 0.00 | -0.3262 | 0.365 | 12d | 12d | IC<0 连续 12d -> DISABLED |
| 37 | `VT_MOM_GAP` | vibe_trading_candidate | disabled | 0.00 | -0.4630 | 0.299 | 15d | 15d | IC<0 连续 15d -> DISABLED |
| 38 | `MOM_VOLUME_ADJ` | existing | disabled | 0.00 | -0.5552 | 0.247 | 10d | 10d | IC<0 连续 10d -> DISABLED |
| 39 | `LIQ_AMIHUD` | existing | disabled | 0.00 | -0.3823 | 0.238 | 15d | 15d | IC<0 连续 15d -> DISABLED |
| 40 | `MOM_60D` | existing | disabled | 0.00 | -0.6679 | 0.209 | 11d | 11d | IC<0 连续 11d -> DISABLED |
| 41 | `MOM_INDUSTRY_ADJ` | existing | disabled | 0.00 | -0.6679 | 0.209 | 11d | 11d | IC<0 连续 11d -> DISABLED |
| 42 | `MOM_120D` | existing | disabled | 0.00 | -0.6520 | 0.197 | 11d | 11d | IC<0 连续 11d -> DISABLED |
| 43 | `VT_LIQ_AMIHUD_SCALED` | vibe_trading_candidate | disabled | 0.00 | -0.1395 | 0.192 | 15d | 15d | IC<0 连续 15d -> DISABLED |
| 44 | `VT_MOM_ILLIQUID_60D` | vibe_trading_candidate | disabled | 0.00 | -0.5154 | 0.151 | 11d | 11d | IC<0 连续 11d -> DISABLED |
| 45 | `LIQ_VOLUME_ZSCORE` | existing | disabled | 0.00 | -0.7471 | 0.143 | 11d | 11d | IC<0 连续 11d -> DISABLED |
| 46 | `LIQ_TURNOVER_60D` | existing | disabled | 0.00 | +0.1647 | 0.087 | 0d | 0d | cum_dd=0.087 >= 0.08 -> 减至 25% |
| 47 | `VT_REV_VOLUME_SPIKE` | vibe_trading_candidate | disabled | 0.00 | -0.8151 | 0.035 | 11d | 11d | IC<0 连续 11d -> DISABLED |
| 48 | `MOM_REVERSAL_5D` | existing | disabled | 0.00 | -0.8232 | 0.026 | 11d | 11d | IC<0 连续 11d -> DISABLED |
| 49 | `VT_REV_SHORT_TERM` | vibe_trading_candidate | disabled | 0.00 | -0.8232 | 0.026 | 11d | 11d | IC<0 连续 11d -> DISABLED |
| 50 | `VT_VOL_REGIME` | vibe_trading_candidate | emergency_exit | 0.00 | +0.5251 | 0.229 | 0d | 0d | cum_dd=0.229 >= 0.12 -> EMERGENCY_EXIT |
| 51 | `LIQ_TURNOVER_20D` | existing | emergency_exit | 0.00 | +0.1726 | 0.178 | 0d | 0d | cum_dd=0.178 >= 0.12 -> EMERGENCY_EXIT |
| 52 | `LIQ_DEPTH` | existing | emergency_exit | 0.00 | +0.1726 | 0.178 | 0d | 0d | cum_dd=0.178 >= 0.12 -> EMERGENCY_EXIT |
| 53 | `MOM_UP_DOWN` | existing | emergency_exit | 0.00 | +0.2291 | 0.000 | 0d | 0d | daily_pnl=-0.0316 < -0.03 -> 减半 |
| 54 | `VOL_BETA` | existing | emergency_exit | 0.00 | +0.3444 | 0.000 | 0d | 0d | cum_dd=0.165 >= 0.12 -> EMERGENCY_EXIT |
| 55 | `LIQ_ZERO_RET_DAYS` | existing | warned | 1.00 | -0.0924 | 0.052 | 2d | 2d | IC 偏低 1d -> WARNED |
| 56 | `VT_LIQ_TURNOVER_REGIME` | vibe_trading_candidate | warned | 1.00 | +0.1173 | 0.008 | 0d | 0d | IC 偏低 1d -> WARNED |
| 57 | `VT_VOL_DOWNSIDE_RATIO` | vibe_trading_candidate | warned | 1.00 | -0.1676 | 0.000 | 1d | 1d | IC 偏低 1d -> WARNED |


## 7. 审计轨迹

完整状态持久化于：
```
reports/kill_switch/2026-07-25/kill_switch_state.json
```

包含每个因子的：
- 当前状态 + 仓位比例
- IC 历史（最近 5 日）
- PnL 历史
- 触发记录列表
- 累计回撤 / 连续低 IC 日数 / 连续负 IC 日数

## 8. 集成入口

本批次由 `daily_workflow.py` Phase 9 自动调用 `run_daily_kill_switch(trade_date)` 生成。

---
*本仪表盘由 FactorKillSwitch v1.0 + kill_switch_daily_runner 自动生成。*
