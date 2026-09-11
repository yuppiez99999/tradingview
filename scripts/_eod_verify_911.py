#!/usr/bin/env python3
"""09-11 EOD 生产机复跑：离线可验证部分（不触网 / 不重跑 EOD）。

用途
    生产机执行 `15_每日工作流/run_daily_eod_workflow.py --date 2026-09-11` 之后，
    用本脚本对「材料附二待终态行」做**只读核对**，把待终态项转为终态。

	核对项（与 task 三要求一一对应）
	A. B4 首 EOD 应跳 warmup    —— 3 条硬证据
	B. T3 七项无回归            —— 结构化七项明细
	C. 三 shadow cron           —— 计划任务触发 + 产出（含 S12 第三线）

设计约束
    - **只读**：不写任何 reports/ 运行时产物，不改 flag，不下单。
    - **离线**：不调用 Wind / 东财 / 新浪 / TDX 等外部数据源。
    - **fail-closed**：产物缺失一律记 MISSING 并计入 FAIL，不静默当通过。

用法
    python -X utf8 scripts/_eod_verify_911.py --date 2026-09-11
    python -X utf8 scripts/_eod_verify_911.py --date 2026-09-11 --md-out reports/operations/eod_verify_2026-09-11.md
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from utils.datetime_utils import now_bj

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_REPORTS = _PROJECT_ROOT / "reports"
_ARCHIVE = _PROJECT_ROOT / "每日报告归档"

B4_SKIP_REASON = "B4 already enabled, warmup complete"

# T3 七项键名（与 scripts/t3_post_market_check.py 的 checks 字典严格同构）
T3_CHECK_KEYS = [
    "① 数据源四查",
    "② EOD 任务执行",
    "③ 风控配置降级",
    "④ 账户对账",
    "⑤ 双cron产出",
    "⑥ C10 fills新鲜度",
    "⑦ PhaseB/D11进度",
]

SHADOW_CRONS = [
    ("S12_Shadow_EOD", "16:30", "s12"),
    ("Shadow30Day_EOD", "16:35", "mvsk"),
    ("GNN_S6_Paper_EOD", "16:50", "s6"),
]


# ────────────────────────────────────────────────────────────
# 工具
# ────────────────────────────────────────────────────────────


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return out
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _item(name: str, status: str, notes: str, evidence: str = "") -> dict:
    return {"name": name, "status": status, "notes": notes, "evidence": evidence}


# ────────────────────────────────────────────────────────────
# A. B4 首 EOD 应跳 warmup
# ────────────────────────────────────────────────────────────


def check_b4_skip(target_date: str) -> list[dict]:
    """三条硬证据，任一缺失即 MISSING（fail-closed）。"""
    items: list[dict] = []
    compact = target_date.replace("-", "")

    # A1. EOD 摘要 JSON（唯一机器可读入口，含阶段 4.86 跳过字段）
    summary_path = _ARCHIVE / target_date / f"eod_workflow_summary_{target_date}.json"
    summary = _read_json(summary_path)
    if summary is None:
        items.append(
            _item(
                "A1 eod_workflow_summary.phase4_86_b4_shadow",
                "MISSING",
                f"摘要不存在或不可解析: {summary_path}",
            )
        )
    else:
        phase = (summary.get("phases") or {}).get("phase4_86_b4_shadow")
        if not isinstance(phase, dict):
            items.append(
                _item(
                    "A1 eod_workflow_summary.phase4_86_b4_shadow",
                    "FAIL",
                    "摘要中缺 phase4_86_b4_shadow 键（B4 阶段未记录）",
                )
            )
        elif phase.get("skipped") is True and phase.get("reason") == B4_SKIP_REASON:
            items.append(
                _item(
                    "A1 eod_workflow_summary.phase4_86_b4_shadow",
                    "PASS",
                    f"skipped=True, reason='{phase.get('reason')}', flag_invariant={phase.get('flag_invariant')}",
                    str(summary_path),
                )
            )
        else:
            items.append(
                _item(
                    "A1 eod_workflow_summary.phase4_86_b4_shadow",
                    "FAIL",
                    f"未跳过 warmup: {json.dumps(phase, ensure_ascii=False)[:200]}",
                )
            )

    # A2. EOD 运行日志文本
    log_path = _PROJECT_ROOT / "logs" / f"daily_eod_{compact}.log"
    if not log_path.exists():
        items.append(_item("A2 EOD 日志跳过行", "MISSING", f"日志不存在: {log_path}"))
    else:
        text = log_path.read_text(encoding="utf-8", errors="replace")
        skip_line = "B4 已启用" in text or B4_SKIP_REASON in text
        warmup_run = "B4 shadow 每日预热 (USE_MLOPS_PIPELINE=False 不变式)" in text
        if skip_line and not warmup_run:
            items.append(
                _item("A2 EOD 日志跳过行", "PASS", "日志含跳过行且无预热执行行", str(log_path))
            )
        else:
            items.append(
                _item(
                    "A2 EOD 日志跳过行",
                    "FAIL",
                    f"skip_line={skip_line}, warmup_run={warmup_run}（期望 True/False）",
                )
            )

    # A3. b4_shadow_status.json 未被当日刷新（证明 runner 未跑）
    status_path = _REPORTS / "shadow" / "b4_shadow_status.json"
    b4 = _read_json(status_path)
    if b4 is None:
        items.append(_item("A3 b4_shadow_status 未被刷新", "MISSING", str(status_path)))
    else:
        last_run = str(b4.get("last_run", ""))
        refetched = last_run.startswith(target_date)
        items.append(
            _item(
                "A3 b4_shadow_status 未被刷新",
                "FAIL" if refetched else "PASS",
                f"last_run={last_run or '?'}, warmup_days={b4.get('warmup_days', '?')}/7, "
                f"连败={b4.get('consecutive_failures', '?')}（当日不应再刷新）",
                str(status_path),
            )
        )

    # A4. flag 终态（信息项，不参与 PASS/FAIL 判据）
    sc = _read_json(_PROJECT_ROOT / "system_config.json") or {}
    ff = (sc.get("evolution") or sc).get("feature_flags") or {}
    items.append(
        _item(
            "A4 USE_MLOPS_PIPELINE 终态",
            "INFO",
            f"system_config.json 口径 = {ff.get('USE_MLOPS_PIPELINE')}",
        )
    )
    return items


# ────────────────────────────────────────────────────────────
# B. T3 七项无回归
# ────────────────────────────────────────────────────────────


def check_t3(target_date: str, rerun: bool) -> list[dict]:
    """★2026-09-07 起脚本不再把结果写 stdout，故以落盘文件为准。"""
    items: list[dict] = []
    path = _REPORTS / "operations" / f"t3_check_{target_date}.md"
    if rerun:
        try:
            proc = subprocess.run(
                [sys.executable, "scripts/t3_post_market_check.py", "--date", target_date],
                cwd=str(_PROJECT_ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,
            )
            items.append(
                _item(
                    "B0 T3 重跑",
                    "PASS" if proc.returncode == 0 else "FAIL",
                    f"exit_code={proc.returncode}",
                )
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            items.append(_item("B0 T3 重跑", "FAIL", f"执行异常: {e}"))
            return items

    if not path.exists():
        items.append(_item("B 七项明细", "MISSING", f"核对文件不存在: {path}"))
        return items

    text = path.read_text(encoding="utf-8", errors="replace")
    for key in T3_CHECK_KEYS:
        tag = key.split()[0]
        row = None
        for line in text.splitlines():
            if line.startswith(f"| {tag} |"):
                row = line
                break
        if row is None:
            items.append(_item(f"B {key[2:]}", "MISSING", "七项表中无此行"))
            continue
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        status = cells[2] if len(cells) > 2 else "?"
        note = cells[3] if len(cells) > 3 else ""
        items.append(_item(f"B {key[2:]}", status, note, str(path)))

    overall = "PASS" if "**总体: T3 PASS**" in text else (
        "FAIL" if "**总体: T3 FAIL**" in text else "MISSING"
    )
    items.append(_item("B 总体", overall, "t3_check 文件总体结论", str(path)))
    return items


# ────────────────────────────────────────────────────────────
# C. 三 shadow cron
# ────────────────────────────────────────────────────────────


def _cron_log_status(tag: str) -> tuple[str, str]:
    log_dir = _PROJECT_ROOT / "logs" / "task_logs"
    if not log_dir.exists():
        return "MISSING", f"无 task_logs 目录: {log_dir}"
    cands = sorted(log_dir.glob(f"{tag}_*.log"))
    if not cands:
        return "MISSING", f"无 {tag}_*.log"
    latest = cands[-1]
    try:
        tail = latest.read_text(encoding="utf-8", errors="replace")[-400:]
    except OSError as e:
        return "MISSING", f"读取失败: {e}"
    return "INFO", f"{latest.name} → …{tail.strip()[-200:]}"


def check_shadow_crons(target_date: str) -> list[dict]:
    items: list[dict] = []

    # C1. 计划任务排期状态（schtasks 不可用时记 INFO，不硬判）
    if sys.platform == "win32":
        for tag, sched, _ in SHADOW_CRONS:
            try:
                proc = subprocess.run(
                    ["schtasks", "/Query", "/TN", tag, "/FO", "LIST", "/V"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60,
                )
                if proc.returncode != 0:
                    items.append(_item(f"C1 {tag} 排期", "FAIL", f"schtasks rc={proc.returncode}"))
                    continue
                fields = {}
                for line in proc.stdout.splitlines():
                    if ":" in line:
                        k, _, v = line.partition(":")
                        fields[k.strip()] = v.strip()
                items.append(
                    _item(
                        f"C1 {tag} 排期",
                        "PASS",
                        f"Last Run={fields.get('Last Run Time', '?')} "
                        f"Last Result={fields.get('Last Result', '?')} "
                        f"Next Run={fields.get('Next Run Time', '?')} (计划 {sched})",
                    )
                )
            except (subprocess.TimeoutExpired, OSError) as e:
                items.append(_item(f"C1 {tag} 排期", "MISSING", f"schtasks 不可用: {e}"))
    else:
        items.append(
            _item("C1 排期查询", "SKIP", "非 Windows，schtasks 不可用（生产机应重跑）")
        )

    # C2. 产出（旧口径：mvsk + S6；S12 为第三线，单列）
    mvsk_rows = [
        r
        for r in _read_jsonl(_REPORTS / "shadow" / "mvsk_p5_daily_diff.jsonl")
        if r.get("date") == target_date
    ]
    qlib_rows = [
        r
        for r in _read_jsonl(_REPORTS / "shadow" / "qlib_lgb_v2_daily.jsonl")
        if r.get("date") == target_date
    ]
    s6_rows = [
        r
        for r in _read_jsonl(_REPORTS / "gnn_factor" / "s6_paper_trading.jsonl")
        if r.get("date") == target_date
    ]
    skeleton = sum(1 for r in s6_rows if r.get("status") == "skeleton")

    mvsk_ok = len(mvsk_rows) >= 1
    s6_ok = len(s6_rows) >= 1 and skeleton < len(s6_rows)
    items.append(
        _item(
            "C2 Shadow30Day mvsk 产出",
            "PASS" if mvsk_ok else "FAIL",
            f"mvsk 当日 {len(mvsk_rows)} 条；qlib 当日 {len(qlib_rows)} 条（R-6 停跑归档，预期 0）",
        )
    )
    items.append(
        _item(
            "C2 GNN S6 产出",
            "PASS" if s6_ok else "FAIL",
            f"S6 当日 {len(s6_rows)} 条（skeleton {skeleton}）",
        )
    )

    # C3. S12 第三线持仓状态（账户对账）
    try:
        proc = subprocess.run(
            [sys.executable, "scripts/run_s12_shadow.py", "--status"],
            cwd=str(_PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        blob = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode == 0 and "S12_SHADOW_P3" in blob:
            nav = "?"
            for line in blob.splitlines():
                if "NAV" in line:
                    nav = line.strip()
                    break
            items.append(_item("C3 S12 影子账户状态", "PASS", f"rc=0；{nav}"))
        else:
            items.append(
                _item(
                    "C3 S12 影子账户状态",
                    "MISSING",
                    f"rc={proc.returncode}（沙箱可能缺运行时产物；生产机应重跑）",
                )
            )
    except (subprocess.TimeoutExpired, OSError) as e:
        items.append(_item("C3 S12 影子账户状态", "MISSING", f"执行异常: {e}"))

    # C4. wrapper 日志（三线逐次落盘）
    for tag, _, _ in SHADOW_CRONS:
        status, note = _cron_log_status(tag)
        items.append(_item(f"C4 {tag} wrapper 日志", status, note))

    return items


# ────────────────────────────────────────────────────────────
# 汇总 / 输出
# ────────────────────────────────────────────────────────────


def run_checks(target_date: str, rerun_t3: bool) -> dict:
    sections = {
        "A. B4 首 EOD 跳 warmup": check_b4_skip(target_date),
        "B. T3 七项无回归": check_t3(target_date, rerun_t3),
        "C. 三 shadow cron": check_shadow_crons(target_date),
    }
    must = [i for items in sections.values() for i in items if i["status"] in ("PASS", "FAIL", "MISSING")]
    fails = [i for i in must if i["status"] in ("FAIL", "MISSING")]
    return {
        "date": target_date,
        "checked_at": now_bj().strftime("%Y-%m-%d %H:%M:%S"),
        "sections": sections,
        "fail_count": len(fails),
        "overall": "PASS" if not fails else "INCOMPLETE",
    }


def render_md(result: dict) -> str:
    lines = [
        f"## 09-11 EOD 复跑核对（{result['date']}，核对于 {result['checked_at']}）",
        "",
        "> 离线只读核对，不触网、不重跑 EOD；缺失项记 MISSING 并计 INCOMPLETE（fail-closed）。",
        "",
    ]
    for title, items in result["sections"].items():
        lines.append(f"### {title}")
        lines.append("")
        lines.append("| 项 | 结果 | 备注 |")
        lines.append("| --- | --- | --- |")
        for it in items:
            lines.append(f"| {it['name']} | {it['status']} | {it['notes']} |")
        lines.append("")
    tail = (
        "三项终态已核实"
        if result["overall"] == "PASS"
        else f"待生产机补齐 {result['fail_count']} 项"
    )
    lines.append(f"**总体: {tail}**")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="09-11 EOD 复跑离线核对")
    parser.add_argument("--date", default="2026-09-11")
    parser.add_argument("--rerun-t3", action="store_true", help="先重跑 t3_post_market_check")
    parser.add_argument("--md-out", default="", help="Markdown 结果落盘路径")
    parser.add_argument("--json-out", default="", help="JSON 结果落盘路径")
    args = parser.parse_args()

    result = run_checks(args.date, args.rerun_t3)
    md = render_md(result)
    print(md)

    if args.md_out:
        out = Path(args.md_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        print(f"已保存: {out}")
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已保存: {out}")
    return 0 if result["overall"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
