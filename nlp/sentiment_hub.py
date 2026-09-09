"""舆情综合监控 Hub — 晨间信息采集任务 4 的实现

生成两类报告:
  1. 舆情综合日报_{date}.md — 基于持仓标的的舆情监控 (Ling 主判 + 规则引擎兜底)
  2. 动力煤舆情日报_{date}.md — 动力煤/煤炭板块专项监控 (可选 Ling 判断)

设计原则 (2026-09-07 升级: 两套舆情链路统一走 Ling):
  - 不依赖外部新闻爬虫 (MediaCrawlerAdapter 等可选, 有则用, 无则降级)
  - 基于 config/positions.json 持仓生成监控标的清单
  - 新闻方向判断: Ling-3.0-flash (ModelScope, nlp/ling_judge.py) 主判;
    无 token / API 失败 / 被禁用时自动回退规则引擎 (关键词命中) —— 观测路径 fail-open
  - 报告结构兼容下游消费端 tools/apply_llm_decisions_to_plan.py:
    "### 负面命中/正面命中" 表格行计数 + 情绪判断行的"谨慎乐观/中性"等标签

接口: run_all(target_date, output_dir, run_trend, run_coal, force) -> dict
"""

from __future__ import annotations


import os
import sys
import time
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

# Ling 判断客户端 (无 token 时 ling_enabled()=False, 全链路自动回退规则引擎)
try:
    from nlp.ling_judge import judge_news, ling_enabled, ling_status
except Exception:  # 被直接 python nlp/sentiment_hub.py 运行时兜底补 sys.path
    _pkg_root = Path(__file__).resolve().parent.parent
    if str(_pkg_root) not in sys.path:
        sys.path.insert(0, str(_pkg_root))
    from nlp.ling_judge import judge_news, ling_enabled, ling_status


def _load_positions() -> dict[str, Any]:
    """加载 config/positions.json 持仓"""
    # P2 修复 (2026-09-09): 委托 positions_loader 统一入口
    from utils.positions_loader import load_positions

    return load_positions()


def _assess_sentiment_level(name: str, sector: str) -> str:
    """基于标的名称/板块的规则引擎舆情等级 (静态监控框架用)"""
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
    """通过 Wind MCP 抓取持仓标的新闻, 关键词命中检测 + 保留全量条目供 Ling 判断

    优雅降级: wind_search_news 不可用 / 失败 / 无命中 → 返回 status 标记, 不抛异常

    Returns:
        {
            "status": "ok" | "disabled" | "unavailable" | "no_hits",
            "negative": [{code, name, title, source, publish_time, keyword}, ...],
            "positive": [...],
            "items": [  # 每条有标题的新闻 (供 Ling 逐条判断)
                {code, name, title, source, publish_time, snippet,
                 kw_neg, kw_pos}, ...
            ],
            "scanned": int,
            "skipped": int,
        }
    """
    if os.environ.get("SENTIMENT_HUB_USE_WIND_NEWS", "1") == "0":
        return {
            "status": "disabled",
            "negative": [],
            "positive": [],
            "items": [],
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
            "items": [],
            "scanned": 0,
            "skipped": 0,
        }

    negative_hits: list[dict[str, Any]] = []
    positive_hits: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
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
            source = item.get("source", "") or ""
            pub = item.get("publish_time", "") or ""
            text = title + snippet
            kw_neg = None
            kw_pos = None
            for kw in CRITICAL_NEGATIVE_KEYWORDS:
                if kw in text:
                    kw_neg = kw
                    break
            for kw in CRITICAL_POSITIVE_KEYWORDS:
                if kw in text:
                    kw_pos = kw
                    break
            if kw_neg:
                negative_hits.append(
                    {
                        "code": code,
                        "name": name,
                        "title": title[:80],
                        "source": source,
                        "publish_time": pub,
                        "keyword": kw_neg,
                    }
                )
            if kw_pos:
                positive_hits.append(
                    {
                        "code": code,
                        "name": name,
                        "title": title[:80],
                        "source": source,
                        "publish_time": pub,
                        "keyword": kw_pos,
                    }
                )
            if title:
                items.append(
                    {
                        "code": code,
                        "name": name,
                        "title": title[:80],
                        "source": source,
                        "publish_time": pub,
                        "snippet": snippet[:300],
                        "kw_neg": kw_neg,
                        "kw_pos": kw_pos,
                    }
                )

    status = "ok" if (negative_hits or positive_hits or items) else "no_hits"
    return {
        "status": status,
        "negative": negative_hits,
        "positive": positive_hits,
        "items": items,
        "scanned": scanned,
        "skipped": skipped,
    }


def _annotate_items_with_ling(
    items: list[dict[str, Any]], holdings_text: str
) -> dict[str, Any]:
    """对抓到的新闻逐条 Ling 判断 direction/confidence/brief (原地写 items)

    控制:
      - SENTIMENT_LING_MAX_ITEMS=45    单次最多判断条数 (默认 45)
      - SENTIMENT_LING_BUDGET_SEC=420  总时间预算 (默认 7 分钟, 超时停止)
      规则关键词命中项优先判断。

    Returns:
        {"ling_used": bool, "judged": int, "failed": int}
    """
    if not items:
        return {"ling_used": False, "judged": 0, "failed": 0}
    if not holdings_text or not ling_enabled():
        return {"ling_used": False, "judged": 0, "failed": 0}

    max_items = int(os.environ.get("SENTIMENT_LING_MAX_ITEMS", "45") or 45)
    budget = float(os.environ.get("SENTIMENT_LING_BUDGET_SEC", "420") or 420)
    start_ts = time.time()
    judged = 0
    failed = 0
    # 关键词命中项优先判断
    order = sorted(
        items,
        key=lambda it: (0 if (it.get("kw_neg") or it.get("kw_pos")) else 1),
    )
    for it in order:
        if (judged + failed) >= max_items:
            break
        if (time.time() - start_ts) > budget:
            break
        title = (it.get("title") or "").strip()
        if not title:
            continue
        res = judge_news(title, it.get("snippet") or "", holdings_text)
        if res:
            it["direction"] = res["direction"]
            it["confidence"] = res["confidence"]
            it["brief"] = res["brief"]
            it["engine"] = "ling"
            judged += 1
        else:
            failed += 1
    return {"ling_used": True, "judged": judged, "failed": failed}


def _compile_rows(
    alerts: dict[str, Any], annotate: dict[str, Any]
) -> tuple[list, list, list, list]:
    """把抓取条目编译为展示行, 分为 利空/利好/中性·混合 三组

    优先级: Ling 判定结果 > 规则关键词兜底; 无信号且未判的条目不展示
    """
    ling_used = bool(annotate.get("ling_used"))
    neg: list[dict[str, Any]] = []
    pos: list[dict[str, Any]] = []
    mid: list[dict[str, Any]] = []
    for it in alerts.get("items", []):
        row = {
            "code": it.get("code", ""),
            "name": it.get("name", ""),
            "title": it.get("title", ""),
            "source": it.get("source", ""),
            "publish_time": it.get("publish_time", ""),
        }
        d = it.get("direction")
        if d:
            # Ling 判定结果 (含中性/混合)
            row["confidence"] = it.get("confidence", "-")
            row["brief"] = it.get("brief") or "-"
            row["direction"] = d
            if d == "利空":
                neg.append(row)
            elif d == "利好":
                pos.append(row)
            else:
                if ling_used:  # 中性/混合仅提示不预警
                    mid.append(row)
        elif it.get("kw_neg"):
            row["direction"] = "利空"
            row["confidence"] = "-"
            row["brief"] = "规则命中关键词[{}]".format(it["kw_neg"])
            neg.append(row)
        elif it.get("kw_pos"):
            row["direction"] = "利好"
            row["confidence"] = "-"
            row["brief"] = "规则命中关键词[{}]".format(it["kw_pos"])
            pos.append(row)
    return neg, pos, mid, ling_used


def _render_news_alerts_section(
    alerts: dict[str, Any],
    neg: list[dict[str, Any]],
    pos: list[dict[str, Any]],
    mid: list[dict[str, Any]],
    annotate: dict[str, Any],
    scanned: int,
    skipped: int,
) -> str:
    """渲染新闻舆情预警章节 (结构兼容 apply_llm_decisions_to_plan 解析)"""
    lines = ["\n## 五、新闻舆情预警 (Wind MCP)\n\n"]
    status = alerts.get("status", "unavailable")

    if status == "disabled":
        lines.append("> 已通过 SENTIMENT_HUB_USE_WIND_NEWS=0 关闭 Wind MCP 新闻扫描\n")
        return "".join(lines)
    if status == "unavailable":
        lines.append(
            "> Wind MCP 不可用 (导入失败或未配置 WIND_API_KEY), 跳过新闻扫描\n"
        )
        return "".join(lines)

    if annotate.get("ling_used"):
        engine_note = "Ling-3.0-flash 主判 + 规则关键词兜底"
        if annotate.get("failed"):
            engine_note += f" (有 {annotate['failed']} 条调用失败回退规则)"
    else:
        engine_note = ling_status() + " -> 规则引擎关键词兜底"

    header = f"> 判断引擎: {engine_note}\n"
    header += f"> 扫描 {scanned} 只标的"
    if skipped:
        header += f" (跳过 {skipped} 只)"
    header += f", 共 {len(alerts.get('items', []))} 条新闻"
    if annotate.get("judged"):
        header += f", Ling 判定 {annotate['judged']} 条"
    header += "\n"
    lines.append(header)

    # 负面命中表 (利空)
    lines.append(f"\n### 负面命中 ({len(neg)} 条)\n\n")
    if neg:
        lines.append("| 标的 | 方向 | 置信度 | 判断理由 | 新闻标题 | 来源 | 发布时间 |\n")
        lines.append("|------|------|--------|---------|---------|------|---------|\n")
        for h in neg[:30]:
            lines.append(
                f"| {h['code']} {h['name']} | {h['direction']} | {h['confidence']} | "
                f"{h['brief']} | {h['title']} | {h['source']} | {h['publish_time']} |\n"
            )
        if len(neg) > 30:
            lines.append(f"\n> 另有 {len(neg) - 30} 条利空未列出\n")
    else:
        lines.append("无利空信号 (Ling 判定/规则兜底)\n")

    # 正面命中表 (利好)
    lines.append(f"\n### 正面命中 ({len(pos)} 条)\n\n")
    if pos:
        lines.append("| 标的 | 方向 | 置信度 | 判断理由 | 新闻标题 | 来源 | 发布时间 |\n")
        lines.append("|------|------|--------|---------|---------|------|---------|\n")
        for h in pos[:30]:
            lines.append(
                f"| {h['code']} {h['name']} | {h['direction']} | {h['confidence']} | "
                f"{h['brief']} | {h['title']} | {h['source']} | {h['publish_time']} |\n"
            )
        if len(pos) > 30:
            lines.append(f"\n> 另有 {len(pos) - 30} 条利好未列出\n")
    else:
        lines.append("无利好信号 (Ling 判定/规则兜底)\n")

    # 中性/混合提示 (放在正负面之后, 不影响下游表行计数)
    lines.append(f"\n### 中性/混合提示 ({len(mid)} 条, 不触发预警)\n\n")
    if mid:
        lines.append("| 标的 | 方向 | 置信度 | 判断理由 | 新闻标题 | 来源 | 发布时间 |\n")
        lines.append("|------|------|--------|---------|---------|------|---------|\n")
        for h in mid[:20]:
            lines.append(
                f"| {h['code']} {h['name']} | {h['direction']} | {h['confidence']} | "
                f"{h['brief']} | {h['title']} | {h['source']} | {h['publish_time']} |\n"
            )
    else:
        lines.append("无中性/混合条目\n")

    return "".join(lines)


def _mood_line(
    put_count: int, neg_n: int, pos_n: int, mid_n: int, ling_used: bool
) -> str:
    """情绪判断行文本 — 保留 '谨慎乐观'/'中性' 等下游可识别标签"""
    if ling_used:
        if neg_n >= 5:
            label = "偏谨慎"
        elif neg_n >= 3:
            label = (
                "谨慎乐观 (有尾部保护, 负面条数增加)"
                if put_count > 0
                else "中性偏谨慎"
            )
        elif put_count > 0:
            label = "谨慎乐观 (有尾部保护)"
        elif pos_n > neg_n and pos_n > 0:
            label = "中性偏多"
        else:
            label = "中性"
        stat = (
            f"Ling 判定: 利好 {pos_n} / 利空 {neg_n} / 中性·混合 {mid_n} 条"
        )
    else:
        label = "谨慎乐观 (有尾部保护)" if put_count > 0 else "中性"
        stat = "规则引擎兜底判定 (Ling 不可用)"
    return f"{label} | {stat}"


def _generate_trend_report(target_date: str, positions: dict[str, Any]) -> str:
    """生成舆情综合日报 markdown"""
    pos_map = positions.get("positions", {})
    holdings_text = ", ".join(
        [p.get("name", "") for p in pos_map.values() if p.get("name")]
    )
    lines = [
        f"# 舆情综合日报 {target_date}\n",
        f"\n生成时间: {datetime.now():%Y-%m-%d %H:%M:%S}\n",
        f"\n> 基于持仓 {len(pos_map)} 只标的的舆情监控 (Ling 主判 + 规则引擎兜底)\n",
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
        "> 新闻方向由 Ling 判断; 关键词作为规则兜底与快速预警 (见第五章扫描结果)\n"
    )
    lines.append("\n## 三、正面关键词监控\n\n")
    lines.append(f"监控关键词: {', '.join(CRITICAL_POSITIVE_KEYWORDS)}\n\n")

    # 先取新闻 + Ling 判断, 供第四/五章共用
    alerts = _fetch_wind_news_alerts(pos_map)
    annotate = _annotate_items_with_ling(alerts.get("items", []), holdings_text)
    neg_rows, pos_rows, mid_rows, ling_used = _compile_rows(alerts, annotate)

    hedge = positions.get("hedge_positions", {})
    ao = hedge.get("active_orders", {})
    put_count = sum(
        pp.get("contracts", 0) for pp in (ao.get("put_protection", []) or [])
    )
    call_count = sum(
        cc.get("contracts", 0) for cc in (ao.get("covered_call", []) or [])
    )

    lines.append("\n## 四、市场情绪判断\n\n")
    lines.append(
        f"- 对冲头寸: Covered Call {call_count} 张 + Put 保护 {put_count} 张\n"
    )
    lines.append(f"- 持仓数: {len(pos_map)} 只\n")
    lines.append(
        "- 情绪判断: "
        + _mood_line(
            put_count,
            len(neg_rows),
            len(pos_rows),
            len(mid_rows),
            bool(ling_used),
        )
        + "\n"
    )

    lines.append(
        _render_news_alerts_section(
            alerts,
            neg_rows,
            pos_rows,
            mid_rows,
            annotate,
            alerts.get("scanned", 0),
            alerts.get("skipped", 0),
        )
    )

    lines.append("\n## 六、数据源说明\n\n")
    lines.append(
        "- 情感判断: Ling-3.0-flash (ModelScope, `nlp/ling_judge.py`) 主判每条新闻"
        " direction/confidence/brief; 无 token 或调用失败自动回退规则关键词命中 (fail-open)\n"
    )
    lines.append(
        "- 规则兜底: 负面/正面关键词命中 (见二/三章), 基于持仓+板块+关键词\n"
    )
    lines.append(
        "- 新闻数据: `tools.wind_mcp_fetcher.wind_search_news` (Wind MCP financial_docs.get_financial_news)\n"
    )
    lines.append(
        "- 环境变量: SENTIMENT_LING_ENABLED=0 禁用 Ling; SENTIMENT_LING_MAX_ITEMS=45 每轮判断上限;"
        " SENTIMENT_LING_BUDGET_SEC=420 时间预算; SENTIMENT_HUB_USE_WIND_NEWS=1 新闻扫描 (默认) / =0 关闭\n"
    )
    lines.append(
        "- Ling token: MODELSCOPE_TOKEN (环境变量 / 28仓根 .env / 02_舆情监控/.env)\n"
    )
    return "".join(lines)


def _fetch_coal_news_judged(coal_positions: dict[str, Any]) -> list[dict[str, Any]]:
    """动力煤板块新闻 Ling 判断 (尽力而为, 失败返回空列表)"""
    if not ling_enabled():
        return []
    names = [p.get("name", "") for p in coal_positions.values() if p.get("name")]
    holdings_text = ", ".join(names) if names else "动力煤/煤炭板块"
    try:
        from tools.wind_mcp_fetcher import wind_search_news

        news = wind_search_news("动力煤", top_k=8)
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    for item in (news or [])[:8]:
        title = item.get("title", "") or ""
        if not title:
            continue
        res = judge_news(title, item.get("snippet", "") or "", holdings_text)
        if res and res["direction"] in ("利空", "利好"):
            rows.append(
                {
                    "direction": res["direction"],
                    "confidence": res["confidence"],
                    "brief": res["brief"],
                    "title": title[:80],
                    "source": item.get("source", ""),
                }
            )
    return rows


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

    coal_rows = _fetch_coal_news_judged(coal_positions)
    lines.append("\n## 四、舆情等级\n\n")
    if coal_rows:
        neg_rows = [r for r in coal_rows if r["direction"] == "利空"]
        pos_rows = [r for r in coal_rows if r["direction"] == "利好"]
        if neg_rows:
            label = "偏谨慎 (动力煤新闻存在利空信号)" if len(neg_rows) >= 2 else "中性偏谨慎"
        elif pos_rows:
            label = "中性偏多"
        else:
            label = "中性"
        lines.append(f"- 舆情等级: {label} (Ling-3.0-flash 判定 {len(coal_rows)} 条动力煤新闻)\n")
        for r in coal_rows[:10]:
            mark = "利空" if r["direction"] == "利空" else "利好"
            lines.append(
                f"- [{mark}] {r['brief']} | 标题: {r['title']} | 置信度 {r['confidence']}"
                f" | 来源: {r['source']}\n"
            )
    else:
        lines.append("- 舆情等级: 中性 (无负面舆情触发)\n")
    lines.append("- 关注: 迎峰度夏/度冬旺季需求、进口煤政策、安监停产\n")
    lines.append("\n## 五、数据源说明\n\n")
    lines.append(
        "- 动力煤新闻: Ling 判断 (`wind_search_news(query='动力煤')`), 失败降级为中性\n"
    )
    lines.append("- 监控框架: 基于持仓+关键词; Wind 现货价格/港口库存待接入\n")
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
    td = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    od = sys.argv[2] if len(sys.argv) > 2 else "每日报告归档/" + td
    r = run_all(target_date=td, output_dir=od, force=True)
