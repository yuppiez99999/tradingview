# 第十一批次流水线跑批报告 - eleventh_batch_20260725_142456

> P2.2 v2 改进验证（QualityTrend 因子优化）
> 生成时间：2026-07-25 14:25:23

## 1. 批次基本信息

| 项目 | 值 |
|------|----|
| batch_id | `eleventh_batch_20260725_142456` |
| 数据标的数 | 105 |
| 候选因子总数 | 28 |
| n_trials | 105 |
| shadow risk_managed | True（P2.1c 默认） |
| 历史数据 schema_version | 2（含 revenue/yoy_pni 字段） |

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

## 3. P2.2 v2 改进对比 - 4 个 QualityTrend 因子

| 因子 | 状态 | v1 max_corr | v1 IC_IR | v2 max_corr | v2 IC_IR | corr 变化 | IC_IR 变化 | G1 | G2 |
|------|------|------------|---------|------------|---------|----------|-----------|----|----|
| VT_QUALTREND_ROE_DELTA | rejected | 0.543 (MOM_252D) | +0.1364 | 0.323 (MOM_20D) | +0.0240 | -0.220 | -0.1124 | ✅ | ❌ |
| VT_QUALTREND_MARGIN_EXP | rejected | 0.279 (QUA_ROE) | +0.1270 | 0.232 (QUA_ROE) | +0.0612 | -0.047 | -0.0658 | ✅ | ❌ |
| VT_QUALTREND_DEBT_RED | rejected | 0.776 (QUA_DEBT_TO_EQUITY) | +0.0000 | 0.430 (SIZE_LOG_REV) | +0.0839 | -0.346 | +0.0839 | ✅ | ❌ |
| VT_QUALTREND_GROWTH_ACCEL | rejected | 0.292 (MOM_20D) | +0.2676 | 0.304 (QUA_ROE) | +0.0224 | +0.012 | -0.2452 | ✅ | ❌ |


### v2 改进设计

| 因子 | v1 公式 | v2 公式 | 改进目标 |
|------|---------|---------|---------|
| VT_QUALTREND_ROE_DELTA | `roe[q] - roe[q-4]` | `rank(roe[q]) - rank(roe[q-4])` | 降低与 MOM_252D 的相关性 |
| VT_QUALTREND_MARGIN_EXP | `gm[q] - gm[q-4]` | `rank(gm[q]) - rank(gm[q-4])` | 统一标准化口径 |
| VT_QUALTREND_DEBT_RED | `-(d2e[q] - d2e[q-4])` | `current_ratio[q] - current_ratio[q-4]` | 解 QUA_DEBT_TO_EQUITY 共线 |
| VT_QUALTREND_GROWTH_ACCEL | 净利润 YoY 加速 | `rank(0.5*yoy_pni_accel + 0.5*rev_accel)` | 突破 IC_IR 0.3 阈值 |

### v2 改进经济含义

| 因子 | v2 公式 | 经济含义 |
|------|---------|---------|
| VT_QUALTREND_ROE_DELTA | `rank(roe[q]) - rank(roe[q-4])` | ROE 截面排名提升 → 相对盈利能力改善 |
| VT_QUALTREND_MARGIN_EXP | `rank(gm[q]) - rank(gm[q-4])` | 毛利率截面排名提升 → 相对议价能力增强 |
| VT_QUALTREND_DEBT_RED | `current_ratio[q] - current_ratio[q-4]` | 流动比率上升 → 短期偿债能力改善 |
| VT_QUALTREND_GROWTH_ACCEL | `rank(0.5*yoy_pni_accel + 0.5*rev_accel)` | 扣非净利 + 营收增长率双维度加速 |

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
| VT_QUALTREND_ROE_DELTA | - | rejected | +0.0240 | 0.323 |
| VT_QUALTREND_MARGIN_EXP | - | rejected | +0.0612 | 0.232 |
| VT_QUALTREND_DEBT_RED | - | rejected | +0.0839 | 0.430 |
| VT_QUALTREND_GROWTH_ACCEL | - | rejected | +0.0224 | 0.304 |
