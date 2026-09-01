"""检索索引/查询测试：用临时源目录构建索引并查询。"""

import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import search.indexer as idx  # noqa: E402
import search.query as qry  # noqa: E402


def test_index_and_query(tmp_path):
    recipes = tmp_path / "recipes"
    recipes.mkdir()
    (recipes / "a.md").write_text(
        "执行链断链审计法：订单只生成不成交", encoding="utf-8"
    )
    (recipes / "b.md").write_text("GBK 编码崩溃与货币符号", encoding="utf-8")

    saved = idx._system_sources
    idx._system_sources = lambda: {"recipes": str(recipes)}
    try:
        db = str(tmp_path / "index.db")
        n = idx.build_index(db)
        assert n == 2
        res = qry.query(db, ["执行链", "断链"])
        assert len(res) == 1
        assert "执行链" in res[0]["context"]
    finally:
        idx._system_sources = saved


def test_query_no_match(tmp_path):
    recipes = tmp_path / "recipes"
    recipes.mkdir()
    (recipes / "a.md").write_text("执行链断链审计", encoding="utf-8")
    saved = idx._system_sources
    idx._system_sources = lambda: {"recipes": str(recipes)}
    try:
        db = str(tmp_path / "index.db")
        idx.build_index(db)
        assert qry.query(db, ["不存在的关键词xyz"]) == []
    finally:
        idx._system_sources = saved


def test_incremental_build(tmp_path):
    recipes = tmp_path / "recipes"
    recipes.mkdir()
    (recipes / "a.md").write_text("执行链断链审计法", encoding="utf-8")
    (recipes / "b.md").write_text("GBK 编码崩溃", encoding="utf-8")
    saved = idx._system_sources
    idx._system_sources = lambda: {"recipes": str(recipes)}
    db = str(tmp_path / "index.db")
    try:
        n1 = idx.build_index(db)
        assert n1 == 2
        # 未变化 -> 增量跳过, 0 个文件重索引
        n2 = idx.build_index(db)
        assert n2 == 0
        # 仅 b 变化 -> 1 个文件重索引
        (recipes / "b.md").write_text("GBK 编码与货币符号修复", encoding="utf-8")
        n3 = idx.build_index(db)
        assert n3 == 1
        # 查询仍可命中两篇
        assert len(qry.query(db, ["GBK"])) == 1
        assert len(qry.query(db, ["执行链"])) == 1
    finally:
        idx._system_sources = saved


def test_query_since_filter(tmp_path):
    recipes = tmp_path / "recipes"
    recipes.mkdir()
    old = recipes / "old.md"
    old.write_text("旧的执行链断链审计", encoding="utf-8")
    new = recipes / "new.md"
    new.write_text("新的执行链断链审计", encoding="utf-8")
    past = time.mktime(datetime(2020, 1, 1).timetuple())
    os.utime(old, (past, past))
    saved = idx._system_sources
    idx._system_sources = lambda: {"recipes": str(recipes)}
    db = str(tmp_path / "index.db")
    try:
        idx.build_index(db)
        assert len(qry.query(db, ["执行链"])) == 2
        res = qry.query(db, ["执行链"], since="2024-01-01")
        assert len(res) == 1
        assert "new" in res[0]["path"]
    finally:
        idx._system_sources = saved
