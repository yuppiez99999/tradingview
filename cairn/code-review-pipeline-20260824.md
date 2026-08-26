# 代码审查明细 — 主链路 institutional_pipeline_runner (2026-08-24)

> 审查对象: `institutional_pipeline_runner.py` (1730 行) + `utils/pipeline_data_mixin.py`
> 审查批次: 全量量化逐模块审查计划 (plan: quant-system-module-audit-20260824) 第 6/10 模块
> 审查工具: code-explorer subagent 追跨文件调用链 + 五轴/DoD 复核
> 重点: EOD 数据断链/报告落盘/alpha_signals 产出/执行闭环

## 0. 门禁基线

| 门禁 | 结果 |
|---|---|
| ruff 静态扫描 | ✅ All checks passed |
| EOD 阶段 | engineering_debt_gate [BLOCK] D11 PhaseB shadow (计划内) |

## 1. 缺陷清单与修复状态

| ID | 严重度 | 文件 | 缺陷 | 状态 |
|---|---|---|---|---|
| PI-2 | HIGH | pipeline_data_mixin.py / institutional_pipeline_runner.py | `_real_snapshot` 数据不可用时返回 `_mock_snapshot`(price=10.0, source=mock)，`_step_data_gate` 未感知 mock 直接做数据门控 → 用假价判断可能放行虚假信号 | ✅ 已修复 |
| PI-1 | 架构确认 | institutional_pipeline_runner.py | `_step_execution_routing`(L1584) 只生成 ExecutionPlan 进报告，无撮合/落盘。经核为**架构约定**(实盘下单由独立 daily_trade_executor/automated_execution_system 完成)，非断链 | ✅ 无缺陷(记录) |
| PI-3 | MEDIUM | pipeline_data_mixin.py | `_save_alpha_signals_report` 路径与 REPORT_DIR 不一致(QUANT_DATA_ROOT 迁移 D 盘时断链) | ⏸ 记录(P1) |
| PI-5 | MEDIUM | institutional_pipeline_runner.py | `_step_execution_routing` L1592 `market_state={"volatility":0.02}` 硬编码假波动率传执行路由 | ⏸ 记录(P1) |
| PI-4 | MEDIUM | institutional_pipeline_runner.py | L1144 `max(base_factor*vol*mom, floor)` 当乘法得 NaN 时 Python max 返回 NaN 传播 | ⏸ 记录(P1) |
| PI-6 | LOW | 多处 | `_load_returns_matrix` 除零无守卫、`_calc_momentum` 无 isfinite、硬编码板块映射 | ⏸ 记录(P2) |

**汇总**: HIGH×1, MEDIUM×3, LOW×1；已修复 1 项, 记录 4 项。

## 2. 修复详情

### PI-2 — 数据门控感知 mock 快照
`_step_data_gate` (institutional_pipeline_runner.py L446-461) 改造:
- 遍历 symbol 时检查 `snapshot.get("source")=="mock"` → 记录 `mock_used` + `logger.warning("[DATA_DEGRADED] ... 数据门控基于降级数据")`。
- 每个 gate 记录附 `data_degraded=True` 标记。
- 返回值新增 `mock_used` / `data_degraded` 字段, 供上游决策感知数据降级 (观测路径 fail-open 留日志, 不静默)。

## 3. 验证快照

| 验证 | 结果 |
|---|---|
| PI-2 mock 快照 (2 symbol) | data_degraded=True, mock_used=2, 每 gate 带标记 |
| PI-2 真实快照 | data_degraded=False, mock_used=0, 无标记 |
| 回归测试 | 2 passed (test_pipeline_audit_regression_20260824.py) |
| ruff_incremental_gate | ✅ 3 文件无新增违规 |

## 4. 审查结论 (code-explorer 深挖)

- **alpha_signals 产出链通** (runner 自身 `_step_signal_fusion` L668 调 `_save_alpha_signals_report`, 无 U9 断链问题; U9 旧断链已由 generate_daily_trade_plan 修复)。
- **执行闭环架构确认**: EOD 管道 `_step_execution_routing` 产执行计划给报告, 实盘下单由独立执行器完成 — 不是断链 (区别于 memory 20413815 期权对冲真断链)。
- **关键缺口**: 数据门控对 mock 假价不设防 (PI-2 已修复) + 执行路由硬编码假波动率 (PI-5 待办)。

## 5. 待办 (后续批次)

- PI-3: alpha_signals 路径统一到 REPORT_DIR。
- PI-5: 执行路由 volatility 从真实历史数据估算, 而非硬编码 0.02。
- PI-4: factor 乘法加 np.isfinite 守卫, 防 NaN 传播。
