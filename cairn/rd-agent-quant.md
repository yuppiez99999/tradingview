# R&D-Agent-Quant 多智能体因子挖掘引擎 (LIT-1.1)

> **创建**: 2026-08-24（本文档补写 2026-08-26）
> **状态**: 骨架已实现 + 规则降级可用; LLM 驱动路径待接入
> **文献**: #2 R&D-Agent (NeurIPS 2025, ★★★★★)
> **实现**: `utils/alpha_factor/rd_agent_quant.py` (~305行)
> **LOG 指针**: 2026-08-24 LIT-1.1 R&D-Agent-Quant 骨架

---

## 1. 定位与 LIT-S1 内部分工

R&D-Agent-Quant 是 LIT-S1 的「因子发现」环节，与同 Sprint 其余任务构成 AI 因子挖掘管线:

| 模块 | 职责 | 文献 |
|------|------|------|
| R&D-Agent-Quant (LIT-1.1) | 多智能体协作挖掘因子 | #2 NeurIPS 2025 |
| AlphaForge (LIT-1.2) | 因子动态权重组合 | #3 AAAI 2025 |
| DeepFund (LIT-1.3) | 防泄漏评估基准 | #22 NeurIPS 2025 |
| AI-Trader (LIT-1.4) | 实时未污染基准 | #23 |
| AlphaCFG (LIT-1.5) | 语法引导因子发现 | #1 |

## 2. 四角色架构

| 角色 | 职责 | 产出 |
|------|------|------|
| Researcher 研究员 | 生成因子假设 | `FactorProposal` |
| Developer 开发者 | 实现因子表达式 | 表达式 (接表达式引擎) |
| Reviewer 评审员 | IC/IR/换手/衰减评估 | `FactorEvaluation` |
| Manager 管理员 | 协调 + 保留/淘汰 | accepted/rejected |

核心数据类: `FactorProposal`（提案）、`FactorEvaluation`（评估）、`MiningResult`（循环结果，含提案/接受/拒绝/改善率）。状态机 `FactorStatus`: PROPOSED → IMPLEMENTED → VALIDATED → ACCEPTED / REJECTED。

## 3. 核心工作流

```
run_mining_cycle()
  → _researcher_propose()     (LLM 或规则降级)
  → _reviewer_evaluate()      (门禁: IC≥0.03 / IR≥0.5 / 换手≤0.5)
  → _manager_decide()         (passed → ACCEPTED 入因子库)
  → MiningResult (提案/接受/拒绝/改善率)
```

## 4. 关键设计

- **零硬依赖**: `llm_available=False` 时降级为规则因子 (momentum_20d / reversal_5d / volume_price_divergence)
- **因子库瘦身**: `max_factors=30`, 目标从 100 因子收缩 -70% (保 IC 同时降维)
- **门禁三阈值**: IC≥0.03 / IR≥0.5 / 换手≤0.5, 不达标逐条记录 `rejection_reason`

## 5. 实现状态与已知局限 (2026-08-24)

| 环节 | 状态 |
|------|------|
| 四角色骨架 + 状态机 | ✅ 已实现 |
| 规则降级路径 | ✅ 可用 (快速自检通过) |
| Reviewer 真实回测评估 | ⚠️ 骨架: IC 硬编码 0.04, 未接 evaluator/library 真实 IC |
| LLM 驱动因子生成 | 🚧 `_researcher_llm_propose` 待实现 (置空警告降级) |

> **踩坑**: 当前自检接受率 100% (IC=0.04/IR=0.67/turnover=0.3), 是 Reviewer 硬编码所致而非真实信号——生产启用前必须接 `evaluator.py` 真实 IC 回传, 否则 Manager 门禁形同虚设。

## 6. 验证快照 (2026-08-24)

- **自检通过**: `quick_check()` → 3 因子提案全部接受 (IC=0.04, IR=0.67, turnover=0.3)
- **ruff**: `alpha_factor/**/*.py` 新增 T201 豁免

## 7. 文件清单

| 文件 | 行数 | 用途 |
|------|------|------|
| `utils/alpha_factor/rd_agent_quant.py` | 305 | 核心实现 |
| `utils/alpha_factor/evaluator.py` | (复用) | 真实因子评估（待接线） |
| `utils/alpha_factor/library.py` | (复用) | 因子库管理（待接线） |

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [AlphaCFG 语法引导因子发现 (LIT-1.5)](alpha-cfg-discovery.md) (相似度 29%)
- [AlphaForge 动态权重组合机制 (LIT-1.2)](alpha-forge-combiner.md) (相似度 29%)
- [v8.7 发布 — LIT-5.6 全量集成验收](v87-release.md) (相似度 28%)
- [FinRL-X 权重中心接口架构](finrl-x-interface.md) (相似度 16%)
- [篮子清算最小 shortfall](basket-liquidation.md) (相似度 14%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
