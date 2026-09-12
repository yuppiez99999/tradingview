"""
Hacker News 每日热帖讨论榜报告生成器
=====================================

功能:
    - 抓取 Hacker News 当日/近期高互动帖子
    - 按评论数 + 点赞数综合排序
    - 生成 Markdown 日报保存到 每日报告归档/YYYY-MM-DD/

用法:
    python hn_daily_report.py                        # 生成今日报告 (默认 Top 20)
    python hn_daily_report.py --date 2026-07-06      # 指定日期
    python hn_daily_report.py --top 30                # Top 30
    python hn_daily_report.py --dry-run               # 仅预览不写入文件
    python hn_daily_report.py --enhanced              # 带分类标签 + 摘要的增强版报告

数据源:
    - HN Algolia API (https://hn.algolia.com/api/v1/search_by_date / items/:id)
    - 无需 API Key，免费公开接口
"""

from __future__ import annotations

import argparse
import datetime
import logging
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

# ============================================================
# 配置
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
REPORT_ROOT = BASE_DIR.parent / "每日报告归档"
DEFAULT_TOP_N = 20
REQUEST_TIMEOUT = 20
ALGOLIA_SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
ALGOLIA_ITEM_URL = "https://hn.algolia.com/api/v1/items"

# ============================================================
# 日志 / 控制台编码兼容
# ============================================================
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("hn_daily_report")


# ============================================================
# 分类标签规则
# ============================================================
_TAG_RULES: list[tuple[list[str], str]] = [
    (
        [
            "ai",
            "gpt",
            "llm",
            "claude",
            "gemini",
            "openai",
            "deepseek",
            "模型",
            "machine learning",
            "deep learning",
        ],
        "AI/ML",
    ),
    (
        ["security", "privacy", "漏洞", "leak", "hack", "攻击", "密码", "隐私"],
        "安全/隐私",
    ),
    (
        [
            "hardware",
            "chip",
            "cpu",
            "gpu",
            "semiconductor",
            "芯片",
            "硬件",
            "nvidia",
            "amd",
            "intel",
        ],
        "硬件/芯片",
    ),
    (
        [
            "science",
            "research",
            "paper",
            "研究",
            "论文",
            "物理",
            "天文",
            "生物",
            "医学",
        ],
        "科学/研究",
    ),
    (["startup", "launch", "founder", "创业", "融资", "ipo", "收购"], "创业/产品"),
    (
        ["economy", "policy", "regulation", "market", "经济", "政策", "监管", "股市"],
        "经济/政策",
    ),
    (
        ["database", "data", "storage", "analytics", "数据库", "存储", "流式"],
        "数据/数据库",
    ),
    (
        ["cloud", "aws", "azure", "gcp", "data center", "云计算", "数据中心"],
        "云/基础设施",
    ),
    (
        ["open source", "github", "linux", "开源", "工具", "framework", "library"],
        "开源/工具",
    ),
    (
        [
            "programming",
            "code",
            "python",
            "javascript",
            "typescript",
            "java",
            "go",
            "rust",
            "编程",
            "代码",
        ],
        "编程/开发",
    ),
    (["space", "rocket", "nasa", "spacex", "卫星", "火箭", "航天"], "航天/太空"),
    (["robot", "robotics", "自动驾驶", "机器人", "无人机"], "机器人/自动驾驶"),
    (
        ["energy", "battery", "solar", "nuclear", "能源", "电池", "光伏", "核电"],
        "能源/碳中和",
    ),
    (["biotech", "pharma", "drug", "生物技术", "医药", "疫苗"], "生物/医药"),
    (
        ["finance", "bank", "payment", "crypto", "bitcoin", "以太坊", "金融", "支付"],
        "金融/加密",
    ),
]


def _classify_story(title: str, url: str) -> str:
    """根据标题和链接自动生成分类标签。"""
    text = f"{title} {url}".lower()
    for keywords, tag in _TAG_RULES:
        if any(k in text for k in keywords):
            return tag
    return "综合"


def _extract_domain(url: str) -> str:
    """提取域名作为来源。"""
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return host or "news.ycombinator.com"
    except Exception:
        return "news.ycombinator.com"


def _clean_text(text: str | None, max_length: int = 140) -> str:
    """清洗文本并截断为摘要长度。"""
    if not text:
        return ""
    text = text.strip()
    if not text:
        return ""
    text = text.replace("\n", " ").replace("\r", " ")
    while "  " in text:
        text = text.replace("  ", " ")
    if len(text) > max_length:
        text = text[:max_length].rstrip() + "..."
    return text


def _summarize_story(story: dict[str, Any]) -> str:
    """基于 story_text / title / url 生成简短摘要。"""
    text = story.get("story_text") or story.get("text") or ""
    if _clean_text(text):
        return _clean_text(text)
    title = story.get("title") or ""
    url = story.get("url") or ""
    if title and url:
        domain = _extract_domain(url)
        return f"{title}（来源：{domain}）"
    return title or "暂无摘要"


def _fetch_item_summary(object_id: str) -> dict[str, Any]:
    """从 Algolia items 端点获取单个帖子的摘要信息。"""
    url = f"{ALGOLIA_ITEM_URL}/{object_id}"
    try:
        resp = requests.get(
            url, timeout=REQUEST_TIMEOUT, proxies={"http": None, "https": None}
        )
        if resp.status_code == 200:
            data = resp.json()
            return {
                "summary": _summarize_story(data),
                "story_text": data.get("story_text") or data.get("text") or "",
                "domain": _extract_domain(data.get("url") or ""),
            }
    except requests.RequestException as e:
        logger.warning("获取 HN 帖子详情失败 object_id=%s: %s", object_id, e)
    return {"summary": "", "story_text": "", "domain": ""}


def _fetch_top_comments(object_id: str, max_comments: int = 3) -> list[str]:
    """获取高赞/热门评论的简短摘要。"""
    url = f"{ALGOLIA_ITEM_URL}/{object_id}"
    try:
        resp = requests.get(
            url, timeout=REQUEST_TIMEOUT, proxies={"http": None, "https": None}
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        children = data.get("children") or []
    except requests.RequestException:
        return []

    scored: list[tuple[int, str]] = []
    for child in children[: max(20, max_comments * 3)]:
        if child.get("type") != "comment":
            continue
        text = child.get("text") or ""
        if not _clean_text(text):
            continue
        points = child.get("points") or 0
        comments = child.get("num_comments") or 0
        score = points + comments
        scored.append((score, _clean_text(text, max_length=180)))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item[1] for item in scored[:max_comments]]


# ============================================================
# HN 数据抓取
# ============================================================
def _parse_date(date_str: str | None) -> datetime.date:
    """解析 YYYY-MM-DD 日期字符串。"""
    if not date_str:
        return datetime.date.today()
    try:
        return datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        logger.error("日期格式错误，应为 YYYY-MM-DD: %s", date_str)
        sys.exit(1)


def _score(hit: dict[str, Any]) -> float:
    """综合互动分：评论数 + 点赞数（评论权重更高，反映讨论热度）。"""
    points = hit.get("points") or 0
    comments = hit.get("num_comments") or 0
    return comments * 2 + points


def fetch_hn_top_stories(
    top_n: int = DEFAULT_TOP_N, target_date: datetime.date | None = None
) -> list[dict[str, Any]]:
    """从 HN Algolia 获取热门帖子。

    Args:
        top_n: 返回条数
        target_date: 目标日期，None 时获取最近 24 小时

    Returns:
        按讨论热度排序的帖子列表
    """
    if target_date is None:
        target_date = datetime.date.today()

    params = {
        "tags": "story",
        "hitsPerPage": str(min(top_n * 5, 200)),  # 多取一些以便在客户端筛选
    }

    url = f"{ALGOLIA_SEARCH_URL}?{urlencode(params)}"
    logger.info("请求 HN Algolia: %s", url)

    try:
        resp = requests.get(
            url, timeout=REQUEST_TIMEOUT, proxies={"http": None, "https": None}
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("HN Algolia 请求失败: %s", e)
        return []

    try:
        data = resp.json()
    except ValueError as e:
        logger.error("HN Algolia 响应解析失败: %s", e)
        return []

    hits = data.get("hits", [])

    # 客户端过滤：最低互动门槛 + 日期过滤
    filtered: list[dict[str, Any]] = []
    for h in hits:
        points = h.get("points") or 0
        comments = h.get("num_comments") or 0
        if points < 3 and comments < 2:
            continue
        if target_date:
            created_at_i = h.get("created_at_i")
            if created_at_i is None:
                continue
            start_ts = int(
                datetime.datetime.combine(
                    target_date, datetime.time.min, tzinfo=datetime.UTC
                ).timestamp()
            )
            end_ts = start_ts + 86400
            if not (start_ts <= created_at_i < end_ts):
                continue
        filtered.append(h)

    logger.info("获取到 %d 条 HN 帖子（过滤后）", len(filtered))

    ranked = sorted(filtered, key=_score, reverse=True)[:top_n]

    results: list[dict[str, Any]] = []
    for rank, hit in enumerate(ranked, start=1):
        object_id = hit.get("objectID", "")
        title = hit.get("title") or "(无标题)"
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={object_id}"
        hn_url = f"https://news.ycombinator.com/item?id={object_id}"
        author = hit.get("author") or "匿名"
        points = hit.get("points") or 0
        comments = hit.get("num_comments") or 0
        created_at_i = hit.get("created_at_i")
        date_str = ""
        if created_at_i:
            dt = datetime.datetime.fromtimestamp(created_at_i, tz=datetime.UTC)
            date_str = dt.strftime("%Y-%m-%d %H:%M UTC")

        results.append(
            {
                "rank": rank,
                "title": title,
                "url": url,
                "hn_url": hn_url,
                "author": author,
                "points": points,
                "comments": comments,
                "created_at": date_str,
                "score": _score(hit),
                "object_id": object_id,
            }
        )

    return results


# ============================================================
# 报告生成
# ============================================================
def render_markdown(
    stories: list[dict[str, Any]], report_date: datetime.date, top_n: int
) -> str:
    """渲染基础版 HN 热帖讨论榜 Markdown 报告。"""
    today_str = report_date.strftime("%Y-%m-%d")
    now_str = datetime.now_bj().strftime("%H:%M:%S")
    weekday_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][
        report_date.weekday()
    ]

    lines: list[str] = []
    add = lines.append

    add("# 🔥 Hacker News 热帖讨论榜")
    add("")
    add(f"> **日期**: {today_str} ({weekday_cn})  ")
    add(f"> **生成时间**: {now_str}  ")
    add("> **数据源**: [Hacker News](https://news.ycombinator.com/) via Algolia API  ")
    add("> **排序规则**: 综合讨论热度 = 评论数×2 + 点赞数  ")
    add("")
    add("---")
    add("")

    if not stories:
        add(
            "今日未获取到 HN 热帖，可能原因：网络异常、API 限流或当日无符合条件的帖子。"
        )
        add("")
        add("> 建议检查网络连接后重试；若需手工验证，可直接访问：  ")
        add(
            f"> [HN Algolia 查询链接]({ALGOLIA_SEARCH_URL}?{urlencode({'tags': 'story', 'numericFilters': 'points>5,num_comments>0', 'hitsPerPage': 30})})"  # noqa: E501
        )
        add("")
        return "\n".join(lines)

    add(f"## 今日 Top {len(stories)} 热帖")
    add("")

    for s in stories:
        add(f"### {s['rank']}. {s['title']}")
        add("")
        add(f"🔗 [原文链接]({s['url']}) | [HN 讨论]({s['hn_url']})  ")
        add(
            f"👤 {s['author']} | 👍 {s['points']} | 💬 {s['comments']} | 🕒 {s['created_at']}"
        )
        add("")

    add("---")
    add("")
    add(f"*报告由 HN 每日热帖系统自动生成  |  共收录 {len(stories)} 条帖子*")
    add("")
    add(f"*生成时间: {today_str} {now_str}  |  仅供参考，不构成投资建议*")
    add("")

    return "\n".join(lines)


def render_markdown_enhanced(
    stories: list[dict[str, Any]], report_date: datetime.date, top_n: int
) -> str:
    """渲染增强版 HN 热帖讨论榜：自动分类 + 摘要 + 热门评论摘要。"""
    today_str = report_date.strftime("%Y-%m-%d")
    now_str = datetime.now_bj().strftime("%H:%M:%S")
    weekday_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][
        report_date.weekday()
    ]

    lines: list[str] = []
    add = lines.append

    add("# 🧠 HN 每日热帖讨论榜（增强版）")
    add("")
    add(f"> **日期**: {today_str} ({weekday_cn})  ")
    add(f"> **生成时间**: {now_str}  ")
    add("> **数据源**: [Hacker News](https://news.ycombinator.com/) via Algolia API  ")
    add("> **增强内容**: 自动分类标签 + 摘要 + 评论摘要  ")
    add("")
    add("---")
    add("")

    if not stories:
        add(
            "今日未获取到 HN 热帖，可能原因：网络异常、API 限流或当日无符合条件的帖子。"
        )
        add("")
        add("> 建议检查网络连接后重试；若需手工验证，可直接访问：  ")
        add(
            f"> [HN Algolia 查询链接]({ALGOLIA_SEARCH_URL}?{urlencode({'tags': 'story', 'numericFilters': 'points>5,num_comments>0', 'hitsPerPage': 30})})"  # noqa: E501
        )
        add("")
        return "\n".join(lines)

    # 分类统计
    tag_counts: dict[str, int] = {}
    for s in stories:
        tag = s.get("tag") or "综合"
        tag_counts[tag] = tag_counts.get(tag, 0) + 1

    add("## 📊 分类分布")
    add("")
    add("| 分类 | 数量 |")
    add("| --- | ---: |")
    for tag, count in sorted(tag_counts.items(), key=lambda x: x[1], reverse=True):
        add(f"| {tag} | {count} |")
    add("")
    add("---")
    add("")

    add(f"## 今日 Top {len(stories)} 热帖")
    add("")

    for s in stories:
        tag = s.get("tag") or "综合"
        domain = s.get("domain") or "news.ycombinator.com"
        summary = s.get("summary") or ""
        top_comments = s.get("top_comments") or []

        add(f"### {s['rank']}. {s['title']}")
        add("")
        add(f"🏷️ `{tag}` | 🌐 `{domain}`")
        add("")
        if summary:
            add(f"📝 {summary}")
            add("")
        add(f"🔗 [原文链接]({s['url']}) | [HN 讨论]({s['hn_url']})  ")
        add(
            f"👤 {s['author']} | 👍 {s['points']} | 💬 {s['comments']} | 🕒 {s['created_at']}"
        )
        add("")

        if top_comments:
            add("**💡 热门评论摘要：**")
            add("")
            for idx, comment in enumerate(top_comments, start=1):
                add(f"{idx}. {comment}")
                add("")

    add("---")
    add("")
    add(f"*报告由 HN 每日热帖系统自动生成  |  共收录 {len(stories)} 条帖子*")
    add("")
    add(f"*生成时间: {today_str} {now_str}  |  仅供参考，不构成投资建议*")
    add("")

    return "\n".join(lines)


def enrich_stories(stories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """为帖子补充分类、摘要和评论摘要。"""
    enriched: list[dict[str, Any]] = []
    for s in stories:
        object_id = s.get("object_id", "")
        item_info: dict[str, Any] = {"summary": "", "story_text": "", "domain": ""}
        if object_id:
            item_info = _fetch_item_summary(object_id)

        tag = _classify_story(s.get("title", ""), s.get("url", ""))
        domain = item_info.get("domain") or _extract_domain(s.get("url", ""))
        summary = item_info.get("summary") or s.get("title", "")

        top_comments: list[str] = []
        if object_id and s.get("comments", 0) > 0:
            top_comments = _fetch_top_comments(object_id, max_comments=3)

        item = dict(s)
        item["tag"] = tag
        item["domain"] = domain
        item["summary"] = summary
        item["top_comments"] = top_comments
        enriched.append(item)
    return enriched


def save_report(
    markdown_text: str, report_date: datetime.date, enhanced: bool = False
) -> Path:
    """保存 Markdown 报告到 每日报告归档/YYYY-MM-DD/。"""
    date_folder = REPORT_ROOT / report_date.strftime("%Y-%m-%d")
    date_folder.mkdir(parents=True, exist_ok=True)

    suffix = "增强版" if enhanced else ""
    filename = f"HN热帖讨论榜_{suffix}{report_date.strftime('%Y%m%d')}.md"
    filepath = date_folder / filename

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(markdown_text)

    logger.info("报告已保存: %s", filepath)
    return filepath


# ============================================================
# CLI
# ============================================================
def main() -> int:
    parser = argparse.ArgumentParser(description="Hacker News 每日热帖讨论榜报告生成器")
    parser.add_argument(
        "--date", type=str, default=None, help="指定日期 YYYY-MM-DD，默认今天"
    )
    parser.add_argument(
        "--top", type=int, default=DEFAULT_TOP_N, help=f"Top N 条，默认 {DEFAULT_TOP_N}"
    )
    parser.add_argument("--dry-run", action="store_true", help="仅预览不写入文件")
    parser.add_argument(
        "--enhanced",
        action="store_true",
        help="生成增强版报告（带分类标签、摘要、评论摘要）",
    )
    args = parser.parse_args()

    report_date = _parse_date(args.date)
    top_n = max(1, args.top)
    enhanced = bool(args.enhanced)

    logger.info(
        "开始生成 HN 热帖报告: date=%s, top=%d, enhanced=%s",
        report_date,
        top_n,
        enhanced,
    )
    stories = fetch_hn_top_stories(top_n=top_n, target_date=report_date)

    if not stories:
        logger.warning("未获取到任何 HN 帖子，生成空报告")

    if enhanced:
        logger.info("增强模式：开始补充分类、摘要与评论摘要...")
        stories = enrich_stories(stories)
        markdown_text = render_markdown_enhanced(stories, report_date, top_n)
    else:
        markdown_text = render_markdown(stories, report_date, top_n)

    if args.dry_run:
        logger.info(markdown_text)
        return 0

    filepath = save_report(markdown_text, report_date, enhanced=enhanced)
    logger.info(f"✅ HN 热帖报告已生成: {filepath}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
