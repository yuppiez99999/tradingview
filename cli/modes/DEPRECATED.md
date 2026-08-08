# cli/modes 目录已废弃 (方案A · 2026-08-04)

## 状态

**已废弃** — 不再被主入口文件 `量化策略系统_统一入口_v8.6.py` 引用。

## 废弃原因

`cli/modes/` 下的模式处理器依赖一组**从未在 git 中存在过的"幻影模块"**：

- `core.context` (BASE_DIR / logger / strategy_registry / ProgressIndicator / connector_manager 等)
- `engine.managers` (ETFFundFlowMonitor)
- `engine.rebalance` (ExcelDrivenRebalancingEngineV4)
- `utils.cli_helpers` (write_report_file / archive_report / get_ml_signal_section 等)
- `cli/__init__` / `core/__init__`

这些模块缺失导致 `from cli.modes import (...)` 在主入口文件 line 130 第一个 import 就崩，主文件完全无法运行。

## 方案A 处置

按"方案A"处置（与"方案B 完整重建 8 阶段"二选一，本次选 A）：

1. **主入口文件**删除 `from cli.modes import (...)` 块（原 L343-365）。
2. 用 `_deprecated_mode_stub(mode_name, flag, alt_entry)` 工厂生成 **21 个本地占位 handler**，打印废弃提示并指向 `v8.3_institutional/` 替代入口。
3. `engine.managers` / `engine.rebalance` 硬 import 改为 `try/except ImportError` 降级（engine/ 包阶段4未完成）。
4. `run_quick_check` 中 4 处 API 不匹配调用（`strategy_registry.list` / ETF 阈值除法 / `connector_manager.get_status` / `graceful_fallback.is_fallback_mode`）加 `try/except` 守卫，确保 `--check` 完整跑通。

## 保留目录的原因

- 保留 21 个模式处理器的**业务逻辑实现**作为后续重建的参考蓝本（接口推断来源）。
- 若未来执行"方案B 完整重建"（创建 core.context / engine / utils.cli_helpers 等），可从此目录恢复 import。
- 删除目录会丢失这份迁移出来的代码，不利于后续重建。

## 21 个废弃模式 → 替代入口

| flag | 模式 | 替代入口 |
|---|---|---|
| `--daily` | 每日工作流 | `py -3.8 v8.3_institutional/daily_workflow.py --phase all` |
| `--risk` | 风险监控 | 参考 v8.3_institutional/ |
| `--etf-flow` | ETF资金流向 | 参考量化策略系统_统一入口_v8.6.py 本地 `run_live_monitoring` |
| `--portfolio-opt` | 投资组合优化 | 参考量化策略系统_统一入口_v8.6.py 本地 `run_rebalance` |
| `--kommo-monitor` | 康波周期监控 | 参考量化策略系统_统一入口_v8.6.py |
| `--commodity-fund` | 大宗商品基本面 | 参考量化策略系统_统一入口_v8.6.py |
| `--kondratiev` | 康波+十五五交叠 | 参考量化策略系统_统一入口_v8.6.py |
| `--fifteen-five` | 十五五规划适配 | 参考量化策略系统_统一入口_v8.6.py |
| `--social-security` | 社保基金ETF追踪 | 参考量化策略系统_统一入口_v8.6.py |
| `--macro-analysis` | 宏观综合分析 | 参考量化策略系统_统一入口_v8.6.py |
| `--ai-decision` | AI盘中决策 | 参考量化策略系统_统一入口_v8.6.py |
| `--futures-options` | 期货期权扫描 | 参考量化策略系统_统一入口_v8.6.py |
| `--unified-monitor` | 统一监控 | 参考量化策略系统_统一入口_v8.6.py |
| `--hedge` | 对冲分析 | 参考量化策略系统_统一入口_v8.6.py |
| `--hedge-rebalance` | 对冲+再平衡联动 | 参考量化策略系统_统一入口_v8.6.py |
| `--hedge-detail` | 期货对冲明细 | 参考量化策略系统_统一入口_v8.6.py |
| `--ml-significance` | ML显著性验证 | 参考量化策略系统_统一入口_v8.6.py |
| `--kronos` | Kronos K线预测 | 参考量化策略系统_统一入口_v8.6.py |
| `--gemma` | Gemma分析增强 | 参考量化策略系统_统一入口_v8.6.py |
| `--dcf` | DCF估值模型 | 参考量化策略系统_统一入口_v8.6.py |
| `--comps` | 可比公司分析 | 参考量化策略系统_统一入口_v8.6.py |

## 仍可用的本地模式 (13 个)

主入口文件本地定义，不依赖 cli/modes，方案A 后保持可用：

`--live` / `--report` / `--rebalance` / `--backtest` / `--check` / `--hypothesis` / `--train-model` / `--train-enhanced` / `--ml-signal` / `--ml-enhanced` / `--ai-hedge` / `--stress-test` / `--stop-loss`

注：`--rebalance` 依赖 `ExcelDrivenRebalancingEngineV4` (engine/rebalance)，engine/ 未创建时会通过 dispatch 层 try/except 降级报错。
