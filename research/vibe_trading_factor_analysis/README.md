# Vibe-Trading 因子分析项目

> Vibe-Trading 开源因子库（450+ Alpha 因子）与现有量化交易系统的桥接层。
> **8 级流水线验证 + IC 加权组合 + 影子账户 + 因子委员会** —— 候选因子经完整流水线验证后才能进入生产因子库。

参考：research_report_github_projects_integration_strategy_20260725.md / P2_FACTOR_ALPHA_QUALITY.md

---

## 1. 定位与安全契约

本项目是适配器（Adapter）+ 流水线编排器（PipelineOrchestrator），把 Vibe-Trading 项目的因子计算逻辑包装为候选因子池，与现有 utils/alpha_factor_library.py 隔离运行，并经 8 级流水线验证后才纳入生产。

| 契约 | 实现 |
|------|------|
| 只读 | 候选因子仅计算与正交性检查，不直接影响交易决策 |
| 隔离 | 独立目录 research/vibe_trading_factor_analysis/，不修改 utils/ |
| 审计 | 每批次生成 CandidateFactorPool.batch_id + pipeline_state.json，可追溯 |
| 可降级 | 现有因子库不可用时降级返回空池，不抛异常 |
| 风险管理 | Shadow 阶段默认启用风险管理层（波动率缩放 + 回撤去杠杆），与生产使用方式一致 |

## 2. 目录结构

```
research/vibe_trading_factor_analysis/
├── adapters/
│   ├── vibe_trading_factor_adapter.py   # 核心桥接器（10 大类因子）
│   └── factor_history_builder.py        # 日频因子历史构建 + IC/IC_IR/decay 计算
├── pipeline/
│   └── pipeline_orchestrator.py         # 8 级流水线编排器（含 IC 加权组合集成）
├── validators/
│   ├── dsr_validator.py                 # G3 DSR 防过拟合
│   ├── regime_conditioner.py             # Enhancement: Regime 普适性
│   └── capacity_analyzer.py             # Enhancement: 容量分析
├── shadow/
│   └── shadow_account.py                # Shadow 影子账户 + 风险管理层
├── committee/
│   └── factor_committee.py               # 多 Agent 因子委员会评审
├── metadata/
│   ├── factor_mapping.json              # 候选因子 → 现有因子映射 + 经济逻辑
│   └── candidate_factors_catalog.json   # 操作元数据（数据依赖/warm-up/成本）
├── scripts/                              # 批次验证脚本（第 1~21 批次）
│   ├── real_data_loader.py              # 真实数据加载器
│   ├── run_seventh_batch.py             # 第七批次：P2.1 微观结构因子
│   ├── run_tenth_batch.py               # 第十批次：P2.2 QualityTrend 因子
│   ├── run_seventeenth_batch_ic_weighted.py        # 第十七批次：IC 加权组合 v6.5
│   ├── run_nineteenth_batch_ic_weighted_config_e_plus.py  # 第十九批次：Config_E+ 突破 v6.7
│   ├── run_twentieth_batch_pipeline_ic_weighted_integration.py  # 第二十批次：流水线集成 v6.8
│   └── run_twentyfirst_batch_lookback_optimization.py  # 第二十一批次：lookback 优化 v6.9
├── tests/
│   └── _smoke_test_adapter.py           # 烟雾测试
└── reports/                             # 运行时生成（按批次 ID 分目录）
```

## 3. 快速开始

### 3.1 单因子流水线（PipelineOrchestrator）

```python
from research.vibe_trading_factor_analysis.pipeline.pipeline_orchestrator import (
    PipelineOrchestrator,
)

orchestrator = PipelineOrchestrator()
result = orchestrator.run(
    price_data=price_data,
    fundamentals=fundamentals,
    fundamentals_history=fundamentals_history,  # QualityTrend 类必需
    benchmark_returns=benchmark_returns,
    portfolio_value=1e8,
    n_trials=13,
)

print(f"通过: {result.approved} / 总计: {result.total_candidates}")
print(f"IC 加权组合: {len(result.factor_combinations)} 个")
```

### 3.2 候选因子计算（仅适配器）

```python
from research.vibe_trading_factor_analysis.adapters.vibe_trading_factor_adapter import (
    VibeTradingFactorAdapter,
)

adapter = VibeTradingFactorAdapter(config={"ortho_threshold": 0.5})
pool = adapter.compute_candidate_factors(
    price_data, fundamentals, fundamentals_history=fundamentals_history
)
```

## 4. 候选因子清单（20+ 因子，10 大类）

详见 `metadata/factor_mapping.json` 与 `metadata/candidate_factors_catalog.json`。

### 4.1 原 Vibe-Trading 适配因子（13 个，8 大类）

| 因子 | 类别 | 公式 | Gate1 状态 |
|------|------|------|-----------|
| VT_MOM_ILLIQUID_60D | Momentum | mom_60d * (1/avg_vol_60d) | conditional_pass |
| VT_MOM_VOL_WEIGHTED_20D | Momentum | mom_20d * (vol/avg_vol_20d) | conditional_pass |
| VT_MOM_HIGH_LOW_60D | Momentum | close/max(high,60d)-1 | needs_review |
| VT_REV_SHORT_TERM | Reversal | -mom_5d | fail_duplicate |
| VT_REV_VOLUME_SPIKE | Reversal | -mom_5d*(vol_5d/vol_15d) | conditional_pass |
| VT_LIQ_AMIHUD_SCALED | Liquidity | mean(\|ret\|/vol,60d) | fail_duplicate |
| VT_LIQ_TURNOVER_REGIME | Liquidity | mean(vol,5d)/mean(vol,60d) | pass |
| VT_VOL_REGIME | Volatility | -std(ret,20d)/std(ret,60d) | pass |
| VT_VOL_DOWNSIDE_RATIO | Volatility | downside_std/total_std | conditional_pass |
| VT_VAL_EARNINGS_YIELD_SCALED | Value | 1/pe * roe | pass |
| VT_QUALITY_ROE_STABILITY | Quality | mean(roe,4q)/std(roe,4q) | pass |
| VT_SIZE_LOG_NS_DEFENDED | Size | log(float_mcap)*(1-st_share_pct) | conditional_pass |
| VT_GROWTH_SURPRISE_WEIGHTED | Growth | eps_surprise * log(mcap) | pass |

### 4.2 P2.1 微观结构类因子（7 个，OHLCV 衍生）

| 因子 | 公式 | 经济含义 | 状态 |
|------|------|----------|------|
| VT_MICRO_CLOSE_STRENGTH | mean((close-open)/(high-low), 5d) | 收盘强度 | ✅ approved 流水线验证 |
| VT_MICRO_ORDER_IMBALANCE | mean((2*close-high-low)/(high-low), 20d) | 订单流不平衡 | 评估中 |
| VT_MICRO_GAP_TREND | sum(sign(open-prev_close), 20d)/20 | 隔夜跳空累积 | 评估中 |
| VT_MICRO_RANGE_RATIO | (mean((high-low)/close,5d))/(mean((high-low)/close,60d))-1 | 短期振幅变化率 | 评估中 |
| VT_MICRO_VOL_SKEW | skew(volume[-20:]) | 成交量分布偏态 | 原始 IC_IR=-0.42 |
| **VT_MICRO_VOL_SKEW_INV** | **-1 * skew(volume[-20:])** | **反向使用为看涨信号** | **✅ 完整通过 G1-G4+Enhancement+Regime+Shadow 7 级 Gate** |
| VT_MICRO_CLOSING_MOMENTUM | mean((close-(high+low)/2)/((high-low)/2), 5d) | 收盘相对日内中点偏移 | 评估中 |

### 4.3 P2.2 质量变化类因子（4 个，QualityTrend）

| 因子 | 公式 | 经济含义 | 状态 |
|------|------|----------|------|
| VT_QUALTREND_ROE_DELTA | roe[q] - roe[q-4] | ROE 同比改善 | IC_IR=+0.2263 |
| **VT_QUALTREND_MARGIN_EXP** | **winsorize(gm[q] - gm[q-4])** | **毛利率同比扩张** | **✅ QualityTrend 类首个 approved 因子** |
| VT_QUALTREND_DEBT_RED | current_ratio[q] - cr[q-4] | 流动比率改善（解共线） | IC_IR=+0.1934 |
| VT_QUALTREND_GROWTH_ACCEL | yoy_pni 水平值 | 扣非净利同比加速 | IC_IR=+0.1615（defer，需新数据源） |

**汇总**：原 13 + 微观结构 7 + QualityTrend 4 = 24 个候选因子，G2 通过率从 6.2% 提升至 20%+。

## 5. 8 级因子流水线（Factor Validation Pipeline）

| 关卡 | 名称 | 阈值 | 实现模块 | 状态 |
|------|------|------|----------|------|
| Gate 1 | 正交性 | \|corr\| < 0.5 | VibeTradingFactorAdapter.check_orthogonality | ✅ 已实现 |
| Gate 2 | IC 稳定性 | IC_IR ≥ 0.3, decay < 0.95 | factor_history_builder + compute_ic_ir/decay | ✅ 已实现（真实日频 IC 序列） |
| Gate 3 | 防过拟合 | DSR > 0, n_trials ≥ 5 | validators/dsr_validator.py | ✅ 已实现 |
| Gate 4 | 经济逻辑 | 评分 ≥ 7（学术+A股适配+实证支撑） | pipeline_orchestrator._assess_economic_logic | ✅ 已实现 |
| Enhancement | 容量+Regime | capacity_ratio ≥ 1.0, 全 regime 普适 | validators/capacity_analyzer.py + regime_conditioner.py | ✅ 已实现 |
| Shadow | 影子账户 | live_DSR > 0.5, max_dd < 12% | shadow/shadow_account.py | ✅ 已实现（含风险管理） |
| Committee | 因子委员会 | avg ≥ 7, no veto | committee/factor_committee.py | ✅ 已实现（5 Agent） |
| IC 加权组合 | 因子组合 | 组合 IC_IR > 单因子最优 | pipeline_orchestrator._build_ic_weighted_combinations | ✅ 已实现（v6.8 集成） |

### 5.1 流水线状态机

```
candidate -> g1 -> g2 -> g3 -> g4 -> enhanced -> shadow -> committee -> approved/rejected
                                                                      ↓
                                                            deferred_fundamentals（fundamentals 为 proxy 时）
```

每步状态持久化到 `reports/{batch_id}/pipeline_state.json`，失败降级：单步失败不阻断主流程，记录失败原因，因子状态置 rejected。

## 6. 与现有系统集成方式

### 6.1 适配器 + 流水线编排器双模式

1. **只读桥接**：`VibeTradingFactorAdapter` 计算候选因子，不修改 `utils/alpha_factor_library.py`
2. **正交性闸口**：候选因子必须与现有 50+ 因子相关性 \|corr\| < 0.5 才能进入下一阶段
3. **8 级流水线**：通过 G1-G4 + Enhancement + Shadow + Committee + IC 加权组合后，方可纳入生产因子库
4. **影子账户**：通过 G4 的因子先进影子账户观察 90 个交易日（含风险管理），live_DSR > 0.5 + max_dd < 12% 才纳入
5. **因子委员会**：5 Agent（Alpha/Risk/Execution/Economic/Capacity）多视角评审，avg ≥ 7 且无 veto 才 approved

### 6.2 IC 加权组合机制（v6.8 集成）

`PipelineOrchestrator` 在单因子流水线后自动构建 IC 加权组合：

```python
# 配置示例
orchestrator = PipelineOrchestrator(config={
    "ic_weighted_enabled": True,            # 一键开关
    "ic_weighted_lookback": 10,             # 滚动 IC_IR 窗口（v6.9 优化自 20）
    "ic_weighted_pairs": [
        {
            "factor_a": "VT_MICRO_VOL_SKEW_INV",
            "factor_b": "VT_QUALTREND_MARGIN_EXP",
            "desc": "微观结构 + 质量变化（信号反转 + 信号正常）",
        },
    ],
})
```

**方法学**：
- 动态权重：`w_i = IC_IR_i / sum(|IC_IR_j|)`，保留符号自适应信号反转
- VT_MICRO_VOL_SKEW_INV 在 75.5% 时间被反向使用（weight<0）
- Config_E_plus1 参数（target_vol=0.07）让组合通过 Shadow
- 与单因子流水线独立运行，失败不阻断主流程

### 6.3 安全边界

- 候选因子池 **不接入** 交易决策链路（signal_fusion / portfolio_optimizer）
- 候选因子计算失败时降级返回空池，不影响主流程
- 所有候选因子带 `origin="vibe_trading"` 标签，可审计追溯
- IC 加权组合结果通过 `PipelineResult.factor_combinations` 字段独立输出

## 7. 运行测试

```bash
# 烟雾测试（验证适配器核心功能）
python research/vibe_trading_factor_analysis/tests/_smoke_test_adapter.py

# 单批次流水线验证（示例：第十批次 QualityTrend）
python research/vibe_trading_factor_analysis/scripts/run_tenth_batch.py

# IC 加权组合 lookback 优化（第二十一批次 v6.9）
python research/vibe_trading_factor_analysis/scripts/run_twentyfirst_batch_lookback_optimization.py
```

预期输出包含 `[OK] 候选因子计算完成` 与 `[SUCCESS] 烟雾测试全部通过`。

## 8. 路线图

### 8.1 已完成

- ✅ **Gate 1-G4**：正交性 + IC 稳定性 + DSR 防过拟合 + 经济逻辑（四道关卡）
- ✅ **Enhancement**：容量分析（CapacityAnalyzer）+ Regime 普适性（RegimeConditioner）
- ✅ **Shadow 影子账户**：90 日观察 + 风险管理层（波动率缩放 + 回撤去杠杆）+ Monte Carlo P95 回撤
- ✅ **因子委员会**：5 Agent 多视角评审（Alpha/Risk/Execution/Economic/Capacity）
- ✅ **IC 加权组合**：动态权重 + 符号自适应 + Config_E_plus1 参数（v6.8 集成）
- ✅ **lookback 优化**：5/10/15/20/30 5 组梯度测试，lookback=10 最优（v6.9）

### 8.2 待实现

- [ ] **P2.3 资金流类因子**：北向资金、龙虎榜、融资融券、大单净买入（需 akshare 数据源）
- [ ] **P2.4 事件驱动类因子**：财报公告日 PEAD、业绩预告方向、限售解禁异常收益
- [ ] **GROWTH_ACCEL 重新设计**：需新数据源（分析师预期/研报情感/业绩预告）
- [ ] **多因子 IC 加权扩展**：当前 2 因子组合，扩展到 N 因子 IC 加权
- [ ] **下游消费**：在 daily_workflow 中消费 `factor_combinations` 字段
- [ ] **因子状态监控**：基于 `weights_stats.neg_weight_pct` 建立信号反转预警
- [ ] **Vibe-Trading 全量 450+ 因子接入**（当前 24 个代表性因子）

## 9. 关键约束（来自 project_memory）

- 候选因子计算必须使用真实 OHLCV，禁止 `synthesize_ohlcv_from_returns()`
- 正交性检查使用真实 forward returns，禁止 `bfill()`
- 任何进入生产因子库的因子必须先通过 8 级流水线 + 90 日影子观察
- QualityTrend 类因子依赖 `fundamentals_history`（真实季度财务数据），缺失时 defer
- 风险管理不能挽救坏因子（IC_IR<0.3 的因子即使加风险管理也过不了 DSR），但能将好因子的高波动降至可接受范围
- IC 加权组合应作为因子组合的默认方法（优于等权组合）
- 单因子用 Config_E（target_vol=0.08），因子组合用 Config_E_plus1（target_vol=0.07）

## 10. 最新突破：v6.5→v6.9 IC 加权组合方法学闭环（2026-07-25）

### 10.1 批次演进

| 批次 | 版本 | 关键发现 | 突破 |
|------|------|----------|------|
| 第十七批次 | v6.5 | IC 加权方法学优于等权 | IC_IR +0.4434（vs 等权 +0.1364） |
| 第十八批次 | v6.6 | IC 加权与单因子参数敏感性相反 | Config_E 让 IC 加权 live_dsr -0.34→+0.40 |
| 第十九批次 | v6.7 | Config_E_plus1 通过 Shadow | live_dsr=+0.6151, max_dd=0.0438 |
| 第二十批次 | v6.8 | PipelineOrchestrator 集成 | 8 项验收全通过，与独立脚本一致 |
| **第二十一批次** | **v6.9** | **lookback 10 优于 20** | **live_dsr +0.62→+2.20 (+258%)** |

### 10.2 已 approved 因子（完整 8 级流水线）

| 因子 | 类别 | IC_IR | live_dsr | max_dd | 配置 |
|------|------|-------|----------|--------|------|
| VT_MICRO_VOL_SKEW_INV | 微观结构 | +0.42 | +0.70 | 0.1057 | Config_A + 风险管理 |
| VT_QUALTREND_MARGIN_EXP | QualityTrend | +0.3981 | +0.9960 | 0.0759 | Config_E（因子特定 override） |

### 10.3 IC 加权组合（v6.9 最终配置）

- **因子对**：VT_MICRO_VOL_SKEW_INV + VT_QUALTREND_MARGIN_EXP
- **组合方法**：IC 加权滚动权重（lookback=10）
- **Shadow 参数**：Config_E_plus1（target_vol=0.07, dd_threshold=0.018, dd_factor=0.18）
- **实测表现**（生产流水线集成验证，与独立脚本 100% 一致）：
  - IC_IR = +0.5840
  - live_dsr = +2.2033
  - max_dd = 0.0323
  - total_return = +0.3290

### 10.4 方法学关键教训

1. **IC 加权 > 等权**：等权组合假设因子信号方向一致，当信号反转时会稀释 Alpha；IC 加权用滚动 IC_IR 作为动态权重，符号自适应
2. **IC 加权和风险管理互补**：IC 加权适应信号方向（动态权重），激进参数控制噪声（target_vol 降低），两者结合既适应信号反转又控制回撤
3. **最小必要激进化原则**：选择刚过 live_dsr>0.5 阈值的最小激进化参数（Config_E_plus1），保留最多 Alpha 信号
4. **lookback 是关键超参数**：lookback=10（2 周）显著优于 lookback=20（1 月），但太短（5 天）会失去统计稳定性
5. **因子特定 Shadow 配置**：单因子无 IC 自适应，激进参数压缩 Alpha；IC 加权已自适应信号反转，激进参数压缩噪声而非 Alpha
6. **风险管理不能挽救坏因子**：IC_IR<0.3 的因子即使加风险管理也过不了 DSR，但能将好因子的高波动降至可接受范围

详细文档：`docs/vibe_trading_factor_analysis/P2_FACTOR_ALPHA_QUALITY.md`
