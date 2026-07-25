# -*- coding: utf-8 -*-
"""
AI 决策沙箱（只读模式） — v1.0
================================

目标:
  - 在不触发真实下单的前提下，让 GLM-5.2 基于当日交易计划、持仓与市场状态生成结构化决策建议；
  - 输出到 trade_instructions/ai_sandbox_{YYYY-MM-DD}.json，供人工复核后再接入执行层。

使用方式:
  # 由 daily_workflow --ai-sandbox 自动调用
  python daily_workflow.py --ai-sandbox [--date YYYY-MM-DD]
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# 路径兼容
_BASE = Path(__file__).resolve().parent
_SRC = _BASE / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
_BASE_PARENT = _BASE.parent
if str(_BASE_PARENT) not in sys.path:
    sys.path.insert(0, str(_BASE_PARENT))
# 兼容 15_每日工作流 下的 llm_client
_LLM_DIR = _BASE_PARENT / "15_每日工作流"
if _LLM_DIR.exists() and str(_LLM_DIR) not in sys.path:
    sys.path.insert(0, str(_LLM_DIR))

# 日志
try:
    from utils.logger import get_logger
    logger = get_logger("ai_decision_sandbox")
except Exception:
    import logging
    logger = logging.getLogger("ai_decision_sandbox")

# LLM
try:
    from llm_client import chat as _llm_chat
    _LLM_READY = True
except Exception:
    _LLM_READY = False


class AIDecisionSandbox:
    """只读 AI 决策沙箱"""

    def __init__(self, trade_date: Optional[str] = None):
        self.trade_date = trade_date or datetime.now().strftime("%Y-%m-%d")
        self.date_compact = self.trade_date.replace("-", "")
        self.timestamp = datetime.now().isoformat()

        # 目录
        self.base_dir = _BASE
        self.trade_plans_dir = self.base_dir / "trade_plans"
        self.instructions_dir = self.base_dir.parent / "trade_instructions"
        self.instructions_dir.mkdir(parents=True, exist_ok=True)

        # 风控边界（只读校验，不执行）
        self.limits = {
            "max_single_order_amount": 200_000,  # 单票上限 20万
            "max_total_amount": 200_000,         # 单日建议总额上限 20万
            "max_position_concentration": 0.30,  # 单票上限 30%
            "forbidden_symbols": ["*ST", "ST", "退市"],  # 黑名单关键词
        }

        # 数据容器
        self.trade_plan: Dict[str, Any] = {}
        self.positions: Dict[str, Any] = {}
        self.hedge_plan: Dict[str, Any] = {}
        self.market_state: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # 数据读取（只读）
    # ------------------------------------------------------------------
    def _load_json(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"读取失败 {path}: {e}")
            return {}

    def load_trade_plan(self) -> bool:
        path = self.trade_plans_dir / f"trade_plan_{self.date_compact}.json"
        self.trade_plan = self._load_json(path)
        return bool(self.trade_plan)

    def load_positions(self) -> bool:
        path = self.base_dir.parent / "config" / "positions.json"
        self.positions = self._load_json(path)
        return bool(self.positions)

    def load_hedge_plan(self) -> bool:
        path = self.base_dir.parent / "reports" / f"hedge_execution_orders_{self.date_compact}.json"
        self.hedge_plan = self._load_json(path)
        return bool(self.hedge_plan)

    def load_market_state(self) -> Dict[str, Any]:
        # 简化：从 trade_plan 或空结构推导
        self.market_state = self.trade_plan.get("market_state", {})
        return self.market_state

    def load_all(self) -> bool:
        ok = True
        ok &= self.load_trade_plan()
        ok &= self.load_positions()
        ok &= self.load_hedge_plan()
        self.load_market_state()
        return ok

    # ------------------------------------------------------------------
    # 基础校验
    # ------------------------------------------------------------------
    def validate_suggestion(self, suggestion: Dict[str, Any]) -> List[str]:
        errors: List[str] = []

        # 1) 黑名单
        for order in suggestion.get("spot_orders", []):
            symbol = str(order.get("symbol", ""))
            for kw in self.limits["forbidden_symbols"]:
                if kw in symbol:
                    errors.append(f"黑名单标的: {symbol}")

        # 2) 金额上限
        total = sum(float(o.get("amount", 0)) for o in suggestion.get("spot_orders", []))
        if total > self.limits["max_total_amount"]:
            errors.append(f"单日建议总额 {total:,.0f} 超过上限 {self.limits['max_total_amount']:,.0f}")

        # 3) 单票上限
        for order in suggestion.get("spot_orders", []):
            amt = float(order.get("amount", 0))
            if amt > self.limits["max_single_order_amount"]:
                errors.append(f"单票金额 {amt:,.0f} 超过上限 {self.limits['max_single_order_amount']:,.0f}: {order.get('symbol')}")

        # 4) 对冲一致性
        hedge_orders = suggestion.get("hedge_orders", [])
        if self.hedge_plan and not hedge_orders:
            errors.append("已有对冲计划，但 AI 建议未包含对冲部分")

        return errors

    # ------------------------------------------------------------------
    # Prompt 构造
    # ------------------------------------------------------------------
    def _build_prompt(self) -> str:
        # 精简上下文，避免 token 爆炸
        raw_positions = self.positions.get("positions", {})
        if isinstance(raw_positions, dict):
            position_items = list(raw_positions.items())[:20]
            positions_summary = []
            for symbol, pos in position_items:
                positions_summary.append({
                    "symbol": symbol,
                    "name": pos.get("name"),
                    "quantity": pos.get("shares"),
                    "avg_cost": pos.get("avg_cost"),
                    "est_price": pos.get("est_price"),
                    "style": pos.get("style"),
                })
        else:
            positions_summary = [
                {
                    "symbol": pos.get("symbol"),
                    "name": pos.get("name"),
                    "quantity": pos.get("quantity") or pos.get("shares"),
                    "avg_cost": pos.get("avg_cost"),
                    "est_price": pos.get("est_price"),
                    "style": pos.get("style"),
                }
                for pos in list(raw_positions)[:20]
            ]

        trade_plan_summary = {
            "date": self.trade_date,
            "daily_budget": self.trade_plan.get("daily_budget"),
            "phase": self.trade_plan.get("phase"),
            "targets": self.trade_plan.get("targets", [])[:20],
            "hedge_plan_loaded": bool(self.hedge_plan),
        }

        prompt = f"""你是量化交易系统的只读决策顾问。基于以下信息生成今日交易建议，仅输出 JSON，不要额外说明。

【持仓摘要】
{json.dumps(positions_summary, ensure_ascii=False)}

【当日交易计划摘要】
{json.dumps(trade_plan_summary, ensure_ascii=False)}

【市场状态】
{json.dumps(self.market_state, ensure_ascii=False)}

【输出要求】
返回 JSON，结构如下：
{{
  "date": "{self.trade_date}",
  "model": "GLM-5.2",
  "mode": "sandbox_only",
  "summary": "一句话总结建议",
  "spot_orders": [
    {{"symbol": "代码.后缀", "direction": "buy/sell", "amount": 金额, "reason": "理由"}}
  ],
  "hedge_orders": [
    {{"type": "FUTURES/OPTIONS/SAFE_HAVEN", "symbol": "标的", "direction": "开/平", "amount": 金额, "reason": "理由"}}
  ],
  "risk_notes": ["风险点1", "风险点2"],
  "confidence": 0.0-1.0
}}

约束：
- 仅建议，不执行；
- 单票建议金额不超过 200,000；
- 总建议金额不超过 200,000；
- 不要建议黑名单标的；
- 如建议卖出，需给出明确风控理由。"""
        return prompt

    # ------------------------------------------------------------------
    # 调用 LLM
    # ------------------------------------------------------------------
    def generate(self) -> Dict[str, Any]:
        if not _LLM_READY:
            return {
                "date": self.trade_date,
                "model": "NONE",
                "mode": "sandbox_only",
                "error": "llm_client 不可用，无法生成 AI 建议",
                "spot_orders": [],
                "hedge_orders": [],
                "risk_notes": ["LLM 未就绪"],
                "confidence": 0.0,
            }

        prompt = self._build_prompt()
        system = "你是一名量化交易只读决策顾问，输出必须严格 JSON，不要输出非 JSON 内容。"

        raw = _llm_chat(prompt, system=system, temperature=0.2, max_tokens=2000)
        if not raw:
            return {
                "date": self.trade_date,
                "model": "GLM-5.2",
                "mode": "sandbox_only",
                "error": "LLM 调用失败，返回空",
                "spot_orders": [],
                "hedge_orders": [],
                "risk_notes": ["LLM 返回空"],
                "confidence": 0.0,
            }

        # 尝试解析 JSON
        suggestion = self._parse_json_response(raw)
        suggestion.setdefault("date", self.trade_date)
        suggestion.setdefault("model", "GLM-5.2")
        suggestion.setdefault("mode", "sandbox_only")
        suggestion.setdefault("spot_orders", [])
        suggestion.setdefault("hedge_orders", [])
        suggestion.setdefault("risk_notes", [])
        suggestion.setdefault("confidence", 0.0)

        # 校验
        validation_errors = self.validate_suggestion(suggestion)
        suggestion["validation"] = {
            "errors": validation_errors,
            "passed": len(validation_errors) == 0,
            "checked_at": self.timestamp,
        }

        return suggestion

    def _parse_json_response(self, raw: str) -> Dict[str, Any]:
        # 提取首个 JSON 对象
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {"_raw": raw}
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return {"_raw": raw}

    # ------------------------------------------------------------------
    # 落盘
    # ------------------------------------------------------------------
    def save(self, suggestion: Dict[str, Any]) -> Path:
        filename = f"ai_sandbox_{self.trade_date}.json"
        out_path = self.instructions_dir / filename
        payload = {
            "meta": {
                "generated_at": self.timestamp,
                "trade_date": self.trade_date,
                "module": "ai_decision_sandbox",
                "mode": "sandbox_only",
                "description": "AI 决策建议（只读沙箱），不触发下单",
            },
            "suggestion": suggestion,
        }
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"AI 决策沙箱建议已保存: {out_path}")
        return out_path

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    def run(self) -> Dict[str, Any]:
        logger.info(f"AI 决策沙箱启动: {self.trade_date}")

        if not self.load_all():
            logger.warning("部分数据未加载，建议可能不完整")

        suggestion = self.generate()
        out_path = self.save(suggestion)

        passed = suggestion.get("validation", {}).get("passed", False)
        status = "PASS" if passed else "FAIL"
        logger.info(f"AI 决策沙箱完成: {status} -> {out_path}")

        return {
            "status": status,
            "trade_date": self.trade_date,
            "output": str(out_path),
            "suggestion": suggestion,
        }


def run_ai_sandbox(trade_date: Optional[str] = None) -> Dict[str, Any]:
    return AIDecisionSandbox(trade_date=trade_date).run()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AI 决策沙箱（只读）")
    parser.add_argument("--date", type=str, default=None, help="交易日期 YYYY-MM-DD")
    args = parser.parse_args()
    result = run_ai_sandbox(trade_date=args.date)
    print(json.dumps(result, ensure_ascii=False, indent=2))
