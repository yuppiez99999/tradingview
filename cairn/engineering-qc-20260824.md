# 工程 CI/质量 (Wave 7-QC 衔接) (2026-08-24)

> 工作线 D: 工程 CI/质量
> 状态: 基线核查完成; 存量债量化记录; 渐进推进

## 0. 结论

**本轮所有改动均通过 ruff_incremental_gate (无新增违规), 未引入新技术债。**
QC-2 (mypy strict) 基线与 QC-3 (ruff 清零 + 覆盖率) 为渐进改进, 存量债已量化记录。

## 1. 质量基线核查

| 指标 | 现状 | 目标 (Wave 7-QC) |
|---|---|---|
| ruff 核心目录 (utils/execution + ms_strategy/src/execution) | **14 个存量 ANN (类型注解缺失)** | 清零 |
| mypy | docs/mypy_baseline_v9.2.txt 存在 (1967 行基线) | mypy strict (QC-2) |
| 覆盖率 | reports/coverage.xml + htmlcov 存在 | 80% (QC-3) |
| 本轮新增违规 | **0 (ruff_incremental_gate 全通过)** | 保持 |

## 2. 存量债明细 (QC-3 ruff 清零清单)

14 个 ruff 错误全部是 **ANN (Missing return type annotation)** 类, 位于:
- ms_strategy/src/execution/smart_order_router.py (多个私有方法, 如 `_check_global_slippage` L475)
- 其余在 utils/execution 目录

均为私有方法缺返回注解, 不影响运行时行为, 纯类型完善。属渐进清理项。

## 3. 本轮交付

- ✅ 所有实盘工作线改动通过 ruff_incremental_gate (0 新增违规)
- ✅ 新增回归测试 (DTE-1/EX-1/风控) 全部通过
- ⏸ 存量 14 ANN 债记录, 后续批次逐步补齐 (QC-3)

## 4. 后续 (渐进)

- QC-2: mypy strict 推进 utils/ 目录 (基于现有基线逐步开启 strict)。
- QC-3: 补齐 14 个 ANN 注解 + 覆盖率提升到 80%。
- CI: 增量门禁 (ruff_incremental) 已防新债, 存量债走 nightly 全量扫描。
