"""
执行复盘模块（第三步-2） — v2.0
=================================

目标:
  - 收盘后自动核对 AI 确认结果、实际执行结果、当日盈亏；
  - 输出执行偏差、AI 命中率与可改进点，供后续自动复盘使用。

输入:
  - trade_instructions/ai_approved_{YYYY-MM-DD}.json
  - reports/daily_pnl_report_{YYYY-MM-DD}.json（收盘报告）
输出:
  - reports/execution_review_{YYYY-MM-DD}.json
  - reports/dynamic_risk_{YYYY-MM-DD}.json（自动闭环时）
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_BASE = Path(__file__).resolve().parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

try:
    from utils.logger import get_logger
    logger = get_logger("execution_reviewer")
except Exception:
    import logging
    logger = logging.getLogger("execution_reviewer")


class ExecutionReviewer:
    """执行后复盘"""

    def __init__(self, trade_date: str | None = None):
        self.trade_date = trade_date or datetime.now().strftime("%Y-%m-%d")
        self.date_compact = self.trade_date.replace("-", "")
        self.timestamp = datetime.now().isoformat()

        self.instructions_dir = _BASE.parent / "trade_instructions"
        self.reports_dir = _BASE / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)

        self.approved_path = self.instructions_dir / f"ai_approved_{self.date_compact}.json"
        self.pnl_path = self.reports_dir / f"daily_pnl_report_{self.date_compact}.json"
        self.out_path = self.reports_dir / f"execution_review_{self.date_compact}.json"

        self.approved: dict[str, Any] = {}
        self.pnl: dict[str, Any] = {}

    def load_approved(self) -> bool:
        if not self.approved_path.exists():
            logger.error(f"缺少 AI 确认文件: {self.approved_path}")
            return False
        try:
            self.approved = json.loads(self.approved_path.read_text(encoding="utf-8"))
            return True
        except Exception as e:
            logger.error(f"读取 AI 确认文件失败: {e}")
            return False

    def load_pnl(self) -> bool:
        if not self.pnl_path.exists():
            logger.warning(f"缺少收盘盈亏报告: {self.pnl_path}")
            self.pnl = {}
            return False
        try:
            self.pnl = json.loads(self.pnl_path.read_text(encoding="utf-8"))
            return True
        except Exception as e:
            logger.warning(f"读取收盘盈亏报告失败: {e}")
            self.pnl = {}
            return False

    def analyze(self) -> dict[str, Any]:
        approved_orders = self.approved.get("approved_instructions", []) or []
        rejected_orders = self.approved.get("rejected_instructions", []) or []

        pnl_summary = self.pnl.get("summary", {}) if isinstance(self.pnl, dict) else {}
        total_pnl = float(pnl_summary.get("total_pnl", 0) or 0)

        ai_hit_signals = 0
        ai_total_signals = len(approved_orders)
        execution_gaps = []

        for order in approved_orders:
            symbol = order.get("code") or order.get("symbol") or ""
            if not symbol:
                continue
            ai_total_signals += 1
            if isinstance(self.pnl, dict):
                positions = self.pnl.get("positions", []) or []
                hit = any(str(p.get("code") or p.get("symbol") or "") == str(symbol) for p in positions)
                if hit:
                    ai_hit_signals += 1
                else:
                    execution_gaps.append({
                        "symbol": symbol,
                        "issue": "建议标的未在收盘持仓/盈亏中出现，可能存在未执行或已平仓"
                    })

        review = {
            "trade_date": self.trade_date,
            "generated_at": self.timestamp,
            "module": "execution_reviewer",
            "status": "PASS",
            "metrics": {
                "ai_total_signals": ai_total_signals,
                "ai_hit_signals": ai_hit_signals,
                "ai_hit_rate": ai_hit_signals / ai_total_signals if ai_total_signals > 0 else 0,
                "rejected_count": len(rejected_orders),
                "daily_pnl": total_pnl,
            },
            "execution_gaps": execution_gaps,
            "improvements": [],
            "notes": [
                "复盘结果用于后续动态风控与执行优化",
                "若 ai_hit_rate 持续偏低，需检查执行层与建议映射关系",
            ],
        }

        if review["metrics"]["ai_hit_rate"] < 0.5 and ai_total_signals > 0:
            review["improvements"].append("AI 命中率偏低，建议检查执行层与建议映射关系")
        if total_pnl < 0:
            review["improvements"].append("当日亏损，建议复盘 AI 建议与风控阈值是否过松")
        if execution_gaps:
            review["improvements"].append(f"存在 {len(execution_gaps)} 条执行偏差，请人工复核")

        return review

    def save(self, payload: dict[str, Any]) -> Path:
        self.out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"已写入复盘结果: {self.out_path}")
        return self.out_path

    def _trigger_dynamic_risk(self) -> dict[str, Any]:
        """自动闭环：复盘保存后继续触发动态风控"""
        try:
            from dynamic_risk_adjuster import run_dynamic_risk_adjuster
            logger.info("自动闭环：开始执行动态风控...")
            risk_result = run_dynamic_risk_adjuster(trade_date=self.trade_date)
            logger.info(f"自动闭环：动态风控完成 => {risk_result.get('status')} | 输出={risk_result.get('path')}")
            return risk_result
        except Exception as e:
            logger.warning(f"自动闭环：动态风控触发失败: {e}")
            return {"status": "SKIPPED", "error": str(e)}

    def run(self, auto_closed_loop: bool = False) -> dict[str, Any]:
        self.load_approved()
        self.load_pnl()
        review = self.analyze()
        self.save(review)

        result = {
            "status": review.get("status"),
            "path": str(self.out_path),
            "metrics": review.get("metrics"),
            "auto_closed_loop": False,
        }

        if auto_closed_loop:
            risk_result = self._trigger_dynamic_risk()
            result["auto_closed_loop"] = True
            result["dynamic_risk"] = risk_result

        return result


def run_execution_review(trade_date: str | None = None, auto_closed_loop: bool = False) -> dict[str, Any]:
    reviewer = ExecutionReviewer(trade_date=trade_date)
    return reviewer.run(auto_closed_loop=auto_closed_loop)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="执行复盘")
    parser.add_argument("--date", default=None, help="交易日期 YYYY-MM-DD")
    parser.add_argument("--auto-closed-loop", action="store_true", help="复盘后自动触发动态风控")
    args = parser.parse_args()
    result = run_execution_review(trade_date=args.date, auto_closed_loop=args.auto_closed_loop)
    print(json.dumps(result, ensure_ascii=False, indent=2))
