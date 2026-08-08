#!/usr/bin/env python3
"""
研究内容蒸馏离线批处理脚本
==========================

创建日期: 2026-07-26
集成批次: GitHub 周榜热门项目深度集成 (第二批) - 阶段 4
来源: cangjie-skill (RIA--TV++ 内容蒸馏方法论)

职责:
- 扫描 data/research_inputs/ 目录下的研究内容
  - reports/*.pdf       研报 PDF
  - earnings/*.txt      业绩会纪要 (文件名约定: earnings_<symbol>_YYYYQn.txt)
  - books/*.md          财经书籍章节 (Markdown)
  - news/*.json         新闻批量 JSON (数组格式, 每条 {title, content, symbol})
- 调用 ResearchDistiller 蒸馏
- 输出到 data/distilled_signals/distilled_signals_YYYYMMDD.json
- 失败不阻断 (异常捕获 + 日志)

部署:
- Windows 任务计划: QuantResearchDistill_06AM, 每交易日 06:00 触发
- 必须在 QuantWorkflow_07AM (07:00) 之前完成
- 由 daily_workflow Phase 5 (phase_signal) 在 07:00 加载应用

用法:
    python scripts/distill_research_batch.py
    python scripts/distill_research_batch.py --date 2026-07-27
    python scripts/distill_research_batch.py --input-dir /path/to/inputs --verbose
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

# 将项目根目录加入 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def setup_logging(verbose: bool = False) -> None:
    """配置日志格式"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def scan_research_inputs(input_dir: Path) -> dict[str, list[Path]]:
    """扫描研究输入目录, 返回 {category: [paths]}

    Args:
        input_dir: data/research_inputs/ 目录

    Returns:
        {"reports": [pdf], "earnings": [txt], "books": [md], "news": [json]}
    """
    categories: dict[str, list[Path]] = {
        "reports": [],
        "earnings": [],
        "books": [],
        "news": [],
    }
    if not input_dir.exists():
        return categories

    # reports/*.pdf
    reports_dir = input_dir / "reports"
    if reports_dir.exists():
        categories["reports"] = sorted(reports_dir.glob("*.pdf"))

    # earnings/*.txt (文件名约定: earnings_<symbol>_YYYYQn.txt)
    earnings_dir = input_dir / "earnings"
    if earnings_dir.exists():
        categories["earnings"] = sorted(earnings_dir.glob("*.txt"))

    # books/*.md
    books_dir = input_dir / "books"
    if books_dir.exists():
        categories["books"] = sorted(books_dir.glob("*.md"))

    # news/*.json (数组格式)
    news_dir = input_dir / "news"
    if news_dir.exists():
        categories["news"] = sorted(news_dir.glob("*.json"))

    return categories


def parse_symbol_from_earnings_filename(filename: str) -> str:
    """从业绩会纪要文件名解析标的代码

    文件名约定: earnings_<symbol>_YYYYQn.txt
    示例:
        earnings_600276.SH_2026Q2.txt  → "600276.SH"
        earnings_600276_2026Q2.txt     → "600276"
        earnings_000001.SZ_2026H1.txt  → "000001.SZ"

    Args:
        filename: 文件名 (含或不含扩展名)

    Returns:
        标的代码字符串 (解析失败返回空字符串)
    """
    stem = Path(filename).stem
    parts = stem.split("_")
    if len(parts) >= 2 and parts[0] == "earnings":
        return parts[1]
    return ""


def main() -> int:
    """主入口

    Returns:
        0 成功 / 1 失败 (失败不阻断 daily_workflow, 仅返回错误码)
    """
    parser = argparse.ArgumentParser(
        description="研究内容蒸馏离线批处理 (cangjie-skill RIA--TV++ 量化版)",
    )
    parser.add_argument(
        "--date", type=str, default=None,
        help="交易日期 YYYY-MM-DD 或 YYYYMMDD, 默认今日",
    )
    parser.add_argument(
        "--input-dir", type=str, default=None,
        help="研究输入目录, 默认 data/research_inputs/",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="显示 DEBUG 日志",
    )
    args = parser.parse_args()

    setup_logging(args.verbose)
    logger = logging.getLogger("distill_research_batch")

    trade_date = args.date or datetime.now().strftime("%Y-%m-%d")
    input_dir = (
        Path(args.input_dir) if args.input_dir
        else (_PROJECT_ROOT / "data" / "research_inputs")
    )

    logger.info("=" * 70)
    logger.info("研究内容蒸馏离线批处理 (RIA--TV++ 量化版)")
    logger.info(f"  trade_date: {trade_date}")
    logger.info(f"  input_dir:  {input_dir}")
    logger.info(f"  started_at: {datetime.now().isoformat()}")
    logger.info("=" * 70)

    # 延迟导入, 避免日志配置前导入触发默认日志
    try:
        from utils.research_distiller import DistilledSignal, ResearchDistiller
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        logger.info(f"[FATAL] 无法导入 ResearchDistiller: {e}")
        logger.error("导入 ResearchDistiller 失败: %s", e, exc_info=True)
        return 1

    try:
        distiller = ResearchDistiller()
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        logger.info(f"[FATAL] ResearchDistiller 初始化失败: {e}")
        logger.error("ResearchDistiller 初始化失败: %s", e, exc_info=True)
        return 1

    print(f"\n[1/4] ResearchDistiller 初始化: LLM={distiller.llm_available}, "
          f"名称词典={len(distiller._name_to_symbol)}")

    # 扫描输入
    categories = scan_research_inputs(input_dir)
    total_files = sum(len(v) for v in categories.values())
    logger.info(f"\n[2/4] 扫描 {input_dir}: 共 {total_files} 个文件")
    for cat, paths in categories.items():
        if paths:
            logger.info(f"  - {cat}: {len(paths)} 个文件")

    if total_files == 0:
        logger.info("\n[3/4] 无输入文件, 跳过蒸馏")
        # 即使无输入, 也保存一个空快照 (方便上游判断已运行)
        try:
            empty_path = distiller.save_daily_snapshot([], trade_date)
            if str(empty_path):
                logger.info(f"\n[4/4] 空快照已保存: {empty_path}")
            else:
                logger.info("\n[4/4] 空快照保存失败 (非致命, daily_workflow 会 fail-closed)")
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("保存空快照失败 (非致命): %s", e)
        return 0

    all_signals: list[DistilledSignal] = []

    # 蒸馏研报
    for pdf_path in categories["reports"]:
        try:
            logger.info("蒸馏研报: %s", pdf_path.name)
            sigs = distiller.distill_report(pdf_path)
            all_signals.extend(sigs)
            logger.info("  → 提取 %d 个信号", len(sigs))
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("研报蒸馏失败 (%s): %s", pdf_path, e)

    # 蒸馏业绩会纪要 (从文件名解析 symbol)
    for txt_path in categories["earnings"]:
        try:
            transcript = txt_path.read_text(encoding="utf-8", errors="ignore")
            symbol = parse_symbol_from_earnings_filename(txt_path.name)
            if not symbol:
                logger.warning("业绩会纪要文件名无法解析 symbol, 跳过: %s", txt_path.name)
                continue
            logger.info("蒸馏业绩会: %s (symbol=%s)", txt_path.name, symbol)
            sigs = distiller.distill_earnings_call(transcript, symbol)
            all_signals.extend(sigs)
            logger.info("  → 提取 %d 个信号", len(sigs))
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("业绩会蒸馏失败 (%s): %s", txt_path, e)

    # 蒸馏书籍章节
    for md_path in categories["books"]:
        try:
            logger.info("蒸馏书籍章节: %s", md_path.name)
            sigs = distiller.distill_book_chapter(md_path)
            all_signals.extend(sigs)
            logger.info("  → 提取 %d 个信号", len(sigs))
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("书籍蒸馏失败 (%s): %s", md_path, e)

    # 蒸馏新闻批量
    for json_path in categories["news"]:
        try:
            with open(json_path, encoding="utf-8") as f:
                news_items = json.load(f)
            if not isinstance(news_items, list):
                logger.warning("新闻文件格式错误 (应为数组): %s", json_path)
                continue
            logger.info("蒸馏新闻批量: %s (%d 条)", json_path.name, len(news_items))
            sigs = distiller.distill_news_batch(news_items)
            all_signals.extend(sigs)
            logger.info("  → 提取 %d 个信号", len(sigs))
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("新闻蒸馏失败 (%s): %s", json_path, e)

    # 保存快照
    logger.info(f"\n[3/4] 蒸馏完成, 共 {len(all_signals)} 个信号")
    logger.info("\n[4/4] 保存每日快照...")
    try:
        output_path = distiller.save_daily_snapshot(all_signals, trade_date)
        if str(output_path) and output_path.exists():
            logger.info(f"  ✓ 已保存: {output_path}")
            signal_map = distiller.to_signal_map(all_signals)
            logger.info(f"  ✓ 聚合后标的数: {len(signal_map)}")
            if signal_map:
                sample = dict(list(signal_map.items())[:5])
                logger.info(f"  ✓ 信号示例: {sample}")
            # 输出蒸馏器统计
            status = distiller.get_status()
            logger.info(f"  ✓ 统计: {status['stats']}")
        else:
            logger.info("  ✗ 保存失败, 详见日志")
            logger.error("保存快照失败 (output_path=%s)", output_path)
            return 1
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        logger.error("保存快照异常: %s", e, exc_info=True)
        return 1

    logger.info(f"\n完成: {datetime.now().isoformat()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
