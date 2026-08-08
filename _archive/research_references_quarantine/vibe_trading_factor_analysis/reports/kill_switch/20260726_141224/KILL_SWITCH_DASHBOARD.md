# FactorKillSwitch 实时监控仪表盘

> 生成时间：2026-07-26 14:12:24
> 批次：20260726_141224
> 历史监控窗口：30 日

## 1. 监控概览

| 项目 | 值 |
|------|-----|
| 监控因子总数 | 75 |
| 现有生产因子 | 51 |
| 候选因子（vibe_trading） | 24 |
| ACTIVE 比例 | 6/75 (8.0%) |

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
| degraded | 36 | 48.0% |
| emergency_exit | 14 | 18.7% |
| disabled | 10 | 13.3% |
| warned | 8 | 10.7% |
| active | 6 | 8.0% |
| retired | 1 | 1.3% |


## 4. 按来源 × 状态分布

| 来源 | 状态 | 因子数 |
|------|------|--------|
| existing | degraded | 30 |
| existing | disabled | 7 |
| existing | emergency_exit | 6 |
| existing | active | 4 |
| existing | warned | 4 |
| vibe_trading_candidate | emergency_exit | 8 |
| vibe_trading_candidate | degraded | 6 |
| vibe_trading_candidate | warned | 4 |
| vibe_trading_candidate | disabled | 3 |
| vibe_trading_candidate | active | 2 |
| vibe_trading_candidate | retired | 1 |


## 5. 非活跃因子详情

| # | 因子 | 来源 | 状态 | 仓位 | 最后 IC | 累计回撤 | 连续低IC日 | 连续负IC日 | 最后触发 |
|---|------|------|------|------|---------|---------|----------|----------|---------|
| 1 | `QUA_ROE` | existing | degraded | 0.00 | +0.3703 | 0.113 | 0d | 0d | cum_dd=0.113 >= 0.08 -> 减至 25% |
| 2 | `LIQ_VOLUME_ZSCORE` | existing | degraded | 0.50 | -0.4944 | 0.107 | 9d | 9d | IC<0.02 连续 9d -> DEGRADED |
| 3 | `MOM_60D` | existing | degraded | 0.50 | -0.3780 | 0.104 | 7d | 7d | IC<0.02 连续 7d -> DEGRADED |
| 4 | `MOM_VOLUME_ADJ` | existing | degraded | 0.50 | -0.2719 | 0.104 | 7d | 7d | IC<0.02 连续 7d -> DEGRADED |
| 5 | `MOM_INDUSTRY_ADJ` | existing | degraded | 0.50 | -0.3780 | 0.104 | 7d | 7d | IC<0.02 连续 7d -> DEGRADED |
| 6 | `VT_REV_VOL_DRAIN_INV` | vibe_trading_candidate | degraded | 0.50 | -0.4837 | 0.095 | 5d | 3d | IC<0.02 连续 5d -> DEGRADED |
| 7 | `QUA_DEBT_TO_EQUITY` | existing | degraded | 0.25 | +0.0737 | 0.094 | 0d | 0d | cum_dd=0.094 >= 0.08 -> 减至 25% |
| 8 | `SIZE_LOG_MCAP` | existing | degraded | 0.25 | -0.1128 | 0.082 | 1d | 1d | cum_dd=0.082 >= 0.08 -> 减至 25% |
| 9 | `SIZE_NON_LINEAR` | existing | degraded | 0.25 | -0.0863 | 0.082 | 1d | 1d | cum_dd=0.082 >= 0.08 -> 减至 25% |
| 10 | `SIZE_CUBIC` | existing | degraded | 0.25 | -0.0057 | 0.082 | 1d | 1d | cum_dd=0.082 >= 0.08 -> 减至 25% |
| 11 | `VT_REV_BREADTH_5D` | vibe_trading_candidate | degraded | 0.50 | -0.7557 | 0.074 | 7d | 7d | IC<0.02 连续 7d -> DEGRADED |
| 12 | `LIQ_RSVP` | existing | degraded | 0.00 | -0.0130 | 0.059 | 1d | 1d | cum_dd=0.086 >= 0.08 -> 减至 25% |
| 13 | `VT_MOM_HIGH_VOL_ALPHA` | vibe_trading_candidate | degraded | 0.00 | +0.3682 | 0.057 | 0d | 0d | cum_dd=0.081 >= 0.08 -> 减至 25% |
| 14 | `VT_MICRO_VOL_SKEW_INV` | vibe_trading_candidate | degraded | 0.50 | -0.3165 | 0.042 | 3d | 3d | daily_pnl=-0.0434 < -0.03 -> 减半 |
| 15 | `VOL_20D` | existing | degraded | 0.00 | +0.0497 | 0.038 | 0d | 0d | cum_dd=0.093 >= 0.08 -> 减至 25% |
| 16 | `VOL_60D` | existing | degraded | 0.00 | +0.0503 | 0.038 | 0d | 0d | cum_dd=0.093 >= 0.08 -> 减至 25% |
| 17 | `VOL_IDIO` | existing | degraded | 0.00 | +0.1114 | 0.038 | 0d | 0d | cum_dd=0.093 >= 0.08 -> 减至 25% |
| 18 | `MOM_120D` | existing | degraded | 0.50 | -0.3582 | 0.037 | 7d | 7d | IC<0.02 连续 7d -> DEGRADED |
| 19 | `VT_MICRO_CLOSE_STRENGTH` | vibe_trading_candidate | degraded | 0.00 | +0.5407 | 0.035 | 0d | 0d | cum_dd=0.115 >= 0.08 -> 减至 25% |
| 20 | `VT_VOL_OVERNIGHT_RATIO` | vibe_trading_candidate | degraded | 0.00 | -0.1498 | 0.035 | 4d | 4d | cum_dd=0.115 >= 0.08 -> 减至 25% |
| 21 | `LIQ_ZERO_RET_DAYS` | existing | degraded | 0.50 | -0.3332 | 0.035 | 3d | 3d | daily_pnl=-0.0597 < -0.03 -> 减半 |
| 22 | `VAL_PCF` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 23 | `VAL_EARNINGS_YIELD` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 24 | `VAL_BOOK_YIELD` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 25 | `VAL_DIVIDEND_YIELD` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 26 | `VAL_FCF_YIELD` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 27 | `VAL_EV_EBITDA` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 28 | `VAL_SALES_EV` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 29 | `QUA_ROA` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 30 | `QUA_ROIC` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 31 | `QUA_NET_MARGIN` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 32 | `QUA_CURRENT_RATIO` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 33 | `QUA_ACCRUALS` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 34 | `SIZE_LOG_NS` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 35 | `SIZE_LOG_ASSETS` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 36 | `SIZE_SMALL_LARGE_RATIO` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 37 | `MOM_REVERSAL_20D` | existing | disabled | 0.00 | -0.1945 | 0.326 | 11d | 11d | IC<0 连续 11d -> DISABLED |
| 38 | `VT_VOL_CLUSTERING` | vibe_trading_candidate | disabled | 0.00 | -0.1393 | 0.223 | 17d | 17d | IC<0 连续 17d -> DISABLED |
| 39 | `SIZE_LOG_REV` | existing | disabled | 0.00 | -0.1606 | 0.142 | 13d | 13d | IC<0 连续 13d -> DISABLED |
| 40 | `LIQ_AMIHUD` | existing | disabled | 0.00 | -0.0616 | 0.138 | 11d | 11d | IC<0 连续 11d -> DISABLED |
| 41 | `VT_MOM_HIGH_VOL_ALPHA_INV` | vibe_trading_candidate | disabled | 0.00 | -0.3682 | 0.086 | 5d | 5d | IC<0 连续 10d -> DISABLED |
| 42 | `VAL_PE` | existing | disabled | 0.00 | +0.1622 | 0.080 | 0d | 0d | cum_dd=0.083 >= 0.08 -> 减至 25% |
| 43 | `VAL_PS` | existing | disabled | 0.00 | +0.1639 | 0.079 | 0d | 0d | daily_pnl=-0.0496 < -0.03 -> 减半 |
| 44 | `VOL_120D` | existing | disabled | 0.00 | +0.0283 | 0.016 | 0d | 0d | cum_dd=0.081 >= 0.08 -> 减至 25% |
| 45 | `VT_MOM_AUTOCORR_5D` | vibe_trading_candidate | disabled | 0.00 | -0.3345 | 0.007 | 4d | 4d | cum_dd=0.081 >= 0.08 -> 减至 25% |
| 46 | `MOM_12_1M` | existing | disabled | 0.00 | -0.0003 | 0.000 | 11d | 11d | IC<0 连续 11d -> DISABLED |
| 47 | `VT_MOM_ACCEL_5_20` | vibe_trading_candidate | emergency_exit | 0.00 | +0.2879 | 0.353 | 0d | 0d | cum_dd=0.353 >= 0.12 -> EMERGENCY_EXIT |
| 48 | `VT_REV_OVERREACTION` | vibe_trading_candidate | emergency_exit | 0.00 | +0.0346 | 0.265 | 0d | 0d | cum_dd=0.265 >= 0.12 -> EMERGENCY_EXIT |
| 49 | `VT_MICRO_GAP_TREND` | vibe_trading_candidate | emergency_exit | 0.00 | +0.0859 | 0.259 | 0d | 0d | cum_dd=0.259 >= 0.12 -> EMERGENCY_EXIT |
| 50 | `VOL_SKEW` | existing | emergency_exit | 0.00 | +0.1990 | 0.213 | 0d | 0d | cum_dd=0.213 >= 0.12 -> EMERGENCY_EXIT |
| 51 | `LIQ_TURNOVER_60D` | existing | emergency_exit | 0.00 | +0.0970 | 0.198 | 0d | 0d | cum_dd=0.198 >= 0.12 -> EMERGENCY_EXIT |
| 52 | `VT_LIQ_AMIHUD_SCALED` | vibe_trading_candidate | emergency_exit | 0.00 | +0.0657 | 0.181 | 0d | 0d | cum_dd=0.181 >= 0.12 -> EMERGENCY_EXIT |
| 53 | `VT_MOM_OVERNIGHT_GAP_INV` | vibe_trading_candidate | emergency_exit | 0.00 | -0.3696 | 0.180 | 3d | 3d | cum_dd=0.180 >= 0.12 -> EMERGENCY_EXIT |
| 54 | `VT_MOM_OVERNIGHT_GAP` | vibe_trading_candidate | emergency_exit | 0.00 | +0.3696 | 0.150 | 0d | 0d | cum_dd=0.150 >= 0.12 -> EMERGENCY_EXIT |
| 55 | `LIQ_TURNOVER_20D` | existing | emergency_exit | 0.00 | +0.0923 | 0.148 | 0d | 0d | cum_dd=0.148 >= 0.12 -> EMERGENCY_EXIT |
| 56 | `LIQ_DEPTH` | existing | emergency_exit | 0.00 | +0.0923 | 0.148 | 0d | 0d | cum_dd=0.148 >= 0.12 -> EMERGENCY_EXIT |
| 57 | `VT_GROWTH_COMPOSITE` | vibe_trading_candidate | emergency_exit | 0.00 | +0.1010 | 0.140 | 0d | 0d | cum_dd=0.140 >= 0.12 -> EMERGENCY_EXIT |
| 58 | `VAL_PB` | existing | emergency_exit | 0.00 | +0.1787 | 0.122 | 0d | 0d | cum_dd=0.122 >= 0.12 -> EMERGENCY_EXIT |
| 59 | `LIQ_SPREAD` | existing | emergency_exit | 0.00 | -0.0131 | 0.041 | 1d | 1d | cum_dd=0.121 >= 0.12 -> EMERGENCY_EXIT |
| 60 | `VT_MICRO_CLOSING_MOMENTUM` | vibe_trading_candidate | emergency_exit | 0.00 | +0.2937 | 0.037 | 0d | 0d | cum_dd=0.135 >= 0.12 -> EMERGENCY_EXIT |
| 61 | `VT_MICRO_VOL_SKEW` | vibe_trading_candidate | retired | 0.00 | +0.3165 | 0.221 | 20d | 20d | IC<0 连续 20d -> RETIRED |
| 62 | `VT_MICRO_RANGE_RATIO` | vibe_trading_candidate | warned | 1.00 | +0.1418 | 0.049 | 0d | 0d | IC 偏低 1d -> WARNED |
| 63 | `VOL_BETA` | existing | warned | 1.00 | -0.0863 | 0.011 | 1d | 1d | IC 偏低 1d -> WARNED |
| 64 | `VOL_252D` | existing | warned | 1.00 | +0.0005 | 0.006 | 1d | 0d | IC 偏低 1d -> WARNED |
| 65 | `MOM_REVERSAL_5D` | existing | warned | 1.00 | -0.8714 | 0.000 | 3d | 3d | IC 偏低 1d -> WARNED |
| 66 | `MOM_UP_DOWN` | existing | warned | 1.00 | -0.0048 | 0.000 | 1d | 1d | IC 偏低 1d -> WARNED |
| 67 | `VT_LIQ_TURNOVER_REGIME` | vibe_trading_candidate | warned | 1.00 | +0.3522 | 0.000 | 0d | 0d | IC 偏低 1d -> WARNED |
| 68 | `VT_MICRO_ORDER_IMBALANCE` | vibe_trading_candidate | warned | 1.00 | -0.2306 | 0.000 | 1d | 1d | IC 偏低 1d -> WARNED |
| 69 | `VT_REV_OVERREACTION_INV` | vibe_trading_candidate | warned | 1.00 | -0.0346 | 0.000 | 1d | 1d | IC 偏低 1d -> WARNED |


## 6. 集成方式

本监控可被 daily_workflow.py 集成调用：

```python
from research.vibe_trading_factor_analysis.scripts.run_kill_switch_monitor import (
    load_kill_switch_state, run_daily_update,
)

# Phase 9: FactorKillSwitch 实时监控（新增）
ks = load_kill_switch_state()  # 加载持久化状态
for factor_name, factor_ic, factor_pnl in daily_factor_metrics:
    new_status = run_daily_update(ks, factor_name, ic=factor_ic, daily_pnl=factor_pnl)
    if not new_status.is_tradable:
        logger.warning(f"因子 {factor_name} 触发 KillSwitch: {new_status.status}")
        # 调整生产仓位 / 禁用信号源
```


## 7. 审计轨迹

完整状态持久化于：
```
reports/kill_switch/20260726_141224/kill_switch_state.json
```

包含每个因子的：
- 当前状态 + 仓位比例
- IC 历史（最近 30 日）
- PnL 历史
- 触发记录列表
- 累计回撤 / 连续低 IC 日数 / 连续负 IC 日数

---
*本仪表盘由 FactorKillSwitch v1.0 自动生成，遵循 DECISION_v1.0 锁定参数。*
