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