# 代码审查明细 — 轻量子模块 (alpha/governance/macro/ml/monitoring) (2026-08-24)

> 审查对象: ms_strategy/src/{alpha,governance,macro,ml,monitoring}
> 审查批次: 全量量化逐模块审查计划 (plan: quant-system-module-audit-20260824) 第 9/10 模块
> 重点: 因子 finite/除零/模型退役/告警通道

## 0. 门禁基线

| 门禁 | 结果 |
|---|---|
| ruff 静态扫描 | ✅ All checks passed |
| drift 相关单测 (T58) | ✅ 41 passed |

## 1. 缺陷清单与修复状态

| ID | 严重度 | 文件 | 缺陷 | 状态 |
|---|---|---|---|---|
| LW-1 | MEDIUM | ml/drift_detector.py | `check_all` 的 `datetime.fromisoformat(a.timestamp)` 在时间戳格式异常时抛错, 使核心漂移检测崩溃 | ✅ 已修复(try 包裹+降级跳过) |
| LW-2 | MEDIUM | monitoring/intraday_monitor.py | `source_priority` 含 `ifind_mcp`, 但 iFinD 已按 AGENTS.md 2026-08-03 剔除, 数据源优先级过时 | ⏸ 记录(P2, memory 8 教训) |
| LW-3 | LOW | monitoring/intraday_monitor.py | `target_beta = 0.3` 硬编码, 与配置脱节 (DRY) | ⏸ 记录(P2) |
| signal_generator | 已修复 | alpha/signal_generator.py | memory 21978331 的 generate() 一维信号 bug 已在 08-12 修复, 无回归 | ✅ 无缺陷 |

**汇总**: MEDIUM×2, LOW×1；已修复 1 项, 记录 2 项。

## 2. 修复详情

### LW-1 — drift_detector check_all 崩溃防护
`check_all()` 的 `datetime.fromisoformat(a.timestamp)` 用 try 包裹, 时间戳格式异常时按"旧告警"跳过 + warning, 不再抛错崩溃。漂移检测是 `should_retrain`/`generate_report` 的核心调用路径, 防御性修复防单条坏数据拖垮检测。

## 3. 审查亮点 (优秀实现)

- `compute_psi`: L548-552 除零防护 (`max(len,1)` + `np.where==0,0.0001`) + PSI 公式正确 (memory 58293104 模型退役标准)。
- `check_feature_drift` (KS): L492 样本量守卫 (`<10` 跳过)。
- `ADWINDetector`: 概念漂移检测窗口切割逻辑正确 (memory 52796540 因子衰减)。

## 4. 验证快照

| 验证 | 结果 |
|---|---|
| ruff 轻量子 | ✅ All checks passed |
| drift 相关单测 | 41 passed (T58) |
| LW-1 修复 | fromisoformat 异常降级跳过, 不崩 |

## 5. 待办

- LW-2: intraday_monitor 数据源优先级移除 iFinD, 对齐 AGENTS.md 标准 (Wind→TDX→AKShare→新浪)。
- LW-3: target_beta 从配置读取。

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [代码审查明细 — risk 模块 (2026-08-24)](code-review-risk-20260824.md) (相似度 28%)
- [代码审查明细 — data 模块 (2026-08-24)](code-review-data-20260824.md) (相似度 21%)
- [代码审查明细 — hedging 模块 (2026-08-24)](code-review-hedging-20260824.md) (相似度 21%)
- [代码审查明细 — 主链路 institutional_pipeline_runner (2026-08-24)](code-review-pipeline-20260824.md) (相似度 21%)
- [代码审查明细 — backtest 模块 (2026-08-24)](code-review-backtest-20260824.md) (相似度 20%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
