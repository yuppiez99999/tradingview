# 期权对冲执行 Runbook（Hedge Order Executor）

> 文档日期: 2026-08-06
> 状态: 生效（P0 修复已落地）
> 模块: `hedge_order_executor.py` + 统一入口 `--hedge-execute` 模式
> 背景: 修复「期权对冲只生成不执行」架构断链（详见《期权对冲未执行_根因诊断报告_20260806.md》）

---

## 1. 模块用途

补齐期权对冲「订单 → 撮合 → 成交 → 持仓/Delta 更新」完整闭环。
此前 `HedgeExecutionEngine.generate_hedge_orders()` 只生成 PENDING 期权订单写入
trade_plan，但系统**没有任何执行器**把订单送入撮合引擎，导致：
- trade_plan 期权订单永远停留在 PENDING
- 组合 Delta/Beta 从不因期权对冲下降
- daily_pnl 读不到 `hedge_execution_fill_*.json`，对冲数据恒为空

本模块解决了上述全部问题。

---

## 2. 使用方式

### 2.1 统一入口（推荐）

```bash
cd "28-终极量化交易系统8.4"

# 撮合执行当日 PENDING 期权订单
python "量化策略系统_统一入口_v8.6.py" --hedge-execute --date YYYY-MM-DD

# 干跑（只生成成交计划，不落盘不更新持仓）
python "量化策略系统_统一入口_v8.6.py" --hedge-execute --date YYYY-MM-DD --dry-run

# 仅输出待确认订单，不撮合（人工审阅用）
python "量化策略系统_统一入口_v8.6.py" --hedge-execute --date YYYY-MM-DD --confirm-only
```

### 2.2 直接运行

```bash
# 需使用 venv Python（裸 python 可能因 .pth GBK 编码启动失败）
.\.venv\Scripts\python.exe hedge_order_executor.py --date YYYY-MM-DD
.\.venv\Scripts\python.exe hedge_order_executor.py --date YYYY-MM-DD --dry-run
```

> Windows 控制台注意: print 输出货币符号用 `RMB`（避免 `¥`\xa5 触发 GBK
> `UnicodeEncodeError`）。或设置 `$env:PYTHONIOENCODING="utf-8"`。

---

## 3. 执行流程

```
trade_plan (PENDING 期权订单)
    │ 提取: hedge_execution.options_orders / covered_call_orders
    │       futures_options_hedge.orders / active_orders
    ▼
OptionsSimBroker 撮合
    ├─ BUY_PUT           支付权利金 (premium_total / premium_budget)
    ├─ SELL_CALL_COVERED 收取权利金 (est_total_premium)
    └─ multiplier=10000, 权利金 ±0.05% 滑点
    ▼
落盘 + 更新
    ├─ reports/hedge_execution_fill_{date}.json     ← daily_pnl 消费
    ├─ v8.3_institutional/reports/...同文件
    ├─ 每日报告归档/{date}/对冲执行单_{date}.json
    ├─ positions.json hedge_positions → FILLED + actual_positions
    └─ HedgeExecutionEngine.on_fill() → TCA 归因 (reports/tca/fills_{date}.jsonl)
```

---

## 4. 关键实现点

| 项 | 说明 |
|----|------|
| Delta 估算 | Put OTM 5% Δ≈-0.25；备兑卖出 Call Δ≈-0.20（每张覆盖 10000 份标的） |
| Beta 影响 | `(期权名义覆盖 / 组合市值) × 标的Beta(≈1) × |Δ|`，BUY_PUT/卖出Call均降低组合Beta |
| instrument 精确匹配 | **必须区分 `510050 Call` 与 `510050 Put`**——按代码前缀匹配会交叉污染权利金 |
| underlying 提取 | 从 instrument 正则提取 6 位标的代码（如 `510050 Put`→`510050`），否则 Put 无法计算 Beta 影响 |
| 原子写入 | JSON 先写 `.tmp` 再 `os.replace`，遵循不可变性原则 |

---

## 5. 验证基准（2026-08-06 实测）

| 指标 | 值 |
|------|-----|
| 收集 PENDING 订单 | 5 笔 (3 Put + 2 Covered Call) |
| 撮合成交 | 5/5 FILLED |
| 组合 Beta 对冲前 | 0.5186 |
| 组合 Beta 对冲后 | 0.3558（-0.1628） |
| Put 支出 / Call 收入 | ¥350,175 / ¥3,073 |
| 净成本 | ¥347,101.54 |
| TCA 归因 | 5 笔全部记录 |

---

## 6. 排障

| 现象 | 排查方向 |
|------|----------|
| 提示 "未找到 trade_plan, 无订单可执行" | 确认 `v8.3_institutional/trade_plans/trade_plan_{date}.json` 存在 |
| 收集到 0 笔 PENDING 订单 | 订单可能已被 EOD 执行器消费转 FILLED；或用 `--confirm-only` 先查 |
| daily_pnl 仍报"对冲数据为空" | 确认 `reports/hedge_execution_fill_{date}.json` 已生成 |
| `UnicodeEncodeError` (¥) | 用 `RMB` 输出或设 `PYTHONIOENCODING=utf-8` |
| 裸 `python` 启动失败 | 改用 `.venv\Scripts\python.exe`（.pth GBK 问题） |

---

## 7. 回滚

执行前自动创建备份。若需回滚到执行前状态：

```bash
# positions.json 恢复
Copy-Item config\positions.json.bak_YYYYMMDD_pre_exec config\positions.json -Force

# 清理成交产物（可选）
Remove-Item reports\hedge_execution_fill_*.json, "每日报告归档\*\对冲执行单_*.json" -Force
```

---

## 8. 后续增强方向

- 接入真实期权合约定价（Wind MCP / 交易所行情）替换估算权利金
- 到期滚仓（PUT_ROLL_DTE=5）接入执行器自动移仓
- 与 `alpha_hedge_engine.py` 的真实期权定价/流动性检查集成
