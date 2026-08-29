"""MMR 看板更新: 将 judge confirmed/dismissed 结论写入 CODE_REVIEW_BACKLOG.md

用法:
    python scripts/mmr_update_backlog.py \
        --input reports/mmr_reviews/judge/pr_42.json \
        --backlog docs/CODE_REVIEW_BACKLOG.md \
        --pr-number 42

confirmed -> 在 "2. 存量问题清单" 表格追加条目
dismissed -> 在 "4. 豁免登记" 表格追加条目 (标记 false_positive)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


def _format_backlog_row(finding: dict, pr_number: int, verdict_info: dict) -> str:
    fid = finding.get("id", "?")
    location = finding.get("location", "?")
    severity = finding.get("severity", "medium")
    title = finding.get("title", "")[:40]
    today = datetime.now().strftime("%Y-%m-%d")
    return (
        f"| MMR-{fid} | {location} | {title} (PR#{pr_number}) | "
        f"{severity} | mmr-judge | {today} | 待修复 |"
    )


def _format_exemption_row(finding: dict, pr_number: int, verdict_info: dict) -> str:

    location = finding.get("location", "?")
    reason = (
        f"mmr judge {verdict_info.get('confirmed_count', 0)}/"
        f"{verdict_info.get('total_judges', 0)} dismissed (ocr false positive)"
    )
    today = datetime.now().strftime("%Y-%m-%d")
    return f"| {location} | {reason} | mmr-judge-bot | {today} |"


def update_backlog(
    mmr_result: dict,
    backlog_path: Path,
    pr_number: int,
) -> tuple[int, int]:
    findings = mmr_result.get("findings", [])

    confirmed_rows: list[str] = []
    dismissed_rows: list[str] = []

    all_discoveries = mmr_result.get("discoveries", findings)

    for v in mmr_result.get("verdicts", []):
        fid = v.get("finding_id", "")
        vinfo = v
        matching = [f for f in all_discoveries if f.get("id") == fid]
        finding = (
            matching[0] if matching else {"id": fid, "location": "?", "severity": "?"}
        )
        if v.get("verdict") == "confirmed":
            confirmed_rows.append(_format_backlog_row(finding, pr_number, vinfo))
        else:
            dismissed_rows.append(_format_exemption_row(finding, pr_number, vinfo))

    if not confirmed_rows and not dismissed_rows:
        return 0, 0

    content = backlog_path.read_text(encoding="utf-8")
    lines = content.split("\n")
    out_lines: list[str] = []
    in_section_2 = False
    in_section_4 = False
    section_2_inserted = False
    section_4_inserted = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## 2."):
            in_section_2 = True
            in_section_4 = False
        elif stripped.startswith("## 3."):
            in_section_2 = False
        elif stripped.startswith("## 4."):
            in_section_4 = True
            in_section_2 = False
        elif stripped.startswith("## 5.") or (
            stripped.startswith("## ") and in_section_4
        ):
            in_section_4 = False

        out_lines.append(line)

        if (
            in_section_2
            and stripped.startswith("|")
            and "状态" in stripped
            and not section_2_inserted
        ):
            for row in confirmed_rows:
                out_lines.append(row)
            section_2_inserted = True

        if (
            in_section_4
            and stripped.startswith("|")
            and "日期" in stripped
            and not section_4_inserted
        ):
            for row in dismissed_rows:
                out_lines.append(row)
            section_4_inserted = True

    if not section_2_inserted and confirmed_rows:
        for row in confirmed_rows:
            out_lines.append(row)

    if not section_4_inserted and dismissed_rows:
        for row in dismissed_rows:
            out_lines.append(row)

    backlog_path.write_text("\n".join(out_lines), encoding="utf-8")
    return len(confirmed_rows), len(dismissed_rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="MMR 看板更新")
    parser.add_argument("--input", required=True, help="mmr judge 结果 JSON")
    parser.add_argument("--backlog", required=True, help="CODE_REVIEW_BACKLOG.md 路径")
    parser.add_argument("--pr-number", type=int, required=True, help="PR 编号")
    args = parser.parse_args()

    in_path = Path(args.input)
    if not in_path.exists():
        print(f"[ERROR] 输入不存在: {in_path}", file=sys.stderr)
        return 1

    backlog_path = Path(args.backlog)
    if not backlog_path.exists():
        print(f"[ERROR] 看板不存在: {backlog_path}", file=sys.stderr)
        return 1

    with open(in_path, encoding="utf-8") as f:
        mmr_result = json.load(f)

    confirmed, dismissed = update_backlog(mmr_result, backlog_path, args.pr_number)
    print(
        f"[OK] 看板更新: {confirmed} confirmed + {dismissed} dismissed -> {backlog_path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
