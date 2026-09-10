---
type: fix-record
status: done
authoring_mode: session
created: 2026-09-10
updated: 2026-09-10
source: 代码质量审计报告_20260909.md（批次二 · 执行链 P2 修复包 剩余项）
---

# 审计批次二 · 执行链 P2 收口（2026-09-10）

> 承接 2026-09-09「审计修复批改一二」（commit 664c14aa / 66694ebb / fef8b708 / a064479e 等）。
> 本文记录批次二执行链 P2 的**剩余开放项**闭环，含判据、修法、验证与"不修"边界。

---

## 1. live_scheduler 单实例锁 TOCTOU（P2）

**缺陷**：`main()` 走 `if _is_running(): return; _write_lock(pid)` —— 典型 check-then-act。
两个进程（cron 与手工启动、容器重启重入）可同时通过 `_is_running()` 检查，再各自覆盖写锁文件 →
双实例并行调度，定时任务与交易指令**重复执行**。

**修法**：新增 `_acquire_lock()`，以 `os.open(path, O_CREAT|O_EXCL|O_WRONLY)` **原子创建**锁文件；
已存在时读 pid：存活则拒绝（返回 False），为 stale（pid 已失效）则清理后重试一次。
`_write_lock()` 删除（消除双重实现）。`--status` 同步改为 `_is_running()` 判定，不再把僵尸锁报成"运行中"。

**验证**：`test_acquire_lock_is_atomic`（二次获取必 False）/ `test_acquire_lock_reclaims_stale_lock`
（pid=999999 的 stale 锁被接管）/ `test_write_lock_removed_after_refactor`（防双重实现回潮）。

## 2. 定时任务挂死即停摆（P2）

**缺陷**：`task_func` 直接在 `threading.Timer` 线程内同步执行，且 `self.executor`（ThreadPoolExecutor）
**创建后从未使用**（死基础设施，docstring 里"ThreadPoolExecutor 实现并发执行"名不副实）。
任一任务挂死 → ① 该模块下一次 Timer 永不安排（永久停摆）；② `stop()` 的
`executor.shutdown(wait=True)` 无限阻塞。

**修法**：
- `_run_task(module_name, task_func, timeout=None)` 改为提交到 executor 并 `future.result(timeout=...)`；
  超时捕获 `concurrent.futures.TimeoutError` → 标记 `TIMEOUT` 并**返回**，调度链继续。
- 超时值：模块定义 `timeout_seconds` > `DEFAULT_TASK_TIMEOUT`（默认 1800s，`LIVE_SCHEDULER_TASK_TIMEOUT` 可覆盖）。
- `stop()` 改 `shutdown(wait=False)` + `SHUTDOWN_GRACE_SECONDS`（默认 30s）有界等待，超时告警而非永久阻塞。
- 注：Python 无法强杀线程，超时后孤儿线程仍在池内；此处保证的是**调度不停摆**与**停机可返回**，
  这是看门狗语义而非资源回收语义。

**验证**：`test_run_task_times_out_without_blocking_schedule`（sleep 2s + timeout 0.1s，实测 < 1.5s 返回且状态 TIMEOUT）/
`test_run_task_records_result_and_survives_exception`（异常被吸收为 ERROR，不中断链）。

## 3. module_last_run 写读竞态（P2）

**缺陷**：`self.module_last_run[module_name] = now` 在锁外写，而 `get_status()` 另一线程遍历该 dict →
`RuntimeError: dictionary changed size during iteration`。另有 `check_and_run` 的
"读 key → 跑任务 → 写 key"无锁 check-then-act，任务耗时 > 60s 时会被下一轮 Timer 重复触发。

**修法**：新增 `_record_module_result()` 把 `module_last_run / module_results / MODULE_STATUS`
三处写入收拢进同一临界区（`_results_lock`）；`get_status()` 改为锁内**快照**后返回；
`check_and_run` 改为"锁内先占位（写日期 key）再执行"。

**验证**：`test_get_status_no_runtime_error_under_concurrency`（4 写线程 × 200 次读快照，零异常）。

## 4. hedge_order_executor "读了对冲持仓后即丢弃"（死代码 → 功能缺失）

**缺陷**：`positions_data.get("hedge_positions", {}) or {}` 求值后丢弃，导致 docstring 声明的
**来源 #4「positions.json 回流」从未生效**——positions.json 里的 PENDING 期权订单会漏单。

**修法**：`_collect_pending_orders(plan, fallback_active_orders=None, trade_date=None)`；
plan 未携带 `active_orders` 时回退到 `hedge_positions["active_orders"]`，
**且仅在回流结构 `date == trade_date` 时采用**（防执行陈旧对冲订单，order_id 去重防重复撮合）。

**验证**（负向实证）：修复前该回流订单收集数 = 0，修复后 = 1；
`test_collect_pending_orders_skips_stale_fallback` 保证去掉日期护栏即变红。

## 5. daily_trade_executor 成本魔法数字（P2）

**缺陷**：`slippage_rate = 0.001  # 滑点 10bp (可配置)` —— 注释称可配置，实为内联常量；
同段还有 `avg_daily_volume=1000000`、`inst_amount > 50000`。

**修法**：新增 `_cost_param(key, default)` 三级取值 **cost_model 段 > `QUANT_<KEY>` 环境变量 > 内置默认值**，
默认值与历史硬编码**逐位一致（行为零变化）**；`cost_model` 段写入 `configs/trade_execution.yaml`
（该文件被 .gitignore 排除，故必须保留环境变量通道，使参数不依赖未跟踪文件即可覆盖）。
WT 兜底 ADV 超量级时新增 WARNING（避免"假装有流动性"的乐观成本静默流入 PnL）。

**验证**：`test_cost_model_defaults_match_legacy_hardcode` + `test_cost_param_priority_config_then_env_then_default`。

---

## 验证快照（2026-09-10）

| 项 | 结果 |
|---|---|
| 新增回归测试 | `tests/unit/test_p2_execution_chain_20260910.py` 12 passed |
| 执行链相关回归集（6 文件） | 186 passed |
| py_compile / ruff（改动文件） | 0 错（`hedge_order_executor.py` 的 I001 为改动前既有，import 块未触碰） |
| `ruff_incremental_gate.py`（4 文件） | exit 0（零新增） |
| `industrial_grade_check.py` | 11 PASS / 1 WARN / 0 FAIL（C1 WARN = xtquant 未装，符合预期） |
| `assert_data_validity.py` | 11 PASS / 1 FAIL（D1 真实 FAIL，见下） |
| `engineering_debt_gate.py` | T1-T5 OK；T6 66 处宽捕获 RED（扫描仅 utils/scripts/quant_modules/ai_decision，不含本次改动文件）+ D11 Phase B shadow 未满 |

## 未修 / 边界声明（诚实记录）

- **D1 压力测试空仓 FAIL**：`assert_data_validity.py` 用 `sorted(glob('stress_test_*.json'), reverse=True)[0]`
  取"最新"，被 `stress_test_SIMULATED_*.json`（`'S' > '2'`）恒占首位 → 读到 `is_simulated=true` 即跳过 → 假 PASS 之外
  还会遮盖真 FAIL；真实最新 `stress_test_20260909.json` 4 场景 actual_pnl 全 0（空仓）。属独立项，本次未动。
- **ms_strategy/scripts/live_scheduler.py 存在同源缺陷**（另有单测 patch 依赖 `_is_running`/`_write_lock`），
  本次只修生产入口（根目录 `live_scheduler.py`，Dockerfile CMD / K8s command）。
  两份副本的收敛属批次三「重复实现归并」范畴，未擅自合并以免破坏既有测试契约。
- **P1-6 密钥轮换**仍待用户操作（llmkey.txt 已删，`.env` 内容轮换属人工动作）。
