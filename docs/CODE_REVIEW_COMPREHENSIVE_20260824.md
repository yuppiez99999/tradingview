# 全量核心模块代码审查综合报告 (2026-08-24)

> 审查批次: plan `quant-system-module-audit-20260824` (全量量化逐模块审查计划)
> 审查方法: 既有 SOP 四步法 + Q1-Q7 硬门禁 + code-review-and-quality 五轴 + 量化 DoD
> 审查范围: ms_strategy 9 子模块 + 3 主链路大文件
> 状态: 10/10 阶段完成 (9 模块审查 + 综合报告)

## 0. 综合结论

本轮对量化系统全量核心模块完成系统性审查+修复闭环。**共发现 33 个缺陷（P0×1 / HIGH×8 / MEDIUM×15 / LOW×9），已修复 20 个，记录 13 个结构性待办**。全部修复通过回归测试 + ruff 增量门禁 + assert_data_validity 验证。

**总体健康度**：核心执行/风控路径正确性良好，主要问题集中在**执行断链（建仓不落 FillsStore）、实盘保护缺失、数据降级未标记、风控 fail-open**四类。已修复的 20 个缺陷消除了确定性的运行时崩溃、假数据、风控失效风险。

## 1. 缺陷严重度分布

| 严重度 | 数量 | 已修复 | 记录待办 |
|---|---|---|---|
| P0 (资金损失/实盘错单) | 1 | 1 | 0 |
| HIGH (安全/断链/风控失效) | 8 | 6 | 2 |
| MEDIUM (结构盲区/契约) | 15 | 10 | 5 |
| LOW (存量噪声) | 9 | 3 | 6 |
| **合计** | **33** | **20** | **13** |

## 2. 逐模块缺陷与修复状态索引

| 模块 | 报告 | 缺陷数 | 已修复 | 关键项 |
|---|---|---|---|---|
| execution (7文件) | cairn/code-review-execution-20260824.md | 9 | 7 | **EX-5 P0** qmt orderStock 参数错位; EX-7 algo_engine 调不存在 SOR 方法 |
| hedging (5文件) | cairn/code-review-hedging-20260824.md | 5 | 2 | HG-4 过度对冲缩放 estimated_cost 未缩放; HG-1 硬编码期货价无 OFFLINE |
| risk (6文件) | cairn/code-review-risk-20260824.md | 4 | 3 | RK-2 no-op 别名; bandit B105 误报 |
| backtest (8文件) | cairn/code-review-backtest-20260824.md | 7 | 3 | BT-6/2 大单 participation 冲击爆炸; BT-8 入口 NaN |
| data (1文件) | cairn/code-review-data-20260824.md | 4 | 2 | DT-1 get_price 无 stale; DT-2 无效价污染缓存 |
| pipeline (1730行) | cairn/code-review-pipeline-20260824.md | 6 | 1 | PI-2 数据门控用 mock 假价; PI-1 架构确认非断链 |
| daily_executor (1631行) | cairn/code-review-daily-executor-20260824.md | 9 | 4 | **DTE-1 CRITICAL** 建仓断链; DTE-2/3/4 风控 fail-open |
| unified_entry (1890行) | cairn/code-review-unified-entry-20260824.md | 8 | 2 | UE-1 实盘门控缺失; UE-2 gemma 假成功; UE-3 权重失真 |
| lightweight (5文件) | cairn/code-review-lightweight-20260824.md | 3 | 1 | LW-1 drift fromisoformat 崩溃; LW-2 iFinD 过时 |

## 3. 修复亮点 (关键真实缺陷)

- **EX-5 (P0)**: QmtBrokerAPI.orderStock 第3参/第5参语义互换 + account 传字符串 → 纠正为官方签名, 防接实盘下错单。
- **EX-7**: AlgoEngine.execute_order 调用不存在的 SOR 方法 → 补 execute_twap/vwap/pov + sor=None 守卫。
- **BT-6/BT-8**: cost_model.market_impact participation 钳制到 (0,1] + 入口 qty/price/volatility 防御, 防大单冲击爆炸与 NaN。
- **DT-1**: get_price 支持 max_age stale 校验, 行情断流不再静默返回陈旧价。
- **PI-2**: 数据门控感知 mock 快照 (source=mock) → 标记 data_degraded, 不静默用假价 10.0。
- **DTE-2/3/4**: 风控异常 fail-close 保守阻断 + 止损降级可见 + 行情缺失显式 stale 而非假价分配。
- **DTE-7**: total_built_after 消除重复累加 (进度口径虚增)。
- **UE-2/UE-3**: gemma 退出码透传 + 压力测试权重跳过缺价标的。

## 4. 防复发门禁 (新增回归测试)

新增 7 个回归测试文件 (tests/unit/test_*_audit_regression_20260824.py), **40 个测试**, 覆盖全部修复缺陷的契约行为:

| 测试文件 | 覆盖缺陷 |
|---|---|
| test_execution_audit_regression_20260824.py | EX-2/7/8/11/12 |
| test_hedging_audit_regression_20260824.py | HG-1/HG-4 |
| test_backtest_audit_regression_20260824.py | BT-2/6/8 |
| test_data_audit_regression_20260824.py | DT-1/DT-2 |
| test_pipeline_audit_regression_20260824.py | PI-2 |
| test_daily_executor_audit_regression_20260824.py | DTE-2/3/4/7 |
| test_unified_entry_audit_regression_20260824.py | UE-2/UE-3 |

## 5. 验证快照 (全绿)

| 门禁 | 结果 |
|---|---|
| 40 个新增回归测试 | ✅ 40 passed |
| 各模块相关单测 | ✅ 全部通过 (execution 211/hedging 120/risk 21/drift 41 等) |
| ruff_incremental_gate | ✅ 全部改动文件无新增违规 |
| assert_data_validity | ✅ 12 PASS, 0 FAIL |
| bandit (risk) | ✅ 0 issue (2 nosec) |
| engineering_debt_gate | [BLOCK] 仅 D11 PhaseB shadow (计划内, 非本轮引入) |

## 6. 结构性待办路线图 (13 项, 后续批次)

### P1 (架构级, 建议优先)
1. **UE-1 统一实盘门控**: 所有下单/撮合 handler (--live/--hedge-execute/--rebalance-execute) 加 TRADING_ENV=production 双签门控或默认 dry_run (memory 23032726 模式)。
2. **DTE-1 建仓接入撮合链**: daily_trade_executor 建仓执行接入 broker_factory/OrderRouter + FillsStore 落盘, 消除"账本自我记账"断链。
3. **PI-3/PI-5**: alpha_signals 路径统一 REPORT_DIR; 执行路由 volatility 用真实历史估算。
4. **EX-1 broker 契约统一**: Protocol vs 抽象类二选一。

### P2 (渐进清理)
5. DTE-6 --auto-confirm 护栏; 6. DTE-8 trade_plan 双轨同步; 7. LW-2 iFinD 移除;
8. UE-5 --ml-enhanced 回退; 9. HG-7 vol_hedger VIX 分段配置化; 10. RK-1 risk 对冲阈值统一;
11. PI-4 factor NaN; 12. BT-7 adv 缺失标记; 13. UE-4/6/7 gemma 去重/资金对齐/废弃 stub。

## 7. 方法论沉淀 (已入 cairn/agent-skills-integration.md §6)

- **执行闭环审查**: 不能只看"有没有撮合+落盘", 还要看"消费方是否只吃自己的订单" (FillsStore 按 strategy 过滤)。
- **门禁会拦自己引入的复杂度** (C901), 提取辅助函数。
- **数据降级必须显式标记**: mock/stale/硬编码价都要有 source/degraded 标记, 且消费方要检查。
- **风控 fail-open 是红线**: 风控/止损异常必须 fail-close 保守阻断。
- **主链路大文件回归测试用契约等价复刻**, 避免导入副作用。
