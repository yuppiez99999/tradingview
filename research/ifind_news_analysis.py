"""
iFinD 资讯研判命令行入口
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from utils.ifind_news_analyzer import IFinDNewsAnalyzer, StockInsight


def _serialize_insight(insight: StockInsight) -> dict:
    return {
        "symbol": insight.symbol,
        "name": insight.name,
        "direction": insight.direction,
        "confidence": round(insight.confidence, 4),
        "news_count": insight.news_count,
        "reasons": insight.reasons,
        "updated_at": insight.updated_at,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="iFinD 资讯读取并做标的研判")
    parser.add_argument("--symbol", help="单个标的，如 300308")
    parser.add_argument("--name", help="标的名称，如 中际旭创")
    parser.add_argument("--symbols", help="多个标的，逗号分隔")
    parser.add_argument("--size", type=int, default=5, help="单次查询条数")
    parser.add_argument("--days", type=int, default=3, help="回溯天数")
    parser.add_argument("--trending", help="热点关键词")
    parser.add_argument("--industry", help="热点行业")
    args = parser.parse_args(argv)

    analyzer = IFinDNewsAnalyzer()
    if not analyzer.available():
        print("iFinD 模块不可用，请检查 skills/ifind-finance-data/call.py 与 mcp_config.json")
        return 2

    output: dict = {"ok": True, "updated_at": datetime.now().isoformat(), "insights": []}

    if args.trending:
        items = analyzer.search_trending(args.trending, industry_name=args.industry or "", size=args.size)
        output["trending"] = [
            {
                "title": item.title,
                "snippet": item.snippet,
                "source": item.source,
                "publish_time": item.publish_time,
            }
            for item in items
        ]
    elif args.symbol or args.symbols:
        symbols = [s.strip() for s in (args.symbols or args.symbol or "").split(",") if s.strip()]
        if not symbols and args.symbol:
            symbols = [args.symbol.strip()]
        insights = analyzer.batch_analyze(symbols, size=args.size, days=args.days)
        output["insights"] = [_serialize_insight(i) for i in insights]
    else:
        parser.print_help()
        return 1

    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
