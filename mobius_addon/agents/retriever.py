"""联网论文检索：arXiv + Semantic Scholar，评分去重取 Top-N。"""

from __future__ import annotations

import re
from typing import Any

import _common as _c
import requests

try:  # 仓库内运行时
    from utils.safe_xml import safe_xml_fromstring
except ImportError:  # 独立脚本/脱离仓库根运行时
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from utils.safe_xml import safe_xml_fromstring

_LOGGER = _c.get_logger()

ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}


def search_arxiv(query: str, max_results: int = 10) -> list[dict[str, Any]]:
    url = "https://export.arxiv.org/api/query"  # HTTPS: 论文 XML 属不可信远程输入
    params = {
        "search_query": "all:" + query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "relevance",
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        root = safe_xml_fromstring(resp.text)
        out = []
        for entry in root.findall("a:entry", ATOM_NS):
            title = (
                entry.findtext("a:title", default="", namespaces=ATOM_NS) or ""
            ).strip()
            summary = (
                entry.findtext("a:summary", default="", namespaces=ATOM_NS) or ""
            ).strip()
            idurl = entry.findtext("a:id", default="", namespaces=ATOM_NS) or ""
            authors = [
                a.findtext("a:name", default="", namespaces=ATOM_NS)
                for a in entry.findall("a:author", ATOM_NS)
            ]
            published = (
                entry.findtext("a:published", default="", namespaces=ATOM_NS) or ""
            )
            year = int(published[:4]) if published[:4].isdigit() else 0
            aid = idurl.split("/abs/")[-1] if "abs" in idurl else idurl
            out.append(
                {
                    "paper_id": "arxiv:" + aid,
                    "source": "arxiv",
                    "title": title,
                    "authors": [a for a in authors if a],
                    "year": year,
                    "url": idurl,
                    "citations": 0,
                    "abstract": summary,
                }
            )
        return out
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("arXiv 检索失败: %s", exc)
        return []


def search_s2(query: str, max_results: int = 10) -> list[dict[str, Any]]:
    url = "https://api.semanticscholar.org/graph/v1/paper/search"
    params = {
        "query": query,
        "limit": max_results,
        "fields": "title,year,citationCount,authors,abstract,externalIds,url",
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        out = []
        for paper in data.get("data", []):
            ext = paper.get("externalIds") or {}
            aid = ext.get("ArXiv") or paper.get("paperId") or ""
            out.append(
                {
                    "paper_id": "s2:" + str(aid),
                    "source": "s2",
                    "title": paper.get("title", ""),
                    "authors": [a.get("name", "") for a in paper.get("authors", [])],
                    "year": paper.get("year") or 0,
                    "url": paper.get("url", ""),
                    "citations": paper.get("citationCount") or 0,
                    "abstract": paper.get("abstract") or "",
                }
            )
        return out
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("Semantic Scholar 检索失败: %s", exc)
        return []


def _norm(s: str) -> str:
    return re.sub(r"\W+", " ", (s or "").lower()).strip()


def rank_and_dedup(
    papers: list[dict[str, Any]], max_papers: int = 10
) -> list[dict[str, Any]]:
    seen = {}
    for paper in papers:
        key = _norm(paper.get("title", ""))
        if not key:
            continue
        if key in seen:
            if paper.get("citations", 0) > seen[key].get("citations", 0):
                seen[key] = paper
            continue
        seen[key] = paper
    unique = list(seen.values())

    def score(p: dict[str, Any]) -> float:
        cit = min(p.get("citations", 0), 500)
        recency_penalty = max(0, 2026 - int(p.get("year") or 0))
        return float(cit) - recency_penalty * 2

    unique.sort(key=score, reverse=True)
    return unique[:max_papers]


def retrieve(topic: str, max_papers: int = 10) -> list[dict[str, Any]]:
    """检索并合并 arXiv + S2 结果，评分去重后返回 Top-N。"""
    papers = []  # type: List[Dict[str, Any]]
    papers += search_arxiv(topic, max_papers)
    papers += search_s2(topic, max_papers)
    return rank_and_dedup(papers, max_papers)
