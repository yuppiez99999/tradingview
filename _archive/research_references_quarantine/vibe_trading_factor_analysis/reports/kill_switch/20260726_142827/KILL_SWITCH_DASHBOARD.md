# FactorKillSwitch 实时监控仪表盘

> 生成时间：2026-07-26 14:28:27
> 批次：20260726_142827
> 历史监控窗口：30 日

## 1. 监控概览

| 项目 | 值 |
|------|-----|
| 监控因子总数 | 4 |
| 现有生产因子 | 2 |
| 候选因子（vibe_trading） | 2 |
| ACTIVE 比例 | 1/4 (25.0%) |

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
| retired | 3 | 75.0% |
| active | 1 | 25.0% |


## 4. 按来源 × 状态分布

| 来源 | 状态 | 因子数 |
|------|------|--------|
| existing | active | 1 |
| existing | retired | 1 |
| vibe_trading_candidate | retired | 2 |


## 5. 非活跃因子详情

| # | 因子 | 来源 | 状态 | 仓位 | 最后 IC | 累计回撤 | 连续低IC日 | 连续负IC日 | 最后触发 |
|---|------|------|------|------|---------|---------|----------|----------|---------|
| 1 | `SENTIMENT` | vibe_trading_candidate | retired | 0.00 | -0.4472 | 0.006 | 20d | 20d | IC<0 连续 20d -> RETIRED |
| 2 | `VOL_20D` | existing | retired | 0.00 | -0.0719 | 0.000 | 20d | 20d | IC<0 连续 20d -> RETIRED |
| 3 | `LIQUIDITY` | vibe_trading_candidate | retired | 0.00 | -0.0719 | 0.000 | 20d | 20d | IC<0 连续 20d -> RETIRED |


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
reports/kill_switch/20260726_142827/kill_switch_state.json
```

包含每个因子的：
- 当前状态 + 仓位比例
- IC 历史（最近 30 日）
- PnL 历史
- 触发记录列表
- 累计回撤 / 连续低 IC 日数 / 连续负 IC 日数

---
*本仪表盘由 FactorKillSwitch v1.0 自动生成，遵循 DECISION_v1.0 锁定参数。*
