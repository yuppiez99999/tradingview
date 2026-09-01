"""研报 RAG + 因子记忆库 — OpenViking 风格脚手架 (Wave 9 / W9-B)

灵感来源: https://github.com/volcengine/OpenViking (29,362 stars, 2026-08-19 GitHub Trending)
OpenViking 是字节跳动出品的自进化 AI Agent 上下文数据库, 统一 Agent Memory / Knowledge RAG / Skills。
本模块借鉴其设计理念, 为 28 系统提供:
  - 研报语义检索 (向量库 + Embedding)
  - 因子库沉淀 (因子定义 + IC 历史 + 实验结论)
  - 策略迭代记忆 (跨会话保留回测/PR review 结论)

设计原则:
  - 边缘安全: 不动 utils/alpha_factor/library.py 主链路, 仅提供新 RAG API
  - 后端可插拔: 默认 sqlite (零依赖), 可选 chroma/faiss (W9-B 后续迭代)
  - Embedding 可选: 默认 hash 桶 (POC), 可选 transformer_encoder.py 的 117因子→64维
  - 09-05 前仅脚手架 + POC, 不接入 research_distiller.py 主链路
  - 09-06 起进入实质对接 (W9-B Sprint)

接入点:
  - utils/ai_tools/code_graph_rag.py (现有代码图谱 RAG, 非向量检索, 互补)
  - utils/alpha_factor/library.py (因子库主入口, 09-06 后对接)
  - utils/research_distiller.py (研报蒸馏, 09-06 后对接)
  - data/feature_registry.json (因子注册表)

关联文档:
  - docs/高价值项目集成排期计划_20260811.md §8.3 (W9-B Sprint)
  - cairn/github-trending-wave9-20260819.md (待创建)
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from utils.logging_manager import get_logger

    logger = get_logger("research_rag")
except ImportError:
    logger = logging.getLogger("research_rag")


# ============================================================
# 数据结构
# ============================================================


@dataclass
class Document:
    """RAG 文档 (研报/公告/因子说明等)

    Attributes:
        doc_id: 文档 ID (自动生成)
        source: 来源 (research_report/announcement/factor_doc/...)
        title: 标题
        content: 正文
        metadata: 元数据 (date/symbol/author/...)
        embedding: 向量 (None 时按需计算)
        created_at: 创建时间戳
    """

    doc_id: str
    source: str
    title: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: list[float] | None = None
    created_at: float = field(default_factory=time.time)


@dataclass
class RetrievalResult:
    """检索结果"""

    doc: Document
    score: float  # 相似度 0~1
    rank: int


# ============================================================
# Embedding 接口 (可插拔)
# ============================================================


class Embedder:
    """Embedding 基类

    W9-B POC 用 HashEmbedder (零依赖);
    W9-B 后续可接 transformer_encoder.py 的 PyTorch 117因子→64维。
    """

    def embed(self, text: str) -> list[float]:
        raise NotImplementedError


class HashEmbedder(Embedder):
    """Hash 桶 Embedding (POC, 零依赖)

    不是真正的语义 Embedding, 仅用于 W9-B 脚手架验证流程。
    09-06 后替换为 transformer_encoder 或外部 Embedding API。
    """

    def __init__(self, dim: int = 64) -> None:
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        h = hashlib.sha256(text.encode("utf-8")).digest()
        vec = [(b - 128) / 128.0 for b in h]
        while len(vec) < self.dim:
            h = hashlib.sha256(h).digest()
            vec.extend((b - 128) / 128.0 for b in h)
        return vec[: self.dim]


# ============================================================
# 向量库后端 (可插拔)
# ============================================================


class VectorStore:
    """向量库基类

    W9-B POC 用 SQLiteVectorStore (零依赖);
    W9-B 后续可接 chroma/faiss/milvus。
    """

    def add(self, doc: Document) -> None:
        raise NotImplementedError

    def search(self, query: list[float], top_k: int = 5) -> list[RetrievalResult]:
        raise NotImplementedError


class SQLiteVectorStore(VectorStore):
    """SQLite 向量库 (POC, 零依赖)

    存储: documents 表 (doc_id/source/title/content/metadata/embedding/created_at)
    检索: 暴力余弦相似度 (W9-B POC 足够, 09-06 后换 faiss)
    """

    def __init__(self, db_path: str | None = None) -> None:
        if db_path is None:
            base = Path(__file__).resolve().parent.parent.parent
            db_path = str(base / "data" / "research_rag.db")
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    doc_id TEXT PRIMARY KEY,
                    source TEXT,
                    title TEXT,
                    content TEXT,
                    metadata TEXT,
                    embedding TEXT,
                    created_at REAL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_source ON documents(source)")

    def add(self, doc: Document) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?,?,?)",
                (
                    doc.doc_id,
                    doc.source,
                    doc.title,
                    doc.content,
                    json.dumps(doc.metadata, ensure_ascii=False),
                    json.dumps(doc.embedding or []),
                    doc.created_at,
                ),
            )
        logger.info("add doc: %s (%s)", doc.doc_id, doc.source)

    def search(self, query: list[float], top_k: int = 5) -> list[RetrievalResult]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT doc_id, source, title, content, metadata, embedding, created_at FROM documents"
            ).fetchall()

        results: list[RetrievalResult] = []
        for i, row in enumerate(rows):
            doc = Document(
                doc_id=row[0],
                source=row[1],
                title=row[2],
                content=row[3],
                metadata=json.loads(row[4]) if row[4] else {},
                embedding=json.loads(row[5]) if row[5] else [],
                created_at=row[6],
            )
            score = _cosine(query, doc.embedding) if doc.embedding else 0.0
            results.append(RetrievalResult(doc=doc, score=score, rank=i))

        results.sort(key=lambda r: r.score, reverse=True)
        for i, r in enumerate(results[:top_k]):
            r.rank = i + 1
        return results[:top_k]


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na > 0 and nb > 0 else 0.0


# ============================================================
# 研报 RAG 主接口
# ============================================================


class ResearchRAG:
    """研报 RAG 主接口 (OpenViking 风格)

    Usage:
        rag = ResearchRAG()
        rag.ingest("research_report", "宁德时代深度报告", "正文...", {"symbol": "300750"})
        results = rag.query("新能源车电池龙头", top_k=3)
        for r in results:
            print(r.rank, r.score, r.doc.title)
    """

    def __init__(
        self,
        embedder: Embedder | None = None,
        store: VectorStore | None = None,
    ) -> None:
        self.embedder = embedder or HashEmbedder()
        self.store = store or SQLiteVectorStore()

    def ingest(
        self,
        source: str,
        title: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> Document:
        """摄入文档 (自动计算 Embedding)"""
        doc_id = hashlib.md5(
            f"{source}:{title}".encode(), usedforsecurity=False
        ).hexdigest()[:16]
        doc = Document(
            doc_id=doc_id,
            source=source,
            title=title,
            content=content,
            metadata=metadata or {},
            embedding=self.embedder.embed(content),
        )
        self.store.add(doc)
        return doc

    def query(self, text: str, top_k: int = 5) -> list[RetrievalResult]:
        """语义检索"""
        qvec = self.embedder.embed(text)
        return self.store.search(qvec, top_k=top_k)
