"""test_gat_factor_unit.py — 图注意力网络因子 (纯 numpy) 单元测试

覆盖要点:
    - GATFactor 构造 / _init_params (W/a 形状)
    - _attention (系数形状/非负/行和≈1/全孤立节点)
    - compute (因子形状/自动初始化)
    - _rank_loss (完全相关/反相关/NaN/样本不足)
    - train (少量 epoch 返回 losses)
    - build_adjacency (mock graph/无向对称/缺失节点跳过)
    - gat_factor_values (端到端)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from utils.alpha_factor.gat_factor import GATFactor, build_adjacency, gat_factor_values

# ============================================================
# GATFactor 构造与参数初始化
# ============================================================


class TestGATFactorInit:
    @pytest.mark.unit
    def test_defaults(self):
        gat = GATFactor()
        assert gat.n_hidden == 16
        assert gat.n_heads == 2
        assert gat.leaky_alpha == 0.2
        assert gat.W is None
        assert gat.a is None

    @pytest.mark.unit
    def test_init_params_shapes(self):
        gat = GATFactor(n_hidden=8, n_heads=3)
        gat._init_params(n_features=5)
        assert gat.W.shape == (3, 8, 5)
        assert gat.a.shape == (3, 16)  # 2 * n_hidden
        assert gat.n_features == 5

    @pytest.mark.unit
    def test_init_params_deterministic(self):
        """固定种子 → 相同参数"""
        g1 = GATFactor(n_hidden=4, n_heads=2)
        g1._init_params(3)
        g2 = GATFactor(n_hidden=4, n_heads=2)
        g2._init_params(3)
        np.testing.assert_array_equal(g1.W, g2.W)
        np.testing.assert_array_equal(g1.a, g2.a)


# ============================================================
# _attention
# ============================================================


class TestAttention:
    @pytest.mark.unit
    def test_attention_shape(self):
        gat = GATFactor(n_hidden=4, n_heads=2)
        n, d = 5, 3
        features = np.random.randn(n, d)
        adj = np.ones((n, n)) - np.eye(n)  # 全连接无自环
        gat._init_params(d)
        alpha = gat._attention(features, adj)
        assert alpha.shape == (n, n)

    @pytest.mark.unit
    def test_attention_non_negative(self):
        gat = GATFactor(n_hidden=4, n_heads=2)
        features = np.random.randn(5, 3)
        adj = np.ones((5, 5)) - np.eye(5)
        gat._init_params(3)
        alpha = gat._attention(features, adj)
        assert (alpha >= 0).all()

    @pytest.mark.unit
    def test_attention_row_sums_approx_one(self):
        """有邻居时行和≈1 (softmax 归一化)"""
        gat = GATFactor(n_hidden=4, n_heads=2)
        features = np.random.randn(5, 3)
        adj = np.ones((5, 5)) - np.eye(5)  # 每节点4个邻居
        gat._init_params(3)
        alpha = gat._attention(features, adj)
        row_sums = alpha.sum(axis=1)
        np.testing.assert_allclose(row_sums, 1.0, atol=1e-5)

    @pytest.mark.unit
    def test_isolated_node_zero_attention(self):
        """孤立节点 (无邻居) 行和=0"""
        gat = GATFactor(n_hidden=4, n_heads=2)
        features = np.random.randn(3, 3)
        adj = np.zeros((3, 3))
        adj[0, 1] = adj[1, 0] = 1.0  # 只有 0-1 连边, 节点2孤立
        gat._init_params(3)
        alpha = gat._attention(features, adj)
        assert alpha[2, :].sum() == pytest.approx(0, abs=1e-6)

    @pytest.mark.unit
    def test_no_edge_zero_attention(self):
        """无边 → 全零注意力"""
        gat = GATFactor(n_hidden=4, n_heads=2)
        features = np.random.randn(3, 3)
        adj = np.zeros((3, 3))
        gat._init_params(3)
        alpha = gat._attention(features, adj)
        assert (alpha == 0).all()


# ============================================================
# compute
# ============================================================


class TestCompute:
    @pytest.mark.unit
    def test_compute_shape(self):
        gat = GATFactor(n_hidden=4, n_heads=2)
        features = np.random.randn(5, 3)
        adj = np.ones((5, 5)) - np.eye(5)
        factor = gat.compute(features, adj)
        assert factor.shape == (5,)

    @pytest.mark.unit
    def test_compute_auto_init(self):
        """未初始化时自动初始化"""
        gat = GATFactor(n_hidden=4, n_heads=2)
        assert gat.W is None
        features = np.random.randn(5, 3)
        adj = np.ones((5, 5)) - np.eye(5)
        gat.compute(features, adj)
        assert gat.W is not None
        assert gat.n_features == 3

    @pytest.mark.unit
    def test_compute_no_edges_zero(self):
        """无边 → 因子全零"""
        gat = GATFactor(n_hidden=4, n_heads=2)
        features = np.random.randn(5, 3)
        adj = np.zeros((5, 5))
        factor = gat.compute(features, adj)
        np.testing.assert_allclose(factor, 0, atol=1e-6)


# ============================================================
# _rank_loss
# ============================================================


class TestRankLoss:
    @pytest.mark.unit
    def test_perfect_correlation(self):
        """因子与标签完全正相关 → loss ≈ -1"""
        gat = GATFactor()
        factor = np.array([1, 2, 3, 4, 5, 6], dtype=float)
        labels = np.array([10, 20, 30, 40, 50, 60], dtype=float)
        loss = gat._rank_loss(factor, labels)
        assert loss == pytest.approx(-1.0, abs=1e-6)

    @pytest.mark.unit
    def test_perfect_anti_correlation(self):
        """因子与标签完全反相关 → loss ≈ +1"""
        gat = GATFactor()
        factor = np.array([1, 2, 3, 4, 5, 6], dtype=float)
        labels = np.array([60, 50, 40, 30, 20, 10], dtype=float)
        loss = gat._rank_loss(factor, labels)
        assert loss == pytest.approx(1.0, abs=1e-6)

    @pytest.mark.unit
    def test_insufficient_samples(self):
        """有效样本 < 5 → loss=0"""
        gat = GATFactor()
        factor = np.array([1, 2, 3], dtype=float)
        labels = np.array([4, 5, 6], dtype=float)
        loss = gat._rank_loss(factor, labels)
        assert loss == 0.0

    @pytest.mark.unit
    def test_nan_labels(self):
        """NaN 标签被过滤"""
        gat = GATFactor()
        factor = np.array([1, 2, 3, 4, 5, 6], dtype=float)
        labels = np.array([10, 20, np.nan, 40, 50, 60], dtype=float)
        loss = gat._rank_loss(factor, labels)
        # 5 个有效样本, 完全正相关 → -1
        assert loss == pytest.approx(-1.0, abs=1e-6)


# ============================================================
# train
# ============================================================


class TestTrain:
    @pytest.mark.unit
    def test_returns_losses(self):
        gat = GATFactor(n_hidden=4, n_heads=2)
        features = np.random.randn(10, 3)
        adj = np.ones((10, 10)) - np.eye(10)
        labels = np.random.randn(10)
        losses = gat.train(features, adj, labels, epochs=3, lr=0.01)
        assert len(losses) == 3

    @pytest.mark.unit
    def test_loss_is_float(self):
        gat = GATFactor(n_hidden=4, n_heads=2)
        features = np.random.randn(10, 3)
        adj = np.ones((10, 10)) - np.eye(10)
        labels = np.random.randn(10)
        losses = gat.train(features, adj, labels, epochs=2, lr=0.01)
        for loss in losses:
            assert isinstance(loss, float)


# ============================================================
# build_adjacency
# ============================================================


class TestBuildAdjacency:
    @pytest.mark.unit
    def test_simple_graph(self):
        mock_graph = MagicMock()
        mock_edge = MagicMock()
        mock_edge.target = "B"
        mock_edge.strength = 0.8
        mock_graph.adjacency = {"A": [mock_edge]}

        adj, syms = build_adjacency(mock_graph, ["A", "B", "C"])
        assert syms == ["A", "B", "C"]
        assert adj.shape == (3, 3)
        assert adj[0, 1] == 0.8  # A→B
        assert adj[1, 0] == 0.8  # 无向对称

    @pytest.mark.unit
    def test_missing_symbol_skipped(self):
        mock_graph = MagicMock()
        mock_edge = MagicMock()
        mock_edge.target = "D"  # 不在 symbols 中
        mock_edge.strength = 0.5
        mock_graph.adjacency = {"A": [mock_edge]}

        adj, syms = build_adjacency(mock_graph, ["A", "B"])
        assert (adj == 0).all()

    @pytest.mark.unit
    def test_weight_key(self):
        mock_graph = MagicMock()
        mock_edge = MagicMock()
        mock_edge.target = "B"
        mock_edge.custom_weight = 0.3
        mock_edge.strength = 0.8
        mock_graph.adjacency = {"A": [mock_edge]}

        adj, _ = build_adjacency(mock_graph, ["A", "B"], weight_key="custom_weight")
        assert adj[0, 1] == 0.3

    @pytest.mark.unit
    def test_undirected_symmetric(self):
        mock_graph = MagicMock()
        e1 = MagicMock()
        e1.target = "B"
        e1.strength = 0.5
        e2 = MagicMock()
        e2.target = "C"
        e2.strength = 0.3
        mock_graph.adjacency = {"A": [e1, e2]}

        adj, _ = build_adjacency(mock_graph, ["A", "B", "C"])
        assert adj[0, 1] == adj[1, 0]
        assert adj[0, 2] == adj[2, 0]


# ============================================================
# gat_factor_values (端到端)
# ============================================================


class TestGatFactorValues:
    @pytest.mark.unit
    def test_returns_tuple(self):
        mock_graph = MagicMock()
        mock_edge = MagicMock()
        mock_edge.target = "B"
        mock_edge.strength = 0.5
        mock_graph.adjacency = {"A": [mock_edge]}

        symbols = ["A", "B", "C"]
        features = np.random.randn(3, 2)
        labels = np.random.randn(3)
        factor, gat, losses = gat_factor_values(
            mock_graph,
            symbols,
            features,
            labels,
            n_hidden=4,
            epochs=2,
            lr=0.01,
        )
        assert factor.shape == (3,)
        assert isinstance(gat, GATFactor)
        assert len(losses) == 2
