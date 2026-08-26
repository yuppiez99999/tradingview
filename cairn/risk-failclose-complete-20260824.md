# 风控 fail-close 完整化 (2026-08-24)

> 工作线 B: 风控 fail-close 完整化
> 本轮聚焦: DTE-6 auto-confirm 护栏 (最直接 fail-close 改进); RK-1/DTE-8 记录后续
> 状态: DTE-6 已实现; RK-1/DTE-8 记录为后续批次

## 0. 结论

**DTE-6 (--auto-confirm 风控护栏) 已实现**: 自动确认 (绕过人工审核直接下单) 仅允许在
显式 TRADING_ENV ∈ {shadow, production} 下, 否则告警并降级为"不自动确认" (人工确认保护)。
RK-1 (对冲阈值统一) / DTE-8 (trade_plan 同步) 为跨模块架构改动, 记录后续批次。

## 1. 修复详情

### DTE-6 (daily_trade_executor.py _run_mode)
`--auto-confirm` 加环境门控:
- TRADING_ENV ∈ {shadow, production} → 允许自动确认
- 否则 (默认 ci/sim/未设置) → `[BLOCK]` 告警 + `args.auto_confirm = False` (降级为不自动确认)

```python
_env = os.environ.get("TRADING_ENV", "sim").strip().lower()
if _env not in ("shadow", "production"):
    logger.error("[BLOCK] --auto-confirm 需显式 TRADING_ENV=shadow|production, 已降级为不自动确认")
    args.auto_confirm = False
```

新增 import: `os`。

## 2. 验证快照

| 验证 | 结果 |
|---|---|
| TRADING_ENV=production | 允许自动确认 ✅ |
| TRADING_ENV=shadow | 允许 (演练) ✅ |
| 默认 sim | 阻断自动确认 ✅ |
| ci 环境 | 阻断自动确认 ✅ |
| 回归测试 | 5 passed (test_risk_failclose_20260824.py) |
| ruff_incremental_gate | ✅ 3 文件无新增违规 |

## 3. 后续批次 (跨模块架构改动, 非实盘阻断)

### RK-1 — 对冲阈值统一
risk_manager.auto_hedge 硬编码阈值 (beta>0.7/vix>30) 与 hedging 模块 BetaHedger 重复且配置脱节
(DRY + memory 8 教训)。需统一到配置。

### DTE-8 — trade_plan 双轨同步
daily_trade_executor 建仓结果不写回 trade_plan, 与下游 live_scheduler/hedge 双轨不同步。
需建仓结果回写 trade_plan (配合 DTE-1 FillsStore 落盘)。

## 4. 关联

- DTE-6 与 UE-1 (统一实盘门控) 互补: UE-1 管撮合/下单入口, DTE-6 管自动确认入口。
- 当前系统 TRADING_ENV 默认 sim, DTE-6 不激活, 不影响现有手动确认流程; 设 shadow/production 后自动激活。
