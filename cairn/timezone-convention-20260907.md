# 执行链时区规约（2026-09-07 R3 审查落地）

> 背景: 20260907 代码审查报告 R3 —— 全仓 `datetime.utcnow()` 491 处 / 31 文件混用,
> naive UTC 与本地时间无标识并存是「8 小时错位事故」温床。本文为执行链权威规约。

## 一、规约（口径铁律）

| 语义类别 | 规范 | 落点示例 |
|---|---|---|
| 审计/日志/跨系统时间戳 | **UTC，显式标识**（aware `+00:00` 或 `Z` 后缀 / naive+字段名注明 UTC） | fills JSONL、TCA、drift 审计记录、NTP |
| 交易决策/订单/展示/文件名 | **Asia/Shanghai（北京时间），naive 或 aware 均可但必须一致** | A 股盘中语义、执行切片建议时间、UI 时间戳 |
| 时间差计算（delta） | 与对比基准同口径即可，禁止混 naive/aware | 重同步判断、熔断冷却 |
| 时间同步基准 | NTP 原生 UTC；经 `server_ts()` 输出 UTC naive | ntp_sync.py |

编码铁律：
1. **禁止**新增 `datetime.utcnow()` / `datetime.utcfromtimestamp()`（Py3.12+ 弃用）。
   统一用 `datetime.now(timezone.utc)`（aware）或 `.replace(tzinfo=None)` 取 naive。
2. naive 时间戳若实际为 UTC，字段名/注释必须显式带 `utc`；否则按本地（北京）口径。
3. 新增 A 股语义时间用模块级 helper：`datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)`。

## 二、本次已治理（执行链 4 文件）

| 文件 | 改动 |
|---|---|
| `ms_strategy/src/execution/ntp_sync.py` | 5 处 `datetime.utcnow()` → 模块 helper `_utcnow()`（naive UTC，类型不变）；语义保持 NTP=UTC |
| `ms_strategy/src/execution/smart_order_router.py` | MockBroker `place/wait_fill` 的 ts 2 处 → `_utcnow_iso()`（UTC naive + 注释显式标注；与 ntp server_ts 口径一致） |
| `ms_strategy/src/execution/algo_engine.py` | `split()` 切片 `now = datetime.utcnow()` → `_now_bj_naive()`（执行建议时间 = 北京时间，消除 8h 错位） |
| `etf_option_hedge_rebalancer.py` | drift 审计 `triggered_at` → `datetime.now(timezone.utc).isoformat().replace("+00:00","Z")`（输出同形 `...Z`，消弃用） |

## 三、剩余治理面（非执行关键路径，随重构渐进）

- 2026-09-08 补修 scripts/ 4 文件 12 处（09-08 审查报告 R3 遗漏项）：
  `shadow_admission_launcher.py`（3 处；含真 bug——`_compute_observation_progress`
  的 ValueError 回退用本地 `datetime.now()` 与 UTC `now` 做 delta，8h 错位）、
  `daily_evolution_check.py`（6 处）、`gradual_rollout_manager.py`（2 处）、
  `run_fineng_comparison.py`（1 处）。统一 `datetime.now(timezone.utc)`
  （UP017 口径用 `UTC` 别名），输出 `...Z` 形状不变。
- 全仓其余 ~27 文件 470+ 处 utcnow 不在本轮范围，主要分布在：
  reports/UI 时间戳、research 脚本、历史审计模块。规则已立，新增代码禁 utcnow；
  存量按模块 touched 时渐进替换（先消弃用告警，再校准语义）。
- 治理顺序建议：任何触碰交易决策/成交回报时间的模块优先。
