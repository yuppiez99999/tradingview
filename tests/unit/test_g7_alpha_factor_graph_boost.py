"""G7 覆盖率冲刺 — utils/alpha_factor/graph.py 单元测试.

目标: 覆盖率从 7.34% → ≥70%

测试范围:
    1. _momentum: 正常/边界 (len<=window, closes[-window]<=0)
    2. _neighbor_map: graph=None / edge.strength<=0 过滤 / 正常
    3. _weighted_avg: den=0 / w<=0 跳过 / v=None 跳过 / 正常加权
    4. compute_lead_lag_factors: 5 个因子 + min_neighbors 过滤 + 行业中性化分支
    5. orthogonalize_chain_factors: 4 映射 / 空 values 跳过 / 无 anchor 透传

运行:
    python -m pytest tests/unit/test_g7_alpha_factor_graph_boost.py -v
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from unittest.mock import patch

import pytest

# ============================================================
# PROJECT_ROOT sys.path 注入 (使测试文件可独立运行)
# ============================================================
PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils.alpha_factor.base import FactorValue  # noqa: E402
from utils.alpha_factor.graph import (  # noqa: E402
    _momentum,
    _neighbor_map,
    _weighted_avg,
    compute_lead_lag_factors,
    orthogonalize_chain_factors,
)

# ============================================================
# 测试用图结构 (模拟 SupplyChainGraph.adjacency)
# ============================================================


@dataclass
class _FakeEdge:
    target: str
    strength: float


class _FakeGraph:
    """最小图 mock: 仅需 adjacency: dict[str, list[_FakeEdge]]。"""

    def __init__(self, adjacency: dict[str, list[_FakeEdge]] | None = None) -> None:
        self.adjacency = adjacency or {}


def _make_price_data() -> dict[str, dict[str, list[float]]]:
    """构造 3 只标的的价格数据, 每只 80 天 (足够 60 日动量)。"""
    return {
        "A": {"closes": [100.0 * (1.001**i) for i in range(80)]},
        "B": {"closes": [50.0 * (1.002**i) for i in range(80)]},
        "C": {"closes": [80.0 * (0.999**i) for i in range(80)]},
    }


# ============================================================
# 1. _momentum
# ============================================================


class TestMomentum:
    def test_normal(self) -> None:
        # window=2 → closes[-1]/closes[-2]-1 = 121.0/110.0-1 = 0.1
        closes = [100.0, 110.0, 121.0]
        assert _momentum(closes, 2) == pytest.approx(0.1)

    def test_len_equals_window_returns_none(self) -> None:
        # len > window 才计算; len == window 不满足
        assert _momentum([10.0, 11.0], 2) is None

    def test_len_less_than_window_returns_none(self) -> None:
        assert _momentum([10.0], 5) is None

    def test_zero_base_returns_none(self) -> None:
        # closes[-window] == 0 → None
        closes = [10.0, 0.0, 11.0]
        assert _momentum(closes, 2) is None

    def test_negative_base_returns_none(self) -> None:
        # closes[-window] < 0 (异常数据) → None (条件 > 0)
        closes = [10.0, -5.0, 11.0]
        assert _momentum(closes, 2) is None

    def test_long_series(self) -> None:
        closes = [100.0 * (1.01**i) for i in range(80)]
        m = _momentum(closes, 20)
        assert m is not None
        assert m > 0


# ============================================================
# 2. _neighbor_map
# ============================================================


class TestNeighborMap:
    def test_graph_none_returns_empty(self) -> None:
        assert _neighbor_map(None, "A") == {}

    def test_no_adjacency_returns_empty(self) -> None:
        g = _FakeGraph({})
        assert _neighbor_map(g, "A") == {}

    def test_filters_zero_strength(self) -> None:
        g = _FakeGraph({"A": [_FakeEdge("B", 0.0), _FakeEdge("C", 0.5)]})
        assert _neighbor_map(g, "A") == {"C": 0.5}

    def test_filters_negative_strength(self) -> None:
        g = _FakeGraph({"A": [_FakeEdge("B", -0.1), _FakeEdge("C", 0.3)]})
        assert _neighbor_map(g, "A") == {"C": 0.3}

    def test_filters_none_strength(self) -> None:
        g = _FakeGraph({"A": [_FakeEdge("B", 0.4), _FakeEdge("D", 0.0)]})
        neigh = _neighbor_map(g, "A")
        assert "D" not in neigh
        assert neigh["B"] == 0.4

    def test_normal_multiple_neighbors(self) -> None:
        g = _FakeGraph({"A": [_FakeEdge("B", 0.3), _FakeEdge("C", 0.7)]})
        assert _neighbor_map(g, "A") == {"B": 0.3, "C": 0.7}


# ============================================================
# 3. _weighted_avg
# ============================================================


class TestWeightedAvg:
    def test_normal(self) -> None:
        values = {"B": 2.0, "C": 4.0}
        weights = {"B": 0.5, "C": 0.5}
        assert _weighted_avg(values, weights) == pytest.approx(3.0)

    def test_den_zero_returns_none(self) -> None:
        # 所有权重 <= 0
        values = {"B": 2.0}
        weights = {"B": 0.0}
        assert _weighted_avg(values, weights) is None

    def test_skip_zero_weight(self) -> None:
        values = {"B": 2.0, "C": 4.0}
        weights = {"B": 0.0, "C": 1.0}
        assert _weighted_avg(values, weights) == pytest.approx(4.0)

    def test_skip_none_value(self) -> None:
        values = {"B": None, "C": 4.0}  # type: ignore[dict-item]
        weights = {"B": 1.0, "C": 1.0}
        assert _weighted_avg(values, weights) == pytest.approx(4.0)

    def test_missing_weight_treated_as_zero(self) -> None:
        values = {"B": 2.0, "C": 4.0}
        weights = {"B": 1.0}  # C 缺权重
        assert _weighted_avg(values, weights) == pytest.approx(2.0)

    def test_empty_values_returns_none(self) -> None:
        assert _weighted_avg({}, {}) is None


# ============================================================
# 4. compute_lead_lag_factors
# ============================================================


class TestComputeLeadLagFactors:
    def test_graph_none_returns_empty_factors(self) -> None:
        # graph=None → 所有邻居映射空 → 5 个因子 values 均空 (CONCENTRATION 也空)
        price_data = _make_price_data()
        factors = compute_lead_lag_factors(price_data, graph=None)
        assert set(factors.keys()) == {
            "CHAIN_MOM_20D",
            "CHAIN_MOM_60D",
            "CHAIN_REVERSAL_5D",
            "CHAIN_NEIGHBOR_DIFF",
            "CHAIN_CONCENTRATION",
        }
        for fv in factors.values():
            assert fv.values == {}
            assert fv.category == "LeadLag"

    def test_normal_five_factors(self) -> None:
        price_data = _make_price_data()
        # A → B (strength 0.5), A → C (strength 0.5)
        g = _FakeGraph({"A": [_FakeEdge("B", 0.5), _FakeEdge("C", 0.5)]})
        factors = compute_lead_lag_factors(price_data, graph=g)
        # A 有邻居 B/C, 邻居有动量 → CHAIN_MOM_20D 应包含 A
        assert "A" in factors["CHAIN_MOM_20D"].values
        assert "A" in factors["CHAIN_MOM_60D"].values
        assert "A" in factors["CHAIN_REVERSAL_5D"].values
        assert "A" in factors["CHAIN_NEIGHBOR_DIFF"].values
        assert "A" in factors["CHAIN_CONCENTRATION"].values

    def test_min_neighbors_filter(self) -> None:
        price_data = _make_price_data()
        # A 仅 1 个邻居, min_neighbors=2 → A 被过滤
        g = _FakeGraph({"A": [_FakeEdge("B", 0.5)]})
        factors = compute_lead_lag_factors(price_data, graph=g, min_neighbors=2)
        # CHAIN_MOM_20D 等需 ≥2 邻居, A 仅 1 → 不出现
        assert "A" not in factors["CHAIN_MOM_20D"].values
        # CONCENTRATION 不受 min_neighbors 限制 (仅 len(neigh) < 1)
        assert "A" in factors["CHAIN_CONCENTRATION"].values

    def test_concentration_herfindahl(self) -> None:
        price_data = _make_price_data()
        # 单一强邻居 → 赫芬达尔 = 1.0
        g = _FakeGraph({"A": [_FakeEdge("B", 1.0)]})
        factors = compute_lead_lag_factors(price_data, graph=g)
        assert factors["CHAIN_CONCENTRATION"].values["A"] == pytest.approx(1.0)

    def test_concentration_balanced(self) -> None:
        price_data = _make_price_data()
        # 两个等权邻居 → 赫芬达尔 = 0.5
        g = _FakeGraph({"A": [_FakeEdge("B", 0.5), _FakeEdge("C", 0.5)]})
        factors = compute_lead_lag_factors(price_data, graph=g)
        assert factors["CHAIN_CONCENTRATION"].values["A"] == pytest.approx(0.5)

    def test_neighbor_diff_uses_own_momentum(self) -> None:
        price_data = _make_price_data()
        g = _FakeGraph({"A": [_FakeEdge("B", 1.0)]})
        factors = compute_lead_lag_factors(price_data, graph=g)
        # A 必须自身有 mom_20d 才能算 NEIGHBOR_DIFF
        assert "A" in factors["CHAIN_NEIGHBOR_DIFF"].values

    def test_neighbor_diff_skips_when_own_momentum_missing(self) -> None:
        # A 的 closes 不足 20 → 无 mom_20d → NEIGHBOR_DIFF 跳过 A
        price_data = {
            "A": {"closes": [100.0, 101.0, 102.0]},
            "B": {"closes": [50.0 * (1.002**i) for i in range(80)]},
        }
        g = _FakeGraph({"A": [_FakeEdge("B", 1.0)]})
        factors = compute_lead_lag_factors(price_data, graph=g)
        assert "A" not in factors["CHAIN_NEIGHBOR_DIFF"].values

    def test_industry_neutralize_applied_to_chain_mom_20d(self) -> None:
        price_data = _make_price_data()
        g = _FakeGraph({"A": [_FakeEdge("B", 0.5), _FakeEdge("C", 0.5)]})
        industries = {"A": "Tech", "B": "Tech", "C": "Finance"}
        with patch("utils.alpha_factor.base.neutralize_by_industry") as mock_neut:
            mock_neut.side_effect = lambda v, i: {k: 0.0 for k in v}
            factors = compute_lead_lag_factors(
                price_data, graph=g, industries=industries
            )
            assert mock_neut.called
            # 中性化后 CHAIN_MOM_20D 全为 0
            assert all(v == 0.0 for v in factors["CHAIN_MOM_20D"].values.values())

    def test_industry_none_skips_neutralize(self) -> None:
        price_data = _make_price_data()
        g = _FakeGraph({"A": [_FakeEdge("B", 0.5)]})
        with patch("utils.alpha_factor.base.neutralize_by_industry") as mock_neut:
            factors = compute_lead_lag_factors(price_data, graph=g, industries=None)
            assert not mock_neut.called
            # 因子值非全 0 (邻居有动量)
            assert factors["CHAIN_MOM_20D"].values


# ============================================================
# 5. orthogonalize_chain_factors
# ============================================================


class TestOrthogonalizeChainFactors:
    def test_empty_values_skipped(self) -> None:
        chain = {
            "CHAIN_MOM_20D": FactorValue(
                name="CHAIN_MOM_20D", category="LeadLag", values={}
            )
        }
        mom = {
            "MOM_20D": FactorValue(
                name="MOM_20D", category="Momentum", values={"A": 1.0}
            )
        }
        result = orthogonalize_chain_factors(chain, mom)
        # 空 values → 跳过, 不出现在 result
        assert "CHAIN_MOM_20D" not in result

    def test_no_anchor_passthrough(self) -> None:
        # CHAIN_CONCENTRATION 不在 mapping → 无 anchor → 透传
        chain = {
            "CHAIN_CONCENTRATION": FactorValue(
                name="CHAIN_CONCENTRATION",
                category="LeadLag",
                values={"A": 0.5, "B": 0.3},
            )
        }
        mom = {
            "MOM_20D": FactorValue(
                name="MOM_20D", category="Momentum", values={"A": 1.0}
            )
        }
        result = orthogonalize_chain_factors(chain, mom)
        assert result["CHAIN_CONCENTRATION"].values == {"A": 0.5, "B": 0.3}

    def test_anchor_not_in_mom_passthrough(self) -> None:
        # anchor_name 在 mapping 但 mom_factors 无该键 → 透传
        chain = {
            "CHAIN_MOM_20D": FactorValue(
                name="CHAIN_MOM_20D", category="LeadLag", values={"A": 1.0, "B": 2.0}
            )
        }
        mom = {}  # type: ignore[var-annotated]
        result = orthogonalize_chain_factors(chain, mom)
        assert result["CHAIN_MOM_20D"].values == {"A": 1.0, "B": 2.0}

    def test_anchor_empty_values_passthrough(self) -> None:
        chain = {
            "CHAIN_MOM_20D": FactorValue(
                name="CHAIN_MOM_20D", category="LeadLag", values={"A": 1.0, "B": 2.0}
            )
        }
        mom = {"MOM_20D": FactorValue(name="MOM_20D", category="Momentum", values={})}
        result = orthogonalize_chain_factors(chain, mom)
        assert result["CHAIN_MOM_20D"].values == {"A": 1.0, "B": 2.0}

    def test_residualize_applied(self) -> None:
        chain = {
            "CHAIN_MOM_20D": FactorValue(
                name="CHAIN_MOM_20D",
                category="LeadLag",
                values={"A": 1.0, "B": 2.0, "C": 3.0},
            )
        }
        mom = {
            "MOM_20D": FactorValue(
                name="MOM_20D",
                category="Momentum",
                values={"A": 2.0, "B": 4.0, "C": 6.0},
            )
        }
        with patch("utils.alpha_factor.graph.residualize") as mock_res:
            mock_res.side_effect = lambda v, c: {k: 0.0 for k in v}
            result = orthogonalize_chain_factors(chain, mom)
            assert mock_res.called
            assert all(v == 0.0 for v in result["CHAIN_MOM_20D"].values.values())

    def test_all_four_mappings(self) -> None:
        # 验证 4 个映射键均能触发正交化
        chain = {
            "CHAIN_MOM_20D": FactorValue(
                name="CHAIN_MOM_20D",
                category="LeadLag",
                values={"A": 1.0, "B": 2.0, "C": 3.0},
            ),
            "CHAIN_MOM_60D": FactorValue(
                name="CHAIN_MOM_60D",
                category="LeadLag",
                values={"A": 1.0, "B": 2.0, "C": 3.0},
            ),
            "CHAIN_REVERSAL_5D": FactorValue(
                name="CHAIN_REVERSAL_5D",
                category="LeadLag",
                values={"A": 1.0, "B": 2.0, "C": 3.0},
            ),
            "CHAIN_NEIGHBOR_DIFF": FactorValue(
                name="CHAIN_NEIGHBOR_DIFF",
                category="LeadLag",
                values={"A": 1.0, "B": 2.0, "C": 3.0},
            ),
        }
        mom = {
            "MOM_20D": FactorValue(
                name="MOM_20D",
                category="Momentum",
                values={"A": 2.0, "B": 4.0, "C": 6.0},
            ),
            "MOM_60D": FactorValue(
                name="MOM_60D",
                category="Momentum",
                values={"A": 2.0, "B": 4.0, "C": 6.0},
            ),
            "MOM_REVERSAL_5D": FactorValue(
                name="MOM_REVERSAL_5D",
                category="Momentum",
                values={"A": 2.0, "B": 4.0, "C": 6.0},
            ),
        }
        with patch("utils.alpha_factor.graph.residualize") as mock_res:
            mock_res.side_effect = lambda v, c: {k: -1.0 for k in v}
            result = orthogonalize_chain_factors(chain, mom)
            # 4 个因子均被正交化
            assert mock_res.call_count == 4
            for name in chain:
                assert all(v == -1.0 for v in result[name].values.values())
