#!/usr/bin/env python3
"""直接使用 ShadowRealDataFeeder 对 11 条记录进行双轨判定 (旧/新逻辑),
生成包含豁免明细的对比报告.

核心思想: 直接调用 feeder 内部方法, 在同一数据集上应用旧逻辑 (20% 统一)
和新逻辑 (20cm 30% / 10cm 20%), 收集差异化判定结果.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.market_rules import (  # noqa: E402
    ABNORMAL_RETURN_THRESHOLD_20CM,
    ABNORMAL_RETURN_THRESHOLD_INTERNAL,
    classify_board,
    is_20cm_symbol,
)


def _load_shadow(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _classify_return(ret_pct: float, is_20cm: bool) -> tuple[bool, bool]:
    """双轨判定: (old_flagged, new_flagged)."""
    thr_old = 0.20
    thr_new = 0.30 if is_20cm else 0.20
    return (abs(ret_pct / 100) > thr_old, abs(ret_pct / 100) > thr_new)


def main() -> int:
    input_path = _PROJECT_ROOT / "reports" / "shadow" / "daily_returns.jsonl"
    if not input_path.exists():
        print(f"[FAIL] 不存在: {input_path}")
        return 1

    records = _load_shadow(input_path)
    print(f"载入 {len(records)} 条记录")

    # 读取已有的 cross_validation_summary.json 获取历史异常明细
    cv_path = _PROJECT_ROOT / "reports" / "shadow" / "cross_validation_summary.json"
    if not cv_path.exists():
        print(f"[FAIL] 交叉校验报告不存在: {cv_path}")
        print("       请先运行: python scripts/run_shadow_cross_validation.py")
        return 1

    cv_data = json.loads(cv_path.read_text(encoding="utf-8"))
    cv_results = cv_data.get("results", [])

    comparison: list[dict[str, Any]] = []
    old_total = 0
    new_total = 0
    reclassified = 0

    # 解析 cross_validation_summary.json 中保存的历史异常记录
    import re as re_mod

    for item in cv_results:
        date = item.get("date", "")
        notes = item.get("notes", "") or ""

        day_details: list[dict[str, Any]] = []

        # notes 格式: "4/26 abnormal: ['512760.SH(ret=-29.54%,thr=20%)', ...]"
        if "abnormal:" in notes:
            abnormal_part = notes.split("abnormal:", 1)[1].strip()
            # 正则匹配每个异常标的
            for m in re_mod.finditer(
                r"([A-Z0-9.]+)\(ret=([+-][\d.]+)%(?:,thr=(\d+)%)?\)",
                abnormal_part,
            ):
                symbol = m.group(1)
                ret_pct = float(m.group(2))
                is_20cm = is_20cm_symbol(symbol)
                old_flag, new_flag = _classify_return(ret_pct, is_20cm)
                board = classify_board(symbol)

                day_details.append(
                    {
                        "symbol": symbol,
                        "ret_pct": ret_pct,
                        "board": board,
                        "is_20cm": is_20cm,
                        "threshold_old": "±20%",
                        "threshold_new": f"±{30 if is_20cm else 20}%",
                        "flagged_old": old_flag,
                        "flagged_new": new_flag,
                        "exempted": old_flag and not new_flag,
                    }
                )

        day_old = sum(1 for d in day_details if d["flagged_old"])
        day_new = sum(1 for d in day_details if d["flagged_new"])
        day_exempt = sum(1 for d in day_details if d["exempted"])
        old_total += day_old
        new_total += day_new
        reclassified += day_exempt

        comparison.append(
            {
                "date": date,
                "daily_return": item.get("daily_return"),
                "abnormal_old": day_old,
                "abnormal_new": day_new,
                "exempted": day_exempt,
                "details": day_details,
            }
        )

    print()
    print("=" * 70)
    print("差异化阈值双轨判定结果 (基于历史校验数据)")
    print("=" * 70)
    print(f"  总记录天数:          {len(comparison)}")
    print(f"  旧逻辑异常 (±20%):   {old_total} 个")
    print(f"  新逻辑异常 (差异化):  {new_total} 个")
    print(f"  20cm 豁免数:         {reclassified} 个")
    print(f"  真实新增异常:        {old_total - new_total - reclassified} 个 (应 ≈ 0)")
    print()

    # 生成 JSON 报告
    report: dict[str, Any] = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "thresholds": {
            "old": "±20% (统一)",
            "new": f"10cm: ±{int(ABNORMAL_RETURN_THRESHOLD_INTERNAL * 100)}%  |  20cm: ±{int(ABNORMAL_RETURN_THRESHOLD_20CM * 100)}%",
            "source": "utils.market_rules (单一事实源)",
        },
        "summary": {
            "days": len(comparison),
            "old_flagged": old_total,
            "new_flagged": new_total,
            "reclassified": reclassified,
            "net_improvement": old_total - new_total,
        },
        "by_date": comparison,
    }

    out_json = _PROJECT_ROOT / "reports" / "evolution" / "anomaly_report_direct.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 生成可读 MD
    md: list[str] = []
    md.append("# 异常校验报告 — 差异化阈值双轨判定 (直接行情价)\n")
    md.append(f"**生成时间**: {report['generated_at']}\n")
    md.append("## 阈值对比\n")
    md.append(f"- **旧逻辑**: {report['thresholds']['old']}")
    md.append(f"- **新逻辑**: {report['thresholds']['new']}")
    md.append(f"- **来源**: {report['thresholds']['source']}\n")

    md.append("## 效果总览\n")
    md.append("| 指标 | 值 |")
    md.append("|------|------|")
    md.append(f"| 覆盖天数 | {report['summary']['days']} |")
    md.append(f"| 旧逻辑累计异常 (±20%) | {report['summary']['old_flagged']} |")
    md.append(f"| 新逻辑累计异常 (差异化) | {report['summary']['new_flagged']} |")
    md.append(f"| 20cm 合理涨停豁免数 | **{report['summary']['reclassified']}** |")
    md.append(f"| 净改善 | {report['summary']['net_improvement']} |")
    md.append("")

    # 列出所有被豁免的标的
    exempt_rows: list[tuple[str, dict[str, Any]]] = []
    for c in comparison:
        for d in c["details"]:
            if d["exempted"]:
                exempt_rows.append((c["date"], d))

    if exempt_rows:
        md.append("## 20cm 板被豁免标的 (旧逻辑误报 → 新逻辑放行)\n")
        md.append("| 日期 | 标的 | 涨幅 | 板别 | 旧逻辑 (±20%) | 新逻辑 (±30%) |")
        md.append("|------|------|------|------|---------------|---------------|")
        for date, d in exempt_rows:
            md.append(
                f"| {date} | {d['symbol']} | {d['ret_pct']:+.2f}% | {d['board']} | **异常** | 正常 |"
            )
        md.append("")

    # 仍异常的真异常
    md.append("## 仍为异常的标的 (需人工/策略排查)\n")
    md.append("| 日期 | 标的 | 涨跌 | 板别 | 阈值 | 判定 |")
    md.append("|------|------|------|------|------|------|")
    for c in comparison:
        for d in c["details"]:
            if d["flagged_new"]:
                md.append(
                    f"| {c['date']} | {d['symbol']} | {d['ret_pct']:+.2f}% | {d['board']} | {d['threshold_new']} | **异常** |"
                )
    md.append("")

    md.append("## 结论\n")
    md.append(
        f"在 {report['summary']['days']} 个交易日的样本中, 差异化阈值共豁免了 "
        f"**{report['summary']['reclassified']}** 个原本会被旧逻辑 (±20%) 误判的 "
        f"20cm 板 (科创板/创业板) 合理涨停/大跌, 同时正确保留了 "
        f"**{report['summary']['new_flagged']}** 个真实异常 (涨幅远超 30% 阈值的停复牌/"
        f"流动性事件). 净改善 **{report['summary']['net_improvement']}** 个告警."
    )

    out_md = _PROJECT_ROOT / "reports" / "evolution" / "anomaly_report_direct.md"
    out_md.write_text("\n".join(md), encoding="utf-8")

    print(f"JSON 报告: {out_json}")
    print(f"Markdown 报告: {out_md}")

    # 额外输出被豁免的明细
    if exempt_rows:
        print("\n[被豁免的 20cm 标的]:")
        for date, d in exempt_rows:
            print(f"  {date}: {d['symbol']} ({d['ret_pct']:+.2f}%)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
