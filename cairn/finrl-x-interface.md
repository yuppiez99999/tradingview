# FinRL-X 权重中心接口架构

> **任务**: LIT-4.1 FinRL-X 权重中心接口架构 — Sprint LIT-S4
> **文献**: #49 FinRL-X (PAKDD 2026)
> **状态**: ✅ 已完成 (2026-08-23)

## 1. 核心组件

- **WeightCenter**: 权重中心 — 回测/实盘单一真相源
- **StrategyPipeline**: 可组合策略管线 — 数据→特征→信号→权重
- **BacktestLiveConsistency**: 回测=实盘一致性验证
- **LegacyAdapter**: 旧管道兼容适配器

## 2. 文件清单

| 文件 | 行数 | 用途 |
|------|------|------|
| `core/finrl_x_interface.py` | ~370 | 核心实现 |
| `tests/unit/test_finrl_x_interface_unit.py` | ~240 | 26 单元测试 |