# AlphaForge 动态权重组合机制 (LIT-1.2)

> **创建**: 2026-08-24（本文档补写 2026-08-26）
> **状态**: 已实现 + 自检通过; 待接真实 IC 流生产启用
> **文献**: #3 AlphaForge (AAAI 2025, ★★★★★)
> **实现**: `utils/alpha_factor/alpha_forge_combiner.py` (~336行)
> **LOG 指针**: 2026-08-24 LIT-1.2 AlphaForge 动态权重组合机制

---

## 1. 定位

在 LIT-1.1 挖掘出的因子基础上，AlphaForge 根据因子**滚动历史表现**动态分配组合权重，替代固定等权。目标 IC 提升 ≥5%。与 LIT-1.1/1.5 的差异见 `cairn/alpha-cfg-discovery.md` §1 对比表。

## 2. 四阶段权重管线

```
compute_dynamic_weights()
  → 滚动窗口 IC/IR (window=60 双端队列)
  → score = ic_mean × ir_mean × decay_factor
  → Softmax 温度加权 (temperature=1.0)
  → 约束 (max≤0.3 且 ≤1/n×3, min≥0.01, 归一化)
  → 换手率限制 (turnover>0.3 时新旧混合)
  → WeightedIC 期望
```

## 3. 关键参数

| 参数 | 默认 | 说明 |
|------|------|------|
| window | 60 | 滚动 IC/IR 窗口 |
| temperature | 1.0 | Softmax 温度 (越高越趋向等权) |
| decay_penalty | 0.5 | IC 衰减惩罚系数 |
| max_weight / min_weight | 0.3 / 0.01 | 单因子权重边界 |
| turnover_limit | 0.3 | 单次调整换手上限 |

## 4. 衰减评分 (decay_score)

`近期10期 IC 均值 / 远期 IC 均值`, clamp 到 [0.1, 2.0]:
- 衰减 → ratio<1 → 降权
- 增强 → ratio>1 → 加权
- 样本<10 期 → 返回 1.0 (不判衰减)

## 5. 零行为变更设计

- 旧固定权重接口**保留**, 新能力通过 `enable_dynamic=True` 显式启用
- 未注册因子时 `compute_dynamic_weights` 返回 `mode="empty"` (不报错)
- 权重调整全程记录 `WeightAdjustment` (旧权重/新权重/IC/IR/衰减分/原因)

## 6. 验证快照 (2026-08-24)

- **自检通过**: 3 因子等权 0.333 / IC_expected=0.034 / turnover=0.0 (首轮无历史)
- 30 期随机 IC 注入后权重稳定收敛, 换手约束生效

## 7. 已知局限

- `update_performance` 的 IC 尚需接线真实数据源 (当前快速自检用 `np.random.seed(42)` 模拟)
- 生产启用前需验证 `decay_score` 与 `turnover_limit` 在真实因子上不导致权重震荡

## 8. 文件清单

| 文件 | 行数 | 用途 |
|------|------|------|
| `utils/alpha_factor/alpha_forge_combiner.py` | 336 | 核心实现 |
| `utils/alpha_factor/rd_agent_quant.py` | (复用) | 上游因子产出 |
| `utils/alpha_factor/evaluator.py` | (复用) | 真实 IC 来源（待接） |

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [R&D-Agent-Quant 多智能体因子挖掘引擎 (LIT-1.1)](rd-agent-quant.md) (相似度 29%)
- [AlphaCFG 语法引导因子发现 (LIT-1.5)](alpha-cfg-discovery.md) (相似度 24%)
- [FinRL-X 权重中心接口架构](finrl-x-interface.md) (相似度 23%)
- [v8.7 发布 — LIT-5.6 全量集成验收](v87-release.md) (相似度 19%)
- [RAG + RL 自适应情感分析 — LIT-5.4](rag-rl-sentiment.md) (相似度 11%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
