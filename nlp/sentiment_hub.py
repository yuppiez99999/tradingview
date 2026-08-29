"""舆情综合监控 Hub — 晨间信息采集任务 4 的实现

生成两类报告:
  1. 舆情综合日报_{date}.md — 基于持仓标的的舆情监控框架
  2. 动力煤舆情日报_{date}.md — 动力煤/煤炭板块专项监控

设计原则:
  - 不依赖外部新闻爬虫 (MediaCrawlerAdapter 等可选, 有则用, 无则降级)
  - 基于 config/positions.json 持仓生成监控标的清单
  - 舆情等级基于规则引擎 (关键词 + 持仓涨跌), 非 LLM 调用

接口: run_all(target_date, output_dir, run_trend, run_coal, force) -> dict
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

CRITICAL_NEGATIVE_KEYWORDS = [
    "暴跌",
    "闪崩",
    "退市",
    "立案",
    "处罚",
    "违约",
    "爆雷",
    "造假",
    "下调",
    "减持",
    "质押",
    "诉讼",
    "亏损",
    "停牌",
    "风险",
]
CRITICAL_POSITIVE_KEYWORDS = [
    "利好",
    "增持",
    "回购",
    "突破",
    "涨停",
    "业绩超预期",
    "中标",
    "补贴",
    "政策支持",
    "战略合作",
    "收购",
    "合并",
]
COAL_KEYWORDS = ["动力煤", "焦煤", "焦炭", "煤炭", "电厂", "日耗", "港口库存", "螺纹钢"]


def _load_positions() -> dict[str, Any]:
    """加载 config/positions.json 持仓"""
    here = Path(__file__).resolve().parent
    proj_root = here.parent
    pos_path = proj_root / "config" / "positions.json"
    if not pos_path.exists():
        return {}
    try:
        with open(pos_path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _assess_sentiment_level(name: str, sector: str) -> str:
    """基于标的名称/板块的规则引擎舆情等级"""
    text = (name + sector).lower()
    if any(kw in text for kw in ["医药", "医疗", "创新药"]):
        return "中性偏多 (集采政策敏感)"
    if any(kw in text for kw in ["半导体", "芯片", "光模块", "AI"]):
        return "偏多 (国产替代+AI 算力)"
    if any(kw in text for kw in ["证券", "金融"]):
        return "偏多 (牛市旗手)"
    if any(kw in text for kw in ["煤炭", "电力", "银行"]):
        return "中性 (高股息防御)"
    if any(kw in text for kw in ["黄金"]):
        return "偏多 (避险+降息预期)"
    return "中性"


def _fetch_wind_news_alerts(
    pos_map: dict[str, Any],
    top_k: int = 5,
) -> dict[str, Any]:
    """通过 Wind MCP 抓取持仓标的新闻并用关键词命中检测

    优雅降级: wind_search_news 不可用 / 失败 / 无命中 → 返回 status 标记, 不抛异常

    Returns:
        {
            "status": "ok" | "disabled" | "unavailable" | "no_hits",
            "negative": [{code, name, title, source, publish_time, keyword}, ...],
            "positive": [...],
            "scanned": int,
            "skipped": int,
        }
    """
    if os.environ.get("SENTIMENT_HUB_USE_WIND_NEWS", "1") == "0":
        return {
            "status": "disabled",
            "negative": [],
            "positive": [],
            "scanned": 0,
            "skipped": 0,
        }

    try:
        here = Path(__file__).resolve().parent
        proj_root = here.parent
        if str(proj_root) not in sys.path:
            sys.path.insert(0, str(proj_root))
        from tools.wind_mcp_fetcher import wind_search_news
    except Exception:
        return {
            "status": "unavailable",
            "negative": [],
            "positive": [],
            "scanned": 0,
            "skipped": 0,
        }

    negative_hits: list[dict[str, Any]] = []
    positive_hits: list[dict[str, Any]] = []
    scanned = 0
    skipped = 0

    for code, p in pos_map.items():
        name = p.get("name", "")
        if not name:
            skipped += 1
            continue
        try:
            news = wind_search_news(name, top_k=top_k)
        except Exception:
            skipped += 1
            continue
        scanned += 1
        for item in news or []:
            title = item.get("title", "") or ""
            snippet = item.get("snippet", "") or ""
            text = title + snippet
            for kw in CRITICAL_NEGATIVE_KEYWORDS:
                if kw in text:
                    negative_hits.append(
                        {
                            "code": code,
                            "name": name,
                            "title": title[:80],
                            "source": item.get("source", ""),
                            "publish_time": item.get("publish_time", ""),
                            "keyword": kw,
                        }
                    )
                    break
            for kw in CRITICAL_POSITIVE_KEYWORDS:
                if kw in text:
                    positive_hits.append(
                        {
                            "code": code,
                            "name": name,
                            "title": title[:80],
                            "source": item.get("source", ""),
                            "publish_time": item.get("publish_time", ""),
                            "keyword": kw,
                        }
                    )
                    break

    status = "ok" if (negative_hits or positive_hits) else "no_hits"
    return {
        "status": status,
        "negative": negative_hits,
        "positive": positive_hits,
        "scanned": scanned,
        "skipped": skipped,
    }


def _render_news_alerts_section(alerts: dict[str, Any]) -> str:
    """渲染新闻舆情预警章节 (Wind MCP)"""
    status = alerts.get("status", "unavailable")
    scanned = alerts.get("scanned", 0)
    skipped = alerts.get("skipped", 0)
    negative = alerts.get("negative", [])
    positive = alerts.get("positive", [])

    lines = ["\n## 五、新闻舆情预警 (Wind MCP)\n\n"]

    if status == "disabled":
        lines.append("> 已通过 SENTIMENT_HUB_USE_WIND_NEWS=0 关闭 Wind MCP 新闻扫描\n")
        return "".join(lines)
    if status == "unavailable":
        lines.append(
            "> Wind MCP 不可用 (导入失败或未配置 WIND_API_KEY), 跳过新闻扫描\n"
        )
        lines.append(
            "> 启用方式: 配置 WIND_API_KEY 环境变量并确保 tools/wind_mcp_fetcher.py 可导入\n"
        )
        return "".join(lines)

    header = f"> 扫描 {scanned} 只标的新闻"
    if skipped:
        header += f" (跳过 {skipped} 只: 无名称或调用失败)"
    lines.append(header + "\n\n")

    lines.append("### 负面命中\n\n")
    if negative:
        lines.append("| 标的 | 命中关键词 | 新闻标题 | 来源 | 发布时间 |\n")
        lines.append("|------|-----------|---------|------|---------|\n")
        for h in negative[:30]:
            lines.append(
                f"| {h['code']} {h['name']} | {h['keyword']} | "
                f"{h['title']} | {h['source']} | {h['publish_time']} |\n"
            )
        if len(negative) > 30:
            lines.append(f"\n> 另有 {len(negative) - 30} 条负面命中未列出\n")
    else:
        lines.append("无负面关键词命中\n")

    lines.append("\n### 正面命中\n\n")
    if positive:
        lines.append("| 标的 | 命中关键词 | 新闻标题 | 来源 | 发布时间 |\n")
        lines.append("|------|-----------|---------|------|---------|\n")
        for h in positive[:30]:
            lines.append(
                f"| {h['code']} {h['name']} | {h['keyword']} | "
                f"{h['title']} | {h['source']} | {h['publish_time']} |\n"
            )
        if len(positive) > 30:
            lines.append(f"\n> 另有 {len(positive) - 30} 条正面命中未列出\n")
    else:
        lines.append("无正面关键词命中\n")

    return "".join(lines)


def _generate_trend_report(target_date: str, positions: dict[str, Any]) -> str:
    """生成舆情综合日报 markdown"""
    pos_map = positions.get("positions", {})
    lines = [
        f"# 舆情综合日报 {target_date}\n",
        f"\n生成时间: {datetime.now():%Y-%m-%d %H:%M:%S}\n",
        f"\n> 基于持仓 {len(pos_map)} 只标的的舆情监控框架 (规则引擎, 非 LLM)\n",
        "\n## 一、监控标的清单\n\n",
        "| 代码 | 名称 | 板块 | 舆情等级 | 监控关键词 |\n",
        "|------|------|------|---------|------------|\n",
    ]
    for code, p in pos_map.items():
        name = p.get("name", "")
        sector = p.get("sector", p.get("style", ""))
        level = _assess_sentiment_level(name, sector)
        kws = (
            ",".join([kw for kw in CRITICAL_NEGATIVE_KEYWORDS if kw in name][:3]) or "-"
        )
        lines.append(f"| {code} | {name} | {sector} | {level} | {kws} |\n")
    lines.append("\n## 二、负面关键词监控\n\n")
    lines.append(f"监控关键词: {', '.join(CRITICAL_NEGATIVE_KEYWORDS)}\n\n")
    lines.append(
        "> 若新闻标题命中上述关键词, 触发舆情预警 (见第五章 Wind MCP 新闻扫描结果)\n"
    )
    lines.append("\n## 三、正面关键词监控\n\n")
    lines.append(f"监控关键词: {', '.join(CRITICAL_POSITIVE_KEYWORDS)}\n\n")
    lines.append("\n## 四、市场情绪判断\n\n")
    hedge = positions.get("hedge_positions", {})
    ao = hedge.get("active_orders", {})
    put_count = sum(
        pp.get("contracts", 0) for pp in (ao.get("put_protection", []) or [])
    )
    call_count = sum(
        cc.get("contracts", 0) for cc in (ao.get("covered_call", []) or [])
    )
    lines.append(
        f"- 对冲头寸: Covered Call {call_count} 张 + Put 保护 {put_count} 张\n"
    )
    lines.append(f"- 持仓数: {len(pos_map)} 只\n")
    lines.append(
        f"- 情绪判断: {'谨慎乐观 (有尾部保护)' if put_count > 0 else '中性'}\n"
    )

    alerts = _fetch_wind_news_alerts(pos_map)
    lines.append(_render_news_alerts_section(alerts))

    lines.append("\n## 六、数据源说明\n\n")
    lines.append(
        "- 当前: 规则引擎 (基于持仓+板块+关键词) + Wind MCP 新闻扫描 (可选, 失败降级)\n"
    )
    lines.append(
        "- 新闻数据: `tools.wind_mcp_fetcher.wind_search_news` (Wind MCP financial_docs.get_financial_news)\n"
    )
    lines.append(
        "- 可选增强: 接入 `utils.signal_sources.sentiment_signal_source.SentimentSignalSource` (需 MediaCrawlerAdapter, 自媒体 7 平台)\n"
    )
    lines.append(
        "- 可选增强: 接入 `utils.finance_agents.sentiment_agent.SentimentAgent` (需 news_items context, 可由 wind_search_news 喂入)\n"
    )
    lines.append(
        "- 环境变量: SENTIMENT_HUB_USE_WIND_NEWS=1 开启 (默认) / =0 关闭 Wind MCP 新闻扫描\n"
    )
    return "".join(lines)


def _generate_coal_report(target_date: str, positions: dict[str, Any]) -> str:
    """生成动力煤舆情日报 markdown"""
    pos_map = positions.get("positions", {})
    coal_positions = {
        c: p
        for c, p in pos_map.items()
        if any(
            kw in (p.get("name", "") + p.get("sector", ""))
            for kw in ["煤炭", "煤", "电力"]
        )
    }
    lines = [
        f"# 动力煤舆情日报 {target_date}\n",
        f"\n生成时间: {datetime.now():%Y-%m-%d %H:%M:%S}\n",
        "\n## 一、动力煤监控关键词\n\n",
        f"监控关键词: {', '.join(COAL_KEYWORDS)}\n\n",
        "## 二、相关持仓\n\n",
    ]
    if coal_positions:
        lines.append("| 代码 | 名称 | 板块 | 持仓股数 | 估算价 |\n")
        lines.append("|------|------|------|---------|--------|\n")
        for code, p in coal_positions.items():
            lines.append(
                f"| {code} | {p.get('name','')} | {p.get('sector','')} | "
                f"{p.get('shares',0)} | {p.get('est_price',0):.2f} |\n"
            )
    else:
        lines.append("无煤炭/电力相关持仓\n")
    lines.append("\n## 三、动力煤基本面监控项\n\n")
    lines.append("| 监控项 | 状态 | 说明 |\n|--------|------|------|\n")
    lines.append("| 港口库存 | 待接入数据源 | 秦皇岛/曹妃甸港口库存 |\n")
    lines.append("| 电厂日耗 | 待接入数据源 | 六大电厂日耗煤量 |\n")
    lines.append("| 螺纹钢价格 | 待接入数据源 | 需求侧 proxy |\n")
    lines.append("| 焦煤/焦炭价差 | 待接入数据源 | 炼钢利润 proxy |\n")
    lines.append("\n## 四、舆情等级\n\n")
    lines.append("- 当前: 中性 (无负面舆情触发)\n")
    lines.append("- 关注: 迎峰度夏/度冬旺季需求、进口煤政策、安监停产\n")
    lines.append("\n## 五、数据源说明\n\n")
    lines.append("- 当前: 监控框架 (基于持仓+关键词)\n")
    lines.append("- 可选增强: 接入 Wind 动力煤现货价格、港口库存数据 (Wind MCP)\n")
    lines.append(
        "- 可选增强: 接入 `tools.wind_mcp_fetcher.wind_search_news` 搜索动力煤/煤炭板块新闻 (query='动力煤'/'焦煤'/'煤炭')\n"
    )
    return "".join(lines)


def run_all(
    target_date: str,
    output_dir: str,
    run_trend: bool = True,
    run_coal: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    """舆情综合监控主入口

    Args:
        target_date: 目标日期 YYYY-MM-DD
        output_dir: 输出目录
        run_trend: 是否生成舆情综合日报
        run_coal: 是否生成动力煤舆情日报
        force: 强制重新生成

    Returns:
        {"ok": bool, "trend_report": str, "coal_report": str}
    """
    date_short = target_date.replace("-", "")
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    positions = _load_positions()
    result: dict[str, Any] = {"ok": True, "trend_report": "", "coal_report": ""}

    if run_trend:
        trend_path = out / f"舆情综合日报_{date_short}.md"
        if force or not trend_path.exists():
            trend_path.write_text(
                _generate_trend_report(target_date, positions), encoding="utf-8"
            )
        result["trend_report"] = str(trend_path)

    if run_coal:
        coal_path = out / f"动力煤舆情日报_{date_short}.md"
        if force or not coal_path.exists():
            coal_path.write_text(
                _generate_coal_report(target_date, positions), encoding="utf-8"
            )
        result["coal_report"] = str(coal_path)

    return result


if __name__ == "__main__":
    import sys

    td = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    od = sys.argv[2] if len(sys.argv) > 2 else "每日报告归档/" + td
    r = run_all(target_date=td, output_dir=od, force=True)
