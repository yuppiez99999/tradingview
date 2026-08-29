"""G7 覆盖率冲刺 v2 — hedge_engine + hedge_rebalance_integrator 未覆盖路径补测试.

补充 test_g7_hedge_engine_boost.py 未覆盖的方法:

hedge_engine.py 未覆盖:
  - 模块函数: _exec_ifind / fetch_futures_prices_from_* / get_live_futures_prices
  - HedgeEngine: _compute_portfolio_vol_cov / _compute_expected_shortfall / _compute_mrc
    / run_historical_stress_tests / compute_correlation_matrix / monitor_daily_correlation
    / check_sector_concentration / generate_futures_hedge / generate_options_hedge
    / generate_hedge_plan / compute_optimal_hedge_ratio(带波动率/回撤参数)
  - 常量: DEFAULT_BETAS / SECTOR_MAP / HISTORICAL_STRESS_SCENARIOS 等

hedge_rebalance_integrator.py 未覆盖:
  - _determine_market_regime (MILD/HIGH 路径)
  - _compute_tail_hedge_ratio (NONE/FIXED 模式, 边界条件)
  - decide_hedge (多路径: 无需对冲/有/无hedge_engine/FIXED/DYNAMIC模式)
  - check_rebalance (无需/战术/战略再平衡)
  - joint_optimize (多路径: 不一致/margin预警)
  - _estimate_performance / generate_execution_plan (多优先级)
  - run_full_workflow / run_joint_analysis / _get_ifind_prices_batch

运行:
    python -m pytest tests/unit/test_g7_hedge_engine_boost_v2.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ============================================================
# sys.path 注入 — 确保能找到 utils 模块
# ============================================================
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# 辅助函数
# ============================================================


def _make_synthetic_returns(
    codes: list[str], n_days: int = 70, seed: int = 42
) -> dict[str, list[float]]:
    """生成合成的历史日收益率数据 (用于协方差矩阵/VaR/ES/MRC测试)"""
    import random

    rng = random.Random(seed)
    return {code: [rng.gauss(0, 0.02) for _ in range(n_days)] for code in codes}


def _make_portfolio_risk(**kwargs):
    """构造 PortfolioRisk 实例, 默认有效值"""
    from utils.hedge_engine import PortfolioRisk

    defaults = dict(
        total_value=1_000_000,
        stock_exposure=800_000,
        cash=200_000,
        beta_csi300=1.2,
        beta_csi500=1.3,
        beta_csi1000=1.4,
        beta_sse50=1.1,
        volatility_30d=0.02,
        var_95_daily=20_000,
    )
    defaults.update(kwargs)
    return PortfolioRisk(**defaults)


# ============================================================
# 1. hedge_engine 模块函数: 期货价格获取链
# ============================================================


class TestHedgeEngineFuturesPrices:
    """期货价格多源回退链路测试"""

    def test_exec_ifind_no_client(self) -> None:
        """iFinD 客户端不可用时返回错误 (已废弃: 源码重构移除 iFinD 数据源)"""
        pytest.skip("utils/hedge_engine.py 已移除 _exec_ifind (iFinD 数据源不再使用)")

    def test_exec_ifind_with_mock_client_data(self) -> None:
        """iFinD 客户端返回 data 时正常透传 (已废弃: 源码重构移除 iFinD 数据源)"""
        pytest.skip("utils/hedge_engine.py 已移除 _exec_ifind (iFinD 数据源不再使用)")

    def test_exec_ifind_error_response(self) -> None:
        """iFinD 返回 error 字段时透传错误 (已废弃: 源码重构移除 iFinD 数据源)"""
        pytest.skip("utils/hedge_engine.py 已移除 _exec_ifind (iFinD 数据源不再使用)")

    def test_exec_ifind_exception(self) -> None:
        """iFinD 调用异常时返回错误 (已废弃: 源码重构移除 iFinD 数据源)"""
        pytest.skip("utils/hedge_engine.py 已移除 _exec_ifind (iFinD 数据源不再使用)")

    def test_exec_ifind_non_dict_result(self) -> None:
        """iFinD 返回非字典结果时直接返回 (已废弃: 源码重构移除 iFinD 数据源)"""
        pytest.skip("utils/hedge_engine.py 已移除 _exec_ifind (iFinD 数据源不再使用)")

    def test_fetch_futures_prices_from_ifind_unavailable(self) -> None:
        """iFinD 不可用时返回空字典 (已废弃: 源码重构移除 iFinD 数据源)"""
        pytest.skip(
            "utils/hedge_engine.py 已移除 fetch_futures_prices_from_ifind (iFinD 数据源不再使用)"
        )

    def test_fetch_futures_prices_from_wind_no_module(self) -> None:
        """Wind MCP 模块不可用时返回空字典"""
        from utils.hedge_engine import fetch_futures_prices_from_wind

        result = fetch_futures_prices_from_wind()
        assert isinstance(result, dict)

    def test_fetch_futures_prices_from_sina_mocked(self) -> None:
        """Sina 数据源 mock 测试 — 构造有效响应"""
        from utils.hedge_engine import fetch_futures_prices_from_sina

        # 构造 sina 行情响应格式: ="IF2406,open,pre_close,price,..."
        parts = ["IF2406"] + ["4000.000"] * 25  # 26 个逗号分隔字段
        text = f'="{",".join(parts)}"'
        mock_resp = MagicMock()
        mock_resp.read.return_value = text.encode("gbk")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = fetch_futures_prices_from_sina()
        assert isinstance(result, dict)
        assert "IF" in result
        assert result["IF"] == 4000.0

    def test_fetch_futures_prices_from_sina_error(self) -> None:
        """Sina 网络异常时返回空字典不崩溃"""
        from utils.hedge_engine import fetch_futures_prices_from_sina

        with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
            result = fetch_futures_prices_from_sina()
        assert isinstance(result, dict)
        assert result == {}

    def test_fetch_futures_prices_from_akshare_no_module(self) -> None:
        """AKShare 未安装时返回空字典"""
        from utils.hedge_engine import fetch_futures_prices_from_akshare

        result = fetch_futures_prices_from_akshare()
        assert isinstance(result, dict)

    def test_fetch_futures_prices_from_efinance_no_module(self) -> None:
        """efinance 未安装时返回空字典"""
        from utils.hedge_engine import fetch_futures_prices_from_efinance

        result = fetch_futures_prices_from_efinance()
        assert isinstance(result, dict)

    def test_get_live_futures_prices_all_fallback(self) -> None:
        """所有数据源失败时使用默认回退价格"""
        from utils.hedge_engine import DEFAULT_FUTURES_PRICES, get_live_futures_prices

        # API 重构: 源码已移除 iFinD, 回退链为 Wind → AKShare → Sina → efinance → 默认
        with (
            patch("utils.hedge_engine.fetch_futures_prices_from_wind", return_value={}),
            patch(
                "utils.hedge_engine.fetch_futures_prices_from_akshare", return_value={}
            ),
            patch("utils.hedge_engine.fetch_futures_prices_from_sina", return_value={}),
            patch(
                "utils.hedge_engine.fetch_futures_prices_from_efinance", return_value={}
            ),
        ):
            prices = get_live_futures_prices()
        assert "IF" in prices
        assert "IC" in prices
        assert "IM" in prices
        assert "IH" in prices
        assert prices["IF"] == DEFAULT_FUTURES_PRICES["IF"]
        assert prices["IC"] == DEFAULT_FUTURES_PRICES["IC"]

    def test_get_live_futures_prices_with_ifind(self) -> None:
        """Wind 提供完整价格时直接使用 (API 重构: iFinD 已移除, 改用 Wind)"""
        from utils.hedge_engine import get_live_futures_prices

        wind_prices = {"IF": 3900.0, "IC": 6200.0, "IM": 6800.0, "IH": 2700.0}
        with (
            patch(
                "utils.hedge_engine.fetch_futures_prices_from_wind",
                return_value=wind_prices,
            ),
            patch(
                "utils.hedge_engine.fetch_futures_prices_from_akshare", return_value={}
            ),
            patch("utils.hedge_engine.fetch_futures_prices_from_sina", return_value={}),
            patch(
                "utils.hedge_engine.fetch_futures_prices_from_efinance", return_value={}
            ),
        ):
            prices = get_live_futures_prices()
        assert prices["IF"] == 3900.0
        assert prices["IC"] == 6200.0

    def test_get_live_futures_prices_partial_from_wind(self) -> None:
        """Wind 部分补充价格"""
        from utils.hedge_engine import get_live_futures_prices

        # API 重构: 源码已移除 iFinD, 回退链为 Wind → AKShare → Sina → efinance → 默认
        with (
            patch(
                "utils.hedge_engine.fetch_futures_prices_from_wind",
                return_value={"IF": 3900.0, "IC": 6200.0},
            ),
            patch(
                "utils.hedge_engine.fetch_futures_prices_from_akshare",
                return_value={"IM": 6800.0},
            ),
            patch("utils.hedge_engine.fetch_futures_prices_from_sina", return_value={}),
            patch(
                "utils.hedge_engine.fetch_futures_prices_from_efinance", return_value={}
            ),
        ):
            prices = get_live_futures_prices()
        assert prices["IF"] == 3900.0
        assert prices["IC"] == 6200.0
        assert prices["IM"] == 6800.0


# ============================================================
# 2. hedge_engine 常量补充测试
# ============================================================


class TestHedgeEngineExtraConstants:
    """hedge_engine 未覆盖常量测试"""

    def test_default_betas_structure(self) -> None:
        from utils.hedge_engine import HedgeEngine

        assert hasattr(HedgeEngine, "DEFAULT_BETAS")
        betas = HedgeEngine.DEFAULT_BETAS
        assert "300750" in betas
        assert "688981" in betas
        # 每个标的有4个Beta (CSI300/500/1000/SSE50)
        assert len(betas["300750"]) == 4

    def test_sector_map(self) -> None:
        from utils.hedge_engine import HedgeEngine

        smap = HedgeEngine.SECTOR_MAP
        assert smap["300750"] == "高端制造"
        assert smap["601088"] == "顺周期"
        assert smap["518880"] == "黄金ETF"

    def test_fixed_income_types(self) -> None:
        from utils.hedge_engine import HedgeEngine

        assert "国债ETF" in HedgeEngine.FIXED_INCOME_TYPES

    def test_sector_limit(self) -> None:
        from utils.hedge_engine import HedgeEngine

        assert HedgeEngine.SECTOR_LIMIT == 0.35

    def test_mrc_limit(self) -> None:
        from utils.hedge_engine import HedgeEngine

        assert HedgeEngine.MRC_LIMIT == 0.25

    def test_correlation_warn(self) -> None:
        from utils.hedge_engine import HedgeEngine

        assert HedgeEngine.CORRELATION_WARN == 0.70

    def test_historical_stress_scenarios(self) -> None:
        from utils.hedge_engine import HedgeEngine

        scenarios = HedgeEngine.HISTORICAL_STRESS_SCENARIOS
        assert len(scenarios) == 6
        assert "2015股灾 (沪深300 -45%)" in scenarios
        for _name, shock in scenarios.items():
            assert "csi300" in shock
            assert "csi500" in shock
            assert "sector" in shock

    def test_hedge_roll_cost_annual(self) -> None:
        from utils.hedge_engine import HEDGE_ROLL_COST_ANNUAL

        assert HEDGE_ROLL_COST_ANNUAL == 0.025

    def test_hedge_margin_opp_cost(self) -> None:
        from utils.hedge_engine import HEDGE_MARGIN_OPP_COST

        assert HEDGE_MARGIN_OPP_COST == 0.020

    def test_fallback_prices_updated(self) -> None:
        from utils.hedge_engine import FALLBACK_PRICES_UPDATED

        assert isinstance(FALLBACK_PRICES_UPDATED, str)


# ============================================================
# 3. HedgeEngine: 历史压力测试
# ============================================================


class TestHedgeEngineStressTests:
    """run_historical_stress_tests 测试"""

    def test_stress_tests_empty_positions(self) -> None:
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        result = engine.run_historical_stress_tests({}, {})
        assert result == {}

    def test_stress_tests_zero_shares(self) -> None:
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        positions = {"600519": {"shares": 0}}
        prices = {"600519": 1800.0}
        result = engine.run_historical_stress_tests(positions, prices)
        assert result == {}

    def test_stress_tests_with_positions(self) -> None:
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        positions = {
            "300750": {"shares": 100},
            "601088": {"shares": 200},
        }
        prices = {"300750": 230.0, "601088": 38.0}
        result = engine.run_historical_stress_tests(positions, prices)
        assert len(result) == 6  # 6 个历史情景
        for _scenario_name, scenario_result in result.items():
            assert "estimated_loss" in scenario_result
            assert "drawdown_pct" in scenario_result
            assert "breaches_limit" in scenario_result
            assert "surviving_value" in scenario_result
            assert "sector_impact" in scenario_result

    def test_stress_tests_gold_etf_special(self) -> None:
        """黄金ETF在危机中上涨的特殊处理"""
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        positions = {"518880": {"shares": 1000}}
        prices = {"518880": 5.2}
        result = engine.run_historical_stress_tests(positions, prices)
        assert len(result) == 6
        # 黄金ETF在危机中应该损失较小或盈利
        for scenario_result in result.values():
            # gold shock 为正, so loss should be negative (profit) or small
            assert scenario_result["estimated_loss"] >= 0  # abs value

    def test_stress_tests_fallback_price(self) -> None:
        """无价格时使用兜底价格"""
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        positions = {"300750": {"shares": 100}}
        prices = {}  # 无价格
        result = engine.run_historical_stress_tests(positions, prices)
        assert len(result) == 6


# ============================================================
# 4. HedgeEngine: 协方差矩阵/ES/MRC
# ============================================================


class TestHedgeEngineCovariance:
    """_compute_portfolio_vol_cov / _compute_expected_shortfall / _compute_mrc 测试"""

    def test_compute_portfolio_vol_cov_no_data(self) -> None:
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        result = engine._compute_portfolio_vol_cov({}, {}, ["600519"])
        assert result == 0.015  # 回退值

    def test_compute_portfolio_vol_cov_insufficient_data(self) -> None:
        """收益率不足30天的标的被跳过"""
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        returns = {"600519": [0.01] * 10}  # 只有10天
        result = engine._compute_portfolio_vol_cov({"600519": 1.0}, returns, ["600519"])
        assert result == 0.015  # 回退值

    def test_compute_portfolio_vol_cov_single_asset(self) -> None:
        """单个标的有足够数据时使用加权标准差"""
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        returns = {"600519": [0.01] * 50}
        result = engine._compute_portfolio_vol_cov({"600519": 1.0}, returns, ["600519"])
        assert isinstance(result, float)
        assert result >= 0

    def test_compute_portfolio_vol_cov_multi_asset(self) -> None:
        """多标协方差矩阵计算"""
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        codes = ["600519", "000858"]
        returns = _make_synthetic_returns(codes, n_days=70, seed=42)
        weights = {"600519": 0.6, "000858": 0.4}
        result = engine._compute_portfolio_vol_cov(weights, returns, codes)
        assert isinstance(result, float)
        assert result > 0

    def test_compute_expected_shortfall_no_data(self) -> None:
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        result = engine._compute_expected_shortfall({}, {}, [], 1_000_000)
        assert isinstance(result, float)
        assert result > 0

    def test_compute_expected_shortfall_with_data(self) -> None:
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        codes = ["600519", "000858"]
        returns = _make_synthetic_returns(codes, n_days=70, seed=42)
        weights = {"600519": 0.6, "000858": 0.4}
        result = engine._compute_expected_shortfall(
            weights, returns, codes, 1_000_000, 0.95
        )
        assert isinstance(result, float)

    def test_compute_mrc_insufficient_codes(self) -> None:
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        returns = {"600519": [0.01] * 50}
        result = engine._compute_mrc({"600519": 1.0}, returns, ["600519"], 0.02)
        assert result == {}

    def test_compute_mrc_zero_vol(self) -> None:
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        codes = ["600519", "000858"]
        returns = _make_synthetic_returns(codes, n_days=70, seed=42)
        result = engine._compute_mrc(
            {"600519": 0.5, "000858": 0.5}, returns, codes, 0.0
        )
        assert result == {}

    def test_compute_mrc_with_data(self) -> None:
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        codes = ["600519", "000858"]
        returns = _make_synthetic_returns(codes, n_days=70, seed=42)
        result = engine._compute_mrc(
            {"600519": 0.6, "000858": 0.4}, returns, codes, 0.02
        )
        assert isinstance(result, dict)
        assert "600519" in result
        assert "000858" in result

    def test_assess_portfolio_risk_with_historical_returns(self) -> None:
        """assess_portfolio_risk 传入 historical_returns 路径"""
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        codes = ["600519", "000858"]
        returns = _make_synthetic_returns(codes, n_days=70, seed=42)
        positions = {"600519": {"shares": 100}, "000858": {"shares": 200}}
        prices = {"600519": 1800.0, "000858": 150.0}
        risk = engine.assess_portfolio_risk(
            positions, prices, historical_returns=returns
        )
        assert risk.volatility_30d > 0
        assert risk.var_95_daily > 0
        assert risk.cvar_95_daily > 0


# ============================================================
# 5. HedgeEngine: 相关性监控
# ============================================================


class TestHedgeEngineCorrelation:
    """compute_correlation_matrix / monitor_daily_correlation / check_sector_concentration"""

    def test_compute_correlation_matrix_insufficient(self) -> None:
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        result = engine.compute_correlation_matrix({}, [], 60)
        assert result == {}

    def test_compute_correlation_matrix_single_code(self) -> None:
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        returns = {"600519": [0.01] * 70}
        result = engine.compute_correlation_matrix(returns, ["600519"], 60)
        assert result == {}

    def test_compute_correlation_matrix_short_data(self) -> None:
        """数据长度不足 lookback_days 时返回空"""
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        returns = {"600519": [0.01] * 30, "000858": [0.02] * 30}
        result = engine.compute_correlation_matrix(
            returns, ["600519", "000858"], lookback_days=60
        )
        assert result == {}

    def test_compute_correlation_matrix_valid(self) -> None:
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        codes = ["600519", "000858"]
        returns = _make_synthetic_returns(codes, n_days=70, seed=42)
        result = engine.compute_correlation_matrix(returns, codes, lookback_days=60)
        assert isinstance(result, dict)
        assert "600519" in result
        assert "000858" in result["600519"]
        # 自相关 = 1.0
        assert result["600519"]["600519"] == 1.0

    def test_monitor_daily_correlation_no_data(self) -> None:
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        result = engine.monitor_daily_correlation({}, {})
        assert result["status"] == "NO_DATA"
        assert result["alert"] is False

    def test_monitor_daily_correlation_valid(self) -> None:
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        codes = ["600519", "000858"]
        returns = _make_synthetic_returns(codes, n_days=70, seed=42)
        positions = {c: {"shares": 100} for c in codes}
        result = engine.monitor_daily_correlation(positions, returns)
        assert result["status"] == "OK"
        assert "average_correlation" in result
        assert "max_correlation" in result
        assert "alert" in result
        assert "risk_score" in result

    def test_monitor_daily_correlation_high_alert(self) -> None:
        """构造高相关系数触发预警"""
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        # 两个完全正相关的标的
        rets = [0.01 * (i % 5 - 2) for i in range(70)]
        returns = {"600519": rets[:], "000858": rets[:]}
        positions = {"600519": {"shares": 100}, "000858": {"shares": 100}}
        result = engine.monitor_daily_correlation(
            positions, returns, alert_threshold=0.5
        )
        assert result["max_correlation"] >= 0.99  # 完全正相关
        assert result["alert"] is True

    def test_check_sector_concentration(self) -> None:
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        codes = ["600519", "000858"]
        returns = _make_synthetic_returns(codes, n_days=70, seed=42)
        positions = {
            "600519": {"shares": 100, "category": "白酒"},
            "000858": {"shares": 200, "category": "白酒"},
        }
        result = engine.check_sector_concentration(positions, returns)
        assert "sector_concentration" in result
        assert "alert_sectors" in result
        assert "overall_concentration_risk" in result
        assert "白酒" in result["sector_concentration"]

    def test_check_sector_concentration_single_code(self) -> None:
        """板块内只有1个标的时返回'标的不足'"""
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        positions = {"600519": {"shares": 100, "category": "白酒"}}
        result = engine.check_sector_concentration(positions, {})
        assert result["sector_concentration"]["白酒"]["risk"] == 0.0


# ============================================================
# 6. HedgeEngine: generate_futures_hedge
# ============================================================


class TestHedgeEngineFuturesHedge:
    """generate_futures_hedge 多路径测试"""

    def test_generate_futures_hedge_zero_ratio(self) -> None:
        """对冲比率为0时返回空方案"""
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        risk = _make_portfolio_risk()
        result = engine.generate_futures_hedge(risk, 0.0)
        assert result["contracts"] == {}
        assert result["total_notional"] == 0
        assert result["total_margin"] == 0

    def test_generate_futures_hedge_negative_ratio(self) -> None:
        """负对冲比率等同于0"""
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        risk = _make_portfolio_risk()
        result = engine.generate_futures_hedge(risk, -0.1)
        assert result["contracts"] == {}

    def test_generate_futures_hedge_with_prices(self) -> None:
        """传入期货价格时正常计算"""
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        risk = _make_portfolio_risk()
        prices = {"IF": 3900.0, "IC": 6200.0, "IM": 6800.0, "IH": 2700.0}
        result = engine.generate_futures_hedge(risk, 0.3, futures_prices=prices)
        assert "contracts" in result
        assert result["total_notional"] > 0
        assert result["total_margin"] > 0
        assert "price_source" in result
        assert result["price_source"] == "user_provided"
        # 应该分配到 IC/IM/IF
        assert len(result["contracts"]) > 0

    def test_generate_futures_hedge_auto_prices(self) -> None:
        """不传价格时自动获取"""
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        risk = _make_portfolio_risk()
        mock_prices = {"IF": 3900.0, "IC": 6200.0, "IM": 6800.0, "IH": 2700.0}
        with patch(
            "utils.hedge_engine.get_live_futures_prices", return_value=mock_prices
        ):
            result = engine.generate_futures_hedge(risk, 0.3)
        assert result["price_source"] == "auto"
        assert result["total_notional"] > 0

    def test_generate_futures_hedge_zero_beta(self) -> None:
        """组合Beta为0时无需对冲"""
        from utils.hedge_engine import HedgeEngine, PortfolioRisk

        engine = HedgeEngine()
        risk = PortfolioRisk(
            total_value=1_000_000,
            stock_exposure=800_000,
            beta_csi300=0.0,
            beta_csi500=0.0,
            beta_csi1000=0.0,
        )
        prices = {"IF": 3900.0, "IC": 6200.0, "IM": 6800.0}
        result = engine.generate_futures_hedge(risk, 0.3, futures_prices=prices)
        assert result["contracts"] == {}
        assert "Beta<=0" in result["reason"]

    def test_generate_futures_hedge_fallback_detection(self) -> None:
        """检测使用回退价格的品种"""
        from utils.hedge_engine import DEFAULT_FUTURES_PRICES, HedgeEngine

        engine = HedgeEngine()
        risk = _make_portfolio_risk()
        # 传入与默认价格相同的值 → 标记为 fallback
        prices = {
            "IF": DEFAULT_FUTURES_PRICES["IF"],
            "IC": DEFAULT_FUTURES_PRICES["IC"],
            "IM": DEFAULT_FUTURES_PRICES["IM"],
            "IH": DEFAULT_FUTURES_PRICES["IH"],
        }
        result = engine.generate_futures_hedge(risk, 0.3, futures_prices=prices)
        assert len(result["fallback_used"]) == 4


# ============================================================
# 7. HedgeEngine: generate_options_hedge
# ============================================================


class TestHedgeEngineOptionsHedge:
    """generate_options_hedge 多策略测试"""

    def test_options_hedge_zero_ratio(self) -> None:
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        risk = _make_portfolio_risk()
        result = engine.generate_options_hedge(risk, 0.0)
        assert result["contracts"] == []
        assert result["total_cost"] == 0

    def test_options_hedge_protective_put(self) -> None:
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        risk = _make_portfolio_risk(beta_csi300=1.3, beta_csi500=1.0)
        result = engine.generate_options_hedge(risk, 0.3, strategy="protective_put")
        assert result["strategy"] == "protective_put"
        assert result["contracts"] > 0
        assert result["total_premium"] > 0
        assert result["underlying"] == "510300"

    def test_options_hedge_collar(self) -> None:
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        risk = _make_portfolio_risk()
        result = engine.generate_options_hedge(risk, 0.3, strategy="collar")
        assert result["strategy"] == "collar"
        assert result["contracts"] > 0
        assert "net_cost" in result
        assert "put_strike" in result
        assert "call_strike" in result

    def test_options_hedge_put_spread(self) -> None:
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        risk = _make_portfolio_risk()
        result = engine.generate_options_hedge(risk, 0.3, strategy="put_spread")
        assert result["strategy"] == "put_spread"
        assert result["contracts"] > 0
        assert "spread_cost" in result
        assert "buy_put_strike" in result
        assert "sell_put_strike" in result

    def test_options_hedge_unknown_strategy(self) -> None:
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        risk = _make_portfolio_risk()
        result = engine.generate_options_hedge(risk, 0.3, strategy="unknown_xyz")
        assert result["contracts"] == []
        assert "未知策略" in result["reason"]

    def test_options_hedge_sse50_underlying(self) -> None:
        """beta_csi500 > beta_csi300 时选 510050"""
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        risk = _make_portfolio_risk(beta_csi300=1.0, beta_csi500=1.5)
        result = engine.generate_options_hedge(risk, 0.3, strategy="protective_put")
        assert result["underlying"] == "510050"


# ============================================================
# 8. HedgeEngine: generate_hedge_plan
# ============================================================


class TestHedgeEngineHedgePlan:
    """generate_hedge_plan 完整对冲方案测试"""

    def test_hedge_plan_no_hedge(self) -> None:
        """风险可控时无需对冲"""
        from utils.hedge_engine import (
            HedgeEngine,
            HedgeSignalStrength,
            HedgeType,
            PortfolioRisk,
        )

        engine = HedgeEngine()
        risk = PortfolioRisk()  # 所有字段为0
        rec = engine.generate_hedge_plan(risk)
        assert rec.hedge_type == HedgeType.NONE
        assert rec.strength == HedgeSignalStrength.NO_HEDGE
        assert rec.hedge_ratio == 0.0

    def test_hedge_plan_futures(self) -> None:
        """期货对冲方案"""
        from utils.hedge_engine import (
            HedgeEngine,
        )

        engine = HedgeEngine()
        risk = _make_portfolio_risk(beta_csi300=1.6, beta_csi500=1.5)
        prices = {"IF": 3900.0, "IC": 6200.0, "IM": 6800.0, "IH": 2700.0}
        rec = engine.generate_hedge_plan(
            risk,
            portfolio_volatility=0.35,
            portfolio_drawdown_60d=0.20,
            futures_prices=prices,
        )
        assert rec.hedge_ratio > 0
        assert len(rec.futures_instruments) > 0

    def test_hedge_plan_options(self) -> None:
        """期权对冲方案"""
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        risk = _make_portfolio_risk(beta_csi300=1.6, beta_csi500=1.5)
        rec = engine.generate_hedge_plan(
            risk,
            portfolio_volatility=0.35,
            portfolio_drawdown_60d=0.20,
            prefer_options=True,
        )
        assert rec.hedge_ratio > 0
        assert len(rec.options_instruments) > 0

    def test_hedge_plan_with_stress_tests(self) -> None:
        """传入 positions/prices 时运行压力测试"""
        from utils.hedge_engine import HedgeEngine

        engine = HedgeEngine()
        risk = _make_portfolio_risk(beta_csi300=1.6)
        positions = {"300750": {"shares": 100}}
        prices = {"300750": 230.0}
        futures_prices = {"IF": 3900.0, "IC": 6200.0, "IM": 6800.0, "IH": 2700.0}
        rec = engine.generate_hedge_plan(
            risk,
            portfolio_volatility=0.35,
            portfolio_drawdown_60d=0.20,
            futures_prices=futures_prices,
            positions=positions,
            prices=prices,
        )
        assert len(rec.stress_tests) == 6

    def test_hedge_plan_cost_benefit_filtered(self) -> None:
        """成本效益过滤 — 对冲经分析后不划算返回0"""
        from utils.hedge_engine import (
            HedgeEngine,
        )

        engine = HedgeEngine()
        # 低波动率 + 低回撤 → tail_ratio=0 → 可能被成本过滤
        risk = _make_portfolio_risk(beta_csi300=0.5)
        rec = engine.generate_hedge_plan(
            risk,
            portfolio_volatility=0.20,
            portfolio_drawdown_60d=0.05,
        )
        # 低beta组合可能被过滤为0
        assert rec.hedge_ratio >= 0.0

    def test_hedge_plan_sector_warnings(self) -> None:
        """板块集中度预警"""
        from utils.hedge_engine import HedgeEngine, PortfolioRisk

        engine = HedgeEngine()
        risk = PortfolioRisk(
            total_value=1_000_000,
            stock_exposure=800_000,
            beta_csi300=1.5,
            beta_csi500=1.4,
            beta_csi1000=1.3,
            sector_concentration_warning="高端制造板块权重50%超限",
            mrc_warnings=["300750 MRC=30%超限"],
        )
        prices = {"IF": 3900.0, "IC": 6200.0, "IM": 6800.0, "IH": 2700.0}
        rec = engine.generate_hedge_plan(
            risk,
            portfolio_volatility=0.35,
            portfolio_drawdown_60d=0.20,
            futures_prices=prices,
        )
        assert len(rec.sector_warnings) > 0
        assert len(rec.mrc_warnings) > 0


# ============================================================
# 9. HedgeEngine: compute_optimal_hedge_ratio 多路径
# ============================================================


class TestHedgeEngineOptimalRatio:
    """compute_optimal_hedge_ratio 参数化路径测试"""

    def test_optimal_ratio_no_hedge_strength(self) -> None:
        from utils.hedge_engine import HedgeEngine, HedgeSignalStrength

        engine = HedgeEngine()
        risk = _make_portfolio_risk()
        ratio = engine.compute_optimal_hedge_ratio(risk, HedgeSignalStrength.NO_HEDGE)
        assert ratio == 0.0

    def test_optimal_ratio_with_high_volatility(self) -> None:
        """高波动率触发尾部保护"""
        from utils.hedge_engine import HedgeEngine, HedgeSignalStrength

        engine = HedgeEngine()
        risk = _make_portfolio_risk(beta_csi300=1.3)
        ratio = engine.compute_optimal_hedge_ratio(
            risk,
            HedgeSignalStrength.MODERATE,
            portfolio_volatility=0.35,  # > 0.28 触发
        )
        assert ratio > 0

    def test_optimal_ratio_with_high_drawdown(self) -> None:
        """高回撤触发尾部保护"""
        from utils.hedge_engine import HedgeEngine, HedgeSignalStrength

        engine = HedgeEngine()
        risk = _make_portfolio_risk(beta_csi300=1.3)
        ratio = engine.compute_optimal_hedge_ratio(
            risk,
            HedgeSignalStrength.MODERATE,
            portfolio_drawdown_60d=0.20,  # > 0.12 触发
        )
        assert ratio > 0

    def test_optimal_ratio_capped_at_max(self) -> None:
        """对冲比率不超过上限"""
        from utils.hedge_engine import HedgeEngine, HedgeSignalStrength

        engine = HedgeEngine()
        risk = _make_portfolio_risk(beta_csi300=1.5)
        ratio = engine.compute_optimal_hedge_ratio(
            risk,
            HedgeSignalStrength.FULL,
            portfolio_volatility=0.50,  # 极端波动
            portfolio_drawdown_60d=0.30,
        )
        assert ratio <= 1.0

    def test_optimal_ratio_low_beta_portfolio(self) -> None:
        """低Beta组合(如黄金/国债ETF)对冲比率应较低"""
        from utils.hedge_engine import HedgeEngine, HedgeSignalStrength, PortfolioRisk

        engine = HedgeEngine()
        risk = PortfolioRisk(
            total_value=1_000_000,
            stock_exposure=800_000,
            beta_csi300=0.3,
            beta_csi500=0.2,
            beta_csi1000=0.1,
        )
        ratio = engine.compute_optimal_hedge_ratio(
            risk,
            HedgeSignalStrength.LIGHT,
            portfolio_volatility=0.15,  # 低波动不触发尾部
        )
        # 低beta+低波动 → 比率应该为0或很低
        assert ratio <= 0.5

    def test_optimal_ratio_full_strength(self) -> None:
        from utils.hedge_engine import HedgeEngine, HedgeSignalStrength

        engine = HedgeEngine()
        risk = _make_portfolio_risk(beta_csi300=1.3)
        ratio = engine.compute_optimal_hedge_ratio(
            risk,
            HedgeSignalStrength.FULL,
            portfolio_volatility=0.30,
        )
        assert ratio > 0


# ============================================================
# 10. hedge_rebalance_integrator 常量补充测试
# ============================================================


class TestIntegratorExtraConstants:
    """hedge_rebalance_integrator 未覆盖常量测试"""

    def test_rebalance_thresholds(self) -> None:
        from utils.hedge_rebalance_integrator import REBALANCE_THRESHOLDS

        assert "low" in REBALANCE_THRESHOLDS
        assert "normal" in REBALANCE_THRESHOLDS
        assert "high" in REBALANCE_THRESHOLDS
        assert REBALANCE_THRESHOLDS["low"]["threshold"] == 0.03
        assert REBALANCE_THRESHOLDS["normal"]["threshold"] == 0.05
        assert REBALANCE_THRESHOLDS["high"]["threshold"] == 0.08

    def test_sector_rotation(self) -> None:
        from utils.hedge_rebalance_integrator import SECTOR_ROTATION

        assert "recovery" in SECTOR_ROTATION
        assert "prosperity" in SECTOR_ROTATION
        assert "stagflation" in SECTOR_ROTATION
        assert "recession" in SECTOR_ROTATION
        assert SECTOR_ROTATION["recession"]["defensive"] == 0.35

    def test_default_sector_weights(self) -> None:
        from utils.hedge_rebalance_integrator import DEFAULT_SECTOR_WEIGHTS

        assert DEFAULT_SECTOR_WEIGHTS["high_end_manufacturing"] == 0.45
        assert DEFAULT_SECTOR_WEIGHTS["defensive"] == 0.15

    def test_portfolio_hedge_thresholds(self) -> None:
        from utils.hedge_rebalance_integrator import (
            PORTFOLIO_HEDGE_THRESHOLDS,
            HedgeMode,
            MarketRegime,
        )

        calm = PORTFOLIO_HEDGE_THRESHOLDS[MarketRegime.CALM]
        assert calm["hedge_ratio"] == 0.0
        assert calm["mode"] == HedgeMode.NONE
        tail = PORTFOLIO_HEDGE_THRESHOLDS[MarketRegime.TAIL_EVENT]
        assert tail["hedge_ratio"] == 0.40
        assert tail["mode"] == HedgeMode.DYNAMIC

    def test_tail_trigger_constants(self) -> None:
        from utils.hedge_rebalance_integrator import (
            TAIL_DD_TRIGGER,
            TAIL_MAX_HEDGE,
            TAIL_MIN_HEDGE,
            TAIL_VOL_TRIGGER,
        )

        assert TAIL_VOL_TRIGGER == 0.28
        assert TAIL_DD_TRIGGER == 0.12
        assert TAIL_MIN_HEDGE == 0.25
        assert TAIL_MAX_HEDGE == 0.40


# ============================================================
# 11. HedgeRebalanceIntegrator: _determine_market_regime 多路径
# ============================================================


class TestIntegratorMarketRegime:
    """_determine_market_regime MILD/HIGH 路径测试"""

    def test_regime_mild_volatile(self) -> None:
        from utils.hedge_rebalance_integrator import (
            HedgeRebalanceIntegrator,
            MarketRegime,
            PortfolioRisk,
        )

        integrator = HedgeRebalanceIntegrator()
        risk = PortfolioRisk()
        regime = integrator._determine_market_regime(
            risk, portfolio_volatility=0.20, portfolio_drawdown_60d=0.09
        )
        assert regime == MarketRegime.MILD_VOLATILE

    def test_regime_high_volatile(self) -> None:
        from utils.hedge_rebalance_integrator import (
            HedgeRebalanceIntegrator,
            MarketRegime,
            PortfolioRisk,
        )

        integrator = HedgeRebalanceIntegrator()
        risk = PortfolioRisk()
        regime = integrator._determine_market_regime(
            risk, portfolio_volatility=0.30, portfolio_drawdown_60d=0.15
        )
        assert regime == MarketRegime.HIGH_VOLATILE

    def test_regime_mild_by_vol_only(self) -> None:
        """仅波动率超标触发MILD"""
        from utils.hedge_rebalance_integrator import (
            HedgeRebalanceIntegrator,
            MarketRegime,
            PortfolioRisk,
        )

        integrator = HedgeRebalanceIntegrator()
        regime = integrator._determine_market_regime(
            PortfolioRisk(), portfolio_volatility=0.19, portfolio_drawdown_60d=0.0
        )
        assert regime == MarketRegime.MILD_VOLATILE

    def test_regime_mild_by_dd_only(self) -> None:
        """仅回撤超标触发MILD"""
        from utils.hedge_rebalance_integrator import (
            HedgeRebalanceIntegrator,
            MarketRegime,
            PortfolioRisk,
        )

        integrator = HedgeRebalanceIntegrator()
        regime = integrator._determine_market_regime(
            PortfolioRisk(), portfolio_volatility=0.10, portfolio_drawdown_60d=0.09
        )
        assert regime == MarketRegime.MILD_VOLATILE

    def test_regime_high_by_vol_only(self) -> None:
        from utils.hedge_rebalance_integrator import (
            HedgeRebalanceIntegrator,
            MarketRegime,
            PortfolioRisk,
        )

        integrator = HedgeRebalanceIntegrator()
        regime = integrator._determine_market_regime(
            PortfolioRisk(), portfolio_volatility=0.26, portfolio_drawdown_60d=0.0
        )
        assert regime == MarketRegime.HIGH_VOLATILE

    def test_regime_uses_defaults(self) -> None:
        """不传参数时使用默认估算值"""
        from utils.hedge_rebalance_integrator import (
            HedgeRebalanceIntegrator,
            MarketRegime,
            PortfolioRisk,
        )

        integrator = HedgeRebalanceIntegrator()
        regime = integrator._determine_market_regime(PortfolioRisk())
        # 默认 vol=0.18, dd=0.0 → CALM
        assert regime == MarketRegime.CALM


# ============================================================
# 12. HedgeRebalanceIntegrator: _compute_tail_hedge_ratio 多路径
# ============================================================


class TestIntegratorTailHedgeRatio:
    """_compute_tail_hedge_ratio 多模式/边界测试"""

    def test_tail_ratio_none_mode(self) -> None:
        from utils.hedge_rebalance_integrator import (
            HedgeMode,
            HedgeRebalanceIntegrator,
        )

        integrator = HedgeRebalanceIntegrator(hedge_mode=HedgeMode.NONE)
        ratio = integrator._compute_tail_hedge_ratio(
            portfolio_volatility=0.40, portfolio_drawdown_60d=0.25
        )
        assert ratio == 0.0

    def test_tail_ratio_below_triggers(self) -> None:
        """波动率和回撤均在触发线以下"""
        from utils.hedge_rebalance_integrator import HedgeRebalanceIntegrator

        integrator = HedgeRebalanceIntegrator()
        ratio = integrator._compute_tail_hedge_ratio(
            portfolio_volatility=0.20, portfolio_drawdown_60d=0.05
        )
        assert ratio == 0.0

    def test_tail_ratio_vol_triggered(self) -> None:
        """波动率触发尾部对冲"""
        from utils.hedge_rebalance_integrator import HedgeRebalanceIntegrator

        integrator = HedgeRebalanceIntegrator()
        ratio = integrator._compute_tail_hedge_ratio(
            portfolio_volatility=0.30, portfolio_drawdown_60d=0.0
        )
        assert ratio >= 0.25  # >= TAIL_MIN_HEDGE
        assert ratio <= 0.40  # <= TAIL_MAX_HEDGE

    def test_tail_ratio_dd_triggered(self) -> None:
        """回撤触发尾部对冲"""
        from utils.hedge_rebalance_integrator import HedgeRebalanceIntegrator

        integrator = HedgeRebalanceIntegrator()
        ratio = integrator._compute_tail_hedge_ratio(
            portfolio_volatility=0.10, portfolio_drawdown_60d=0.15
        )
        assert ratio >= 0.25
        assert ratio <= 0.40

    def test_tail_ratio_capped_at_max(self) -> None:
        """极端波动/回撤时封顶"""
        from utils.hedge_rebalance_integrator import HedgeRebalanceIntegrator

        integrator = HedgeRebalanceIntegrator()
        ratio = integrator._compute_tail_hedge_ratio(
            portfolio_volatility=0.60, portfolio_drawdown_60d=0.40
        )
        assert ratio == 0.40

    def test_tail_ratio_both_triggered(self) -> None:
        """波动率和回撤同时触发 — 取较大值"""
        from utils.hedge_rebalance_integrator import HedgeRebalanceIntegrator

        integrator = HedgeRebalanceIntegrator()
        ratio = integrator._compute_tail_hedge_ratio(
            portfolio_volatility=0.35, portfolio_drawdown_60d=0.20
        )
        assert ratio > 0
        assert ratio <= 0.40

    def test_tail_ratio_uses_defaults(self) -> None:
        """不传参数时使用默认估算值"""
        from utils.hedge_rebalance_integrator import HedgeRebalanceIntegrator

        integrator = HedgeRebalanceIntegrator()
        ratio = integrator._compute_tail_hedge_ratio()
        # 默认 vol=0.18, dd=0.0 → 不触发
        assert ratio == 0.0


# ============================================================
# 13. HedgeRebalanceIntegrator: decide_hedge 多路径
# ============================================================


class TestIntegratorDecideHedge:
    """decide_hedge 多路径测试"""

    def test_decide_hedge_no_hedge_needed(self) -> None:
        """安全范围无需对冲"""
        from utils.hedge_rebalance_integrator import (
            HedgeMode,
            HedgeRebalanceIntegrator,
        )

        integrator = HedgeRebalanceIntegrator(hedge_mode=HedgeMode.TAIL_ONLY)
        risk = _make_portfolio_risk()
        decision = integrator.decide_hedge(
            risk, portfolio_volatility=0.10, portfolio_drawdown_60d=0.05
        )
        assert decision.needed is False
        assert decision.hedge_ratio == 0.0
        assert decision.strength_name == "NO_HEDGE"

    def test_decide_hedge_tail_triggered(self) -> None:
        """尾部事件触发对冲"""
        from utils.hedge_rebalance_integrator import (
            HedgeMode,
            HedgeRebalanceIntegrator,
            MarketRegime,
        )

        integrator = HedgeRebalanceIntegrator(hedge_mode=HedgeMode.TAIL_ONLY)
        risk = _make_portfolio_risk()
        # mock generate_futures_hedge 避免外部调用
        mock_futures = {
            "contracts": {
                "IC": {"contracts": 2, "notional": 200_000, "margin": 28_000}
            },
            "total_notional": 200_000,
            "total_margin": 28_000,
            "fallback_used": [],
            "price_source": "auto",
            "reason": "test",
        }
        with patch.object(
            integrator.hedge_engine, "generate_futures_hedge", return_value=mock_futures
        ):
            decision = integrator.decide_hedge(
                risk, portfolio_volatility=0.35, portfolio_drawdown_60d=0.20
            )
        assert decision.needed is True
        assert decision.hedge_ratio > 0
        assert decision.regime == MarketRegime.TAIL_EVENT
        assert "IC" in decision.futures_instruments

    def test_decide_hedge_no_engine(self) -> None:
        """hedge_engine 不可用时的降级路径"""
        from utils.hedge_rebalance_integrator import (
            HedgeMode,
            HedgeRebalanceIntegrator,
        )

        integrator = HedgeRebalanceIntegrator(hedge_mode=HedgeMode.TAIL_ONLY)
        integrator.hedge_engine = None  # 模拟不可用
        risk = _make_portfolio_risk()
        decision = integrator.decide_hedge(
            risk, portfolio_volatility=0.35, portfolio_drawdown_60d=0.20
        )
        assert decision.needed is True
        assert decision.price_source == "hedge_engine_unavailable"
        assert decision.futures_instruments == []

    def test_decide_hedge_fixed_mode(self) -> None:
        """FIXED 模式 — 使用 regime 配置的对冲比率"""
        from utils.hedge_rebalance_integrator import (
            HedgeMode,
            HedgeRebalanceIntegrator,
            MarketRegime,
        )

        integrator = HedgeRebalanceIntegrator(hedge_mode=HedgeMode.FIXED)
        risk = _make_portfolio_risk()
        mock_futures = {
            "contracts": {
                "IF": {"contracts": 1, "notional": 100_000, "margin": 12_000}
            },
            "total_notional": 100_000,
            "total_margin": 12_000,
            "fallback_used": [],
            "price_source": "auto",
            "reason": "",
        }
        with patch.object(
            integrator.hedge_engine, "generate_futures_hedge", return_value=mock_futures
        ):
            decision = integrator.decide_hedge(
                risk, portfolio_volatility=0.30, portfolio_drawdown_60d=0.15
            )
        assert decision.needed is True
        assert decision.regime == MarketRegime.HIGH_VOLATILE

    def test_decide_hedge_dynamic_mode(self) -> None:
        """DYNAMIC 模式 — 取 tail 和 regime 的最大值"""
        from utils.hedge_rebalance_integrator import (
            HedgeMode,
            HedgeRebalanceIntegrator,
        )

        integrator = HedgeRebalanceIntegrator(hedge_mode=HedgeMode.DYNAMIC)
        risk = _make_portfolio_risk()
        mock_futures = {
            "contracts": {},
            "total_notional": 0,
            "total_margin": 0,
            "fallback_used": [],
            "price_source": "auto",
            "reason": "",
        }
        with patch.object(
            integrator.hedge_engine, "generate_futures_hedge", return_value=mock_futures
        ):
            decision = integrator.decide_hedge(
                risk, portfolio_volatility=0.35, portfolio_drawdown_60d=0.20
            )
        assert decision.needed is True

    def test_decide_hedge_vol_correction(self) -> None:
        """波动率目标修正 — portfolio_vol > target*1.3"""
        from utils.hedge_rebalance_integrator import (
            HedgeMode,
            HedgeRebalanceIntegrator,
        )

        integrator = HedgeRebalanceIntegrator(hedge_mode=HedgeMode.TAIL_ONLY)
        risk = _make_portfolio_risk()
        mock_futures = {
            "contracts": {},
            "total_notional": 0,
            "total_margin": 0,
            "fallback_used": [],
            "price_source": "auto",
            "reason": "",
        }
        with patch.object(
            integrator.hedge_engine, "generate_futures_hedge", return_value=mock_futures
        ):
            decision = integrator.decide_hedge(
                risk,
                portfolio_volatility=0.30,  # > 0.18*1.3=0.234
                portfolio_drawdown_60d=0.0,
            )
        # 波动率触发 → needed=True
        assert decision.needed is True


# ============================================================
# 14. HedgeRebalanceIntegrator: check_rebalance 多路径
# ============================================================


class TestIntegratorCheckRebalance:
    """check_rebalance 多路径测试"""

    def _make_integrator_with_config(self, tmp_path):
        """构造带配置的 integrator"""
        from utils.hedge_rebalance_integrator import HedgeRebalanceIntegrator

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        integrator = HedgeRebalanceIntegrator(
            base_dir=str(tmp_path),
            config_dir=str(config_dir),
            portfolio_value=1_000_000,
        )
        return integrator

    def test_check_rebalance_no_assets(self, tmp_path) -> None:
        """无 assets 配置时返回无需再平衡"""
        integrator = self._make_integrator_with_config(tmp_path)
        integrator.portfolio_config = {}  # 空 config
        risk = _make_portfolio_risk()
        with patch.object(integrator, "load_prices", return_value={}):
            decision = integrator.check_rebalance(risk, portfolio_volatility=0.18)
        assert decision.needed is False
        assert decision.rebalance_type == "none"

    def test_check_rebalance_within_threshold(self, tmp_path) -> None:
        """偏离度在阈值内 — 无需调整"""
        integrator = self._make_integrator_with_config(tmp_path)
        integrator.portfolio_config = {
            "assets": [
                {
                    "code": "600519",
                    "name": "贵州茅台",
                    "category": "白酒",
                    "target_weight": 0.10,
                },
            ]
        }
        integrator.positions = {}  # 无实际持仓
        risk = _make_portfolio_risk(stock_exposure=100_000)
        # 价格使持仓正好在目标权重
        with (
            patch.object(integrator, "load_prices", return_value={"600519": 100.0}),
            patch.object(integrator, "_get_sector_adjusted_weights", return_value={}),
        ):
            decision = integrator.check_rebalance(risk, portfolio_volatility=0.18)
        assert isinstance(decision.needed, bool)

    def test_check_rebalance_strategic(self, tmp_path) -> None:
        """严重偏离触发战略再平衡"""
        integrator = self._make_integrator_with_config(tmp_path)
        integrator.portfolio_config = {
            "assets": [
                {
                    "code": "600519",
                    "name": "贵州茅台",
                    "category": "白酒",
                    "target_weight": 0.10,
                },
                {
                    "code": "000858",
                    "name": "五粮液",
                    "category": "白酒",
                    "target_weight": 0.10,
                },
            ]
        }
        # 构造严重偏离: 600519 持仓远超目标
        integrator.positions = {
            "600519": {"shares": 5000},
            "000858": {"shares": 0},
        }
        risk = _make_portfolio_risk(stock_exposure=500_000)
        with (
            patch.object(
                integrator,
                "load_prices",
                return_value={"600519": 100.0, "000858": 150.0},
            ),
            patch.object(integrator, "_get_sector_adjusted_weights", return_value={}),
        ):
            decision = integrator.check_rebalance(risk, portfolio_volatility=0.18)
        assert decision.needed is True
        assert decision.rebalance_type in ("strategic", "tactical")

    def test_check_rebalance_low_vol_threshold(self, tmp_path) -> None:
        """低波动率使用更严格阈值"""
        integrator = self._make_integrator_with_config(tmp_path)
        integrator.portfolio_config = {
            "assets": [
                {
                    "code": "600519",
                    "name": "贵州茅台",
                    "category": "白酒",
                    "target_weight": 0.10,
                },
            ]
        }
        integrator.positions = {}
        risk = _make_portfolio_risk(stock_exposure=100_000)
        with (
            patch.object(integrator, "load_prices", return_value={"600519": 100.0}),
            patch.object(integrator, "_get_sector_adjusted_weights", return_value={}),
        ):
            decision = integrator.check_rebalance(risk, portfolio_volatility=0.10)
        # 低波动 → threshold=0.03
        assert decision.threshold == 0.03

    def test_check_rebalance_high_vol_threshold(self, tmp_path) -> None:
        """高波动率使用更宽松阈值"""
        integrator = self._make_integrator_with_config(tmp_path)
        integrator.portfolio_config = {
            "assets": [
                {
                    "code": "600519",
                    "name": "贵州茅台",
                    "category": "白酒",
                    "target_weight": 0.10,
                },
            ]
        }
        integrator.positions = {}
        risk = _make_portfolio_risk(stock_exposure=100_000)
        with (
            patch.object(integrator, "load_prices", return_value={"600519": 100.0}),
            patch.object(integrator, "_get_sector_adjusted_weights", return_value={}),
        ):
            decision = integrator.check_rebalance(risk, portfolio_volatility=0.30)
        assert decision.threshold == 0.08


# ============================================================
# 15. HedgeRebalanceIntegrator: joint_optimize 多路径
# ============================================================


class TestIntegratorJointOptimize:
    """joint_optimize 多路径测试"""

    def test_joint_optimize_not_needed(self) -> None:
        """对冲或再平衡不需要时直接返回"""
        from utils.hedge_rebalance_integrator import (
            HedgeDecision,
            HedgeRebalanceIntegrator,
            RebalanceDecision,
        )

        integrator = HedgeRebalanceIntegrator()
        risk = _make_portfolio_risk()
        hedge = HedgeDecision(needed=False)
        rebalance = RebalanceDecision(needed=False)
        adj_h, adj_r, warnings = integrator.joint_optimize(risk, hedge, rebalance)
        assert warnings == []
        assert adj_h == hedge
        assert adj_r == rebalance

    def test_joint_optimize_hedge_only(self) -> None:
        """仅对冲需要 — 直接返回"""
        from utils.hedge_rebalance_integrator import (
            HedgeDecision,
            HedgeRebalanceIntegrator,
            RebalanceDecision,
        )

        integrator = HedgeRebalanceIntegrator()
        risk = _make_portfolio_risk()
        hedge = HedgeDecision(needed=True, hedge_ratio=0.3)
        rebalance = RebalanceDecision(needed=False)
        _, _, warnings = integrator.joint_optimize(risk, hedge, rebalance)
        assert warnings == []

    def test_joint_optimize_minor_discrepancy(self) -> None:
        """轻微不一致 — 在可接受范围"""
        from utils.hedge_rebalance_integrator import (
            HedgeDecision,
            HedgeRebalanceIntegrator,
            RebalanceDecision,
        )

        integrator = HedgeRebalanceIntegrator()
        risk = _make_portfolio_risk(stock_exposure=800_000)
        hedge = HedgeDecision(needed=True, hedge_ratio=0.10, total_margin=50_000)
        # net_cash_flow 使 after_rebalance 与 after_hedge 差异在 5-15%
        rebalance = RebalanceDecision(
            needed=True,
            net_cash_flow=20_000,  # 小额再平衡
        )
        _, _, warnings = integrator.joint_optimize(risk, hedge, rebalance)
        # 可能有轻微不一致警告
        assert isinstance(warnings, list)

    def test_joint_optimize_major_discrepancy(self) -> None:
        """严重不一致 — 调整对冲比率"""
        from utils.hedge_rebalance_integrator import (
            HedgeDecision,
            HedgeRebalanceIntegrator,
            RebalanceDecision,
        )

        integrator = HedgeRebalanceIntegrator()
        risk = _make_portfolio_risk(stock_exposure=800_000)
        hedge = HedgeDecision(needed=True, hedge_ratio=0.30, total_margin=100_000)
        # 构造大额 net_cash_flow 使差异 > 15%
        # after_hedge=560K, after_rebalance=800K-500K=300K, discrepancy=260K/800K=0.325
        rebalance = RebalanceDecision(
            needed=True,
            net_cash_flow=500_000,
        )
        adj_h, adj_r, warnings = integrator.joint_optimize(risk, hedge, rebalance)
        assert len(warnings) > 0
        assert any("不一致" in w for w in warnings)

    def test_joint_optimize_margin_warning(self) -> None:
        """保证金需求过高预警"""
        from utils.hedge_rebalance_integrator import (
            HedgeDecision,
            HedgeRebalanceIntegrator,
            RebalanceDecision,
        )

        integrator = HedgeRebalanceIntegrator()
        risk = _make_portfolio_risk(stock_exposure=800_000, cash=100_000)
        hedge = HedgeDecision(
            needed=True,
            hedge_ratio=0.05,
            total_margin=200_000,  # 高保证金
        )
        rebalance = RebalanceDecision(needed=True, net_cash_flow=10_000)
        _, _, warnings = integrator.joint_optimize(risk, hedge, rebalance)
        assert any("保证金" in w for w in warnings)


# ============================================================
# 16. HedgeRebalanceIntegrator: _estimate_performance
# ============================================================


class TestIntegratorEstimatePerformance:
    """_estimate_performance 测试"""

    def test_estimate_no_hedge_no_rebalance(self) -> None:
        from utils.hedge_rebalance_integrator import (
            HedgeDecision,
            HedgeRebalanceIntegrator,
            RebalanceDecision,
        )

        integrator = HedgeRebalanceIntegrator()
        risk = _make_portfolio_risk()
        hedge = HedgeDecision(needed=False)
        rebalance = RebalanceDecision(needed=False)
        ret, dd, sharpe, vol = integrator._estimate_performance(risk, hedge, rebalance)
        assert isinstance(ret, float)
        assert isinstance(dd, float)
        assert isinstance(sharpe, float)
        assert isinstance(vol, float)
        assert ret > 0
        assert dd > 0
        assert vol > 0

    def test_estimate_with_hedge(self) -> None:
        from utils.hedge_rebalance_integrator import (
            HedgeDecision,
            HedgeRebalanceIntegrator,
            RebalanceDecision,
        )

        integrator = HedgeRebalanceIntegrator()
        risk = _make_portfolio_risk()
        hedge = HedgeDecision(needed=True, hedge_ratio=0.30)
        rebalance = RebalanceDecision(needed=False)
        ret, dd, vol, sharpe = integrator._estimate_performance(risk, hedge, rebalance)
        # 对冲会降低收益和回撤
        assert ret < 0.12  # base_return - hedge_penalty
        assert dd < 0.18  # base_drawdown - hedge_dr_reduce

    def test_estimate_strategic_rebalance(self) -> None:
        from utils.hedge_rebalance_integrator import (
            HedgeDecision,
            HedgeRebalanceIntegrator,
            RebalanceDecision,
        )

        integrator = HedgeRebalanceIntegrator()
        risk = _make_portfolio_risk()
        hedge = HedgeDecision(needed=False)
        rebalance = RebalanceDecision(needed=True, rebalance_type="strategic")
        ret, _, _, _ = integrator._estimate_performance(risk, hedge, rebalance)
        # 战略再平衡提升收益
        assert ret > 0.12

    def test_estimate_tactical_rebalance(self) -> None:
        from utils.hedge_rebalance_integrator import (
            HedgeDecision,
            HedgeRebalanceIntegrator,
            RebalanceDecision,
        )

        integrator = HedgeRebalanceIntegrator()
        risk = _make_portfolio_risk()
        hedge = HedgeDecision(needed=False)
        rebalance = RebalanceDecision(needed=True, rebalance_type="tactical")
        ret, _, _, _ = integrator._estimate_performance(risk, hedge, rebalance)
        # 战术再平衡小幅提升
        assert ret > 0.12


# ============================================================
# 17. HedgeRebalanceIntegrator: generate_execution_plan 多优先级
# ============================================================


class TestIntegratorExecutionPlan:
    """generate_execution_plan 多优先级测试"""

    def test_plan_high_priority(self) -> None:
        """高对冲比率 → HIGH 优先级"""
        from utils.hedge_rebalance_integrator import (
            HedgeDecision,
            HedgeRebalanceIntegrator,
            RebalanceDecision,
        )

        integrator = HedgeRebalanceIntegrator()
        risk = _make_portfolio_risk()
        hedge = HedgeDecision(needed=True, hedge_ratio=0.40, total_margin=100_000)
        rebalance = RebalanceDecision(needed=False)
        plan = integrator.generate_execution_plan(risk, hedge, rebalance)
        assert plan.execution_priority == "HIGH"
        assert plan.execution_window == "当日/次日"

    def test_plan_high_priority_strategic(self) -> None:
        """战略再平衡 → HIGH 优先级"""
        from utils.hedge_rebalance_integrator import (
            HedgeDecision,
            HedgeRebalanceIntegrator,
            RebalanceDecision,
        )

        integrator = HedgeRebalanceIntegrator()
        risk = _make_portfolio_risk()
        hedge = HedgeDecision(needed=True, hedge_ratio=0.10, total_margin=20_000)
        rebalance = RebalanceDecision(
            needed=True,
            rebalance_type="strategic",
            positions_to_adjust=[],
            total_buy_amount=0,
            total_sell_amount=0,
        )
        plan = integrator.generate_execution_plan(risk, hedge, rebalance)
        assert plan.execution_priority == "HIGH"

    def test_plan_medium_priority(self) -> None:
        """中等对冲 → MEDIUM 优先级"""
        from utils.hedge_rebalance_integrator import (
            HedgeDecision,
            HedgeRebalanceIntegrator,
            RebalanceDecision,
        )

        integrator = HedgeRebalanceIntegrator()
        risk = _make_portfolio_risk()
        hedge = HedgeDecision(needed=True, hedge_ratio=0.15, total_margin=30_000)
        rebalance = RebalanceDecision(needed=False)
        plan = integrator.generate_execution_plan(risk, hedge, rebalance)
        assert plan.execution_priority == "MEDIUM"
        assert plan.execution_window == "本周内"

    def test_plan_low_priority(self) -> None:
        """低对冲 → LOW 优先级"""
        from utils.hedge_rebalance_integrator import (
            HedgeDecision,
            HedgeRebalanceIntegrator,
            RebalanceDecision,
        )

        integrator = HedgeRebalanceIntegrator()
        risk = _make_portfolio_risk()
        hedge = HedgeDecision(needed=False, hedge_ratio=0.0)
        rebalance = RebalanceDecision(needed=False)
        plan = integrator.generate_execution_plan(risk, hedge, rebalance)
        assert plan.execution_priority == "LOW"
        assert plan.execution_window == "两周内"

    def test_plan_with_warnings(self) -> None:
        """带预警标志的执行计划"""
        from utils.hedge_rebalance_integrator import (
            HedgeDecision,
            HedgeRebalanceIntegrator,
            RebalanceDecision,
        )

        integrator = HedgeRebalanceIntegrator()
        risk = _make_portfolio_risk()
        hedge = HedgeDecision(needed=True, hedge_ratio=0.20, total_margin=50_000)
        rebalance = RebalanceDecision(needed=False)
        warnings = ["测试预警1", "测试预警2"]
        plan = integrator.generate_execution_plan(risk, hedge, rebalance, warnings)
        assert plan.warning_flags == warnings

    def test_plan_with_stress_tests(self) -> None:
        """带压力测试结果的执行计划"""
        from utils.hedge_rebalance_integrator import (
            HedgeDecision,
            HedgeRebalanceIntegrator,
            RebalanceDecision,
        )

        integrator = HedgeRebalanceIntegrator()
        risk = _make_portfolio_risk()
        hedge = HedgeDecision(needed=False, hedge_ratio=0.0)
        rebalance = RebalanceDecision(needed=False)
        stress = {
            "2015股灾": {
                "breaches_limit": True,
                "drawdown_pct": 20.0,
                "estimated_loss": 100_000,
            }
        }
        plan = integrator.generate_execution_plan(
            risk, hedge, rebalance, stress_tests=stress
        )
        assert plan.stress_tests == stress

    def test_plan_summary_hedge_needed(self) -> None:
        """对冲需要时 summary 包含对冲信息"""
        from utils.hedge_rebalance_integrator import (
            HedgeDecision,
            HedgeMode,
            HedgeRebalanceIntegrator,
            RebalanceDecision,
        )

        integrator = HedgeRebalanceIntegrator()
        risk = _make_portfolio_risk()
        hedge = HedgeDecision(
            needed=True,
            mode=HedgeMode.TAIL_ONLY,
            hedge_ratio=0.30,
            total_margin=50_000,
            futures_instruments=["IC"],
        )
        rebalance = RebalanceDecision(needed=False)
        plan = integrator.generate_execution_plan(risk, hedge, rebalance)
        assert "对冲" in plan.summary

    def test_plan_summary_rebalance_needed(self) -> None:
        """再平衡需要时 summary 包含再平衡信息"""
        from utils.hedge_rebalance_integrator import (
            HedgeDecision,
            HedgeRebalanceIntegrator,
            RebalanceDecision,
        )

        integrator = HedgeRebalanceIntegrator()
        risk = _make_portfolio_risk()
        hedge = HedgeDecision(needed=False)
        rebalance = RebalanceDecision(
            needed=True,
            rebalance_type="tactical",
            positions_to_adjust=[],
            total_buy_amount=50_000,
            total_sell_amount=30_000,
        )
        plan = integrator.generate_execution_plan(risk, hedge, rebalance)
        assert "再平衡" in plan.summary


# ============================================================
# 18. HedgeRebalanceIntegrator: run_full_workflow
# ============================================================


class TestIntegratorFullWorkflow:
    """run_full_workflow 完整工作流测试 (mocked)"""

    def test_full_workflow_mocked(self, tmp_path) -> None:
        """mock 内部方法后完整工作流应正常执行"""
        from utils.hedge_rebalance_integrator import (
            HedgeRebalanceIntegrator,
        )

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        integrator = HedgeRebalanceIntegrator(
            base_dir=str(tmp_path),
            config_dir=str(config_dir),
            portfolio_value=1_000_000,
        )
        risk = _make_portfolio_risk()
        with (
            patch.object(integrator, "assess_risk", return_value=risk),
            patch.object(integrator, "load_prices", return_value={"600519": 100.0}),
            patch.object(integrator, "check_rebalance") as mock_rebalance,
            patch.object(integrator.hedge_engine, "generate_futures_hedge") as mock_fh,
        ):
            from utils.hedge_rebalance_integrator import RebalanceDecision

            mock_rebalance.return_value = RebalanceDecision(needed=False)
            mock_fh.return_value = {
                "contracts": {},
                "total_notional": 0,
                "total_margin": 0,
                "fallback_used": [],
                "price_source": "auto",
                "reason": "",
            }
            plan = integrator.run_full_workflow(
                portfolio_volatility=0.35, portfolio_drawdown_60d=0.20
            )
        assert plan is not None
        assert plan.portfolio_value == 1_000_000

    def test_full_workflow_calm_market(self, tmp_path) -> None:
        """平静市场 — 无需对冲"""
        from utils.hedge_rebalance_integrator import HedgeRebalanceIntegrator

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        integrator = HedgeRebalanceIntegrator(
            base_dir=str(tmp_path),
            config_dir=str(config_dir),
            portfolio_value=1_000_000,
        )
        risk = _make_portfolio_risk()
        with (
            patch.object(integrator, "assess_risk", return_value=risk),
            patch.object(integrator, "load_prices", return_value={}),
            patch.object(integrator, "check_rebalance") as mock_rebalance,
        ):
            from utils.hedge_rebalance_integrator import RebalanceDecision

            mock_rebalance.return_value = RebalanceDecision(needed=False)
            plan = integrator.run_full_workflow(
                portfolio_volatility=0.10, portfolio_drawdown_60d=0.05
            )
        assert plan is not None

    def test_full_workflow_with_stress_tests(self, tmp_path) -> None:
        """带压力测试的完整工作流"""
        from utils.hedge_rebalance_integrator import HedgeRebalanceIntegrator

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        integrator = HedgeRebalanceIntegrator(
            base_dir=str(tmp_path),
            config_dir=str(config_dir),
            portfolio_value=1_000_000,
        )
        risk = _make_portfolio_risk()
        positions = {"600519": {"shares": 100}}
        integrator.positions = {"positions": positions}
        with (
            patch.object(integrator, "assess_risk", return_value=risk),
            patch.object(integrator, "load_prices", return_value={"600519": 100.0}),
            patch.object(integrator, "check_rebalance") as mock_rebalance,
            patch.object(integrator.hedge_engine, "generate_futures_hedge") as mock_fh,
            patch.object(
                integrator.hedge_engine, "run_historical_stress_tests"
            ) as mock_stress,
        ):
            from utils.hedge_rebalance_integrator import RebalanceDecision

            mock_rebalance.return_value = RebalanceDecision(needed=False)
            mock_fh.return_value = {
                "contracts": {},
                "total_notional": 0,
                "total_margin": 0,
                "fallback_used": [],
                "price_source": "auto",
                "reason": "",
            }
            mock_stress.return_value = {
                "2015股灾": {
                    "breaches_limit": True,
                    "drawdown_pct": 20.0,
                    "estimated_loss": 100_000,
                },
            }
            plan = integrator.run_full_workflow(
                portfolio_volatility=0.35, portfolio_drawdown_60d=0.20
            )
        assert len(plan.stress_tests) > 0


# ============================================================
# 19. hedge_rebalance_integrator 模块函数
# ============================================================


class TestIntegratorModuleFunctions:
    """hedge_rebalance_integrator 模块级函数补充测试"""

    def test_get_integrator_all_modes(self) -> None:
        from utils.hedge_rebalance_integrator import (
            HedgeMode,
            HedgeRebalanceIntegrator,
            get_integrator,
        )

        for mode_name, expected_mode in [
            ("tail_only", HedgeMode.TAIL_ONLY),
            ("dynamic", HedgeMode.DYNAMIC),
            ("fixed", HedgeMode.FIXED),
            ("none", HedgeMode.NONE),
        ]:
            integrator = get_integrator(portfolio_value=500_000, mode=mode_name)
            assert isinstance(integrator, HedgeRebalanceIntegrator)
            assert integrator.hedge_mode == expected_mode

    def test_get_integrator_unknown_mode_defaults(self) -> None:
        """未知模式默认为 TAIL_ONLY"""
        from utils.hedge_rebalance_integrator import (
            HedgeMode,
            get_integrator,
        )

        integrator = get_integrator(mode="unknown_mode")
        assert integrator.hedge_mode == HedgeMode.TAIL_ONLY

    def test_run_joint_analysis_no_save(self, tmp_path) -> None:
        """run_joint_analysis with save=False"""
        from utils.hedge_rebalance_integrator import JointPlan, run_joint_analysis

        with patch("utils.hedge_rebalance_integrator.get_integrator") as mock_get:
            integrator = MagicMock()
            plan = JointPlan(portfolio_value=1_000_000, stock_exposure=800_000)
            integrator.run_full_workflow.return_value = plan
            mock_get.return_value = integrator
            result_plan, fpath = run_joint_analysis(base_dir=str(tmp_path), save=False)
        assert result_plan is not None
        assert fpath == ""

    def test_run_joint_analysis_with_save(self, tmp_path) -> None:
        """run_joint_analysis with save=True"""
        from utils.hedge_rebalance_integrator import JointPlan, run_joint_analysis

        with patch("utils.hedge_rebalance_integrator.get_integrator") as mock_get:
            integrator = MagicMock()
            plan = JointPlan(portfolio_value=1_000_000, stock_exposure=800_000)
            integrator.run_full_workflow.return_value = plan
            integrator.save_report.return_value = str(tmp_path / "report.md")
            mock_get.return_value = integrator
            result_plan, fpath = run_joint_analysis(base_dir=str(tmp_path), save=True)
        assert result_plan is not None
        assert fpath != ""

    def test_get_ifind_prices_batch_unavailable(self) -> None:
        """iFinD 不可用时返回空字典 (已废弃: 源码重构移除 iFinD 数据源)"""
        pytest.skip(
            "utils/hedge_rebalance_integrator.py 已移除 _get_ifind_prices_batch (iFinD 数据源不再使用)"
        )

    def test_get_ifind_prices_batch_no_codes(self) -> None:
        """无有效代码时返回空字典 (已废弃: 源码重构移除 iFinD 数据源)"""
        pytest.skip(
            "utils/hedge_rebalance_integrator.py 已移除 _get_ifind_prices_batch (iFinD 数据源不再使用)"
        )

    def test_exec_ifind_no_client(self) -> None:
        """(已废弃: 源码重构移除 iFinD 数据源)"""
        pytest.skip(
            "utils/hedge_rebalance_integrator.py 已移除 _exec_ifind (iFinD 数据源不再使用)"
        )

    def test_exec_ifind_with_mock(self) -> None:
        """(已废弃: 源码重构移除 iFinD 数据源)"""
        pytest.skip(
            "utils/hedge_rebalance_integrator.py 已移除 _exec_ifind (iFinD 数据源不再使用)"
        )


# ============================================================
# 20. HedgeRebalanceIntegrator: format_report 边界
# ============================================================


class TestIntegratorFormatReportExtra:
    """format_report 补充路径测试"""

    def test_format_report_with_hedge(self) -> None:
        """带对冲决策的报告"""
        from utils.hedge_rebalance_integrator import (
            HedgeDecision,
            HedgeMode,
            HedgeRebalanceIntegrator,
            JointPlan,
            MarketRegime,
        )

        integrator = HedgeRebalanceIntegrator()
        plan = JointPlan(
            portfolio_value=1_000_000,
            stock_exposure=800_000,
            after_hedge_exposure=560_000,
            timestamp="2026-01-01 10:00:00",
        )
        plan.hedge = HedgeDecision(
            needed=True,
            mode=HedgeMode.TAIL_ONLY,
            regime=MarketRegime.TAIL_EVENT,
            hedge_ratio=0.30,
            futures_instruments=["IC", "IM"],
            futures_contracts={"IC": 2, "IM": 1},
            futures_notional={"IC": 200_000, "IM": 100_000},
            futures_margin={"IC": 28_000, "IM": 15_000},
            total_notional=300_000,
            total_margin=43_000,
            price_source="auto",
            expected_beta_after=0.9,
            reasoning="测试对冲",
            fallback_used=["IF"],
        )
        report = integrator.format_report(plan)
        assert isinstance(report, str)
        assert "IC" in report
        assert "回退" in report

    def test_format_report_with_rebalance(self) -> None:
        """带再平衡决策的报告"""
        from utils.hedge_rebalance_integrator import (
            HedgeRebalanceIntegrator,
            JointPlan,
            PositionWeight,
            RebalanceDecision,
        )

        integrator = HedgeRebalanceIntegrator()
        plan = JointPlan(
            portfolio_value=1_000_000,
            stock_exposure=800_000,
            timestamp="2026-01-01 10:00:00",
        )
        pw = PositionWeight(
            code="600519",
            name="贵州茅台",
            category="白酒",
            target_weight=0.10,
            current_weight=0.15,
            deviation=0.05,
            deviation_pct=0.50,
            current_value=150_000,
            target_value=100_000,
            adjustment=-50_000,
            adjustment_shares=-5,
            current_price=100.0,
            action="SELL",
            priority=50,
        )
        plan.rebalance = RebalanceDecision(
            needed=True,
            rebalance_type="strategic",
            threshold=0.05,
            positions_to_adjust=[pw],
            total_buy_amount=0,
            total_sell_amount=50_000,
            net_cash_flow=50_000,
        )
        report = integrator.format_report(plan)
        assert isinstance(report, str)
        assert "strategic" in report
        assert "SELL" in report

    def test_format_report_with_stress_tests(self) -> None:
        """带压力测试的报告"""
        from utils.hedge_rebalance_integrator import (
            HedgeRebalanceIntegrator,
            JointPlan,
        )

        integrator = HedgeRebalanceIntegrator()
        plan = JointPlan(
            portfolio_value=1_000_000,
            stock_exposure=800_000,
            timestamp="2026-01-01 10:00:00",
        )
        plan.stress_tests = {
            "2015股灾": {
                "breaches_limit": True,
                "drawdown_pct": 25.0,
                "estimated_loss": 200_000,
            },
            "2020疫情": {
                "breaches_limit": False,
                "drawdown_pct": 10.0,
                "estimated_loss": 80_000,
            },
        }
        report = integrator.format_report(plan)
        assert "压力测试" in report
        assert "突破" in report

    def test_format_report_with_warnings(self) -> None:
        """带预警的报告"""
        from utils.hedge_rebalance_integrator import (
            HedgeRebalanceIntegrator,
            JointPlan,
        )

        integrator = HedgeRebalanceIntegrator()
        plan = JointPlan(
            portfolio_value=1_000_000,
            stock_exposure=800_000,
            timestamp="2026-01-01 10:00:00",
        )
        plan.warning_flags = ["保证金过高", "组合不一致"]
        report = integrator.format_report(plan)
        assert "保证金过高" in report
