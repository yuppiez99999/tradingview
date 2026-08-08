# FactorKillSwitch 实时监控仪表盘

> 生成时间：2026-07-25 10:43:32
> 批次：20260725_104332
> 历史监控窗口：30 日

## 1. 监控概览

| 项目 | 值 |
|------|-----|
| 监控因子总数 | 67 |
| 现有生产因子 | 51 |
| 候选因子（vibe_trading） | 16 |
| ACTIVE 比例 | 6/67 (9.0%) |

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
| degraded | 46 | 68.7% |
| emergency_exit | 7 | 10.4% |
| active | 6 | 9.0% |
| disabled | 5 | 7.5% |
| warned | 3 | 4.5% |


## 4. 按来源 × 状态分布

| 来源 | 状态 | 因子数 |
|------|------|--------|
| existing | degraded | 37 |
| existing | emergency_exit | 6 |
| existing | active | 4 |
| existing | disabled | 3 |
| existing | warned | 1 |
| vibe_trading_candidate | degraded | 9 |
| vibe_trading_candidate | active | 2 |
| vibe_trading_candidate | disabled | 2 |
| vibe_trading_candidate | warned | 2 |
| vibe_trading_candidate | emergency_exit | 1 |


## 5. 非活跃因子详情

| # | 因子 | 来源 | 状态 | 仓位 | 最后 IC | 累计回撤 | 连续低IC日 | 连续负IC日 | 最后触发 |
|---|------|------|------|------|---------|---------|----------|----------|---------|
| 1 | `MOM_REVERSAL_20D` | existing | degraded | 0.50 | -0.4500 | 0.338 | 10d | 9d | IC<0.02 连续 10d -> DEGRADED |
| 2 | `VT_REV_OVERREACTION` | vibe_trading_candidate | degraded | 0.50 | -0.1703 | 0.274 | 9d | 8d | IC<0.02 连续 9d -> DEGRADED |
| 3 | `MOM_252D` | existing | degraded | 0.50 | -0.3262 | 0.230 | 7d | 7d | IC<0.02 连续 7d -> DEGRADED |
| 4 | `MOM_VOLUME_ADJ` | existing | degraded | 0.50 | -0.5552 | 0.161 | 5d | 5d | IC<0.02 连续 5d -> DEGRADED |
| 5 | `VT_QUA_COMPOSITE` | vibe_trading_candidate | degraded | 0.50 | +0.0000 | 0.150 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 6 | `MOM_60D` | existing | degraded | 0.50 | -0.6679 | 0.132 | 6d | 6d | IC<0.02 连续 6d -> DEGRADED |
| 7 | `MOM_INDUSTRY_ADJ` | existing | degraded | 0.50 | -0.6679 | 0.132 | 6d | 6d | IC<0.02 连续 6d -> DEGRADED |
| 8 | `MOM_120D` | existing | degraded | 0.50 | -0.6520 | 0.117 | 6d | 6d | IC<0.02 连续 6d -> DEGRADED |
| 9 | `VOL_SKEW` | existing | degraded | 0.25 | +0.2003 | 0.099 | 0d | 0d | cum_dd=0.099 >= 0.08 -> 减至 25% |
| 10 | `VOL_20D` | existing | degraded | 0.00 | +0.4357 | 0.096 | 0d | 0d | cum_dd=0.096 >= 0.08 -> 减至 25% |
| 11 | `VOL_60D` | existing | degraded | 0.00 | +0.4401 | 0.096 | 0d | 0d | cum_dd=0.096 >= 0.08 -> 减至 25% |
| 12 | `VOL_IDIO` | existing | degraded | 0.00 | +0.4648 | 0.096 | 0d | 0d | cum_dd=0.096 >= 0.08 -> 减至 25% |
| 13 | `VT_MOM_ILLIQUID_60D` | vibe_trading_candidate | degraded | 0.50 | -0.5154 | 0.096 | 6d | 6d | IC<0.02 连续 6d -> DEGRADED |
| 14 | `LIQ_VOLUME_ZSCORE` | existing | degraded | 0.50 | -0.7471 | 0.091 | 6d | 6d | IC<0.02 连续 6d -> DEGRADED |
| 15 | `VT_REV_VOLUME_SPIKE` | vibe_trading_candidate | degraded | 0.50 | -0.8151 | 0.031 | 6d | 6d | IC<0.02 连续 6d -> DEGRADED |
| 16 | `MOM_REVERSAL_5D` | existing | degraded | 0.50 | -0.8232 | 0.025 | 6d | 6d | IC<0.02 连续 6d -> DEGRADED |
| 17 | `VT_REV_SHORT_TERM` | vibe_trading_candidate | degraded | 0.50 | -0.8232 | 0.025 | 6d | 6d | IC<0.02 连续 6d -> DEGRADED |
| 18 | `VAL_PE` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 19 | `VAL_PB` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 20 | `VAL_PS` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 21 | `VAL_PCF` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 22 | `VAL_EARNINGS_YIELD` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 23 | `VAL_BOOK_YIELD` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 24 | `VAL_DIVIDEND_YIELD` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 25 | `VAL_FCF_YIELD` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 26 | `VAL_EV_EBITDA` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 27 | `VAL_SALES_EV` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 28 | `QUA_ROE` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 29 | `QUA_ROA` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 30 | `QUA_ROIC` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 31 | `QUA_GROSS_MARGIN` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 32 | `QUA_NET_MARGIN` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 33 | `QUA_DEBT_TO_EQUITY` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 34 | `QUA_CURRENT_RATIO` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 35 | `QUA_ACCRUALS` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 36 | `SIZE_LOG_MCAP` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 37 | `SIZE_LOG_NS` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 38 | `SIZE_LOG_REV` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 39 | `SIZE_LOG_ASSETS` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 40 | `SIZE_SMALL_LARGE_RATIO` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 41 | `SIZE_NON_LINEAR` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 42 | `SIZE_CUBIC` | existing | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 43 | `VT_VAL_COMPOSITE` | vibe_trading_candidate | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 44 | `VT_VAL_EARNINGS_YIELD_SCALED` | vibe_trading_candidate | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 45 | `VT_SIZE_LOG_NORMALIZED` | vibe_trading_candidate | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 46 | `VT_GROWTH_COMPOSITE` | vibe_trading_candidate | degraded | 0.50 | +0.0000 | 0.000 | 30d | 0d | IC<0.02 连续 30d -> DEGRADED |
| 47 | `MOM_12_1M` | existing | disabled | 0.00 | -0.3691 | 0.278 | 10d | 10d | IC<0 连续 10d -> DISABLED |
| 48 | `VT_MOM_GAP` | vibe_trading_candidate | disabled | 0.00 | -0.4630 | 0.152 | 10d | 10d | IC<0 连续 10d -> DISABLED |
| 49 | `LIQ_AMIHUD` | existing | disabled | 0.00 | -0.3823 | 0.149 | 10d | 10d | IC<0 连续 10d -> DISABLED |
| 50 | `VT_LIQ_AMIHUD_SCALED` | vibe_trading_candidate | disabled | 0.00 | -0.1395 | 0.134 | 10d | 10d | IC<0 连续 10d -> DISABLED |
| 51 | `LIQ_TURNOVER_60D` | existing | disabled | 0.00 | +0.1647 | 0.069 | 0d | 0d | daily_pnl=-0.0414 < -0.03 -> 减半 |
| 52 | `VT_VOL_REGIME` | vibe_trading_candidate | emergency_exit | 0.00 | +0.5251 | 0.325 | 0d | 0d | cum_dd=0.325 >= 0.12 -> EMERGENCY_EXIT |
| 53 | `LIQ_RSVP` | existing | emergency_exit | 0.00 | +0.4289 | 0.193 | 0d | 0d | cum_dd=0.193 >= 0.12 -> EMERGENCY_EXIT |
| 54 | `LIQ_SPREAD` | existing | emergency_exit | 0.00 | +0.4630 | 0.155 | 0d | 0d | cum_dd=0.155 >= 0.12 -> EMERGENCY_EXIT |
| 55 | `LIQ_TURNOVER_20D` | existing | emergency_exit | 0.00 | +0.1726 | 0.120 | 0d | 0d | cum_dd=0.120 >= 0.12 -> EMERGENCY_EXIT |
| 56 | `LIQ_DEPTH` | existing | emergency_exit | 0.00 | +0.1726 | 0.120 | 0d | 0d | cum_dd=0.120 >= 0.12 -> EMERGENCY_EXIT |
| 57 | `VOL_BETA` | existing | emergency_exit | 0.00 | +0.3444 | 0.072 | 0d | 0d | cum_dd=0.165 >= 0.12 -> EMERGENCY_EXIT |
| 58 | `MOM_UP_DOWN` | existing | emergency_exit | 0.00 | +0.2291 | 0.000 | 0d | 0d | daily_pnl=-0.0316 < -0.03 -> 减半 |
| 59 | `LIQ_ZERO_RET_DAYS` | existing | warned | 1.00 | -0.0924 | 0.040 | 2d | 2d | IC 偏低 1d -> WARNED |
| 60 | `VT_LIQ_TURNOVER_REGIME` | vibe_trading_candidate | warned | 1.00 | +0.1173 | 0.008 | 0d | 0d | IC 偏低 1d -> WARNED |
| 61 | `VT_VOL_DOWNSIDE_RATIO` | vibe_trading_candidate | warned | 1.00 | -0.1676 | 0.000 | 1d | 1d | IC 偏低 1d -> WARNED |


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
reports/kill_switch/20260725_104332/kill_switch_state.json
```

包含每个因子的：
- 当前状态 + 仓位比例
- IC 历史（最近 30 日）
- PnL 历史
- 触发记录列表
- 累计回撤 / 连续低 IC 日数 / 连续负 IC 日数

---
*本仪表盘由 FactorKillSwitch v1.0 自动生成，遵循 DECISION_v1.0 锁定参数。*
