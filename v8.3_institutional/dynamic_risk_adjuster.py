"""
动态风控调整器 (Dynamic Risk Adjuster)
======================================
v8.6 补齐模块: 基于复盘结果调整风控阈值, 写回 AI Gate 限值文件

闭环链路:
    复盘报告 → 风险评分 → 阈值调整 → 写回 dynamic_risk_limits + ai_gate

用法:
    from dynamic_risk_adjuster import run_dynamic_risk_adjuster
    run_dynamic_risk_adjuster(trade_date="2026-08-19", write_gate_limits=True)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from utils.datetime_utils import now_bj

logger = logging.getLogger("dynamic_risk_adjuster")

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
REPORTS_DIR = BASE_DIR / "reports"
TRADE_INSTRUCTIONS_DIR = PROJECT_ROOT / "trade_instructions"

# 基础风控限值 (与 ai_gate hard_limits 对齐)
BASE_LIMITS = {
    "max_single_order_amount": 200000,
    "max_total_amount": 200000,
    "min_approval_rate": 0.9,
    "max_position_concentration": 0.3,
    "price_band": 0.03,
}


def _load_review_report(trade_date: str) -> dict[str, Any]:
    """加载复盘报告"""
    p = REPORTS_DIR / f"execution_review_{trade_date}.json"
    if p.exists():
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _load_latest_ai_gate(trade_date: str) -> dict[str, Any]:
    """加载 AI Gate 限值文件"""
    p = TRADE_INSTRUCTIONS_DIR / f"ai_gate_{trade_date}.json"
    if p.exists():
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _compute_tighten_factor(risk_level: str, risk_score: float) -> float:
    """根据风险等级计算收紧系数 (越小越严格)

    Returns:
        tighten_factor: 0.80 (高风险) ~ 1.0 (无风险)
    """
    if risk_level == "HIGH":
        return 0.80
    elif risk_level == "MEDIUM":
        return 0.90
    elif risk_level == "LOW":
        return 0.95
    else:
        return 1.0


def run_dynamic_risk_adjuster(
    trade_date: str, write_gate_limits: bool = False
) -> dict[str, Any]:
    """基于复盘结果调整风控阈值

    Args:
        trade_date: 交易日期 YYYY-MM-DD
        write_gate_limits: 是否写回 AI Gate 限值文件

    Returns:
        调整结果字典
    """
    logger.info("=" * 60)
    logger.info(
        f"动态风控调整启动 | trade_date={trade_date} | write_gate_limits={write_gate_limits}"
    )
    logger.info("=" * 60)

    review = _load_review_report(trade_date)
    if not review:
        logger.warning(f"复盘报告不存在, 跳过动态风控调整: {trade_date}")
        return {"status": "SKIP", "reason": "review_report_not_found"}

    risk_assessment = review.get("risk_assessment", {})
    risk_level = risk_assessment.get("risk_level", "LOW")
    risk_score = float(risk_assessment.get("risk_score", 0))
    tighten_factor = _compute_tighten_factor(risk_level, risk_score)

    # 调整限值 (收紧系数越小, 限值越严格)
    adjusted_limits = {
        "max_single_order_amount": round(
            BASE_LIMITS["max_single_order_amount"] * tighten_factor, 0
        ),
        "max_total_amount": round(BASE_LIMITS["max_total_amount"] * tighten_factor, 0),
        "min_approval_rate": round(
            min(0.99, BASE_LIMITS["min_approval_rate"] + (1 - tighten_factor) * 0.1), 4
        ),
        "max_position_concentration": round(
            BASE_LIMITS["max_position_concentration"] * tighten_factor, 4
        ),
        "price_band": round(BASE_LIMITS["price_band"] * tighten_factor, 4),
    }

    # 触发数据
    ai_review = review.get("ai_review", {})
    pnl_summary = review.get("pnl_summary", {})
    trigger = {
        "ai_hit_rate": ai_review.get("ai_hit_rate", 0),
        "daily_pnl": pnl_summary.get("net_pnl", 0),
        "pnl_ratio": pnl_summary.get("net_pnl_pct", 0),
        "execution_gaps": review.get("hedge_review", {}).get("execution_gaps", 0),
        "rejected_count": ai_review.get("rejected_count", 0),
        "approved_count": ai_review.get("approved_count", 0),
    }

    # 风险评分明细
    scores = risk_assessment.get("scores", {})
    risk_scores = {
        "ai_hit_rate_score": round(1.0 - trigger["ai_hit_rate"], 4),
        "pnl_score": scores.get("pnl_score", 0),
        "execution_gap_score": scores.get("gap_score", 0),
        "rejection_score": round(
            trigger["rejected_count"]
            / max(1, trigger["approved_count"] + trigger["rejected_count"]),
            4,
        ),
    }

    result = {
        "trade_date": trade_date,
        "generated_at": now_bj().isoformat(),
        "module": "dynamic_risk_adjuster",
        "source": "auto_closed_loop" if write_gate_limits else "manual",
        "base_limits": BASE_LIMITS,
        "adjusted_limits": adjusted_limits,
        "risk_level": risk_level,
        "risk_score": risk_score,
        "tighten_factor": tighten_factor,
        "trigger": trigger,
        "risk_scores": risk_scores,
        "notes": [
            f"风险等级={risk_level}，综合评分={risk_score:.4f}",
            f"收紧系数={tighten_factor} (越小越严格)",
            "动态风控仅作为阈值参考，最终仍以 AI 决策门硬风控为准",
        ],
    }

    # 写回 dynamic_risk_limits
    dr_path = TRADE_INSTRUCTIONS_DIR / f"dynamic_risk_limits_{trade_date}.json"
    dr_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dr_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    logger.info(f"动态风控限值已保存: {dr_path}")

    # 写回 AI Gate 限值文件
    if write_gate_limits:
        ai_gate = _load_latest_ai_gate(trade_date)
        if ai_gate:
            ai_gate["dynamic_limits_loaded"] = True
            ai_gate["dynamic_limits_path"] = str(dr_path)
            ai_gate.setdefault("hard_limits", {}).update(
                {
                    "max_single_order_amount": int(
                        adjusted_limits["max_single_order_amount"]
                    ),
                    "max_total_amount": int(adjusted_limits["max_total_amount"]),
                    "max_position_concentration": adjusted_limits[
                        "max_position_concentration"
                    ],
                    "price_protection_pct": adjusted_limits["price_band"],
                }
            )
            ai_gate_path = TRADE_INSTRUCTIONS_DIR / f"ai_gate_{trade_date}.json"
            with open(ai_gate_path, "w", encoding="utf-8") as f:
                json.dump(ai_gate, f, ensure_ascii=False, indent=2)
            logger.info(f"AI Gate 限值已写回: {ai_gate_path}")

    logger.info(
        f"动态风控调整完成: risk_level={risk_level} tighten={tighten_factor} "
        f"max_single={adjusted_limits['max_single_order_amount']}"
    )

    return result


if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    d = sys.argv[1] if len(sys.argv) > 1 else now_bj().strftime("%Y-%m-%d")
    run_dynamic_risk_adjuster(
        trade_date=d,
        write_gate_limits="--write-gate" in sys.argv or "--auto" in sys.argv,
    )
