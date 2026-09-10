"""P2 修复回归测试 (2026-09-10, 代码质量审计报告_20260909 批次二 · 执行链 P2 收口).

覆盖三项此前会失败的行为:
  1. live_scheduler 单实例锁 TOCTOU → 原子获取 (_acquire_lock)
  2. live_scheduler 定时任务挂死即停摆 → 看门狗超时 (TIMEOUT 状态 + 调度链继续)
  3. hedge_order_executor "读对冲持仓后即丢弃" 死代码 → positions.json 回流真正接入
外加 daily_trade_executor 成本魔法数字 → 配置驱动 (缺省值与原硬编码逐位一致)。

门禁负向验证口径: 回滚任一修复后本文件对应用例必红。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import live_scheduler as ls  # noqa: E402
from hedge_order_executor import _collect_pending_orders  # noqa: E402


# ============================================================
# 1. 单实例锁原子获取 (原 check-then-write TOCTOU)
# ============================================================
def test_acquire_lock_is_atomic(monkeypatch, tmp_path):
    """第二次获取必须失败 —— 锁由第一个调用者独占持有。"""
    monkeypatch.setattr(ls, "LOCK_FILE", tmp_path / ".live_scheduler.lock")

    assert ls._acquire_lock() is True
    # 同一进程再次获取: 锁已存在且持有者 (本进程) 存活 → 必须拒绝
    assert ls._acquire_lock() is False

    payload = json.loads((tmp_path / ".live_scheduler.lock").read_text(encoding="utf-8"))
    assert payload["pid"] == os.getpid()


def test_acquire_lock_reclaims_stale_lock(monkeypatch, tmp_path):
    """stale 锁 (PID 已失效) 应被清理并重新获取成功。"""
    lock_file = tmp_path / ".live_scheduler.lock"
    monkeypatch.setattr(ls, "LOCK_FILE", lock_file)
    lock_file.write_text(
        json.dumps({"pid": 999999, "start_time": "2026-01-01T00:00:00"}), encoding="utf-8"
    )

    assert ls._acquire_lock() is True
    payload = json.loads(lock_file.read_text(encoding="utf-8"))
    assert payload["pid"] != 999999


def test_write_lock_removed_after_refactor(monkeypatch, tmp_path):
    """旧的非原子 _write_lock 已被 _acquire_lock 取代 (防止双重实现回潮)。"""
    monkeypatch.setattr(ls, "LOCK_FILE", tmp_path / ".live_scheduler.lock")
    assert not hasattr(ls, "_write_lock")
    assert hasattr(ls, "_acquire_lock")


# ============================================================
# 2. 任务看门狗超时 (原: 挂死即停摆)
# ============================================================
def test_run_task_times_out_without_blocking_schedule(monkeypatch):
    """任务超时必须快速返回并标记 TIMEOUT, 而不是永久阻塞调度线程。"""
    monkeypatch.setattr(ls, "DEFAULT_TASK_TIMEOUT", 0.1)
    scheduler = ls.LiveScheduler(dry_run=True, max_workers=1)

    def hang(dry_run: bool = False) -> dict:
        time.sleep(2.0)
        return {"status": "OK", "duration": 2.0}

    start = time.time()
    scheduler._run_task("unit_hang_module", hang)
    elapsed = time.time() - start

    assert elapsed < 1.5, f"看门狗未生效, 阻塞 {elapsed:.2f}s"
    assert ls.MODULE_STATUS["unit_hang_module"]["status"] == "TIMEOUT"
    assert ls.MODULE_STATUS["unit_hang_module"]["error"]

    scheduler.executor.shutdown(wait=False)


def test_run_task_records_result_and_survives_exception(monkeypatch):
    """正常任务写状态; 抛异常的任务被吸收 (不中断调度链) 且状态为 ERROR。"""
    scheduler = ls.LiveScheduler(dry_run=True, max_workers=1)

    scheduler._run_task(
        "unit_ok_module", lambda dry_run=False: {"status": "OK", "duration": 0.01}
    )
    assert ls.MODULE_STATUS["unit_ok_module"]["status"] == "OK"
    assert "unit_ok_module" in scheduler.module_last_run

    def boom(dry_run: bool = False) -> dict:
        raise RuntimeError("boom")

    scheduler._run_task("unit_boom_module", boom)  # 不应抛出
    assert ls.MODULE_STATUS["unit_boom_module"]["status"] == "ERROR"

    scheduler.executor.shutdown(wait=False)


def test_get_status_no_runtime_error_under_concurrency():
    """并发写入 module_results 时 get_status() 不得抛 dict changed size。"""
    scheduler = ls.LiveScheduler(dry_run=True, max_workers=2)

    stop = threading.Event()

    def writer(idx: int) -> None:
        while not stop.is_set():
            scheduler._record_module_result(
                f"unit_m{idx}", {"status": "OK", "duration": 0.0}
            )

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    try:
        for _ in range(200):
            status = scheduler.get_status()
            assert status["running"] is True
            # 快照必须是独立副本, 不被后续写入污染
            assert isinstance(status["modules"], dict)
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=5)

    scheduler.executor.shutdown(wait=False)


# ============================================================
# 3. hedge_order_executor positions.json 回流 (原死代码)
# ============================================================
def _pending_put(order_id: str, date: str) -> dict:
    return {
        "order_id": order_id,
        "date": date,
        "instrument": "510050 Put",
        "direction": "BUY_PUT",
        "contracts": 20,
        "status": "PENDING",
    }


def test_collect_pending_orders_uses_positions_fallback_same_date():
    """plan 无 active_orders 时, 同日期回流订单必须被收集 (原实现丢弃 → 漏单)。"""
    plan = {"hedge_execution": {}}
    fallback = {
        "date": "2026-08-04",
        "status": "PENDING_EXECUTION",
        "put_protection": [_pending_put("oid-1", "2026-08-04")],
    }

    orders = _collect_pending_orders(
        plan, fallback_active_orders=fallback, trade_date="2026-08-04"
    )
    assert [o["order_id"] for o in orders] == ["oid-1"]


def test_collect_pending_orders_skips_stale_fallback():
    """回流结构日期与执行日不一致时必须跳过, 防止执行陈旧对冲订单。"""
    plan = {"hedge_execution": {}}
    fallback = {
        "date": "2026-08-04",
        "put_protection": [_pending_put("oid-old", "2026-08-04")],
    }

    orders = _collect_pending_orders(
        plan, fallback_active_orders=fallback, trade_date="2026-09-10"
    )
    assert orders == []


def test_collect_pending_orders_dedups_plan_and_fallback():
    """plan 与回流含同一订单时必须按 order_id 去重 (不重复撮合)。"""
    order = _pending_put("oid-dup", "2026-09-10")
    plan = {"hedge_execution": {"options_orders": [dict(order)]}}
    fallback = {"date": "2026-09-10", "put_protection": [dict(order)]}

    orders = _collect_pending_orders(
        plan, fallback_active_orders=fallback, trade_date="2026-09-10"
    )
    assert len(orders) == 1


def test_collect_pending_orders_backward_compatible():
    """旧调用契约 (仅传 plan) 必须保持可用。"""
    plan = {"hedge_execution": {"options_orders": [_pending_put("oid-plan", "2026-09-10")]}}
    orders = _collect_pending_orders(plan)
    assert [o["order_id"] for o in orders] == ["oid-plan"]


# ============================================================
# 4. 成本参数配置驱动 (原滑点 10bp / ADV 100万硬编码)
# ============================================================
def test_cost_model_defaults_match_legacy_hardcode():
    """三级取值后的内置默认值必须与历史硬编码逐位一致 (行为零变化)。"""
    import daily_trade_executor as dte

    assert dte.SLIPPAGE_RATE == 0.001
    assert dte.COMMISSION_RATE == 0.0003
    assert dte.TRANSFER_FEE_RATE == 0.00001
    assert dte.STAMP_DUTY_RATE == 0.0005
    assert dte.WT_SPLIT_MIN_AMOUNT == 50000
    assert dte.WT_ASSUMED_ADV == 1000000

    # configs/ 被 .gitignore 排除 (事实源 = ROADMAP+LOG), 故 YAML 断言仅在本地存在时生效
    yaml_path = PROJECT_ROOT / "configs" / "trade_execution.yaml"
    if yaml_path.exists():
        assert "cost_model:" in yaml_path.read_text(encoding="utf-8")


def test_cost_param_priority_config_then_env_then_default(monkeypatch):
    """成本参数优先级: cost_model 段 > QUANT_<KEY> 环境变量 > 内置默认值。"""
    import daily_trade_executor as dte

    monkeypatch.setattr(dte, "_cost_model", {})
    monkeypatch.setenv("QUANT_SLIPPAGE_RATE", "0.002")
    assert dte._cost_param("slippage_rate", 0.001) == 0.002  # 环境变量命中

    monkeypatch.setattr(dte, "_cost_model", {"slippage_rate": 0.003})
    assert dte._cost_param("slippage_rate", 0.001) == 0.003  # 配置段优先于环境变量

    monkeypatch.setattr(dte, "_cost_model", {})
    monkeypatch.setenv("QUANT_SLIPPAGE_RATE", "abc")
    assert dte._cost_param("slippage_rate", 0.001) == 0.001  # 非法值回退默认

    monkeypatch.delenv("QUANT_SLIPPAGE_RATE")
    assert dte._cost_param("slippage_rate", 0.001) == 0.001
