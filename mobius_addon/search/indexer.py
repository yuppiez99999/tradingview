"""追溯检索：只读扫描多源文档，建 SQLite 全文索引（按 mtime 增量）。

中文检索采用子串匹配（在存储的全文内容上做 AND 子串判断），
避免按词切分导致连续中文被合并成单一 token 而查不到。
"""

from __future__ import annotations

import os
import sqlite3

import _common as _c

CONTENT_LIMIT = 200_000  # 单文件索引内容上限（字符），防止超大文件撑爆内存


def _system_sources() -> dict[str, str]:
    root = _c.get_sys_root()
    return {
        "logs": os.path.join(root, "logs"),
        "memory": os.path.join(root, "..", ".codebuddy", "memory"),
        "research": os.path.join(root, "research"),
        "cairn": os.path.join(root, "cairn"),
        "second_brain": os.path.join(root, "second-brain"),
        "recipes": os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "recipes"
        ),
    }


def _iter_files(base: str) -> list[str]:
    out = []
    for dirpath, _dirs, files in os.walk(base):
        for fn in files:
            if fn.lower().endswith((".md", ".txt", ".jsonl", ".log", ".json")):
                out.append(os.path.join(dirpath, fn))
    return out


def build_index(db_path: str, incremental: bool = True) -> int:
    """构建检索索引，返回本次新增/更新（+删除）的文件数。

    incremental=True 时仅对 mtime 变化或新增的文件重索引，并删除已不
    存在的文件记录；首次运行或 --rebuild 时设 incremental=False 全量重建。
    """
    _c.ensure_env()
    sources = _system_sources()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sources("
        "id INTEGER PRIMARY KEY, path TEXT UNIQUE, mtime REAL, content TEXT)"
    )
    conn.commit()

    # 1) 收集待索引文件（绝对路径 -> mtime）
    file_map: dict[str, float] = {}
    for _name, base in sources.items():
        if not os.path.isdir(base):
            continue
        for fp in _iter_files(base):
            try:
                file_map[os.path.abspath(fp)] = os.path.getmtime(fp)
            except OSError:
                continue

    # 2) 删除已不存在的文件记录（增量模式下才需要清理）
    changed = 0
    if incremental:
        existing = [r[0] for r in conn.execute("SELECT path FROM sources").fetchall()]
        for ep in existing:
            if ep not in file_map:
                conn.execute("DELETE FROM sources WHERE path=?", (ep,))
                changed += 1
    else:
        conn.execute("DELETE FROM sources")

    # 3) 新增 / mtime 变化 → 重新写入内容
    for fp, mtime in file_map.items():
        row = conn.execute("SELECT mtime FROM sources WHERE path=?", (fp,)).fetchone()
        if row is not None and abs(row[0] - mtime) < 1e-6:
            continue  # 未变化，跳过
        try:
            with open(fp, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except Exception:  # noqa: BLE001
            continue
        conn.execute(
            "INSERT INTO sources(path, mtime, content) VALUES(?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET mtime=excluded.mtime, "
            "content=excluded.content",
            (fp, mtime, text[:CONTENT_LIMIT]),
        )
        changed += 1

    conn.commit()
    conn.close()
    return changed
