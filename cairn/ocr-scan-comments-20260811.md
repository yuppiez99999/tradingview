# OCR 扫描代码评论落地（2026-08-11）

> 来源：`logs/_ocr_scan_chunk2.log`  
> 扫描目标：`daily_trading_workflow.py`（分块为 `_ocr_chunks/chunk2_dailyworkflow_core.py`）  
> 扫描时间：2026-07-28 18:11  
> 产出评论：19 条

---

## 评论汇总

### 1. [bug · critical] `phase_hedge_fund` 返回类型违约

- **位置**：`_ocr_chunks/chunk2_dailyworkflow_core.py:664-675`
- **问题**：方法声明返回 `Dict[str, Any]`，但当 `self.ks is None` 时返回 tuple `([], [], [{'level': -1, ...}])`，导致调用方 `result['status']` 崩溃。
- **修复**：统一返回 `result` dict，并写入 `self.state["phases"]["hedge_fund"]`。

### 2. [bug · medium] `_run_single_agent_shadow` 信号缓存读取错误

- **位置**：`_ocr_chunks/chunk2_dailyworkflow_core.py:2029-2034`
- **问题**：通过 `getattr(self.signal_fusion, "_research_distilled_signals", {})` 读取私有属性，但 LGB/Pipeline 信号存储在其他属性中，导致 `_fusion_strength` 常为 0。
- **修复**：改用 `self.signal_fusion._fusion_signals` 或公共 API `get_signal_strength(symbol)`。

### 3. [bug · high] 环境检测字符串与枚举不一致

- **位置**：`_ocr_chunks/chunk2_dailyworkflow_core.py:1952-1954`
- **问题**：`_get_env_v869()` 返回 `TradingEnv` 枚举，但代码用 `== "production"` 字符串比较，永远为 False，导致生产模式跳过逻辑失效。
- **修复**：改为 `_shadow_env == TradingEnv.PRODUCTION or _shadow_env == "production"`。

### 4. [bug · high] 期货行情缺失时使用确定性模拟数据

- **位置**：`_ocr_chunks/chunk2_dailyworkflow_core.py:1622-1626`
- **问题**：实时数据源未集成时，用 `random.seed(42)` 生成模拟数据，每次运行产生相同价格序列，交易决策基于不变假数据。
- **修复**：改为 `logger.error` + `return {}`，fail-closed 跳过 Phase 4.9。

### 5. [bug · high] 风控状态加载后未持久化

- **位置**：`_ocr_chunks/chunk2_dailyworkflow_core.py:1535-1536`
- **问题**：`_load_directional_futures_risk_state` 加载的日盈亏、连续亏损、暂停日期在 `trader.run()` 后未写回文件，跨交易日暂停机制失效。
- **修复**：在调用后检查 `df_result.pause_until`，调用 `_save_directional_futures_risk_state` 写回。

### 6. [performance · medium] 高频循环中 f-string 日志性能损耗

- **位置**：`_ocr_chunks/chunk2_dailyworkflow_core.py:21-21`（全文件多处）
- **问题**：`logger.info(f"...")` 即使日志级别抑制也会执行字符串格式化，在对冲执行高频循环中造成性能开销。
- **修复**：改为 `logger.info("...", arg)` 惰性求值。

### 7. [bug · high] 持仓策略归属字段名不匹配

- **位置**：`_ocr_chunks/chunk2_dailyworkflow_core.py:2280-2284`
- **问题**：`_build_coordination_current_positions` 用 `pos.get("type", "STOCK")` 判断策略，但 `_get_portfolio_positions_for_stress_test` 构建的字典 key 是 `"strategy"`，导致所有持仓被错误归类为 `"stock_long"`。
- **修复**：改为 `pos.get("strategy", "stock_long")`。

### 8. [bug · high] `wait_fill` 返回 None 时空指针访问

- **位置**：`_ocr_chunks/chunk2_dailyworkflow_core.py:0-0`（对冲执行循环）
- **问题**：`broker.place()` 的 `wait_fill` 可能返回 `None`，代码直接 `fill["price"]` 导致 `TypeError`。
- **修复**：加 `fill is not None` 判断，用 `fill.get("price", fut_price)`。

### 9. [bug · high] BL 优化使用简化对角协方差矩阵

- **位置**：`_ocr_chunks/chunk2_dailyworkflow_core.py:2114-2117`
- **问题**：`_np.eye(n_assets) * 0.04 ** 2` 假设所有资产独立同分布且日波动 4%，忽略资产间相关性，导致 BL 优化权重偏离有效前沿。
- **修复**：TODO 从 data_layer 加载真实历史协方差矩阵。

### 10. [bug · medium] 错误冲突日志循环体为空

- **位置**：`_ocr_chunks/chunk2_dailyworkflow_core.py:2300-2300`
- **问题**：`for c in err_conflicts:` 循环体为空，只记录数量不记录详情。
- **修复**：在循环内加 `logger.warning("冲突: %s | %s | 建议=%s", c.symbol, c.description, c.suggested_action)`。

### 11. [bug · high] 量化中性策略回撤状态未持久化

- **位置**：`_ocr_chunks/chunk2_dailyworkflow_core.py:1195-1207`
- **问题**：`run_monthly_rebalance` 的 `action="pause"` 决策后，新回撤状态和连续超限月数未写回文件，跨交易日暂停机制失效。
- **修复**：在 `run_monthly_rebalance` 后调用 `_save_strategy_drawdown_state`。

### 12. [bug · medium] 交易方向映射丢失原始意图

- **位置**：`_ocr_chunks/chunk2_dailyworkflow_core.py:2249-2249`
- **问题**：`side` 映射为 `"BUY"` 或 `"SELL"`，丢失 `"SELL_SHORT"` 与 `"CLOSE_LONG"` 的区别，多策略协调器无法正确理解交易意图。
- **修复**：保留原始 `side` 值或显式区分三种卖出类型。

### 13. [bug · high] 对冲计划键名不匹配

- **位置**：`_ocr_chunks/chunk2_dailyworkflow_core.py:2255-2255`
- **问题**：`_build_coordination_target_signals` 读 `self.state["phases"]["hedge"]["hedge_plan"]`，但 `phase_hedge` 存储的字典无此 key（有 `"orders"`, `"vix"` 等），导致 `hedge_plan` 恒为 `{}`，宏观对冲信号永不进入协调器。
- **修复**：改为读 `hedge_status.get("orders", [])` 并过滤 `action == "SHORT_FUTURES"`。

### 14. [bug · medium] 熔断强制加对冲可能重复记录

- **位置**：`_ocr_chunks/chunk2_dailyworkflow_core.py:259-269`
- **问题**：LEVEL_3/LEVEL_4 熔断时直接追加 `force_action` 到 `executed_orders`，未检查是否已被前面的对冲执行逻辑处理，导致重复记录。
- **修复**：追加前检查 `already_recorded = any(o.get("type") == "CIRCUIT_BREAKER_HEDGE" for o in executed_orders)`。

### 15. [bug · medium] `order.pop("_meta")` 破坏性修改共享状态

- **位置**：`_ocr_chunks/chunk2_dailyworkflow_core.py:474-474`
- **问题**：`_execute_sim_hedge_orders` 中 `order.pop("_meta", {})` 修改了 `coordinated["orders"]` 的引用，移除 `_meta` 字段，后续代码丢失元数据。
- **修复**：改为 `order.get("_meta", {})`。

### 16. [bug · medium] 期权预算计算导致 LIMIT 价格极小

- **位置**：`_ocr_chunks/chunk2_dailyworkflow_core.py:0-0`（期权预算分配）
- **问题**：`sub_budget = budget * ratio` 且 `sub_contracts = max(1, int(contracts * ratio))`，当 `ratio` 很小时 `opt_price` 极小（几分钱），LIMIT 订单无法成交。
- **修复**：需重新评估预算分配逻辑，确保 `opt_price` 在合理区间。

### 17. [bug · medium] `numpy` 局部导入导致静默降级

- **位置**：`_ocr_chunks/chunk2_dailyworkflow_core.py:2115-2117`
- **问题**：`_phase_signal_apply_bl_optimization` 内部 `import numpy as _np`，若未安装则被外层 `except Exception` 捕获，BL 优化静默降级。
- **修复**：将 `import numpy` 移到文件顶部，使 `ImportError` 在模块加载时暴露。

### 18. [bug · medium] `__file__` 路径解析脆弱

- **位置**：`_ocr_chunks/chunk2_dailyworkflow_core.py:1880-1880`
- **问题**：`_Path(__file__).resolve().parents[1] / "models" / "lgb_enhanced" / "lgb_enhanced_signals.json"` 在软链接、zip 包、`__main__` 加载时可能指向错误位置。
- **修复**：改用 `Path.cwd() / "models" / "lgb_enhanced" / "lgb_enhanced_signals.json"` 或配置化路径。

### 19. [bug · medium] 紧耦合访问私有属性

- **位置**：`_ocr_chunks/chunk2_dailyworkflow_core.py:2031-2034`
- **问题**：`_run_single_agent_shadow` 访问 `self.signal_fusion._research_distilled_signals`，若未来重构重命名/移除该属性，代码静默失败（`AttributeError` 被外层捕获）。
- **修复**：改用公共 API `get_signal_strength(symbol, default=0.0)`。

---

## 分类统计

| 严重程度 | 数量 | 占比 |
|---------|------|------|
| critical | 1 | 5.3% |
| high | 6 | 31.6% |
| medium | 11 | 57.9% |
| performance | 1 | 5.3% |

## 模块热点

- **`daily_trading_workflow.py`**：单文件 19 条评论，每个主要方法至少包含一个 high 级别问题。

## 跨领域关注

1. **不安全内部属性访问**：多处直接访问其他类的私有属性（`_research_distilled_signals`、`_get_env_v869`）。
2. **可变数据未拷贝修改**：`order.pop("_meta")` 修改共享引用。
3. **日志性能反模式**：高频循环中 f-string 日志。
4. **导入验证缺失**：局部 import 导致运行时才暴露缺失依赖。
5. **路径解析脆弱**：`__file__` + `parents[1]` 在特殊加载场景下失效。
6. **方向映射信息丢失**：`side` 映射为 BUY/SELL 丢失原始交易意图。

## 后续行动

- [ ] 将 critical/high 问题纳入下一轮代码审查修复批次
- [ ] 对 `daily_trading_workflow.py` 进行专项重构（状态持久化、类型契约、路径配置化）
- [ ] 补充单元测试覆盖风控状态持久化和环境检测分支
- [ ] 将本报告链接到 `cairn/LOG.md` 和 `docs/CODE_REVIEW_*.md`
