# 代码审查明细 — risk 模块 (2026-08-24)

> 审查对象: `ms_strategy/src/risk/` (6 文件: risk_manager/risk_budgeter/stress_tester/portfolio_risk_assessor/event_monitor/__init__)
> 审查批次: 全量核心模块逐模块审查计划 (plan: quant-system-module-audit-20260824) 第 3/10 模块
> 重点: 风控阈值/除零守卫(Q2)/陈旧数据(Q4)/禁裸except(Q5)/SSL密钥(security-and-hardening)

## 0. 门禁基线

| 门禁 | 结果 |
|---|---|
| ruff 静态扫描 | ✅ All checks passed |
| bandit 安全扫描 | 2 Low (B105 误报) → 修复后 0 issue |

## 1. 缺陷清单与修复状态

| ID | 严重度 | 文件 | 缺陷 | 状态 |
|---|---|---|---|---|
| RK-2 | LOW | risk_manager.py | `RiskBudgeter = RiskBudgeter` no-op 向后兼容别名 (死代码/混淆) | ✅ 已修复(删除) |
| B105×2 | 误报 | stress_tester.py | bandit 把 `'pass': True` 误判为硬编码密码字符串 | ✅ 已修复(# nosec B105) |
| RK-1 | MEDIUM | risk_manager.py | `auto_hedge` 阈值硬编码 (beta>0.7/vix>30)，与 hedging 模块 BetaHedger 重复且配置脱节 (DRY + memory 8 教训) | ⏸ 记录(P1 结构债) |
| RK-3 | MEDIUM | risk_budgeter.py | `risk_parity_weights` Newton-Raphson 500 次硬上限不收敛时静默返回近似解 (无收敛性显式检查) | ⏸ 记录(P1) |

**汇总**: MEDIUM×2, LOW×1, 误报×2；已修复 3 项, 记录 2 项。

## 2. 修复详情

### RK-2 — 删除 no-op 别名
`risk_manager.py` 末尾 `RiskBudgeter = RiskBudgeter` 是无效的向后兼容别名（类名=导入名），删除。

### B105×2 — bandit 误报消除
`stress_tester.py` 两处 `'pass': True`（NO_DATA/BAD_DATA 场景标记）被 bandit 误判为密码字符串，加 `# nosec B105` 注释消除（明确标注非密码）。

## 3. 审查亮点 (优秀实现, 无需改)

- `risk_budgeter.kelly_weight`: L132 sigma<=0 返回 0 (Q2 除零守卫 ✅)
- `risk_budgeter.risk_parity_weights`: L203 port_var<=0 break + L207 nan_to_num + L215 加 1e-8 (Q1 np.isfinite + Q2 除零 ✅)
- `stress_tester.run_scenario`: L78 np.isfinite + L87 denom replace(0,nan) + L88 非有限兜底 (Q1+Q2 ✅)

## 4. 验证快照

| 验证 | 结果 |
|---|---|
| ruff risk | ✅ All checks passed |
| bandit risk | 0 issue (2 nosec 跳过) |
| risk 相关单测 | 21 passed |

## 5. 待办 (后续批次)

- **RK-1**: 统一 risk_manager 与 hedging 模块的对冲阈值到配置 (消除 DRY)。
- **RK-3**: risk_parity_weights 增加收敛性检查/警告 (不收敛时显式标记)。

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [代码审查明细 — 轻量子模块 (alpha/governance/macro/ml/monitoring) (2026-08-24)](code-review-lightweight-20260824.md) (相似度 28%)
- [代码审查明细 — 主链路 institutional_pipeline_runner (2026-08-24)](code-review-pipeline-20260824.md) (相似度 20%)
- [代码审查明细 — hedging 模块 (2026-08-24)](code-review-hedging-20260824.md) (相似度 19%)
- [代码审查明细 — data 模块 (2026-08-24)](code-review-data-20260824.md) (相似度 19%)
- [代码审查明细 — execution 模块 (2026-08-24)](code-review-execution-20260824.md) (相似度 18%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
