# 第九批次流水线跑批报告 - ninth_batch_20260725_135116

> P2.1c+P2.1d 集成验证（risk_managed 默认 + 4 个新反向因子 + Committee 评审）
> 生成时间：2026-07-25 13:51:37

## 1. 批次基本信息

| 项目 | 值 |
|------|----|
| batch_id | `ninth_batch_20260725_135116` |
| 数据标的数 | 105 |
| 候选因子总数 | 24 |
| n_trials | 105 |
| shadow risk_managed | True（P2.1c 默认） |

## 2. 各 Gate 通过率

| Gate | 通过数 | 通过率 |
|------|--------|--------|
| G1 正交性 | 23 | 95.8% |
| G2 IC 稳定性 | 2 | 8.3% |
| G3 DSR 防过拟合 | 1 | 4.2% |
| G4 经济逻辑 | 1 | 4.2% |
| Enhancement | 0 | 0.0% |
| **Shadow (risk_managed)** | **0** | **0.0%** |
| **Committee (Approved)** | **0** | **0.0%** |

## 3. P2.1c 验收 - VT_MICRO_VOL_SKEW_INV 完整流水线

| Gate | 结果 | 关键指标 |
|------|------|---------|
| G1 正交性 | ✅ | max_corr=0.331 |
| G2 IC 稳定性 | ✅ | IC_IR=-0.3050 |
| G3 DSR | ❌ | DSR=-5.3932 |
| G4 经济逻辑 | ❌ | score=0.0 |
| Enhancement | ❌ | capacity_ratio=0.00 |
| **Shadow (risk_managed)** | **❌** | **max_dd=0.0000, live_dsr=0.0000** |
| **Committee** | **❌** | **avg=0.00, chair=-** |
| **最终状态** | **rejected** | **approved=False** |

### Shadow 风险管理层详情
- risk_managed: False
- max_drawdown: 0.0000（阈值 < 0.12）
- mc_p95_dd: 0.0000（阈值 < 0.18）
- live_dsr: 0.0000（阈值 > 0.5）
- avg_scaler: 0.0000
- derisk_triggered_days: 0

### Committee 评审详情
- avg_score: 0.00（阈值 >= 7.0）
- has_veto: False
- chair_decision: -
- approved: False


## 4. P2.1d 验收 - 4 个新反向因子

| 因子 | 原始 IC_IR | 预期反向 IC_IR | 实际 IC_IR | G2 通过 | 状态 |
|------|----------|-------------|----------|--------|------|
| VT_REV_OVERREACTION_INV | -0.20 | +0.20 | +0.0920 | ❌ | rejected |
| VT_MOM_OVERNIGHT_GAP_INV | -0.14 | +0.14 | +0.1150 | ❌ | rejected |
| VT_MOM_HIGH_VOL_ALPHA_INV | -0.13 | +0.13 | -0.1488 | ❌ | rejected |
| VT_VOL_CLUSTERING_INV | -0.13 | +0.13 | +0.0786 | ❌ | rejected |

## 5. 结论

- **P2.1c 集成验证**：risk_managed=True 作为 Shadow 默认配置已集成到 PipelineOrchestrator
- **P2.1d 反向因子**：4 个新反向因子已添加，观察其 IC_IR 反转效果
- **Committee 评审**：通过 Shadow 的因子自动进入 FactorCommittee 评审
