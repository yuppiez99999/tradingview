#!/usr/bin/env python3
"""
EOD 收盘审核脚本 — 审核数据质量 + 盘中决策情况
==================================================

在 EOD 工作流归档阶段后调用，审核:
  1. 数据质量: PnL 报告的 data_source_health 状态 (HEALTHY/FALLBACK_HEAVY/NOSIGNAL_*)
  2. 盘中决策: 扫描盘中LLM决策报告，统计成功/失败次数
  3. 数据源告警: 检查是否有数据源告警文件 + 连续失败计数
  4. 交易计划可信度: 基于数据质量判断交易计划是否可信

输出: 每日报告归档/YYYY-MM-DD/eod_audit_report.md
退出码: 0=审核通过, 1=审核不通过(数据不可信或盘中决策全失败)

用法:
  python run_eod_audit.py                       # 审核今日
  python run_eod_audit.py --date 2026-08-27     # 审核指定日期
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from utils.datetime_utils import now_bj

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
ARCHIVE_DIR = PROJECT_ROOT / "每日报告归档"


def _audit_data_quality(today_dir: Path) -> dict:
    """审核数据质量 — 读取 PnL 报告的 data_source_health"""
    result = {"status": "unknown", "details": {}}
    for f in today_dir.glob("daily_pnl_report_*.json"):
        try:
            report = json.loads(f.read_text(encoding="utf-8"))
            health = report.get("meta", {}).get("data_source_health", {})
            result["status"] = health.get("status", "unknown")
            result["details"] = {
                "fallback_ratio": health.get("fallback_ratio", 0),
                "no_data_ratio": health.get("no_data_ratio", 0),
                "real_ratio": health.get("real_ratio", 0),
                "fallback_count": health.get("fallback_count", 0),
                "no_data_count": health.get("no_data_count", 0),
                "real_count": health.get("real_count", 0),
                "total_positions": health.get("total_positions", 0),
            }
            break
        except Exception:
            continue
    return result


def _audit_intraday_decisions(today_dir: Path) -> dict:
    """审核盘中决策 — 扫描盘中LLM决策报告，统计成功/失败"""
    decisions = []
    for f in sorted(today_dir.glob("盘中LLM决策_*.md")):
        content = f.read_text(encoding="utf-8")
        is_fail = "失败" in content or "⛔" in content
        decisions.append(
            {
                "file": f.name,
                "time": f.stem.replace("盘中LLM决策_", ""),
                "success": not is_fail,
            }
        )
    success_count = sum(1 for d in decisions if d["success"])
    fail_count = sum(1 for d in decisions if not d["success"])
    return {
        "total": len(decisions),
        "success": success_count,
        "fail": fail_count,
        "all_failed": fail_count > 0 and success_count == 0,
        "decisions": decisions,
    }


def _audit_data_alerts(today_dir: Path) -> dict:
    """检查数据源告警文件 + 连续失败计数"""
    alert_file = today_dir / "数据源告警.md"
    counter_file = today_dir / "_intraday_fail_count.txt"
    fail_count = 0
    if counter_file.exists():
        try:
            fail_count = int(counter_file.read_text().strip())
        except Exception:
            pass
    return {
        "alert_triggered": alert_file.exists(),
        "alert_file": alert_file.name if alert_file.exists() else "",
        "consecutive_fail_count": fail_count,
    }


def _audit_trade_plan(today_dir: Path, data_quality: dict) -> dict:
    """审核交易计划可信度"""
    plan_files = list(today_dir.glob("trade_plan_*.json"))
    has_plan = len(plan_files) > 0
    dq = data_quality["status"]
    trustworthy = dq in ("HEALTHY", "PARTIAL_SNAPSHOT")
    return {
        "has_plan": has_plan,
        "plan_files": [f.name for f in plan_files],
        "data_quality": dq,
        "trustworthy": trustworthy,
        "action": "可自动执行" if trustworthy else "需人工确认后方可执行",
    }


def generate_audit_report(
    report_date: str,
    data_quality: dict,
    intraday: dict,
    alerts: dict,
    trade_plan: dict,
) -> tuple[str, bool]:
    """生成审核报告 Markdown，返回 (报告内容, 审核是否通过)"""
    ts = now_bj().strftime("%Y-%m-%d %H:%M:%S")
    dq = data_quality["status"]
    dq_details = data_quality["details"]

    # 审核是否通过
    audit_pass = (
        dq in ("HEALTHY", "PARTIAL_SNAPSHOT")
        and not intraday["all_failed"]
        and not alerts["alert_triggered"]
    )

    # 数据质量评级
    if dq in ("HEALTHY",):
        dq_emoji = "✅"
        dq_label = "数据# 数据可信"
    elif dq == "PARTIAL_SNAPSHOT":
        dq_emoji = "🟡"
        dq_label = "部分快照"
    elif dq == "FALLBACK_HEAVY":
        dq_emoji = "⚠️⚠️"
        dq_label = "严重降级 — 报告不可信"
    elif dq == "NOSIGNAL_PARTIAL":
        dq_emoji = "⚠️"
        dq_label = "部分数据缺失"
    elif dq == "NOSIGNAL_MAJORITY":
        dq_emoji = "⛔"
        dq_label = "多数数据不可用 — 报告不可信"
    else:
        dq_emoji = "❓"
        dq_label = "未知"

    # 盘中决策评级
    if intraday["total"] == 0:
        id_emoji = "➖"
        id_label = "无盘中决策记录"
    elif intraday["all_failed"]:
        id_emoji = "⛔"
        id_label = f"全部失败 ({intraday['fail']}/{intraday['total']})"
    elif intraday["fail"] > 0:
        id_emoji = "⚠️"
        id_label = f"部分失败 ({intraday['success']}成功/{intraday['fail']}失败)"
    else:
        id_emoji = "✅"
        id_label = f"全部成功 ({intraday['success']})"

    lines = [
        f"# EOD 收盘审核报告 — {report_date}\n",
        f"\n**审核时间**: {ts}",
        f"**审核结果**: {'✅ 通过' if audit_pass else '❌ 不通过'}\n",
        "---\n",
        "## 一、数据质量审核\n",
        "| 项目 | 值 |",
        "|------|-----|",
        f"| 状态 | {dq_emoji} {dq} — {dq_label} |",
        f"| 实时行情 | {dq_details.get('real_count', 0)}/{dq_details.get('total_positions', 0)} ({dq_details.get('real_ratio', 0):.0%}) |",  # noqa: E501
        f"| 兜底价格 | {dq_details.get('fallback_count', 0)}/{dq_details.get('total_positions', 0)} ({dq_details.get('fallback_ratio', 0):.0%}) |",  # noqa: E501
        f"| 无数据 | {dq_details.get('no_data_count', 0)}/{dq_details.get('total_positions', 0)} ({dq_details.get('no_data_ratio', 0):.0%}) |\n",  # noqa: E501
    ]

    if dq in ("FALLBACK_HEAVY", "NOSIGNAL_MAJORITY", "NOSIGNAL_PARTIAL"):
        lines.append("> ⚠️ **盈亏数据非真实交易结果，交易计划需人工确认**\n")

    lines += [
        "---\n",
        "## 二、盘中决策审核\n",
        "| 项目 | 值 |",
        "|------|-----|",
        f"| 总次数 | {intraday['total']} |",
        f"| 成功 | {intraday['success']} |",
        f"| 失败 | {intraday['fail']} |",
        f"| 结果 | {id_emoji} {id_label} |\n",
    ]

    if intraday["fail"] > 0:
        lines.append("### 失败详情\n")
        for d in intraday["decisions"]:
            if not d["success"]:
                lines.append(f"- {d['time']} ❌ {d['file']}")
        lines.append("")

    lines += [
        "---\n",
        "## 三、数据源告警\n",
        "| 项目 | 值 |",
        "|------|-----|",
        f"| 告警触发 | {'⚠️ 是 — ' + alerts['alert_file'] if alerts['alert_triggered'] else '否'} |",
        f"| 连续失败次数 | {alerts['consecutive_fail_count']} |\n",
    ]

    if alerts["alert_triggered"]:
        lines.append("> ⚠️ **数据源告警已触发，请检查数据源连接状态**\n")

    lines += [
        "---\n",
        "## 四、交易计划可信度\n",
        "| 项目 | 值 |",
        "|------|-----|",
        f"| 计划文件 | {', '.join(trade_plan['plan_files']) or '无'} |",
        f"| 数据质量 | {trade_plan['data_quality']} |",
        f"| 可信度 | {'✅ 可信' if trade_plan['trustworthy'] else '⚠️ 不可信'} |",
        f"| 执行建议 | **{trade_plan['action']}** |\n",
        "---\n",
        f"*EOD 收盘审核 v1.0 | {ts}*",
    ]

    return "\n".join(lines), audit_pass


def run_audit(report_date: str) -> bool:
    """执行 EOD 收盘审核"""
    today_dir = ARCHIVE_DIR / report_date
    if not today_dir.exists():
        print(f"[ERROR] 报告目录不存在: {today_dir}")  # noqa: T201
        return False

    data_quality = _audit_data_quality(today_dir)
    intraday = _audit_intraday_decisions(today_dir)
    alerts = _audit_data_alerts(today_dir)
    trade_plan = _audit_trade_plan(today_dir, data_quality)

    report, audit_pass = generate_audit_report(
        report_date, data_quality, intraday, alerts, trade_plan
    )

    out_file = today_dir / "eod_audit_report.md"
    out_file.write_text(report, encoding="utf-8")
    print(f"[{'OK' if audit_pass else 'WARN'}] 审核报告: {out_file}")  # noqa: T201
    print(f"[INFO] 审核结果: {'通过' if audit_pass else '不通过'}")  # noqa: T201
    return audit_pass


def main() -> None:
    parser = argparse.ArgumentParser(description="EOD 收盘审核")
    parser.add_argument(
        "--date",
        type=str,
        default=now_bj().strftime("%Y-%m-%d"),
        help="审核日期 (默认今日)",
    )
    args = parser.parse_args()
    ok = run_audit(args.date)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
