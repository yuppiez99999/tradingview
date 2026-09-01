"""
iFinD 自动研判 - 读取 portfolio.yaml 全持仓并批量生成标的研判报告
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any

# 确保能导入 utils 模块
project_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import yaml  # noqa: E402

from utils.ifind_news_analyzer import IFinDNewsAnalyzer, StockInsight  # noqa: E402


def _load_portfolio_symbols(portfolio_path: str) -> list[dict[str, Any]]:
    if not os.path.exists(portfolio_path):
        raise FileNotFoundError(f"portfolio.yaml 不存在: {portfolio_path}")
    with open(portfolio_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    # 支持两种格式：positions (对象) 或 assets (数组)
    positions = data.get("positions") or {}
    assets = data.get("assets") or []

    items: list[dict[str, Any]] = []

    # 优先使用 assets 数组（新格式）
    if isinstance(assets, list) and len(assets) > 0:
        for asset in assets:
            code = asset.get("code", "")
            if code and code != "CASH":  # 跳过现金项
                items.append(
                    {
                        "code": str(code),
                        "name": str(asset.get("name", code)),
                        "sector": str(asset.get("category", asset.get("style", ""))),
                        "target_weight": asset.get("weight"),
                        "asset_type": str(asset.get("category", "")),
                    }
                )

    # 如果没有从 assets 获取到数据，尝试 positions（旧格式）
    if not items and isinstance(positions, dict) and len(positions) > 0:
        for code, pos in positions.items():
            items.append(
                {
                    "code": str(code),
                    "name": str(pos.get("code", code)),  # 注意：这里可能需要调整
                    "sector": str(pos.get("sector", pos.get("category", ""))),
                    "target_weight": pos.get("weight", pos.get("target_weight")),
                    "asset_type": str(pos.get("asset_type", "")),
                }
            )

    if not items:
        if isinstance(positions, dict) and not positions:
            print("警告：portfolio.yaml 中 positions 为空")
        else:
            print("警告：未从 portfolio.yaml 中读取到有效持仓标的")

    return items


def _calc_technical_alpha(code: str) -> float | None:
    """
    计算 GTJA191 Alpha144 映射后的 technical_alpha 得分。
    若因子库或历史数据不可用，则返回 None。
    """
    try:
        from utils.data_provider import get_historical_data
        from utils.gtja191_factors import GTJA191Factors

        df = get_historical_data(code, period="6m")
        if (
            df is None
            or df.empty
            or "close" not in df.columns
            or "amount" not in df.columns
        ):
            return None

        factors = GTJA191Factors(lookback=20)
        value = factors.alpha144(df)
        if value is None:
            return None

        score = max(-1.0, min(1.0, 1.0 - float(value) * 1e8))
        return round(float(score), 4)
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
        TimeoutError,
        ConnectionError,
    ):
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        return None


def _build_markdown_report(
    insights: list[StockInsight], items: list[dict[str, Any]], meta: dict[str, Any]
) -> str:
    lines = [
        "# iFinD 自动标的研判报告",
        "",
        f"- 生成时间：{meta.get('generated_at', datetime.now().isoformat())}",
        f"- 标的数量：{len(insights)}",
        "- 数据源：iFinD 新闻/公告语义检索 + GTJA191 Alpha144 技术因子",
        "- 研判逻辑：关键词多空信号 + 置信度 + technical_alpha",
        "",
        "## 标的概览",
        "",
        "| 标的 | 名称 | 方向 | 置信度 | 资讯数 | technical_alpha | 研判结论 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    direction_emoji = {"positive": "📈", "negative": "📉", "neutral": "➡️"}
    for item, insight in zip(items, insights, strict=True):
        emoji = direction_emoji.get(insight.direction, "➡️")
        technical_alpha = _calc_technical_alpha(item["code"])
        alpha_str = f"{technical_alpha:.4f}" if technical_alpha is not None else "N/A"
        lines.append(
            f"| {item['code']} | {item['name']} | {emoji} {insight.direction} | {insight.confidence:.2f} | {insight.news_count} | {alpha_str} | {'；'.join(insight.reasons[:2])} |"  # noqa: E501
        )
    lines.extend(
        [
            "",
            "## 逐标的详情",
            "",
        ]
    )
    for item, insight in zip(items, insights, strict=True):
        technical_alpha = _calc_technical_alpha(item["code"])
        alpha_str = f"{technical_alpha:.4f}" if technical_alpha is not None else "N/A"
        lines.extend(
            [
                f"### {item['name']} ({item['code']})",
                "",
                f"- 板块：{item['sector'] or '未知'}",
                f"- 方向：{insight.direction}",
                f"- 置信度：{insight.confidence:.2f}",
                f"- 资讯数：{insight.news_count}",
                f"- technical_alpha：{alpha_str}",
                f"- 更新时间：{insight.updated_at}",
                "",
                "**研判理由：**",
            ]
        )
        for reason in insight.reasons[:8]:
            lines.append(f"- {reason}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="iFinD 自动研判：基于 portfolio.yaml 批量研判全持仓"
    )
    parser.add_argument(
        "--portfolio",
        default=os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "11_量化策略",
            "config",
            "portfolio.yaml",
        ),
        help="portfolio.yaml 路径",
    )
    parser.add_argument("--size", type=int, default=4, help="单标的查询条数")
    parser.add_argument("--days", type=int, default=3, help="回溯天数")
    parser.add_argument("--output", default="", help="报告输出路径，默认自动生成")
    args = parser.parse_args(argv)

    items = _load_portfolio_symbols(args.portfolio)
    if not items:
        print("未读取到持仓标的，请检查 portfolio.yaml")
        return 2

    analyzer = IFinDNewsAnalyzer()
    if not analyzer.available():
        print(
            "iFinD 模块不可用，请检查 skills/ifind-finance-data/call.py 与 mcp_config.json"
        )
        return 2

    insights: list[StockInsight] = []
    for item in items:
        try:
            insights.append(
                analyzer.analyze_symbol(
                    item["code"], name=item["name"], size=args.size, days=args.days
                )
            )
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass

    base_dir = os.path.dirname(os.path.abspath(__file__))
    today_str = datetime.now().strftime("%Y-%m-%d")
    default_archive_dir = os.path.join(base_dir, "每日报告归档", today_str)
    os.makedirs(default_archive_dir, exist_ok=True)
    default_output = os.path.join(
        default_archive_dir,
        f"iFinD自动标的研判报告_{datetime.now().strftime('%Y%m%d')}.md",
    )
    output_path = args.output or default_output
    meta = {
        "generated_at": datetime.now().isoformat(),
        "portfolio": args.portfolio,
        "size": args.size,
        "days": args.days,
    }
    report = _build_markdown_report(insights, items, meta)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)

    print(
        json.dumps(
            {
                "ok": True,
                "symbols": len(items),
                "analyzed": len(insights),
                "report": output_path,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
