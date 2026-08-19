#!/usr/bin/env python3
"""质量看板渲染器 — 把 quality_snapshot.py --json 的输出变成可打开的 HTML 看板.

配套文档: docs/CODE_REVIEW_PROCESS.md §7.1
数据来源: python scripts/quality_snapshot.py --json

为什么单独写:
    CI 每周定时跑 quality_snapshot.py --json, 这份脚本把 JSON 渲染成
    一份零依赖、可离线打开的 HTML 看板, 上传为 artifact 供团队随时查看趋势。

用法:
    python scripts/quality_snapshot.py --json | python scripts/weekly_board.py --stdin
    python scripts/weekly_board.py snapshot.json --out quality-board.html

兼容 Python 3.8 (仅用标准库)。
"""
import argparse
import json
import sys
from html import escape

# 门禁目标, 必须与 scripts/quality_snapshot.py 的 GATES 保持一致
GATES = [
    ("未提交变更总数", "worktree_dirty", 100, "le"),
    ("git 中 venv/二进制", "git_pollution", 0, "le"),
    ("P0 区 print()", "p0_print", 60, "le"),
    ("P0 区静默异常", "p0_silent", 6, "le"),
    ("单文件最大行数", "max_lines", 3000, "le"),
]


def _compute_actual(data: dict) -> dict:
    wt = data.get("worktree", {})
    dirty = wt.get("deleted", 0) + wt.get("modified", 0) + wt.get("untracked", 0)
    p0 = data.get("p0", {})
    biggest = data.get("biggest_file", {}).get("lines", 0)
    return {
        "worktree_dirty": dirty,
        "git_pollution": data.get("git_pollution", 0),
        "p0_print": p0.get("print", 0),
        "p0_silent": p0.get("silent_except", 0),
        "max_lines": biggest,
    }


def _gate_rows(data: dict):
    actual = _compute_actual(data)
    rows = []
    passed = 0
    for label, key, target, op in GATES:
        val = actual.get(key, 0)
        ok = (val <= target) if op == "le" else (val >= target)
        passed += ok
        rows.append((label, val, target, op, ok))
    return rows, passed, len(GATES)


def _zone_rows(data: dict):
    """返回 (name, files, prints, density, exception, error, silent) 按文件数降序。"""
    zones = data.get("zones", {})
    rows = []
    for name, z in zones.items():
        files = z.get("files", 0)
        pr = z.get("print", 0)
        density = (pr / files) if files else 0.0
        rows.append((name, files, pr, density,
                     z.get("exception", 0), z.get("error", 0),
                     z.get("silent_except", 0)))
    rows.sort(key=lambda r: -r[1])
    return rows


def render(data: dict) -> str:
    ts = data.get("timestamp", "")
    disk = data.get("disk_py_files", 0)
    wt = data.get("worktree", {})
    dirty = wt.get("deleted", 0) + wt.get("modified", 0) + wt.get("untracked", 0)
    p0 = data.get("p0", {})
    gate_rows, passed, total = _gate_rows(data)
    zone_rows = _zone_rows(data)
    max_density = max((r[3] for r in zone_rows), default=1.0) or 1.0

    # 工作区卫生告警
    if dirty > 100:
        hygiene = (
            '<div class="alert alert-red">'
            f'⚠️ <b>工作区与版本控制脱节：{dirty} 个变更未提交</b><br>'
            'PR 无法反映真实改动，审查已失效。请按 docs/CODE_REVIEW_PROCESS.md §0 收敛。'
            '</div>'
        )
    else:
        hygiene = (
            '<div class="alert alert-green">'
            f'✅ 工作区已收敛（未提交变更 {dirty} ≤ 100），审查前提成立。'
            '</div>'
        )

    # 门禁 chips
    gate_html = []
    for label, val, target, op, ok in gate_rows:
        cls = "pass" if ok else "fail"
        op_txt = "&le;" if op == "le" else "&ge;"
        gate_html.append(
            f'<div class="chip {cls}"><div class="chip-label">{escape(label)}</div>'
            f'<div class="chip-val">{val}</div>'
            f'<div class="chip-target">目标 {op_txt} {target}</div></div>'
        )
    gate_html = "".join(gate_html)
    gate_summary = (
        '<div class="gate-summary {c}">阶段1 门禁达成 {p}/{t}'
        '{extra}</div>'
    ).format(
        c="pass" if passed == total else "fail",
        p=passed, t=total,
        extra="" if passed == total else " — 未达成项即下一阶段治理重点",
    )

    # 分区表 + 密度条
    zone_html = []
    for name, files, pr, density, exc, err, silent in zone_rows:
        width = int(min(density / max_density, 1.0) * 100)
        zone_html.append(
            f'<tr><td class="mono">{escape(name)}</td><td class="num">{files}</td>'
            f'<td class="num">{pr}</td>'
            f'<td><div class="bar"><div class="bar-fill" style="width:{width}%"></div>'
            f'</div><span class="num">{density:.1f}</span></td>'
            f'<td class="num">{exc}</td><td class="num">{err}</td>'
            f'<td class="num">{silent}</td></tr>'
        )
    zone_html = "".join(zone_html)

    biggest = data.get("biggest_file", {})
    p0_files = p0.get("files", 0)

    return """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>代码质量看板</title>
<style>
  :root {{ --bg:#f7f8fa; --card:#fff; --ink:#1f2329; --muted:#6b7280;
          --line:#e5e7eb; --red:#dc2626; --green:#16a34a; --amber:#d97706; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
          font-family:-apple-system,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;
          line-height:1.5; padding:24px; }}
  .wrap {{ max-width:960px; margin:0 auto; }}
  h1 {{ font-size:22px; margin:0 0 4px; }}
  .sub {{ color:var(--muted); font-size:13px; margin-bottom:20px; }}
  .alert {{ padding:14px 16px; border-radius:10px; margin-bottom:18px; font-size:14px; }}
  .alert-red {{ background:#fef2f2; border:1px solid #fecaca; color:#991b1b; }}
  .alert-green {{ background:#f0fdf4; border:1px solid #bbf7d0; color:#166534; }}
  .section-title {{ font-size:15px; font-weight:600; margin:24px 0 12px;
                   border-left:4px solid var(--amber); padding-left:10px; }}
  .chips {{ display:flex; flex-wrap:wrap; gap:10px; }}
  .chip {{ flex:1 1 150px; min-width:140px; background:var(--card);
          border:1px solid var(--line); border-radius:10px; padding:12px 14px; }}
  .chip.pass {{ border-left:4px solid var(--green); }}
  .chip.fail {{ border-left:4px solid var(--red); }}
  .chip-label {{ font-size:12px; color:var(--muted); }}
  .chip-val {{ font-size:24px; font-weight:700; margin:2px 0; }}
  .chip-target {{ font-size:11px; color:var(--muted); }}
  .gate-summary {{ margin-top:12px; padding:10px 14px; border-radius:8px;
                   font-weight:600; font-size:14px; }}
  .gate-summary.pass {{ background:#f0fdf4; color:#166534; }}
  .gate-summary.fail {{ background:#fffbeb; color:#92400e; }}
  table {{ width:100%; border-collapse:collapse; background:var(--card);
          border:1px solid var(--line); border-radius:10px; overflow:hidden;
          font-size:13px; }}
  th,td {{ padding:9px 10px; text-align:left; border-bottom:1px solid var(--line); }}
  th {{ background:#f9fafb; color:var(--muted); font-weight:600; }}
  td.num,th.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .mono {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }}
  .bar {{ display:inline-block; width:90px; height:10px; background:#eef2f7;
          border-radius:5px; vertical-align:middle; margin-right:8px; overflow:hidden; }}
  .bar-fill {{ height:100%; background:linear-gradient(90deg,#60a5fa,#2563eb); }}
  .cards {{ display:flex; gap:10px; flex-wrap:wrap; }}
  .card {{ flex:1 1 140px; background:var(--card); border:1px solid var(--line);
          border-radius:10px; padding:12px 14px; }}
  .card .k {{ font-size:12px; color:var(--muted); }}
  .card .v {{ font-size:20px; font-weight:700; margin-top:2px; }}
  .foot {{ margin-top:24px; color:var(--muted); font-size:12px; }}
  code {{ background:#eef2f7; padding:1px 5px; border-radius:4px; font-size:12px; }}
</style></head>
<body><div class="wrap">
  <h1>代码质量看板</h1>
  <div class="sub">生成时间 {ts} ｜ 磁盘实存业务 Python 文件 {disk} 个 ｜ 数据源 <code>quality_snapshot.py</code></div>

  {hygiene}

  <div class="section-title">阶段1 门禁达成</div>
  <div class="chips">{gate_html}</div>
  {gate_summary}

  <div class="section-title">日志规范分区对比</div>
  <table>
    <thead><tr><th>分区</th><th class="num">文件</th><th class="num">print()</th>
      <th class="num">密度</th><th class="num">exception()</th><th class="num">error()</th>
      <th class="num">静默异常</th></tr></thead>
    <tbody>{zone_html}</tbody>
  </table>

  <div class="section-title">P0 生产交易路径</div>
  <div class="cards">
    <div class="card"><div class="k">文件数</div><div class="v">{p0f}</div></div>
    <div class="card"><div class="k">print()</div><div class="v">{p0p}</div></div>
    <div class="card"><div class="k">静默异常</div><div class="v">{p0s}</div></div>
    <div class="card"><div class="k">exception()</div><div class="v">{p0e}</div></div>
    <div class="card"><div class="k">最大文件</div><div class="v" style="font-size:14px">{bf}</div>
      <div class="k">{bl} 行</div></div>
  </div>

  <div class="foot">依据 docs/CODE_REVIEW_STANDARD.md §5 ｜ 由 <code>scripts/weekly_board.py</code> 渲染 ｜ 每周一 10:30 (北京时间) 自动刷新</div>
</div></body></html>
""".format(
        ts=escape(ts), disk=disk, hygiene=hygiene, gate_html=gate_html,
        gate_summary=gate_summary, zone_html=zone_html,
        p0f=p0_files, p0p=p0.get("print", 0), p0s=p0.get("silent_except", 0),
        p0e=p0.get("exception", 0),
        bf=escape(biggest.get("path", "")), bl=biggest.get("lines", 0),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="质量看板 HTML 渲染")
    ap.add_argument("json_path", nargs="?", help="quality_snapshot.py --json 输出文件")
    ap.add_argument("--stdin", action="store_true", help="从 stdin 读取 JSON")
    ap.add_argument("--out", help="输出 HTML 路径 (缺省=stdout)")
    args = ap.parse_args()

    if args.stdin:
        raw = sys.stdin.read()
    elif args.json_path:
        with open(args.json_path, encoding="utf-8") as fh:
            raw = fh.read()
    else:
        # 缺省: 直接调用 quality_snapshot 的 collect()
        try:
            sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
            import quality_snapshot as qs  # type: ignore
            raw = json.dumps(qs.collect(), ensure_ascii=False)
        except Exception as exc:  # pragma: no cover
            print(f"[error] 无法获取快照数据: {exc}", file=sys.stderr)
            return 1

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"[error] JSON 解析失败: {exc}", file=sys.stderr)
        return 1

    html = render(data)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(html)
        print(f"[OK] 看板已写入 {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(html)
    return 0


if __name__ == "__main__":
    sys.exit(main())
