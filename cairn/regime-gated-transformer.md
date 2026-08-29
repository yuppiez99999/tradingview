# 制度门控 Transformer (Adaptive Financial Transformer)

> 知识专题文档 — LIT-5.1 交付物
> 文献: #58 Adaptive Financial Transformer (Regime-Gated) (2026.06)
> 代码: `utils/regime_gated_transformer.py`
> 测试: `tests/unit/test_regime_gated_transformer_unit.py` (27 测试)
> LOG: 2026-08-24 LIT-5.1

## 核心设计

### 三大组件

1. **FeatureSemanticMapper** (95→11 降维):
   - 11 个语义类: 趋势/动量/反转/波动率/流动性/价值/成长/质量/情绪/宏观/技术形态
   - 加权聚合: 各类特征均值 → 语义特征向量
   - 复杂度降低: 88.4% (95→11)

2. **RegimeDetector** (制度检测):
   - 4 种制度: 低波/高波/趋势/反转
   - 基于语义特征 + 外部提示 (regime_hint)
   - Softmax 归一化 → 制度概率

3. **RegimeGatedTransformer** (制度门控 Transformer):
   - 每个制度一组注意力权重 (W_q, W_k, W_v, W_ff)
   - 制度门控: gate = sigmoid(Σ prob_r × gate_weight_r)
   - 门控残差: x = x + gate × attention(x)
   - numpy 实现 (无 PyTorch 依赖)

### 验收结果

- **95→11 语义类**: 复杂度降 88.4% ≥ 10% ✅
- **制度门控**: hint=0.0→低波(79.77%), hint=1.0→高波(81.79%) ✅
- **4 种制度可达**: 低波/高波/趋势/反转 ✅

## API

```python
from utils.regime_gated_transformer import AdaptiveFinancialTransformer

model = AdaptiveFinancialTransformer()
result = model.forward(features_95, regime_hint=0.5)
print(result.predictions, result.regime, result.complexity_reduction)
```

## 11 语义类映射

| 类别 | 特征索引 | 典型特征 |
|------|---------|---------|
| 趋势 | 0-8 | MA/EMA/趋势线 |
| 动量 | 9-17 | RSI/MACD/动量 |
| 反转 | 18-26 | KDJ/威廉/反转 |
| 波动率 | 27-35 | ATR/布林/波动率 |
| 流动性 | 36-44 | 换手率/成交量 |
| 价值 | 45-53 | PE/PB/股息率 |
| 成长 | 54-62 | 营收增长/利润增长 |
| 质量 | 63-71 | ROE/ROA/资产负债率 |
| 情绪 | 72-80 | 情绪指标/新闻 |
| 宏观 | 81-89 | 利率/CPI/PMI |
| 技术形态 | 90-94 | 图形/形态 |

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [RegimeFolio 制度感知组合优化](regime-aware-allocator.md) (相似度 20%)
- [Alpha 因子体系](alpha-factor-system.md) (相似度 13%)
- [情绪因子演化：v4.0 → v4.3](sentiment-factor-evolution.md) (相似度 12%)
- [v8.7 发布 — LIT-5.6 全量集成验收](v87-release.md) (相似度 10%)
- [模型训练与生命周期](model-training.md) (相似度 9%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
