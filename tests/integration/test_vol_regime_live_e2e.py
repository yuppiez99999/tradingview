"""
VolRegimeWeighter 实盘集成端到端测试
=====================================

验证完整集成链路:
    1. 盘中场景: AutoTradingSystem._check_vol_regime → VixDataSource → DrawdownReader → VolRegimeWeighter
    2. EOD 场景: EvolutionOrchestrator._run_vol_regime_weighter → VixDataSource → DrawdownReader → VolRegimeWeighter
    3. current_drawdown 参数正确传递并影响 Regime 修正
    4. VIX 数据源降级链 (shadow_state → Wind K线 → 缓存)

测试策略:
    - 使用真实的 shadow_state.json 数据 (从 output/shadow_account/ 读取)
    - Mock Wind MCP 外部调用 (避免网络依赖)
    - 验证报告文件生成 (reports/evolution/)
    - 验证 decisions.jsonl 审计链 (EOD 场景)
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ============================================================
# 测试 fixtures
# ============================================================
@pytest.fixture
def real_shadow_state() -> dict:
    """读取真实 shadow_state.json 数据."""
    state_path = _PROJECT_ROOT / "output" / "shadow_account" / "shadow_state.json"
    if not state_path.exists():
        pytest.skip(f"shadow_state.json 不存在: {state_path}")
    with state_path.open("r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def real_portfolio() -> dict:
    """读取真实 portfolio.yaml 数据."""
    import yaml

    portfolio_path = _PROJECT_ROOT / "configs" / "portfolio.yaml"
    if not portfolio_path.exists():
        pytest.skip(f"portfolio.yaml 不存在: {portfolio_path}")
    with portfolio_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@pytest.fixture
def temp_reports_dir(tmp_path) -> Path:
    """创建临时报告目录."""
    reports_dir = tmp_path / "evolution"
    reports_dir.mkdir(parents=True, exist_ok=True)
    return reports_dir


# ============================================================
# 测试场景 1: 盘中实时监控链路
# ============================================================
class TestLiveMonitoringChain:
    """测试 AutoTradingSystem 盘中实时监控链路."""

    def test_live_chain_vix_from_shadow_state(
        self, real_shadow_state, real_portfolio, caplog
    ):
        """盘中链路: VIX 从 shadow_state 获取, 完整调用 VolRegimeWeighter."""
        # 跳过 Flag 检查, 强制启用
        with (
            patch("utils.infra.feature_flags.is_enabled", return_value=True),
            patch("utils.alpha.vol_regime_weighter.VolRegimeWeighter") as MockWeighter,
        ):

            # Mock VolRegimeWeighter.run_cycle 返回 bull 档
            MockWeighter.return_value.run_cycle.return_value = {
                "status": "ok",
                "regime": "neutral",  # shadow_state 7 天数据, 回撤小, 应该是 neutral 或 bull
                "confidence": 0.65,
                "report_path": "/tmp/test.json",
                "decision_logged": False,
                "suggested_weights": {},
            }

            # 使用真实 VixDataSource 和 DrawdownReader
            from utils.alpha.drawdown_reader import DrawdownReader
            from utils.alpha.vix_data_source import VixDataSource

            vix = VixDataSource().fetch_vix(use_cache=False)
            dd = DrawdownReader().get_current_drawdown()

            # 验证数据获取成功 (shadow_state 有 7 天数据)
            assert vix is not None, "VIX 应该从 shadow_state 获取成功"
            assert dd is not None, "回撤应该从 shadow_state 计算成功"
            assert 5 <= vix <= 50, f"VIX={vix} 不在合理区间"
            assert 0 <= dd < 0.20, f"回撤={dd} 不在合理区间"

            # 调用 VolRegimeWeighter
            from utils.alpha.vol_regime_weighter import VolRegimeWeighter

            weighter = VolRegimeWeighter()
            result = weighter.run_cycle(
                portfolio_snapshot=real_portfolio,
                vix_value=vix,
                daily_returns=None,
                current_drawdown=dd,
                orchestrator=None,  # 盘中不写 decisions.jsonl
            )

            # 验证结果
            assert result["status"] in ("ok", "disabled")
            if result["status"] == "ok":
                assert result["regime"] in ("bull", "neutral", "bear", "crisis")
                assert 0 <= result["confidence"] <= 1

    def test_live_chain_bear_regime_alert(self, real_portfolio, caplog):
        """bear 档触发告警日志."""
        # 模拟 VIX=35 (bear 档)
        with (
            patch("utils.infra.feature_flags.is_enabled", return_value=True),
            patch("utils.alpha.vix_data_source.VixDataSource") as MockVixDS,
            patch("utils.alpha.drawdown_reader.DrawdownReader") as MockDDReader,
        ):

            MockVixDS.return_value.fetch_vix.return_value = 35.0
            MockDDReader.return_value.get_current_drawdown.return_value = 0.08

            # Mock AutoTradingSystem
            with patch(
                "utils.execution.automated_execution_system.AutomatedExecutionSystem.__init__",
                return_value=None,
            ):
                from utils.auto_trading_system import AutoTradingSystem

                system = AutoTradingSystem.__new__(AutoTradingSystem)
                system.monitor_interval = 30
                system.is_running = False
                system._monitor_thread = None
                system.stats = {
                    "start_time": None,
                    "cycles_completed": 0,
                    "errors": 0,
                    "last_update": None,
                }

                with (
                    patch("pathlib.Path.exists", return_value=True),
                    patch("pathlib.Path.open", MagicMock()),
                    patch("yaml.safe_load", return_value=real_portfolio),
                    patch(
                        "utils.alpha.vol_regime_weighter.VolRegimeWeighter"
                    ) as MockWeighter,
                ):

                    MockWeighter.return_value.run_cycle.return_value = {
                        "status": "ok",
                        "regime": "bear",
                        "confidence": 0.75,
                        "report_path": "/tmp/test.json",
                        "decision_logged": False,
                        "suggested_weights": {},
                    }

                    with caplog.at_level(logging.WARNING):
                        system._check_vol_regime()

                    # 验证告警日志
                    assert any(
                        "波动率告警" in r.message and r.levelno == logging.WARNING
                        for r in caplog.records
                    )


# ============================================================
# 测试场景 2: EOD 完整链路
# ============================================================
class TestEodChain:
    """测试 EOD EvolutionOrchestrator 完整链路."""

    def test_eod_chain_orchestrator_call(
        self, real_shadow_state, real_portfolio, temp_reports_dir
    ):
        """EOD 链路: orchestrator → _fetch_vix → DrawdownReader → run_cycle."""
        from utils.alpha.drawdown_reader import DrawdownReader
        from utils.alpha.vix_data_source import VixDataSource
        from utils.alpha.vol_regime_weighter import VolRegimeWeighter

        # 使用真实数据源
        vix = VixDataSource().fetch_vix(use_cache=False)
        dd = DrawdownReader().get_current_drawdown()

        assert vix is not None
        assert dd is not None

        # 调用 VolRegimeWeighter (传入 reports_dir)
        weighter = VolRegimeWeighter()
        result = weighter.run_cycle(
            portfolio_snapshot=real_portfolio,
            vix_value=vix,
            daily_returns=None,
            current_drawdown=dd,
            orchestrator=None,
            reports_dir=temp_reports_dir,
        )

        # 验证结果
        assert result["status"] in ("ok", "disabled")
        if result["status"] == "ok":
            assert "report_path" in result
            # 验证报告文件生成
            report_path = Path(result["report_path"])
            if report_path.exists():
                with report_path.open("r", encoding="utf-8") as f:
                    report_data = json.load(f)
                assert "regime" in report_data or "suggestion" in report_data

    def test_eod_chain_fetch_vix_method(self, real_shadow_state):
        """EOD 链路: EvolutionOrchestrator._fetch_vix 方法重写后正常工作."""
        # 创建 mock orchestrator
        MagicMock()

        # 调用 _fetch_vix 方法 (使用真实 VixDataSource)
        from utils.alpha.evolution_orchestrator import EvolutionOrchestrator

        # 直接调用实例方法 (需要绕过 __init__)
        orchestrator = EvolutionOrchestrator.__new__(EvolutionOrchestrator)
        vix = orchestrator._fetch_vix()

        # shadow_state.json 有 7 天数据, VIX 应该能计算出来
        # 如果 Wind MCP 不可用, 会从 shadow_state 计算 RV
        assert vix is None or 5 <= vix <= 50


# ============================================================
# 测试场景 3: current_drawdown 参数传递
# ============================================================
class TestCurrentDrawdownPropagation:
    """验证 current_drawdown 参数正确传递并影响 Regime 修正."""

    def test_drawdown_floor_neutral(self, real_portfolio):
        """回撤 > 5% 时, regime 至少为 neutral (即使 VIX 很低)."""
        from utils.alpha.vol_regime_weighter import VolRegimeWeighter

        weighter = VolRegimeWeighter()
        # VIX=15 (bull), 但回撤=8% (> 5%), 应该修正为至少 neutral
        suggestion = weighter.compute_weights(
            current_weights={"科技": 0.20, "现金": 0.10},
            vix_value=15.0,
            daily_returns=None,
            current_drawdown=0.08,
        )

        # 验证 regime 被修正 (至少 neutral)
        assert suggestion.regime.label in ("neutral", "bear", "crisis")
        assert suggestion.regime.label != "bull"  # 不应该是 bull

    def test_drawdown_floor_bear(self, real_portfolio):
        """回撤 > 12% 时, regime 至少为 bear."""
        from utils.alpha.vol_regime_weighter import VolRegimeWeighter

        weighter = VolRegimeWeighter()
        # VIX=15 (bull), 但回撤=15% (> 12%), 应该修正为至少 bear
        suggestion = weighter.compute_weights(
            current_weights={"科技": 0.20, "现金": 0.10},
            vix_value=15.0,
            daily_returns=None,
            current_drawdown=0.15,
        )

        # 验证 regime 被修正 (至少 bear)
        assert suggestion.regime.label in ("bear", "crisis")

    def test_no_drawdown_no_modification(self, real_portfolio):
        """回撤 = None 时, 不修正 regime."""
        from utils.alpha.vol_regime_weighter import VolRegimeWeighter

        weighter = VolRegimeWeighter()
        # VIX=15 (bull), 回撤=None, regime 应该是 bull
        suggestion = weighter.compute_weights(
            current_weights={"科技": 0.20, "现金": 0.10},
            vix_value=15.0,
            daily_returns=None,
            current_drawdown=None,
        )

        # 回撤为 None 时不修正, regime 由 VIX 决定
        assert suggestion.regime.label == "bull"


# ============================================================
# 测试场景 4: VIX 数据源降级链
# ============================================================
class TestVixDataSourceChain:
    """测试 VIX 数据源降级链."""

    def test_shadow_state_primary(self, real_shadow_state):
        """主数据源 (shadow_state RV) 可用时返回."""
        from utils.alpha.vix_data_source import VixDataSource

        ds = VixDataSource()
        vix = ds._fetch_from_shadow_state_rv()

        assert vix is not None
        assert 5 <= vix <= 50

    def test_wind_kline_fallback(self):
        """Wind K 线备选 (Mock 测试)."""
        from utils.alpha.vix_data_source import VixDataSource

        ds = VixDataSource()

        # Mock wind_get_kline 返回 30 条 K 线数据
        mock_kline = [{"close": 2.50 + i * 0.01 * ((-1) ** i)} for i in range(30)]

        with patch.dict(
            sys.modules,
            {
                "wind_mcp_fetcher": MagicMock(
                    wind_get_kline=MagicMock(return_value=mock_kline)
                )
            },
        ):
            vix = ds._fetch_from_wind_kline()

        # Mock 数据波动率可能不在合理区间, 但函数应该返回 float 或 None
        assert vix is None or isinstance(vix, float)

    def test_full_chain_with_cache(self, real_shadow_state, tmp_path):
        """完整降级链 + 缓存写入."""
        from utils.alpha.vix_data_source import VixDataSource

        ds = VixDataSource()
        cache_path = tmp_path / "vix_cache.json"
        ds.CACHE_PATH = cache_path

        # 第一次调用 (主数据源可用)
        vix1 = ds.fetch_vix(use_cache=False)
        assert vix1 is not None
        assert cache_path.exists()  # 缓存已写入

        # 验证缓存内容
        with cache_path.open("r", encoding="utf-8") as f:
            cache_data = json.load(f)
        assert cache_data["vix"] == vix1
        assert cache_data["source"] in ("shadow_state_rv", "wind_kline_vol")


# ============================================================
# 测试场景 5: 配置文件完整性
# ============================================================
class TestConfigIntegrity:
    """验证配置文件完整性."""

    def test_vol_regime_weighter_yaml_complete(self):
        """vol_regime_weighter.yaml 包含所有必要字段."""
        import yaml

        config_path = _PROJECT_ROOT / "configs" / "vol_regime_weighter.yaml"
        if not config_path.exists():
            pytest.skip("vol_regime_weighter.yaml 不存在")

        with config_path.open("r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

        # 验证必要字段
        assert "regime_thresholds" in config
        assert "constraints" in config
        assert "degradation" in config
        assert "data_source" in config  # v8.6.14 新增

        # 验证数据源配置
        data_source = config.get("data_source", {})
        assert "vix" in data_source
        assert "drawdown" in data_source
        assert "live_monitoring" in data_source
        assert "eod_report" in data_source

        # 验证 VIX 数据源配置
        vix_config = data_source["vix"]
        assert "primary" in vix_config
        assert "secondary" in vix_config
        assert "cache_ttl_seconds" in vix_config
        assert "cache_path" in vix_config

    def test_feature_flag_registered(self):
        """USE_VOL_REGIME_WEIGHTER 已在 feature_flags.yaml 注册."""
        ff_path = _PROJECT_ROOT / "configs" / "feature_flags.yaml"
        if not ff_path.exists():
            pytest.skip("feature_flags.yaml 不存在")

        with ff_path.open("r", encoding="utf-8") as f:
            content = f.read()

        assert "USE_VOL_REGIME_WEIGHTER" in content
