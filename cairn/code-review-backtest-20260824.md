# 代码审查明细 — backtest 模块 (2026-08-24)

> 审查对象: `ms_strategy/src/backtest/` (8 文件: cost_aware_backtest/cost_model/combinatorial_purged_cv/metrics/noise_injection_test/scenario_lib/walk_forward)
> 审查批次: 全量量化逐模块审查计划 (plan: quant-system-module-audit-20260824) 第 4/10 模块
> 重点: 成本模型 (memory 43466822 滑点分层 / 84396458 事件驱动+成本扣除)

## 0. 门禁基线

| 门禁 | 结果 |
|---|---|
| ruff 静态扫描 | ✅ All checks passed |

## 1. 缺陷清单与修复状态

| ID | 严重度 | 文件 | 缺陷 | 状态 |
|---|---|---|---|---|
| BT-6 | MEDIUM | cost_model.py | `market_impact` participation = qty/daily_volume，qty>volume 时 `sqrt(participation)>1` 冲击成本爆炸（违反容量约束） | ✅ 已修复(钳制到1) |
| BT-2 | MEDIUM | cost_aware_backtest.py | 简化版冲击同样未钳制 participation>1 | ✅ 已修复(钳制到1) |
| BT-8 | MEDIUM | cost_model.py | `market_impact` 入口无 qty/price/volatility 校验，负值/NaN 产生 NaN 或负冲击 | ✅ 已修复(入口钳制) |
| BT-7 | LOW | cost_model.py | `trade_cost` adv=0 时 `daily_volume=max(qty*100,1)` 人为降低 participation(1%) 低估冲击 | ⏸ 记录(P2) |
| BT-4 | LOW | cost_aware_backtest.py | `avg_cost_bps` 量纲混用 (capital/n_trades vs 每笔成本) | ⏸ 记录(P2) |
| BT-5 | LOW | cost_aware_backtest.py | `prices.loc[date]` 无重复/缺失索引容错 | ⏸ 记录(P2) |
| BT-1 | 误判撤销 | cost_aware_backtest.py | vol_30d 窗口 `.iloc[i-30:i]` 实为滞后窗口(不含当日), 无前视偏差 | ✅ 无缺陷 |

**汇总**: MEDIUM×3, LOW×3；已修复 3 项, 记录 3 项 P2 存量。

## 2. 修复详情

### BT-6/BT-2 — participation 钳制
`market_impact` (cost_model) 与简化版 (cost_aware_backtest) 均将 `participation_rate = min(qty/daily_volume, 1.0)`。大单冲击成本不再爆炸。

### BT-8 — 入口防御
`market_impact` 入口钳制: qty<=0/price<=0/daily_volume<=0/volatility<=0/非有限 → 返回 0.0 (非 NaN/负冲击)。

## 3. 验证快照

| 验证 | 结果 |
|---|---|
| 大单 qty=200k>vol=100k | impact=5680 (有限, 修复前 sqrt(2)=1.414 放大) |
| 正常单 qty=1k | impact=2.84 (合理) |
| 负 qty / price=0 / 负 vol / vol=0 | 全返回 0.0 (防御生效) |
| 回归测试 | 7 passed (test_backtest_audit_regression_20260824.py) |
| ruff_incremental_gate | ✅ 4 文件无新增违规 |

## 4. 审查结论 (memory 对应)

- 成本模型已正确实现: 佣金万2.5/印花税千1(卖出)/过户费万0.2/AC平方根冲击 (memory 43466822)。
- 未发现前视偏差 (vol_30d 为滞后窗口)、事件驱动/成本扣除链路完整 (memory 84396458)。
- 主要缺陷集中在**大单冲击钳制缺失** (BT-6) 与**入口 NaN 防御** (BT-8)。

## 5. 待办 (后续批次)

- BT-7: adv 缺失时应显式标记降级而非 `max(qty*100,1)` 低估冲击。
- BT-4/BT-5: avg_cost_bps 量纲、索引容错。
