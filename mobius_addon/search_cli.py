"""检索入口：构建/查询本地多源文档索引。

用法:
  python mobius_addon/search_cli.py --kw 执行链 断链 --rebuild
  python mobius_addon/search_cli.py --kw GBK 编码
  python mobius_addon/search_cli.py --kw 对冲 --since 2026-08-01 --source cairn
"""

import argparse
import os

from _common import ensure_env, get_logger

ensure_env()


def main() -> None:
    parser = argparse.ArgumentParser(description="Mobius 外挂: 追溯检索")
    parser.add_argument("--kw", required=True, nargs="+", help="关键词(AND 关系)")
    parser.add_argument(
        "--since",
        default=None,
        help="时间过滤下限(YYYY-MM-DD 或 YYYY-MM-DD HH:MM, 仅含该时间之后的文档)",
    )
    parser.add_argument("--source", default=None, help="来源路径过滤(子串匹配)")
    parser.add_argument("--rebuild", action="store_true", help="强制全量重建索引")
    args = parser.parse_args()

    root = os.path.dirname(os.path.abspath(__file__))
    db = os.path.join(root, "index.db")
    if args.rebuild or not os.path.exists(db):
        from search.indexer import build_index  # noqa: WPS433

        build_index(db, incremental=False)

    from search.query import query  # noqa: WPS433

    res = query(db, args.kw, source=args.source, since=args.since)
    get_logger().info("命中 %d 个文件", len(res))
    for r in res:
        print(f"[{r['mtime']:.0f}] {r['path']}")  # noqa: T201
        print(r["context"])  # noqa: T201
        print("-" * 60)  # noqa: T201


if __name__ == "__main__":
    main()
