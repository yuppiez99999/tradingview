# 篮子清算最小 shortfall

> 知识专题文档 — LIT-4.5 交付物 (可选)
> 文献: #55 Minimal Shortfall Basket Liquidation (2025.02)
> 代码: `utils/basket_liquidation.py`
> 测试: `tests/unit/test_basket_liquidation_unit.py` (18 测试)
> LOG: 2026-08-23 LIT-4.5

## 核心设计

### 因子模型降维 (解决维度灾难)

N 只股票的清算需要 N×N 协方差矩阵。因子模型将其降维:

```
Σ = BB^T + D
```
- B: N×K 因子载荷矩阵 (K << N)
- D: N×N 对角特异性方差矩阵
- 有效维度: K + 1 (远小于 N)

### 联合清算

- 各股票清算轨迹: shares × (1 - t/T)
- 相关性调整: 高相关股票联合清算有额外冲击
- vs 朴素清算: 独立清算 (无相关性调整) 的对比

## API

```python
from utils.basket_liquidation import BasketLiquidator
import numpy as np

liquidator = BasketLiquidator()
corr = np.array([[1.0, 0.5], [0.5, 1.0]])
result = liquidator.liquidate(
    symbols=["A", "B"], shares=[10000, 5000],
    adv=[500000, 1000000], corr_matrix=corr,
)
print(result.total_shortfall, result.vs_naive_improvement)
```

## Sprint LIT-S4 总结

| 任务 | 交付物 | 测试数 | 状态 |
|------|--------|--------|------|
| LIT-4.1 | core/finrl_x_interface.py | 26 | ✅ |
| LIT-4.2 | utils/market_impact_model.py (增强) | 51 | ✅ |
| LIT-4.3 | utils/execution/tt_dac_ps.py | 36 | ✅ |
| LIT-4.4 | utils/safe_execution_agent.py | 41 | ✅ |
| LIT-4.5 | utils/basket_liquidation.py | 18 | ✅ |
| **合计** | **5 模块** | **172 测试** | **全部 ✅** |

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [v8.7 发布 — LIT-5.6 全量集成验收](v87-release.md) (相似度 36%)
- [市场冲击模型 (Almgren-Chriss + 永久冲击指数衰减)](market-impact-model.md) (相似度 21%)
- [TT-DAC-PS 最优执行算法](tt-dac-ps.md) (相似度 17%)
- [AlphaCFG 语法引导因子发现 (LIT-1.5)](alpha-cfg-discovery.md) (相似度 16%)
- [R&D-Agent-Quant 多智能体因子挖掘引擎 (LIT-1.1)](rd-agent-quant.md) (相似度 14%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
