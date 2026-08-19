# 后续计划：14 天观察期监控与 Stage 2 推进

## 摘要

T5.7（实盘券商直连）和 T5.8（MLops 流水线）的代码、单元测试、Shadow 配置和功能验证（10/10 通过）已全部完成。EOD 自动化闭环已建立（`QuantEOD_1530` 任务每日 15:30 触发）。当前两个观察期均为 0/14 天，需要等待 14 天实盘数据积累后评估 Stage 2 准入条件。

本计划覆盖：观察期监控、Fail-Fast 触发响应、Stage 2 准入评估、关键风险点防范。

## 当前状态分析

### 已完成
- ✅ T5.7 broker_adapters + broker_failover 实现（单测 98/98 PASS，覆盖率 ≥ 79%）
- ✅ T5.8 mlops_pipeline 全套模块（单测 134/134 PASS，覆盖率 ≥ 70%）
- ✅ P3 功能验证 10/10 通过（`reports/shadow_p3/functional_verify.json`）
- ✅ EOD 任务注册（`QuantEOD_1530`，周一至五 15:30 自动触发）
- ✅ daily_return 闭环验证（0.016482 非零值，幂等性正确）
- ✅ 6 个关键 Bug 修复（v8.6.11：路径/兜底/幂等性/nav累积/wmic/jsonl）

### 进行中
- 🔄 T2.4 观察期：0/14 天（预计 2026-08-09 完成）
- 🔄 T5.7/T5.8 P3 观察期：0/14 天（预计 2026-08-10 完成）

### 阻塞条件
- HC-4：观察期 < 14 天 → Stage 2 BLOCKED（`observation_in_progress`）

## 提议变更

### 阶段 1：观察期每日监控（2026-07-28 ~ 2026-08-10）

**目标**：确保每日 EOD 自动产生非零 daily_return，观察期数据正确累积。

**监控内容**：
1. **EOD 任务执行状态**
   - 文件：`C:\QuantSys\logs\workflow_eod_latest.log`
   - 检查项：Last Result = 0，日志包含 "Phase 10 完成" + "jsonl 追加/更新成功"
   - 频率：每日 16:00 检查（EOD 15:30 触发后 30 分钟）

2. **daily_returns.jsonl 数据累积**
   - 文件：`e:\各种PY程序\28-终极量化交易系统8.4\reports\shadow\daily_returns.jsonl`
   - 检查项：每日新增 1 条记录，source = "v9_phase10_real_backtest"
   - 异常处理：若连续 2 天无新增，手动运行 `python scripts/shadow_admission_launcher.py daily`

3. **shadow_state.json 状态健康**
   - 文件：`e:\各种PY程序\28-终极量化交易系统8.4\output\shadow_account\shadow_state.json`
   - 检查项：status = "RUNNING"，daily_nav 数量递增，current_nav > 0
   - 异常处理：若 status = "TERMINATED"，检查 fail_fast_log 并触发应急流程

4. **DSR 报告生成**
   - 文件：`e:\各种PY程序\28-终极量化交易系统8.4\reports\shadow\{date}_dsr.json`
   - 检查项：is_real_data = true，days_tracked 递增
   - 异常处理：若 is_real_data = false，检查 daily_returns.jsonl 是否为空

### 阶段 2：Fail-Fast 触发响应（如触发）

**触发条件**：
- 单日回撤 > 3%
- 3 日累计回撤 > 5%

**响应流程**：
1. 立即停止 V9 生产策略下单
2. 检查 `output/shadow_account/shadow_state.json` 的 `fail_fast_log`
3. 分析根因（市场异常 / 策略失效 / 数据错误）
4. 修复后重新初始化影子账户：`python launch_shadow_account.py`
5. 重新启动 14 天观察期（从 0 天开始）

**硬约束**（来自 project_memory）：
- 单日回撤 > 3% 或 3 日累计回撤 > 5% → 立即终止
- 观察期不可逆终止，不可推进 Stage 2

### 阶段 3：Stage 2 准入评估（2026-08-10 ~ 2026-08-12）

**T2.4 准入条件**（6 项，全部满足才可推进）：
1. 观察期 ≥ 14 天
2. fail_fast_triggered == false
3. DSR ≥ 5
4. 年化收益 ≥ 15%
5. 最大回撤 ≤ 10%
6. Sharpe CV < 1.0

**T5.7/T5.8 P3 准入条件**（11 项 promoters，全部 True 才可推进）：
- observation_days >= 14
- fail_fast_triggered == false
- broker_order_accuracy >= 0.99
- broker_failover_success_rate >= 0.95
- broker_audit_completeness == 1.0
- broker_risk_control_effectiveness >= 0.98
- mlops_register_success_rate == 1.0
- mlops_split_reproducibility == 1.0
- mlops_drift_alert_accuracy >= 0.90
- mlops_retrain_trigger_accuracy >= 0.95
- mlops_orchestration_resilience == 1.0

**评估流程**：
1. 运行 `python scripts/shadow_admission_launcher.py evaluate`（T2.4）
2. 运行 `python scripts/shadow_admission_launcher.py evaluate --config shadow_p3_admission`（T5.7/T5.8）
3. 检查返回的 `can_promote` 字段
4. 若 `can_promote = true`，推进 Stage 2：
   - T2.4：启用 LLMRouter/DecisionTheoriesFusion/MultiFactorSignal
   - T5.7/T5.8：启用 `USE_LIVE_BROKER_ADAPTERS` / `USE_MLOPS_PIPELINE` Feature Flag
5. 若 `can_promote = false`，分析 `blockers` 字段，决定是否延长观察期或修复问题

### 阶段 4：关键风险点防范

**风险 1：daily_return 为 0**
- 原因：trade_plan 文件缺失或 target_weights 为空
- 防范：EOD 任务前确保 07:00 V9 workflow 生成 trade_plan
- 监控：每日检查 daily_returns.jsonl 的 daily_return 字段非零

**风险 2：EOD 任务失败**
- 原因：SYSTEM 用户权限、Python 路径、junction 断开
- 防范：每日 16:00 检查任务状态和日志
- 应急：手动运行 `C:\QuantSys\run_workflow_task.bat eod`

**风险 3：Fail-Fast 误触发**
- 原因：市场异常波动或数据错误
- 防范：每日检查 shadow_state.json 的 status 字段
- 应急：参考阶段 2 响应流程

**风险 4：DSR 样本不足**
- 原因：DSR 最小样本要求 20 天，但观察期仅 14 天
- 防范：观察期 14 天后若 DSR 仍为 `insufficient_samples`，延长观察期至 20 天
- 备选：使用 `simulated_returns` 配置补充测试数据（仅用于功能验证，不可推进 Stage 2）

## 假设与决策

### 假设
1. V9 生产策略每个交易日产生有效 trade_plan（07:00 workflow 任务正常）
2. EOD 任务（15:30）每日自动触发并成功执行
3. 市场无极端异常（无单日回撤 > 3% 的 Fail-Fast 触发）
4. 数据源（ifind/akshare）每日可获取收盘价

### 决策
1. **不主动干预观察期**：让 EOD 任务自动累积数据，仅监控不修改
2. **不启用 Feature Flag**：T5.7/T5.8 的 5 个 Feature Flag 保持默认 False，Stage 2 推进后才启用
3. **不延长观察期**：除非 DSR 样本不足或 Fail-Fast 触发，否则严格 14 天
4. **不修改 V9 基线**：观察期内不调整 V9 策略参数，保持基线稳定

## 验证步骤

### 每日验证（16:00）
```powershell
# 1. 检查 EOD 任务状态
schtasks /query /tn "QuantEOD_1530" /v /fo LIST | findstr "Last Result"
# 期望: Last Result = 0

# 2. 检查 daily_returns.jsonl 新增
Get-Content "e:\各种PY程序\28-终极量化交易系统8.4\reports\shadow\daily_returns.jsonl" | Measure-Object -Line
# 期望: 行数 = 观察期天数

# 3. 检查 shadow_state 状态
Get-Content "e:\各种PY程序\28-终极量化交易系统8.4\output\shadow_account\shadow_state.json" | ConvertFrom-Json | Select-Object status, current_nav
# 期望: status = RUNNING, current_nav > 0

# 4. 检查 DSR 报告生成
Get-ChildItem "e:\各种PY程序\28-终极量化交易系统8.4\reports\shadow\*_dsr.json" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
# 期望: 当日日期的 DSR 报告存在
```

### 阶段性验证（每 7 天）
```powershell
# 检查观察期进度
python "e:\各种PY程序\28-终极量化交易系统8.4\scripts\shadow_admission_launcher.py" status
# 期望: observation_days 递增, fail_fast_triggered = false
```

### Stage 2 评估（2026-08-10）
```powershell
# T2.4 评估
python "e:\各种PY程序\28-终极量化交易系统8.4\scripts\shadow_admission_launcher.py" evaluate

# T5.7/T5.8 P3 评估
python "e:\各种PY程序\28-终极量化交易系统8.4\scripts\shadow_admission_launcher.py" evaluate --config shadow_p3_admission
# 期望: can_promote = true (或明确的 blockers 列表)
```

## 关键时间节点

| 日期 | 事件 | 动作 |
|------|------|------|
| 2026-07-28 | 观察期第 1 天 | 首个完整交易日 EOD 自动运行 |
| 2026-08-03 | 观察期第 7 天 | 阶段性验证，检查 7 天数据累积 |
| 2026-08-09 | T2.4 观察期第 14 天 | T2.4 准入评估 |
| 2026-08-10 | T5.7/T5.8 观察期第 14 天 | P3 准入评估 |
| 2026-08-12 | Stage 2 推进（如通过） | 启用 Feature Flag，推进灰度阶段 |

## 应急联系点

- **EOD 任务失败**：手动运行 `C:\QuantSys\run_workflow_task.bat eod`
- **Fail-Fast 触发**：立即停止 V9 策略，分析 `fail_fast_log`
- **数据缺失**：检查 `daily_returns.jsonl`，手动运行 `shadow_admission_launcher.py daily`
- **观察期重置**：`python launch_shadow_account.py`（重新初始化影子账户）
