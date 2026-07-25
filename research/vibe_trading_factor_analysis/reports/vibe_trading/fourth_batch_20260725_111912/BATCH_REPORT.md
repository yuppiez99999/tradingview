# 首批次流水线跑批报告 - fourth_batch_20260725_111912

> CIO 视角批次质量评估（v1.0）
> 生成时间：2026-07-25 11:19:15

## 1. 批次基本信息

| 项目 | 值 |
|------|----|
| batch_id | `fourth_batch_20260725_111912` |
| 开始时间 | 2026-07-25T11:19:12.404311 |
| 结束时间 | 2026-07-25T11:19:15.409034 |
| 数据标的数 | 23 |
| 候选因子总数 | 16 |
| n_trials (多重检验基数) | 23 |
| portfolio_value | 1e8 |

## 2. 各 Gate 通过率

| Gate | 通过数 | 通过率 | 说明 |
|------|--------|--------|------|
| G1 正交性 | 3 | 18.8% | 与现有 50+ 因子相关性 \|corr\| < 0.5 |
| G2 IC 稳定性 | 0 | 0.0% | IC_IR_120d >= 0.3, decay < 0.6 |
| G3 DSR 防过拟合 | 0 | 0.0% | DSR > 0, n_trials >= 5 |
| G4 经济逻辑 | 0 | 0.0% | 评分 >= 7 |
| Enhancement | 0 | 0.0% | Capacity + Regime |
| Shadow 90d | 0 | 0.0% | live_DSR > 0.5, max_dd < 12% |
| **Committee Approved** | **0** | **0.0%** | avg >= 7, no veto |
| Rejected | 11 | 68.8% | 任一 Gate 未通过 |
| Failed | 0 | 0.0% | 异常崩溃 |
| **Deferred (fundamentals)** | **5** | **31.2%** | V/Q/S/Growth 因子在 fundamentals 为 proxy 时跳过评估 |

## 3. 状态分布

| 状态 | 因子数 |
|------|--------|
| rejected | 11 |
| deferred_fundamentals | 5 |

## 4. Top 10 因子详情（按流水线进度排序）

| # | 因子 | 最终状态 | G1\|corr\| | G2 IC_IR | G3 DSR | G4 评分 | Capacity | Regime | Shadow DSR/DD | Committee |
|---|------|---------|------------|----------|--------|---------|----------|--------|--------------|-----------|
| 1 | `VT_MOM_ILLIQUID_60D` | rejected | 0.852 | - | - | - | - | - | -/- | - |
| 2 | `VT_MOM_VOLUME_WEIGHTED_20D` | rejected | 0.928 | - | - | - | - | - | -/- | - |
| 3 | `VT_MOM_GAP` | rejected | 1.000 | - | - | - | - | - | -/- | - |
| 4 | `VT_MOM_HIGH_LOW_60D` | rejected | 0.782 | - | - | - | - | - | -/- | - |
| 5 | `VT_REV_SHORT_TERM` | rejected | 1.000 | - | - | - | - | - | -/- | - |
| 6 | `VT_REV_VOLUME_SPIKE` | rejected | 0.954 | - | - | - | - | - | -/- | - |
| 7 | `VT_REV_OVERREACTION` | rejected | 0.598 | -0.132 | - | - | - | - | -/- | - |
| 8 | `VT_LIQ_AMIHUD_SCALED` | rejected | 0.699 | 0.082 | - | - | - | - | -/- | - |
| 9 | `VT_LIQ_TURNOVER_REGIME` | rejected | 0.650 | 0.135 | - | - | - | - | -/- | - |
| 10 | `VT_VOL_REGIME` | rejected | 0.781 | - | - | - | - | - | -/- | - |


## 5. 失败原因聚合（Top 8）

| 失败 Gate | 因子数 |
|-----------|--------|
| Gate1 | 8 |
| Defer | 5 |
| Gate2 | 3 |


## 6. CIO 评估要点

### 6.1 数据质量
- 使用真实 A 股 OHLCV 数据（23 个标的，2 年）
- 510300 ETF 数据缺失，用 23 标的等权日收益作为基准代理（符合 project_memory 硬约束「避免 synthesize_ohlcv_from_returns」）
- fundamentals 使用价格代理（pe/pb/roe 占位），Gate4 经济逻辑评分应考虑此降级

### 6.2 简化实现风险
- **Gate2 IC 稳定性**：当前用单期 IC 经验映射 IC_IR，未做真实 120d 滚动（可能高估 IC_IR）
- **Gate3 DSR**：用最近 30 日多空 PnL 作为输入，样本量不达 120d 标准（DSR 检验功效偏弱）
- **Shadow 90d**：用 `[candidate.values] * 90` 复制因子值历史，未做真实日频因子值滚动（实际 live_DSR 会被高估）
- **Regime**：用占位历史，Regime 划分可信度有限

### 6.3 下批次改进方向
1. 实现 `compute_factor_history(price_data, factor_def)` 返回日频因子值序列
2. Gate2 改为真实 120d 滚动 IC_IR
3. Gate3 / Shadow 用真实 90 日每日因子值 + forward returns
4. 补齐 510300 ETF 真实数据（用 wind-mcp-skill）
5. 扩展标的覆盖至全市场（≥300 标的）

### 6.4 准入决议
- **本批次为架构验证批，不写入生产因子库**
- 待 S4 分析后，决定是否进入 S5（写入 alpha_factor_library.py）
- 若 G1-G4 通过率均 > 30% 且 Shadow 通过率 > 0%，可批准进入 S5
- 若通过率过低（< 10%），需先修复简化实现风险，再跑第二批次

## 7. 审计轨迹

完整流水线状态持久化于：
```
reports/vibe_trading/fourth_batch_20260725_111912/pipeline_state.json
```

包含每个候选因子的：
- 各 Gate 详细结果（max_abs_corr, IC, DSR, economic_score, capacity_ratio, live_dsr 等）
- 失败原因列表
- 委员会各 Agent 评分与最终决议

---
*本报告由 PipelineOrchestrator v1.0 自动生成，遵循 DECISION_v1.0 锁定参数。*
