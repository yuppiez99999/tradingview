# 模型漂移排查手册 (Model Drift Runbook)

> **GAP-6 交付物** | ECC mle-workflow MLE-10 修复
>
> 当 `SimModeDriftMonitor` 或 `DelayedLabelTracker` 触发告警时, 按本手册排查.

---

## 1. 触发条件

### 1.1 特征漂移 (Feature Drift)

| 指标 | 阈值 | 严重等级 | 触发动作 |
|------|------|----------|----------|
| KS 统计量 | KS < 0.1 | LOW | 仅记录, 无动作 |
| KS 统计量 | 0.1 ≤ KS < 0.2 | MEDIUM | 记录 + 观察 3 天 |
| KS 统计量 | 0.2 ≤ KS < 0.4 | HIGH | 记录 + 告警 owner |
| KS 统计量 | KS ≥ 0.4 | CRITICAL | 告警 owner + **触发回滚评估** |
| PSI | PSI < 0.1 | LOW | 仅记录 |
| PSI | 0.1 ≤ PSI < 0.25 | MEDIUM | 记录 + 观察 |
| PSI | 0.25 ≤ PSI < 0.5 | HIGH | 记录 + 告警 |
| PSI | PSI ≥ 0.5 | CRITICAL | 告警 + **触发回滚评估** |

**双指标取较严重者**: 同一特征同时算 KS 和 PSI, 取 severity 较高者作为最终等级.

### 1.2 预测漂移 (Prediction Drift)

模型输出分布 (predictions) 的漂移, 阈值与特征漂移一致.
预测漂移通常意味着模型整体失配, 严重程度天然偏高.

### 1.3 延迟标签指标 (Delayed Label Metrics)

| 指标 | 阈值 | 触发动作 |
|------|------|----------|
| IC | \|IC\| < 0.02 | 因子失效, 触发回滚评估 |
| IC_IR | \|IC_IR\| < 0.3 | 因子无效, 触发回滚评估 |
| IC_IR 符号反转 | 与训练时异号 | **立即回滚** (因子方向已反) |
| 观测率 | < 50% | 数据不足, 仅观察不告警 |

---

## 2. 排查步骤 (五步法)

### Step 1: 确认告警来源

```bash
# 查看当日漂移报告
type reports\drift\drift_report_2026-07-29.json

# 查看延迟标签追踪汇总
py -3.11 -c "from utils.alpha.delayed_label_tracker import DelayedLabelTracker; t = DelayedLabelTracker(); print(t.get_summary())"

# 查看延迟指标
py -3.11 -c "from utils.alpha.delayed_label_tracker import DelayedLabelTracker; t = DelayedLabelTracker(); m = t.compute_delayed_metrics(); print(m.to_dict())"
```

确认:
- 哪个特征/预测触发告警?
- 严重等级 (LOW / MEDIUM / HIGH / CRITICAL)?
- 哪个模型版本 (artifact_name)?

### Step 2: 检查数据源

特征漂移最常见的原因是**数据源异常**:

```bash
# 2.1 检查通达信/akshare 连接
py -3.11 -c "from utils.astock_realtime import get_realtime_quotes; print(get_realtime_quotes(['510300']))"

# 2.2 检查数据完整性 (null 比例突增?)
py -3.11 -c "import pandas as pd; df = pd.read_parquet('data/xxx.parquet'); print(df.isnull().mean())"

# 2.3 检查 V9 数据契约
py -3.11 -c "from utils.alpha.data_contract import V9_DEFAULT_CONTRACT; import pandas as pd; panel = pd.read_csv('research/outputs/factor_panel_latest.csv'); r = V9_DEFAULT_CONTRACT.validate(panel, mode='warn_only'); print(r.to_dict())"
```

常见数据源问题:
- 通达信断线 → OHLCV 缺失 → MOM/VOL 异常
- akshare 限流 → 数据不全 → 因子 null 比例突增
- 复权方式变更 → close 序列跳变 → 动量类因子漂移

### Step 3: 检查特征分布

```bash
# 对比基线 vs 当前分布
py -3.11 -c "
from utils.alpha.drift_monitor import compute_feature_drift
import pandas as pd
baseline = pd.read_csv('research/outputs/factor_panel_baseline.csv')['MOM_5D']
current = pd.read_csv('research/outputs/factor_panel_today.csv')['MOM_5D']
report = compute_feature_drift(baseline, current, feature_name='MOM_5D')
print(report.to_dict())
"
```

关键观察:
- `baseline_mean` vs `current_mean` 差距 → 整体偏移
- `drift_score` (KS) → 分布形状变化
- `psi` → 分箱比例变化

### Step 4: 检查 Regime 切换

市场 regime 切换会导致因子分布系统性变化:

```bash
# 查看当前 regime
py -3.11 -c "from utils.regime_conditioner import RegimeConditioner; rc = RegimeConditioner(); print(rc.get_current_regime())"
```

常见 regime 影响:
- 牛→熊切换 → 动量因子失效, 波动率因子上升
- 低波→高波切换 → 所有波动率类因子 PSI 飙升
- 行业轮动 → 行业因子 IC 反转

### Step 5: 决定是否触发回滚

**回滚决策矩阵**:

| 场景 | 严重等级 | 观测率 | 决策 |
|------|----------|--------|------|
| 单特征 LOW/MEDIUM | LOW/MEDIUM | 任意 | 继续 sim_mode, 观察 3 天 |
| 多特征 HIGH | HIGH | < 50% | 继续 sim_mode, 等更多标签 |
| 多特征 HIGH | HIGH | ≥ 50% | **触发回滚评估** |
| 任一 CRITICAL | CRITICAL | 任意 | **立即回滚** |
| IC_IR 符号反转 | 任意 | ≥ 50% | **立即回滚** |

---

## 3. 回滚流程

### 3.1 回滚到上一稳定版本

```bash
# 1. 查看历史 artifact (按时间倒序)
dir research\outputs\lgbm_factor_mining_v* /B /O-D

# 2. 找到上一稳定版本的 manifest
type research\outputs\lgbm_factor_mining_v{prev_sha}_d{prev_date}\manifest.json

# 3. 更新生产配置指向旧版本
# 修改 config/shadow_account_config.json 的 backtest_benchmark 字段

# 4. 验证回滚后 V9 基线
py -3.11 -m pytest tests/regression/test_v9_baseline.py -v

# 5. 重启 sim_mode (不接实盘)
py -3.11 15_每日工作流\run_daily_morning.py --phase all
```

### 3.2 回滚到 V9 基线 (兜底)

如果所有近期 artifact 都有问题, 回滚到 V9 基线:

```bash
# V9 基线参数 (硬约束, 不可改)
# annual_return = 19.62%
# sharpe = 1.315
# max_drawdown = 9.95%

# 1. 设置 Feature Flag 回退到静态预测
set USE_DYNAMIC_RETURN_PREDICTION=false

# 2. 重启工作流 (静态路径, _static_predict)
py -3.11 15_每日工作流\run_daily_morning.py --phase all

# 3. 跑双门禁校验
py -3.11 15_每日工作流\run_daily_eod_workflow.py
```

---

## 4. 告警通知流程

### 4.1 自动告警 (CRITICAL / HIGH)

`SimModeDriftMonitor.run_daily_check()` 触发 HIGH/CRITICAL 时:
1. 持久化到 `reports/drift/drift_report_{date}.json`
2. 日志 `logger.warning` 输出告警详情
3. 读取 `config/alert_owners.yaml` 获取 owner / slack_channel
4. (阶段 2 实施) 飞书 webhook 通知

### 4.2 手动告警 (LOW / MEDIUM)

LOW / MEDIUM 不自动告警, 需人工查看:
```bash
# 每日盘后查看漂移报告
type reports\drift\drift_report_2026-07-29.json | findstr "CRITICAL HIGH"
```

---

## 5. 预防措施

### 5.1 训练时

- ✅ 使用 GAP-7 `TrainingConfig` 记录 dataset_uri / code_sha / config_hash
- ✅ 使用 GAP-8 `V9_DEFAULT_CONTRACT` 校验训练数据
- ✅ manifest.json 落盘, 便于追溯

### 5.2 服务时

- ✅ sim_mode 下激活 `SimModeDriftMonitor` (GAP-6)
- ✅ 每日 `run_daily_check()` 批量检查所有特征
- ✅ 延迟标签追踪器 (`DelayedLabelTracker`) 追踪 IC 衰减

### 5.3 回滚演练

- 每月一次回滚演练 (在 sim_mode 环境)
- 验证回滚到 V9 基线的流程是否通畅
- 验证 manifest.json 可正确加载

---

## 6. 常见问题 (FAQ)

### Q1: KS 检验和 PSI 哪个更敏感?

- **KS 检验**: 对分布形状敏感 (中位数附近)
- **PSI**: 对分箱比例敏感 (尾部更敏感)
- 建议: 两个都看, 取较严重者 (本实现已采用)

### Q2: 延迟标签观测率为什么这么低?

延迟标签要等 `label_delay_days` (默认 5 天) 才能观测.
sim_mode 运行 1 天时, 观测率 = 0% 是正常的.
运行 14 天后, 观测率应达 9/14 ≈ 64%.

### Q3: IC_IR 符号反转怎么办?

IC_IR 符号反转意味着因子方向已反 (原来正 IC, 现在负 IC).
**立即回滚**, 然后参考 project_memory 的反向信号因子处理方法学:
```
IC_IR <= -0.3 时 is_inverted=True, effective_ic_ir=abs(ic_ir)
```
(VT_MICRO_VOL_SKEW 从 IC_IR=-0.42 反向为 +0.42, DSR 从 -6.87 反向为 +1.87)

### Q4: sim_mode 激活 drift_monitor 影响性能吗?

- `SimModeDriftMonitor.run_daily_check()` 每日只跑 1 次 (盘后)
- 单次约 1-2s (800 标的 × 50 因子, KS 检验 + PSI)
- 不影响交易路径 (异步执行)

### Q5: 如何手动触发漂移检查?

```bash
py -3.11 -c "
import pandas as pd
from utils.alpha.drift_monitor import SimModeDriftMonitor
baseline = pd.read_csv('research/outputs/factor_panel_baseline.csv')
current = pd.read_csv('research/outputs/factor_panel_today.csv')
monitor = SimModeDriftMonitor(
    model_name='v9_lgb',
    model_version='lgbm_factor_mining_vabc12345_d20260729',
    baseline_panel=baseline,
    sim_mode=True,
)
reports = monitor.run_daily_check(current)
for r in reports:
    if r.severity.value in ('high', 'critical'):
        print(r.to_dict())
print(monitor.get_summary())
"
```

---

## 7. 相关文档

- [ECC 阶段 1 执行计划](../../.trae/documents/ECC_Stage1_Code_Review_Refactor_Plan.md)
- [数据契约 (GAP-8)](../contracts/DATA_CONTRACT.md)
- [可复现性 (GAP-7)](../../research/lgbm_reproducibility.py)
- [V9 Iteration Compact](../iterations/V9_LGB_ITERATION_COMPACT.md)
- [双门禁机制](../../v8.3_institutional/gate_manager.py)

---

## 变更历史

| 日期 | 版本 | 变更 | 作者 |
|------|------|------|------|
| 2026-07-29 | 1.0 | 初始版本 (GAP-6 交付物) | ECC 阶段 1 |
