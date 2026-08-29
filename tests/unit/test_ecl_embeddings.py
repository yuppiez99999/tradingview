"""嵌入降级链单测 — CTX-A2 T1.

覆盖: 三档各自单测 (monkeypatch 探测)、cosine_sim、hash 向量性质.
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest

from utils.infra.ecl.embeddings import (
    _hash_ngram_embedding,
    cosine_sim,
    detect_embedding_backend,
    embed,
    reset_cache,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_cache()
    yield
    reset_cache()


class TestDetectBackend:
    """三档降级链探测."""

    def test_hash_fallback(self) -> None:
        with patch(
            "utils.infra.ecl.embeddings._detect_sentence_transformers",
            return_value=False,
        ):
            with patch("utils.infra.ecl.embeddings._detect_fts5", return_value=False):
                backend = detect_embedding_backend()
        assert backend == "hash"

    def test_fts5_when_no_st(self) -> None:
        with patch(
            "utils.infra.ecl.embeddings._detect_sentence_transformers",
            return_value=False,
        ):
            with patch("utils.infra.ecl.embeddings._detect_fts5", return_value=True):
                backend = detect_embedding_backend()
        assert backend == "fts5"

    def test_sentence_transformers_priority(self) -> None:
        with patch(
            "utils.infra.ecl.embeddings._detect_sentence_transformers",
            return_value=True,
        ):
            with patch("utils.infra.ecl.embeddings._detect_fts5", return_value=True):
                backend = detect_embedding_backend()
        assert backend == "sentence_transformers"

    def test_cache_after_first_detect(self) -> None:
        with patch(
            "utils.infra.ecl.embeddings._detect_sentence_transformers",
            return_value=False,
        ):
            with patch("utils.infra.ecl.embeddings._detect_fts5", return_value=False):
                b1 = detect_embedding_backend()
                b2 = detect_embedding_backend()
        assert b1 == b2 == "hash"


class TestEmbed:
    """embed API."""

    def test_hash_returns_vector(self) -> None:
        with patch(
            "utils.infra.ecl.embeddings._detect_sentence_transformers",
            return_value=False,
        ):
            with patch("utils.infra.ecl.embeddings._detect_fts5", return_value=False):
                vec = embed("bull regime vix low")
        assert vec is not None
        assert len(vec) == 256

    def test_fts5_returns_none(self) -> None:
        with patch(
            "utils.infra.ecl.embeddings._detect_sentence_transformers",
            return_value=False,
        ):
            with patch("utils.infra.ecl.embeddings._detect_fts5", return_value=True):
                vec = embed("bull regime vix low")
        assert vec is None

    def test_hash_empty_text(self) -> None:
        vec = _hash_ngram_embedding("")
        assert all(v == 0.0 for v in vec)

    def test_hash_same_text_same_vector(self) -> None:
        v1 = _hash_ngram_embedding("bull regime")
        v2 = _hash_ngram_embedding("bull regime")
        assert v1 == v2

    def test_hash_l2_normalized(self) -> None:
        vec = _hash_ngram_embedding("a meaningful text for normalization test")
        norm = np.linalg.norm(vec)
        assert abs(norm - 1.0) < 1e-5 or norm == 0.0


class TestCosineSim:
    """余弦相似度."""

    def test_identical_vectors(self) -> None:
        v = [1.0, 0.0, 0.0]
        assert abs(cosine_sim(v, v) - 1.0) < 1e-6

    def test_orthogonal_vectors(self) -> None:
        assert abs(cosine_sim([1.0, 0.0], [0.0, 1.0])) < 1e-6

    def test_zero_vector(self) -> None:
        assert cosine_sim([0.0, 0.0], [1.0, 0.0]) == 0.0

    def test_empty_vector(self) -> None:
        assert cosine_sim([], [1.0]) == 0.0

    def test_similar_text_high_similarity(self) -> None:
        v1 = _hash_ngram_embedding("bull regime vix low drawdown shallow")
        v2 = _hash_ngram_embedding("bull regime vix low drawdown shallow")
        sim = cosine_sim(v1, v2)
        assert sim > 0.99
