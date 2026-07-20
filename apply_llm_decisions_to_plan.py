# -*- coding: utf-8 -*-
"""
LLM 决策灌入次日计划
====================

读取前一日 daily_pnl_report，把 LLM 决策自动写入次日 trade_plan：
- 期货对冲升级（IF 手数）
- Put 尾部保护（510050、510300）
- 建仓顺序调整（防御优先、科技设限）

用法:
    python apply_llm_decisions_to_plan.py [report_date] [plan_date]

默认 report_date = 昨日，plan_date = 下一个交易日
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPORTS_DIR = SCRIPT_DIR / "v7.5_institutional" / "reports"
PLAN_DIR = SCRIPT_DIR / "v7.5_institutional" / "trade_plans"


def _prev_trading_day(d: datetime) -> datetime:
    """简单回退到最近一个周一至周五（不含节假日）。"""
    day = d - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def apply_llm_decisions(report_date: str, plan_date: str) -> Path:
    report_path = REPORTS_DIR / f"daily_pnl_report_{report_date}.json"
    plan_path = PLAN_DIR / f"trade_plan_{plan_date.replace('-', '')}.json"
    if not report_path.exists():
        raise FileNotFoundError(f"报告不存在: {report_path}")
    if not plan_path.exists():
        raise FileNotFoundError(f"计划不存在: {plan_path}")

    report = _load_json(report_path)
    plan = _load_json(plan_path)

    ai_recs = report.get("ai_recommendations", [])
    rec_text = "\n".join(ai_recs)

    # === 1) 期货对冲升级 ===
    if "IF空头" in rec_text or "增加期货" in rec_text or "提升Beta对冲效率" in rec_text:
        plan.setdefault("llm_overrides", {})
        plan["llm_overrides"]["futures_if_contracts"] = 5
        plan["llm_overrides"]["applied_by"] = f"LLM daily_pnl_report_{report_date}"

    # === 2) 新增 Put 保护 ===
    if "510050 Put" in rec_text or "Put" in rec_text:
        plan.setdefault("llm_overrides", {})
        puts = plan["llm_overrides"].get("put_protection", [])
        if not any(p.get("code") == "510050" for p in puts):
            puts.append({
                "code": "510050",
                "direction": "BUY_PUT",
                "contracts": 10,
                "strike_basis": "OTM_5pct",
                "note": "LLM decision"
            })
        if not any(p.get("code") == "510300" for p in puts):
            puts.append({
                "code": "510300",
                "direction": "BUY_PUT",
                "contracts": 5,
                "strike_basis": "OTM_5pct",
                "note": "LLM decision"
            })
        plan["llm_overrides"]["put_protection"] = puts

    # === 3) 建仓顺序调整 ===
    if "优先" in rec_text or "监控" in rec_text or "调整" in rec_text:
        plan.setdefault("llm_overrides", {})
        plan["llm_overrides"]["build_sequence"] = "优先防御底仓，科技成长设限"

    # === 元数据记录 ===
    plan.setdefault("metadata", {})
    plan["metadata"]["llm_adjustments"] = {
        "applied_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": f"daily_pnl_report_{report_date}",
        "adjustments": ai_recs
    }

    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return plan_path


def main() -> int:
    if len(sys.argv) > 1:
        report_date = sys.argv[1]
    else:
        today = datetime.now()
        report_date = _prev_trading_day(today).strftime("%Y-%m-%d")

    if len(sys.argv) > 2:
        plan_date = sys.argv[2]
    else:
        today = datetime.now()
        plan_date = today.strftime("%Y-%m-%d")

    try:
        out = apply_llm_decisions(report_date, plan_date)
        print(f"[OK] LLM决策已写入: {out}")
        return 0
    except Exception as e:
        print(f"[FAIL] 失败: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
