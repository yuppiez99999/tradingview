"""经验库 RAG 语义检索 (Experience RAG).

借鉴 google-skills/agent-platform-rag-engine-management 的 RAG corpus + 检索 +
grounded generation 模式, 适配到本系统 cairn/ 知识库与 experiences/ 经验库场景.

与 utils/ai_tools/research_rag.py 互补:
    - research_rag: 研报/因子文档 RAG (sqlite + hash 桶 POC)
    - experience_rag: cairn 知识专题 + 工程经验 RAG (JSON + GLM-5 embedding API)

核心能力:
    1. Corpus 管理: 多语料库隔离 (cairn/experiences/custom).
    2. 嵌入: 优先 GLM-5 embedding API (零新依赖, 复用 glm5_client),
       降级 LiteLLM embedding, 最终降级 hash 桶.
    3. 检索: 余弦相似度 top-k (numpy, 零依赖).
    4. Grounded generation: 检索 + GLM-5 生成 (复用 glm5_client).
    5. 安全分级: create/add 走 Tier M, delete 走 Tier D, list/retrieve 走 Tier R.
    6. 批量摄入: ingest_cairn() 一键把 cairn/*.md 灌入语料库.

存储 (data/experience_rag/):
    corpora.json       # 语料库元数据
    <corpus>_docs.json # 文档 + embedding 向量

用法:
    from utils.experience_rag import ExperienceRAG
    rag = ExperienceRAG()
    rag.ingest_cairn()  # 一键摄入 cairn/ 知识库
    results = rag.retrieve("如何处理对冲成本过滤?", top_k=3)
    answer = rag.grounded_generate("对冲再平衡五阶段流程?")

集成日期: 2026-08-25 (借鉴 google-skills rag-engine-management 模式)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np

from .tier_safety import tier_d, tier_m, tier_r

logger = logging.getLogger("experience_rag")

_BASE_DIR = Path(__file__).resolve().parent.parent
_STORE_DIR = _BASE_DIR / "data" / "experience_rag"
_CAIRN_DIR = _BASE_DIR / "cairn"


# ============================================================
# 数据结构
# ============================================================


@dataclass
class RagDocument:
    """RAG 文档."""

    doc_id: str
    content: str
    source: str = ""
    title: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: list[float] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RetrievalResult:
    """检索结果."""

    doc: RagDocument
    score: float
    rank: int


@dataclass
class Corpus:
    """语料库."""

    name: str
    description: str = ""
    dimension: int = 0
    doc_count: int = 0
    created_at: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )


# ============================================================
# Embedder (GLM-5 API → LiteLLM → hash 桶)
# ============================================================


class Embedder:
    """嵌入基类."""

    def embed(self, text: str) -> list[float]:
        raise NotImplementedError

    @property
    def dimension(self) -> int:
        return 0


class HashEmbedder(Embedder):
    """hash 桶兜底 (零依赖, 非语义)."""

    def __init__(self, dim: int = 128) -> None:
        self._dim = dim

    def embed(self, text: str) -> list[float]:
        h = hashlib.sha256(text.encode("utf-8")).digest()
        vec = [(b - 128) / 128.0 for b in h]
        while len(vec) < self._dim:
            h = hashlib.sha256(h).digest()
            vec.extend((b - 128) / 128.0 for b in h)
        return vec[: self._dim]

    @property
    def dimension(self) -> int:
        return self._dim


class GLM5Embedder(Embedder):
    """GLM-5 embedding API (复用 glm5_client/LiteLLM).

    优先级: LiteLLM embedding → openai SDK → 降级 hash 桶.
    """

    def __init__(self, model: str = "embedding-3", dim: int = 1024) -> None:
        self._model = model
        self._dim = dim
        self._client: Any = None
        self._init_client()

    def _init_client(self) -> None:
        try:
            import litellm

            self._client = ("litellm", litellm)
            return
        except ImportError:
            pass
        try:
            from openai import OpenAI

            api_key = os.environ.get("ZHIPUAI_API_KEY", "")
            if api_key:
                self._client = (
                    "openai",
                    OpenAI(
                        api_key=api_key, base_url="https://open.bigmodel.cn/api/paas/v4"
                    ),
                )
        except ImportError:
            pass

    def embed(self, text: str) -> list[float]:
        if not text.strip():
            return [0.0] * self._dim
        try:
            if self._client and self._client[0] == "litellm":
                resp = self._client[1].embedding(model=self._model, input=text)
                vec = resp["data"][0]["embedding"]
                return vec
            if self._client and self._client[0] == "openai":
                resp = self._client[1].embeddings.create(model=self._model, input=text)
                return list(resp.data[0].embedding)
        except Exception as exc:
            logger.warning("GLM-5 embedding 失败, 降级 hash 桶: %s", exc)
        return HashEmbedder(self._dim).embed(text)

    @property
    def dimension(self) -> int:
        return self._dim


def _make_embedder(use_glm5: bool = True) -> Embedder:
    """构造 embedder (优先 GLM-5, 降级 hash)."""
    if use_glm5:
        try:
            return GLM5Embedder()
        except Exception as exc:
            logger.warning("GLM5Embedder 初始化失败, 降级 hash: %s", exc)
    return HashEmbedder()


# ============================================================
# RAG 引擎
# ============================================================


class ExperienceRAG:
    """经验库 RAG 引擎.

    Args:
        store_dir: 存储目录, 默认 data/experience_rag/.
        use_glm5: True 用 GLM-5 embedding API, False 直接用 hash 桶.
    """

    def __init__(self, store_dir: Optional[Path] = None, *, use_glm5: bool = True):
        self.store_dir = Path(store_dir) if store_dir else _STORE_DIR
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.embedder = _make_embedder(use_glm5)
        self._corpora: dict[str, Corpus] = {}
        self._docs: dict[str, list[RagDocument]] = {}
        self._load()

    # ------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------

    def _load(self) -> None:
        meta_path = self.store_dir / "corpora.json"
        if meta_path.exists():
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                for name, c in (data or {}).items():
                    self._corpora[name] = Corpus(**c)
                    doc_path = self.store_dir / f"{name}_docs.json"
                    if doc_path.exists():
                        docs = json.loads(doc_path.read_text(encoding="utf-8"))
                        self._docs[name] = [RagDocument(**d) for d in docs]
            except Exception as exc:
                logger.error("加载语料库失败: %s", exc)

    def _save(self) -> None:
        try:
            (self.store_dir / "corpora.json").write_text(
                json.dumps(
                    {n: asdict(c) for n, c in self._corpora.items()},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            for name, docs in self._docs.items():
                (self.store_dir / f"{name}_docs.json").write_text(
                    json.dumps(
                        [d.to_dict() for d in docs], ensure_ascii=False, indent=2
                    ),
                    encoding="utf-8",
                )
        except Exception as exc:
            logger.error("持久化失败: %s", exc)

    # ------------------------------------------------------------
    # Corpus CRUD (Tier 分级)
    # ------------------------------------------------------------

    @tier_m("创建语料库", params_extractor=lambda self, name, **kw: {"name": name})
    def create_corpus(self, name: str, *, description: str = "") -> Corpus:
        """创建语料库 (Tier M)."""
        if name in self._corpora:
            return self._corpora[name]
        c = Corpus(
            name=name, description=description, dimension=self.embedder.dimension
        )
        self._corpora[name] = c
        self._docs[name] = []
        self._save()
        return c

    @tier_r("列出语料库")
    def list_corpora(self) -> list[dict[str, Any]]:
        """列出全部语料库 (Tier R)."""
        return [
            {**asdict(c), "doc_count": len(self._docs.get(n, []))}
            for n, c in self._corpora.items()
        ]

    @tier_d("删除语料库", params_extractor=lambda self, name: {"name": name})
    def delete_corpus(self, name: str) -> bool:
        """永久删除语料库 (Tier D)."""
        if name not in self._corpora:
            return False
        del self._corpora[name]
        self._docs.pop(name, None)
        (self.store_dir / f"{name}_docs.json").unlink(missing_ok=True)
        self._save()
        return True

    # ------------------------------------------------------------
    # 文档摄入与检索
    # ------------------------------------------------------------

    @tier_m(
        "添加文档",
        params_extractor=lambda self, corpus, content, **kw: {
            "corpus": corpus,
            "content_len": len(content),
        },
    )
    def add_document(
        self,
        corpus: str,
        content: str,
        *,
        source: str = "",
        title: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> RagDocument:
        """添加文档 (自动嵌入, Tier M)."""
        if corpus not in self._corpora:
            self.create_corpus(corpus)
        doc_id = hashlib.md5(
            f"{corpus}:{content[:200]}".encode(), usedforsecurity=False
        ).hexdigest()[:16]
        existing = {d.doc_id for d in self._docs[corpus]}
        if doc_id in existing:
            return next(d for d in self._docs[corpus] if d.doc_id == doc_id)
        embedding = self.embedder.embed(content)
        doc = RagDocument(
            doc_id=doc_id,
            content=content,
            source=source,
            title=title,
            metadata=metadata or {},
            embedding=embedding,
        )
        self._docs[corpus].append(doc)
        self._corpora[corpus].doc_count = len(self._docs[corpus])
        self._save()
        return doc

    @tier_r("检索", params_extractor=lambda self, query, **kw: {"query": query[:80]})
    def retrieve(
        self, query: str, corpus: str = "cairn", *, top_k: int = 3
    ) -> list[RetrievalResult]:
        """语义检索 top-k (Tier R)."""
        if corpus not in self._docs or not self._docs[corpus]:
            return []
        q_vec = np.array(self.embedder.embed(query), dtype=np.float64)
        results: list[tuple[float, RagDocument]] = []
        for doc in self._docs[corpus]:
            if not doc.embedding:
                continue
            d_vec = np.array(doc.embedding, dtype=np.float64)
            score = self._cosine(q_vec, d_vec)
            results.append((score, doc))
        results.sort(key=lambda x: x[0], reverse=True)
        return [
            RetrievalResult(doc=d, score=float(s), rank=i + 1)
            for i, (s, d) in enumerate(results[:top_k])
        ]

    @tier_r(
        "grounded 生成",
        params_extractor=lambda self, query, **kw: {"query": query[:80]},
    )
    def grounded_generate(
        self,
        query: str,
        corpus: str = "cairn",
        *,
        top_k: int = 3,
        system_prompt: str = "你是量化交易经验助手, 基于以下经验上下文回答.",
    ) -> str:
        """检索 + GLM-5 生成 (Tier RC)."""
        results = self.retrieve(query, corpus, top_k=top_k)
        if not results:
            return f"[无检索结果] {query}"
        context = "\n\n---\n\n".join(
            f"[{r.rank}] (score={r.score:.3f}, source={r.doc.source})\n{r.doc.content[:500]}"
            for r in results
        )
        prompt = f"经验上下文:\n{context}\n\n问题: {query}\n\n请基于上述经验上下文回答:"
        try:
            from .glm5_client import quick_chat

            return quick_chat(prompt, system_prompt=system_prompt)
        except Exception as exc:
            logger.warning("GLM-5 生成失败, 返回检索结果拼接: %s", exc)
            return context

    # ------------------------------------------------------------
    # 批量摄入
    # ------------------------------------------------------------

    def ingest_cairn(self, *, corpus: str = "cairn", limit: int = 100) -> int:
        """一键摄入 cairn/ 知识专题 (Tier M 批量)."""
        if not _CAIRN_DIR.exists():
            logger.warning("cairn/ 目录不存在: %s", _CAIRN_DIR)
            return 0
        count = 0
        for md_file in _CAIRN_DIR.rglob("*.md"):
            if count >= limit:
                break
            try:
                content = md_file.read_text(encoding="utf-8")
                if len(content) < 50:
                    continue
                # BUG FIX (2026-08-25): add_document 是 Tier M 操作, 被拒绝时
                # 返回 None。原代码无条件 count += 1, 非交互环境下会假成功。
                doc = self.add_document(
                    corpus,
                    content,
                    source=str(md_file.relative_to(_BASE_DIR)),
                    title=md_file.stem,
                    metadata={
                        "ingested_at": datetime.now().isoformat(timespec="seconds")
                    },
                )
                if doc is None:
                    logger.warning(
                        "Tier M 拒绝添加文档, 停止摄入 (提示: 设 AUTO_CONFIRM_TIER_M=1 可批量放行)"
                    )
                    break
                count += 1
            except Exception as exc:
                logger.warning("摄入 %s 失败: %s", md_file, exc)
        logger.info("cairn 摄入完成: %d 个文档", count)
        return count

    # ------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------

    @staticmethod
    def _cosine(a: np.ndarray, b: np.ndarray) -> float:
        na = float(np.linalg.norm(a))
        nb = float(np.linalg.norm(b))
        if na == 0 or nb == 0:
            return 0.0
        return float(np.dot(a, b) / (na * nb))


__all__ = [
    "ExperienceRAG",
    "RagDocument",
    "RetrievalResult",
    "Corpus",
    "Embedder",
    "GLM5Embedder",
    "HashEmbedder",
]
