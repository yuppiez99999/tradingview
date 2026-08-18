"""test_gat_factor_torch_unit.py — GAT 因子 torch 版单元测试

torch 在 Python 3.8 环境 DLL 加载失败 (caffe2_nvrtc.dll),
本测试文件在 torch 可用时运行, 否则全部跳过。
"""
from __future__ import annotations

import pytest

try:
    import torch
    _TORCH_OK = True
except (ImportError, OSError, ModuleNotFoundError):
    _TORCH_OK = False

pytestmark = pytest.mark.skipif(not _TORCH_OK, reason="torch 不可用 (DLL 加载失败)")

if _TORCH_OK:
    import numpy as np
    from utils.alpha_factor.gat_factor_torch import GATFactorTorch, GATLayer, build_adjacency


class TestGATLayer:
    @pytest.mark.unit
    def test_forward_shape(self):
        layer = GATLayer(n_features=4, n_hidden=8, n_heads=2)
        h = torch.randn(5, 4)
        adj = torch.ones(5, 5) - torch.eye(5)
        out = layer(h, adj)
        assert out.shape == (5, 2 * 8)


class TestGATFactorTorch:
    @pytest.mark.unit
    def test_compute_shape(self):
        model = GATFactorTorch(n_hidden=4, n_heads=2)
        features = np.random.randn(5, 3).astype(np.float32)
        adj = np.ones((5, 5), dtype=np.float32) - np.eye(5, dtype=np.float32)
        factor = model.compute(features, adj)
        assert factor.shape == (5,)

    @pytest.mark.unit
    def test_train_returns_losses(self):
        model = GATFactorTorch(n_hidden=4, n_heads=2)
        features = np.random.randn(10, 3).astype(np.float32)
        adj = np.ones((10, 10), dtype=np.float32) - np.eye(10, dtype=np.float32)
        labels = np.random.randn(10).astype(np.float32)
        losses = model.train(features, adj, labels, epochs=3)
        assert len(losses) == 3
