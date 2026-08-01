# -*- coding: utf-8 -*-
"""
AI 自动确认模块（第三步-1） — v1.0
====================================

目标:
  - 在 AI 决策门已通过硬风控的前提下，对满足条件的 gate 结果做自动确认；
  - 同时保留人工确认通道，避免全自动失控。

输入:
  - trade_instructions/ai_gate_{YYYY-MM-DD}.json
输出:
  - trade_instructions/ai_approved_{YYYY-MM-DD}.json
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
    logger = get_logger("ai_auto_approver")
except Exception as e:
    import logging
    logger = logging.getLogger("ai_auto_approver")


class AIAutoApprover:
    """基于规则 + 轻量 LLM 校验的自动确认器"""

    def __init__(self, trade_date: Optional[str] = None, auto_mode: bool = False):
        self.trade_date = trade_date or datetime.now().strftime("%Y-%m-%d")
        self.auto_mode = auto_mode
        self.timestamp = datetime.now().isoformat()

        self.instructions_dir = _BASE.parent / "trade_instructions"
        self.instructions_dir.mkdir(parents=True, exist_ok=True)

        # 自动确认阈值
        self.auto_limits = {
            "max_approved_count": 20,
            "max_total_amount": 200_000,
            "max_rejected_count": 0,
            "min_approval_rate": 0.9,
            "require_risk_alerts_empty": False,
            "max_risk_alerts": 5,
        }

        self.gate_path = self._resolve_gate_path()
        self.out_path = self.instructions_dir / f"ai_approved_{self.trade_date.replace('-', '')}.json"

        self.gate: Dict[str, Any] = {}
        self.checks: Dict[str, Any] = {}

    def _resolve_gate_path(self) -> Path:
        compact = self.trade_date.replace("-", "")
        candidates = [
            self.instructions_dir / f"ai_gate_{self.trade_date}.json",
            self.instructions_dir / f"ai_gate_{compact}.json",
        ]
        for path in candidates:
            if path.exists():
                return path
        return candidates[0]

    # ------------------------------------------------------------------
    # 数据加载
    # ------------------------------------------------------------------
    def load_gate(self) -> bool:
        if not self.gate_path.exists():
            logger.error(f"缺少 gate 文件: {self.gate_path}")
            return False
        try:
            self.gate = json.loads(self.gate_path.read_text(encoding="utf-8"))
            return True
        except Exception as e:
            logger.error(f"读取 gate 文件失败: {e}")
            return False

    # ------------------------------------------------------------------
    # 自动确认条件检查
    # ------------------------------------------------------------------
    def _run_checks(self) -> Dict[str, Any]:
        fusion = self.gate.get("fusion_result", {}) or {}
        approved = fusion.get("approved_instructions", []) or []
        rejected = fusion.get("rejected_instructions", []) or []
        total_amount = float(fusion.get("total_amount", 0) or 0)
        risk_alerts = self.gate.get("fusion_result", {}).get("risk_alerts", []) or []

        total = len(approved) + len(rejected)
        approval_rate = len(approved) / total if total > 0 else 1.0

        self.checks = {
            "approval_rate": approval_rate,
            "total_amount": total_amount,
            "risk_alerts": risk_alerts,
            "rejected_count": len(rejected),
            "approved_count": len(approved),
        }
        return self.checks

    def _is_auto_approve(self) -> bool:
        c = self.checks
        limits = self.auto_limits
        if c["approved_count"] > limits["max_approved_count"]:
            return False
        if c["total_amount"] > limits["max_total_amount"]:
            return False
        if c["rejected_count"] > limits["max_rejected_count"]:
            return False
        if c["approval_rate"] < limits["min_approval_rate"]:
            return False
        if limits.get("require_risk_alerts_empty", True) and c["risk_alerts"]:
            return False
        if len(c.get("risk_alerts", [])) > limits.get("max_risk_alerts", 5):
            return False
        return True

    # ------------------------------------------------------------------
    # 确认结果生成
    # ------------------------------------------------------------------
    def build_approved_result(self, approved: bool) -> Dict[str, Any]:
        meta = {
            "trade_date": self.trade_date,
            "generated_at": self.timestamp,
            "module": "ai_auto_approver",
            "mode": "auto" if self.auto_mode else "manual",
            "source_gate": str(self.gate_path),
            "approved": approved,
        }

        if not approved:
            return {
                **meta,
                "reason": "未满足自动确认条件，请人工复核",
                "checks": self.checks,
                "auto_limits": self.auto_limits,
                "approved_instructions": [],
                "rejected_instructions": [],
                "pending_instructions": self.gate.get("fusion_result", {}).get("approved_instructions", []),
            }

        approved_instructions = self.gate.get("fusion_result", {}).get("approved_instructions", []) or []
        rejected_instructions = self.gate.get("fusion_result", {}).get("rejected_instructions", []) or []

        result = {
            **meta,
            "reason": "满足自动确认条件",
            "checks": self.checks,
            "auto_limits": self.auto_limits,
            "approved_instructions": approved_instructions,
            "rejected_instructions": rejected_instructions,
            "pending_instructions": [],
            "approval_time": datetime.now().isoformat(),
            "notes": [
                "自动确认仅适用于满足规则阈值的审核结果",
                "若市场状态突变，请人工复核后执行",
                "执行层应再次校验 ai_approved=true 后再下单",
            ],
        }
        return result

    def save(self, payload: Dict[str, Any]) -> Path:
        self.out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"已写入确认结果: {self.out_path}")
        return self.out_path

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    def run(self) -> Dict[str, Any]:
        if not self.load_gate():
            return {"status": "FAIL", "error": "load_gate_failed"}

        self._run_checks()
        approved = self._is_auto_approve()
        if not self.auto_mode:
            approved = False

        payload = self.build_approved_result(approved=approved)
        self.save(payload)
        status = "PASS" if approved else "SKIP"
        logger.info(
            "AI 自动确认完成: status=%s, approval_rate=%.2f%%, total_amount=%s",
            status,
            self.checks.get("approval_rate", 0) * 100,
            self.checks.get("total_amount", 0),
        )
        return {"status": status, "approved": approved, "path": str(self.out_path)}


def run_ai_auto_approver(trade_date: Optional[str] = None, auto_mode: bool = False) -> Dict[str, Any]:
    approver = AIAutoApprover(trade_date=trade_date, auto_mode=auto_mode)
    return approver.run()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AI 自动确认")
    parser.add_argument("--date", default=None, help="交易日期 YYYY-MM-DD")
    parser.add_argument("--auto", action="store_true", help="开启全自动确认")
    args = parser.parse_args()
    result = run_ai_auto_approver(trade_date=args.date, auto_mode=args.auto)
    print(json.dumps(result, ensure_ascii=False, indent=2))
