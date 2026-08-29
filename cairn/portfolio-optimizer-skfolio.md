# skfolio 统一优化后端

> **任务**: LIT-3.3 skfolio 统一优化后端 — Sprint LIT-S3
> **文献**: #42 skfolio (2025.07)
> **状态**: ✅ 已完成 (2026-08-23)
> **指针**: [LOG 2026-08-23 LIT-3.3](./LOG.md)

## 1. 功能

5 种投资组合优化策略统一接口 (scikit-learn 风格):

| 策略 | 方法 | 闭式解 |
|------|------|--------|
| MeanVariance | Markowitz | w ∝ Σ^{-1} μ / γ |
| MaxSharpe | 最大夏普 | w ∝ Σ^{-1} (μ-rf) |
| MinVariance | 最小方差 | w ∝ Σ^{-1} 1 |
| RiskParity | 风险平价 | 迭代: w*sqrt(target/RC) |
| HRP | 层次风险平价 | 聚类+递归二分 |

## 2. 架构

- **NumpyOptimizer**: numpy 核心实现 (零依赖)
- **SkfolioOptimizer**: 集成接口 (fit + get_weights + predict + compare)
- **skfolio 可选**: SKFOLIO_AVAILABLE 标志, 未安装时降级到 numpy

## 3. 关键实现

### 3.1 风险平价 (平方根更新)
```python
adjustment = sqrt(target / risk_contrib)
new_weights = weights * adjustment
```
比线性更新更稳定，收敛更快。

### 3.2 HRP (Lopez de Prado 2016)
1. 相关性距离 → 层次聚类
2. 准对角化 → 叶子顺序
3. 递归二分 → alpha 分配

### 3.3 协方差正则化
cov_reg = cov + I * 1e-8 (确保正定)

## 4. 验证结果

端到端 CLI (5 资产 x 252 天):
- MaxSharpe 夏普 1.07 (最高)
- MinVariance 风险 0.15 (最低)
- RiskParity 权重分散
- HRP 与 MinVariance 接近

## 5. 测试覆盖

- `tests/unit/test_portfolio_optimizer_skfolio_unit.py` — 28 单元测试全绿
- 覆盖: 枚举/结果/5策略/配置/集成接口/便捷函数/端到端

## 6. 文件清单

| 文件 | 行数 | 用途 |
|------|------|------|
| `utils/portfolio_optimizer_skfolio.py` | ~490 | 核心实现 |
| `tests/unit/test_portfolio_optimizer_skfolio_unit.py` | ~295 | 单元测试 |
| `ruff.toml` | (+5行) | T201/UP042/BLE001 豁免 |

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [Deep Hedging RL 范式集成](deep-hedging-rl.md) (相似度 15%)
- [FinRL-X 权重中心接口架构](finrl-x-interface.md) (相似度 13%)
- [DeltaHedge 多智能体期权优化](delta-hedge-multi-agent.md) (相似度 13%)
- [隐含波动率曲面深度对冲](iv-surface-deep-hedge.md) (相似度 12%)
- [v8.7 发布 — LIT-5.6 全量集成验收](v87-release.md) (相似度 11%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
