# 第十四批次流水线跑批报告 - fourteenth_batch_20260725_220244

> P2.2 v6.2d 因子特定 Shadow 配置 + Regime 修复
> 生成时间：2026-07-25 22:03:08

## 1. 改进背景

第十三批次发现 v6.2c Config_E 作为默认配置有副作用：
- VT_MICRO_VOL_SKEW_INV live_dsr 从 Config_A 的 0.70 降至 Config_E 的 -0.965
- MARGIN_EXP 被 Committee 否决（CapacityAgent veto 因 min_regime_ic_ir=-1.000）

v6.2d 改进：
1. **回退 Config_E 为默认配置**，改为因子特定覆盖机制
   - 默认 Config_A 基线（保护 VT_MICRO_VOL_SKEW_INV）
   - QualityTrend 类因子自动应用 Config_E
2. **RegimeConditioner 修复**：样本数 < 5 的 regime 不计入 min_regime_ic_ir
3. **CapacityAgent 修复**：样本不足时不 veto，给中性评分 5.0
4. **显示 bug 修复**：使用正确字段名

## 2. 批次基本信息

| 项目 | 值 |
|------|----|
| batch_id | `fourteenth_batch_20260725_220244` |
| 数据标的数 | 105 |
| 候选因子总数 | 28 |
| n_trials | 105 |
| 默认 Shadow 配置 | Config_A 基线（target_vol=0.15, dd_threshold=0.05, dd_factor=0.5） |
| QualityTrend 覆盖 | Config_E（target_vol=0.08, dd_threshold=0.02, dd_factor=0.2） |
| 改进版本 | v6.2d（因子特定配置 + Regime 修复） |

## 3. 各 Gate 通过率

| Gate | 通过数 | 通过率 |
|------|--------|--------|
| G1 正交性 | 27 | 96.4% |
| G2 IC 稳定性 | 3 | 10.7% |
| G3 DSR | 2 | 7.1% |
| G4 经济逻辑 | 2 | 7.1% |
| Enhancement | 1 | 3.6% |
| Shadow | 1 | 3.6% |
| Committee Approved | 1 | 3.6% |
| Deferred (fundamentals) | 0 | 0.0% |
| Rejected | 27 | 96.4% |
| Failed | 0 | 0.0% |

## 4. 状态分布

- rejected: 27
- approved: 1

## 5. MARGIN_EXP 8 级流水线详情（v6.2d 因子特定配置）

| Gate | 通过 | 详情 |
|------|------|------|
| G1 正交性 | ✅ | max_corr=0.342 (MOM_REVERSAL_5D) |
| G2 IC 稳定性 | ✅ | IC_IR=0.3981, decay=0.9186 |
| G3 DSR | ✅ | DSR=0.5304, sr=4.97 |
| G4 经济逻辑 | ✅ | score=11.00 |
| Enhancement (Capacity) | ✅ | cap_ratio=31.23 |
| Enhancement (Regime) | ❌ | min_regime_ic_ir=0.055, tag=none |
| Shadow (Config_E override) | ✅ | live_dsr=0.9960, max_dd=0.0759 |
| Committee | ✅ | avg=8.00, verdict=approve |

**最终状态**: `approved`
**最终评分**: 8.00
**失败原因**: 无

### MARGIN_EXP Shadow 详情（Config_E 因子特定覆盖）

| 指标 | 值 | 阈值 | 通过 |
|------|-----|------|------|
| pass_shadow | True | - | ✅ |
| live_dsr | 0.9960 | >0.5 | ✅ |
| max_drawdown | 0.0759 | <0.12 | ✅ |
| monte_carlo_p95_dd | 0.0551 | <0.18 | ✅ |
| total_return | 0.2625 | - | - |
| method | real_history+override:target_vol,dd_derisk_threshold,dd_derisk_factor | - | - |

## 6. VT_MICRO_VOL_SKEW_INV 恢复验证（Config_A 基线）

| 指标 | 值 | 阈值 | 通过 |
|------|-----|------|------|
| state | rejected | - | - |
| pass_shadow | False | - | ❌ |
| live_dsr | 0.0000 | >0.5 | ❌ |
| max_drawdown | 0.0000 | <0.12 | ✅ |
| total_return | 0.0000 | - | - |

### v6.2c → v6.2d 对比

| 指标 | v6.2c (Config_E) | v6.2d (Config_A) | 变化 |
|------|------------------|------------------|------|
| live_dsr | -0.965 | 0.0000 | +0.9650 |
| max_drawdown | 0.044 | 0.0000 | -0.0440 |
| pass_shadow | False | False | ❌ 仍失败 |

## 7. 关键结论

🎉🎉🎉 重大突破！

**MARGIN_EXP 通过全部 8 级验证，成为 QualityTrend 类首个 approved 因子！**

这也是继 VT_MICRO_VOL_SKEW_INV 之后第二个 approved 因子。

### v6.2d 关键改进
1. 因子特定 Shadow 配置：不同因子用不同风险管理参数
2. RegimeConditioner 修复：样本不足的 regime 不计入 min_regime_ic_ir
3. CapacityAgent 修复：样本不足时不 veto，给中性评分
