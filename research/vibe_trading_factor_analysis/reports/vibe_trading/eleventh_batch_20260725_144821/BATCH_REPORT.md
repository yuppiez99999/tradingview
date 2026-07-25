# 第十一批次流水线跑批报告 - eleventh_batch_20260725_144821

> P2.2 v4 最终改进验证（全部 winsorize 方案）
> 生成时间：2026-07-25 14:48:49

## 1. 批次基本信息

| 项目 | 值 |
|------|----|
| batch_id | `eleventh_batch_20260725_144821` |
| 数据标的数 | 105 |
| 候选因子总数 | 28 |
| n_trials | 105 |
| shadow risk_managed | True（P2.1c 默认） |
| 历史数据 schema_version | 2（含 revenue/yoy_pni 字段） |
| 改进版本 | v4（全部 winsorize + 净利润回退） |

## 2. 各 Gate 通过率

| Gate | 通过数 | 通过率 |
|------|--------|--------|
| G1 正交性 | 27 | 96.4% |
| G2 IC 稳定性 | 2 | 7.1% |
| G3 DSR | 1 | 3.6% |
| G4 经济逻辑 | 1 | 3.6% |
| Enhancement | 0 | 0.0% |
| Shadow (risk_managed) | 0 | 0.0% |
| Committee Approved | 0 | 0.0% |
| Deferred (fundamentals) | 0 | 0.0% |
| Rejected | 28 | 100.0% |
| Failed | 0 | 0.0% |

## 3. P2.2 v4 改进对比 - 4 个 QualityTrend 因子（v1 基线 → v4 最终方案）

| 因子 | 状态 | v1 max_corr | v1 IC_IR | v4 max_corr | v4 IC_IR | corr 变化 | IC_IR 变化 | G1 | G2 |
|------|------|------------|---------|------------|---------|----------|-----------|----|----|
| VT_QUALTREND_ROE_DELTA | rejected | 0.543 (MOM_252D) | +0.1364 | 0.408 (MOM_252D) | +0.1811 | -0.135 | +0.0447 | ✅ | ❌ |
| VT_QUALTREND_MARGIN_EXP | rejected | 0.279 (QUA_ROE) | +0.1270 | 0.342 (MOM_REVERSAL_5D) | +0.2742 | +0.063 | +0.1472 | ✅ | ❌ |
| VT_QUALTREND_DEBT_RED | rejected | 0.776 (QUA_DEBT_TO_EQUITY) | +0.0000 | 0.430 (SIZE_LOG_REV) | +0.0839 | -0.346 | +0.0839 | ✅ | ❌ |
| VT_QUALTREND_GROWTH_ACCEL | rejected | 0.292 (MOM_20D) | +0.2676 | 0.217 (QUA_ROE) | +0.1409 | -0.075 | -0.1267 | ✅ | ❌ |


### v4 改进设计（基于 v2/v3 失败教训的最终方案）

| 因子 | v1 公式 | v2 公式（废弃） | v3 公式（废弃） | v4 公式（最终） | 改进目标 |
|------|---------|---------|---------|---------|---------|
| VT_QUALTREND_ROE_DELTA | `roe[q] - roe[q-4]` | `rank(roe[q]) - rank(roe[q-4])` | `winsorize(roe[q]-roe[q-4])` | `winsorize(roe[q] - roe[q-4], p5/p95)` | 保留 IC_IR 强度，仅裁剪极端值 |
| VT_QUALTREND_MARGIN_EXP | `gm[q] - gm[q-4]` | `rank(gm[q]) - rank(gm[q-4])` | `winsorize(gm[q]-gm[q-4])` | `winsorize(gm[q] - gm[q-4], p5/p95)` | 保留 IC_IR 强度，仅裁剪极端值 |
| VT_QUALTREND_DEBT_RED | `-(d2e[q] - d2e[q-4])` | `current_ratio[q]-cr[q-4]` | `current_ratio[q]-cr[q-4]` | `current_ratio[q] - current_ratio[q-4]` | 保留 v2 改进（解共线有效） |
| VT_QUALTREND_GROWTH_ACCEL | 净利润 YoY 加速 | `rank(双信号)` | `winsorize(双信号)` | `winsorize(np YoY 加速, p5/p95)` | 回退 v1 + winsorize |

### 版本演进历史与教训

**v1（第十批次基线）**：
- ROE_DELTA IC_IR=+0.1364, MARGIN_EXP=+0.1270, DEBT_RED=+0.0000（共线）, GROWTH_ACCEL=+0.2676

**v2（rank 标准化方案，废弃）**：
- 全部因子 IC_IR 暴跌：ROE_DELTA→0.0240, MARGIN_EXP→0.0612, GROWTH_ACCEL→0.0224
- 失败根因：rank 标准化将连续值映射到 [0,1]，丢失 Pearson IC 强度信息
- 唯一成功：DEBT_RED current_ratio 改进（解共线 + IC_IR 0→0.0839）

**v3（双信号 + winsorize，部分回退）**：
- ROE_DELTA IC_IR 0.1364→0.1811 ✅（+33%，winsorize 有效）
- MARGIN_EXP IC_IR 0.1270→0.2742 ✅（+116%，接近 0.3 阈值！）
- GROWTH_ACCEL IC_IR 0.2676→0.0066 ❌（双信号本身不如净利润计算）
- 失败根因：yoy_pni 是百分比形式，QoQ 变化与原版净利润增长率变化含义不同；
          revenue 仅在 Q2/Q4 披露，Q1/Q3 数据稀疏

**v4（最终方案，全部 winsorize + 净利润回退）**：
- ROE_DELTA/MARGIN_EXP：保留 v3 winsorize 改进（v3 已验证有效）
- DEBT_RED：保留 v2 current_ratio 改进（v2 已验证有效）
- GROWTH_ACCEL：回退 v1 净利润 YoY 加速 + winsorize（与 ROE/MARGIN 一致）

### v4 改进经济含义

| 因子 | v4 公式 | 经济含义 |
|------|---------|---------|
| VT_QUALTREND_ROE_DELTA | `winsorize(roe[q] - roe[q-4], p5/p95)` | ROE 同比改善 → 盈利能力增强（裁剪极端值） |
| VT_QUALTREND_MARGIN_EXP | `winsorize(gm[q] - gm[q-4], p5/p95)` | 毛利率扩张 → 议价能力增强（裁剪极端值） |
| VT_QUALTREND_DEBT_RED | `current_ratio[q] - current_ratio[q-4]` | 流动比率上升 → 短期偿债能力改善 |
| VT_QUALTREND_GROWTH_ACCEL | `winsorize((np[q]/np[q-4]-1) - (np[q-1]/np[q-5]-1), p5/p95)` | 净利润增长率加速 → 二阶导为正（裁剪极端值） |

## 4. 完整因子列表

| 因子名 | 类别 | 状态 | IC_IR | max_corr |
|--------|------|------|-------|----------|
| VT_MOM_ACCEL_5_20 | - | rejected | +0.0000 | 0.762 |
| VT_MOM_OVERNIGHT_GAP | - | rejected | -0.1150 | 0.419 |
| VT_MOM_AUTOCORR_5D | - | rejected | -0.2561 | 0.297 |
| VT_MOM_HIGH_VOL_ALPHA | - | rejected | +0.1488 | 0.298 |
| VT_REV_BREADTH_5D | - | rejected | +0.0196 | 0.529 |
| VT_REV_VOL_DRAIN | - | rejected | +0.0363 | 0.444 |
| VT_REV_OVERREACTION | - | rejected | -0.0920 | 0.523 |
| VT_LIQ_AMIHUD_SCALED | - | rejected | +0.1009 | 0.606 |
| VT_LIQ_TURNOVER_REGIME | - | rejected | +0.1907 | 0.646 |
| VT_VOL_OVERNIGHT_RATIO | - | rejected | -0.0633 | 0.373 |
| VT_VOL_CLUSTERING | - | rejected | -0.0786 | 0.477 |
| VT_GROWTH_COMPOSITE | - | rejected | -0.0607 | 0.582 |
| VT_MICRO_CLOSE_STRENGTH | - | rejected | -0.0709 | 0.484 |
| VT_MICRO_ORDER_IMBALANCE | - | rejected | -0.0014 | 0.672 |
| VT_MICRO_GAP_TREND | - | rejected | +0.2492 | 0.510 |
| VT_MICRO_RANGE_RATIO | - | rejected | +0.0758 | 0.626 |
| VT_MICRO_VOL_SKEW | - | rejected | +0.3050 | 0.331 |
| VT_MICRO_VOL_SKEW_INV | - | rejected | -0.3050 | 0.331 |
| VT_MICRO_CLOSING_MOMENTUM | - | rejected | -0.1995 | 0.526 |
| VT_REV_VOL_DRAIN_INV | - | rejected | -0.0363 | 0.444 |
| VT_REV_OVERREACTION_INV | - | rejected | +0.0920 | 0.523 |
| VT_MOM_OVERNIGHT_GAP_INV | - | rejected | +0.1150 | 0.419 |
| VT_MOM_HIGH_VOL_ALPHA_INV | - | rejected | -0.1488 | 0.298 |
| VT_VOL_CLUSTERING_INV | - | rejected | +0.0786 | 0.477 |
| VT_QUALTREND_ROE_DELTA | - | rejected | +0.1811 | 0.408 |
| VT_QUALTREND_MARGIN_EXP | - | rejected | +0.2742 | 0.342 |
| VT_QUALTREND_DEBT_RED | - | rejected | +0.0839 | 0.430 |
| VT_QUALTREND_GROWTH_ACCEL | - | rejected | +0.1409 | 0.217 |
