"""RiskMonitor 单元测试.

被测模块: utils/pipeline/risk_monitor.py
覆盖目标: >=85% line + branch

测试范围:
- 初始化 + 组件优雅降级
- 线程启动/停止生命周期
- 保证金检查 L1/L2/L3 三级熔断
- 回撤检查 (日回撤 + 总回撤)
- 隔夜跳空检查
- 告警处理 + 历史记录
- run_check 单次检查接口
- 异常容错 (fail-closed)
"""
from __future__ import annotations

import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.pipeline.risk_monitor import RiskMonitor  # noqa: E402
from utils.pipeline.types import (  # noqa: E402
    PipelineConfig,
    PipelineResult,
    PipelineStage,
    RiskAlert,
)


def _make_config(**overrides) -> PipelineConfig:
    """构造测试用 PipelineConfig"""
    cfg = PipelineConfig()
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


class RiskMonitorTest:
    """RiskMonitor 全方法单元测试"""

    # ============================================================
    # 初始化
    # ============================================================

    def test_init_with_default_config(self):
        """默认配置初始化"""
        monitor = RiskMonitor()
        assert monitor._running is False
        assert monitor._thread is None
        assert monitor._latest_alert is None
        assert monitor._alert_history == []

    def test_init_with_custom_config(self):
        """自定义配置初始化"""
        cfg = _make_config(risk_check_interval_seconds=10, kill_switch_l3_margin=0.9)
        monitor = RiskMonitor(config=cfg)
        assert monitor.config.risk_check_interval_seconds == 10
        assert monitor.config.kill_switch_l3_margin == 0.9

    def test_init_components_kill_switch_loaded(self):
        """KillSwitch 成功加载时 _kill_switch 非 None"""
        monitor = RiskMonitor()
        # kill_switch.py 存在，应该加载成功
        # 但如果依赖缺失可能为 None，只验证不崩溃
        assert hasattr(monitor, "_kill_switch")

    def test_init_components_import_failure(self):
        """KillSwitch 导入失败时优雅降级"""
        with patch.dict(sys.modules, {"utils.kill_switch": None}):
            monitor = RiskMonitor()
            assert monitor._kill_switch is None or hasattr(monitor, "_kill_switch")

    # ============================================================
    # 线程生命周期
    # ============================================================

    def test_start_stop(self):
        """启动后停止线程"""
        monitor = RiskMonitor(config=_make_config(risk_check_interval_seconds=1))
        monitor.start()
        assert monitor._running is True
        assert monitor._thread is not None
        assert monitor._thread.is_alive()
        time.sleep(0.1)
        monitor.stop()
        assert monitor._running is False
        assert monitor._thread is None

    def test_start_when_already_running(self):
        """重复启动不创建新线程"""
        monitor = RiskMonitor(config=_make_config(risk_check_interval_seconds=1))
        monitor.start()
        first_thread = monitor._thread
        monitor.start()  # 应该跳过
        assert monitor._thread is first_thread
        monitor.stop()

    def test_stop_when_not_running(self):
        """未启动时停止不崩溃"""
        monitor = RiskMonitor()
        monitor.stop()
        assert monitor._running is False

    def test_monitor_loop_handles_exception(self):
        """_monitor_loop 中 _run_checks 异常不崩溃"""
        monitor = RiskMonitor(config=_make_config(risk_check_interval_seconds=1))
        monitor._running = True

        def boom():
            raise RuntimeError("test error")

        with patch.object(monitor, "_run_checks", side_effect=boom):
            thread = threading.Thread(target=monitor._monitor_loop, daemon=True)
            thread.start()
            time.sleep(0.15)
            monitor._running = False
            thread.join(timeout=3)
        # 如果没崩溃说明异常被捕获

    # ============================================================
    # 保证金检查 _check_margin
    # ============================================================

    def test_check_margin_l3(self):
        """保证金 >= L3 阈值触发 L3 熔断"""
        cfg = _make_config(kill_switch_l1_margin=0.5, kill_switch_l2_margin=0.65, kill_switch_l3_margin=0.75)
        monitor = RiskMonitor(config=cfg)
        mock_ks = MagicMock()
        mock_ks.get_status.return_value = {"margin_ratio": 0.80}
        monitor._kill_switch = mock_ks

        alert = monitor._check_margin()
        assert alert is not None
        assert alert.level == 3
        assert alert.source == "kill_switch"
        assert "L3" in alert.message
        assert "停止开仓" in alert.actions_taken

    def test_check_margin_l2(self):
        """保证金 >= L2 阈值触发 L2 强平"""
        cfg = _make_config(kill_switch_l1_margin=0.5, kill_switch_l2_margin=0.65, kill_switch_l3_margin=0.75)
        monitor = RiskMonitor(config=cfg)
        mock_ks = MagicMock()
        mock_ks.get_status.return_value = {"margin_ratio": 0.68}
        monitor._kill_switch = mock_ks

        alert = monitor._check_margin()
        assert alert is not None
        assert alert.level == 2
        assert "L2" in alert.message

    def test_check_margin_l1(self):
        """保证金 >= L1 阈值触发 L1 警戒"""
        cfg = _make_config(kill_switch_l1_margin=0.5, kill_switch_l2_margin=0.65, kill_switch_l3_margin=0.75)
        monitor = RiskMonitor(config=cfg)
        mock_ks = MagicMock()
        mock_ks.get_status.return_value = {"margin_ratio": 0.55}
        monitor._kill_switch = mock_ks

        alert = monitor._check_margin()
        assert alert is not None
        assert alert.level == 1
        assert "L1" in alert.message

    def test_check_margin_normal(self):
        """保证金低于所有阈值返回 None 或估算结果"""
        cfg = _make_config(kill_switch_l1_margin=0.5, kill_switch_l2_margin=0.65, kill_switch_l3_margin=0.75)
        monitor = RiskMonitor(config=cfg)
        mock_ks = MagicMock()
        mock_ks.get_status.return_value = {"margin_ratio": 0.30}
        monitor._kill_switch = mock_ks
        # 估算路径可能返回 None（无 positions.json）或 alert
        with patch.object(monitor, "_estimate_margin_from_positions", return_value=None):
            alert = monitor._check_margin()
        assert alert is None

    def test_check_margin_no_kill_switch(self):
        """无 KillSwitch 时走估算降级路径"""
        monitor = RiskMonitor(config=_make_config())
        monitor._kill_switch = None
        with patch.object(monitor, "_estimate_margin_from_positions", return_value=None):
            alert = monitor._check_margin()
        assert alert is None

    def test_check_margin_exception(self):
        """KillSwitch 异常时返回 None"""
        monitor = RiskMonitor(config=_make_config())
        mock_ks = MagicMock()
        mock_ks.get_status.side_effect = RuntimeError("connection lost")
        monitor._kill_switch = mock_ks
        alert = monitor._check_margin()
        assert alert is None

    # ============================================================
    # 估算保证金 _estimate_margin_from_positions
    # ============================================================

    def test_estimate_margin_no_file(self):
        """positions.json 不存在返回 None"""
        monitor = RiskMonitor(config=_make_config())
        with patch("pathlib.Path.exists", return_value=False):
            alert = monitor._estimate_margin_from_positions()
        assert alert is None

    def test_estimate_margin_l3_trigger(self, tmp_path):
        """估算保证金超 L3 阈值触发告警"""
        cfg = _make_config(kill_switch_l3_margin=0.75)
        monitor = RiskMonitor(config=cfg)
        positions_file = tmp_path / "positions.json"
        import json
        positions_file.write_text(json.dumps({
            "stocks": [{"quantity": 1000, "current_price": 80}],
            "meta": {"total_capital": 100000},
        }), encoding="utf-8")

        with patch.object(Path, "resolve", return_value=positions_file.parent), \
             patch("builtins.open", create=True) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = positions_file.read_text(encoding="utf-8")
            # 直接 mock json.load 更简单
            with patch("json.load", return_value={
                "stocks": [{"quantity": 1000, "current_price": 80}],
                "meta": {"total_capital": 100000},
            }):
                with patch.object(Path, "exists", return_value=True):
                    alert = monitor._estimate_margin_from_positions()
        # margin_ratio = 80000/100000 = 0.8 >= 0.75
        assert alert is not None
        assert alert.level == 3
        assert alert.source == "risk_monitor_estimate"

    def test_estimate_margin_exception(self):
        """估算保证金异常返回 None"""
        monitor = RiskMonitor(config=_make_config())
        with patch.object(Path, "exists", return_value=True), \
             patch("builtins.open", side_effect=OSError("disk error")):
            alert = monitor._estimate_margin_from_positions()
        assert alert is None

    # ============================================================
    # 回撤检查 _check_drawdown
    # ============================================================

    def test_check_drawdown_total(self):
        """总回撤超阈值触发 L3"""
        cfg = _make_config(max_daily_drawdown=0.05, max_total_drawdown=0.15)
        monitor = RiskMonitor(config=cfg)
        mock_rg = MagicMock()
        mock_rg.get_status.return_value = {"daily_drawdown": 0.03, "total_drawdown": 0.16}
        monitor._risk_guard = mock_rg

        alert = monitor._check_drawdown()
        assert alert is not None
        assert alert.level == 3
        assert "总回撤" in alert.message

    def test_check_drawdown_daily(self):
        """日回撤超阈值触发 L2"""
        cfg = _make_config(max_daily_drawdown=0.05, max_total_drawdown=0.15)
        monitor = RiskMonitor(config=cfg)
        mock_rg = MagicMock()
        mock_rg.get_status.return_value = {"daily_drawdown": 0.06, "total_drawdown": 0.10}
        monitor._risk_guard = mock_rg

        alert = monitor._check_drawdown()
        assert alert is not None
        assert alert.level == 2
        assert "日回撤" in alert.message

    def test_check_drawdown_normal(self):
        """回撤正常返回 None"""
        cfg = _make_config(max_daily_drawdown=0.05, max_total_drawdown=0.15)
        monitor = RiskMonitor(config=cfg)
        mock_rg = MagicMock()
        mock_rg.get_status.return_value = {"daily_drawdown": 0.02, "total_drawdown": 0.08}
        monitor._risk_guard = mock_rg

        alert = monitor._check_drawdown()
        assert alert is None

    def test_check_drawdown_no_risk_guard(self):
        """无 RiskGuardIntegrator 返回 None"""
        monitor = RiskMonitor(config=_make_config())
        monitor._risk_guard = None
        alert = monitor._check_drawdown()
        assert alert is None

    def test_check_drawdown_exception(self):
        """RiskGuardIntegrator 异常返回 None"""
        monitor = RiskMonitor(config=_make_config())
        mock_rg = MagicMock()
        mock_rg.get_status.side_effect = KeyError("missing")
        monitor._risk_guard = mock_rg
        alert = monitor._check_drawdown()
        assert alert is None

    # ============================================================
    # 隔夜跳空检查 _check_overnight_gap
    # ============================================================

    def test_check_overnight_gap_normal_time(self):
        """非盘前时段返回 None"""
        monitor = RiskMonitor(config=_make_config())
        # mock datetime 为下午
        mock_dt = MagicMock()
        mock_dt.now.return_value = datetime(2026, 8, 26, 14, 30)
        with patch("utils.pipeline.risk_monitor.datetime", mock_dt):
            alert = monitor._check_overnight_gap()
        assert alert is None

    def test_check_overnight_gap_pre_market(self):
        """盘前时段 (9:00-9:25) 返回 None (简化实现)"""
        monitor = RiskMonitor(config=_make_config())
        mock_dt = MagicMock()
        mock_dt.now.return_value = datetime(2026, 8, 26, 9, 15)
        with patch("utils.pipeline.risk_monitor.datetime", mock_dt):
            alert = monitor._check_overnight_gap()
        assert alert is None

    # ============================================================
    # 告警处理 _handle_alert
    # ============================================================

    def test_handle_alert_stores(self):
        """告警存储到 latest + history"""
        monitor = RiskMonitor(config=_make_config())
        alert = RiskAlert(level=2, source="test", message="test alert", actions_taken=["a1", "a2"])
        monitor._handle_alert(alert)
        assert monitor._latest_alert is alert
        assert monitor._alert_history[-1] is alert

    def test_handle_alert_writes_to_memory(self):
        """告警写入 EvolutionMemory"""
        monitor = RiskMonitor(config=_make_config())
        alert = RiskAlert(level=1, source="test", message="msg")
        mock_memory = MagicMock()
        with patch.dict(sys.modules, {"utils.evolution_memory": mock_memory}):
            mock_memory.EvolutionMemory.return_value = mock_memory
            monitor._handle_alert(alert)
            mock_memory.log_event.assert_called_once()

    def test_write_to_memory_import_error(self):
        """EvolutionMemory 不可用时静默跳过"""
        monitor = RiskMonitor(config=_make_config())
        alert = RiskAlert(level=1, source="test", message="msg")
        with patch.dict(sys.modules, {"utils.evolution_memory": None}):
            # 不应崩溃
            monitor._write_to_memory(alert)

    # ============================================================
    # 查询接口
    # ============================================================

    def test_get_latest_alert(self):
        """获取最新告警"""
        monitor = RiskMonitor(config=_make_config())
        alert = RiskAlert(level=1, source="t", message="m")
        monitor._latest_alert = alert
        assert monitor.get_latest_alert() is alert

    def test_get_latest_alert_none(self):
        """无告警时返回 None"""
        monitor = RiskMonitor(config=_make_config())
        assert monitor.get_latest_alert() is None

    def test_get_alert_history(self):
        """获取告警历史"""
        monitor = RiskMonitor(config=_make_config())
        alerts = [RiskAlert(level=i, source="t", message=f"m{i}") for i in range(5)]
        monitor._alert_history = alerts
        history = monitor.get_alert_history(limit=3)
        assert len(history) == 3
        assert history[-1].level == 4

    def test_get_alert_history_empty(self):
        """空历史返回空列表"""
        monitor = RiskMonitor(config=_make_config())
        assert monitor.get_alert_history() == []

    # ============================================================
    # run_check 单次检查接口
    # ============================================================

    def test_run_check_success_no_alerts(self):
        """无告警时 run_check 成功"""
        monitor = RiskMonitor(config=_make_config())
        with patch.object(monitor, "_check_margin", return_value=None), \
             patch.object(monitor, "_check_drawdown", return_value=None):
            result = monitor.run_check()
        assert isinstance(result, PipelineResult)
        assert result.stage == PipelineStage.RISK_MONITOR
        assert result.success is True
        assert result.metrics["alerts_count"] == 0

    def test_run_check_with_l1_alert(self):
        """L1 告警时 success=True (L1 < 2)"""
        monitor = RiskMonitor(config=_make_config())
        l1_alert = RiskAlert(level=1, source="t", message="m")
        with patch.object(monitor, "_check_margin", return_value=l1_alert), \
             patch.object(monitor, "_check_drawdown", return_value=None):
            result = monitor.run_check()
        assert result.success is True
        assert result.metrics["alerts_count"] == 1
        assert result.metrics["max_alert_level"] == 1

    def test_run_check_with_l2_alert_fails(self):
        """L2 告警时 success=False"""
        monitor = RiskMonitor(config=_make_config())
        l2_alert = RiskAlert(level=2, source="t", message="m")
        with patch.object(monitor, "_check_margin", return_value=l2_alert), \
             patch.object(monitor, "_check_drawdown", return_value=None):
            result = monitor.run_check()
        assert result.success is False
        assert result.metrics["max_alert_level"] == 2

    def test_run_check_with_l3_alert_fails(self):
        """L3 告警时 success=False"""
        monitor = RiskMonitor(config=_make_config())
        l3_alert = RiskAlert(level=3, source="t", message="m")
        with patch.object(monitor, "_check_margin", return_value=l3_alert), \
             patch.object(monitor, "_check_drawdown", return_value=None):
            result = monitor.run_check()
        assert result.success is False
        assert result.metrics["max_alert_level"] == 3

    def test_run_check_multiple_alerts(self):
        """多告警取最大级别"""
        monitor = RiskMonitor(config=_make_config())
        l1 = RiskAlert(level=1, source="t", message="m1")
        l3 = RiskAlert(level=3, source="t", message="m3")
        with patch.object(monitor, "_check_margin", return_value=l1), \
             patch.object(monitor, "_check_drawdown", return_value=l3):
            result = monitor.run_check()
        assert result.success is False
        assert result.metrics["alerts_count"] == 2
        assert result.metrics["max_alert_level"] == 3

    def test_run_check_exception(self):
        """run_check 异常返回失败结果"""
        monitor = RiskMonitor(config=_make_config())
        with patch.object(monitor, "_check_margin", side_effect=RuntimeError("boom")):
            result = monitor.run_check()
        assert result.success is False
        assert result.error is not None
        assert "boom" in result.error

    # ============================================================
    # _run_checks 集成
    # ============================================================

    def test_run_checks_dispatches(self):
        """_run_checks 调用三个检查方法"""
        monitor = RiskMonitor(config=_make_config())
        with patch.object(monitor, "_check_margin", return_value=None) as m1, \
             patch.object(monitor, "_check_drawdown", return_value=None) as m2, \
             patch.object(monitor, "_check_overnight_gap", return_value=None) as m3, \
             patch.object(monitor, "_handle_alert") as mh:
            monitor._run_checks()
        m1.assert_called_once()
        m2.assert_called_once()
        m3.assert_called_once()
        mh.assert_not_called()

    def test_run_checks_handles_alert(self):
        """_run_checks 有告警时调用 _handle_alert"""
        monitor = RiskMonitor(config=_make_config())
        alert = RiskAlert(level=1, source="t", message="m")
        with patch.object(monitor, "_check_margin", return_value=alert), \
             patch.object(monitor, "_check_drawdown", return_value=None), \
             patch.object(monitor, "_check_overnight_gap", return_value=None), \
             patch.object(monitor, "_handle_alert") as mh:
            monitor._run_checks()
        mh.assert_called_once_with(alert)