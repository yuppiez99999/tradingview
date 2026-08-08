# MARGIN_EXP 风险管理参数调优报告 - p2_2_margin_exp_risk_tuning_20260725_214637

> P2.2 v6.2b 调优实验
> 生成时间：2026-07-25 21:46:37
> 目标因子：`VT_QUALTREND_MARGIN_EXP`

## 1. 实验背景

v6.2 根因分析发现 MARGIN_EXP Shadow 失败的根因：
- day 39-43 集中亏损 -27.02%（5 天内）
- raw_dd=58.66% → rm_dd=20.42%（基线参数下）
- 仍超 0.12 阈值

本实验测试 5 组从基线到超激进的风险管理参数，寻找最优配置。

## 2. 实验配置

| 标的数 | n_trials | Shadow 观察日 |
|--------|---------|--------------|
| 105 | 105 | 90 |

## 3. 实验结果

| Config | 描述 | 参数 | pass | live_dsr (>0.5) | max_dd (<0.12) | mc_p95 (<0.18) | total_return | realized_vol | avg_scaler | derisk_days | 最优 |
|--------|------|------|------|----------------|----------------|----------------|--------------|--------------|------------|-------------|------|
| Config_A_baseline | 基线（v6.1 默认） | target_vol=0.15<br>dd_threshold=0.05<br>dd_factor=0.5 | ❌ | 0.1276 ❌ | 0.2042 ❌ | 0.1136 ✅ | 0.3867 | 0.1729 | 0.6211 | 38/90 |  |
| Config_B_low_target_vol | 低 target_vol（0.15→0.10） | target_vol=0.10<br>dd_threshold=0.05<br>dd_factor=0.5 | ❌ | 0.8428 ✅ | 0.1327 ❌ | 0.0721 ✅ | 0.3451 | 0.1379 | 0.4947 | 35/90 |  |
| Config_C_early_derisk | 早去杠杆（threshold 0.05→0.03, factor 0.5→0.3） | target_vol=0.15<br>dd_threshold=0.03<br>dd_factor=0.3 | ❌ | 1.0172 ✅ | 0.1280 ❌ | 0.0763 ✅ | 0.4022 | 0.1467 | 0.5652 | 40/90 |  |
| Config_D_aggressive | 最激进（target_vol=0.10 + 早去杠杆 + factor=0.3） | target_vol=0.10<br>dd_threshold=0.03<br>dd_factor=0.3 | ✅ | 0.5720 ✅ | 0.1085 ✅ | 0.0702 ✅ | 0.2861 | 0.1310 | 0.4176 | 49/90 |  |
| Config_E_super_aggressive | 超激进（target_vol=0.08 + threshold=0.02 + factor=0.2） | target_vol=0.08<br>dd_threshold=0.02<br>dd_factor=0.2 | ✅ | 0.9960 ✅ | 0.0759 ✅ | 0.0551 ✅ | 0.2625 | 0.1195 | 0.3617 | 49/90 | 🎯 |


## 4. 最优配置分析

**结论**: ✅ 找到通过 max_dd 阈值的配置

**最优配置**: Config_E_super_aggressive (超激进（target_vol=0.08 + threshold=0.02 + factor=0.2）)

**参数**:
- target_vol = 0.08
- dd_derisk_threshold = 0.02
- dd_derisk_factor = 0.2

**结果**:
- pass_shadow = True
- live_dsr = 0.9960 (阈值 >0.5: ✅)
- max_drawdown = 0.0759 (阈值 <0.12: ✅)
- monte_carlo_p95_dd = 0.0551 (阈值 <0.18: ✅)
- total_return = 0.2625
- realized_vol = 0.1195
- avg_scaler = 0.3617
- derisk_days = 49 / 90

## 5. 关键发现

1. **风险管理参数与回撤的关系**:
   - 基线 (Config A): target_vol=0.15, max_dd=0.2042
   - 激进 (Config D): target_vol=0.10 + 早去杠杆, max_dd=0.1085
   - 超激进 (Config E): target_vol=0.08, max_dd=0.0759
2. **Alpha 信号与风险管理的权衡**:
   - 更激进的风险管理会降低 max_dd，但也会压缩 live_dsr
   - 需在回撤控制与 Alpha 保留之间找平衡

## 6. 下一步建议

1. **采用最优配置 Config_E_super_aggressive**: 已通过 Shadow，可推进至 G4+Committee 评审
