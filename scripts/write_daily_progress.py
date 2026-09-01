"""write_daily_progress.py — 每日进展写入 cairn/LOG.md + 生成 progress 简报 (TODO_from_ROADMAP #7)

用法:
    python scripts/write_daily_progress.py --title "标题" --summary "摘要"
    python scripts/write_daily_progress.py --title "标题" --summary-file summary.md

LOG.md 条目插入到说明行之后 (反序, 最新在顶部).
同时生成 cairn/progress_YYYYMMDD.md 简报.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_LOG_PATH = _PROJECT_ROOT / "cairn" / "LOG.md"
_PROGRESS_DIR = _PROJECT_ROOT / "cairn"


def append_log_entry(title: str, summary: str, date: datetime | None = None) -> Path:
    """在 LOG.md 顶部插入新条目, 返回 LOG.md 路径."""
    if date is None:
        date = datetime.now()
    date_str = date.strftime("%Y-%m-%d")
    entry = f"## {date_str} · {title}\n\n{summary}\n\n"

    text = _LOG_PATH.read_text(encoding="utf-8-sig")
    has_bom = text.startswith("\ufeff")
    if has_bom:
        text = text[1:]

    lines = text.split("\n")
    insert_at = 0
    for i, line in enumerate(lines):
        if line.startswith("## "):
            insert_at = i
            break
    if insert_at == 0:
        insert_at = 4

    new_lines = lines[:insert_at] + entry.split("\n") + lines[insert_at:]
    new_text = "\n".join(new_lines)
    if has_bom:
        new_text = "\ufeff" + new_text
    _LOG_PATH.write_text(new_text, encoding="utf-8")
    return _LOG_PATH


def write_progress_brief(title: str, summary: str, date: datetime | None = None) -> Path:
    """生成 cairn/progress_YYYYMMDD.md 简报, 返回路径."""
    if date is None:
        date = datetime.now()
    date_str = date.strftime("%Y-%m-%d")
    path = _PROGRESS_DIR / f"progress_{date_str.replace('-', '')}.md"
    content = f"# 每日进展简报 {date_str}\n\n## {title}\n\n{summary}\n"
    path.write_text(content, encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="每日进展写入 cairn/LOG.md + progress 简报")
    parser.add_argument("--title", required=True, help="条目标题")
    parser.add_argument("--summary", help="摘要内容 (多行用 \\n 或 --summary-file)")
    parser.add_argument("--summary-file", type=Path, help="从文件读摘要")
    args = parser.parse_args()

    if args.summary_file:
        summary = args.summary_file.read_text(encoding="utf-8")
    elif args.summary:
        summary = args.summary.replace("\\n", "\n")
    else:
        print("需提供 --summary 或 --summary-file", file=sys.stderr)
        return 1

    log_path = append_log_entry(args.title, summary)
    progress_path = write_progress_brief(args.title, summary)
    print(f"LOG.md 已更新: {log_path}")
    print(f"简报已生成: {progress_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
