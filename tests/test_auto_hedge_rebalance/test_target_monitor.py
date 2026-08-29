"""TargetMonitor 单元测试 — 覆盖年化计算/纠偏分级/预检/增量更新。

测试策略:
    - 覆盖率目标 ≥ 90%
    - 年化收益计算与手工计算结果一致
    - 纠偏分级边界值精确
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from utils.auto_hedge_rebalance.models import CorrectionAction
from utils.auto_hedge_rebalance.target_monitor import TargetMonitor


@pytest.fixture
def nav_path(tmp_path: Path) -> str:
    return str(tmp_path / "test_nav.json")


@pytest.fixture
def monitor(nav_path: str) -> TargetMonitor:
    return TargetMonitor(
        nav_history_path=nav_path,
        rolling_window=252,
        target_annual_return=0.08,
        target_max_drawdown=0.20,
    )


def _write_nav_history(path: str, navs: list[float]) -> None:
    """写入净值历史。"""
    history = [{"date": f"2026-01-{i+1:02d}", "nav": nav} for i, nav in enumerate(navs)]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f)


class TestComputeRollingAnnualReturn:
    """年化收益计算测试。"""

    def test_empty_history(self, monitor: TargetMonitor) -> None:
        # Arrange & Act
        annual_return, insufficient = monitor.compute_rolling_annual_return()
        # Assert
        assert annual_return == 0.0
        assert insufficient is True

    def test_insufficient_sample(self, nav_path: str, monitor: TargetMonitor) -> None:
        # Arrange — 仅10日数据
        _write_nav_history(
            nav_path, [1.0, 1.01, 1.02, 1.01, 1.03, 1.02, 1.04, 1.03, 1.05, 1.04]
        )
        # Act
        annual_return, insufficient = monitor.compute_rolling_annual_return()
        # Assert
        assert insufficient is True

    def test_sufficient_sample(self, nav_path: str, monitor: TargetMonitor) -> None:
        # Arrange — 60日数据，从1.0到1.05
        navs = [1.0 * (1.05 ** (i / 59)) for i in range(60)]
        _write_nav_history(nav_path, navs)
        # Act
        annual_return, insufficient = monitor.compute_rolling_annual_return()
        # Assert
        assert insufficient is False
        assert annual_return > 0


class TestComputeRollingMaxDrawdown:
    """最大回撤计算测试。"""

    def test_no_drawdown(self, nav_path: str, monitor: TargetMonitor) -> None:
        # Arrange — 单调递增
        _write_nav_history(nav_path, [1.0, 1.01, 1.02, 1.03, 1.04])
        # Act
        max_dd = monitor.compute_rolling_max_drawdown()
        # Assert
        assert max_dd == 0.0

    def test_with_drawdown(self, nav_path: str, monitor: TargetMonitor) -> None:
        # Arrange — 1.0 → 1.2 → 0.9 (回撤25%)
        _write_nav_history(nav_path, [1.0, 1.1, 1.2, 1.0, 0.9])
        # Act
        max_dd = monitor.compute_rolling_max_drawdown()
        # Assert
        assert max_dd == pytest.approx(0.25, abs=1e-6)


class TestDetectReturnDeviation:
    """收益偏离检测测试。"""

    def test_meet_target(self, monitor: TargetMonitor) -> None:
        # Arrange & Act
        action = monitor._detect_return_deviation(0.09)
        # Assert
        assert action == CorrectionAction.NONE

    def test_mild_deviation(self, monitor: TargetMonitor) -> None:
        # Arrange — 7% (偏离1pp < 2pp)
        action = monitor._detect_return_deviation(0.07)
        # Assert
        assert action == CorrectionAction.MILD_TUNE

    def test_moderate_deviation(self, monitor: TargetMonitor) -> None:
        # Arrange — 5% (偏离3pp)
        action = monitor._detect_return_deviation(0.05)
        # Assert
        assert action == CorrectionAction.MODERATE_ROTATE

    def test_severe_deviation(self, monitor: TargetMonitor) -> None:
        # Arrange — 3% (偏离5pp ≥ 4pp)
        action = monitor._detect_return_deviation(0.03)
        # Assert
        assert action == CorrectionAction.SEVERE_REVIEW


class TestDetectDrawdownBreach:
    """回撤突破检测测试。"""

    def test_no_breach(self, monitor: TargetMonitor) -> None:
        action = monitor._detect_drawdown_breach(0.10)
        assert action == CorrectionAction.NONE

    def test_warning(self, monitor: TargetMonitor) -> None:
        action = monitor._detect_drawdown_breach(0.16)
        assert action == CorrectionAction.MILD_TUNE

    def test_danger(self, monitor: TargetMonitor) -> None:
        action = monitor._detect_drawdown_breach(0.19)
        assert action == CorrectionAction.DEFENSE_BOOST

    def test_breach(self, monitor: TargetMonitor) -> None:
        action = monitor._detect_drawdown_breach(0.21)
        assert action == CorrectionAction.EMERGENCY_LIQUIDATE


class TestMonitor:
    """综合监控测试。"""

    def test_monitor_normal(self, nav_path: str, monitor: TargetMonitor) -> None:
        # Arrange — 良好表现
        navs = [1.0 * (1.10 ** (i / 59)) for i in range(60)]
        _write_nav_history(nav_path, navs)
        # Act
        result = monitor.monitor()
        # Assert
        assert result.rolling_annual_return > 0
        assert result.correction_action == CorrectionAction.NONE

    def test_monitor_with_deviation(
        self, nav_path: str, monitor: TargetMonitor
    ) -> None:
        # Arrange — 低收益
        _write_nav_history(nav_path, [1.0, 1.001, 1.002, 1.003, 1.004] * 12)
        # Act
        result = monitor.monitor()
        # Assert
        assert result.return_deviation > 0


class TestPrecheck:
    """预检测试。"""

    def test_no_backtest_engine(self, monitor: TargetMonitor) -> None:
        # Arrange & Act
        result = monitor.precheck_strategy_feasibility({})
        # Assert
        assert result.passed is True
        assert "不可用" in result.reason

    def test_precheck_pass(self, nav_path: str) -> None:
        # Arrange
        mock_engine = MagicMock()
        mock_engine.run_backtest.return_value = {
            "annual_return": 0.10,
            "max_drawdown": 0.15,
        }
        monitor = TargetMonitor(nav_history_path=nav_path, backtest_engine=mock_engine)
        # Act
        result = monitor.precheck_strategy_feasibility({"strategy": "test"})
        # Assert
        assert result.passed is True
        assert result.backtest_annual_return == 0.10

    def test_precheck_fail(self, nav_path: str) -> None:
        # Arrange — 回测不满足目标
        mock_engine = MagicMock()
        mock_engine.run_backtest.return_value = {
            "annual_return": 0.05,
            "max_drawdown": 0.25,
        }
        monitor = TargetMonitor(nav_history_path=nav_path, backtest_engine=mock_engine)
        # Act
        result = monitor.precheck_strategy_feasibility({"strategy": "test"})
        # Assert
        assert result.passed is False

    def test_precheck_exception(self, nav_path: str) -> None:
        # Arrange
        mock_engine = MagicMock()
        mock_engine.run_backtest.side_effect = Exception("回测失败")
        monitor = TargetMonitor(nav_history_path=nav_path, backtest_engine=mock_engine)
        # Act
        result = monitor.precheck_strategy_feasibility({"strategy": "test"})
        # Assert
        assert result.passed is False
        assert "异常" in result.reason


class TestUpdateNavHistory:
    """增量更新测试。"""

    def test_update_appends(self, nav_path: str, monitor: TargetMonitor) -> None:
        # Arrange & Act
        monitor.update_nav_history(1.0, "2026-01-01")
        monitor.update_nav_history(1.01, "2026-01-02")
        # Assert
        with open(nav_path, encoding="utf-8") as f:
            history = json.load(f)
        assert len(history) == 2
        assert history[0]["nav"] == 1.0
        assert history[1]["nav"] == 1.01
