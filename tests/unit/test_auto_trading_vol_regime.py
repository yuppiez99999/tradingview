"""
AutoTradingSystem._check_vol_regime 单元测试
==============================================

测试 AutoTradingSystem 中的波动率 Regime 监控子模块.

测试用例:
    - test_check_vol_regime_disabled: Feature Flag=False 时跳过
    - test_check_vol_regime_enabled_bull: 启用时 bull 档正常输出
    - test_check_vol_regime_bear_alert: bear 档触发告警
    - test_check_vol_regime_no_exception: 任何异常都不阻塞主循环
    - test_snapshot_includes_vol_regime: snapshot 包含 vol_regime 字段
    - test_snapshot_vol_regime_disabled: Flag=False 时 snapshot 返回 enabled=False
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ============================================================
# 测试 fixtures
# ============================================================
@pytest.fixture
def auto_trading_system():
    """创建 AutoTradingSystem 实例 (Mock 父类)."""
    # Mock 父类 AutomatedExecutionSystem 避免 Real 初始化
    with (
        patch("utils.auto_trading_system._AUTOMATED_AVAILABLE", True),
        patch(
            "utils.execution.automated_execution_system.AutomatedExecutionSystem.__init__",
            return_value=None,
        ),
    ):
        from utils.auto_trading_system import AutoTradingSystem

        system = AutoTradingSystem.__new__(AutoTradingSystem)
        # 手动初始化必要属性
        system.monitor_interval = 30
        system.is_running = False
        system._monitor_thread = None
        system.stats = {
            "start_time": None,
            "cycles_completed": 0,
            "errors": 0,
            "last_update": None,
        }
        return system


# ============================================================
# 测试用例: Feature Flag 控制
# ============================================================
class TestFeatureFlagControl:
    """测试 Feature Flag 对 _check_vol_regime 的控制."""

    def test_check_vol_regime_disabled(self, auto_trading_system, caplog):
        """Feature Flag=False 时直接跳过, 不调用 VolRegimeWeighter."""
        with (
            patch("utils.infra.feature_flags.is_enabled", return_value=False),
            caplog.at_level(logging.INFO),
        ):
            auto_trading_system._check_vol_regime()

        # 验证日志包含"未启用"
        assert any("未启用" in record.message for record in caplog.records)

    def test_check_vol_regime_flag_check_exception(self, auto_trading_system, caplog):
        """Feature Flag 检查异常时不阻塞."""
        with patch(
            "utils.infra.feature_flags.is_enabled",
            side_effect=RuntimeError("Flag 系统异常"),
        ):
            with caplog.at_level(logging.WARNING):
                auto_trading_system._check_vol_regime()

        # 异常被捕获, 输出 warning
        assert any("异常" in record.message for record in caplog.records)


# ============================================================
# 测试用例: 启用后的 Regime 识别
# ============================================================
class TestRegimeIdentification:
    """测试启用后的 Regime 识别逻辑."""

    def test_check_vol_regime_bull(self, auto_trading_system, caplog):
        """bull 档正常输出."""
        mock_result = {
            "status": "ok",
            "regime": "bull",
            "confidence": 0.85,
            "report_path": "/tmp/test.json",
            "decision_logged": False,
            "suggested_weights": {},
        }

        with (
            patch("utils.infra.feature_flags.is_enabled", return_value=True),
            patch("utils.alpha.vol_regime_weighter.VolRegimeWeighter") as MockWeighter,
            patch("utils.alpha.vix_data_source.VixDataSource") as MockVixDS,
            patch("utils.alpha.drawdown_reader.DrawdownReader") as MockDDReader,
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.open", MagicMock()),
            patch("yaml.safe_load", return_value={"assets": []}),
        ):

            MockWeighter.return_value.run_cycle.return_value = mock_result
            MockVixDS.return_value.fetch_vix.return_value = 15.0  # VIX=15 → bull
            MockDDReader.return_value.get_current_drawdown.return_value = 0.0

            with caplog.at_level(logging.INFO):
                auto_trading_system._check_vol_regime()

        # 验证日志包含 Regime=bull
        assert any("Regime=bull" in record.message for record in caplog.records)
        # 验证调用了 run_cycle
        MockWeighter.return_value.run_cycle.assert_called_once()

    def test_check_vol_regime_bear_alert(self, auto_trading_system, caplog):
        """bear 档触发告警."""
        mock_result = {
            "status": "ok",
            "regime": "bear",
            "confidence": 0.75,
            "report_path": "/tmp/test.json",
            "decision_logged": False,
            "suggested_weights": {},
        }

        with (
            patch("utils.infra.feature_flags.is_enabled", return_value=True),
            patch("utils.alpha.vol_regime_weighter.VolRegimeWeighter") as MockWeighter,
            patch("utils.alpha.vix_data_source.VixDataSource") as MockVixDS,
            patch("utils.alpha.drawdown_reader.DrawdownReader") as MockDDReader,
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.open", MagicMock()),
            patch("yaml.safe_load", return_value={"assets": []}),
        ):

            MockWeighter.return_value.run_cycle.return_value = mock_result
            MockVixDS.return_value.fetch_vix.return_value = 35.0  # VIX=35 → bear
            MockDDReader.return_value.get_current_drawdown.return_value = 0.08

            with caplog.at_level(logging.WARNING):
                auto_trading_system._check_vol_regime()

        # 验证 WARNING 日志包含告警
        assert any(
            "波动率告警" in record.message and record.levelno == logging.WARNING
            for record in caplog.records
        )

    def test_check_vol_regime_crisis_alert(self, auto_trading_system, caplog):
        """crisis 档触发告警."""
        mock_result = {
            "status": "ok",
            "regime": "crisis",
            "confidence": 0.90,
            "report_path": "/tmp/test.json",
            "decision_logged": False,
            "suggested_weights": {},
        }

        with (
            patch("utils.infra.feature_flags.is_enabled", return_value=True),
            patch("utils.alpha.vol_regime_weighter.VolRegimeWeighter") as MockWeighter,
            patch("utils.alpha.vix_data_source.VixDataSource") as MockVixDS,
            patch("utils.alpha.drawdown_reader.DrawdownReader") as MockDDReader,
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.open", MagicMock()),
            patch("yaml.safe_load", return_value={"assets": []}),
        ):

            MockWeighter.return_value.run_cycle.return_value = mock_result
            MockVixDS.return_value.fetch_vix.return_value = 45.0  # VIX=45 → crisis
            MockDDReader.return_value.get_current_drawdown.return_value = 0.15

            with caplog.at_level(logging.WARNING):
                auto_trading_system._check_vol_regime()

        # 验证 WARNING 日志包含告警
        assert any(
            "波动率告警" in record.message and record.levelno == logging.WARNING
            for record in caplog.records
        )

    def test_check_vol_regime_disabled_by_weighter(self, auto_trading_system, caplog):
        """VolRegimeWeighter 返回 disabled 状态时正常处理."""
        mock_result = {
            "status": "disabled",
            "regime": "neutral",
            "confidence": 0.0,
            "reason": "feature_flag_disabled",
        }

        with (
            patch("utils.infra.feature_flags.is_enabled", return_value=True),
            patch("utils.alpha.vol_regime_weighter.VolRegimeWeighter") as MockWeighter,
            patch("utils.alpha.vix_data_source.VixDataSource") as MockVixDS,
            patch("utils.alpha.drawdown_reader.DrawdownReader") as MockDDReader,
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.open", MagicMock()),
            patch("yaml.safe_load", return_value={"assets": []}),
        ):

            MockWeighter.return_value.run_cycle.return_value = mock_result
            MockVixDS.return_value.fetch_vix.return_value = 25.0
            MockDDReader.return_value.get_current_drawdown.return_value = 0.0

            with caplog.at_level(logging.INFO):
                auto_trading_system._check_vol_regime()

        # 验证日志包含"已禁用"
        assert any("已禁用" in record.message for record in caplog.records)


# ============================================================
# 测试用例: 异常容错
# ============================================================
class TestExceptionHandling:
    """测试异常处理, 确保不阻塞主循环."""

    def test_check_vol_regime_no_exception(self, auto_trading_system, caplog, tmp_path):
        """任何异常都不阻塞主监控循环."""
        # 跨平台/环境修复 (2026-09-09): 此前依赖仓库根 configs/portfolio.yaml 真实存在
        # (该文件被 .gitignore 排除, clone 后缺失) — 缺失时流程在文件检查处提前 return,
        # 永远走不到 VolRegimeWeighter 异常分支。现 patch 存在性 + 内容, 保证走到异常点。
        fake_portfolio = tmp_path / "portfolio.yaml"
        fake_portfolio.write_text("positions: []\n", encoding="utf-8")
        with (
            patch("utils.infra.feature_flags.is_enabled", return_value=True),
            patch("pathlib.Path.exists", return_value=True),
            patch(
                "pathlib.Path.open",
                mock_open(read_data="positions: []\n"),
            ),
            patch(
                "utils.alpha.vol_regime_weighter.VolRegimeWeighter",
                side_effect=RuntimeError("模拟异常"),
            ),
            caplog.at_level(logging.WARNING),
        ):
            # 不应该抛出异常
            auto_trading_system._check_vol_regime()

        # 验证异常被捕获并输出 warning
        assert any("异常" in record.message for record in caplog.records)

    def test_check_vol_regime_portfolio_missing(self, auto_trading_system, caplog):
        """portfolio.yaml 不存在时正常跳过."""
        with (
            patch("utils.infra.feature_flags.is_enabled", return_value=True),
            patch("pathlib.Path.exists", return_value=False),
            caplog.at_level(logging.WARNING),
        ):
            auto_trading_system._check_vol_regime()

        # 验证日志包含"不存在"
        assert any("不存在" in record.message for record in caplog.records)

    def test_check_vol_regime_import_error(self, auto_trading_system, caplog):
        """模块导入失败时正常跳过."""
        with (
            patch(
                "utils.infra.feature_flags.is_enabled",
                side_effect=ImportError("模块未安装"),
            ),
            caplog.at_level(logging.INFO),
        ):
            auto_trading_system._check_vol_regime()

        # ImportError 被 catch, 输出 info 日志
        assert any(
            "未加载" in record.message or "异常" in record.message
            for record in caplog.records
        )


# ============================================================
# 测试用例: snapshot 方法
# ============================================================
class TestSnapshotVolRegime:
    """测试 snapshot 方法中的 vol_regime 字段."""

    def test_snapshot_includes_vol_regime_field(self, auto_trading_system):
        """snapshot 包含 vol_regime 字段."""
        snapshot = auto_trading_system.snapshot()
        assert "vol_regime" in snapshot

    def test_snapshot_vol_regime_disabled(self, auto_trading_system):
        """Flag=False 时 snapshot 返回 enabled=False."""
        with patch("utils.infra.feature_flags.is_enabled", return_value=False):
            snapshot = auto_trading_system.snapshot()

        assert snapshot["vol_regime"]["enabled"] is False

    def test_snapshot_vol_regime_enabled(self, auto_trading_system):
        """Flag=True 时 snapshot 返回 VIX 和 drawdown."""
        with (
            patch("utils.infra.feature_flags.is_enabled", return_value=True),
            patch("utils.alpha.vix_data_source.VixDataSource") as MockVixDS,
            patch("utils.alpha.drawdown_reader.DrawdownReader") as MockDDReader,
        ):

            MockVixDS.return_value.fetch_vix.return_value = 22.5
            MockDDReader.return_value.get_current_drawdown.return_value = 0.03

            snapshot = auto_trading_system.snapshot()

        assert snapshot["vol_regime"]["enabled"] is True
        assert snapshot["vol_regime"]["vix"] == 22.5
        assert snapshot["vol_regime"]["current_drawdown"] == 0.03

    def test_snapshot_vol_regime_error(self, auto_trading_system):
        """VixDataSource 异常时 snapshot 返回 error."""
        with (
            patch("utils.infra.feature_flags.is_enabled", return_value=True),
            patch(
                "utils.alpha.vix_data_source.VixDataSource",
                side_effect=RuntimeError("VIX 系统异常"),
            ),
        ):
            snapshot = auto_trading_system.snapshot()

        assert "error" in snapshot["vol_regime"]
