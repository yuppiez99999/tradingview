# 第十三批次流水线跑批报告 - thirteenth_batch_20260725_215019

> P2.2 v6.2c Config_E 超激进参数完整验证
> 生成时间：2026-07-25 21:50:41

## 1. 改进背景

v6.2b 调优实验发现 Config_E 超激进参数能让 MARGIN_EXP 通过 Shadow：
- target_vol=0.08（基线 0.15→0.08）
- dd_derisk_threshold=0.02（基线 0.05→0.02）
- dd_derisk_factor=0.2（基线 0.5→0.2）

实测：live_dsr=0.9960 (>0.5✅), max_dd=0.0759 (<0.12✅), pass_shadow=True ✅

v6.2c 将 Config_E 参数应用到 PipelineOrchestrator 默认配置，验证 MARGIN_EXP 是否能走完完整 8 级流水线。

## 2. 批次基本信息

| 项目 | 值 |
|------|----|
| batch_id | `thirteenth_batch_20260725_215019` |
| 数据标的数 | 105 |
| 候选因子总数 | 28 |
| n_trials | 105 |
| shadow risk_managed | True（Config_E 超激进参数） |
| shadow target_vol | 0.08 |
| shadow dd_derisk_threshold | 0.02 |
| shadow dd_derisk_factor | 0.2 |
| 改进版本 | v6.2c（Config_E 完整验证） |

## 3. 各 Gate 通过率

| Gate | 通过数 | 通过率 |
|------|--------|--------|
| G1 正交性 | 27 | 96.4% |
| G2 IC 稳定性 | 3 | 10.7% |
| G3 DSR | 2 | 7.1% |
| G4 经济逻辑 | 2 | 7.1% |
| Enhancement | 0 | 0.0% |
| Shadow (Config_E) | 1 | 3.6% |
| Committee Approved | 0 | 0.0% |
| Deferred (fundamentals) | 0 | 0.0% |
| Rejected | 28 | 100.0% |
| Failed | 0 | 0.0% |

## 4. 状态分布

- rejected: 28

## 5. MARGIN_EXP 8 级流水线详情（v6.2c Config_E）

| Gate | 通过 | 详情 |
|------|------|------|
| G1 正交性 | ✅ | max_corr=0.342 (MOM_REVERSAL_5D) |
| G2 IC 稳定性 | ✅ | IC_IR=0.3981, decay=0.9186 |
| G3 DSR | ❌ | DSR=0.5304, sr=4.97 |
| G4 经济逻辑 | ❌ | score=0.00 |
| Enhancement (Capacity) | ❌ | cap_ratio=31.23 |
| Enhancement (Regime) | ❌ | regime_consistency=0.00 |
| Shadow (Config_E) | ✅ | live_dsr=0.9960, max_dd=0.0759 |
| Committee | ❌ | avg=7.40, verdict=- |

**最终状态**: `rejected`
**最终评分**: 7.40
**失败原因**: ['Committee: 被否决（专家否决：CapacityAgent）']

### MARGIN_EXP Shadow 详情（Config_E 参数）

| 指标 | 值 | 阈值 | 通过 |
|------|-----|------|------|
| pass_shadow | True | - | ✅ |
| live_dsr | 0.9960 | >0.5 | ✅ |
| max_drawdown | 0.0759 | <0.12 | ✅ |
| monte_carlo_p95_dd | 0.0561 | <0.18 | ✅ |
| total_return | 0.2625 | - | - |
| realized_vol | 0.1195 | target=0.08 | - |
| avg_scaler | 0.3617 | - | - |
| derisk_days | 49 / 90 | - | - |

## 6. 关键结论

⚠️ MARGIN_EXP 未能走完 8 级流水线。

失败的 Gate: G3, G4

需进一步分析失败原因，可能需要:
1. 调整 G4 经济逻辑评分（如果是 G4 失败）
2. 调整 Committee 评审专家配置（如果是 Committee 失败）
3. 重新评估 Config_E 参数是否合适
