---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-08-03
updated: 2026-08-04
contains: shadow-data-quality-loop, drift-detection, psi-small-sample, observation-period, watchdog, idempotent-alert, data-quality-classification, double-gate, backtest-backfill-pollution, 14-day-threshold, two-layer-threshold, gate-a-vs-gate-b-semantics, three-step-commands, gate-a-gate-b-lag, drift-response-pipeline-survey, natural-trigger-decision
related:
  - cairn/self-evolution-framework.md
  - cairn/data-pipeline.md
  - cairn/risk-architecture.md
  - cairn/LOG.md
---

# Shadow 数据质量闭环设计

> 记录 Shadow 账户观察期数据从"污染态"到"可信态"的完整闭环：数据清洗 → 漂移告警集成 → 达标看门狗。核心解决两个问题：① 回测回填数据污染导致漂移告警失真；② 小样本 PSI 不可靠导致 critical 级误判。对应 LOG 2026-08-03「Shadow 数据质量闭环落地」条目。

## 一、背景与动机

### 1.1 问题起源：双重数据失真

Shadow 账户 `reports/shadow/daily_returns.jsonl` 在 2026-08-02 触发了一次 `severity=critical, PSI=4.37` 的漂移告警。排查发现告警基于**污染数据**，存在两层失真：

**第一层：回测回填污染**
- 6 条记录中仅 1 条真实市场数据（16.7%），其余 5 条为回测回填/修复值
- 3 条零收益是假的（占位符），2 条收益方向/幅度严重失真（回测虚高 5 倍 / 虚低 12 倍）
- 基线 `[0.0055, 0.0055, 0.0055]`（重复值）vs 当前 `[0.0, 0.0, -0.0213]` → PSI=4.37 是虚假漂移

**第二层：小样本 PSI 误判**
- 用清洗后的 6 条真实数据重算，PSI=8.48（比污染数据还高）
- 原因：6 天样本切分为 3+3，基线/当前各仅 3 个采样，PSI 在小样本下统计意义有限
- critical 级告警是统计噪音，非真实分布漂移

### 1.2 设计目标

| 目标 | 解决方案 |
|---|---|
| 剔除回测回填污染 | 数据清洗脚本标记 4 类质量，漂移判定仅用 `real` 记录 |
| 拦截小样本 PSI 误判 | 14 天双重门槛看门狗，未达标不触发漂移判定 |
| 告警可溯源 | 告警条目嵌入 `data_quality` 计数 + `cleaned_file_hash` |
| 幂等不堆积 | 同日重复运行替换当日告警（对比旧机制始终追加） |
| 断档可感知 | 连续 ≥2 交易日无新数据则告警 |

## 二、闭环架构

### 2.1 三脚本协同

```
┌─────────────────────────────────────────────────────────────────┐
│  ① clean_shadow_returns.py  (数据清洗)                           │
│  输入: daily_returns.jsonl (原始, 可能含污染)                     │
│  输出: daily_returns_cleaned.jsonl  (带 quality 标签)            │
│        cleaning_report.md           (人类可读报告)                │
│  动作: 标记 real/backtest/fixed/missing + 检测缺失交易日           │
└──────────────────────────┬──────────────────────────────────────┘
                           │ (清洗后数据)
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  ② integrate_cleaned_to_drift.py  (漂移告警集成)                  │
│  输入: daily_returns_cleaned.jsonl                               │
│  输出: drift_alerts.jsonl          (漂移告警, 幂等替换)            │
│        observation_progress.json   (观察期进度刷新)               │
│        integration_log.jsonl       (集成审计日志)                 │
│  动作: 过滤 real → 切分基线/当前 → compute_prediction_drift       │
└──────────────────────────┬──────────────────────────────────────┘
                           │ (被看门狗条件触发)
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  ③ observation_watchdog.py  (达标看门狗)                          │
│  输入: observation_progress.json + daily_returns_cleaned.jsonl   │
│  输出: observation_watchdog.jsonl  (监控日志)                     │
│        (达标时) → 调用 ② 的 run_integration()                    │
│  动作: 双重门槛判定 → 达标触发 / 未达标跳过 → 断档检测             │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 数据流关键约束

1. **单向流动**：原始文件 → 清洗文件 → 告警/进度，绝不反向写入原始文件
2. **质量标签透传**：`quality` 字段从清洗脚本产出后，一路透传到告警条目的 `data_quality` 字段
3. **文件溯源**：告警条目内嵌 `cleaned_file_hash`（SHA256 前 16 位），可追溯告警基于哪个版本的清洗文件

## 三、核心设计决策

### 3.1 双重门槛（GATE-A + GATE-B）

看门狗在触发漂移判定前执行**双重门槛**检查，任一不满足则跳过：

| 门槛 | 条件 | 防范的失效模式 |
|---|---|---|
| GATE-A | `days_completed >= 14` | 日历时间不足（观察期未满 14 天，PSI 统计失真） |
| GATE-B | `real_records >= 14` | 真实数据不足（天数达标但数据断档/质量差，如回测回填污染或 v84_PostMarket 故障） |

**为什么需要双门槛？** GATE-A 的 `days_completed` 来自 `observation_tracker.py` 的 `min(n_shadow_days, trading_days_elapsed)`，其中 `n_shadow_days` 是 `daily_returns.jsonl` 的去重日期数（**含所有质量类型，不限于 real**）。这意味着：

- 若写入的是 backtest/fixed 数据（非 real），GATE-A 仍可能通过 → 由 GATE-B 拦截
- 若数据断档（无写入），`n_shadow_days` 不增长，GATE-A 也会失败

GATE-B 直接数清洗文件里的 `real` 记录数，是更严格的真实数据量检查。双门槛组合确保"既有足够日历时间，又有足够真实数据"。

### 3.2 幂等告警机制

**旧机制**（`daily_evolution_check.py`）：每次运行都追加一行到 `drift_alerts.jsonl`，同日多次运行会堆积重复告警。

**新机制**（`integrate_cleaned_to_drift.py`）：写入前先扫描文件，用 `timestamp` 的日期部分（YYYY-MM-DD）匹配当日已有告警，替换而非追加。

```python
# 幂等写入逻辑（伪代码）
today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
existing_lines = []
replaced_count = 0
for line in read(drift_alerts_file):
    rec = json.loads(line)
    if str(rec.get("timestamp", ""))[:10] == today_str:
        replaced_count += 1  # 跳过旧告警
        continue
    existing_lines.append(line)
write(drift_alerts_file, existing_lines + [new_alert])
```

**设计权衡**：选择"同日替换"而非"同日跳过"，因为重跑时可能用了更新的清洗数据（如当日补录了断档），需要让新告警覆盖旧告警。

### 3.3 断档检测

看门狗每日运行时检查"最新数据日期到今天之间的交易日数"：

- `stalled_trading_days = 0`：数据正常
- `stalled_trading_days < 2`：可接受（可能是当日盘后任务尚未运行）
- `stalled_trading_days >= 2`：**断档告警**，提示检查 `v84_PostMarket` 是否正常运行

**节假日处理**：`HOLIDAYS_2026` 集合排除法定假日，避免节假日被误判为断档。已知限制：节假日列表需人工维护，未覆盖的假日会低估断档天数。

### 3.4 数据质量分类标准

清洗脚本按优先级 `real > fixed > backtest > unknown` 分类：

| 标签 | 判定条件 | 含义 | 漂移判定是否使用 |
|---|---|---|---|
| `real` | `source` 含 `real_market` 或 `w13a` | 真实市场馈送 | ✅ 使用 |
| `fixed` | `source` 含 `fixed` | 人工/脚本修复值 | ❌ 剔除 |
| `backtest` | `source` 含 `backtest` | 回测回填 | ❌ 剔除 |
| `missing` | 检测出的缺失交易日 | 断档占位 | ❌ 剔除 |
| `unknown` | 以上都不匹配 | 需人工检查 | ❌ 剔除 |

**额外标记**（`quality_flags`）：
- `zero_return`：`daily_return == 0.0`（可能占位）
- `fixed_value`：带 `updated_at` 字段（非原始写入，经过修正）
- `gap_detected`：缺失日期占位记录

## 四、统计学原理：小样本 PSI 不可靠

### 4.1 PSI 计算原理

PSI（Population Stability Index）衡量两个分布的差异：

```
PSI = Σ (actual_i% - expected_i%) × ln(actual_i% / expected_i%)
```

工业级阈值：
- PSI < 0.1：无显著变化
- 0.1 ≤ PSI < 0.25：需观察
- 0.25 ≤ PSI < 0.5：需告警
- PSI ≥ 0.5：需回滚

### 4.2 小样本下的失真

PSI 依赖分桶（binning）计算概率分布。当样本量过小时：

1. **分桶不稳定**：6 个样本分 10 桶，多数桶为 0 或 1，`ln(actual/expected)` 在边界值处剧烈波动
2. **单点影响过大**：一个异常值就能让 PSI 从 0.5 跳到 5+
3. **零桶问题**：空桶需平滑处理（加 0.001），平滑策略对结果影响极大

**实测对比**（本项目 6 天数据）：
- 污染数据（3+3 样本）：PSI = 4.37 → critical
- 清洗后真实数据（3+3 样本）：PSI = 8.48 → critical（更高，因为真实数据方差更大）
- 两者都是统计噪音，非真实漂移

### 4.3 门槛设计：两层防护

本闭环采用**两层门槛**防护，从低到高逐级拦截：

| 层级 | 门槛 | 位置 | 作用 |
|---|---|---|---|
| 集成器层 | `MIN_REAL_SAMPLES_FOR_DRIFT = 5` | `integrate_cleaned_to_drift.py` | 最低可运行门槛，<5 条真实数据时返回 skipped 状态 |
| 看门狗层 | `DEFAULT_REQUIRED_DAYS = 14` | `observation_watchdog.py` | 统计可信门槛，<14 天不触发漂移判定 |

**两层的关系**：

- **正常流程**：看门狗（14 天）先检查，通过才调用集成器，集成器的 5 天门槛不会触发
- **强制触发**（`--force-trigger`）：跳过看门狗 14 天门槛，直接调用集成器，此时 5 天门槛作为兜底
- **极端情况**：即使强制触发，<5 条真实数据仍返回 skipped，不会产生无意义告警

**14 天门槛的依据**：

1. **观察期最小周期**（HC 硬约束）：`project_memory.md` 记录"Shadow 账户 Stage 1 需运行满 14 天最小周期"
2. **PSI 统计稳定性**：经验上 PSI 需要 ≥20 样本才稳定，但本项目基线/当前各占 60%/40%，14 天可切分为 8+6，勉强达到 PSI 可信下限

**注意**：14 天是"最低门槛"而非"充分门槛"。即使达标，PSI 仍需结合 KS 统计量、均值差异、业务上下文综合判断，不能单看 PSI 阈值。

## 五、定时任务集成

### 5.1 时序设计

```
15:30  v84_PostMarket          (EOD 工作流, 写入 daily_returns.jsonl)
16:00  v84_DailyPnlReport      (PnL 报告)
16:04  observation_watchdog.py  ← 看门狗, 达标则触发漂移判定
16:05  v84_EvolutionEval        (读漂移告警做决策)
16:30  v84_UniverseScan         (标的池扫描)
```

**为什么放在 16:04？**
- 晚于 `v84_PostMarket`（15:30）：确保当日 Shadow 数据已写入
- 晚于 `v84_DailyPnlReport`（16:00）：PnL 报告优先级更高，不应被看门狗阻塞
- 早于 `v84_EvolutionEval`（16:05）：EvolutionEval 依赖漂移告警做决策，看门狗必须先产出告警

### 5.2 与 v84_EvolutionEval 的协作

`run_evolution_eval.py` 的 `ensure_today_drift_integration()` 已有兜底机制：若当日漂移检测未执行，会调用 `drift_shadow_integrator.py` 兜底重试。看门狗与之互补：

| 场景 | 看门狗行为 | EvolutionEval 兜底行为 |
|---|---|---|
| 观察期未达标（< 14 天） | 跳过漂移判定，写监控日志 | 不触发（数据不足） |
| 观察期达标，PostMarket 正常 | 触发漂移判定，写告警 | 读到告警，跳过兜底 |
| 观察期达标，PostMarket 故障 | 断档告警，不触发判定 | 兜底重试 drift_shadow_integrator |

## 六、已知限制与演进方向

### 6.1 已知限制

1. **节假日列表人工维护**：`HOLIDAYS_2026` 需每年更新，未覆盖的假日会被误判为断档
2. **14 天门槛是最低标准**：PSI 在 14 天下仍不够稳定，达标的首次告警仍需人工复核
3. **无自动重训练联动**：当前看门狗仅触发漂移判定，不触发重训练（HC-1 约束：不切 Feature Flag）。**2026-08-04 调研结论**：漂移响应链路代码已完整 — `auto_retrain_scheduler.py`（DRIFT_DETECTED 触发 + V9 训练 + 注册）+ `mlops_pipeline.py`（Facade 整合）+ `ab_testing.py`（promote_challenger）+ Phase 3 测试（mock 验证全链路）。缺口：从未用真实数据端到端验证（Phase 3 测试 DriftMonitor/AutoRetrainScheduler 为 mock）。**决策**：等 08-14 自然触发漂移判定后再做真实链路验证，不提前模拟注入 — 尊重观察期设计，拿真实 PSI/KS。Wave 2 B3（08-26→29）才启用 `USE_AUTO_RETRAIN`
4. **单机文件存储**：`drift_alerts.jsonl` 是本地文件，无分布式锁，多实例并发写可能冲突（当前单机部署，非问题）

### 6.2 演进方向

| 方向 | 触发条件 | 实施方案 |
|---|---|---|
| 动态门槛 | 观察期积累 ≥30 天 | 用滚动 PSI 置信区间替代固定 14 天门槛 |
| 自动重训练 | 漂移持续 ≥3 天且 severity≥high | 联动 `EvolutionOrchestrator`（需双签启用 Flag） |
| 多模型支持 | V9 之后的模型上线 | `model_name` 参数化，支持多模型并行漂移监控 |
| 特征级漂移 | 特征 panel 数据完整 | 从 `__prediction__` 漂移扩展到单特征漂移（PSI per feature） |

## 七、踩坑记录

### 7.1 contains: backtest-backfill-pollution

**现象**：2026-08-02 漂移告警 `PSI=4.37, severity=critical`，排查发现基线 `[0.0055, 0.0055, 0.0055]` 是 3 个重复值，来自回测回填数据。

**根因**：`daily_returns.jsonl` 混入了回测回填数据（source 含 `backtest`/`fixed`），漂移判定未区分数据质量，把回测值当真实值计算 PSI。

**修复**：清洗脚本标记 4 类质量，漂移判定仅用 `real` 记录。详见 LOG 2026-08-03「Shadow 历史5天真实数据回填」条目。

**教训**：**回测回填数据不能替代真实市场数据**。3 天零收益全部是假的，2 天收益方向/幅度严重失真（虚高 5 倍 / 虚低 12 倍）。观察期必须用真实数据才能做有意义的决策。

### 7.2 contains: psi-small-sample

**现象**：用清洗后的 6 条真实数据重算 PSI，结果 8.48（比污染数据的 4.37 还高），severity 仍是 critical。

**根因**：6 天样本切分为 3+3，PSI 分桶在小样本下不稳定，单点影响过大，统计噪音被放大为 critical 告警。

**修复**：引入 14 天双重门槛看门狗，未达标不触发漂移判定。详见 LOG 2026-08-03「Shadow 数据质量闭环落地」条目。

**教训**：**PSI 在小样本下不可靠**。工业级 PSI 阈值（0.1/0.25/0.5）假设样本量 ≥20，本项目 14 天门槛是观察期硬约束与 PSI 统计稳定性的折中。任何基于 < 14 天样本的 PSI 告警都应视为统计噪音，需人工复核。

### 7.3 contains: idempotent-alert

**现象**：旧机制 `daily_evolution_check.py` 每次运行都追加告警，同日多次运行导致 `drift_alerts.jsonl` 堆积重复告警，干扰下游决策。

**修复**：新机制 `integrate_cleaned_to_drift.py` 写入前扫描当日已有告警，替换而非追加。选择"替换"而非"跳过"，因为重跑时可能用了更新的清洗数据。

**教训**：**告警写入必须幂等**。下游消费者（EvolutionOrchestrator）假设每条告警是独立事件，重复告警会导致重复决策。幂等设计应基于业务时间（日期）而非物理时间（时间戳）。

### 7.4 contains: gate-a-gate-b-lag

**现象**：2026-08-04 盘后三步命令执行后，看门狗报告 GATE-A 显示 6 天但 GATE-B 显示 7 条，两者不一致。

**根因**：GATE-A 读取 `observation_progress.json` 的 `days_completed`（由 `observation_tracker.generate_snapshot()` 更新，定时任务刷新），GATE-B 直接统计 `daily_returns_cleaned.jsonl` 的 real 记录数。三步命令流程为 `backfill_shadow_history.py` 写入原始文件 → `clean_shadow_returns.py` 刷新清洗文件 → `observation_watchdog.py` 读取进度文件，但进度文件的 `days_completed` 尚未被 `observation_tracker` 刷新（定时任务未到或未运行），导致 GATE-A 滞后于 GATE-B。

**影响**：不影响核心逻辑 — 两层门槛都 FAIL 时正确拦截，都 PASS 时正确触发。仅在天数临界（如恰好 14 天）时可能出现 GATE-A 滞后导致延迟一天触发，可接受。

**应对**：无需修复。`days_completed` 会在下次 `observation_tracker` 运行时刷新。若需立即同步，可手动运行 `observation_tracker.py` 刷新进度文件。

### 7.5 contains: three-step-commands, natural-trigger-decision

**实战验证**（2026-08-04）：三步命令盘后执行首次端到端验证通过。

| 步骤 | 脚本 | 结果 |
|---|---|---|
| ① 回填 | `backfill_shadow_history.py --start-date <当日> --end-date <当日>` | 26 标的 100% 覆盖，日收益 -0.0603%，写入 `daily_returns.jsonl` |
| ② 清洗 | `clean_shadow_returns.py` | 7 条记录全部 quality=real，写入 `daily_returns_cleaned.jsonl` |
| ③ 看门狗 | `observation_watchdog.py` | GATE-A 6/14 FAIL + GATE-B 7/14 FAIL，正确拦截，断档恢复 0 天 |

**经验**：① 盘后 15:00 收盘后执行，数据源（TDX/AKShare）才能拿到收盘价；② 三步命令必须顺序执行（回填→清洗→看门狗），不可并行；③ `--dry-run` 可预检数据源是否有今日数据，避免空跑。

**决策记录**：漂移响应链路调研确认代码已完整但 Phase 3 测试用 mock，决定等 08-14 自然触发而非提前模拟注入 — 尊重观察期设计，拿真实 PSI/KS 而非构造数据。

## 八、关联文档

- **LOG 指针**: `cairn/LOG.md` 2026-08-03「Shadow 数据质量闭环落地」条目
- **脚本**: `scripts/clean_shadow_returns.py` / `scripts/integrate_cleaned_to_drift.py` / `scripts/observation_watchdog.py`
- **上游**: `utils/alpha/drift_monitor.py`（`compute_prediction_drift`）/ `scripts/observation_tracker.py`（`generate_snapshot`）
- **关联专题**: `cairn/self-evolution-framework.md`（观察期阶段定义）/ `cairn/data-pipeline.md`（数据源降级链）
- **硬约束**: `project_memory.md`「Shadow 账户 Stage 1 需运行满 14 天最小周期」

## 九、修订记录

- **2026-08-03 修订 1**：基于代码审查修正 GATE-A/GATE-B 语义描述（GATE-A 防范日历时间不足，GATE-B 防范真实数据不足，原文误将 GATE-A 描述为"小样本 PSI 统计失真"）；补充 §4.3 两层门槛说明（集成器层 `MIN_REAL_SAMPLES_FOR_DRIFT=5` + 看门狗层 `DEFAULT_REQUIRED_DAYS=14`）。参见 LOG 2026-08-03「Shadow 数据质量闭环落地」条目。
- **2026-08-04 修订 2**：补充实战验证记录 — 三步命令盘后执行通过（backfill + clean + watchdog，6→7/14 天，日收益 -0.0603%）；记录 GATE-A/GATE-B 刷新不一致现象（§7.4 踩坑 — progress.json 的 days_completed 滞后于 cleaned.jsonl 的 real 记录数）；补充漂移响应链路调研结论（§6.1 已知限制第 3 项 — 代码已完整但 Phase 3 测试用 mock，决定等 08-14 自然触发）。参见 LOG 2026-08-04「观察期数据收集 Day 7 + 漂移响应链路调研」条目。

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [自我进化框架](self-evolution-framework.md) (相似度 12%)
- [EOD 计划任务静默失败 + 观察期样本补录（2026-08-19）](eod-scheduled-task-fix-20260819.md) (相似度 12%)
- [观察期配置脱节修复 — 2026-08-09](observation-period-config-drift-20260809.md) (相似度 11%)
- [自我进化迭代再平衡闭环（Evolution-Rebalance Loop）](evolution-rebalance-loop.md) (相似度 8%)
- [经验上下文层（ECL, Experience Context Layer）设计方案](experience-context-layer.md) (相似度 7%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
