# 运维部署 + 灰度发布 (2026-08-24)

> 工作线 E: 运维/监控/部署 + F: 灰度发布流程
> 状态: 基础设施就绪核查完成; 灰度推进建议明确

## 0. 结论

**告警通道、灰度阶段配置、推进条件均已就绪。** 灰度推进 (stage_2 50%) 天数条件已满足,
但建议在影子真实撮合验证后推进 (依赖 DTE-1 建仓落盘持续积累 strategy=build fills)。

## 1. 运维监控核查 (E线)

| 项 | 状态 |
|---|---|
| 告警通道 utils.notify.send_alert | ✅ 可导入/可调用 (钉钉/飞书/日志三通道) |
| EOD 任务 | 已由 daily_workflow/institutional_pipeline 覆盖 (Phase 0-9) |
| 数据管道降级链 | 已由 DataConnectorManager 多源降级 (Wind→TDX→AKShare→新浪) |
| Docker 封装 | ⏸ 记录 (P2, 实盘部署时可评估; 当前 Windows 本地运行) |

## 2. 灰度发布核查 (F线)

| 项 | 值 |
|---|---|
| 灰度阶段 | stage_1(10%) → stage_2(50%) → stage_3(100%) |
| min_running_days | 14 (当前已 23 天) |
| fail-fast | 未触发 ✅ |
| 推进条件 (launch_shadow_account --advance) | 天数+fail-fast 已满足 |
| **trade_log** | **空 (影子无真实撮合)** ← 推进前需谨慎 |

## 3. 灰度推进建议

**推进 stage_2 的天数条件已满足 (23>14, fail-fast 未触发), 但:**

1. **影子账户无真实撮合** (audit-shadow 核查: trade_log 空, NAV 回算) → 绩效不可信。
2. **DTE-1 刚落地建仓落盘 FillsStore (strategy=build)** → 影子可消费此 fills 积累真实成交。
3. **建议**: 先让影子账户持续运行 1-2 周积累 strategy=build 真实成交, 观察偏离回测 (memory 45164388: 偏离>30%拒绝), 再执行 `--advance` 推进 stage_2。

**命令**: `python launch_shadow_account.py --status` (查状态) / `--advance` (推进, 需人工确认)。

## 4. 上线 checklist (实盘前)

- [ ] 影子账户积累真实成交 (DTE-1 fills) ≥ 1-2 周, 偏离回测 ≤ 30%
- [ ] G1 QMT 接线完成 (xtquant 装 + 账号 + dry_run 影子期验证)
- [ ] UE-1 实盘门控 + DTE-6 auto-confirm 护栏激活验证
- [ ] 告警通道 (utils.notify) 在生产环境可送达
- [ ] 灰度 stage_2(50%) 运行 ≥ 1 周, 无 fail-fast
- [ ] 灰度 stage_3(100%) 运行 ≥ 2 周, 偏离回测 ≤ 30%
- [ ] TRADING_ENV=production 双签完成

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [影子观察期真实性核查 (2026-08-24)](shadow-realness-audit-20260824.md) (相似度 36%)
- [DTE-1 建仓接入 FillsStore 事实源 (2026-08-24)](dte1-build-fills-store-20260824.md) (相似度 26%)
- [P3.0 影子账户闭环门禁 (2026-08-26)](p3-0-gate.md) (相似度 24%)
- [实盘准入框架：12-31 上实盘的 7 项硬性门槛与 6 个决策门](live-trading-admission-criteria-20260811.md) (相似度 18%)
- [风控 fail-close 完整化 (2026-08-24)](risk-failclose-complete-20260824.md) (相似度 14%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
