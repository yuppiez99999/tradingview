"""检索查询：关键词 AND 子串匹配 + 来源/时间过滤 + 上下文片段输出（只读）。"""

from __future__ import annotations

import sqlite3
from datetime import datetime

_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")


def _parse_since(since: str) -> float | None:
    since = (since or "").strip()
    if not since:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(since, fmt).timestamp()
        except ValueError:
            continue
    return None


def query(
    db_path: str,
    keywords: list[str],
    source: str | None = None,
    since: str | None = None,
) -> list[dict]:
    conn = sqlite3.connect(db_path)
    terms = [k.lower() for k in keywords if k]
    if not terms:
        conn.close()
        return []
    rows = conn.execute("SELECT id, path, mtime, content FROM sources").fetchall()
    conn.close()

    since_ts = _parse_since(since)
    results = []
    for _sid, path, mtime, content in rows:
        if source and source not in path:
            continue
        if since_ts is not None and (mtime or 0) < since_ts:
            continue
        low = (content or "").lower()
        if all(t in low for t in terms):
            ctx = _context(content, keywords)
            if ctx:
                results.append({"path": path, "mtime": mtime, "context": ctx})
    return results


def _context(content: str, keywords: list[str]) -> str:
    lines = (content or "").splitlines()
    hits = []
    for i, line in enumerate(lines):
        low = line.lower()
        if any(k.lower() in low for k in keywords):
            start = max(0, i - 2)
            end = min(len(lines), i + 3)
            hits.append("\n".join(lines[start:end]).strip())
            if len(hits) >= 5:
                break
    return "\n---\n".join(hits)
