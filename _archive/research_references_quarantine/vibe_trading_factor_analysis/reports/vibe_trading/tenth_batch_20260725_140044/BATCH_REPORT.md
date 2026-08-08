# 第十批次流水线跑批报告 - tenth_batch_20260725_140044

> P2.2 集成验证（质量变化类因子 QualityTrend）
> 生成时间：2026-07-25 14:01:03

## 1. 批次基本信息

| 项目 | 值 |
|------|----|
| batch_id | `tenth_batch_20260725_140044` |
| 数据标的数 | 105 |
| 候选因子总数 | 28 |
| n_trials | 105 |
| shadow risk_managed | True（P2.1c 默认） |

## 2. 各 Gate 通过率

| Gate | 通过数 | 通过率 |
|------|--------|--------|
| G1 正交性 | 26 | 92.9% |
| G2 IC 稳定性 | 2 | 7.1% |
| G3 DSR | 1 | 3.6% |
| G4 经济逻辑 | 1 | 3.6% |
| Enhancement | 0 | 0.0% |
| Shadow (risk_managed) | 0 | 0.0% |
| Committee Approved | 0 | 0.0% |
| Deferred (fundamentals) | 0 | 0.0% |
| Rejected | 28 | 100.0% |
| Failed | 0 | 0.0% |

## 3. P2.2 验收 - 4 个 QualityTrend 因子

| 因子 | 状态 | G1 max_corr | IC 均值 | IC_IR | G1 | G2 |
|------|------|------------|---------|-------|----|----|
| VT_QUALTREND_ROE_DELTA | rejected | 0.543 (MOM_252D) | +0.0000 | +0.1364 | ✅ | ❌ |
| VT_QUALTREND_MARGIN_EXP | rejected | 0.279 (QUA_ROE) | +0.0000 | +0.1270 | ✅ | ❌ |
| VT_QUALTREND_DEBT_RED | rejected | 0.776 (QUA_DEBT_TO_EQUITY) | +0.0000 | +0.0000 | ❌ | ❌ |
| VT_QUALTREND_GROWTH_ACCEL | rejected | 0.292 (MOM_20D) | +0.0000 | +0.2676 | ✅ | ❌ |


### 设计经济含义

| 因子 | 公式 | 经济含义 |
|------|------|---------|
| VT_QUALTREND_ROE_DELTA | `roe[q] - roe[q-4]` | ROE 改善 → 盈利能力增强 → 看涨 |
| VT_QUALTREND_MARGIN_EXP | `gross_margin[q] - gross_margin[q-4]` | 毛利率扩张 → 议价能力增强 |
| VT_QUALTREND_DEBT_RED | `-(debt_to_equity[q] - debt_to_equity[q-4])` | 负债率下降 → 财务风险降低 |
| VT_QUALTREND_GROWTH_ACCEL | YoY 增长率的 QoQ 变化 | 增长率加速 → 二阶导为正 |

### 与现有因子库正交性预期

- QUA_ROE/QUA_GROSS_MARGIN/QUA_DEBT_TO_EQUITY 是水平值，QualityTrend 是变化率（不同维度）
- VT_GROWTH_COMPOSITE 是单期增长率，VT_QUALTREND_GROWTH_ACCEL 是增长率变化率（二阶导）

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
| VT_QUALTREND_ROE_DELTA | - | rejected | +0.1364 | 0.543 |
| VT_QUALTREND_MARGIN_EXP | - | rejected | +0.1270 | 0.279 |
| VT_QUALTREND_DEBT_RED | - | rejected | +0.0000 | 0.776 |
| VT_QUALTREND_GROWTH_ACCEL | - | rejected | +0.2676 | 0.292 |
