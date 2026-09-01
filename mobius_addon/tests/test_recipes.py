"""recipe 库存在性 + 结构 + 可被检索索引覆盖的测试。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import search.indexer as idx  # noqa: E402
import search.query as qry  # noqa: E402

RECIPES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "recipes")
REQUIRED_SECTIONS = ("## 症状", "## 根因", "## 修复", "## 验证命令", "## 防复发")


def _recipe_files():
    return [
        os.path.join(RECIPES_DIR, f)
        for f in os.listdir(RECIPES_DIR)
        if f.endswith(".md")
    ]


def test_recipes_present_and_structured():
    files = _recipe_files()
    assert len(files) >= 8, f"recipe 数量不足: {len(files)} (需 >=8)"
    for fp in files:
        text = open(fp, encoding="utf-8").read()
        for sec in REQUIRED_SECTIONS:
            assert sec in text, f"{os.path.basename(fp)} 缺少章节 {sec}"


def test_recipes_indexable(tmp_path):
    # 把真实 recipes 目录当作唯一索引源, 验证可被检索命中
    saved = idx._system_sources
    idx._system_sources = lambda: {"recipes": RECIPES_DIR}
    db = str(tmp_path / "index.db")
    try:
        n = idx.build_index(db, incremental=False)
        assert n == len(_recipe_files())
        res = qry.query(db, ["GBK", "编码"])
        assert res, "GBK 相关 recipe 应可被检索命中"
    finally:
        idx._system_sources = saved
