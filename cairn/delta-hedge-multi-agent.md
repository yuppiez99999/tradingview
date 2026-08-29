# DeltaHedge 多智能体期权优化

> **任务**: LIT-3.2 DeltaHedge 多智能体期权优化 — Sprint LIT-S3
> **文献**: #37 PACIS 2025 — Multi-Agent Delta Hedging
> **状态**: ✅ 已完成 (2026-08-23)
> **指针**: [LOG 2026-08-23 LIT-3.2](./LOG.md)

## 1. 问题背景

传统对冲用纯 Beta 加权 (期货/指数对冲, 仅 delta)。实际组合有 gamma/vega/theta 暴露，纯 delta 对冲无法覆盖。

**多智能体对冲**: 多个智能体分别对冲不同希腊字母，期权作为对冲工具，RL 优化权重分配。

## 2. 架构组件

### 2.1 GreeksCalculator (Black-Scholes)
- delta/gamma/vega/theta 计算
- put-call parity 验证通过
- 到期极限正确 (二元 delta)

### 2.2 HedgingAgent
- 每个智能体负责一种希腊字母的中和
- 选择能最有效减少暴露的对冲工具
- quantity = -exposure / instrument_greek

### 2.3 RLWeightOptimizer
- 进化策略优化各智能体权重
- 低残差智能体获得更高权重
- 权重归一化 + 扰动探索

### 2.4 MultiAgentCoordinator
- 按优先级排序: delta > gamma > vega
- 依次执行，更新组合暴露
- RL 权重分配

### 2.5 DeltaHedgeEngine
- hedge() + hedge_batch() + compare_with_beta_hedge()
- 统计 + RL 权重查询

## 3. 验证结果

端到端 CLI:
- 组合暴露: Delta=1000, Gamma=500, Vega=200
- 对冲后 Vega 完全中和 (→0.00)
- 多智能体同时减少 gamma/vega 暴露
- RL 权重自适应分配

## 4. 测试覆盖

- `tests/unit/test_delta_hedge_multi_agent_unit.py` — 37 单元测试全绿
- 覆盖: 枚举/期权工具/BS希腊字母/组合暴露/单智能体/RL优化/协调器/引擎/端到端

## 5. 关键创新

1. **超越纯 Beta 加权**: 同时对冲 delta+gamma+vega
2. **期权作为对冲工具**: 不仅仅是期货/指数
3. **多智能体协调**: 按优先级避免冲突
4. **RL 自适应权重**: 市场制度感知

## 6. 文件清单

| 文件 | 行数 | 用途 |
|------|------|------|
| `utils/delta_hedge_multi_agent.py` | ~440 | 核心实现 |
| `tests/unit/test_delta_hedge_multi_agent_unit.py` | ~375 | 单元测试 |
| `ruff.toml` | (+4行) | T201/UP042 豁免 |

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [隐含波动率曲面深度对冲](iv-surface-deep-hedge.md) (相似度 35%)
- [Deep Hedging RL 范式集成](deep-hedging-rl.md) (相似度 27%)
- [对冲方案 v8.7 优化 — RegimeFolio 动态阈值 + 紧急跨级 + IV 感知 + Deep Hedging + 多智能体](hedge-v87-regime-adaptive-20260827.md) (相似度 17%)
- [AlphaCFG 语法引导因子发现 (LIT-1.5)](alpha-cfg-discovery.md) (相似度 16%)
- [v8.7 发布 — LIT-5.6 全量集成验收](v87-release.md) (相似度 14%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
