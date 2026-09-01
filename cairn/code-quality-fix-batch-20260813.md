# 代码质量修复批次 — 2026-08-13（审查报告 B1/B2/S1/S2/S3/S4/S5/N1）

> 来源：`代码质量与缺陷审查报告_20260813.md` 全量修复
> 沉淀日期：2026-08-13
> 状态：✅ 已完成，30 tests 全绿，门禁无回退

---

## 1. P0 阻断修复

### 1.1 B1 — `daily_trading_workflow.py` 缺 `import os`（guaranteed NameError）

**根因**：模块级 `_resolve_path()` 使用 `os.sep`，但 import 区仅有 `json/random/logging/datetime/pathlib/typing`，无 `os`。
任何调用 `_resolve_path()` 的路径（如 `config/positions.json`）会立即抛 `NameError`。

**修复**：L19 补 `import os`。
**验证**：`ruff --select F821` → 0；B1 smoke test PASS。

**踩坑**：`os` 是 stdlib 最基础模块之一，F821 静态检查应能在 CI 捕获此类问题。当前 ruff baseline 未覆盖 F821 → 建议 baseline 逐步纳入。

---

### 1.2 B2 + S1 — `_execute_single_instruction` 卖单错误结算

**根因**：`daily_trade_executor.py:1374-1496` 是"建仓专用"实现，但接口通用。SELL 指令经此会：
1. 滑点方向永远向上（`×1.001`）→ 卖出成交价虚高
2. 持仓永远累加（`old_shares + actual_qty`）→ 卖出后股数不降反升
3. 返回字典硬编码 `"action": "BUY"` → 下游误判
4. 无印花税（A股卖出单边 0.05%）

**修复设计**：
- 读 `action = str(inst.get("action", "BUY")).upper()`，`is_sell = action == "SELL"` 分支隔离
- 滑点：BUY `×(1+rate)`，SELL `×(1-rate)`
- 成本：买入 `total_cost = fill_amount + 佣金 + 过户费`；卖出 `total_cost = 佣金 + 过户费 + 印花税`（`fill_amount` 为成交毛额，两边一致）
- 持仓：买入 `new_shares=old+qty` + 加权均价；卖出 `new_shares=old-qty`，`avg_cost` 不变（成本基础不变，已实现盈亏另计）
- progress：买入累加，卖出减少（`max(0, …)`）
- 返回字典 `action` 用变量透传，新增 `stamp_duty` 字段

**回归安全**：BUY 路径语义不变；SELL 当前无实际调用方（已确认 `_execute_single_instruction` 实际只收 BUY），属防御性硬化。

**关键踩坑 — 滑点与陈旧测试耦合**：
- 修复前：现有 6 个 `TestExecuteSingleInstruction` 测试全部 **FAIL**（`qty=99, fill_price=1801.8, fill_amount=178378.2`），但断言仍为 `qty==100, fill_price==1800.0`——说明滑点代码早已存在，测试是滑点特性引入后未同步的**陈旧断言**。
- 修复后：6 个旧测试全部 **PASS**（更新为滑点感知值）。
- **教训**：添加交易成本/滑点特性时，必须同步更新所有相关测试断言；否则测试会静默失效，掩盖真正的回归。

---

## 2. P1 对冲修复

### 2.1 S4 — 期权最小名义阈值

**根因**：`hedge_execution_orders.py:352` `contracts = max(1, int(alloc_notional / (est_price * multiplier)))`
极小 `alloc_notional`（如 10000 元）不足 1 张合约名义（如 40000 元），但 `max(1, ...)` 强制开 1 张 → 名义暴露远超预算，过度对冲。

**修复**：`one_contract_notional = est_price * multiplier`，`min_notional = item_cfg.get("min_notional") or one_contract_notional`。
`alloc_notional < min_notional` → `logger.info` + `continue`，跳过该期权。

**设计决策**：阈值来源优先读配置（`item_cfg.get("min_notional")`），兜底为 1 张名义。与文件内已有 `premium_budget` 配置读取模式一致。

---

### 2.2 S5 — 期货 instrument/multiplier 配置化

**根因**：`_build_beta_futures_order` 硬编码 `instrument = "IF"`，`multiplier = 300`。
系统需支持 IC/IH/IM 等品种，硬编码导致无法切换。

**修复**：`instrument = futures_cfg.get("instrument", "IF")`，`multiplier = int(futures_cfg.get("multiplier", 300) or 300)`。
与文件内 L546/L658 已有 `cfg.get("multiplier")` 模式一致。

---

## 3. 健壮性修复

### 3.1 S2 — 空头止损

**根因**：`stop_loss_monitor.py:304-307` `if shares <= 0: return None`
空头持仓（`shares < 0`）被当作 flat 处理，无止损/止盈逻辑。
若未来接入融券做空策略，空头将无风控保护。

**修复设计**：
- 区分三层：`shares == 0` → flat（清高低水位线，return None）；`shares < 0` → `is_short`；`shares > 0` → 现有多头逻辑
- 空头止损线：`entry * (1 - stop_loss_pct)`（stop_loss_pct 为负如 -12% → ×1.12，价格上涨触发）
- 空头止盈线：`entry * (1 - take_profit_pct)`（价格下跌触发）
- 移动止损跟踪**最低价**（`_low_water_mark`），随价格下跌收紧上方止损线
- 平仓 action = `BUY`（买回），`shares` 取绝对值
- 新增 `_low_water_mark` dict，`get_monitoring_status` 快照同步暴露

**设计决策**：多头分支完全不动（回归安全）；空头为镜像对称设计，与多头共享 `stop_loss_pct` / `take_profit_pct` / `trailing_stop` 规则字段，无需新增配置。

---

### 3.2 S3 — live_scheduler 日志加固

**根因**：L170/745 两处 `except Exception` 仅 `logger.debug(...)`，生产环境默认日志级别 INFO+，debug 消息被静默吞掉。
行情获取失败/策略退化告警记录失败等异常在生产中完全不可见。

**修复**：`logger.debug` → `logger.warning`，确保异常在标准生产日志中可见。

---

### 3.3 N1 — 期权乘数 instrument 感知

**根因**：`hedge_order_executor.py:84` `OPTION_MULTIPLIER = 10000` 仅适用于 ETF 期权（上交所/深交所 1 张 = 10000 份）。
股指期权（IO/MO/HO，中金所）乘数为 100，硬编码 10000 会导致名义金额和 Delta 计算错误。

**修复**：新增 `_default_option_multiplier(instrument)` 函数，按 instrument 前缀返回：
- `IO`/`MO`/`HO` → 100（股指期权）
- 其他 → 10000（ETF 期权）
- 两处 `order.get("multiplier") or OPTION_MULTIPLIER` fallback 均改为调用此函数。

---

## 4. 附带修复

### 4.1 `hedge_execution_orders.py` Python 3.8 类型注解兼容

**根因**：`_extract_contract_yyyymm` 和 `_validate_contract_expiry` 使用 `tuple[str, int]` 和 `tuple[bool, str]` 语法。
Python 3.8 不支持内置类型 subscript，导致模块导入抛 `TypeError: 'type' object is not subscriptable`，所有依赖此模块的测试无法收集。

**修复**：`tuple[str, int]` → `Tuple[str, int]`，`tuple[bool, str]` → `Tuple[bool, str]`；补 `from typing import Tuple`。

**教训**：代码库目标运行环境为 Python 3.8（见 `ci.yml`），类型注解必须使用 `typing` 模块兼容语法，不能使用 3.9+ 内置类型。

---

## 5. 测试策略

### 5.1 新增测试文件

| 文件 | 测试数 | 覆盖 |
|------|--------|------|
| `tests/unit/test_daily_trade_executor_unit.py::TestExecuteSingleInstruction` | 9 | BUY 回归（滑点感知）+ SELL 滑点/印花税/持仓减法/progress clamp |
| `tests/unit/test_stop_loss_monitor_unit.py`（新） | 7 | 多头止损/止盈/移动止损 + 空头止损/止盈/移动止损 + flat/无规则 |
| `tests/unit/test_hedge_execution_orders_unit.py`（新） | 6 | 期权最小名义阈值 + 期货 instrument/multiplier 配置化 |
| `tests/unit/test_b1_import_os_fix.py`（新） | 2 | import 不抛 NameError + `_resolve_path` 正常解析 |
| `tests/unit/test_n1_option_multiplier.py`（新） | 2 | ETF 期权 10000 + 股指期权 100 |

### 5.2 陈旧测试修复

修复前 `TestExecuteSingleInstruction` 6/6 FAIL（滑点代码存在但断言未同步）。
修复后 6/6 PASS + 3 个 SELL 新测试 PASS = 9/9 PASS。

**教训**：交易成本/滑点等数值敏感特性引入后，必须审计所有相关测试的硬编码期望值。

---

## 6. 跨项目可复用经验

1. **交易执行函数应显式区分买卖方向**：滑点、费用、持仓算术、进度更新均需分支处理，不能假设"只建仓"。
2. **A股成本模型最小完整集**：买入（佣金+过户费），卖出（佣金+过户费+印花税 0.05%），印花税单边。
3. **空头风控应与多头镜像对称**：共享规则字段，方向反转（止损线在上方、止盈线在下方、平仓 action 反转、水位线跟踪方向反转）。
4. **硬编码 instrument/multiplier 是对冲系统的常见债**：期货（IF/IC/IH/IM）和期权（ETF/股指）乘数均需配置化，建议统一从 `hedge_positions` 配置读取。
5. **Python 3.8 类型注解兼容**：使用 `typing` 模块（`Tuple`/`Optional`/`Dict`），禁止 3.9+ 内置类型 subscript。
6. **日志级别选择**：`logger.debug` 在生产环境默认不可见，关键失败路径（如行情获取、告警发送）应使用 `logger.warning` 或 `logger.error`。

---

## 7. 指针

- 审查报告：`代码质量与缺陷审查报告_20260813.md` §九「修复记录（08-13 实施）」
- 深度复核：`代码质量与缺陷审查报告_20260813_深度复核.md`（Bug-1~6 + Q-1~Q-5）
- 修复计划：`代码质量修复计划_20260813.md`
- 执行器：`daily_trade_executor.py:_execute_single_instruction`
- 对冲订单：`hedge_execution_orders.py`（S4/S5 + 附带 tuple 修复）
- 对冲执行：`hedge_order_executor.py`（N1）
- 止损监控：`stop_loss_monitor.py`（S2）
- 工作流入口：`daily_trading_workflow.py`（B1）
- 调度器：`live_scheduler.py`（S3）

---

## 8. 08-14 深度复核 + Q-1~Q-5/F401 修复闭环

### 8.1 Bug-1~6 验证结果（全部已在工作区修复）

| Bug | 严重度 | 修复位置 | 状态 |
|-----|--------|---------|------|
| Bug-1 空头止损执行断裂 | High | `stop_loss_monitor.py:499` 支持 BUY + `_execute_close(side)` | ✅ |
| Bug-2 合约月份硬编码 | Medium | `hedge_execution_orders.py:548` 动态 `_active_months` | ✅ |
| Bug-3 signal_monitor 类型不一致 | Medium | `signal_monitor.py:234` 改用 `numeric_signals` | ✅ |
| Bug-4 对冲订单误匹配 | Medium | `hedge_order_executor.py:592` 加 `not oid and not fill_oid` 守卫 | ✅ |
| Bug-5 幂等键缺 action | Low | `daily_trade_executor.py:1634` 键含 action | ✅ |
| Bug-6 RUNNING 并发竞争 | Low | `live_scheduler.py:78` `RUNNING_EVENT` + `_results_lock` | ✅ |

### 8.2 本轮新增修复

**P0 — F401 回归清理**（修复 Bug-1~6 时引入的未使用 import）:
- `hedge_order_executor.py:62` 删 `import sys`
- `live_scheduler.py:47` 删 `from pathlib import Path`
- `live_scheduler.py:50` 从 import 移移除 `get_v8_src_dir`

**Q-3 — 历史路径回退移除**:
- `stop_loss_monitor.py:_create_mock_broker` 移除 `os.path.join(_BASE, "..", "11_量化策略")` + `sys.path.insert`
- 改用 `from bridges.broker_adapter import BrokerFactory`（`setup_sys_path()` 已注入 v8.3 src）

**Q-2 — signal_monitor UTF-8 兜底**:
- `signal_monitor.py:analyze_signal_effectiveness` 入口加 `sys.stdout.reconfigure(encoding="utf-8")`
- 保留 print（CLI 报告语义，表格/分隔线/对齐），仅防 GBK 对 `✓✗─🟢` 抛 UnicodeEncodeError

**Q-1 — DEFENSE_ASSETS DRY 收敛**:
- 新建 `utils/hedge_constants.py` 作为单一事实源
- `hedge_execution_orders.py` + `hedge_quantity_calculator.py` 统一导入 `DEFENSE_ASSETS`

**Q-5 — DEFAULT_PRICES 角色标注**:
- `daily_trade_executor.py` 注释标注 DEFAULT_PRICES 为二级兜底（`load_latest_prices()` 已从收盘报告动态读取）

### 8.3 验证

- ruff F821/E9/F401 = 0（阻断性错误清零）
- 173 相关单元测试全绿
- `engineering_debt_gate` GREEN（T1-T18 + D1-D8，覆盖率 0.7477 维持）

### 8.4 踩坑/经验

1. **修复引入回归需同步清理**: Bug-1~6 修复时引入了 3 处 F401 未使用 import（删了使用 `Path`/`sys`/`get_v8_src_dir` 的代码但忘了删 import）。每次修复后应跑 `ruff --select F401` 验证。
2. **CLI 报告函数 print vs logger**: 面向人类终端的表格/分隔线输出，print 有语义合理性；全转 logger 会破坏格式。真正风险是 GBK 编码，用 `sys.stdout.reconfigure` 兜底比全转 logger 更合适。
3. **OFFLINE_ONLY 脚本可 import 纯常量模块**: `hedge_quantity_calculator.py` 的硬护栏阻止**被 import**，不阻止它 **import 别人**。提取共享常量到 `utils/hedge_constants.py` 是安全的 DRY 收敛。
4. **二级兜底已有动态读取时，标注优于改行为**: `DEFAULT_PRICES` 已有 `load_latest_prices()` 从收盘报告动态读取作为一级来源，DEFAULT_PRICES 只是二级兜底。标注角色 + stale 风险，比移除个股价格（改行为+破坏测试）更安全。

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [代码审查经验沉淀：量化系统 8.4 资金安全与回测可信度](code-review-lessons-v8.4.md) (相似度 12%)
- [TradingGroup 自反思机制 (Self-Reflection + Data-Synthesis + Dynamic Stops)](trading-group-reflection.md) (相似度 11%)
- [OCR 扫描代码评论落地（2026-08-11）](ocr-scan-comments-20260811.md) (相似度 10%)
- [代码审查 + 修复批次 标准作业流程 (SOP)](code-review-sop.md) (相似度 9%)
- [2026-08-08 代码审查修复批次经验沉淀](code-review-fix-batch-20260808.md) (相似度 8%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
