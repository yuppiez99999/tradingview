---
title: 审计报告批次三 item 15 收口 — broker 门禁 fail-closed + 模拟/实盘对账任务
date: 2026-09-10
status: 代码与门禁已收口（遗留：计划单补 order_id 以达订单级对账精度）
related:
  - 代码质量审计报告_20260909.md（§7bis 追踪表 · 报告项 15）
  - cairn/audit-p2-execution-chain-20260910.md（批次二）
---

# 批次三 item 15：broker.enable 切换门禁 + 模拟/实盘对账任务

报告项原文：「模拟/实盘对账任务 + broker.enable 切换门禁（环境变量 fail-closed 校验在执行器入口强制引用）（2d）」

## 0. 修复前的真实缺陷（两个）

| # | 缺陷 | 后果（为什么必须修） |
|---|---|---|
| A | `utils/execution/broker_factory.py:get_broker()` 在**真实下单就绪**（`broker.enabled=true` + `dry_run=false` + `TRADING_ENV=production`）三条件全满足时，若真实 broker 装不出来（xtquant 未装 / RPC 未配 / connect 失败），**静默降级 `SimulatedBroker`** | 订单被"模拟成交"、真实账户零仓位 —— "以为在下单、实则空转"的资金管理事故；且日志级别仅 WARNING，极易被淹没 |
| B | `utils/execution/automated_execution_system.py` 捕获装配异常后 `OrderRouter(broker=None)` **继续跑** | 同上，且 `broker=None` 下连模拟成交都没有，订单直接消失 |
| C | T13（`utils/risk/trade_order_reconciler.py` 订单级对账）/ T17（`live_reconciliation_loop.py` 持仓 drift）**已实现且门禁自检通过，但无任何生产入口调用** | 属"有组件无任务"的调度断链 —— 合约里承诺的对账能力实际从不运行 |

A/B 是**决策路径 fail-open**（应 fail-close），C 是**任务缺失**。三者同属"实盘切换前的最后一道闸门"。

## 1. 修复 A：`get_broker()` 实盘就绪改 fail-closed

`utils/execution/broker_factory.py`

新增：

- `class LiveBrokerUnavailableError(RuntimeError)` —— 专用信号，语义即"**绝不降级模拟**"，调用方必须让其向上传播。
- `_live_intent_from_cfg(cfg)` —— 不读盘的三重条件判定（enabled + !dry_run + `TRADING_ENV=production`），供 `get_broker` 与 `is_live_intent` 共用，消除双口径。
- `is_live_intent(cfg=None)` —— 公开判定入口（配置读取失败按 **False** 处理 = 安全默认，与既有 `_enforce_live_gate` 口径一致）。
- `is_live_broker(broker)` —— 以类名白名单（`SimulatedBroker/MockBroker/ShadowBroker/PaperBroker`）**反向**判定"是否真实通道"，避免对 v8.4 `BrokerAPI` / v8.3 适配器两套体系的硬依赖。
- `_degrade_or_raise(cfg, reason, level, live)` —— 统一"降级 or 抛"分支：`live=False` 告警 + 降级；`live=True` 告警 + 抛。所有降级点收敛到一个函数，避免遗漏。

改造：

- `_build_qmt(cfg, live=False)` / `_build_remote_qmt(cfg, live=False)` —— 所有原本 `_build_simulated(cfg)` 的早退分支改为 `_degrade_or_raise(..., live)`。
- **关键**：两处 `except` 块前插入 `except LiveBrokerUnavailableError: raise`，否则 fail-closed 信号会被下方宽捕获（`RuntimeError` 是父类）吞掉再转成"降级"。
- `_build_simulated(cfg, shadow=False, live=False)` —— `live=True` 时直接抛，防止未来在实盘路径误用模拟盘兜底。

门控矩阵（`get_broker`）：

| ①enabled | ②dry_run | ③TRADING_ENV=production | 结果 |
|---|---|---|---|
| ✗ | — | — | `SimulatedBroker`（默认模拟盘） |
| ✓ | ✓ | — | `SimulatedBroker(shadow=True)`（影子/演练） |
| ✓ | ✗ | ✗ | `SimulatedBroker` + 告警（防裸实盘） |
| ✓ | ✗ | ✓ | 走真实通道；**装配失败 → 抛 `LiveBrokerUnavailableError`** |

## 2. 修复 B：主链路装配点 fail-closed

`utils/execution/automated_execution_system.py`

- 导入块改引入 `LiveBrokerUnavailableError` / `get_broker` / `is_live_intent`（`broker_factory` 整体不可用时的赋值降级保留）。
- 新增 `_live_intent_fallback()`：`broker_factory` **整体不可用**（ImportError）时的兜底判定 —— 直读 `system_config.json` 的 broker 段 + `TRADING_ENV`；判定为实盘就绪则拒绝启动。读盘失败按未启用（安全默认）。
- `__init__` 装配点：
  - 捕获 `LiveBrokerUnavailableError` → `logger.critical` + `send_alert(level="critical")` + **`raise`**（告警自身 fail-open，不掩盖主链路 fail-closed 阻断）。
  - `elif _live_intent_fallback():` → 抛 `RuntimeError` 拒绝启动。
  - 其余异常仍保持既有 fail-open 降级（观测路径语义不变）。

## 3. 修复 C：对账任务接入（T13 + T17）

### 新增 `utils/risk/trade_reconciliation_runner.py`

| 能力 | 说明 |
|---|---|
| `build_planned_orders(plan, date_str, scope)` | 从 `trade_plan_<date>.json` 的 `execution_plan.morning_orders/afternoon_orders` 提计划单；标的归一化（`600519.SH`→`600519`）、方向归一化 |
| `load_fill_records(date, path)` | 读 `reports/fills/fills_<date>.jsonl`（FillsStore 事实源）；`order_id` 依次取 `order_id`/`parent_order_id`/`meta.order_id`；非法 JSONL 行跳过并告警 |
| `linkage_coverage(fills)` + `aggregate_symbol_side(orders, fills)` | 成交侧 order_id 覆盖率 < `LINKAGE_MIN_COVERAGE`(0.5) → 自动降级 `(symbol, side)` 聚合（数量求和、均价按量加权 VWAP） |
| `reconcile_date(...)` | 编排：载计划 → 载成交 → 判定关联模式 → 调 T13 `TradeOrderReconciler.reconcile()` → 组装报告 |
| `run_position_drift(broker, local_book)` | 调 T17 `LiveReconciliationLoop.detect_position_drift()`；本地账本来自 `config/positions.json` |
| `report_path_for` / `_write_report` | 落盘 `reports/reconciliation/reconciliation_<date>_<scope>.json`（**文件名含 scope**） |
| `format_summary` / `strict_exit_code` | 人类可读摘要；`RECONCILE_STRICT=1` 或实盘就绪时 `verified=False` → 退出码 1 |

数据契约与**如实记录的缺口**：

1. `trade_plan` 的计划单**无 `order_id`** → runner 按确定性规则合成 `{compact}_{session}_{code}_{side}_{idx:02d}`。
2. `FillsStore` JSONL 顶层无 `order_id`（只有 rebalance 路径写 `meta.order_id`）→ 覆盖率不足即降级，报告标 `linkage_mode="symbol_side"` / `linkage_degraded=True`。**订单级精度不足时绝不上报"订单级通过"**。
3. 期权对冲单由 `hedge_order_executor` 撮合但**未落盘 FillsStore** → 默认 `scope="etf"` 不纳入，避免制造假 `ORDER_COVERAGE`；`scope="all"` 纳入并在报告 `notes` 显式附注该接口缺口。

`status` 口径（**绝不假 PASS**）：`ok` / `no_plan` / `no_planned_orders` / `no_fills`；`verified = (status=="ok" and issues_count==0)`。

### 新增 `scripts/run_trade_reconciliation.py`（可调度 CLI）

```
python scripts/run_trade_reconciliation.py --date 2026-09-11
python scripts/run_trade_reconciliation.py --date 2026-09-10 --scope all --with-drift
$env:RECONCILE_STRICT="1"; python scripts/run_trade_reconciliation.py --date 2026-09-11
```

参数：`--date`(默认今天) / `--plan` / `--fills` / `--scope {etf,all}` / `--qty-tol`(0.10) / `--price-tol-bps`(50) / `--with-drift` / `--out-dir`。
退出码：`0` 观察模式（仅记录）或严格模式通过；`1` 严格模式未通过；`2` 参数/运行错误。

### 接入 EOD：阶段 4.93

`15_每日工作流/run_daily_eod_workflow.py` 新增 `run_phase4_93_reconciliation(report_date, eod_summary, args)`，插在**阶段 4.9 进化编排之后、阶段 4.95 ECL 旁路之前**，`success_count`/`fail_count` 与其它阶段同口径记账；新增 CLI 开关 `--skip-reconcile`。
实盘就绪时额外跑 T17 drift 并推送未通过告警；观察模式仅记录（`scope="etf"`）。

## 4. 验证快照（2026-09-10）

| 项 | 结果 |
|---|---|
| 新增/更新测试 | `tests/unit/test_p15_live_broker_gate_20260910.py`、`tests/unit/test_p15_reconciliation_runner_20260910.py`（含 `test_report_path_separates_scope` 修复"一改即红"用例）、`tests/unit/test_broker_factory_fills_store.py`（契约由"降级模拟"改为"抛异常"，旧用例同步 **✗修复前会失败**） |
| 对账+执行链+broker 回归集（6 文件） | **101 passed** |
| 对账执行链回归集（8 文件，含 t57/smart_order_router） | **220 passed** |
| ruff 增量门禁 | 通过（9 文件，无新增违规） |
| `industrial_grade_check` | 11 PASS / 1 WARN(C1 xtquant 未装) / 0 FAIL（持平） |
| `assert_data_validity` | 11 PASS / 1 FAIL（D1 压力测试空仓真实 FAIL，**独立项**未动） |
| `engineering_debt_gate` | RED（阻断项 D11 PhaseB shadow 14/7 天，**非本次引入**）；T6 fail-safe 宽捕获 66→**67**（+1 = drift 观测路径 broker 边界异常，按项目约定保留宽捕获；另 4 处已收窄为显式异常元组） |
| CLI 真实数据实证（09-10/09-11 真实计划与成交） | `reconciliation_2026-09-10_etf.json` = `no_planned_orders`(计划 0/成交 7) · `reconciliation_2026-09-10_all.json` = `ok`(计划 6/成交 7/问题 7/孤儿 1/`symbol_side` 降级/`verified=False`) · `reconciliation_2026-09-11_etf.json` = `no_fills`(计划 28/成交 0/问题 56) |
| fail-closed 实证 | `TRADING_ENV=production` + `get_broker({'enabled':True,'dry_run':False})` → `RAISED_OK LiveBrokerUnavailableError`（xtquant 未装环境） |
| EOD 阶段实证 | 隔离调用 `run_phase4_93_reconciliation` 返回 `True`，`eod_summary["phases"]["phase4_93_reconciliation"]` 记录 `status/linkage_mode/verified`；`--skip-reconcile` 分支记 `{"skipped": true}` |

**真实数据暴露的既有事实（非本次缺陷）**：09-11 计划 28 单、当日成交文件缺失 → 28 单全部 `ORDER_COVERAGE`（真实未成交或被上游跳过）；09-10 出现 1 笔孤儿成交（成交无对应计划单）。这正是"对账任务接入"的价值 —— 此前这些背离**无人观测**。

## 5. 未修边界 / 遗留

| 项 | 说明 |
|---|---|
| 订单级对账精度 | 需 `trade_plan` 生成器为每个计划单写 `order_id`，且 `daily_trade_executor` / `live_order_executor` 落 FillsStore 时盖**同一个** `order_id`。当前两侧不同源 → 只能 `(symbol, side)` 聚合。**未做**（触及计划生成器，风险高于收益） |
| 期权对冲单未入 FillsStore | `hedge_order_executor` 未调 `record_fill` → `scope="all"` 下期权单必报 `ORDER_COVERAGE`（报告已显式附注） |
| T17 drift 仅在实盘就绪时运行 | 模拟 broker 无真实持仓，与本地账本比对无意义；`--with-drift` 手工可跑 |
| 计划单数量为 0 的日期 | 09-10 `scope=etf` 计划 0 单 → `no_planned_orders`（如实标记，不判 PASS） |
| 巨型文件拆解 / utils 按域拆包 / 样本外验证主链路 | 批次三其余项，**未启动** |

## 6. 防复发要点

1. **决策路径 fail-close、观测路径 fail-open** —— 本次 A/B 的根因就是该边界被搞反；判断"是否实盘就绪"必须集中在 `is_live_intent()` 单一实现，禁止各处自行拼三重条件（双口径必然漂移）。
2. **fail-closed 信号必须防被宽捕获吞掉** —— 在 `except Exception`/`except RuntimeError` 之前显式 `except LiveBrokerUnavailableError: raise`。
3. **"有组件无任务"也是缺陷** —— 组件自检通过 ≠ 生产在跑；验收要问"谁在调度它、产物落哪个路径"。
4. **对账报告必须能表达"我不知道"** —— `status` 四态 + `verified` 单一判据 + `linkage_degraded` 标记；宁可报 `no_fills`，不可把"无数据"渲染成"无问题"。
5. **产物命名要带维度** —— 报告文件名含 `scope`，否则 `--scope all` 手工运行会静默覆盖 EOD 的 `--scope etf` 产物（本会话实测到并已修）。
