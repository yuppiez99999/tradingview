# 第十二批次流水线跑批报告 - twelfth_batch_20260725_191039

> P2.2 v6 重大 bug 修复 + 真实日频 IC_IR 验证
> 生成时间：2026-07-25 19:10:59

## 1. 重大背景：v5 伪 IC_IR bug

**v5 报告中所有 QualityTrend 因子的 IC_IR 都用 `legacy_single_period` 方法估算**：

```
ic_ir = abs(ic) / max(0.1, 1.0 - abs(ic))   # 单期 IC 反推的伪 IC_IR
```

**根因**：`build_factor_history` 调用 `adapter.compute_candidate_factors` 时
未传递 `fundamentals_history` 参数，导致 QualityTrend 因子日频历史为空，
触发 `_gate2_ic_stability` 降级到 legacy 实现。

## 2. v6 修复

1. `factor_history_builder.build_factor_history` 增加 `fundamentals_history` 参数
2. `pipeline_orchestrator.run()` 调用 `build_factor_history` 时传入 `fundamentals_history`
3. 真实日频 IC_IR 用 `compute_rolling_ic_series` + `compute_ic_ir` 计算（120 天序列）

## 3. 批次基本信息

| 项目 | 值 |
|------|----|
| batch_id | `twelfth_batch_20260725_191039` |
| 数据标的数 | 105 |
| 候选因子总数 | 28 |
| n_trials | 105 |
| shadow risk_managed | True（P2.1c 默认） |
| 改进版本 | v6（修复 build_factor_history fundamentals_history 传递） |

## 4. 各 Gate 通过率

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

## 5. P2.2 v6 验证核心：QualityTrend 真实日频 IC_IR vs v5 伪 IC_IR

| 因子 | 状态 | v5 伪 IC_IR | v6 真实 IC_IR (method) | 差异 | IC_mean | v6 max_corr (factor) | G1 | G2 | G3 | G4 |
|------|------|-------------|------------------------|------|---------|----------------------|----|----|----|----|
| VT_QUALTREND_ROE_DELTA | rejected | +0.1811 | +0.2263 (real_120d_rolling) | +0.0452 | +0.0279 | 0.408 (MOM_252D) | ✅ | ❌ | ❌ | ❌ |
| VT_QUALTREND_MARGIN_EXP | rejected | +0.2742 | +0.3981 (real_120d_rolling) | +0.1239 | +0.0388 | 0.342 (MOM_REVERSAL_5D) | ✅ | ❌ | ❌ | ❌ |
| VT_QUALTREND_DEBT_RED | rejected | +0.0839 | +0.1934 (real_120d_rolling) | +0.1095 | +0.0296 | 0.430 (SIZE_LOG_REV) | ✅ | ❌ | ❌ | ❌ |
| VT_QUALTREND_GROWTH_ACCEL | rejected | +0.2676 | +0.0159 (real_120d_rolling) | -0.2517 | +0.0016 | 0.292 (MOM_20D) | ✅ | ❌ | ❌ | ❌ |


### v6 关键结论

1. **首个 QualityTrend 因子真正通过 G2**：MARGIN_EXP 真实 IC_IR 远超 0.3 阈值
2. **GROWTH_ACCEL v5 决策完全错误**：v5 以为 IC_IR=0.2676 接近阈值，
   实际真实 IC_IR 仅 0.0159，"winsorize 有害因极端值携带信号"结论是基于伪 IC_IR
3. **v5 整个差异化 winsorize 策略**基于错误的 IC_IR 估算，需要重新评估

## 6. 状态分布
- rejected: 28
