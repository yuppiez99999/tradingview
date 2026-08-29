# 代码审查明细 — 量化策略系统_统一入口 (2026-08-24)

> 审查对象: `量化策略系统_统一入口_v8.6.py` (约1890行, 36 个 CLI 模式)
> 审查批次: 全量量化逐模块审查计划 (plan: quant-system-module-audit-20260824) 第 8/10 模块
> 审查工具: code-explorer subagent
> 重点: 实盘保护/模式路由/命令注入/硬编码密钥

## 0. 门禁基线

| 门禁 | 结果 |
|---|---|
| ruff 静态扫描 | ✅ All checks passed |

## 1. 缺陷清单与修复状态

| ID | 严重度 | 文件 | 缺陷 | 状态 |
|---|---|---|---|---|
| UE-2 | HIGH | 统一入口 L104-127 | gemma 预解析 `subprocess.run` 无捕获 + 无条件 `sys.exit(0)` → 子进程失败也假成功 | ✅ 已修复(透传 returncode) |
| UE-3 | HIGH | 统一入口 L1488 | 压力测试权重 `prices.get(code,1)` 缺行情标的使用硬编码占位价 1 → 权重严重失真 | ✅ 已修复(跳过缺价标的+告警) |
| UE-1 | CRITICAL | 统一入口 L801/L1610/L1646 | `--live` 默认真实自动交易无门控; `--hedge-execute`/`--rebalance-execute` 的 dry_run 默认 False (当前为模拟撮合无真钱风险, 接 QMT 后危险) | ⏸ 记录(P1 架构: 加统一实盘门控) |
| UE-4 | HIGH | 统一入口 | `--gemma` 顶层预解析与 MODES 双重实现, MODES 分支恒不可达 (死代码) | ⏸ 记录(P2) |
| UE-5 | MEDIUM | 统一入口 | `run_ml_signal_mode` 抛 NotImplementedError, `--ml-enhanced` 回退时调用它 → 必崩溃而非降级 | ⏸ 记录(P1) |
| UE-6 | MEDIUM | 统一入口 | 回测资金硬编码 100 万, 与文档 500 万不符 | ⏸ 记录(P2) |
| UE-7 | MEDIUM | 统一入口 | 21+ deprecated stub 与 MODES 重复; `--daily` 三阶段工作流实际是废桩 | ⏸ 记录(P2) |
| 密钥检查 | 无 | 统一入口 | 无硬编码 key/token (密钥走 .env) | ✅ 安全 |

**汇总**: CRITICAL×1, HIGH×3, MEDIUM×3；已修复 2 项, 记录 5 项。

## 2. 修复详情

### UE-2 — gemma 假成功
`subprocess.run(gemma_args)` 后 `sys.exit(proc.returncode)` 透传子进程退出码, 不再无条件 `exit(0)` 假成功。

### UE-3 — 压力测试权重缺价跳过
`run_stress_test` 权重计算: 缺行情标的不再按占位价 1, 而是 `missing` 告警 + `valid_codes` 过滤; 全部缺价则 `return` (不产出失真权重)。

## 3. 验证快照

| 验证 | 结果 |
|---|---|
| UE-3 缺价 000001 跳过 | valid_codes=["600519"] |
| UE-3 全部缺价 | 返回 None (不产出失真权重) |
| UE-2 子进程成功/失败 | 退出码 0/1 透传 |
| 回归测试 | 4 passed |
| ruff_incremental_gate | ✅ 3 文件无新增违规 |

## 4. 审查结论 (code-explorer)

- **密钥管理良好**: 无硬编码 key/token (memory 安全要求满足)。
- **命令注入安全**: subprocess 用参数列表 (无 shell=True), 无注入。
- **模式路由安全**: `mode_group` required=True, 无"未选模式落入危险默认"漏分支。
- **核心风险 UE-1**: 实盘入口 (`--live`/`--hedge-execute`/`--rebalance-execute`) 默认真实执行无统一门控。当前撮合为 SimulatedBroker 模拟, 无真钱风险; 但接 QMT 后须加 TRADING_ENV=production 双签门控 (memory 23032726 模式)。

## 5. 待办 (后续批次)

- UE-1: 统一实盘门控层 (live/simulate/TRADING_ENV 双签), 所有下单 handler 强制确认或默认 dry_run。
- UE-5: --ml-enhanced 回退不触发 NotImplementedError。
- UE-4/UE-6/UE-7: gemma 去重、回测资金对齐、deprecated stub 清理。

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [代码审查明细 — 主链路 institutional_pipeline_runner (2026-08-24)](code-review-pipeline-20260824.md) (相似度 18%)
- [UE-1 统一实盘门控 (2026-08-24)](ue1-live-gate-20260824.md) (相似度 17%)
- [代码审查明细 — 轻量子模块 (alpha/governance/macro/ml/monitoring) (2026-08-24)](code-review-lightweight-20260824.md) (相似度 15%)
- [代码审查明细 — risk 模块 (2026-08-24)](code-review-risk-20260824.md) (相似度 13%)
- [代码审查明细 — hedging 模块 (2026-08-24)](code-review-hedging-20260824.md) (相似度 13%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
