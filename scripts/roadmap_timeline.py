"""roadmap_timeline.py — ROADMAP 甘特时间轴渲染器（月轴定位，块字符）。

设计铁律
--------
1. **单一事实源**：轨道窗口表只写在 `cairn/ROADMAP.md` 的 `TIMELINE-TRACKS` 标记块内，
   本脚本**不硬编码任何日期** ⇒ 不会出现「ROADMAP 一套口径 / 脚本一套口径」的双口径。
2. **fail-closed**：标记块缺失、字段数不符、日期非法、状态未知、轨道为空 ⇒ RC=2 并报错，
   **绝不静默通过**（空集合不得视为通过）。
3. **漂移门禁**：`--check` 重渲染并与已嵌入的甘特块逐字符比对，不一致 RC=1（可接 CI / pre-commit）。
4. 对齐不变量：月刻度域宽 / 刻度尺宽 / 条带域宽三者必须相等，否则 raise（防止甘特与轴错位而无人发现）。
5. `--check` 以**块内已记录的快照日**重渲染 ⇒ 只判「表 ↔ 图」漂移，不被当日时钟影响；`--write` 未给 `--today` 时用今日刷新快照日。
6. `done` 轨道取全填（与总览 HTML §1.2「提前关闭 → width:100%」同口径），不显示剩余窗口。

用法
----
    python scripts/roadmap_timeline.py                          # 渲染到 stdout
    python scripts/roadmap_timeline.py --today 2026-09-12       # 指定快照日（默认 now_bj）
    python scripts/roadmap_timeline.py --write                  # 回写 ROADMAP 甘特块
    python scripts/roadmap_timeline.py --write \
        --mirror docs/排期计划总览_20260912.md                  # 同时刷新镜像位（可重复）
    python scripts/roadmap_timeline.py --check                  # 漂移检查：RC=1 表示需刷新
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.datetime_utils import now_bj

ROADMAP_PATH = _PROJECT_ROOT / "cairn" / "ROADMAP.md"

TRACKS_BEGIN = "<!-- TIMELINE-TRACKS:BEGIN -->"
TRACKS_END = "<!-- TIMELINE-TRACKS:END -->"
GANTT_BEGIN = "<!-- TIMELINE:GANTT:BEGIN -->"
GANTT_END = "<!-- TIMELINE:GANTT:END -->"

COL_PER_MONTH = 4  # 每月列宽；月标签 "08月" 恰好 4 显示列
FIELD_COUNT = 6
STATUS_ICON = {"done": "✅", "run": "🔄", "wait": "⏳", "block": "⛔"}
GROUP_TITLE = {
    "total": "总览（全窗口 / 发布段）",
    "stage": "阶段带（Stage A~D）",
    "q4": "Q4 轨道",
    "y2027": "2027 段",
}
ELAPSED_CHAR = "█"
PENDING_CHAR = "░"


class TimelineError(RuntimeError):
    """数据或标记块不可用（fail-closed，不降级通过）。"""


@dataclass(frozen=True)
class Track:
    group: str
    name: str
    start: date
    end: date
    status: str
    note: str


def _disp_width(text: str) -> int:
    """终端显示宽度（CJK 宽字符按 2 算），用于对齐含中文的标签列。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _disp_width(text))


def _parse_date(raw: str, where: str) -> date:
    try:
        return date.fromisoformat(raw.strip())
    except ValueError as exc:
        raise TimelineError(f"{where}: 日期非法 {raw!r}（须 YYYY-MM-DD）") from exc


def extract_block(text: str, begin: str, end: str) -> str:
    """取出 begin/end 标记之间的正文；标记缺失或次序颠倒 ⇒ fail-closed。"""
    i = text.find(begin)
    if i < 0:
        raise TimelineError(f"缺少起始标记: {begin}")
    j = text.find(end, i + len(begin))
    if j < 0:
        raise TimelineError(f"缺少结束标记: {end}")
    return text[i + len(begin) : j].strip("\n")


def load_tracks(roadmap_text: str) -> list[Track]:
    """解析 TRACKS 表。字段: group|名称|起|止|状态|备注。"""
    body = extract_block(roadmap_text, TRACKS_BEGIN, TRACKS_END)
    tracks: list[Track] = []
    for lineno, raw in enumerate(body.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("```") or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != FIELD_COUNT:
            raise TimelineError(f"TRACKS 第 {lineno} 行字段数 {len(parts)} != {FIELD_COUNT}: {line}")
        group, name, start_raw, end_raw, status, note = parts
        if group not in GROUP_TITLE:
            raise TimelineError(f"TRACKS 第 {lineno} 行 group 未知: {group!r}（允许 {sorted(GROUP_TITLE)}）")
        if status not in STATUS_ICON:
            raise TimelineError(f"TRACKS 第 {lineno} 行状态未知: {status!r}（允许 {sorted(STATUS_ICON)}）")
        track = Track(group, name, _parse_date(start_raw, f"TRACKS 第 {lineno} 行 start"),
                      _parse_date(end_raw, f"TRACKS 第 {lineno} 行 end"), status, note)
        if track.end < track.start:
            raise TimelineError(f"TRACKS 第 {lineno} 行 end < start: {line}")
        tracks.append(track)
    if not tracks:
        raise TimelineError("TRACKS 块为空 —— fail-closed（空集合不得视为通过）")
    return tracks


def month_axis(axis_start: date, last: date) -> list[tuple[int, int]]:
    months: list[tuple[int, int]] = []
    year, month = axis_start.year, axis_start.month
    while (year, month) <= (last.year, last.month):
        months.append((year, month))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return months


def _col_of(day: date, axis_start: date) -> float:
    """日期 → 轴列位置（月内按日线性插值）。"""
    idx = (day.year - axis_start.year) * 12 + (day.month - axis_start.month)
    days_in_month = monthrange(day.year, day.month)[1]
    return idx * COL_PER_MONTH + (day.day - 1) / days_in_month * COL_PER_MONTH


def _bar_core(track: Track, today: date, axis_start: date, grid: int) -> str:
    start_col = int(round(_col_of(track.start, axis_start)))
    end_col = max(int(round(_col_of(track.end, axis_start))) + 1, start_col + 1)
    if track.status == "done" or today >= track.end:
        # done 行取全填：与总览 HTML §1.2「提前关闭 → width:100%」同口径
        elapsed = end_col - start_col
    elif today < track.start:
        elapsed = 0
    else:
        elapsed = int(round(_col_of(today, axis_start))) + 1 - start_col
    elapsed = max(0, min(elapsed, end_col - start_col))
    span = end_col - start_col
    core = ELAPSED_CHAR * elapsed + PENDING_CHAR * (span - elapsed)
    return (" " * start_col + core).ljust(grid)


def _range_label(track: Track, axis_start: date) -> str:
    def fmt(day: date) -> str:
        return f"{day:%m-%d}" if day.year == axis_start.year else f"{day:%Y-%m-%d}"

    return f"({fmt(track.start)}→{fmt(track.end)})"


def render_gantt(tracks: list[Track], today: date) -> str:
    first_start = min(t.start for t in tracks)
    last_end = max(t.end for t in tracks)
    axis_start = date(first_start.year, first_start.month, 1)
    months = month_axis(axis_start, last_end)
    grid = len(months) * COL_PER_MONTH
    label_width = max(_disp_width(t.name) for t in tracks) + 2

    year_row = [" "] * grid
    for idx, (year, month) in enumerate(months):
        if idx == 0 or month == 1:
            start = idx * COL_PER_MONTH
            for offset, char in enumerate(str(year)):
                if start + offset < grid:
                    year_row[start + offset] = char
    year_line = (_pad("", label_width) + "".join(year_row)).rstrip()
    labels = _pad("", label_width) + "".join(_pad(f"{m:02d}", COL_PER_MONTH) for _, m in months)
    ruler = _pad("", label_width)
    for i, (year, month) in enumerate(months):
        if i == 0:
            head = "├"
        elif month == 1 and year > axis_start.year:
            head = "╪"
        else:
            head = "┼"
        ruler += head + "─" * (COL_PER_MONTH - 1)
    ruler = ruler[:-1] + "┤"
    for text, name in ((labels, "月刻度"), (ruler, "刻度尺")):
        if _disp_width(text) != label_width + grid:
            raise TimelineError(f"{name}域宽 {_disp_width(text)} != 标签列 {label_width} + 条带 {grid}（对齐不变量）")
    labels = labels.rstrip()  # 末月填充空格属尾随空白，校验通过后再裁掉

    lines = [
        f"时间轴（甘特 · 月刻度 {axis_start:%Y-%m} ~ {last_end:%Y-%m} · 快照 {today:%Y-%m-%d}）",
        "",
        year_line,
        labels,
        ruler,
    ]
    if axis_start <= today <= last_end:
        col = int(round(_col_of(today, axis_start)))
        lines.append(_pad("", label_width) + " " * col + f"↑ 今日 {today:%m-%d}")
    lines.append("")

    seen: list[str] = []
    for track in tracks:
        if track.group not in seen:
            seen.append(track.group)
            lines.append(f"── {GROUP_TITLE[track.group]} ──")
        row = _pad(track.name, label_width) + _bar_core(track, today, axis_start, grid)
        suffix = f" {_range_label(track, axis_start)} {STATUS_ICON[track.status]}"
        if track.note:
            suffix += f" {track.note}"
        lines.append(row + suffix)
    return "\n".join(lines)


def gantt_body(rendered: str) -> str:
    """标记块之间的正文（含 ```text 围栏）。比较必须用**正文**，不能再套标记。"""
    return f"```text\n{rendered}\n```"


def _snapshot_date(body: str) -> date | None:
    match = re.search(r"快照 (\d{4}-\d{2}-\d{2})", body)
    return date.fromisoformat(match.group(1)) if match else None


def _write_text(path: Path, text: str) -> None:
    """按原文件的行尾/BOM 写回，避免整文件行尾翻转成假 diff。"""
    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    newline = "\r\n" if raw.count(b"\r\n") * 2 >= raw.count(b"\n") else "\n"
    data = (b"\xef\xbb\xbf" if bom else b"") + text.replace("\n", newline).encode("utf-8")
    path.write_bytes(data)


def _replace_block(text: str, begin: str, end: str, body: str) -> str:
    i = text.find(begin)
    if i < 0 or text.find(end, i + len(begin)) < 0:
        raise TimelineError(f"目标文件缺少标记块：{begin} / {end}")
    j = text.find(end, i + len(begin)) + len(end)
    return text[:i] + body + text[j:]


def main() -> int:
    parser = argparse.ArgumentParser(description="ROADMAP 甘特时间轴渲染器（月轴定位）")
    parser.add_argument("--roadmap", type=Path, default=ROADMAP_PATH, help="轨道表所在文件")
    parser.add_argument("--today", help="快照日 YYYY-MM-DD（默认本机北京日期）")
    parser.add_argument("--write", action="store_true", help="回写甘特块（ROADMAP + --mirror 文件）")
    parser.add_argument("--mirror", type=Path, action="append", default=[], help="镜像文件（可重复）")
    parser.add_argument("--check", action="store_true", help="漂移检查：不一致 RC=1")
    args = parser.parse_args()

    if args.write and args.check:
        print("--write 与 --check 互斥", file=sys.stderr)
        return 2

    try:
        roadmap_text = args.roadmap.read_text(encoding="utf-8-sig")
        tracks = load_tracks(roadmap_text)
        current_body = extract_block(roadmap_text, GANTT_BEGIN, GANTT_END)
        if args.today:
            snapshot = date.fromisoformat(args.today)
        elif args.check:
            # --check 以块内已记录的快照日为准 ⇒ 门禁只判「表 ↔ 图」漂移，不受当日时钟影响
            snapshot = _snapshot_date(current_body)
            if snapshot is None:
                raise TimelineError("甘特块内未记录快照日（形如「快照 2026-09-12」）⇒ fail-closed；请显式传 --today")
        else:
            snapshot = now_bj().date()
        rendered = render_gantt(tracks, snapshot)
        body = gantt_body(rendered)
    except (TimelineError, ValueError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2

    if args.check:
        if current_body == body:
            print(f"[OK] 甘特块与 TRACKS 表一致（{len(tracks)} 条轨道 · 快照 {snapshot:%Y-%m-%d}）")
            return 0
        print("[DRIFT] 甘特块与 TRACKS 表不一致 —— 跑 --write 刷新", file=sys.stderr)
        return 1

    if not args.write:
        print(rendered)
        return 0

    block = f"{GANTT_BEGIN}\n{body}\n{GANTT_END}"
    changed: list[str] = []
    targets = [args.roadmap, *args.mirror]
    for path in targets:
        text = path.read_text(encoding="utf-8-sig")
        try:
            updated = _replace_block(text, GANTT_BEGIN, GANTT_END, block)
        except TimelineError as exc:
            print(f"[FAIL] {path}: {exc}", file=sys.stderr)
            return 2
        if updated != text:
            _write_text(path, updated)
            changed.append(str(path))

    print(f"[OK] 甘特已渲染：{len(tracks)} 条轨道 · 快照 {snapshot:%Y-%m-%d}")
    print(f"     写入 {len(changed)}/{len(targets)} 个文件" + (f"：{', '.join(changed)}" if changed else "（内容无变化）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
