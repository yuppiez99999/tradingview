"""统一入口：论文调研 / 文件导入 / 机构研报决策。

用法:
  python mobius_addon/research_cli.py --topic "low volatility anomaly" --max-papers 10
  python mobius_addon/research_cli.py --file "C:/papers/xxx.pdf"
  python mobius_addon/research_cli.py --report "C:/研报/xxx.pdf"

零侵入：所有产物写入 mobius_addon/output/，不修改任何现有代码/配置。
"""

import argparse
import json
import os

from _common import ensure_env, get_logger

ensure_env()


def _safe_name(s: str) -> str:
    s = (s or "untitled").strip().replace("\n", " ")
    return (
        "".join(c if c.isalnum() or c in "-_ " else "_" for c in s)[:60] or "untitled"
    )


def run_topic(topic: str, max_papers: int, out_root: str) -> None:
    from agents.extractor import extract_paper  # noqa: WPS433
    from agents.retriever import retrieve  # noqa: WPS433
    from agents.synthesizer import (
        build_paper_card,
        scan_factor_coverage,
    )  # noqa: WPS433

    logger = get_logger()
    logger.info("检索论文: %s", topic)
    papers = retrieve(topic, max_papers)
    logger.info("检索到 %d 篇", len(papers))
    cards_dir = os.path.join(out_root, "research_cards")
    os.makedirs(cards_dir, exist_ok=True)
    count = 0
    for paper in papers:
        raw = paper.get("abstract", "")
        insight = extract_paper(raw, title_hint=paper.get("title", ""))
        insight.update(paper)
        text = insight.get("factor_logic", "") + insight.get("key_findings", "")
        insight["coverage_status"] = scan_factor_coverage(text)
        md = build_paper_card(insight)
        base = _safe_name(paper.get("title", paper.get("paper_id", "")))
        with open(os.path.join(cards_dir, base + ".md"), "w", encoding="utf-8") as fh:
            fh.write(md)
        with open(os.path.join(cards_dir, base + ".json"), "w", encoding="utf-8") as fh:
            json.dump(insight, fh, ensure_ascii=False, indent=2)
        count += 1
        logger.info("已生成卡片: %s", base)


def run_file(path: str, out_root: str) -> None:
    from agents.extractor import extract_paper  # noqa: WPS433
    from agents.file_loader import load_text  # noqa: WPS433
    from agents.synthesizer import (
        build_paper_card,
        scan_factor_coverage,
    )  # noqa: WPS433

    logger = get_logger()
    raw = load_text(path)
    logger.info("读取文件: %s (%d 字符)", path, len(raw))
    insight = extract_paper(raw[:12000], title_hint=os.path.basename(path))
    insight["paper_id"] = "file:" + os.path.basename(path)
    insight["source"] = "local_file"
    text = insight.get("factor_logic", "") + insight.get("key_findings", "")
    insight["coverage_status"] = scan_factor_coverage(text)
    cards_dir = os.path.join(out_root, "research_cards")
    os.makedirs(cards_dir, exist_ok=True)
    md = build_paper_card(insight)
    base = _safe_name(os.path.basename(path))
    with open(os.path.join(cards_dir, base + ".md"), "w", encoding="utf-8") as fh:
        fh.write(md)
    with open(os.path.join(cards_dir, base + ".json"), "w", encoding="utf-8") as fh:
        json.dump(insight, fh, ensure_ascii=False, indent=2)


def run_report(path: str, out_root: str) -> None:
    from agents.file_loader import load_text  # noqa: WPS433
    from agents.report_extractor import extract_report  # noqa: WPS433
    from agents.synthesizer import build_report_card  # noqa: WPS433
    from agents.system_judge import judge  # noqa: WPS433

    logger = get_logger()
    raw = load_text(path)
    logger.info("读取研报: %s (%d 字符)", path, len(raw))
    report = extract_report(raw)
    logger.info(
        "研报标的: %s %s", report.get("stock_code", ""), report.get("stock_name", "")
    )
    decision = judge(report)
    md = build_report_card(report, decision)
    cards_dir = os.path.join(out_root, "decision_cards")
    os.makedirs(cards_dir, exist_ok=True)
    base = _safe_name(report.get("stock_code", "") or os.path.basename(path))
    with open(
        os.path.join(cards_dir, f"decision_card_{base}.md"), "w", encoding="utf-8"
    ) as fh:
        fh.write(md)
    with open(
        os.path.join(cards_dir, f"decision_card_{base}.json"), "w", encoding="utf-8"
    ) as fh:
        json.dump(
            {"report": report, "decision": decision},
            fh,
            ensure_ascii=False,
            indent=2,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mobius 外挂: 论文调研/文件导入/研报决策"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--topic", help="联网论文调研关键词")
    group.add_argument("--file", help="本地文件导入(pdf/txt/md) 产出分析卡片")
    group.add_argument("--report", help="机构研报 PDF 导入 -> 系统判断决策卡")
    parser.add_argument("--max-papers", type=int, default=10)
    parser.add_argument(
        "--out", default=None, help="输出目录(默认 mobius_addon/output)"
    )
    args = parser.parse_args()

    out_root = args.out or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "output"
    )
    os.makedirs(out_root, exist_ok=True)

    if args.topic:
        run_topic(args.topic, args.max_papers, out_root)
    elif args.file:
        run_file(args.file, out_root)
    elif args.report:
        run_report(args.report, out_root)


if __name__ == "__main__":
    main()
