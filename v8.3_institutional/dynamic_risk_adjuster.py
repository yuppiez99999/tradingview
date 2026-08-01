# -*- coding: utf-8 -*-
"""
动态风控调整模块（第三步-3） — v2.1
====================================

目标:
  - 基于近期执行复盘、市场波动与历史表现，自动调整风控阈值；
  - 输出调整后的参数，供 AI 决策门 / 自动确认器使用。

输入:
  - reports/execution_review_{YYYY-MM-DD}.json
  - reports/daily_pnl_report_{YYYY-MM-DD}.json
输出:
  - reports/dynamic_risk_{YYYY-MM-DD}.json
  - trade_instructions/dynamic_risk_limits_{YYYY-MM-DD}.json（可被 AI 决策门读取）
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

_BASE = Path(__file__).resolve().parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

try:
    from utils.logger import get_logger
    logger = get_logger("dynamic_risk_adjuster")
except Exception:
    import logging
    logger = logging.getLogger("dynamic_risk_adjuster")


class DynamicRiskAdjuster:
    """动态风控调整器"""

    def __init__(self, trade_date: Optional[str] = None):
        self.trade_date = trade_date or datetime.now().strftime("%Y-%m-%d")
        self.date_compact = self.trade_date.replace("-", "")
        self.timestamp = datetime.now().isoformat()

        self.reports_dir = _BASE / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)

        # 上级项目根目录（trade_instructions 所在目录）
        self.project_root = _BASE.parent

        self.review_path = self.reports_dir / f"execution_review_{self.date_compact}.json"
        self.pnl_path = self.reports_dir / f"daily_pnl_report_{self.date_compact}.json"
        self.out_path = self.reports_dir / f"dynamic_risk_{self.date_compact}.json"
        self.gate_out_path = self.project_root / "trade_instructions" / f"dynamic_risk_limits_{self.date_compact}.json"

        self.review: Dict[str, Any] = {}
        self.pnl: Dict[str, Any] = {}

        # 默认基础阈值
        self.base_limits = {
            "max_single_order_amount": 200_000,
            "max_total_amount": 200_000,
            "min_approval_rate": 0.9,
            "max_position_concentration": 0.30,
            "price_band": 0.03,
        }
        self.adjusted_limits = dict(self.base_limits)

    def load_review(self) -> bool:
        if not self.review_path.exists():
            logger.warning(f"缺少复盘文件: {self.review_path}")
            self.review = {}
            return False
        try:
            self.review = json.loads(self.review_path.read_text(encoding="utf-8"))
            return True
        except Exception as e:
            logger.warning(f"读取复盘文件失败: {e}")
            self.review = {}
            return False

    def load_pnl(self) -> bool:
        if not self.pnl_path.exists():
            logger.warning(f"缺少盈亏报告: {self.pnl_path}")
            self.pnl = {}
            return False
        try:
            self.pnl = json.loads(self.pnl_path.read_text(encoding="utf-8"))
            return True
        except Exception as e:
            logger.warning(f"读取盈亏报告失败: {e}")
            self.pnl = {}
            return False

    def _compute_risk_score(self) -> Dict[str, Any]:
        """计算综合风险评分，返回评分明细"""
        metrics = {}
        if isinstance(self.review, dict):
            metrics = self.review.get("metrics", {}) or {}

        hit_rate = float(metrics.get("ai_hit_rate", 1) or 1)
        daily_pnl = 0.0
        if isinstance(self.pnl, dict):
            daily_pnl = float(self.pnl.get("summary", {}).get("total_pnl", 0) or 0)

        execution_gaps = []
        if isinstance(self.review, dict):
            execution_gaps = self.review.get("execution_gaps", []) or []

        # 组合总值用于计算盈亏比例（若无则仅用绝对额）
        portfolio_value = 0.0
        if isinstance(self.pnl, dict):
            portfolio_value = float(self.pnl.get("summary", {}).get("total_market_value", 0) or 0)

        pnl_ratio = 0.0
        if portfolio_value > 0:
            pnl_ratio = daily_pnl / portfolio_value
        elif daily_pnl != 0:
            # 无组合总值时按绝对额给低权重
            pnl_ratio = daily_pnl / 1_060_000

        # 评分维度：越高越危险
        scores = {
            "ai_hit_rate_score": 0.0,
            "pnl_score": 0.0,
            "execution_gap_score": 0.0,
            "rejection_score": 0.0,
        }

        # 1) AI 命中率
        if hit_rate < 0.3:
            scores["ai_hit_rate_score"] = 0.4
        elif hit_rate < 0.5:
            scores["ai_hit_rate_score"] = 0.2
        elif hit_rate < 0.7:
            scores["ai_hit_rate_score"] = 0.1

        # 2) 盈亏
        if pnl_ratio <= -0.05:
            scores["pnl_score"] = 0.3
        elif pnl_ratio < 0:
            scores["pnl_score"] = 0.15
        elif pnl_ratio < 0.01 and daily_pnl < 0:
            scores["pnl_score"] = 0.1

        # 3) 执行偏差
        gap_count = len(execution_gaps)
        if gap_count >= 3:
            scores["execution_gap_score"] = 0.2
        elif gap_count >= 1:
            scores["execution_gap_score"] = 0.1

        # 4) 拒绝率
        rejected_count = int(metrics.get("rejected_count", 0) or 0)
        approved_count = int(metrics.get("ai_total_signals", 1) or 1)
        if approved_count > 0 and rejected_count / approved_count >= 0.5:
            scores["rejection_score"] = 0.1

        total_score = sum(scores.values())
        return {
            "total_score": total_score,
            "scores": scores,
            "hit_rate": hit_rate,
            "daily_pnl": daily_pnl,
            "pnl_ratio": pnl_ratio,
            "gap_count": gap_count,
            "rejected_count": rejected_count,
            "approved_count": approved_count,
        }

    def _classify_risk(self, total_score: float) -> str:
        if total_score >= 0.6:
            return "HIGH"
        if total_score >= 0.3:
            return "MEDIUM"
        if total_score >= 0.1:
            return "LOW"
        return "NONE"

    def _tighten_factor(self, risk_level: str) -> float:
        mapping = {
            "HIGH": 0.7,
            "MEDIUM": 0.85,
            "LOW": 0.95,
            "NONE": 1.0,
        }
        return mapping.get(risk_level, 1.0)

    def adjust(self) -> Dict[str, Any]:
        risk = self._compute_risk_score()
        risk_level = self._classify_risk(risk["total_score"])
        factor = self._tighten_factor(risk_level)

        limits = dict(self.base_limits)

        # 平滑收紧/恢复
        limits["max_single_order_amount"] = max(100_000, limits["max_single_order_amount"] * factor)
        limits["max_total_amount"] = max(100_000, limits["max_total_amount"] * factor)
        limits["min_approval_rate"] = min(0.95, limits["min_approval_rate"] + (1 - factor) * 0.1)
        limits["max_position_concentration"] = max(0.15, limits["max_position_concentration"] * factor)
        limits["price_band"] = max(0.015, limits["price_band"] * factor)

        self.adjusted_limits = limits
        return {
            "base_limits": self.base_limits,
            "adjusted_limits": limits,
            "risk_level": risk_level,
            "risk_score": round(risk["total_score"], 2),
            "tighten_factor": factor,
            "trigger": {
                "ai_hit_rate": risk["hit_rate"],
                "daily_pnl": risk["daily_pnl"],
                "pnl_ratio": round(risk["pnl_ratio"], 4),
                "execution_gaps": risk["gap_count"],
                "rejected_count": risk["rejected_count"],
                "approved_count": risk["approved_count"],
            },
            "risk_scores": risk["scores"],
            "notes": [
                f"风险等级={risk_level}，综合评分={risk['total_score']:.2f}",
                "收紧系数越低，风控越严格；无风险时恢复基础阈值",
                "动态风控仅作为阈值参考，最终仍以 AI 决策门硬风控为准",
            ],
        }

    def _write_dynamic_limits(self, adjusted: Dict[str, Any]) -> Optional[Path]:
        """将调整后的风控阈值写入 AI 决策门可读取的落盘文件"""
        try:
            payload = {
                "trade_date": self.trade_date,
                "generated_at": self.timestamp,
                "module": "dynamic_risk_adjuster",
                "source": "auto_closed_loop",
                "base_limits": adjusted.get("base_limits", {}),
                "adjusted_limits": adjusted.get("adjusted_limits", {}),
                "risk_level": adjusted.get("risk_level"),
                "risk_score": adjusted.get("risk_score"),
                "tighten_factor": adjusted.get("tighten_factor"),
                "trigger": adjusted.get("trigger", {}),
                "risk_scores": adjusted.get("risk_scores", {}),
                "notes": adjusted.get("notes", []),
            }
            self.gate_out_path.parent.mkdir(parents=True, exist_ok=True)
            self.gate_out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"已写入动态风控限值 => AI Gate 可读取: {self.gate_out_path}")
            return self.gate_out_path
        except Exception as e:
            logger.warning(f"写入动态风控限值失败: {e}")
            return None

    def save(self, payload: Dict[str, Any]) -> Path:
        self.out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"已写入动态风控: {self.out_path}")
        return self.out_path

    def run(self, write_gate_limits: bool = False) -> Dict[str, Any]:
        self.load_review()
        self.load_pnl()
        adjusted = self.adjust()
        self.save(adjusted)

        result = {
            "status": "PASS",
            "path": str(self.out_path),
            "limits": adjusted.get("adjusted_limits"),
            "risk_level": adjusted.get("risk_level"),
            "risk_score": adjusted.get("risk_score"),
            "write_gate_limits": False,
        }

        if write_gate_limits:
            gate_path = self._write_dynamic_limits(adjusted)
            result["write_gate_limits"] = bool(gate_path)
            if gate_path:
                result["gate_limits_path"] = str(gate_path)

        return result


def run_dynamic_risk_adjuster(trade_date: Optional[str] = None, write_gate_limits: bool = False) -> Dict[str, Any]:
    adjuster = DynamicRiskAdjuster(trade_date=trade_date)
    return adjuster.run(write_gate_limits=write_gate_limits)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="动态风控调整")
    parser.add_argument("--date", default=None, help="交易日期 YYYY-MM-DD")
    parser.add_argument("--write-gate-limits", action="store_true", help="同时将调整后的限值写入 AI Gate 可读取文件")
    args = parser.parse_args()
    result = run_dynamic_risk_adjuster(trade_date=args.date, write_gate_limits=args.write_gate_limits)
    print(json.dumps(result, ensure_ascii=False, indent=2))
