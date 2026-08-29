# TradingGroup 自反思机制 (Self-Reflection + Data-Synthesis + Dynamic Stops)

> **文献**: #16 TradingGroup: Self-Reflection + Data-Synthesis (2025.08, ★★★★★)
> **任务**: LIT-2.2 集成 TradingGroup 自反思机制
> **状态**: ✅ 已完成 (2026-08-23)
> **LOG 指针**: `cairn/LOG.md` → "2026-08-23 · LIT-2.2"

## 核心思想

交易代理通过自反思识别决策错误, 合成训练数据改进模型, 动态调整止盈止损。

## 三大核心组件

### 1. Self-Reflection (自反思)

对每个决策进行事后评估 (T+1 或 T+N)，识别错误类型并生成改进建议。

**5 种错误类型**:
- `SIGNAL_ERROR`: 信号方向错误
- `TIMING_ERROR`: 时机错误
- `POSITION_ERROR`: 仓位错误
- `RISK_ERROR`: 风控错误
- `NO_ERROR`: 无错误

**5 级评级**:
- `EXCELLENT` → `GOOD` → `NEUTRAL` → `POOR` → `BAD`

**评估逻辑**: `outcome_return` 是策略收益率（正值=盈利，负值=亏损），与多空方向无关。
- return >= profit_threshold → EXCELLENT
- return <= loss_threshold → SIGNAL_ERROR 或 RISK_ERROR (若 |return| > 5%)
- 中间 → NEUTRAL

### 2. Data Synthesis (数据合成)

从历史决策合成训练数据，三种样本类型：
- **正样本** (positive): 正确决策, label=1, weight=1.0
- **负样本** (negative): 错误决策, label=0, weight=1.0
- **困难样本** (hard): 边界情况 (|return| < threshold), weight=2.0

### 3. Dynamic Stop-Loss/Take-Profit (动态止盈止损)

基于市场状态动态调整：
- **ATR 倍数**: 止损 2×ATR, 止盈 3×ATR
- **时间衰减**: 持仓越久止损越紧 (time_decay = 1 - holding_days/max_days)
- **趋势强度**: 强趋势放宽止盈 (trend_factor = 1 + 0.5×|trend|)
- **最大亏损约束**: 止损距离不超过 entry_price × max_loss_pct
- **仓位调整**: 风险越高仓位越小 (position = max_position × (1 - risk_score×0.5))

## 交付物

| 文件 | 行数 | 说明 |
|------|------|------|
| `utils/trading_group_reflector.py` | ~620 | 核心实现 (3组件 + 主引擎) |
| `tests/unit/test_trading_group_reflector_unit.py` | ~555 | 50 单元测试全绿 |
| `utils/ai_coordinator.py` | +40行 | 集成方法 (get_reflector + reflect_decision + synthesize + stops) |

## AICoordinator 集成

```python
coordinator = get_ai_coordinator()
# 自反思
record = coordinator.reflect_decision(decision, outcome, market_state)
# 数据合成
samples = coordinator.synthesize_training_data()
# 动态止盈止损
stops = coordinator.compute_dynamic_stops(entry, atr, trend, days, action)
```

延迟初始化 + 可选依赖 (try/except import), 不影响现有功能。

## 测试覆盖

- ErrorType / ReflectionGrade 枚举 (2 测试)
- ReflectionRecord 数据结构 (7 测试)
- SyntheticSample / DynamicStops (4 测试)
- DataSynthesizer (7 测试: 正/负/困难/跳过/混合/摘要)
- DynamicStopLossManager (11 测试: 多空/最大亏损/时间衰减/趋势/仓位/风险/异常)
- TradingGroupReflector (15 测试: 评估/历史/合成/止盈止损/摘要/建议)
- 端到端集成 (2 测试)

## ruff.toml 豁免

```toml
# T201: CLI main() 演示入口 print 是合理的
# UP042: str+Enum 继承保留 Py3.8 兼容 (StrEnum 需 Py3.11+)
"utils/trading_group_reflector.py" = ["T201", "UP042"]
```

## 后续方向

- **LIT-2.3**: CN-Buzz2Portfolio 中国市场基准
- **实际集成**: 将 reflector 接入每日交易执行流程 (daily_trade_executor.py)
- **回测验证**: 5 数据集回测验证自反思+动态止盈止损效果

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [细粒度任务分解工作流 (Fine-Grained Task Decomposition)](fine-grained-workflow.md) (相似度 15%)
- [CN-Buzz2Portfolio 中国市场基准](cn-buzz2portfolio.md) (相似度 13%)
- [KTD-Fin 记忆控制评估基准](ktd-fin-eval.md) (相似度 11%)
- [代码质量修复批次 — 2026-08-13（审查报告 B1/B2/S1/S2/S3/S4/S5/N1）](code-quality-fix-batch-20260813.md) (相似度 11%)
- [FinGPT 系列集成 (轻量 LoRA + RLSP 训练管线)](fingpt-integration.md) (相似度 10%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
