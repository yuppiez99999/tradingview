"""
执行复盘器 (Execution Reviewer)
================================
v8.6 补齐模块: 核对 AI 建议与收盘盈亏, 生成复盘报告

闭环链路:
    盈亏报告 → AI 建议 → 对冲成交 → 复盘报告 → 动态风控调整

用法:
    from execution_reviewer import run_execution_review
    run_execution_review(trade_date="2026-08-19", auto_closed_loop=True)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.datetime_utils import now_bj

logger = logging.getLogger("execution_reviewer")

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
REPORTS_DIR = BASE_DIR / "reports"
V83_REPORTS_DIR = REPORTS_DIR
ARCHIVE_DIR = PROJECT_ROOT / "每日报告归档"
TRADE_INSTRUCTIONS_DIR = PROJECT_ROOT / "trade_instructions"


def _load_pnl_report(trade_date: str) -> dict[str, Any]:
    """加载当日盈亏报告 (多路径兼容)"""
    candidates = [
        V83_REPORTS_DIR / f"daily_pnl_report_{trade_date}.json",
        ARCHIVE_DIR / trade_date / f"daily_pnl_report_{trade_date}.json",
    ]
    for p in candidates:
        if p.exists():
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    return {}


def _load_hedge_fill(trade_date: str) -> dict[str, Any]:
    """加载对冲成交记录"""
    p = V83_REPORTS_DIR / f"hedge_execution_fill_{trade_date}.json"
    if p.exists():
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _load_ai_gate(trade_date: str) -> dict[str, Any]:
    """加载 AI 决策门记录"""
    candidates = [
        TRADE_INSTRUCTIONS_DIR / f"ai_gate_{trade_date}.json",
    ]
    for p in candidates:
        if p.exists():
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    return {}


def run_execution_review(
    trade_date: str, auto_closed_loop: bool = False
) -> dict[str, Any]:
    """执行复盘: 核对 AI 建议与收盘盈亏

    Args:
        trade_date: 交易日期 YYYY-MM-DD
        auto_closed_loop: 是否在自动闭环模式下运行

    Returns:
        复盘报告字典
    """
    logger.info("=" * 60)
    logger.info(
        f"执行复盘启动 | trade_date={trade_date} | auto_closed_loop={auto_closed_loop}"
    )
    logger.info("=" * 60)

    pnl_report = _load_pnl_report(trade_date)
    hedge_fill = _load_hedge_fill(trade_date)
    ai_gate = _load_ai_gate(trade_date)

    # 提取盈亏摘要
    pnl_summary = pnl_report.get("portfolio_pnl", {}).get("summary", {})
    net_perf = pnl_report.get("net_performance", {})
    risk_metrics = pnl_report.get("risk_metrics", {})

    total_pnl = float(pnl_summary.get("total_pnl", 0))
    total_pnl_pct = float(pnl_summary.get("total_pnl_pct", 0))
    net_pnl = float(net_perf.get("net_pnl", total_pnl))
    net_pnl_pct = float(net_perf.get("net_pnl_pct", total_pnl_pct))
    hedge_pnl = float(net_perf.get("hedge_pnl", 0))
    hedge_cost = float(net_perf.get("hedge_cost", 0))

    # 提取对冲成交
    hedge_orders = hedge_fill.get("orders", [])
    hedge_enabled = bool(hedge_fill.get("hedge_enabled", False))
    portfolio_beta = float(hedge_fill.get("portfolio_beta", 0))

    # 提取 AI 决策
    ai_approved = []
    ai_rejected = []
    if ai_gate:
        fusion = ai_gate.get("fusion_result", {})
        ai_approved = fusion.get("approved_instructions", [])
        ai_rejected = fusion.get("rejected_instructions", [])

    # 核对 AI 命中率
    ai_hit_count = 0
    ai_total_count = len(ai_approved)
    if ai_total_count > 0 and pnl_report:
        details = pnl_report.get("portfolio_pnl", {}).get("details", [])
        detail_map = {d.get("code", ""): d for d in details}
        for inst in ai_approved:
            code = inst.get("full_code", inst.get("code", ""))
            detail = detail_map.get(code)
            if detail:
                inst_pnl = float(detail.get("pnl", 0))
                inst_action = inst.get("action", "")
                if (
                    inst_action == "BUY"
                    and inst_pnl > 0
                    or inst_action == "SELL"
                    and inst_pnl < 0
                ):
                    ai_hit_count += 1
    ai_hit_rate = ai_hit_count / ai_total_count if ai_total_count > 0 else 0.0

    # 执行缺口 (对冲计划 vs 实际成交)
    hedge_plan = pnl_report.get("hedge_position_plan", {})
    planned_tools = len(hedge_plan.get("details", []))
    executed_tools = len(hedge_orders)
    execution_gaps = max(0, planned_tools - executed_tools)

    # 风险事件
    risk_events = []
    max_dd = float(risk_metrics.get("max_drawdown_pct", 0))
    if max_dd < -0.15:
        risk_events.append(
            {"level": "HIGH", "event": f"最大回撤 {max_dd:.1%} 超过 -15%"}
        )
    beta_exp = float(risk_metrics.get("beta_exposure", 0))
    if beta_exp > 1.0:
        risk_events.append(
            {"level": "MEDIUM", "event": f"Beta 暴露 {beta_exp:.2f} > 1.0"}
        )
    if not hedge_enabled and portfolio_beta > 0.7:
        risk_events.append(
            {"level": "HIGH", "event": f"对冲未启用但 Beta={portfolio_beta:.2f} 高敞口"}
        )

    # 综合评分 (0=低风险, 1=高风险)
    pnl_score = min(1.0, max(0.0, -total_pnl_pct / 0.03))  # 亏损 3% 满分
    dd_score = min(1.0, max(0.0, -max_dd / 0.25))  # 回撤 25% 满分
    gap_score = min(1.0, execution_gaps / 5.0)  # 5 个缺口满分
    ai_miss_score = 1.0 - ai_hit_rate if ai_total_count > 0 else 0.0
    risk_score = (
        pnl_score * 0.35 + dd_score * 0.30 + gap_score * 0.20 + ai_miss_score * 0.15
    )

    if risk_score > 0.6:
        risk_level = "HIGH"
    elif risk_score > 0.3:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    review_report = {
        "trade_date": trade_date,
        "generated_at": now_bj().isoformat(),
        "module": "execution_reviewer",
        "auto_closed_loop": auto_closed_loop,
        "pnl_summary": {
            "total_pnl": total_pnl,
            "total_pnl_pct": total_pnl_pct,
            "net_pnl": net_pnl,
            "net_pnl_pct": net_pnl_pct,
            "hedge_pnl": hedge_pnl,
            "hedge_cost": hedge_cost,
        },
        "hedge_review": {
            "hedge_enabled": hedge_enabled,
            "portfolio_beta": portfolio_beta,
            "planned_tools": planned_tools,
            "executed_tools": executed_tools,
            "execution_gaps": execution_gaps,
            "orders": hedge_orders,
        },
        "ai_review": {
            "ai_total_count": ai_total_count,
            "ai_hit_count": ai_hit_count,
            "ai_hit_rate": round(ai_hit_rate, 4),
            "approved_count": len(ai_approved),
            "rejected_count": len(ai_rejected),
        },
        "risk_assessment": {
            "risk_level": risk_level,
            "risk_score": round(risk_score, 4),
            "risk_events": risk_events,
            "scores": {
                "pnl_score": round(pnl_score, 4),
                "dd_score": round(dd_score, 4),
                "gap_score": round(gap_score, 4),
                "ai_miss_score": round(ai_miss_score, 4),
            },
        },
    }

    # 落盘
    out_path = REPORTS_DIR / f"execution_review_{trade_date}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(review_report, f, ensure_ascii=False, indent=2)
    logger.info(f"复盘报告已保存: {out_path}")
    logger.info(
        f"风险等级={risk_level} 综合评分={risk_score:.4f} "
        f"AI命中率={ai_hit_rate:.2%} 执行缺口={execution_gaps}"
    )

    # 双模型自我判断 (DeepSeek + GLM 交叉验证)
    if auto_closed_loop:
        try:
            from dual_model_judge import run_dual_model_judgment

            dual_verdict = run_dual_model_judgment(review_report, trade_date=trade_date)
            review_report["dual_model_judgment"] = {
                "mode": dual_verdict.get("mode"),
                "final_decision": dual_verdict.get("final_decision"),
                "agreement": dual_verdict.get("cross_validation", {}).get("agreement"),
            }
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(review_report, f, ensure_ascii=False, indent=2)
            logger.info(
                "双模型判断已集成: mode=%s action=%s",
                dual_verdict.get("mode"),
                dual_verdict.get("final_decision", {}).get("action"),
            )
        except (ImportError, RuntimeError, ValueError, TypeError) as exc:
            logger.warning("双模型判断失败, 跳过: %s", exc)

    return review_report


if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    d = sys.argv[1] if len(sys.argv) > 1 else now_bj().strftime("%Y-%m-%d")
    run_execution_review(trade_date=d, auto_closed_loop="--auto" in sys.argv)
