# Deep Hedging RL 范式集成

> **任务**: LIT-3.1 Deep Hedging RL 范式集成 — Sprint LIT-S3
> **文献**: #36 Deep Hedging (Buehler et al. 2018/2025.12)
> **状态**: ✅ 已完成 (2026-08-23)
> **指针**: [LOG 2026-08-23 LIT-3.1](./LOG.md)

## 1. 问题背景

传统对冲用解析 delta-gamma (Black-Scholes)，假设连续交易无摩擦。真实市场存在交易成本、跳空、流动性约束，解析对冲次优。

**Deep Hedging** (Buehler et al. 2018) 用 RL 直接优化对冲策略的风险指标，无需 BS 假设。

## 2. 核心范式

```
Actor: 状态 [log_moneyness, time_to_mat, prev_hedge] → 对冲头寸 [-1, 1]
优化: 进化策略 (ES) — 无需梯度, 兼容不可导风险指标 (CVaR)
目标: min CVaR(对冲误差) 或 max E[U(对冲后财富)]
```

## 3. 架构组件

### 3.1 VolatilitySurface
- SVI 简化版: σ(k,τ) = σ_atm + skew*k + kurt*k² + term_slope*τ
- k = log(K/F) (log-moneyness), τ = 到期时间
- 下限保护: max(iv, 0.01)

### 3.2 MarketSimulator
- GBM 路径模拟 (drift + diffusion * dW)
- 含跳空模拟 (jump_prob + jump_size)
- 可复现 (seed)

### 3.3 HedgingActor
- numpy 神经网络: 3 → hidden → hidden → 1 (tanh)
- Xavier 初始化
- perturb() 参数扰动 (ES 用)

### 3.4 RiskMeasure
- CVaR (Conditional Value at Risk)
- VaR (Value at Risk)
- MSE (均方误差)
- 指数效用: U(w) = -exp(-γw)/γ
- MaxDD (最大回撤)

### 3.5 DeepHedgingTrainer
- 进化策略 (ES) 优化:
  1. 采样 n_perturbations 个参数扰动
  2. 评估每个扰动的风险指标
  3. 加权更新 (低风险 = 高权重, softmax 归一化)
- 噪声衰减: noise_scale = 0.01 * (1 - ep/episodes)

### 3.6 DeepHedgingEngine
- 集成接口: train() + hedge() + get_iv_surface()
- 可选 IV 面开关

## 4. 验证结果

端到端 CLI (50 episodes, 30 steps):
- **CVaR 改善 20.4%** (10.52 → 8.37)
- Std PnL 改善 (3.02 → 2.18)
- IV 面负偏度正确 (OTM put IV > ATM IV)

## 5. 测试覆盖

- `tests/unit/test_deep_hedging_rl_unit.py` — 40 单元测试全绿
- 覆盖: 配置/IV面/市场模拟/Actor/风险指标/训练器/引擎/端到端

## 6. 已知限制

- numpy 实现 (无 GPU 加速)，大量路径时较慢
- ES 优化效率低于梯度方法，但兼容不可导指标
- IV 面为简化 SVI，生产可用 SSVI 或真实 SVI
- 仅支持 put 期权 payoff (可扩展 call)

## 7. 集成点

- `utils/hedge_engine.py`: 可调用 DeepHedgingEngine 替代/增强解析 delta 对冲
- LIT-3.2 (DeltaHedge 多智能体) 将基于此扩展
- LIT-3.5 (IV 曲面深度对冲) 将增强 IV 面模块

## 8. 文件清单

| 文件 | 行数 | 用途 |
|------|------|------|
| `utils/deep_hedging_rl.py` | ~551 | 核心实现 |
| `tests/unit/test_deep_hedging_rl_unit.py` | ~398 | 单元测试 |
| `ruff.toml` | (+4行) | T201/N806 豁免 |

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [DeltaHedge 多智能体期权优化](delta-hedge-multi-agent.md) (相似度 27%)
- [隐含波动率曲面深度对冲](iv-surface-deep-hedge.md) (相似度 27%)
- [skfolio 统一优化后端](portfolio-optimizer-skfolio.md) (相似度 15%)
- [对冲方案 v8.7 优化 — RegimeFolio 动态阈值 + 紧急跨级 + IV 感知 + Deep Hedging + 多智能体](hedge-v87-regime-adaptive-20260827.md) (相似度 13%)
- [v8.7 发布 — LIT-5.6 全量集成验收](v87-release.md) (相似度 13%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
