"""ai_tools/research_rag.py 单元测试 — 研报 RAG + 因子记忆库.

目标模块: utils/ai_tools/research_rag.py (0% → 高覆盖)
覆盖: Document / RetrievalResult / HashEmbedder / SQLiteVectorStore / _cosine / ResearchRAG
"""

from __future__ import annotations

import math
import sqlite3

import pytest

from utils.ai_tools.research_rag import (
    Document,
    Embedder,
    HashEmbedder,
    ResearchRAG,
    RetrievalResult,
    SQLiteVectorStore,
    VectorStore,
    _cosine,
)

# ============================================================
# DocumentTest — RAG 文档数据结构
# ============================================================


class DocumentTest:

    def test_document_defaults(self):
        d = Document(doc_id="d1", source="report", title="t", content="c")
        assert d.doc_id == "d1"
        assert d.source == "report"
        assert d.title == "t"
        assert d.content == "c"
        assert d.metadata == {}
        assert d.embedding is None
        assert isinstance(d.created_at, float)

    def test_document_with_fields(self):
        d = Document(
            doc_id="d2",
            source="ann",
            title="t2",
            content="c2",
            metadata={"sym": "510300"},
            embedding=[0.1, 0.2],
            created_at=1000.0,
        )
        assert d.metadata == {"sym": "510300"}
        assert d.embedding == [0.1, 0.2]
        assert d.created_at == 1000.0


# ============================================================
# RetrievalResultTest — 检索结果
# ============================================================


class RetrievalResultTest:

    def test_retrieval_result(self):
        doc = Document(doc_id="d1", source="s", title="t", content="c")
        r = RetrievalResult(doc=doc, score=0.85, rank=1)
        assert r.doc is doc
        assert r.score == 0.85
        assert r.rank == 1


# ============================================================
# HashEmbedderTest — Hash 桶 Embedding
# ============================================================


class HashEmbedderTest:

    def test_embed_default_dim(self):
        emb = HashEmbedder()
        vec = emb.embed("hello world")
        assert len(vec) == 64

    def test_embed_custom_dim(self):
        emb = HashEmbedder(dim=32)
        vec = emb.embed("test")
        assert len(vec) == 32

    def test_embed_deterministic(self):
        emb = HashEmbedder()
        v1 = emb.embed("same text")
        v2 = emb.embed("same text")
        assert v1 == v2

    def test_embed_different_text_different_vec(self):
        emb = HashEmbedder()
        v1 = emb.embed("text a")
        v2 = emb.embed("text b")
        assert v1 != v2

    def test_embed_value_range(self):
        """每个分量应在 [-1, 1] 范围内 (byte-128)/128."""
        emb = HashEmbedder(dim=128)
        vec = emb.embed("range test")
        assert all(-1.0 <= v <= 1.0 for v in vec)

    def test_embedder_base_raises(self):
        base = Embedder()
        with pytest.raises(NotImplementedError):
            base.embed("x")


# ============================================================
# CosineTest — 余弦相似度辅助函数
# ============================================================


class CosineTest:

    def test_identical_vectors(self):
        v = [1.0, 0.0, 0.0]
        assert _cosine(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_empty_returns_zero(self):
        assert _cosine([], [1.0]) == 0.0
        assert _cosine([1.0], []) == 0.0

    def test_length_mismatch_returns_zero(self):
        assert _cosine([1.0, 2.0], [1.0]) == 0.0

    def test_zero_vector_returns_zero(self):
        assert _cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
        assert _cosine([1.0, 1.0], [0.0, 0.0]) == 0.0

    def test_known_value(self):
        a = [1.0, 2.0, 3.0]
        b = [4.0, 5.0, 6.0]
        expected = (4 + 10 + 18) / (math.sqrt(14) * math.sqrt(77))
        assert _cosine(a, b) == pytest.approx(expected)


# ============================================================
# SQLiteVectorStoreTest — SQLite 向量库
# ============================================================


class SQLiteVectorStoreTest:

    def test_init_creates_table(self, tmp_path):
        db = str(tmp_path / "test_rag.db")
        SQLiteVectorStore(db_path=db)
        with sqlite3.connect(db) as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        assert ("documents",) in tables

    def test_init_creates_index(self, tmp_path):
        db = str(tmp_path / "test_rag.db")
        SQLiteVectorStore(db_path=db)
        with sqlite3.connect(db) as conn:
            idx = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_source'"
            ).fetchall()
        assert len(idx) == 1

    def test_add_and_search(self, tmp_path):
        db = str(tmp_path / "test_rag.db")
        store = SQLiteVectorStore(db_path=db)
        emb = HashEmbedder()
        vec = emb.embed("content one")
        doc = Document(
            doc_id="d1",
            source="report",
            title="T1",
            content="content one",
            embedding=vec,
        )
        store.add(doc)
        results = store.search(vec, top_k=5)
        assert len(results) == 1
        assert results[0].doc.doc_id == "d1"
        assert results[0].score == pytest.approx(1.0)
        assert results[0].rank == 1

    def test_search_empty_store(self, tmp_path):
        db = str(tmp_path / "test_rag.db")
        store = SQLiteVectorStore(db_path=db)
        results = store.search([1.0, 0.0], top_k=5)
        assert results == []

    def test_search_top_k_limit(self, tmp_path):
        db = str(tmp_path / "test_rag.db")
        store = SQLiteVectorStore(db_path=db)
        emb = HashEmbedder()
        for i in range(5):
            vec = emb.embed(f"content {i}")
            store.add(
                Document(
                    doc_id=f"d{i}",
                    source="s",
                    title=f"T{i}",
                    content=f"content {i}",
                    embedding=vec,
                )
            )
        qvec = emb.embed("content 0")
        results = store.search(qvec, top_k=2)
        assert len(results) == 2
        assert results[0].rank == 1
        assert results[1].rank == 2

    def test_search_sorted_by_score_desc(self, tmp_path):
        db = str(tmp_path / "test_rag.db")
        store = SQLiteVectorStore(db_path=db)
        emb = HashEmbedder()
        vecs = [emb.embed(f"doc {i}") for i in range(3)]
        for i, v in enumerate(vecs):
            store.add(
                Document(
                    doc_id=f"d{i}",
                    source="s",
                    title=f"T{i}",
                    content=f"doc {i}",
                    embedding=v,
                )
            )
        results = store.search(vecs[0], top_k=3)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_search_doc_without_embedding(self, tmp_path):
        """文档 embedding 为空时 score=0.0."""
        db = str(tmp_path / "test_rag.db")
        store = SQLiteVectorStore(db_path=db)
        store.add(
            Document(
                doc_id="d0",
                source="s",
                title="T",
                content="c",
                embedding=None,
            )
        )
        results = store.search([1.0, 0.0], top_k=5)
        assert len(results) == 1
        assert results[0].score == 0.0

    def test_add_replaces_existing(self, tmp_path):
        db = str(tmp_path / "test_rag.db")
        store = SQLiteVectorStore(db_path=db)
        emb = HashEmbedder()
        vec = emb.embed("v1")
        store.add(
            Document(doc_id="dup", source="s", title="T1", content="c1", embedding=vec)
        )
        vec2 = emb.embed("v2")
        store.add(
            Document(doc_id="dup", source="s", title="T2", content="c2", embedding=vec2)
        )
        results = store.search(vec2, top_k=5)
        assert len(results) == 1
        assert results[0].doc.title == "T2"

    def test_vector_store_base_raises(self):
        base = VectorStore()
        with pytest.raises(NotImplementedError):
            base.add(Document(doc_id="x", source="s", title="t", content="c"))
        with pytest.raises(NotImplementedError):
            base.search([1.0], top_k=1)


# ============================================================
# ResearchRAGTest — 研报 RAG 主接口
# ============================================================


class ResearchRAGTest:

    def test_default_init(self, tmp_path, monkeypatch):
        """默认初始化使用 HashEmbedder + SQLiteVectorStore."""
        db = str(tmp_path / "default.db")
        store = SQLiteVectorStore(db_path=db)
        rag = ResearchRAG(store=store)
        assert isinstance(rag.embedder, HashEmbedder)
        assert rag.store is store

    def test_ingest_returns_document(self, tmp_path):
        db = str(tmp_path / "ingest.db")
        rag = ResearchRAG(store=SQLiteVectorStore(db_path=db))
        doc = rag.ingest(
            source="research_report",
            title="宁德时代深度",
            content="新能源车电池龙头",
            metadata={"symbol": "300750"},
        )
        assert isinstance(doc, Document)
        assert doc.source == "research_report"
        assert doc.title == "宁德时代深度"
        assert doc.embedding is not None
        assert len(doc.embedding) == 64
        assert doc.metadata == {"symbol": "300750"}

    def test_ingest_doc_id_deterministic(self, tmp_path):
        db = str(tmp_path / "ingest_id.db")
        rag = ResearchRAG(store=SQLiteVectorStore(db_path=db))
        d1 = rag.ingest("s", "title", "content")
        d2 = rag.ingest("s", "title", "content")
        assert d1.doc_id == d2.doc_id

    def test_ingest_different_title_different_id(self, tmp_path):
        db = str(tmp_path / "ingest_diff.db")
        rag = ResearchRAG(store=SQLiteVectorStore(db_path=db))
        d1 = rag.ingest("s", "title1", "content")
        d2 = rag.ingest("s", "title2", "content")
        assert d1.doc_id != d2.doc_id

    def test_query_returns_results(self, tmp_path):
        db = str(tmp_path / "query.db")
        rag = ResearchRAG(store=SQLiteVectorStore(db_path=db))
        rag.ingest("report", "Title A", "alpha factor momentum", {"sym": "A"})
        rag.ingest("report", "Title B", "beta risk volatility", {"sym": "B"})
        results = rag.query("momentum factor", top_k=2)
        assert len(results) == 2
        assert all(isinstance(r, RetrievalResult) for r in results)

    def test_query_empty_store(self, tmp_path):
        db = str(tmp_path / "query_empty.db")
        rag = ResearchRAG(store=SQLiteVectorStore(db_path=db))
        results = rag.query("anything", top_k=5)
        assert results == []

    def test_query_top_k(self, tmp_path):
        db = str(tmp_path / "query_topk.db")
        rag = ResearchRAG(store=SQLiteVectorStore(db_path=db))
        for i in range(5):
            rag.ingest("s", f"T{i}", f"content number {i}")
        results = rag.query("content", top_k=3)
        assert len(results) == 3

    def test_custom_embedder(self, tmp_path):
        db = str(tmp_path / "custom_emb.db")
        emb = HashEmbedder(dim=32)
        rag = ResearchRAG(embedder=emb, store=SQLiteVectorStore(db_path=db))
        doc = rag.ingest("s", "t", "c")
        assert len(doc.embedding) == 32
