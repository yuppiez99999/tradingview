# AlphaCFG 语法引导因子发现 (LIT-1.5)

> **创建**: 2026-08-23
> **状态**: 已实现 + 27 单元测试全绿 + 端到端验证通过 (top-10 因子, 最佳 IC=+0.0978)
> **文献**: #1 AlphaCFG: Grammar-Guided Alpha Discovery (2026.01, ★★★★★)
> **实现**: `utils/alpha_factor/alpha_cfg.py` (~370行) + `tests/unit/test_alpha_cfg_unit.py` (~230行)
> **LOG 指针**: 2026-08-23 LIT-1.5 AlphaCFG 语法引导因子发现

---

## 1. 与 LIT-1.1/1.2 的区别

| 维度 | R&D-Agent-Quant (LIT-1.1) | AlphaForge (LIT-1.2) | AlphaCFG (LIT-1.5) |
|------|--------------------------|---------------------|-------------------|
| 方法 | LLM 驱动多智能体 | 动态权重组合 | CFG + MCTS 搜索 |
| 输入 | 因子挖掘任务 | 已有因子 IC/IR | 语法空间 + 评估器 |
| 输出 | 新因子表达式 | 组合权重 | 新因子表达式 |
| 搜索空间 | LLM 生成 | 权重空间 | CFG 语法空间 |
| 约束 | LLM prompt | 换手率约束 | CFG 语法约束 |

## 2. 核心架构

```
utils/alpha_factor/alpha_cfg.py
├── CFGGrammar        — 上下文无关文法 (递归下降生成)
├── FactorEvaluator   — IC/IR 评估 (合成数据模拟)
├── MCTSNode          — 蒙特卡洛树搜索节点 (UCB1)
├── MCTSSearcher      — MCTS 搜索器 (选择/扩展/模拟/回传)
├── AlphaCFGDiscoverer — 主发现器
└── main()            — CLI 入口
```

## 3. CFG 文法规则

```
alpha  -> expr
expr   -> binary | unary | ts_expr | cs_expr | terminal
binary -> expr op expr       (op: + - * /)
unary  -> func(expr)         (func: rank scale sign abs log neg)
ts     -> ts_func(expr, win) (ts_func: ts_mean ts_std ts_rank ts_delta ts_decay)
cs     -> cs_func(expr)      (cs_func: cs_rank cs_zscore)
term   -> close open high low volume returns vwap
window -> 5 10 20 60 120
```

## 4. MCTS 四步循环

1. **选择 (Selection)**: UCB1 = avg_reward + C * sqrt(2 * ln(parent_visits) / visits)
2. **扩展 (Expansion)**: 从 CFG 规则生成新子节点
3. **模拟 (Simulation)**: 返回完整表达式
4. **回传 (Backpropagation)**: |IC| 作为奖励回传到根节点

## 5. 验收结果 (2026-08-23)

### 5.1 单元测试
- **27 测试全绿** (2.85s)
- 覆盖: CFGGrammar (6) + FactorEvaluator (5) + MCTSNode (6) + MCTSSearcher (4) + AlphaCFGDiscoverer (6)

### 5.2 端到端验证
- **200 次迭代**, 发现 top-10 因子
- **最佳因子**: IC=+0.0978, IR=+0.5174
  `cs_zscore(ts_std(ts_std(ts_delta(open, 20), 60), 20))`
- **因子多样性**: 包含时序/截面/二元运算组合

## 6. 文件清单

| 文件 | 行数 | 用途 |
|------|------|------|
| `utils/alpha_factor/alpha_cfg.py` | 370 | 核心实现 |
| `tests/unit/test_alpha_cfg_unit.py` | 230 | 单元测试 (27) |

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [R&D-Agent-Quant 多智能体因子挖掘引擎 (LIT-1.1)](rd-agent-quant.md) (相似度 29%)
- [v8.7 发布 — LIT-5.6 全量集成验收](v87-release.md) (相似度 27%)
- [AlphaForge 动态权重组合机制 (LIT-1.2)](alpha-forge-combiner.md) (相似度 24%)
- [FinRL-X 权重中心接口架构](finrl-x-interface.md) (相似度 23%)
- [DeltaHedge 多智能体期权优化](delta-hedge-multi-agent.md) (相似度 16%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
