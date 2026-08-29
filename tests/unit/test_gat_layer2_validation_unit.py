"""test_gat_layer2_validation_unit.py — GAT Layer 2 验证单元测试

gat_layer2_validation 依赖 gat_factor_torch (torch DLL 加载失败),
纯函数部分无法单独导入。本文件在 torch 可用时运行, 否则全部跳过。
"""

from __future__ import annotations

import pytest

try:
    import torch  # noqa: F401

    _TORCH_OK = True
except (ImportError, OSError, ModuleNotFoundError, AttributeError):
    _TORCH_OK = False

pytestmark = pytest.mark.skipif(
    not _TORCH_OK, reason="torch 不可用 (gat_factor_torch 依赖)"
)

if _TORCH_OK:
    import numpy as np

    from utils.alpha_factor.gat_layer2_validation import (
        build_block_adjacency,
        calc_ic,
        calc_long_short_sharpe,
        compute_features_at_time,
        compute_static_factor,
    )


class TestComputeFeaturesAtTime:
    @pytest.mark.unit
    def test_basic(self):
        price_data = {"A": {"closes": [10.0] * 100}}
        features, ret = compute_features_at_time(price_data, ["A"], T=50, horizon=20)
        assert features.shape == (1, 2)
        assert ret.shape == (1,)


class TestComputeStaticFactor:
    @pytest.mark.unit
    def test_shape(self):
        features = np.random.randn(5, 2)
        adj = np.ones((5, 5)) - np.eye(5)
        static = compute_static_factor(features, adj)
        assert static.shape == (5,)


class TestBuildBlockAdjacency:
    @pytest.mark.unit
    def test_block_diagonal(self):
        adj = np.ones((3, 3)) - np.eye(3)
        block = build_block_adjacency(adj, n_blocks=2)
        assert block.shape == (6, 6)
        # 块对角: 右上块全零
        assert (block[:3, 3:] == 0).all()


class TestCalcIc:
    @pytest.mark.unit
    def test_perfect_correlation(self):
        factor = np.array([1, 2, 3, 4, 5, 6], dtype=float)
        ret = np.array([10, 20, 30, 40, 50, 60], dtype=float)
        ic = calc_ic(factor, ret)
        assert ic == pytest.approx(1.0, abs=1e-6)

    @pytest.mark.unit
    def test_insufficient_samples(self):
        factor = np.array([1, 2, 3], dtype=float)
        ret = np.array([4, 5, 6], dtype=float)
        assert calc_ic(factor, ret) == 0.0


class TestCalcLongShortSharpe:
    @pytest.mark.unit
    def test_insufficient_samples(self):
        factor = np.array([1, 2, 3], dtype=float)
        ret = np.array([4, 5, 6], dtype=float)
        sharpe = calc_long_short_sharpe([factor], [ret], horizon=20, direction=1)
        assert sharpe == 0.0
