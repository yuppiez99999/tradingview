"""test_transformer_encoder_unit.py — 因子 Transformer 编码器单元测试

覆盖要点:
    - FactorEncodingResult dataclass
    - NumpyFactorEncoder (构造/_layernorm/_gelu/_self_attn_shadow/encode)
    - build_factor_encoder (numpy 后端/force_backend)
    - factors_to_matrix (dict 输入/FactorValue 输入/stocks 指定/None 过滤)
    - encode_factor_frame (端到端/空输入/维度不匹配重建)
"""
from __future__ import annotations

import numpy as np
import pytest

from utils.alpha_factor.transformer_encoder import (
    FactorEncodingResult,
    NumpyFactorEncoder,
    build_factor_encoder,
    encode_factor_frame,
    factors_to_matrix,
)

# ============================================================
# FactorEncodingResult
# ============================================================


class TestFactorEncodingResult:
    @pytest.mark.unit
    def test_defaults(self):
        result = FactorEncodingResult(embeddings=np.zeros((3, 4)), stocks=["A", "B", "C"])
        assert result.attn_weights is None
        assert result.backend == "numpy"
        assert result.meta == {}

    @pytest.mark.unit
    def test_with_attn(self):
        emb = np.random.randn(3, 4)
        attn = np.random.randn(3, 3)
        result = FactorEncodingResult(
            embeddings=emb, stocks=["A", "B", "C"],
            attn_weights=attn, backend="torch",
        )
        assert result.backend == "torch"
        assert result.attn_weights is not None


# ============================================================
# NumpyFactorEncoder
# ============================================================


class TestNumpyFactorEncoder:
    @pytest.mark.unit
    def test_init(self):
        enc = NumpyFactorEncoder(n_factors=10, d_model=8, n_heads=2, n_layers=1)
        assert enc.n_factors == 10
        assert enc.d_model == 8
        assert enc.n_heads == 2
        assert enc.n_layers == 1
        assert enc.W_proj.shape == (10, 8)

    @pytest.mark.unit
    def test_layernorm(self):
        x = np.random.randn(5, 10)
        ln = NumpyFactorEncoder._layernorm(x)
        np.testing.assert_allclose(ln.mean(axis=-1), 0, atol=1e-6)

    @pytest.mark.unit
    def test_gelu(self):
        x = np.array([0.0, 1.0, -1.0])
        result = NumpyFactorEncoder._gelu(x)
        assert result.shape == (3,)
        assert result[0] == pytest.approx(0.0, abs=1e-6)

    @pytest.mark.unit
    def test_encode_shape(self):
        enc = NumpyFactorEncoder(n_factors=5, d_model=8, n_layers=2)
        X = np.random.randn(10, 5)
        Z, attn = enc.encode(X)
        assert Z.shape == (10, 8)

    @pytest.mark.unit
    def test_encode_dim_mismatch_raises(self):
        enc = NumpyFactorEncoder(n_factors=5, d_model=8)
        X = np.random.randn(10, 3)  # 3 != 5
        with pytest.raises(ValueError, match="不匹配"):
            enc.encode(X)

    @pytest.mark.unit
    def test_encode_nan_handling(self):
        """NaN 输入被替换为 0"""
        enc = NumpyFactorEncoder(n_factors=3, d_model=4, n_layers=1)
        X = np.array([[1.0, np.nan, 3.0], [4.0, 5.0, 6.0]])
        Z, _ = enc.encode(X)
        assert not np.isnan(Z).any()

    @pytest.mark.unit
    def test_encode_deterministic(self):
        """固定 seed → 相同输出"""
        enc1 = NumpyFactorEncoder(n_factors=3, d_model=4, seed=42)
        enc2 = NumpyFactorEncoder(n_factors=3, d_model=4, seed=42)
        X = np.random.randn(5, 3)
        Z1, _ = enc1.encode(X)
        Z2, _ = enc2.encode(X)
        np.testing.assert_allclose(Z1, Z2)


# ============================================================
# build_factor_encoder
# ============================================================


class TestBuildFactorEncoder:
    @pytest.mark.unit
    def test_numpy_backend(self):
        enc = build_factor_encoder(n_factors=10, d_model=8, force_backend="numpy")
        assert isinstance(enc, NumpyFactorEncoder)

    @pytest.mark.unit
    def test_auto_fallback_numpy(self):
        """torch 不可用时自动降级 numpy; torch 可用时返回 torch 后端"""
        from utils.alpha_factor.transformer_encoder import _TORCH_AVAILABLE
        enc = build_factor_encoder(n_factors=10, d_model=8)
        if _TORCH_AVAILABLE:
            assert getattr(enc, "backend", None) == "torch" or enc.__class__.__name__ == "_TorchFactorEncoder"
        else:
            assert isinstance(enc, NumpyFactorEncoder)

    @pytest.mark.unit
    def test_force_torch_raises(self):
        from utils.alpha_factor.transformer_encoder import _TORCH_AVAILABLE
        if _TORCH_AVAILABLE:
            enc = build_factor_encoder(n_factors=10, force_backend="torch")
            assert enc.__class__.__name__ == "_TorchFactorEncoder"
        else:
            with pytest.raises(RuntimeError, match="torch"):
                build_factor_encoder(n_factors=10, force_backend="torch")


# ============================================================
# factors_to_matrix
# ============================================================


class TestFactorsToMatrix:
    @pytest.mark.unit
    def test_dict_input(self):
        factors = {
            "MOM": {"A": 1.0, "B": 2.0, "C": 3.0},
            "VOL": {"A": 0.1, "B": 0.2, "C": 0.3},
        }
        X, stocks = factors_to_matrix(factors)
        assert len(stocks) == 3
        assert X.shape == (3, 2)  # 3 stocks, 2 factors

    @pytest.mark.unit
    def test_specified_stocks(self):
        factors = {"MOM": {"A": 1.0, "B": 2.0, "C": 3.0}}
        X, stocks = factors_to_matrix(factors, stocks=["C", "B", "A"])
        assert stocks == ["C", "B", "A"]
        assert X[0, 0] == 3.0  # C
        assert X[1, 0] == 2.0  # B
        assert X[2, 0] == 1.0  # A

    @pytest.mark.unit
    def test_none_values_skipped(self):
        factors = {"MOM": {"A": 1.0, "B": None, "C": 3.0}}
        X, stocks = factors_to_matrix(factors)
        assert "B" not in stocks

    @pytest.mark.unit
    def test_empty_factors(self):
        X, stocks = factors_to_matrix({})
        assert X.shape == (0, 0)
        assert stocks == []

    @pytest.mark.unit
    def test_factor_with_values_attr(self):
        """FactorValue 对象 (有 .values dict 属性)"""
        mock_fv = type("MockFV", (), {"values": {"A": 1.0, "B": 2.0}})()
        factors = {"MOM": mock_fv}
        X, stocks = factors_to_matrix(factors)
        assert len(stocks) == 2


# ============================================================
# encode_factor_frame (端到端)
# ============================================================


class TestEncodeFactorFrame:
    @pytest.mark.unit
    def test_basic(self):
        factors = {
            "MOM": {"A": 1.0, "B": 2.0, "C": 3.0},
            "VOL": {"A": 0.1, "B": 0.2, "C": 0.3},
        }
        result = encode_factor_frame(factors, d_model=4, force_backend="numpy")
        assert isinstance(result, FactorEncodingResult)
        assert result.embeddings.shape == (3, 4)
        assert len(result.stocks) == 3
        assert result.backend == "numpy"

    @pytest.mark.unit
    def test_empty_input(self):
        result = encode_factor_frame({}, d_model=4)
        assert result.embeddings.shape == (0, 4)
        assert "warning" in result.meta

    @pytest.mark.unit
    def test_with_custom_encoder(self):
        factors = {"MOM": {"A": 1.0, "B": 2.0}}
        encoder = NumpyFactorEncoder(n_factors=1, d_model=4)
        result = encode_factor_frame(factors, encoder=encoder)
        assert result.embeddings.shape == (2, 4)

    @pytest.mark.unit
    def test_dim_mismatch_rebuild(self):
        """encoder n_factors 与实际不匹配 → 重建"""
        factors = {
            "MOM": {"A": 1.0, "B": 2.0},
            "VOL": {"A": 0.1, "B": 0.2},
        }
        encoder = NumpyFactorEncoder(n_factors=1, d_model=4)  # 1 != 2
        result = encode_factor_frame(factors, encoder=encoder)
        assert result.embeddings.shape == (2, 4)
