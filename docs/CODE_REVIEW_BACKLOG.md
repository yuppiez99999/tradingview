# 存量代码审查治理看板 (CODE_REVIEW_BACKLOG)

> 配套文档：`docs/CODE_REVIEW_PROCESS.md` §5 存量治理、`docs/CODE_REVIEW_PLAN.md` Task 3.2
> 数据来源：`python scripts/quality_snapshot.py --json`（每周自动生成，贴入下方表格）
> 维护：流程 owner；owner 字段为负责修复人；due 为约定完成日期

## 1. 量化指标趋势（双周更新）

| 日期 | P0 裸 print 数 | 最大文件 (行) | print 残留总数 | 覆盖率 | 备注 |
|------|----------------|---------------|----------------|--------|------|
| 2026-08-08 | 0 (门禁拦截) | 6226 (daily_workflow.py) | 90 (P0区) | 未接入 | 基线建立；量化lint升strict |

## 2. 存量问题清单

| ID | 文件/区域 | 问题类型 | 严重度 | owner | due | 状态 |
|----|-----------|----------|--------|-------|-----|------|
| B1 | v8.3_institutional/daily_workflow.py | 巨型文件 (>6000行) | 中 | 模块owner | 2026-08-30 | 待修复 |
| B2 | 全仓 pytest --cov 未接入 CI | 覆盖率缺口 (G7) | 中 | CI维护者 | 2026-08-15 | 待修复 |
| B3 | G1 QMT 真实下单未接线 | 执行闭环 (C1, 用户决策本期限不做) | 低 | — | — | 豁免(附理由) |

状态取值：`待修复` / `修复中` / `已修复` / `豁免(附理由)`

## 3. 80/20 排序（按修正成本×风险）

> 每周由 `quality_snapshot.py` 输出的"最大文件 Top10"与"P0 违规"合并后，按风险排序填入。
> 当前 Top 风险：B1 巨型文件（维护成本高但无直接风险） < B2 覆盖率缺口（影响回归防护）。

## 4. 豁免登记

| 文件 | 豁免理由 | 审批人 | 日期 |
|------|----------|--------|------|
| G1 QMT 真实下单 | 用户决策本期不接真实券商, 保持 dry_run | 用户 | 2026-08-08 |
| ms_strategy/src/alpha/qlib_signal_adapter.py:688 | `target = close.pct_change().shift(-1)` 为 ML 训练标签, 非特征泄漏 | 流程owner | 2026-08-08 |
| ms_strategy/src/alpha/signal_fusion.py:87 | docstring 示例说明, 非实际代码 | 流程owner | 2026-08-08 |
