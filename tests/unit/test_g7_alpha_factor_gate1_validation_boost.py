"""G7 覆盖率冲刺 — utils/alpha_factor/gate1_validation.py 单元测试.

目标: 覆盖率从 18.24% → ≥70%

测试范围:
    1. to_tx_code: 6/5/9 前缀 → sh, 其他 → sz, 含 . 后缀
    2. fetch_tx_kline: requests 成功/失败/无数据
    3. fetch_prices: 缓存命中/缺失/回写
    4. calc_icir: <2 / std=0 / 正常
    5. _compute_momentum_factors: 3 个锚因子
    6. load_universe / load_expanded_universe: mock 外部依赖
    7. run_gate1_validation: mock 全流程 (PASS/FAIL/error)
    8. main: CLI 入口

运行:
    python -m pytest tests/unit/test_g7_alpha_factor_gate1_validation_boost.py -v
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import utils.alpha_factor.gate1_validation as gv  # noqa: E402

# ============================================================
# 1. to_tx_code
# ============================================================


class TestToTxCode:
    def test_sh_prefix_6(self) -> None:
        assert gv.to_tx_code("600519") == "sh600519"

    def test_sh_prefix_5(self) -> None:
        assert gv.to_tx_code("500001") == "sh500001"

    def test_sh_prefix_9(self) -> None:
        assert gv.to_tx_code("900001") == "sh900001"

    def test_sz_prefix(self) -> None:
        assert gv.to_tx_code("000001") == "sz000001"

    def test_sz_prefix_3(self) -> None:
        assert gv.to_tx_code("300308") == "sz300308"

    def test_with_dot_suffix(self) -> None:
        assert gv.to_tx_code("600519.SH") == "sh600519"

    def test_strips_whitespace(self) -> None:
        assert gv.to_tx_code("  600519  ") == "sh600519"


# ============================================================
# 2. fetch_tx_kline
# ============================================================


class TestFetchTxKline:
    def test_success(self) -> None:
        # mock requests.get 返回有效腾讯 K 线格式
        mock_response = MagicMock()
        mock_response.text = 'kline_dayqfq={"data":{"600519":{"qfqday":[["20260101","10","11","12","9","1000"]]}}}'
        with patch(
            "utils.alpha_factor.gate1_validation.requests.get",
            return_value=mock_response,
        ):
            kline = gv.fetch_tx_kline("600519", days=10)
            assert kline is not None
            assert kline[0][0] == "20260101"

    def test_day_fallback(self) -> None:
        # qfqday 缺失 → 回退 day
        mock_response = MagicMock()
        mock_response.text = 'kline_dayqfq={"data":{"600519":{"day":[["20260101","10","11","12","9","1000"]]}}}'
        with patch(
            "utils.alpha_factor.gate1_validation.requests.get",
            return_value=mock_response,
        ):
            kline = gv.fetch_tx_kline("600519", days=10)
            assert kline is not None

    def test_no_data_returns_none(self) -> None:
        mock_response = MagicMock()
        mock_response.text = 'kline_dayqfq={"data":{}}'
        with patch(
            "utils.alpha_factor.gate1_validation.requests.get",
            return_value=mock_response,
        ):
            assert gv.fetch_tx_kline("600519", days=10) is None

    def test_exception_returns_none(self) -> None:
        with patch(
            "utils.alpha_factor.gate1_validation.requests.get",
            side_effect=OSError("network"),
        ):
            assert gv.fetch_tx_kline("600519", days=10) is None

    def test_invalid_json_returns_none(self) -> None:
        mock_response = MagicMock()
        mock_response.text = "invalid json"
        with patch(
            "utils.alpha_factor.gate1_validation.requests.get",
            return_value=mock_response,
        ):
            assert gv.fetch_tx_kline("600519", days=10) is None


# ============================================================
# 3. fetch_prices
# ============================================================


class TestFetchPrices:
    def test_fetch_and_cache(self, tmp_path) -> None:
        # mock fetch_tx_kline 返回 K 线, mock 缓存文件路径到 tmp_path
        kline = [["20260101", "10", "11", "12", "9", "1000"]]
        with (
            patch.object(gv, "fetch_tx_kline", return_value=kline),
            patch.object(gv, "_PRICE_CACHE_FILE", tmp_path / "cache.json"),
            patch("utils.alpha_factor.gate1_validation.time.sleep"),
        ):
            prices = gv.fetch_prices(["600519"], days=10, use_cache=False)
            assert "600519" in prices
            assert prices["600519"]["closes"] == [11.0]
            assert prices["600519"]["opens"] == [10.0]
            assert prices["600519"]["highs"] == [12.0]
            assert prices["600519"]["lows"] == [9.0]
            assert prices["600519"]["volumes"] == [1000.0]

    def test_skip_no_kline(self, tmp_path) -> None:
        # fetch_tx_kline 返回 None → 跳过该标的
        with (
            patch.object(gv, "fetch_tx_kline", return_value=None),
            patch.object(gv, "_PRICE_CACHE_FILE", tmp_path / "cache.json"),
        ):
            prices = gv.fetch_prices(["600519"], days=10, use_cache=False)
            assert "600519" not in prices

    def test_use_cache_hit(self, tmp_path) -> None:
        # 预置缓存文件 → 直接命中, 不调用 fetch_tx_kline
        import json

        cache_file = tmp_path / "cache.json"
        cache_content = {
            "600519": {
                "closes": [100.0],
                "volumes": [1.0],
                "highs": [101.0],
                "lows": [99.0],
                "opens": [100.0],
                "dates": ["20260101"],
            }
        }
        cache_file.write_text(json.dumps(cache_content), encoding="utf-8")

        with (
            patch.object(gv, "fetch_tx_kline") as mock_fetch,
            patch.object(gv, "_PRICE_CACHE_FILE", cache_file),
        ):
            # 缓存刚写入, st_mtime ≈ now, time.time() - st_mtime < TTL → 命中
            prices = gv.fetch_prices(["600519"], days=10, use_cache=True)
            assert "600519" in prices
            assert prices["600519"]["closes"] == [100.0]
            mock_fetch.assert_not_called()


# ============================================================
# 4. calc_icir
# ============================================================


class TestCalcIcir:
    def test_less_than_two_returns_zero(self) -> None:
        assert gv.calc_icir([0.5]) == 0.0

    def test_empty_returns_zero(self) -> None:
        assert gv.calc_icir([]) == 0.0

    def test_zero_std_returns_zero(self) -> None:
        # 所有值相同 → std=0 → 0.0
        assert gv.calc_icir([0.5, 0.5, 0.5]) == 0.0

    def test_normal(self) -> None:
        ic_series = [0.1, 0.2, 0.3, 0.4]
        result = gv.calc_icir(ic_series)
        assert result != 0.0
        # mean=0.25, std≈0.1118 → ICIR≈2.236
        assert result == pytest.approx(0.25 / 0.111803, rel=1e-3)


# ============================================================
# 5. _compute_momentum_factors
# ============================================================


class TestComputeMomentumFactors:
    def test_three_anchor_factors(self) -> None:
        price_data = {
            "A": {"closes": [100.0 * (1.001**i) for i in range(80)]},
            "B": {"closes": [50.0 * (1.002**i) for i in range(80)]},
        }
        mom = gv._compute_momentum_factors(price_data)
        assert "MOM_20D" in mom
        assert "MOM_60D" in mom
        assert "MOM_REVERSAL_5D" in mom
        assert "A" in mom["MOM_20D"].values
        assert "A" in mom["MOM_60D"].values
        assert "A" in mom["MOM_REVERSAL_5D"].values

    def test_short_series_skipped(self) -> None:
        # 不足 21 天 → MOM_20D 不含该标的
        price_data = {"A": {"closes": [100.0, 101.0, 102.0]}}
        mom = gv._compute_momentum_factors(price_data)
        assert "A" not in mom["MOM_20D"].values
        assert "A" not in mom["MOM_60D"].values


# ============================================================
# 6. load_universe / load_expanded_universe
# ============================================================


class TestLoadUniverse:
    def test_load_universe(self) -> None:
        # mock load_positions_symbols + SupplyChainBuilder
        mock_builder = MagicMock()
        mock_builder.graph.all_nodes = ["600519", "000001"]
        mock_builder.build.return_value = {"node_count": 2, "edge_count": 1}
        with (
            patch(
                "utils.supply_chain_builder.load_positions_symbols",
                return_value=["600519"],
            ),
            patch(
                "utils.alpha_factor.gate1_validation.SupplyChainBuilder",
                return_value=mock_builder,
            ),
        ):
            universe = gv.load_universe()
            assert "600519" in universe
            assert "000001" in universe

    def test_load_expanded_universe(self) -> None:
        # mock graph_data_source + load_positions_symbols
        mock_ds = MagicMock()
        mock_ds.fetch_board_stocks.return_value = [
            {"code": "600519", "industry": "半导体"},
            {"code": "000001", "industry": None},
        ]
        with (
            patch(
                "utils.graph_data_source.get_graph_data_source", return_value=mock_ds
            ),
            patch(
                "utils.supply_chain_builder.load_positions_symbols",
                return_value=["300308.SZ"],
            ),
            patch.object(gv, "time", MagicMock()),
        ):
            symbols, industries = gv.load_expanded_universe(per_board=2)
            assert "600519" in symbols
            assert "000001" in symbols
            assert industries["600519"] == "半导体"
            # 持仓兜底 (去 .SZ 后缀)
            assert "300308" in symbols


# ============================================================
# 7. run_gate1_validation
# ============================================================


class TestRunGate1Validation:
    def test_insufficient_data_returns_error(self) -> None:
        # mock fetch_prices 返回 < 10 只 → error
        with (
            patch.object(gv, "load_expanded_universe", return_value=(["600519"], {})),
            patch.object(
                gv, "fetch_prices", return_value={"600519": {"closes": [100.0]}}
            ),
        ):
            result = gv.run_gate1_validation(expanded=True)
            assert "error" in result
            assert "数据不足" in result["error"]

    def test_baseline_mode(self) -> None:
        # mock load_universe + fetch_prices 返回 < 10 只 → error (覆盖 baseline 分支)
        with (
            patch.object(gv, "load_universe", return_value=["600519"]),
            patch.object(
                gv, "fetch_prices", return_value={"600519": {"closes": [100.0]}}
            ),
        ):
            result = gv.run_gate1_validation(expanded=False)
            assert "error" in result


# ============================================================
# 8. main (CLI 入口)
# ============================================================


class TestMain:
    _FULL_RESULT = {
        "universe_type": "expanded",
        "universe_size": 100,
        "price_coverage": 90,
        "industry_coverage": 80,
        "graph": {"node_count": 100, "edge_count": 50},
        "factors": {
            "CHAIN_MOM_20D": {
                "ic_mean": 0.02,
                "icir": 0.5,
                "eff_ic": 0.02,
                "eff_icir": 0.5,
                "direction": 1,
                "long_short_sharpe": 1.5,
                "coverage": 80,
                "n_windows": 10,
            },
        },
        "gate1_verdict": "PASS",
        "gate1_passers": ["CHAIN_MOM_20D"],
        "gate1_threshold": "同一因子同时满足: effIC≥0.01 且 effICIR>0.3 且 多空夏普>1.0",
    }

    def test_main_pass(self) -> None:
        result = {**self._FULL_RESULT, "gate1_verdict": "PASS"}
        with (
            patch.object(gv, "run_gate1_validation", return_value=result),
            patch("sys.argv", ["gate1_validation"]),
        ):
            rc = gv.main()
            assert rc == 0

    def test_main_fail(self) -> None:
        result = {**self._FULL_RESULT, "gate1_verdict": "FAIL", "gate1_passers": []}
        with (
            patch.object(gv, "run_gate1_validation", return_value=result),
            patch("sys.argv", ["gate1_validation"]),
        ):
            rc = gv.main()
            assert rc == 1

    def test_main_error(self) -> None:
        with (
            patch.object(
                gv, "run_gate1_validation", return_value={"error": "数据不足"}
            ),
            patch("sys.argv", ["gate1_validation"]),
        ):
            rc = gv.main()
            assert rc == 1

    def test_main_json_output(self) -> None:
        # --json 分支 (不进入打印分支, 直接返回 0)
        with (
            patch.object(
                gv, "run_gate1_validation", return_value={"gate1_verdict": "PASS"}
            ),
            patch("sys.argv", ["gate1_validation", "--json"]),
            patch.object(gv.logger, "info") as mock_info,
        ):
            rc = gv.main()
            assert rc == 0
            assert mock_info.called


# ============================================================
# 9. calc_ic_series / run_long_short_ic (边界 + 主体)
# ============================================================


def _make_chain_graph(n: int = 12):
    """构造链式 graph: S0→S1→...→S(n-1), 每边 strength=0.5。"""
    from dataclasses import dataclass

    @dataclass
    class _Edge:
        target: str
        strength: float

    class _Graph:
        def __init__(self) -> None:
            self.adjacency: dict[str, list[_Edge]] = {}
            self.all_nodes = [f"S{i}" for i in range(n)]

    g = _Graph()
    for i in range(n - 1):
        g.adjacency[f"S{i}"] = [_Edge(f"S{i + 1}", 0.5)]
    return g


def _make_chain_price_data(n: int = 12, days: int = 80, seed: int = 42):
    """构造 n 只标的的合成价格数据, 每只 days 天, 有差异化漂移。"""
    rng = __import__("numpy").random.default_rng(seed)
    price_data: dict[str, dict[str, list[float]]] = {}
    for i in range(n):
        base = float(rng.uniform(20.0, 200.0))
        drift = float(rng.normal(0.001, 0.0005))
        closes = [base]
        for _ in range(days - 1):
            closes.append(closes[-1] * (1 + rng.normal(drift, 0.015)))
        price_data[f"S{i}"] = {"closes": closes}
    return price_data


class TestCalcIcSeries:
    def test_insufficient_eligible_returns_empty(self) -> None:
        # eligible < 5 → 返回 []
        price_data = {"A": {"closes": [100.0] * 10}}
        graph = MagicMock()
        result = gv.calc_ic_series(price_data, graph, "CHAIN_MOM_20D")
        assert result == []

    def test_normal_returns_ic_series(self) -> None:
        # 12 只标的, 80 天, 链式 graph → calc_ic_series 走主体
        price_data = _make_chain_price_data(n=12, days=80)
        graph = _make_chain_graph(n=12)
        result = gv.calc_ic_series(
            price_data,
            graph,
            "CHAIN_MOM_20D",
            horizon=5,
            windows=4,
            window_len=30,
            min_neighbors=1,
        )
        # 应返回 list (可能含若干 IC 值)
        assert isinstance(result, list)

    def test_scipy_unavailable_returns_empty(self) -> None:
        # mock scipy.stats.spearmanr ImportError → 返回 []
        price_data = _make_chain_price_data(n=12, days=80)
        graph = _make_chain_graph(n=12)
        import builtins

        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "scipy.stats":
                raise ImportError("no scipy")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_fake_import):
            result = gv.calc_ic_series(price_data, graph, "CHAIN_MOM_20D")
            assert result == []


class TestRunLongShortIc:
    def test_insufficient_eligible_returns_zero(self) -> None:
        # eligible < 10 → 返回 0.0
        price_data = {"A": {"closes": [100.0] * 10}}
        graph = MagicMock()
        result = gv.run_long_short_ic(price_data, graph, "CHAIN_MOM_20D")
        assert result == 0.0

    def test_normal_returns_sharpe(self) -> None:
        # 12 只标的, 80 天, 链式 graph → run_long_short_ic 走主体
        price_data = _make_chain_price_data(n=12, days=80)
        graph = _make_chain_graph(n=12)
        result = gv.run_long_short_ic(
            price_data,
            graph,
            "CHAIN_MOM_20D",
            horizon=20,
            windows=3,
            min_neighbors=1,
        )
        # 应返回 float (可能 0.0 或非零夏普)
        assert isinstance(result, float)


# ============================================================
# 10. run_gate1_validation 成功路径
# ============================================================


class TestRunGate1ValidationSuccess:
    def test_full_pipeline_pass(self) -> None:
        # mock 外部依赖, 用合成数据走完 run_gate1_validation 主体
        price_data = _make_chain_price_data(n=12, days=80)
        graph = _make_chain_graph(n=12)

        # mock universe 构建
        # mock fetch_prices 返回合成数据
        # mock SupplyChainBuilder.build 返回 graph_info + graph
        mock_builder = MagicMock()
        mock_builder.graph = graph
        mock_builder.build.return_value = {"node_count": 12, "edge_count": 11}

        with (
            patch.object(
                gv, "load_expanded_universe", return_value=(list(price_data.keys()), {})
            ),
            patch.object(gv, "fetch_prices", return_value=price_data),
            patch.object(gv, "SupplyChainBuilder", return_value=mock_builder),
        ):
            result = gv.run_gate1_validation(days=80, min_neighbors=1, expanded=True)
            # 应返回完整结果 (非 error)
            assert "error" not in result
            assert "gate1_verdict" in result
            assert result["gate1_verdict"] in ("PASS", "FAIL")
            assert "factors" in result
            assert "universe_size" in result
