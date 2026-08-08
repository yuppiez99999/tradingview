# 第六批次流水线跑批报告 - sixth_batch_20260725_120832

> CIO 视角批次质量评估（P1.1+P1.2+P1.3 改进验证）
> 生成时间：2026-07-25 12:08:43

## 1. 批次基本信息

| 项目 | 值 |
|------|----|
| batch_id | `sixth_batch_20260725_120832` |
| 开始时间 | 2026-07-25T12:08:32.564855 |
| 结束时间 | 2026-07-25T12:08:43.904082 |
| 数据标的数 | 105 |
| 候选因子总数 | 16 |
| n_trials (多重检验基数) | 105 |
| portfolio_value | 1e8 |
| history_days (滚动 IC 窗口) | 120 |
| forward_window | 5 |

## 2. P1 改进验收

| 改进项 | 目标 | 实际 | 验收 |
|--------|------|------|------|
| P1.1 标的扩展 | ≥100 标的 | 105 标的 | ✅ |
| P1.2 真实 fundamentals | 真实率 ≥ 50% | real=100 proxy=5 (95.2%) | ✅ |
| P1.3 真实基准 | 沪深300指数 | 483 天 | ✅ |
| G2 通过率 | > 30% | 6.2% | ⚠️ |

## 3. 各 Gate 通过率

| Gate | 通过数 | 通过率 | 说明 |
|------|--------|--------|------|
| G1 正交性 | 11 | 68.8% | 与现有 50+ 因子相关性 \|corr\| < 0.5 |
| G2 IC 稳定性 | 1 | 6.2% | IC_IR_120d >= 0.3, decay < 0.6 |
| G3 DSR 防过拟合 | 0 | 0.0% | DSR > 0, n_trials >= 5 |
| G4 经济逻辑 | 0 | 0.0% | 评分 >= 7 |
| Enhancement | 0 | 0.0% | Capacity + Regime |
| Shadow 90d | 0 | 0.0% | live_DSR > 0.5, max_dd < 12% |
| **Committee Approved** | **0** | **0.0%** | avg >= 7, no veto |
| Rejected | 16 | 100.0% | 任一 Gate 未通过 |
| Failed | 0 | 0.0% | 异常崩溃 |
| **Deferred (fundamentals)** | **0** | **0.0%** | V/Q/S/Growth 因子在 fundamentals 为 proxy 时跳过评估 |

## 4. 状态分布

| 状态 | 因子数 |
|------|--------|
| rejected | 16 |

## 5. Top 10 因子详情

| # | 因子 | 最终状态 | G1\|corr\| | G2 IC_IR | G3 DSR | G4 评分 | Capacity | Regime | Shadow DSR/DD | Committee |
|---|------|---------|------------|----------|--------|---------|----------|--------|--------------|-----------|
| 1 | `VT_MOM_ACCEL_5_20` | rejected | 0.762 | - | - | - | - | - | -/- | - |
| 2 | `VT_MOM_OVERNIGHT_GAP` | rejected | 0.419 | -0.139 | - | - | - | - | -/- | - |
| 3 | `VT_MOM_AUTOCORR_5D` | rejected | 0.312 | 0.172 | - | - | - | - | -/- | - |
| 4 | `VT_MOM_HIGH_VOL_ALPHA` | rejected | 0.303 | -0.133 | - | - | - | - | -/- | - |
| 5 | `VT_REV_BREADTH_5D` | rejected | 0.529 | -0.056 | - | - | - | - | -/- | - |
| 6 | `VT_REV_VOL_DRAIN` | rejected | 0.444 | -0.327 | -4.347 | - | - | - | -/- | - |
| 7 | `VT_REV_OVERREACTION` | rejected | 0.516 | -0.195 | - | - | - | - | -/- | - |
| 8 | `VT_LIQ_AMIHUD_SCALED` | rejected | 0.608 | -0.026 | - | - | - | - | -/- | - |
| 9 | `VT_LIQ_TURNOVER_REGIME` | rejected | 0.646 | 0.034 | - | - | - | - | -/- | - |
| 10 | `VT_VOL_OVERNIGHT_RATIO` | rejected | 0.381 | -0.039 | - | - | - | - | -/- | - |


## 6. 失败原因聚合（Top 8）

| 失败 Gate | 因子数 |
|-----------|--------|
| Gate2 | 10 |
| Gate1 | 5 |
| Gate3 | 1 |


## 7. CIO 评估要点

### 7.1 P1 改进成效分析

**P1.1 标的扩展（23 → 105）**：
- IC 标准误从 ~0.22 降至 ~0.10（理论值）
- IC_IR 上限从 ~0.45 提升至 ~10.25（理论值）
- 实际 G2 通过率 6.2% (未达 > 30% 目标)

**P1.2 真实 fundamentals（real=100 / proxy=5）**：
- V/Q/S/Growth 因子可正常评估，不再全部 defer
- 真实 PE/PB/ROE/毛利率数据驱动 Gate4 经济逻辑评分

**P1.3 沪深300基准**：
- 替代等权代理，483 天真实日收益率
- Regime 划分基于真实市场基准，可信度提升

### 7.2 准入决议
- **G2 通过率 6.2%** ≤ 30% 阈值，暂缓 S5
- **Approved 因子数 0** 个
- 通过 G2-G4 全链路的因子可写入 alpha_factor_library.py（带 origin="vibe_trading" 标签）

## 8. 审计轨迹

完整流水线状态持久化于：
```
reports/vibe_trading/sixth_batch_20260725_120832/pipeline_state.json
```

---
*本报告由 PipelineOrchestrator v1.0 + P1.1+P1.2+P1.3 改进自动生成。*
