# 时区治理规约（2026-09-10 更新 — 全量清零 + 门禁防回退）

> 背景: 20260907 代码审查报告 R3 —— 全仓 `datetime.utcnow()` 491 处 / 31 文件混用,
> naive UTC 与本地时间无标识并存是「8 小时错位事故」温床。本文为全仓权威规约。
>
> **2026-09-10 重大更新**: DTZ003 全量清零完成 (77处→0), ruff 启用 DTZ003 为 error,
> pre-commit 加 DTZ005 新代码拦截门禁。时区治理进入「防回退」阶段。

## 一、规约（口径铁律）

| 语义类别 | 规范 | 落点示例 |
|---|---|---|
| 审计/日志/跨系统时间戳 | **UTC，显式标识**（aware `+00:00` 或 `Z` 后缀 / naive+字段名注明 UTC） | fills JSONL、TCA、drift 审计记录、NTP |
| 交易决策/订单/展示/文件名 | **Asia/Shanghai（北京时间），naive 或 aware 均可但必须一致** | A 股盘中语义、执行切片建议时间、UI 时间戳 |
| 时间差计算（delta） | 与对比基准同口径即可，禁止混 naive/aware | 重同步判断、熔断冷却 |
| 时间同步基准 | NTP 原生 UTC；经 `server_ts()` 输出 UTC naive | ntp_sync.py |

编码铁律：
1. **禁止**新增 `datetime.utcnow()` / `datetime.utcfromtimestamp()`（Py3.12+ 弃用）。
   ruff DTZ003 已启用为 error, pre-commit 自动阻断。
   统一用 `datetime.now(timezone.utc)`（aware）或 `.replace(tzinfo=None)` 取 naive。
2. **禁止**新增裸 `datetime.now()`（无 tz 参数）。
   pre-commit DTZ005 门禁拦截暂存区新代码; 存量 494 处渐进清理中。
   业务时间用 `now_bj()`, 审计时间戳用 `utc_iso()`, 纯 datetime 运算用 `now_utc_naive()`。
3. naive 时间戳若实际为 UTC，字段名/注释必须显式带 `utc`；否则按本地（北京）口径。
4. 新增 A 股语义时间用 `utils.datetime_utils.now_bj()`（北京时间 naive datetime）。

## 二、本次已治理（执行链 4 文件）

| 文件 | 改动 |
|---|---|
| `ms_strategy/src/execution/ntp_sync.py` | 5 处 `datetime.utcnow()` → 模块 helper `_utcnow()`（naive UTC，类型不变）；语义保持 NTP=UTC |
| `ms_strategy/src/execution/smart_order_router.py` | MockBroker `place/wait_fill` 的 ts 2 处 → `_utcnow_iso()`（UTC naive + 注释显式标注；与 ntp server_ts 口径一致） |
| `ms_strategy/src/execution/algo_engine.py` | `split()` 切片 `now = datetime.utcnow()` → `_now_bj_naive()`（执行建议时间 = 北京时间，消除 8h 错位） |
| `etf_option_hedge_rebalancer.py` | drift 审计 `triggered_at` → `datetime.now(timezone.utc).isoformat().replace("+00:00","Z")`（输出同形 `...Z`，消弃用） |

## 三、2026-09-10 全量清零 + 门禁防回退

### 统一入口模块: `utils/datetime_utils.py`

| 函数 | 语义 | 返回类型 | 用途 |
|---|---|---|---|
| `CN_TZ` | `timezone(timedelta(hours=8))` | `timezone` | 北京时区常量 |
| `now_bj()` | 北京时间 naive | `datetime` (naive) | A股业务时间 (交易决策/订单/展示/文件名) |
| `today_bj()` | 北京日期 | `date` | 交易日日期 |
| `now_utc()` | UTC aware | `datetime` (aware) | 审计时间戳 (aware 形式) |
| `now_utc_naive()` | UTC naive | `datetime` (naive) | 纯 datetime 运算 (elapsed/delta) |
| `utc_iso()` | UTC ISO + Z 后缀 | `str` | 审计/日志/跨系统时间戳 (如 `2026-09-10T12:00:00Z`) |

### DTZ003 全量清零 (77处 → 0) ✅

| 范围 | 处数 | commit |
|---|---|---|
| `utils/datetime_utils.py` 模块创建 + 6 单元测试 | — | `b298d678` |
| `utils/alpha/drift_monitor.py` (8A+2B) | 10 | `6d02d910` |
| alpha层7文件 (auto_retrain/delayed_label/kronos/mlops/ab_testing/model_registry/llm/router) | 29 | `cd147a41` |
| utils层8文件 (vibe_backtest/last30days/broker_failover/feature_flags/risk_bus/cvar/risk_event/dqc) | 11 | `d1a7bb90` |
| 测试7文件 | 25 | `91f4681f` |

### DTZ005 存量清理 (全仓 822→800, ui/pages 22处已清零) 🔧

已修 15+ 文件: signal_fusion, enhanced_signal_fusion, event_tracker, ai_coordinator,
tca_post_trade_attribution, data_quality_monitor, research_distiller, glm5_decision_engine,
supply_chain_risk/predict, pipeline/alpha_pipeline, pipeline/execution_pipeline,
hedge_engine, hedge_execution_engine, hedge_rebalance_integrator, kondratiev_cycle,
five_year_plan, lgb_signal_monitor, auto_trading_system, ai_report_agent 等。

**2026-09-11 ui/pages 批量修复** (commit 92816a90): 14 文件 22 处 `datetime.now()` → `now_bj()`,
覆盖 UI 展示/文件名/日期选择场景, ruff --fix 自动清理 12 未用导入 + 14 排序。

剩余 800 处, 分布: tests/unit 128 / ms_strategy 122 / scripts 20 / quant_modules 17 等。
用 `replaceAll` 方式 (`datetime.now()` → `now_bj()`) 分目录批量推进。

### 门禁防回退机制

1. **ruff.toml**: `select` 中加入 `"DTZ003"` — 永久禁止 `datetime.utcnow()` 回退
2. **pre-commit DTZ005 门禁**: 暂存区 .py 文件中新增裸 `datetime.now()` 被阻断
   - 跳过: `SKIP_DTZ_CHECK=1` (不推荐)
   - 修复: `datetime.now()` → `now_bj()` / `utc_iso()` / `datetime.now(CN_TZ)`
3. **本规约文档**: `cairn/timezone-convention-20260907.md` (本文件)
