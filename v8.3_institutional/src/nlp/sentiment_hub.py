# -*- coding: utf-8 -*-
"""
舆情监控中枢 — 统一调度 TrendSonar + 动力煤舆情
=================================================
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
"""

import os
import sys
import json
import subprocess
from datetime import datetime
from pathlib import Path

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

_MODULE_DIR = Path(__file__).resolve().parent
_BASE_DIR = _MODULE_DIR.parent

# ── 路径常量 ──
_SENTIMENT_DIR = _BASE_DIR / "02_舆情与竞品监控" / "舆情监控"
_TRENDSONAR_SCRIPT = _SENTIMENT_DIR / "trendsonar_daily.py"
_COAL_SCRIPT = _SENTIMENT_DIR / "煤炭舆情日报" / "coal_sentiment_daily.py"
_CONFIG_YAML = _SENTIMENT_DIR / "config.yaml"
_SENTIMENT_DATA_DIR = _SENTIMENT_DIR / "data" / "综合日报"
_COAL_DATA_DIR = _SENTIMENT_DIR / "煤炭舆情日报"
_ARCHIVE_DIR = _BASE_DIR / "每日报告归档"

PYTHON_EXE = sys.executable


# ============================================================
# 底层：子进程执行
# ============================================================

def _run_subprocess(script_path: Path, args: list, timeout: int = 300,
                    cwd: Path = None) -> tuple:
    """执行 Python 子进程，返回 (success: bool, stdout: str, stderr: str)"""
    if not script_path.is_file():
        return False, "", f"脚本不存在: {script_path}"

    cmd = [PYTHON_EXE, str(script_path)] + args
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd or script_path.parent),
            timeout=timeout,
            capture_output=True, text=True,
            encoding='utf-8', errors='replace'
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
            load_config, fetch_rss, fetch_web_36kr, fetch_eastmoney,
            fetch_sina_finance, deduplicate_articles, filter_by_keywords,
            ask_ai, generate_report
        )

        date_short = target_date.replace('-', '')
        config = load_config()
        keywords = config.get('keywords', [])
        exclude = config.get('exclude', [])
        sources = config.get('sources', [])

        # 抓取
        all_articles = []
        for src in sources:
            if not src.get('enabled', True):
                continue
            name = src['name']
            if name == '新浪财经':
                arts = fetch_sina_finance(keywords)
            elif name == '36氪':
                arts = fetch_web_36kr()
            elif name == '财联社':
                arts = fetch_rss(src.get('url', ''), name)
            elif src.get('type') == 'rss':
                arts = fetch_rss(src.get('url', ''), name)
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
        items = config.get('portfolio', {}).get('items', [])
        if items:
            holdings_list = [
                f"{it['name']}({it.get('sina_code', '')}，权重{it.get('weight', '')}%)"
                for it in items
            ]
            holdings_text = '\n'.join(holdings_list)

        # AI (豆包 Speed → DeepSeek → Ollama)
        ai_enabled = config.get('ai', {}).get('enabled', True)
        ai_summary = ask_ai(config, filtered, holdings_text) if ai_enabled else "AI 未启用"

        # 生成报告
        report = generate_report(config, filtered, ai_summary)

        # 保存到归档目录
        md_path = output_dir / f"舆情综合日报_{date_short}.md"
        md_path.write_text(report, encoding='utf-8')
        reports["sentiment_md"] = str(md_path)

        txt_path = output_dir / f"舆情综合日报_{date_short}.txt"
        txt_path.write_text(report, encoding='utf-8')
        reports["sentiment_txt"] = str(txt_path)

        # 摘要 JSON
        summary = {
            'date': target_date,
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'total_fetched': len(all_articles),
            'matched': len(filtered),
            'keywords_used': keywords[:10],
            'sources': [s['name'] for s in sources if s.get('enabled', True)],
            'ai_enabled': ai_enabled,
        }
        json_path = output_dir / f"sentiment_summary_{date_short}.json"
        json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        reports["sentiment_json"] = str(json_path)

        print(f"  ✅ TrendSonar (import) 完成: {len(filtered)} 条匹配，{len(report)/1024:.1f} KB")

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
    date_short = target_date.replace('-', '')

    ok, stdout, stderr = _run_subprocess(
        _TRENDSONAR_SCRIPT, args=[], timeout=600, cwd=_SENTIMENT_DIR
    )
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
                dest = output_dir / f"舆情综合日报_{date_short}{suffix}" if suffix != ".json" else output_dir / f"sentiment_summary_{date_short}.json"
                dest.write_text(src.read_text(encoding='utf-8'), encoding='utf-8')
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

        from coal_sentiment_daily import (
            main as coal_main, TODAY_ISO as _orig_today, TODAY_SHORT as _orig_short
        )

        # coal_sentiment_daily.py 用模块级 REPORT_DATE / TODAY_ISO / TODAY_SHORT，需要猴子补丁替换日期
        import coal_sentiment_daily as csd
        _save_report_date = csd.REPORT_DATE
        _save_iso = csd.TODAY_ISO
        _save_short = csd.TODAY_SHORT
        _save_data_date = csd.DATA_DATE

        target_dt = datetime.strptime(target_date, '%Y-%m-%d')
        csd.REPORT_DATE = target_dt
        csd.TODAY_ISO = target_date
        csd.TODAY_SHORT = target_date.replace('-', '')
        csd.DATA_DATE = target_date  # also update DATA_DATE

        try:
            coal_main()  # 内部已处理所有输出路径
        finally:
            csd.REPORT_DATE = _save_report_date
            csd.TODAY_ISO = _save_iso
            csd.TODAY_SHORT = _save_short
            csd.DATA_DATE = _save_data_date

        date_short = target_date.replace('-', '')
        coal_md = _COAL_DATA_DIR / f"动力煤舆情日报_{target_date}.md"

        if coal_md.is_file():
            dest = output_dir / f"动力煤舆情日报_{date_short}.md"
            dest.write_text(coal_md.read_text(encoding='utf-8'), encoding='utf-8')
            reports["coal_md"] = str(dest)
            print(f"  ✅ 动力煤舆情 (import) 完成: {coal_md.stat().st_size/1024:.1f} KB")
        else:
            print(f"  ⚠️  动力煤日报文件未生成 (import模式)")

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
    date_short = target_date.replace('-', '')

    ok, stdout, stderr = _run_subprocess(
        _COAL_SCRIPT,
        args=["--date", target_date],
        timeout=300,
        cwd=_COAL_DATA_DIR
    )
    if stdout:
        print(stdout[-600:])
    if stderr:
        print(f"  ⚠️ stderr: {stderr[:300]}")

    if ok:
        coal_md = _COAL_DATA_DIR / f"动力煤舆情日报_{target_date}.md"
        if coal_md.is_file():
            dest = output_dir / f"动力煤舆情日报_{date_short}.md"
            dest.write_text(coal_md.read_text(encoding='utf-8'), encoding='utf-8')
            reports["coal_md"] = str(dest)
            print(f"  ✅ 动力煤舆情 (子进程) 完成: {coal_md.stat().st_size/1024:.1f} KB")

    return reports


# ============================================================
# 公开 API
# ============================================================

def check_all(target_date: str = None, output_dir: str = None) -> dict:
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
        target_date = datetime.now().strftime('%Y-%m-%d')
    date_short = target_date.replace('-', '')

    if output_dir is None:
        archive_dir = _ARCHIVE_DIR / target_date
    else:
        archive_dir = Path(output_dir)

    report_defs = {
        "sentiment_md": archive_dir / f"舆情综合日报_{date_short}.md",
        "sentiment_txt": archive_dir / f"舆情综合日报_{date_short}.txt",
        "coal_md": archive_dir / f"动力煤舆情日报_{date_short}.md",
    }

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


def run_all(target_date: str = None, output_dir: str = None,
            run_trend: bool = True, run_coal: bool = True,
            force: bool = False) -> dict:
    """
    生成全部舆情报告并归档到每日报告归档/YYYY-MM-DD/。

    参数:
        target_date: 目标日期 (默认今日)
        output_dir:  归档目录 (默认 每日报告归档/YYYY-MM-DD/)
        run_trend:   是否生成 TrendSonar 综合舆情
        run_coal:    是否生成动力煤专项
        force:       强制重新生成（忽略已存在文件）

    返回:
        {
            "ok": bool,
            "date": str,
            "archive_dir": str,
            "reports": {"sentiment_md": "...", "coal_md": "...", ...},
            "errors": [...],
        }
    """
    if target_date is None:
        target_date = datetime.now().strftime('%Y-%m-%d')

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
    date_short = target_date.replace('-', '')

    # ── 1. TrendSonar 综合舆情 ──
    if run_trend:
        sentiment_md = archive_dir / f"舆情综合日报_{date_short}.md"
        if sentiment_md.is_file() and sentiment_md.stat().st_size > 500 and not force:
            print(f"\n1️⃣  TrendSonar 综合舆情 — 已存在，跳过 ({sentiment_md.stat().st_size/1024:.1f} KB)")
            all_reports["sentiment_md"] = str(sentiment_md)
        else:
            print(f"\n1️⃣  TrendSonar 综合舆情日报...")
            trend_reports = _run_trendsonar_import(target_date, archive_dir)
            if trend_reports:
                all_reports.update(trend_reports)
            else:
                errors.append("TrendSonar: 生成失败（import + 子进程均失败）")

    # ── 2. 动力煤舆情 ──
    if run_coal:
        coal_md = archive_dir / f"动力煤舆情日报_{date_short}.md"
        if coal_md.is_file() and coal_md.stat().st_size > 500 and not force:
            print(f"\n2️⃣  动力煤舆情日报 — 已存在，跳过 ({coal_md.stat().st_size/1024:.1f} KB)")
            all_reports["coal_md"] = str(coal_md)
        else:
            print(f"\n2️⃣  动力煤舆情日报...")
            if _COAL_SCRIPT.is_file():
                coal_reports = _run_coal_import(target_date, archive_dir)
                if coal_reports:
                    all_reports.update(coal_reports)
                else:
                    errors.append("动力煤: 生成失败（import + 子进程均失败）")
            else:
                print(f"  ℹ️  动力煤脚本不存在 (需 Wind 终端)，跳过")
                errors.append("动力煤: 脚本不存在")

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


def remediate(target_date: str = None, output_dir: str = None) -> dict:
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
            trend_reports = _run_trendsonar_import(target_date or datetime.now().strftime('%Y-%m-%d'), archive_dir)
            if trend_reports:
                generated.extend(trend_reports.keys())
            else:
                still_missing.append(key)
        elif key == "coal_md":
            coal_reports = _run_coal_import(target_date or datetime.now().strftime('%Y-%m-%d'), archive_dir)
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

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description="舆情监控中枢")
    parser.add_argument('--date', type=str, default=None, help='目标日期 YYYY-MM-DD')
    parser.add_argument('--check', action='store_true', help='仅检查，不生成')
    parser.add_argument('--remediate', action='store_true', help='自动补全缺失报告')
    parser.add_argument('--force', action='store_true', help='强制重新生成')
    parser.add_argument('--no-coal', action='store_true', help='跳过多媒体')
    parser.add_argument('--no-trend', action='store_true', help='跳过 TrendSonar')
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
