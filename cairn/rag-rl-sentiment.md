# RAG + RL 自适应情感分析 — LIT-5.4

> 文献: #69 CODS 2025 — RAG + RL for Adaptive Sentiment
> 代码: `nlp/rag_rl_sentiment.py` | 测试: `tests/unit/test_rag_rl_sentiment_unit.py`
> LOG 指针: 2026-08-24 · LIT-5.4

## 核心架构

```
AdaptiveSentimentHub
├── RAGRetriever (检索增强生成)
│   ├── TF-IDF 相似度检索
│   └── 关键词命中加成
├── PPOSentimentTuner (PPO 强化学习)
│   ├── 策略: w1×规则 + w2×RAG + w3×历史
│   ├── 梯度: advantage × scores_i (各路差异化)
│   └── 裁剪: clip(ratio, 1-ε, 1+ε)
└── 三路融合分析
    ├── 规则引擎 (关键词命中)
    ├── RAG 增强 (检索新闻情感)
    └── 历史分数 (指数加权)
```

## RAG 检索

- **分词**: 按字符 (适用于中文)
- **向量**: TF-IDF 归一化
- **相似度**: 余弦相似度 + 关键词命中加成 (0.1/字符)
- **检索**: top_k 排序返回

## PPO 强化学习

### 策略
```
sentiment = tanh(w · scores)
scores = [rule_score, rag_score, history_score]
```

### 奖励
```
reward = direction_reward + magnitude_reward
direction_reward = pred_direction × actual_direction  (方向一致=+1)
magnitude_reward = |pred| × |actual| × 0.1  (幅度匹配, 权重低)
```

### 更新 (PPO 裁剪)
```
advantage = reward - mean_reward
grad_i = advantage × scores_i  (各路分数差异化)
ratio = weights / old_weights
clipped_ratio = clip(ratio, 1-ε, 1+ε)
update = lr × grad × clipped_ratio
weights = normalize(weights + update)
```

**关键设计**: 梯度 `advantage × scores_i` 让不同权重有不同更新量 (取决于对应分数)，避免均匀更新。

## 自适应机制

1. **在线学习**: 每次市场反馈后 PPO 更新权重
2. **历史记忆**: 指数加权最近10次预测 (衰减系数0.1)
3. **准确率监控**: 方向准确率 (预测方向 vs 实际方向)

## 验收结果

| 标准 | 状态 | 验证 |
|------|------|------|
| RAG 检索增强 | ✅ | 检索到相关新闻上下文 |
| PPO 自适应 | ✅ | 权重从均匀 0.333 调整到 0.340 |
| 准确率提升 | ✅ | 3次反馈后 33% → 100% |

## CLI 演示

```
'半导体板块利好': 情感=0.322, 置信=1.000
'某公司暴跌': 情感=-0.421, 置信=1.000
PPO 更新后权重: [0.340, 0.330, 0.330] (规则路权重增大)
准确率: 33.3% → 66.7% → 100.0%
```

## 踩坑记录

1. **PPO 梯度均匀问题**: 最初 `grad = advantage × clipped_ratio` 对所有权重相同，导致更新后仍均匀。修复: `grad_i = advantage × scores_i` (各路分数差异化)。
2. **奖励抵消问题**: `magnitude_reward` 权重100太大，抵消了方向惩罚。修复: 降到0.1。
3. **PPO 收敛**: 权重收敛在 0.340 (非持续增大)，因为 advantage 随历史均值趋近当前奖励而减小。这是正常的 PPO 行为。

## 后续依赖

- LIT-5.5 排序损失评估 (依赖 LIT-5.1)
- LIT-5.6 全量集成验收 (依赖全部)

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [v8.7 发布 — LIT-5.6 全量集成验收](v87-release.md) (相似度 24%)
- [FinMultiTime 多模态基准数据 — LIT-5.3](finmultitime-benchmark.md) (相似度 23%)
- [分数阶差分 (Fractional Differencing) — LIT-5.2](fractional-differencing.md) (相似度 19%)
- [排序损失函数系统评估 — LIT-5.5](ranking-loss-eval.md) (相似度 19%)
- [FinRL-X 权重中心接口架构](finrl-x-interface.md) (相似度 11%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
