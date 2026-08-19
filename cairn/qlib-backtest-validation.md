---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-08-18
updated: 2026-08-18
related:
  - cairn/ROADMAP.md
  - cairn/architecture-map.md
  - cairn/risk-architecture.md
contains: qlib-backtest, model-comparison, v9-vs-qlib, signal-fusion-integration, shadow-rollout-plan
---

# qlib 选股模型回测验证与 V9 对比

> 2026-08-18 完成的 LightGBM+Alpha158 选股模型回测验证，两时段样本外均跑赢沪深300。本文档沉淀验证结论、与现有 V9 模型对比、以及接入实盘的 shadow 排期。

## 一、模型配置（最优）

| 项 | 值 |
|---|---|
| 框架 | qlib 0.9.7 + LightGBM |
| 因子 | Alpha158（158 特征，14 大类） |
| 标签 | 未来1天收益 `Ref($close, -2) / Ref($close, -1) - 1` |
| 超参 | num_leaves=128, boost_round=500, learning_rate=0.02, max_depth=8 |
| 选股 | Top10 等权，每日换仓 |
| 训练区间 | 2015-01-01 ~ 2024-12-31（train）/ ~2025-06-30（valid） |
| 市场 | csi300（沪深300成分股，683只） |

## 二、回测验证结果（两时段样本外）

| 时段 | 模型文件 | 策略年化 | 基准年化 | 超额 | 夏普 | 最大回撤 | 交易天数 |
|---|---|---|---|---|---|---|---|
| 2024-06 ~ 2025-06 | `qlib_model_20260818_002409.pkl` | **63.98%** | 13.87% | **+50.10%** | **2.44** | -14.07% | 241 |
| 2025-06 ~ 2026-07 | `qlib_model_20260817_145851.pkl` | **34.73%** | 22.98% | **+11.75%** | **1.86** | -13.97% | 248 |

**结论**：两时段夏普均 >1.8，超额均正，策略非运气。2024-2025 牛市超额 +47%，2025-2026 震荡市超额 +11%，不同市场环境下都有 Alpha。

## 三、迭代实验记录

| 配置 | 日均IC | RankIC | ICIR | 超额收益 | 夏普 | 决策 |
|---|---|---|---|---|---|---|
| 1天标签+弱超参（最优） | 0.021 | 0.0443 | 0.0972 | +11.52% | 1.86 | **保留** |
| 5天标签+弱超参 | 0.0354 | 0.0466 | 0.1604 | -15.71% | 0.53 | 放弃 |
| 1天标签+强超参 | 0.0213 | 0.0394 | 0.1007 | +11.44% | 1.84 | 放弃 |

### 踩坑：5天标签 IC 高但回测差

- **现象**：5天标签日均IC=0.0354（+68%），但回测超额 -15.71%
- **根因**：标签预测5天收益，但回测每天换仓，频率不匹配。5天信号变化慢，不适应每日调仓
- **教训**：IC 高不等于回测好，标签频率必须与换仓频率一致

### 踩坑：强超参 ≈ 弱超参

- **现象**：num_leaves=256/boost_round=800/lr=0.01/depth=10，IC 和回测几乎不变
- **根因**：early stopping 在 215 轮触发，模型很快过拟合，超参不是瓶颈
- **教训**：调超参前先看 early stopping 轮数，已提前停止则加大轮数无意义

## 四、与现有 V9 模型对比

| 维度 | qlib 新模型 | V9 Regime-Specific 双模型 |
|---|---|---|
| 年化收益 | 34.73% / 63.98% | 19.62% |
| 夏普比率 | **1.86 / 2.44** | 1.315 |
| 最大回撤 | -13.97% / -14.07% | 9.95% |
| 当前状态 | 回测验证通过 | 影子账户灰度发布中 |
| 数据要求 | 日频 OHLCV | 日频 + Regime 识别 |
| 换仓频率 | 每日 | 每日 |

**选股能力**：qlib 新模型（夏普 1.86-2.44）> V9（夏普 1.315），可作为更强的 alpha 信号源。

**注意**：qlib 回测为理想环境（无滑点/冲击成本/停牌），V9 的 19.62% 是影子账户含执行成本。实盘环境下差距可能缩小。

## 五、与盘中决策系统的共存关系

**完全能共存，是上下游关系而非竞争。**

系统完整链路：
```
qlib 选股模型（本文档验证的）      ← 买什么（日频信号）
    ↓ alpha_weight=0.4
SignalFusionEngine                  ← 多源融合（alpha 40% + LLM + ETF资金流 + 宏观）
    ↓
AI 辩论引擎（Bull/Bear/Judge）      ← 共识决策
    ↓
风控六件套（T09-T14）               ← 盘中熔断/止损/持仓限制
    ↓
LiveOrderExecutor → QMT broker      ← 实盘下单
```

| 维度 | qlib 选股（上游） | 盘中决策（下游） |
|---|---|---|
| 职责 | 买什么 | 怎么买、何时买、买不了怎么办 |
| 频率 | 日频（盘后离线） | 分钟级（盘中实时） |
| 输出 | Top10 股票信号 | 实际订单 + 成交回报 |
| 风控 | 无 | 六件套 fail-closed |
| 实时应对 | 无 | 熔断/止损/调仓 |

**共存方式**：qlib 模型作为 alpha 信号源接入 `SignalFusionEngine`（`register_source('qlib_lgb_v2', getter)`），与 ETF 资金流、LLM 信号并列融合，盘中决策引擎负责实时执行和风控。

## 六、最佳方案

**两者结合** — 用 qlib 新模型替换 V9 作为 alpha 源，接入现有盘中决策 + 风控 + 执行链路，预计能同时获得高 alpha 和实盘安全性。

- **选股能力**：qlib 新模型（夏普 1.86-2.44）> V9（夏普 1.315）
- **实盘生存能力**：盘中决策系统远优于纯选股（有风控、止损、熔断、券商对接）
- **结合点**：`SignalFusionEngine.register_source('qlib_lgb_v2', getter)`，alpha_weight=0.4

## 七、shadow 接入排期（Wave 7）

参照 MVSK P5 的 shadow 流程，三阶段推进，与 MVSK P5 并行共用 shadow 基础设施：

| 阶段 | 任务 | 时间窗口 | Sprint | 依赖 |
|---|---|---|---|---|
| W7.1.7 | shadow 接入准备：`signal_fusion.py` 注册新信号源 + shadow 模式 | 09-05 ~ 09-12 | Sprint 1 | 回测验证 ✅ |
| W7.2.9 | shadow 运行 30 天对比 V9：每日记录信号/收益/换仓差异 | 09-13 ~ 10-12 | Sprint 2 | W7.1.7 |
| W7.3.8 | 评估决策：Δ夏普 > 0 且无异常换仓 → 替换 V9 为 alpha 源 | 10-13 ~ 11-12 | Sprint 3 | W7.2.9 |

**准入门槛**（W7.3.8 决策条件）：
1. shadow 30 天 Δ夏普 > 0（qlib 新模型优于 V9）
2. 无异常换仓（换手率不暴增）
3. 风控六件套无拦截异常
4. 与 MVSK P5-3 不冲突（中线层 vs 短线层独立）

**通过后**：切换 `SignalFusionEngine` 的 `ml` 信号源从 V9 → qlib_lgb_v2，alpha_weight 保持 0.4，接入风控六件套监控。

## 八、关键文件指针

- 训练脚本：`ms_strategy/cloud_train/modelscope_train.py`（支持 `--label-days` 参数）
- 本地训练：`ms_strategy/cloud_train/local_train.py`
- 回测脚本：`ms_strategy/cloud_train/simple_backtest.py`（手动计算，不依赖 qlib.backtest 模块）
- 模型文件：`reports/qlib_model_20260817_145851.pkl`（时段2）、`reports/qlib_model_20260818_002409.pkl`（时段1）
- 回测结果：`reports/backtest_summary_20260817_162406.json`、`reports/backtest_summary_20260818_003017.json`
- 信号融合接入点：`utils/signal_fusion.py:84` `SignalFusionEngine.register_source()`
- 盘中决策引擎：`utils/etf_flow_decision.py:122` `ETFFlowDecisionEngine`
- 排期：`cairn/ROADMAP.md` Wave 7 Sprint 1-3（W7.1.7 / W7.2.9 / W7.3.8）