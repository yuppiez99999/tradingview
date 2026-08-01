"""
舆情监控中枢 — 统一调度 TrendSonar + 动力煤舆情 + MediaCrawler 自媒体
===============================================================================
将 02_舆情与竞品监控/舆情监控/ 全部能力统一接入每日工作流。

用法:
    from sentiment_hub import run_all, check_all, remediate

    # 生成全部舆情报告并归档
    result = run_all(target_date="2026-06-28")

    # 仅检查哪些报告缺失
    missing = check_all(target_date="2026-06-28")

    # 自动补全缺失报告
    remediate(target_date="2026-06-28")

调用链:
    TrendSonar → config.yaml 关键词 + 5源 + 豆包 Speed/DeepSeek AI → 舆情综合日报
    动力煤专项 → Wind/iFinD + 豆包 Speed/DeepSeek AI → 动力煤舆情日报
    MediaCrawler → 小红书/抖音/B站/微博/知乎 7大平台自媒体舆情 → 自媒体舆情日报 (Feature Flag 控制)
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_MODULE_DIR = Path(__file__).resolve().parent
_BASE_DIR = _MODULE_DIR.parent  # v8.3_institutional\src
# 向上定位到 e:\各种PY程序 (项目根的父级)
# _MODULE_DIR = v8.3_institutional\src\nlp
# .parent = src, .parent.parent = v8.3_institutional, .parent.parent.parent = 28-终极量化交易系统8.4, .parent.parent.parent.parent = e:\各种PY程序
_PROJECT_ROOT = _MODULE_DIR.parents[3]  # e:\各种PY程序

# ── 路径常量 ──
_SENTIMENT_DIR = _PROJECT_ROOT / "02_舆情与竞品监控" / "舆情监控"
_TRENDSONAR_SCRIPT = _SENTIMENT_DIR / "trendsonar_daily.py"
_COAL_SCRIPT = _SENTIMENT_DIR / "煤炭舆情日报" / "coal_sentiment_daily.py"
_CONFIG_YAML = _SENTIMENT_DIR / "config.yaml"
_SENTIMENT_DATA_DIR = _SENTIMENT_DIR / "data" / "综合日报"
_COAL_DATA_DIR = _SENTIMENT_DIR / "煤炭舆情日报"
_ARCHIVE_DIR = _PROJECT_ROOT / "每日报告归档"

PYTHON_EXE = sys.executable

# MediaCrawler 输出目录
_MEDIACRAWLER_DATA_DIR = _PROJECT_ROOT / "data" / "mediacrawler"
_MEDIACRAWLER_DATA_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# 底层：子进程执行
# ============================================================


def _run_subprocess(script_path: Path, args: list, timeout: int = 300, cwd: Optional[Path] = None) -> tuple:
    """执行 Python 子进程，返回 (success: bool, stdout: str, stderr: str)"""
    if not script_path.is_file():
        return False, "", f"脚本不存在: {script_path}"

    cmd = [PYTHON_EXE, str(script_path), *args]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd or script_path.parent),
            timeout=timeout,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", f"超时（>{timeout}秒）"
    except Exception as e:
        return False, "", str(e)


# ============================================================
# TrendSonar 综合舆情 — import 模式
# ============================================================


def _run_trendsonar_import(target_date: str, output_dir: Path) -> dict:
    """通过 Python import 直接运行 TrendSonar（无需子进程开销）"""
    reports = {}

    try:
        sys.path.insert(0, str(_SENTIMENT_DIR))
        from trendsonar_daily import (
            ask_ai,
            deduplicate_articles,
            fetch_eastmoney,
            fetch_rss,
            fetch_sina_finance,
            fetch_web_36kr,
            filter_by_keywords,
            generate_report,
            load_config,
        )

        date_short = target_date.replace("-", "")
        config = load_config()
        keywords = config.get("keywords", [])
        exclude = config.get("exclude", [])
        sources = config.get("sources", [])

        # 抓取
        all_articles = []
        for src in sources:
            if not src.get("enabled", True):
                continue
            name = src["name"]
            if name == "新浪财经":
                arts = fetch_sina_finance(keywords)
            elif name == "36氪":
                arts = fetch_web_36kr()
            elif name == "财联社":
                arts = fetch_rss(src.get("url", ""), name)
            elif src.get("type") == "rss":
                arts = fetch_rss(src.get("url", ""), name)
            else:
                arts = []
            all_articles.extend(arts)
            print(f"    {name}: {len(arts)} 条")

        em_arts = fetch_eastmoney(keywords)
        all_articles.extend(em_arts)
        print(f"    东方财富: {len(em_arts)} 条")

        # 过滤
        all_articles = deduplicate_articles(all_articles)
        filtered = filter_by_keywords(all_articles, keywords, exclude)
        print(f"    去重后 {len(all_articles)} 条，匹配 {len(filtered)} 条")

        # 持仓
        holdings_text = ""
        items = config.get("portfolio", {}).get("items", [])
        if items:
            holdings_list = [f"{it['name']}({it.get('sina_code', '')}，权重{it.get('weight', '')}%)" for it in items]
            holdings_text = "\n".join(holdings_list)

        # AI (豆包 Speed → DeepSeek → Ollama)
        ai_enabled = config.get("ai", {}).get("enabled", True)
        ai_summary = ask_ai(config, filtered, holdings_text) if ai_enabled else "AI 未启用"

        # 生成报告
        report = generate_report(config, filtered, ai_summary)

        # 保存到归档目录
        md_path = output_dir / f"舆情综合日报_{date_short}.md"
        md_path.write_text(report, encoding="utf-8")
        reports["sentiment_md"] = str(md_path)

        txt_path = output_dir / f"舆情综合日报_{date_short}.txt"
        txt_path.write_text(report, encoding="utf-8")
        reports["sentiment_txt"] = str(txt_path)

        # 摘要 JSON
        summary = {
            "date": target_date,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_fetched": len(all_articles),
            "matched": len(filtered),
            "keywords_used": keywords[:10],
            "sources": [s["name"] for s in sources if s.get("enabled", True)],
            "ai_enabled": ai_enabled,
        }
        json_path = output_dir / f"sentiment_summary_{date_short}.json"
        json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        reports["sentiment_json"] = str(json_path)

        print(f"  ✅ TrendSonar (import) 完成: {len(filtered)} 条匹配，{len(report) / 1024:.1f} KB")

    except ImportError as e:
        print(f"  ⚠️  TrendSonar import 失败 ({e})，回退子进程")
        return _run_trendsonar_subprocess(target_date, output_dir)
    except Exception as e:
        print(f"  ⚠️  TrendSonar 异常 ({e})，回退子进程")
        return _run_trendsonar_subprocess(target_date, output_dir)
    finally:
        if str(_SENTIMENT_DIR) in sys.path:
            sys.path.remove(str(_SENTIMENT_DIR))

    return reports


def _run_trendsonar_subprocess(target_date: str, output_dir: Path) -> dict:
    """子进程模式运行 TrendSonar"""
    reports = {}
    date_short = target_date.replace("-", "")

    ok, stdout, stderr = _run_subprocess(_TRENDSONAR_SCRIPT, args=[], timeout=600, cwd=_SENTIMENT_DIR)
    if stdout:
        print(stdout[-800:])
    if stderr:
        print(f"  ⚠️ stderr: {stderr[:300]}")

    if ok:
        # 复制产出到归档
        trend_md = _SENTIMENT_DATA_DIR / f"综合日报_{date_short}.md"
        trend_txt = _SENTIMENT_DATA_DIR / f"综合日报_{date_short}.txt"
        trend_json = _SENTIMENT_DATA_DIR / f"sentiment_summary_{date_short}.json"

        for src, suffix in [(trend_md, ".md"), (trend_txt, ".txt"), (trend_json, ".json")]:
            if src.is_file():
                dest = (
                    output_dir / f"舆情综合日报_{date_short}{suffix}"
                    if suffix != ".json"
                    else output_dir / f"sentiment_summary_{date_short}.json"
                )
                dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
                key = f"sentiment_{suffix[1:]}" if suffix != ".json" else "sentiment_json"
                reports[key] = str(dest)

    return reports


# ============================================================
# 动力煤舆情 — import 模式
# ============================================================


def _run_coal_import(target_date: str, output_dir: Path) -> dict:
    """通过 Python import 直接运行动力煤舆情日报"""
    reports = {}

    try:
        sys.path.insert(0, str(_COAL_DATA_DIR))
        sys.path.insert(0, str(_MODULE_DIR))  # deepseek_investment_summary

        # coal_sentiment_daily.py 用模块级 REPORT_DATE / TODAY_ISO / TODAY_SHORT，需要猴子补丁替换日期
        import coal_sentiment_daily as csd
        from coal_sentiment_daily import (
            TODAY_ISO as _orig_today,  # noqa: F401
        )
        from coal_sentiment_daily import (
            TODAY_SHORT as _orig_short,  # noqa: F401
        )
        from coal_sentiment_daily import (
            main as coal_main,
        )

        _save_report_date = csd.REPORT_DATE
        _save_iso = csd.TODAY_ISO
        _save_short = csd.TODAY_SHORT
        _save_data_date = csd.DATA_DATE

        target_dt = datetime.strptime(target_date, "%Y-%m-%d")
        csd.REPORT_DATE = target_dt
        csd.TODAY_ISO = target_date
        csd.TODAY_SHORT = target_date.replace("-", "")
        csd.DATA_DATE = target_date  # also update DATA_DATE

        try:
            # 保存并清理 sys.argv, 避免 coal_main() 内部 argparse 解析到上游脚本的参数 (如 --force)
            _save_argv = sys.argv
            sys.argv = [sys.argv[0]]  # 只保留脚本名, 移除所有参数
            coal_main()  # 内部已处理所有输出路径
        finally:
            sys.argv = _save_argv  # 恢复 sys.argv
            csd.REPORT_DATE = _save_report_date
            csd.TODAY_ISO = _save_iso
            csd.TODAY_SHORT = _save_short
            csd.DATA_DATE = _save_data_date

        date_short = target_date.replace("-", "")
        coal_md = _COAL_DATA_DIR / f"动力煤舆情日报_{target_date}.md"

        if coal_md.is_file():
            dest = output_dir / f"动力煤舆情日报_{date_short}.md"
            dest.write_text(coal_md.read_text(encoding="utf-8"), encoding="utf-8")
            reports["coal_md"] = str(dest)
            print(f"  ✅ 动力煤舆情 (import) 完成: {coal_md.stat().st_size / 1024:.1f} KB")
        else:
            print("  ⚠️  动力煤日报文件未生成 (import模式)")

    except ImportError as e:
        print(f"  ⚠️  动力煤 import 失败 ({e})，回退子进程")
        return _run_coal_subprocess(target_date, output_dir)
    except Exception as e:
        print(f"  ⚠️  动力煤异常 ({e})，回退子进程")
        return _run_coal_subprocess(target_date, output_dir)
    finally:
        if str(_COAL_DATA_DIR) in sys.path:
            sys.path.remove(str(_COAL_DATA_DIR))
        if str(_MODULE_DIR) in sys.path:
            sys.path.remove(str(_MODULE_DIR))

    return reports


def _run_coal_subprocess(target_date: str, output_dir: Path) -> dict:
    """子进程模式运行动力煤舆情日报"""
    reports = {}
    date_short = target_date.replace("-", "")

    ok, stdout, stderr = _run_subprocess(_COAL_SCRIPT, args=["--date", target_date], timeout=300, cwd=_COAL_DATA_DIR)
    if stdout:
        print(stdout[-600:])
    if stderr:
        print(f"  ⚠️ stderr: {stderr[:300]}")

    if ok:
        coal_md = _COAL_DATA_DIR / f"动力煤舆情日报_{target_date}.md"
        if coal_md.is_file():
            dest = output_dir / f"动力煤舆情日报_{date_short}.md"
            dest.write_text(coal_md.read_text(encoding="utf-8"), encoding="utf-8")
            reports["coal_md"] = str(dest)
            print(f"  ✅ 动力煤舆情 (子进程) 完成: {coal_md.stat().st_size / 1024:.1f} KB")

    return reports


# ============================================================
# MediaCrawler 自媒体舆情
# ============================================================


def _is_mediacrawler_enabled() -> bool:
    """检查 USE_MEDIA_CRAWLER Feature Flag 是否启用"""
    try:
        sys.path.insert(0, str(_PROJECT_ROOT))
        from utils.infra.feature_flags import FeatureFlags

        return FeatureFlags.is_enabled("USE_MEDIA_CRAWLER")
    except Exception:
        return False
    finally:
        if str(_PROJECT_ROOT) in sys.path:
            sys.path.remove(str(_PROJECT_ROOT))


def _run_mediacrawler(target_date: str, output_dir: Path) -> dict:
    """
    运行 MediaCrawler 自媒体舆情抓取

    步骤:
      1. 检查 Feature Flag
      2. 从 config.yaml 读取关键词
      3. 调用 MediaCrawlerAdapter 搜索多平台
      4. 生成自媒体舆情日报
    """
    reports = {}
    date_short = target_date.replace("-", "")

    # 1. 检查 Feature Flag
    if not _is_mediacrawler_enabled():
        print("  ℹ️  MediaCrawler: Feature Flag 未启用 (USE_MEDIA_CRAWLER=False)，跳过")
        return reports

    try:
        sys.path.insert(0, str(_PROJECT_ROOT))
        from utils.media_crawler_adapter import MediaCrawlerAdapter

        # 2. 读取关键词
        try:
            sys.path.insert(0, str(_SENTIMENT_DIR))
            from trendsonar_daily import load_config

            config = load_config()
            keywords = config.get("keywords", [])
        except Exception:
            keywords = ["A股", "股市", "半导体", "AI", "新能源"]

        if not keywords:
            keywords = ["A股", "股市"]

        # 3. 初始化适配器并抓取
        adapter = MediaCrawlerAdapter(enabled=True)
        health = adapter.check_health()

        if not health.get("available", False):
            print(f"  ⚠️  MediaCrawler 服务不可用: {health.get('reason', 'unknown')}，跳过")
            return reports

        print(f"  🕸️  MediaCrawler 抓取关键词: {keywords[:8]}")

        all_items = []
        platform_summary = {}

        # 取前 5 个关键词，每个搜索 4 个主流平台
        for keyword in keywords[:5]:
            items = adapter.fetch_social_news(
                keyword=keyword,
                platforms=["xhs", "bili", "wb", "zhihu"],
                max_items=30,
            )
            all_items.extend(items)

        # 统计各平台数量
        for item in all_items:
            src = item.source
            platform_summary[src] = platform_summary.get(src, 0) + 1

        print(f"  ✅ MediaCrawler 共抓取 {len(all_items)} 条: {platform_summary}")

        # 4. 生成日报
        report_lines = []
        report_lines.append(f"# 自媒体舆情日报 — {target_date}")
        report_lines.append("")
        report_lines.append("## 概览")
        report_lines.append(f"- 抓取时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report_lines.append("- 数据来源: MediaCrawler (小红书/B站/微博/知乎)")
        report_lines.append(f"- 总条目数: {len(all_items)}")
        report_lines.append("- 平台分布:")
        for platform, count in platform_summary.items():
            report_lines.append(f"  - {platform}: {count} 条")
        report_lines.append("")

        # 按热度排序取 Top 20
        all_items_sorted = sorted(
            all_items,
            key=lambda x: x.like_count + x.comment_count * 2 + x.share_count * 3,
            reverse=True,
        )[:20]

        report_lines.append("## 热门内容 TOP 20")
        report_lines.append("")
        for i, item in enumerate(all_items_sorted, 1):
            title_display = item.title or (item.content[:40] + "..." if len(item.content) > 40 else item.content)
            report_lines.append(f"### {i}. {title_display}")
            report_lines.append(f"- 平台: {item.source}")
            report_lines.append(f"- 发布时间: {item.published_at or '未知'}")
            report_lines.append(f"- 互动: 👍{item.like_count} 💬{item.comment_count} 🔄{item.share_count}")
            if item.url:
                report_lines.append(f"- 链接: {item.url}")
            if item.content:
                content_preview = item.content[:200] + "..." if len(item.content) > 200 else item.content
                report_lines.append(f"- 摘要: {content_preview}")
            report_lines.append("")

        # 保存 JSON 数据
        json_data = {
            "date": target_date,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_count": len(all_items),
            "platform_summary": platform_summary,
            "keywords_used": keywords[:8],
            "items": [item.to_dict() for item in all_items_sorted],
        }
        json_path = _MEDIACRAWLER_DATA_DIR / f"mediacrawler_{date_short}.json"
        json_path.write_text(json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8")

        # 保存 Markdown 报告
        md_content = "\n".join(report_lines)
        md_path = output_dir / f"自媒体舆情日报_{date_short}.md"
        md_path.write_text(md_content, encoding="utf-8")
        reports["mediacrawler_md"] = str(md_path)
        reports["mediacrawler_json"] = str(json_path)

        print(f"  ✅ MediaCrawler 报告生成: {md_path.stat().st_size / 1024:.1f} KB")

    except ImportError as e:
        print(f"  ⚠️  MediaCrawler 模块未安装 ({e})，跳过")
    except Exception as e:
        print(f"  ⚠️  MediaCrawler 异常 ({e})，跳过")
    finally:
        if str(_PROJECT_ROOT) in sys.path:
            sys.path.remove(str(_PROJECT_ROOT))
        if str(_SENTIMENT_DIR) in sys.path:
            sys.path.remove(str(_SENTIMENT_DIR))

    return reports


# ============================================================
# 公开 API
# ============================================================


def check_all(target_date: Optional[str] = None, output_dir: Optional[str] = None) -> dict:
    """
    检查所有舆情报告是否存在。

    返回:
        {
            "date": str,
            "archive_dir": str,
            "reports": {
                "sentiment_md": {"exists": bool, "path": str, "size_kb": float},
                "sentiment_txt": {"exists": bool, "path": str, "size_kb": float},
                "coal_md": {"exists": bool, "path": str, "size_kb": float},
            },
            "all_present": bool,
            "missing": [...],
        }
    """
    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")
    date_short = target_date.replace("-", "")

    if output_dir is None:
        archive_dir = _ARCHIVE_DIR / target_date
    else:
        archive_dir = Path(output_dir)

    report_defs = {
        "sentiment_md": archive_dir / f"舆情综合日报_{date_short}.md",
        "sentiment_txt": archive_dir / f"舆情综合日报_{date_short}.txt",
        "coal_md": archive_dir / f"动力煤舆情日报_{date_short}.md",
    }

    # MediaCrawler 报告仅在 Feature Flag 启用时纳入检查
    if _is_mediacrawler_enabled():
        report_defs["mediacrawler_md"] = archive_dir / f"自媒体舆情日报_{date_short}.md"

    reports = {}
    missing = []
    for key, path in report_defs.items():
        exists = path.is_file() and path.stat().st_size > 100
        reports[key] = {
            "exists": exists,
            "path": str(path),
            "size_kb": path.stat().st_size / 1024 if exists else 0,
        }
        if not exists:
            missing.append(key)

    return {
        "date": target_date,
        "archive_dir": str(archive_dir),
        "reports": reports,
        "all_present": len(missing) == 0,
        "missing": missing,
    }


def run_all(
    target_date: Optional[str] = None,
    output_dir: Optional[str] = None,
    run_trend: bool = True,
    run_coal: bool = True,
    run_mediacrawler: bool = True,
    force: bool = False,
) -> dict:
    """
    生成全部舆情报告并归档到每日报告归档/YYYY-MM-DD/。

    参数:
        target_date:      目标日期 (默认今日)
        output_dir:       归档目录 (默认 每日报告归档/YYYY-MM-DD/)
        run_trend:        是否生成 TrendSonar 综合舆情
        run_coal:         是否生成动力煤专项
        run_mediacrawler: 是否生成 MediaCrawler 自媒体舆情 (受 Feature Flag 控制)
        force:            强制重新生成（忽略已存在文件）

    返回:
        {
            "ok": bool,
            "date": str,
            "archive_dir": str,
            "reports": {"sentiment_md": "...", "coal_md": "...", "mediacrawler_md": "...", ...},
            "errors": [...],
        }
    """
    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")

    if output_dir is None:
        archive_dir = _ARCHIVE_DIR / target_date
    else:
        archive_dir = Path(output_dir)

    archive_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("📡 舆情监控中枢 — 统一日报生成")
    print(f"🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📅 目标日期: {target_date}")
    print(f"📁 归档目录: {archive_dir}")
    print("=" * 60)

    all_reports = {}
    errors = []
    date_short = target_date.replace("-", "")

    # ── 1. TrendSonar 综合舆情 ──
    if run_trend:
        sentiment_md = archive_dir / f"舆情综合日报_{date_short}.md"
        if sentiment_md.is_file() and sentiment_md.stat().st_size > 500 and not force:
            print(f"\n1️⃣  TrendSonar 综合舆情 — 已存在，跳过 ({sentiment_md.stat().st_size / 1024:.1f} KB)")
            all_reports["sentiment_md"] = str(sentiment_md)
        else:
            print("\n1️⃣  TrendSonar 综合舆情日报...")
            trend_reports = _run_trendsonar_import(target_date, archive_dir)
            if trend_reports:
                all_reports.update(trend_reports)
            else:
                errors.append("TrendSonar: 生成失败（import + 子进程均失败）")

    # ── 2. 动力煤舆情 ──
    if run_coal:
        coal_md = archive_dir / f"动力煤舆情日报_{date_short}.md"
        if coal_md.is_file() and coal_md.stat().st_size > 500 and not force:
            print(f"\n2️⃣  动力煤舆情日报 — 已存在，跳过 ({coal_md.stat().st_size / 1024:.1f} KB)")
            all_reports["coal_md"] = str(coal_md)
        else:
            print("\n2️⃣  动力煤舆情日报...")
            if _COAL_SCRIPT.is_file():
                coal_reports = _run_coal_import(target_date, archive_dir)
                if coal_reports:
                    all_reports.update(coal_reports)
                else:
                    errors.append("动力煤: 生成失败（import + 子进程均失败）")
            else:
                print("  ℹ️  动力煤脚本不存在 (需 Wind 终端)，跳过")
                errors.append("动力煤: 脚本不存在")

    # ── 3. MediaCrawler 自媒体舆情 ──
    if run_mediacrawler and _is_mediacrawler_enabled():
        mc_md = archive_dir / f"自媒体舆情日报_{date_short}.md"
        if mc_md.is_file() and mc_md.stat().st_size > 500 and not force:
            print(f"\n3️⃣  MediaCrawler 自媒体舆情 — 已存在，跳过 ({mc_md.stat().st_size / 1024:.1f} KB)")
            all_reports["mediacrawler_md"] = str(mc_md)
        else:
            print("\n3️⃣  MediaCrawler 自媒体舆情...")
            mc_reports = _run_mediacrawler(target_date, archive_dir)
            if mc_reports:
                all_reports.update(mc_reports)
            # MediaCrawler 失败不记录为错误 (可选数据源)

    # ── 汇总 ──
    print("\n" + "=" * 60)
    count = len(all_reports)
    print(f"📊 舆情监控结果: {count} 份报告")
    for name, path in all_reports.items():
        size = os.path.getsize(path) / 1024 if os.path.isfile(path) else 0
        print(f"  ✅ {name}: {Path(path).name} ({size:.1f} KB)")
    if errors:
        for e in errors:
            print(f"  ❌ {e}")
    print("=" * 60)

    return {
        "ok": len(all_reports) > 0,
        "date": target_date,
        "archive_dir": str(archive_dir),
        "reports": all_reports,
        "errors": errors,
    }


def remediate(target_date: Optional[str] = None, output_dir: Optional[str] = None) -> dict:
    """
    检查缺失的舆情报告并自动生成。

    返回:
        {
            "ok": bool,
            "generated": [...],    # 本此新生成的报告
            "already_ok": [...],   # 原本就存在的
            "still_missing": [...],# 生成后仍缺失的
        }
    """
    check = check_all(target_date, output_dir)
    archive_dir = Path(check["archive_dir"])

    generated = []
    already_ok = []
    still_missing = []

    for key, info in check["reports"].items():
        if info["exists"]:
            already_ok.append(key)
            continue

        # 缺失 — 尝试生成
        if key in ("sentiment_md", "sentiment_txt"):
            # 需要跑 TrendSonar
            trend_reports = _run_trendsonar_import(target_date or datetime.now().strftime("%Y-%m-%d"), archive_dir)
            if trend_reports:
                generated.extend(trend_reports.keys())
            else:
                still_missing.append(key)
        elif key == "coal_md":
            coal_reports = _run_coal_import(target_date or datetime.now().strftime("%Y-%m-%d"), archive_dir)
            if coal_reports:
                generated.extend(coal_reports.keys())
            else:
                still_missing.append(key)

    return {
        "ok": len(still_missing) == 0,
        "generated": list(set(generated)),
        "already_ok": already_ok,
        "still_missing": still_missing,
    }


# ============================================================
# CLI 入口
# ============================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="舆情监控中枢")
    parser.add_argument("--date", type=str, default=None, help="目标日期 YYYY-MM-DD")
    parser.add_argument("--check", action="store_true", help="仅检查，不生成")
    parser.add_argument("--remediate", action="store_true", help="自动补全缺失报告")
    parser.add_argument("--force", action="store_true", help="强制重新生成")
    parser.add_argument("--no-coal", action="store_true", help="跳过多媒体")
    parser.add_argument("--no-trend", action="store_true", help="跳过 TrendSonar")
    args = parser.parse_args()

    if args.check:
        result = check_all(args.date)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.remediate:
        result = remediate(args.date)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        result = run_all(
            target_date=args.date,
            run_trend=not args.no_trend,
            run_coal=not args.no_coal,
            force=args.force,
        )
        if not result["ok"]:
            sys.exit(1)
