# 第七批次流水线跑批报告 - seventh_batch_20260725_131659

> CIO 视角批次质量评估（P2.1 微观结构因子验证）
> 生成时间：2026-07-25 13:17:14

## 1. 批次基本信息

| 项目 | 值 |
|------|----|
| batch_id | `seventh_batch_20260725_131659` |
| 开始时间 | 2026-07-25T13:16:59.050420 |
| 结束时间 | 2026-07-25T13:17:14.738945 |
| 数据标的数 | 105 |
| 候选因子总数 | 19 |
| n_trials (多重检验基数) | 105 |
| portfolio_value | 1e8 |
| history_days (滚动 IC 窗口) | 120 |
| forward_window | 5 |

## 2. P2.1 微观结构因子验收

| 改进项 | 目标 | 实际 | 验收 |
|--------|------|------|------|
| 新增微观结构因子 | 7 个 | VT_MICRO_* x6 + VT_REV_VOL_DRAIN_INV x1 | ✅ |
| 移除共线 V/Q/S 因子 | 4 个 | VAL_*/QUA_*/SIZE_LOG_NORMALIZED 已过滤 | ✅ |
| 数据基础设施（沿用 P1） | 105 标的 + 真实 fund | 105 标的 + 真实率 95.2% | ✅ |
| **G1 通过率** | **> 80%** | **94.7%** | **✅** |
| **G2 通过率** | **> 30%** | **15.8%** | **⚠️** |
| **微观结构因子通过 G2** | **>= 1** | **1** | **✅** |

## 3. 各 Gate 通过率

| Gate | 通过数 | 通过率 | 说明 |
|------|--------|--------|------|
| G1 正交性 | 18 | 94.7% | 与现有 50+ 因子相关性 \|corr\| < 0.5 |
| G2 IC 稳定性 | 3 | 15.8% | IC_IR_120d >= 0.3, decay < 0.6 |
| G3 DSR 防过拟合 | 0 | 0.0% | DSR > 0, n_trials >= 5 |
| G4 经济逻辑 | 0 | 0.0% | 评分 >= 7 |
| Enhancement | 0 | 0.0% | Capacity + Regime |
| Shadow 90d | 0 | 0.0% | live_DSR > 0.5, max_dd < 12% |
| **Committee Approved** | **0** | **0.0%** | avg >= 7, no veto |
| Rejected | 19 | 100.0% | 任一 Gate 未通过 |
| Failed | 0 | 0.0% | 异常崩溃 |
| **Deferred (fundamentals)** | **0** | **0.0%** | V/Q/S/Growth 因子在 fundamentals 为 proxy 时跳过评估 |

## 4. 状态分布

| 状态 | 因子数 |
|------|--------|
| rejected | 19 |

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
| Gate2 | 15 |
| Gate3 | 3 |
| Gate1 | 1 |


## 7. CIO 评估要点

### 7.1 P2.1 微观结构因子设计成效

**新增 7 个微观结构因子**（与现有 51 因子不共线的新维度）：
- VT_MICRO_CLOSE_STRENGTH（收盘强度）
- VT_MICRO_ORDER_IMBALANCE（订单流不平衡代理）
- VT_MICRO_GAP_TREND（隔夜跳空方向累积）
- VT_MICRO_RANGE_RATIO（日内振幅相对变化率）
- VT_MICRO_VOL_SKEW（成交量分布偏态）
- VT_MICRO_CLOSING_MOMENTUM（收盘相对日内中点偏移）
- VT_REV_VOL_DRAIN_INV（缩量趋势持续，VT_REV_VOL_DRAIN 反向使用）

**移除 4 个共线因子**：
- VT_VAL_COMPOSITE / VT_VAL_EARNINGS_YIELD_SCALED（与 VAL_PE corr=1.00）
- VT_QUA_COMPOSITE（与 QUA_DEBT_TO_EQUITY corr=0.99）
- VT_SIZE_LOG_NORMALIZED（与 SIZE_LOG_MCAP corr=1.00）

### 7.2 G2 通过率分析
- **G2 通过率 15.8%** (未达 > 30% 目标)
- 微观结构因子通过 G2 数量: 1
- 与第六批次 v3 (15.8% G2) 对比，验证 P2.1 微观结构维度是否带来 Alpha 提升

### 7.3 准入决议
- **G2 通过率 15.8%** ≤ 30% 阈值，暂缓 S5
- **Approved 因子数 0** 个
- 通过 G2-G4 全链路的因子可写入 alpha_factor_library.py（带 origin="vibe_trading" 标签）

## 8. 审计轨迹

完整流水线状态持久化于：
```
reports/vibe_trading/seventh_batch_20260725_131659/pipeline_state.json
```

---
*本报告由 PipelineOrchestrator v1.0 + P2.1 微观结构因子改进自动生成。*
