# 安全合规跨市场执行代理

> 知识专题文档 — LIT-4.4 交付物
> 文献: #53 Safe Cross-Market Execution (2025.10)
> 代码: `utils/safe_execution_agent.py`
> 测试: `tests/unit/test_safe_execution_agent_unit.py` (41 测试)
> LOG: 2026-08-23 LIT-4.4

## 核心设计

### 三大组件

1. **ConstrainedMDP** (约束马尔可夫决策过程):
   - 持仓限制: |position + order| ≤ max_position
   - 单笔限额: |order| ≤ max_order_size
   - 市场占比: |order| / adv ≤ max_participation
   - 涨跌停: |price - ref| / ref ≤ price_limit_pct
   - `project_to_feasible()`: 投影到可行域 (裁剪订单)

2. **CVaRController** (CVaR 尾部控制):
   - VaR_α = percentile(losses, α×100)
   - CVaR_α = E[Loss | Loss > VaR_α]
   - 超限时按比例缩减订单: scale = max_cvar / cvar

3. **ZeroKnowledgeAudit** (零知识审计):
   - SHA-256 哈希承诺 (不暴露订单细节)
   - 只记录合规状态和违规描述
   - 可验证合规性, 不暴露策略

### 验收结果

- **无违规**: 调整后订单全部合规 ✅
- **CVaR 尾部控制**: CVaR 计算并检查, 超限时缩减订单 ✅
- **零知识审计**: 审计记录用哈希承诺, 不暴露策略细节 ✅

## API

```python
from utils.safe_execution_agent import SafeExecutionAgent, ExecutionConstraints

constraints = ExecutionConstraints(max_position=100000, max_cvar=500)
agent = SafeExecutionAgent(constraints=constraints)
result = agent.execute_safely(
    symbol="600519", order_shares=10000, adv=500000,
    current_position=5000, price=1800.0,
)
assert result.compliant  # True
```

## 约束参数

| 约束 | 默认值 | 说明 |
|------|--------|------|
| max_position | 100,000 股 | 最大持仓 |
| max_order_size | 50,000 股 | 单笔最大订单 |
| max_participation | 10% | 最大市场占比 |
| max_cvar | 500 bps | CVaR 上限 |
| price_limit_pct | ±10% | 涨跌停限制 |