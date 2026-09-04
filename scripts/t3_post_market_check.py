#!/usr/bin/env python
"""T3 验收盘后核对脚本 — 自动执行 4 项核对并输出 Markdown 结论

用法:
    python scripts/t3_post_market_check.py              # 核对今日
    python scripts/t3_post_market_check.py --date 2026-09-05  # 核对指定日期

输出: reports/operations/t3_check_<date>.md (同时打印到 stdout)
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_REPORTS = _PROJECT_ROOT / "reports"
_OPS_DIR = _REPORTS / "operations"


def _run_s12_status() -> dict:
    """查询 S12_SHADOW_P3 账户状态，返回解析后的关键字段。"""
    result = subprocess.run(
        [sys.executable, "scripts/run_s12_shadow.py", "--status"],
        capture_output=True, cwd=_PROJECT_ROOT,
    )
    output = (result.stdout or b"").decode("utf-8", errors="replace") + (
        result.stderr or b""
    ).decode("utf-8", errors="replace")
    info = {"raw_available": False}
    if "S12_SHADOW_P3" in output:
        info["raw_available"] = True
        m = re.search(r"NAV:\s*([\d.]+)", output)
        if m:
            info["nav"] = float(m.group(1))
        m = re.search(r"搴撴鏁?\s*\+?([\d.]+)%", output) or re.search(r"\+([\d.]+)%", output)
        if m:
            info["cum_return_pct"] = float(m.group(1))
        m = re.search(r"鍐嶅钩琛?\s*(\d+)\s*娆?", output) or re.search(r"rebalance.*?(\d+)", output, re.I)
        if m:
            info["rebalance_count"] = int(m.group(1))
        if "fail-fast" in output and ("鏈\ue744鍙" in output or "not triggered" in output.lower()):
            info["fail_fast"] = False
        if "wind_mcp" in output:
            info["data_source"] = "wind_mcp"
        m = re.search(r"\{'518880':\s*([\d.]+).*?'511260':\s*([\d.]+).*?'512890':\s*([\d.]+)\}", output)
        if m:
            info["weights"] = {
                "518880": float(m.group(1)),
                "511260": float(m.group(2)),
                "512890": float(m.group(3)),
            }
    return info


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _degradation_entries(target_date: str) -> list[dict]:
    """提取指定日期的 degradation_log 条目。"""
    log_file = _REPORTS / "degradation_log.jsonl"
    if not log_file.exists():
        return []
    entries = []
    for line in log_file.read_text(encoding="utf-8").strip().splitlines():
        try:
            entry = json.loads(line)
            if entry.get("ts", "").startswith(target_date):
                entries.append(entry)
        except json.JSONDecodeError:
            continue
    return entries


def _check_data_source(target_date: str) -> dict:
    """① 数据源四查"""
    s12 = _run_s12_status()
    date_compact = target_date.replace("-", "")
    alpha_signals = list(_REPORTS.glob(f"pipeline/alpha_signals_{date_compact}*.json"))
    eod_guard = _REPORTS / f"eod_guard_report_{target_date}.json"
    health = _REPORTS / f"health_score/health_score_{target_date}.json"
    deg_entries = _degradation_entries(target_date)
    data_source_deg = [e for e in deg_entries if e.get("scope") not in ("config_manager",)]

    passed = (
        s12.get("raw_available")
        and len(alpha_signals) > 0
        and eod_guard.exists()
        and health.exists()
        and len(data_source_deg) == 0
    )
    notes = (
        f"实际源 {s12.get('data_source', '?')}; "
        f"alpha_signals {len(alpha_signals)} 个; "
        f"eod_guard {'存在' if eod_guard.exists() else '缺失'}; "
        f"health_score {'存在' if health.exists() else '缺失'}; "
        f"degradation 今日 {len(deg_entries)} 条"
        f"（数据源类 {len(data_source_deg)} 条）"
    )
    return {"pass": passed, "notes": notes}


def _check_eod_tasks(target_date: str) -> dict:
    """② EOD 任务执行"""
    health = _load_json(_REPORTS / f"health_score/health_score_{target_date}.json")
    gate = _load_json(_REPORTS / f"gate/gate_daily_{target_date}.json")
    fills_path = _REPORTS / f"fills/fills_{target_date}.jsonl"

    health_score = health.get("total_score", "?") if health else "?"
    health_status = health.get("status", "?") if health else "?"
    gate_all_ok = gate.get("all_ok", "?") if gate else "?"
    fills_exists = fills_path.exists()

    passed = health is not None and gate is not None
    notes = (
        f"health_score {health_score} {health_status}; "
        f"gate all_ok={gate_all_ok}; "
        f"fills {'存在' if fills_exists else '缺失'}"
    )
    return {"pass": passed, "notes": notes}


def _check_risk_degradation(target_date: str) -> dict:
    """③ 风控配置降级"""
    deg_entries = _degradation_entries(target_date)
    known_scopes = {"config_manager"}
    unknown = [e for e in deg_entries if e.get("scope") not in known_scopes]
    trade_exec = [e for e in deg_entries if "trade_execution" in e.get("key", "")]

    eod_guard = _load_json(_REPORTS / f"eod_guard_report_{target_date}.json")
    kill_switch_ok = True
    if eod_guard:
        ks = eod_guard.get("guards", {}).get("kill_switch", {})
        kill_switch_ok = ks.get("success", False) and ks.get("result", {}).get("level", 1) == 0

    passed = len(unknown) == 0 and len(trade_exec) == 0 and kill_switch_ok
    notes = (
        f"今日 {len(deg_entries)} 条全已知 (config_manager); "
        f"trade_execution×{len(trade_exec)}; "
        f"kill_switch {'正常' if kill_switch_ok else '异常'}"
    )
    return {"pass": passed, "notes": notes}


def _check_account() -> dict:
    """④ 账户对账"""
    s12 = _run_s12_status()
    nav = s12.get("nav")
    weights = s12.get("weights")
    rebalance = s12.get("rebalance_count", "?")
    fail_fast = s12.get("fail_fast", "?")

    equal_weight = (
        weights
        and all(abs(w - 1/3) < 0.001 for w in weights.values())
    )
    passed = nav is not None and nav > 1.0 and equal_weight
    notes = (
        f"nav={nav}; "
        f"权重 {'等权' if equal_weight else '偏离'} {weights}; "
        f"再平衡 {rebalance} 次; "
        f"fail-fast {fail_fast}"
    )
    return {"pass": passed, "notes": notes}


def main():
    parser = argparse.ArgumentParser(description="T3 盘后核对")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    args = parser.parse_args()
    target_date = args.date

    checks = {
        "① 数据源四查": _check_data_source(target_date),
        "② EOD 任务执行": _check_eod_tasks(target_date),
        "③ 风控配置降级": _check_risk_degradation(target_date),
        "④ 账户对账": _check_account(),
    }

    all_pass = all(c["pass"] for c in checks.values())
    now = datetime.now().strftime("%H:%M")

    lines = [
        f"## 盘后核对（T3 / {target_date} {now} 自动生成）",
        "",
        "| # | 检查项 | 结果 | 备注 |",
        "| --- | --- | --- | --- |",
    ]
    for name, result in checks.items():
        status = "PASS" if result["pass"] else "FAIL"
        lines.append(f"| {name.split()[0]} | {name.split()[1]} | {status} | {result['notes']} |")
    lines.append("")
    lines.append(f"**总体: {'T3 PASS' if all_pass else 'T3 FAIL'}**")
    lines.append("")

    output = "\n".join(lines)
    print(output)

    _OPS_DIR.mkdir(parents=True, exist_ok=True)
    out_file = _OPS_DIR / f"t3_check_{target_date}.md"
    out_file.write_text(output, encoding="utf-8")
    print(f"\n已保存到: {out_file}")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())