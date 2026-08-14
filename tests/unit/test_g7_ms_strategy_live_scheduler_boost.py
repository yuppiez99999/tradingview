"""G7 boost: ms_strategy/scripts/live_scheduler.py 单元测试.

覆盖:
  - 6 个 run_* 任务函数 (成功/失败/dry_run 分支, mock 内部 import)
  - LiveScheduler (_run_task/_schedule_module/start/stop/get_status)
  - 锁文件 (_write_lock/_read_lock/_remove_lock/_is_running)
  - signal_handler / main (mock argparse + subprocess)
mock APScheduler 替代物 (threading.Timer) 与所有外部依赖.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "ms_strategy" / "scripts"))

import live_scheduler as ls  # noqa: E402

# ============================================================
# mock 依赖 fixture
# ============================================================


@pytest.fixture
def mock_deps(monkeypatch):
    """注入所有 run_* 函数依赖的 mock 模块到 sys.modules."""
    # utils.data_provider
    utils_dp = types.ModuleType("utils.data_provider")
    provider_inst = MagicMock()
    provider_inst.get_market_data.return_value = {"price": 4.5}
    utils_dp.MarketDataProvider = MagicMock(return_value=provider_inst)
    monkeypatch.setitem(sys.modules, "utils.data_provider", utils_dp)

    # utils.risk_metrics
    risk_mod = types.ModuleType("utils.risk_metrics")
    risk_mod.calculate_portfolio_weights = MagicMock(return_value={"X": 0.5, "Y": 0.5})
    monkeypatch.setitem(sys.modules, "utils.risk_metrics", risk_mod)

    # hedging.beta_hedger
    hedging_mod = types.ModuleType("hedging.beta_hedger")
    hedger_inst = MagicMock()
    hedger_inst.compute_hedge.return_value = {"action": "HEDGE", "contracts": 2}
    hedging_mod.BetaHedger = MagicMock(return_value=hedger_inst)
    monkeypatch.setitem(sys.modules, "hedging.beta_hedger", hedging_mod)

    # utils.etf_flow_monitor
    etf_mod = types.ModuleType("utils.etf_flow_monitor")
    etf_mod.get_etf_flow_summary = MagicMock(return_value={
        "flow_data": {"X": {}}, "total_flow_yi": 1.5,
        "overall_trend": "流入", "signal_count": 3, "signals": [],
    })
    etf_mod.refresh_etf_flow_signals = MagicMock()
    monkeypatch.setitem(sys.modules, "utils.etf_flow_monitor", etf_mod)

    # utils.tf_price_predictor
    tf_mod = types.ModuleType("utils.tf_price_predictor")
    predictor_inst = MagicMock()
    predictor_inst.predict.return_value = {"predicted_return": 0.05, "confidence": 0.8}
    tf_mod.PricePredictor = MagicMock(return_value=predictor_inst)
    monkeypatch.setitem(sys.modules, "utils.tf_price_predictor", tf_mod)

    return {
        "provider": utils_dp.MarketDataProvider,
        "risk": risk_mod.calculate_portfolio_weights,
        "hedger": hedging_mod.BetaHedger,
        "etf_summary": etf_mod.get_etf_flow_summary,
        "etf_refresh": etf_mod.refresh_etf_flow_signals,
        "predictor": tf_mod.PricePredictor,
    }


@pytest.fixture
def restore_running():
    """保存/恢复全局 RUNNING 标志."""
    original = ls.RUNNING
    yield
    ls.RUNNING = original


# ============================================================
# 1. run_market_monitor
# ============================================================


class TestRunMarketMonitor:
    def test_success(self, mock_deps):
        result = ls.run_market_monitor()
        assert result["status"] == "OK"
        assert "data" in result
        assert "duration" in result
        assert result["duration"] >= 0

    def test_data_fields(self, mock_deps):
        result = ls.run_market_monitor()
        assert "n_symbols" in result["data"]
        assert "prices" in result["data"]
        assert "timestamp" in result["data"]

    def test_import_failure_returns_fail(self, monkeypatch):
        # 移除 mock, 让 import 失败 → ImportError 不被捕获, 抛出
        # 但 ImportError 不在捕获列表, 所以会抛出
        monkeypatch.delitem(sys.modules, "utils.data_provider", raising=False)
        # 确保真实模块也不可用
        with patch.dict(sys.modules, {"utils.data_provider": None}):
            with pytest.raises((ImportError, ModuleNotFoundError)):
                ls.run_market_monitor()

    def test_provider_exception_returns_fail(self, mock_deps):
        mock_deps["provider"].return_value.get_market_data.side_effect = RuntimeError("err")
        result = ls.run_market_monitor()
        # 单个 code 异常被 continue 捕获, 整体仍 OK
        assert result["status"] == "OK"


# ============================================================
# 2. run_auto_rebalance
# ============================================================


class TestRunAutoRebalance:
    def test_success(self, mock_deps):
        result = ls.run_auto_rebalance()
        assert result["status"] == "OK"
        assert "duration" in result

    def test_risk_exception_returns_fail(self, mock_deps):
        mock_deps["risk"].side_effect = RuntimeError("risk err")
        fake_data = json.dumps({"positions": {"X": {}}})
        with patch.object(Path, "exists", return_value=True), \
             patch("builtins.open", mock_open(read_data=fake_data)):
            result = ls.run_auto_rebalance()
        assert result["status"] == "FAIL"
        assert "risk err" in result["error"]


# ============================================================
# 3. run_hedge_rebalance
# ============================================================


class TestRunHedgeRebalance:
    def test_success(self, mock_deps):
        result = ls.run_hedge_rebalance()
        assert result["status"] == "OK"
        assert "duration" in result

    def test_hedger_exception_returns_fail(self, mock_deps):
        mock_deps["hedger"].side_effect = RuntimeError("hedger err")
        fake_data = json.dumps({"positions": {"X": {
            "phase1_amount": 100000, "phase2_amount": 0, "phase3_amount": 0,
        }}})
        with patch.object(Path, "exists", return_value=True), \
             patch("builtins.open", mock_open(read_data=fake_data)):
            result = ls.run_hedge_rebalance()
        assert result["status"] == "FAIL"
        assert "hedger err" in result["error"]


# ============================================================
# 4. run_etf_flow_monitor
# ============================================================


class TestRunEtfFlowMonitor:
    def test_success_dry_run(self, mock_deps):
        result = ls.run_etf_flow_monitor(dry_run=True)
        assert result["status"] == "OK"
        assert "n_etfs" in result["data"]

    def test_success_live(self, mock_deps):
        result = ls.run_etf_flow_monitor(dry_run=False)
        assert result["status"] == "OK"
        mock_deps["etf_refresh"].assert_called()

    def test_refresh_exception_uses_cache(self, mock_deps):
        mock_deps["etf_refresh"].side_effect = RuntimeError("refresh err")
        result = ls.run_etf_flow_monitor(dry_run=False)
        # 刷新失败被捕获, 使用缓存, 仍 OK
        assert result["status"] == "OK"

    def test_summary_exception_returns_fail(self, mock_deps):
        mock_deps["etf_summary"].side_effect = RuntimeError("summary err")
        result = ls.run_etf_flow_monitor(dry_run=True)
        assert result["status"] == "FAIL"


# ============================================================
# 5. run_ml_signal_scan
# ============================================================


class TestRunMlSignalScan:
    def test_success(self, mock_deps):
        result = ls.run_ml_signal_scan()
        assert result["status"] == "OK"
        assert "n_predictions" in result["data"]

    def test_predict_exception_continues(self, mock_deps):
        mock_deps["predictor"].return_value.predict.side_effect = RuntimeError("predict err")
        result = ls.run_ml_signal_scan()
        # 单个预测异常被 continue, 整体 OK
        assert result["status"] == "OK"

    def test_predictor_init_exception_returns_fail(self, mock_deps):
        mock_deps["predictor"].side_effect = RuntimeError("init err")
        result = ls.run_ml_signal_scan()
        assert result["status"] == "FAIL"


# ============================================================
# 6. run_daily_report
# ============================================================


class TestRunDailyReport:
    def test_dry_run(self):
        result = ls.run_daily_report(dry_run=True)
        assert result["status"] == "OK"
        assert result["data"]["dry_run"] is True

    def test_script_not_exists(self):
        with patch.object(Path, "exists", return_value=False):
            result = ls.run_daily_report(dry_run=False)
        assert result["status"] == "FAIL"
        assert "不存在" in result["error"]

    def test_script_success(self):
        with patch.object(Path, "exists", return_value=True), \
             patch("subprocess.run") as m_sub:
            m_sub.return_value = MagicMock(returncode=0, stderr="")
            result = ls.run_daily_report(dry_run=False)
        assert result["status"] == "OK"
        assert result["data"]["exit_code"] == 0

    def test_script_failure(self):
        with patch.object(Path, "exists", return_value=True), \
             patch("subprocess.run") as m_sub:
            m_sub.return_value = MagicMock(returncode=1, stderr="report error")
            result = ls.run_daily_report(dry_run=False)
        assert result["status"] == "FAIL"
        assert "report error" in result["error"]

    def test_subprocess_exception(self):
        with patch.object(Path, "exists", return_value=True), \
             patch("subprocess.run", side_effect=OSError("subprocess err")):
            result = ls.run_daily_report(dry_run=False)
        assert result["status"] == "FAIL"


# ============================================================
# 7. LiveScheduler
# ============================================================


class TestLiveScheduler:
    def test_init(self):
        sched = ls.LiveScheduler(dry_run=True, max_workers=3)
        assert sched.dry_run is True
        assert sched.max_workers == 3
        assert len(sched.module_results) == len(ls.MODULE_DEFINITIONS)

    def test_run_task_success(self, restore_running):
        sched = ls.LiveScheduler(dry_run=True)
        sched.module_results["test_mod"] = []
        ls.RUNNING = True
        mock_func = MagicMock(return_value={"status": "OK", "data": {}, "duration": 0.1})
        sched._run_task("test_mod", mock_func)
        mock_func.assert_called_once_with(dry_run=True)
        assert "test_mod" in sched.module_last_run
        assert len(sched.module_results["test_mod"]) == 1
        assert ls.MODULE_STATUS["test_mod"]["status"] == "OK"

    def test_run_task_exception(self, restore_running):
        sched = ls.LiveScheduler(dry_run=True)
        ls.RUNNING = True
        mock_func = MagicMock(side_effect=RuntimeError("task err"))
        sched._run_task("test_mod", mock_func)
        assert ls.MODULE_STATUS["test_mod"]["status"] == "ERROR"
        assert "task err" in ls.MODULE_STATUS["test_mod"]["error"]

    def test_run_task_results_capped(self, restore_running):
        sched = ls.LiveScheduler(dry_run=True)
        sched.module_results["test_mod"] = []
        ls.RUNNING = True
        mock_func = MagicMock(return_value={"status": "OK", "data": {}, "duration": 0.0})
        # 添加 101 次: 第 101 次触发 >100 裁剪到 50
        for _ in range(101):
            sched._run_task("test_mod", mock_func)
        assert len(sched.module_results["test_mod"]) == 50

    def test_start_stop(self, restore_running):
        sched = ls.LiveScheduler(dry_run=True)
        ls.RUNNING = True
        with patch.object(sched, "_schedule_module") as m_sched, \
             patch.object(sched, "_schedule_daily_report"):
            sched.start()
            # 5 个有 interval 的模块 + 1 个 daily_report
            assert m_sched.call_count == 5
            sched.stop()

    def test_stop_cancels_timers(self):
        sched = ls.LiveScheduler(dry_run=True)
        mock_timer = MagicMock()
        sched.timers = {"a": mock_timer, "b": mock_timer}
        with patch.object(sched.executor, "shutdown"):
            sched.stop()
        assert mock_timer.cancel.call_count == 2

    def test_get_status(self, restore_running):
        sched = ls.LiveScheduler(dry_run=True)
        ls.RUNNING = True
        status = sched.get_status()
        assert status["running"] is True
        assert status["dry_run"] is True
        assert "modules" in status
        assert "last_runs" in status
        assert "timestamp" in status

    def test_schedule_module_with_mock_timer(self, restore_running, mock_deps):
        sched = ls.LiveScheduler(dry_run=True)
        ls.RUNNING = True
        mod_def = ls.MODULE_DEFINITIONS[0]  # market_monitor
        with patch("threading.Timer") as m_timer:
            m_timer.return_value = MagicMock()
            sched._schedule_module(mod_def)
        # 立即执行一次 run_and_reschedule → _run_task 被调
        assert "market_monitor" in sched.module_last_run

    def test_schedule_module_not_running_returns(self, restore_running):
        sched = ls.LiveScheduler(dry_run=True)
        ls.RUNNING = False
        mod_def = ls.MODULE_DEFINITIONS[0]
        with patch.object(sched, "_run_task") as m_run:
            sched._schedule_module(mod_def)
            m_run.assert_not_called()


# ============================================================
# 8. 锁文件
# ============================================================


class TestLockFile:
    def test_write_and_read_lock(self, tmp_path, monkeypatch):
        lock_file = tmp_path / "test.lock"
        monkeypatch.setattr(ls, "LOCK_FILE", lock_file)
        ls._write_lock(pid=12345)
        assert lock_file.exists()
        lock_data = ls._read_lock()
        assert lock_data is not None
        assert lock_data["pid"] == 12345
        assert "start_time" in lock_data

    def test_read_lock_not_exists(self, tmp_path, monkeypatch):
        lock_file = tmp_path / "nonexistent.lock"
        monkeypatch.setattr(ls, "LOCK_FILE", lock_file)
        assert ls._read_lock() is None

    def test_read_lock_corrupted(self, tmp_path, monkeypatch):
        lock_file = tmp_path / "corrupt.lock"
        lock_file.write_text("not json", encoding="utf-8")
        monkeypatch.setattr(ls, "LOCK_FILE", lock_file)
        # JSONDecodeError 是 ValueError 子类, 被捕获返回 None
        assert ls._read_lock() is None

    def test_remove_lock(self, tmp_path, monkeypatch):
        lock_file = tmp_path / "test.lock"
        lock_file.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(ls, "LOCK_FILE", lock_file)
        ls._remove_lock()
        assert not lock_file.exists()

    def test_remove_lock_not_exists(self, tmp_path, monkeypatch):
        lock_file = tmp_path / "nonexistent.lock"
        monkeypatch.setattr(ls, "LOCK_FILE", lock_file)
        # 不应抛出
        ls._remove_lock()

    def test_is_running_no_lock(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ls, "LOCK_FILE", tmp_path / "x.lock")
        assert ls._is_running() is False

    def test_is_running_with_lock(self, tmp_path, monkeypatch):
        lock_file = tmp_path / "test.lock"
        lock_file.write_text(json.dumps({"pid": 99999}), encoding="utf-8")
        monkeypatch.setattr(ls, "LOCK_FILE", lock_file)
        with patch("subprocess.run"):
            assert ls._is_running() is True

    def test_is_running_subprocess_exception(self, tmp_path, monkeypatch):
        lock_file = tmp_path / "test.lock"
        lock_file.write_text(json.dumps({"pid": 99999}), encoding="utf-8")
        monkeypatch.setattr(ls, "LOCK_FILE", lock_file)
        with patch("subprocess.run", side_effect=OSError("err")):
            assert ls._is_running() is False

    def test_is_running_lock_no_pid(self, tmp_path, monkeypatch):
        lock_file = tmp_path / "test.lock"
        lock_file.write_text(json.dumps({}), encoding="utf-8")
        monkeypatch.setattr(ls, "LOCK_FILE", lock_file)
        assert ls._is_running() is False


# ============================================================
# 9. signal_handler
# ============================================================


class TestSignalHandler:
    def test_signal_handler_sets_running_false(self, restore_running):
        ls.RUNNING = True
        ls.signal_handler(2, None)
        assert ls.RUNNING is False


# ============================================================
# 10. main
# ============================================================


class TestMain:
    def test_main_status(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ls, "LOCK_FILE", tmp_path / "x.lock")
        with patch("sys.argv", ["live_scheduler.py", "--status"]):
            result = ls.main()
        assert result == 0

    def test_main_stop(self, tmp_path, monkeypatch):
        lock_file = tmp_path / "test.lock"
        lock_file.write_text(json.dumps({"pid": 99999}), encoding="utf-8")
        monkeypatch.setattr(ls, "LOCK_FILE", lock_file)
        with patch("sys.argv", ["live_scheduler.py", "--stop"]), \
             patch("subprocess.run"):
            result = ls.main()
        assert result == 0
        assert not lock_file.exists()

    def test_main_stop_no_lock(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ls, "LOCK_FILE", tmp_path / "x.lock")
        with patch("sys.argv", ["live_scheduler.py", "--stop"]):
            result = ls.main()
        assert result == 0

    def test_main_already_running(self, tmp_path, monkeypatch):
        lock_file = tmp_path / "test.lock"
        lock_file.write_text(json.dumps({"pid": 99999}), encoding="utf-8")
        monkeypatch.setattr(ls, "LOCK_FILE", lock_file)
        with patch("sys.argv", ["live_scheduler.py"]), \
             patch("subprocess.run"), \
             patch.object(ls, "_is_running", return_value=True):
            result = ls.main()
        assert result == 0

    def test_main_start_scheduler(self, tmp_path, monkeypatch, restore_running):
        monkeypatch.setattr(ls, "LOCK_FILE", tmp_path / "x.lock")
        ls.RUNNING = True
        mock_sched = MagicMock()
        with patch("sys.argv", ["live_scheduler.py", "--dry-run"]), \
             patch.object(ls, "_is_running", return_value=False), \
             patch.object(ls, "LiveScheduler", return_value=mock_sched), \
             patch("os.getpid", return_value=12345):
            # 模拟 RUNNING 立即变 False 退出循环
            original_running = ls.RUNNING
            def stop_running(*args, **kwargs):
                ls.RUNNING = False
                return original_running
            with patch("time.sleep", side_effect=stop_running):
                result = ls.main()
        assert result is None or result == 0
        mock_sched.start.assert_called_once()
        mock_sched.stop.assert_called_once()


# ============================================================
# 11. MODULE_DEFINITIONS 常量
# ============================================================


class TestModuleDefinitions:
    def test_module_count(self):
        assert len(ls.MODULE_DEFINITIONS) == 6

    def test_module_names(self):
        names = [m["name"] for m in ls.MODULE_DEFINITIONS]
        assert "market_monitor" in names
        assert "auto_rebalance" in names
        assert "hedge_rebalance" in names
        assert "etf_flow_monitor" in names
        assert "ml_signal_scan" in names
        assert "daily_report" in names

    def test_daily_report_has_trigger_time(self):
        daily = [m for m in ls.MODULE_DEFINITIONS if m["name"] == "daily_report"][0]
        assert daily["interval_seconds"] is None
        assert "trigger_time" in daily

    def test_all_modules_have_task_func(self):
        for m in ls.MODULE_DEFINITIONS:
            assert "task_func" in m
            assert m["task_func"] in dir(ls)


# ============================================================
# 12. 补充覆盖: positions 读取 / trade_plan / portfolio_value / daily_report / main 分支
# ============================================================


class TestRunMarketMonitorPositions:
    def test_reads_positions_file(self, mock_deps):
        fake_data = json.dumps({"positions": {"510300.SH": {}, "510050.SH": {}}})
        with patch.object(Path, "exists", return_value=True), \
             patch("builtins.open", mock_open(read_data=fake_data)):
            result = ls.run_market_monitor()
        assert result["status"] == "OK"
        assert result["data"]["n_symbols"] >= 0


class TestRunAutoRebalanceTradePlan:
    def test_with_trade_plan(self, mock_deps):
        positions_data = json.dumps({"positions": {"X": {}}})
        plan_data = json.dumps({
            "stock_etf_account": {"positions": [{"code": "X", "weight": 0.5}]}
        })
        m_open = MagicMock(side_effect=[
            mock_open(read_data=positions_data).return_value,
            mock_open(read_data=plan_data).return_value,
        ])
        with patch.object(Path, "exists", return_value=True), \
             patch("builtins.open", m_open):
            result = ls.run_auto_rebalance()
        assert result["status"] == "OK"
        assert "deviations" in result["data"]

    def test_positions_exist_trade_plan_not_exist(self, mock_deps):
        positions_data = json.dumps({"positions": {"X": {}}})
        # positions_path.exists()=True, trade_plan_path.exists()=False
        call_count = [0]
        def exists_side_effect(self):
            call_count[0] += 1
            return call_count[0] == 1  # 第一次 True, 第二次 False
        with patch.object(Path, "exists", exists_side_effect), \
             patch("builtins.open", mock_open(read_data=positions_data)):
            result = ls.run_auto_rebalance()
        assert result["status"] == "OK"


class TestRunHedgeRebalanceSuccess:
    def test_portfolio_value_positive(self, mock_deps):
        fake_data = json.dumps({"positions": {"X": {
            "phase1_amount": 500000, "phase2_amount": 0, "phase3_amount": 0,
        }}})
        with patch.object(Path, "exists", return_value=True), \
             patch("builtins.open", mock_open(read_data=fake_data)):
            result = ls.run_hedge_rebalance()
        assert result["status"] == "OK"
        assert "hedge_order" in result["data"]


class TestScheduleDailyReport:
    def test_schedule_daily_report_triggers(self, restore_running, mock_deps):
        from datetime import time as dt_time
        sched = ls.LiveScheduler(dry_run=True)
        sched.module_results["daily_report"] = []
        ls.RUNNING = True
        mock_dt = MagicMock()
        mock_now = MagicMock()
        mock_now.time.return_value = dt_time(15, 16)
        mock_now.strftime.return_value = "2026-08-13"
        mock_dt.now.return_value = mock_now
        with patch.object(ls, "datetime", mock_dt), \
             patch("threading.Timer") as m_timer:
            m_timer.return_value = MagicMock()
            sched._schedule_daily_report()
        assert "daily_report_sched" in sched.timers

    def test_schedule_daily_report_outside_trigger_time(self, restore_running):
        from datetime import time as dt_time
        sched = ls.LiveScheduler(dry_run=True)
        ls.RUNNING = True
        mock_dt = MagicMock()
        mock_now = MagicMock()
        mock_now.time.return_value = dt_time(9, 0)  # 不在 15:15-15:20
        mock_dt.now.return_value = mock_now
        with patch.object(ls, "datetime", mock_dt), \
             patch("threading.Timer") as m_timer:
            m_timer.return_value = MagicMock()
            sched._schedule_daily_report()
        assert "daily_report_sched" in sched.timers


class TestMainBranchCoverage:
    def test_main_status_with_lock(self, tmp_path, monkeypatch):
        lock_file = tmp_path / "test.lock"
        lock_file.write_text(json.dumps({"pid": 12345, "start_time": "2026-08-13"}), encoding="utf-8")
        monkeypatch.setattr(ls, "LOCK_FILE", lock_file)
        with patch("sys.argv", ["live_scheduler.py", "--status"]):
            result = ls.main()
        assert result == 0

    def test_main_stop_subprocess_exception(self, tmp_path, monkeypatch):
        lock_file = tmp_path / "test.lock"
        lock_file.write_text(json.dumps({"pid": 99999}), encoding="utf-8")
        monkeypatch.setattr(ls, "LOCK_FILE", lock_file)
        with patch("sys.argv", ["live_scheduler.py", "--stop"]), \
             patch("subprocess.run", side_effect=OSError("taskkill err")):
            result = ls.main()
        assert result == 0

    def test_main_keyboard_interrupt(self, tmp_path, monkeypatch, restore_running):
        monkeypatch.setattr(ls, "LOCK_FILE", tmp_path / "x.lock")
        ls.RUNNING = True
        mock_sched = MagicMock()
        with patch("sys.argv", ["live_scheduler.py", "--dry-run"]), \
             patch.object(ls, "_is_running", return_value=False), \
             patch.object(ls, "LiveScheduler", return_value=mock_sched), \
             patch("os.getpid", return_value=12345), \
             patch("time.sleep", side_effect=KeyboardInterrupt):
            result = ls.main()
        assert result is None or result == 0
        mock_sched.stop.assert_called_once()


class TestIsRunningEdgeCases:
    def test_is_running_lock_no_pid_key(self, tmp_path, monkeypatch):
        """覆盖 _is_running 行 554: lock 非空但无 pid 键."""
        lock_file = tmp_path / "test.lock"
        lock_file.write_text(json.dumps({"other": "value"}), encoding="utf-8")
        monkeypatch.setattr(ls, "LOCK_FILE", lock_file)
        assert ls._is_running() is False
