# RegimeFolio 制度感知组合优化

> **任务**: LIT-3.4 RegimeFolio 制度感知组合优化 — Sprint LIT-S3
> **文献**: #41 RegimeFolio (2025.10)
> **状态**: ✅ 已完成 (2026-08-23)

## 1. 核心设计

- **VIX 制度分类**: 4级 (低波<15 / 正常 15-20 / 高波 20-30 / 危机>30)
- **Ledoit-Wolf 收缩协方差**: 减少估计误差, 稳定化矩阵
- **制度感知权重调整**: 危机时增防御/降进攻, 低波时反之
- **最大权重约束**: 迭代裁剪, 不可行时降级均匀权重

## 2. 组件

| 组件 | 功能 |
|------|------|
| RegimeClassifier | VIX → 4级制度分类 |
| CovarianceShrinkage | Ledoit-Wolf 收缩 (δ*F + (1-δ)*S) |
| RegimeConfig | 各制度的风险厌恶/最大权重/防御进攻加成 |
| RegimeAwareAllocator | 综合分配器 (分类→收缩→优化→调整→约束) |

## 3. 验证

- 27 单元测试全绿
- 端到端: 低波动夏普 0.62, 危机时均匀防御
- Ledoit-Wolf 收缩强度 0.74 (自动估计)

## 4. 文件清单

| 文件 | 行数 | 用途 |
|------|------|------|
| `utils/regime_aware_allocator.py` | ~440 | 核心实现 |
| `tests/unit/test_regime_aware_allocator_unit.py` | ~290 | 单元测试 |

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [FinRL-X 权重中心接口架构](finrl-x-interface.md) (相似度 22%)
- [制度门控 Transformer (Adaptive Financial Transformer)](regime-gated-transformer.md) (相似度 20%)
- [对冲方案 v8.7 优化 — RegimeFolio 动态阈值 + 紧急跨级 + IV 感知 + Deep Hedging + 多智能体](hedge-v87-regime-adaptive-20260827.md) (相似度 17%)
- [年化收益校准标准 v1.0](returns-calibration-standards.md) (相似度 14%)
- [隐含波动率曲面深度对冲](iv-surface-deep-hedge.md) (相似度 14%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
