"""嵌入降级链 — CTX-A2 T1.

三档运行时一次性探测: sentence_transformers → sqlite FTS5 → hashed n-gram.

设计 (spec_CTX-A2.md §2.6):
    1. import sentence_transformers + 模型缓存命中 → 档 1 (真向量)
    2. CREATE VIRTUAL TABLE ... USING fts5 探测 → 档 2 (SQL 全文检索, embed 返回 None)
    3. 兜底 → 档 3 hashed n-gram (纯 numpy 256 维, 任何环境可跑)

探测结果缓存在模块级, 供 A4 报告引用.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

_HASH_DIM = 256
_NGRAM_SIZE = 3

_cached_backend: str | None = None
_st_model: object | None = None


def _detect_sentence_transformers() -> bool:
    """档 1: 探测 sentence_transformers 是否可用 + 模型缓存命中."""
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
    except ImportError:
        return False
    global _st_model
    for model_name in ("all-MiniLM-L6-v2", "paraphrase-multilingual-MiniLM-L12-v2"):
        try:
            cache_dir = Path.home() / ".cache" / "huggingface"
            if not cache_dir.exists():
                continue
            _st_model = SentenceTransformer(model_name)
            logger.info("嵌入档 1: sentence_transformers (%s)", model_name)
            return True
        except Exception as e:  # noqa: BLE001
            logger.debug("sentence_transformers %s 加载失败: %s", model_name, e)
    return False


def _detect_fts5() -> bool:
    """档 2: 探测 sqlite FTS5 是否可用."""
    try:
        with sqlite3.connect(":memory:") as conn:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS fts5_test USING fts5(content)"
            )
            conn.execute("INSERT INTO fts5_test VALUES ('hello world')")
            row = conn.execute(
                "SELECT * FROM fts5_test WHERE fts5_test MATCH 'hello'"
            ).fetchone()
            if row:
                logger.info("嵌入档 2: sqlite FTS5")
                return True
    except sqlite3.OperationalError as e:
        logger.debug("FTS5 不可用: %s", e)
    return False


def detect_embedding_backend() -> str:
    """探测嵌入后端, 返回 "sentence_transformers" | "fts5" | "hash".

    探测顺序固定, 结果缓存在模块级.
    """
    global _cached_backend
    if _cached_backend is not None:
        return _cached_backend

    if _detect_sentence_transformers():
        _cached_backend = "sentence_transformers"
    elif _detect_fts5():
        _cached_backend = "fts5"
    else:
        _cached_backend = "hash"
        logger.info("嵌入档 3: hashed n-gram (numpy %d 维)", _HASH_DIM)

    return _cached_backend


def _hash_ngram_embedding(text: str) -> list[float]:
    """n-gram hash 到 256 维 (纯 numpy, 任何环境可跑).

    签名: hash(n-gram) % _HASH_DIM → 累加 → L2 归一化.
    """
    text = text.lower().strip()
    if not text:
        return [0.0] * _HASH_DIM

    vec = np.zeros(_HASH_DIM, dtype=np.float32)
    for i in range(len(text) - _NGRAM_SIZE + 1):
        ngram = text[i : i + _NGRAM_SIZE]
        h = int(hashlib.md5(ngram.encode("utf-8")).hexdigest(), 16)
        vec[h % _HASH_DIM] += 1.0

    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec.tolist()


def _sentence_transformers_embedding(text: str) -> list[float]:
    """档 1: sentence_transformers 真向量."""
    global _st_model
    if _st_model is None:
        return _hash_ngram_embedding(text)
    try:
        vec = _st_model.encode(text)  # type: ignore[union-attr]
        return vec.tolist()
    except Exception as e:  # noqa: BLE001
        logger.warning("sentence_transformers encode 失败, 回退 hash: %s", e)
        return _hash_ngram_embedding(text)


def embed(text: str) -> list[float] | None:
    """文本 → 向量.

    Returns:
        sentence_transformers 档: 真向量;
        hash 档: n-gram hash 256 维向量;
        fts5 档: None (调用方走 SQL 全文检索).
    """
    backend = detect_embedding_backend()
    if backend == "sentence_transformers":
        return _sentence_transformers_embedding(text)
    if backend == "hash":
        return _hash_ngram_embedding(text)
    return None


def cosine_sim(a: list[float], b: list[float]) -> float:
    """余弦相似度."""
    if not a or not b:
        return 0.0
    arr_a = np.asarray(a, dtype=np.float32)
    arr_b = np.asarray(b, dtype=np.float32)
    norm_a = np.linalg.norm(arr_a)
    norm_b = np.linalg.norm(arr_b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(arr_a, arr_b) / (norm_a * norm_b))


def reset_cache() -> None:
    """重置模块级缓存 (仅测试用)."""
    global _cached_backend, _st_model
    _cached_backend = None
    _st_model = None
