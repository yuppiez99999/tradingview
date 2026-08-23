# 市场冲击模型 (Almgren-Chriss + 永久冲击指数衰减)

> 知识专题文档 — LIT-4.2 交付物
> 文献: #50 Realistic Market Impact Modeling (2026.03) FinRL-Meta 扩展
> 代码: `utils/market_impact_model.py` v2.0
> 测试: `tests/unit/test_market_impact_model_unit.py` (51 测试)
> LOG: 2026-08-23 LIT-4.2

## 核心设计

### 永久冲击模型对比

| 模型 | 公式 | 小单 (v→0) | 大单 (v→∞) | 适用场景 |
|------|------|-----------|-----------|----------|
| 线性 (经典 AC) | `g(v) = γ × v` | `γ × v` | `γ × v` (无限增长) | 小单/中单 |
| 指数衰减 (文献 #50) | `g(v) = γ × (1 - exp(-β × v)) / β` | `γ × v` (一阶近似) | `γ / β` (饱和) | 大单/机构单 |

### 关键参数

- `gamma = 0.314`: 永久冲击系数 (与线性模型一致)
- `permanent_decay_beta = 10.0`: 衰减速率 (β 越大饱和越快, 饱和值 γ/β 越低)
- `permanent_impact_model`: "linear" (经典 AC) / "exponential_decay" (文献 #50)

### 验收结果

- **大单 (参与度 50%)**: 永久冲击降低 **80.1%** ≥ 50% 阈值 ✅
- **小单 (参与度 0.1%)**: 指数衰减 ≈ 线性 (一阶近似) ✅
- **饱和值**: γ/β × 10000 × 0.5 = 157 bps (大单永久冲击上限)

## API

### MarketImpactModel

```python
from utils.market_impact_model import MarketImpactModel, ImpactParams

# 指数衰减模型
params = ImpactParams(permanent_impact_model="exponential_decay", permanent_decay_beta=10.0)
model = MarketImpactModel(params=params)

# 估计冲击
est = model.estimate(symbol="600519", order_shares=50000, adv=100000, decision_price=1800.0)

# 对比线性 vs 衰减
comparison = model.compare_impact_models(symbol="600519", order_shares=50000, adv=100000)

# 验证成本降低 ≥ 50%
validation = model.validate_cost_reduction(symbol="600519", large_order_shares=50000, adv=100000)
assert validation["passed"]  # True
```

## 踩坑记录

### contains: max修正覆盖衰减效果

**问题**: `estimate()` 中 `perm_impact_bps = max(perm_bps, sqrt_impact_bps * 0.4)` 会用 Square-Root 模型的冲击覆盖指数衰减效果，导致大单时衰减模型的永久冲击与线性模型相同。

**解决**: 对指数衰减模型跳过 max 修正，直接使用 `perm_bps`。`compare_impact_models()` 直接调用 `_permanent_impact_bps()` 比较纯永久冲击，不受 max 修正影响。

### contains: 参数设计一阶近似不一致

**问题**: 初始设计 `g(v) = γ_sat × (1 - exp(-β × v))`，一阶近似 = `γ_sat × β × v`，与线性 `γ × v` 不一致 (差 β 倍)。

**解决**: 改为 `g(v) = γ × (1 - exp(-β × v)) / β`，一阶近似 = `γ × v`，与经典 AC 一致。