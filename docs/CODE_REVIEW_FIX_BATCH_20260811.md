# 代码审查修复批次 2026-08-11

> 基于 OCR + GLM 4.5-air 扫描 `daily_workflow.py` 发现的 7 个 high+critical 问题。
> 注：扫描对象为 `v8.3_institutional/daily_workflow.py`（历史版本 v7.5，已废弃），当前主工作流为 `daily_trading_workflow.py`。
> 本批次将问题归档为技术债务，并在 `daily_trading_workflow.py` 重构时预防同类问题。

## 问题清单

### Critical (1)

| ID | 位置 | 问题 | 影响 | 修复建议 |
|---|---|---|---|---|
| C-1 | `v8.3_institutional/daily_workflow.py:phase_hedge_fund()` | 返回类型违约：函数签名声明返回 `dict`，但部分分支返回 `None` | 下游调用方未做空值检查，直接访问 `.get()` 会抛 `AttributeError` | 统一返回类型为 `dict`，空结果返回 `{}`；或改为返回 `Optional[dict]` 并更新下游调用 |

### High (6)

| ID | 位置 | 问题 | 影响 | 修复建议 |
|---|---|---|---|---|
| H-1 | `v8.3_institutional/daily_workflow.py:_run_single_agent_shadow()` | 信号缓存读取错误：使用过期键名 `agent_cache` 而非 `shadow_agent_cache` | 缓存命中率骤降，每次回测都重新计算信号，性能下降 10x+ | 统一缓存键名，添加缓存版本号，读取失败时降级到实时计算并告警 |
| H-2 | `v8.3_institutional/daily_workflow.py:_get_env_v869()` | 环境检测字符串比较失败：`os.getenv("TRADING_ENV") == "production"` 与配置文件中 `"PRODUCTION"` 大小写不一致 | 生产环境检测永远为 `False`，导致风控/告警模块被静默跳过 | 统一环境变量值规范（建议全大写），添加 `.lower()` 归一化，或使用 `Enum` |
| H-3 | `v8.3_institutional/daily_workflow.py:_load_directional_futures_risk_state()` | 风控状态未持久化：内存中计算的风险敞口未写入磁盘 | 进程重启后风控状态丢失，可能触发重复开仓或过度平仓 | 添加 `_save_directional_futures_risk_state()`，在每次风控检查后落盘 |
| H-4 | `v8.3_institutional/daily_workflow.py:_build_coordination_current_positions()` | 持仓策略归属字段缺失：`positions.json` 缺少 `strategy` 字段 | 月度再平衡无法识别持仓来源，导致重复调仓或遗漏调仓 | 在持仓初始化时写入 `strategy` 字段，回测/实盘统一使用该字段过滤 |
| H-5 | `v8.3_institutional/daily_workflow.py:run_monthly_rebalance()` | 回撤状态未持久化：`_strategy_drawdowns` 字典仅存在于内存 | 月度再平衡时无法获取历史回撤，止损逻辑失效 | 添加 `_save_strategy_drawdown_state()`，在每次日终后落盘 |
| H-6 | `v8.3_institutional/daily_workflow.py:_phase_signal_apply_bl_optimization()` | BL 优化权重未校验：优化器输出权重未检查是否满足约束条件 | 可能出现负权重或权重和不为 1 的情况，导致下单金额异常 | 添加权重后处理：`weights = np.clip(weights, 0, 0.1)`，`weights /= weights.sum()` |

## 修复状态

> 注：经核查，`v8.3_institutional/daily_workflow.py` 为历史版本（v7.5），当前主工作流已迁移至 `daily_trading_workflow.py`。
> 上述 7 个问题在旧版本中已通过代码审查记录，但旧版本不再主动维护。
> 预防措施已应用到 `daily_trading_workflow.py` 重构中。

## 已应用的预防措施（daily_trading_workflow.py）

1. **状态持久化**：新增 `_save_workflow_state()` / `_load_workflow_state()`  helpers，所有运行时状态可落盘到 `reports/workflow_state/`
2. **类型契约**：新增 `PositionDict` / `MarketDataDict` / `SignalDict` / `PortfolioSummaryDict` TypedDict 定义
3. **路径配置化**：新增 `_resolve_path()` 统一路径解析，支持正斜杠/反斜杠混用
4. **环境检测**：建议使用 `Enum` 或常量定义环境变量（待后续专项重构）
5. **缓存键名**：建议统一缓存命名规范，添加版本前缀（待后续专项重构）

## 验证清单

- [x] `daily_trading_workflow.py` 状态持久化 helpers 已添加
- [x] `daily_trading_workflow.py` 类型契约 TypedDict 已定义
- [x] `daily_trading_workflow.py` 路径配置化 `_resolve_path` 已实现
- [ ] C-1：旧版本 `phase_hedge_fund` 返回类型（旧版本废弃，不修复）
- [ ] H-2：环境检测（旧版本废弃，不修复）
- [ ] H-1：缓存键名（旧版本废弃，不修复）
- [ ] H-3：风控状态持久化（已通过 helpers 预防）
- [ ] H-4：持仓策略归属字段（已通过 TypedDict 预防）
- [ ] H-5：回撤状态持久化（已通过 helpers 预防）
- [ ] H-6：BL 优化权重校验（旧版本废弃，不修复）

## 关联文档

- 扫描日志：`logs/_ocr_scan_chunk2.log`
- 扫描产物：`cairn/ocr-scan-comments-20260811.md`
- 快速索引：`docs/ocr-scan-comments-index-20260811.md`
- 修复批次计划：`docs/CODE_REVIEW_FIX_BATCH_20260811.md`
