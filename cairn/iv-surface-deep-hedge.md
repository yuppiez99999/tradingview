# 隐含波动率曲面深度对冲

> **任务**: LIT-3.5 隐含波动率曲面深度对冲（可选）— Sprint LIT-S3
> **文献**: #38 IV Surface Deep Hedging (2025.04)
> **状态**: ✅ 已完成 (2026-08-23)

## 1. 核心增强

超越 delta-gamma 对冲:
- **方差风险溢价** (VRP = IV² - RV²): 做空/做多波动率信号
- **二阶希腊字母**: vanna (∂delta/∂vol) + volga (∂vega/∂vol)
- **多工具对冲**: 最小二乘求解 vega/vanna/volga 中和
- **VRP 感知调整**: 做空波动率时放大 vanna/volga 对冲

## 2. 文件清单

| 文件 | 行数 | 用途 |
|------|------|------|
| `utils/iv_surface_deep_hedge.py` | ~330 | 核心实现 |
| `tests/unit/test_iv_surface_deep_hedge_unit.py` | ~190 | 18 单元测试 |

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [DeltaHedge 多智能体期权优化](delta-hedge-multi-agent.md) (相似度 35%)
- [Deep Hedging RL 范式集成](deep-hedging-rl.md) (相似度 27%)
- [FinRL-X 权重中心接口架构](finrl-x-interface.md) (相似度 20%)
- [对冲方案 v8.7 优化 — RegimeFolio 动态阈值 + 紧急跨级 + IV 感知 + Deep Hedging + 多智能体](hedge-v87-regime-adaptive-20260827.md) (相似度 17%)
- [v8.7 发布 — LIT-5.6 全量集成验收](v87-release.md) (相似度 14%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
