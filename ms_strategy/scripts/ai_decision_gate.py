"""
AI 决策门（带硬风控的半自动执行层） — v2.0
==========================================

新增能力:
  - 支持读取 dynamic_risk_adjuster 输出的动态风控文件，
    在初始化时覆盖默认 hard_limits，实现执行复盘 -> 动态风控 -> AI Gate 的自动闭环。
"""

from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.parent
INSTRUCTIONS_DIR = PROJECT_ROOT / "trade_instructions"
POSITIONS_FILE = PROJECT_ROOT / "config" / "positions.json"
# B1.5: 副本已删除, 改为调用根目录版
DAILY_EXECUTOR_PATH = PROJECT_ROOT / "daily_trade_executor.py"

# 导入 daily_trade_executor 的工具函数
# B1.5: 改为从项目根目录导入 (副本已删除)
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "15_每日工作流"))

from daily_trade_executor import (  # noqa: E402
    DEFAULT_PRICES,
    PRICE_PROTECTION_PCT,
    load_latest_prices,
    load_positions,
)

try:
    from llm_client import chat as _llm_chat
    _LLM_READY = True
except ImportError:
    # 导入降级: llm_client 模块缺失时回退到 _LLM_READY=False
    _LLM_READY = False


class AIDecisionGate:
    """AI 决策门 — 带硬风控的半自动执行层"""

    def __init__(self, trade_date: str | None = None):
        self.trade_date = trade_date or datetime.now().strftime("%Y-%m-%d")
        self.timestamp = datetime.now().isoformat()

        # 目录
        self.instructions_dir = INSTRUCTIONS_DIR
        self.instructions_dir.mkdir(parents=True, exist_ok=True)

        # 硬风控边界 (不可突破)
        self.hard_limits = {
            "max_single_order_amount": 200_000,      # 单票上限 20万
            "max_total_amount": 200_000,             # 单日总额上限 20万
            "max_position_concentration": 0.30,      # 单票持仓上限 30%
            "max_daily_trades": 50,                  # 单日交易笔数上限
            "min_order_amount": 100,                 # 最小下单金额
            "price_protection_pct": PRICE_PROTECTION_PCT,  # ±3% 价格保护
            "daily_loss_stop_pct": 0.03,             # 单日亏损 >3% 熔断
            "portfolio_drawdown_stop_pct": 0.05,     # 组合回撤 >5% 熔断
            "forbidden_keywords": ["*ST", "ST", "退市"],  # 黑名单关键词
            "allowed_directions": ["BUY", "SELL", "HOLD"],  # 允许方向 (list, 非set)
        }

        # 自动闭环：读取 dynamic_risk_adjuster 输出的动态风控限值
        self.dynamic_limits_loaded = False
        self.dynamic_limits_path = self.instructions_dir / f"dynamic_risk_limits_{self.trade_date.replace('-', '')}.json"
        self._load_dynamic_risk_limits()

        # 数据容器
        self.base_instructions: dict[str, Any] = {}
        self.positions: dict[str, Any] = {}
        self.latest_prices: dict[str, float] = {}
        self.ai_suggestion: dict[str, Any] = {}
        self.gate_output: dict[str, Any] = {}

    def _load_dynamic_risk_limits(self) -> bool:
        """自动闭环：加载动态风控文件，仅覆盖允许被调整的键"""
        if not self.dynamic_limits_path.exists():
            return False
        try:
            payload = json.loads(self.dynamic_limits_path.read_text(encoding="utf-8"))
            adjusted = payload.get("adjusted_limits") or {}
            if not isinstance(adjusted, dict):
                return False

            allowed_keys = {
                "max_single_order_amount",
                "max_total_amount",
                "min_approval_rate",
                "max_position_concentration",
                "price_band",
            }
            merged = 0
            for key, value in adjusted.items():
                if key in allowed_keys:
                    self.hard_limits[key] = value
                    merged += 1

            self.dynamic_limits_loaded = merged > 0
            if self.dynamic_limits_loaded:
                print(f"[INFO] AI 决策门已加载动态风控限值: {self.dynamic_limits_path} (合并 {merged} 项)")
            return self.dynamic_limits_loaded
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            print(f"[WARN] 读取动态风控限值失败: {e}")
            return False

    # ------------------------------------------------------------------
    # 数据读取
    # ------------------------------------------------------------------
    def _load_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            print(f"[WARN] 读取失败 {path}: {e}")
            return {}

    def load_base_instructions(self) -> bool:
        """加载 daily_trade_executor 生成的基础指令"""
        path = self.instructions_dir / f"{self.trade_date}_instructions.json"
        self.base_instructions = self._load_json(path)
        return bool(self.base_instructions)

    def load_positions(self) -> bool:
        try:
            self.positions = load_positions()
        except FileNotFoundError:
            positions_path = PROJECT_ROOT / "config" / "positions.json"
            if positions_path.exists():
                with open(positions_path, encoding='utf-8') as f:
                    self.positions = json.load(f)
            else:
                self.positions = {}
        return bool(self.positions)

    def load_latest_prices(self) -> bool:
        self.latest_prices = load_latest_prices()
        return bool(self.latest_prices)

    def load_ai_suggestion(self) -> bool:
        """加载 AI 决策沙箱生成的建议"""
        path = self.instructions_dir / f"ai_sandbox_{self.trade_date}.json"
        sandbox_data = self._load_json(path)
        self.ai_suggestion = sandbox_data.get("suggestion", {})
        return bool(self.ai_suggestion)

    def load_all(self) -> bool:
        ok = True
        ok &= self.load_base_instructions()
        ok &= self.load_positions()
        ok &= self.load_latest_prices()
        self.load_ai_suggestion()
        return ok

    # ------------------------------------------------------------------
    # 硬风控校验 (逐条)
    # ------------------------------------------------------------------
    def _check_blacklist(self, symbol: str) -> tuple[bool, str]:
        for kw in self.hard_limits["forbidden_keywords"]:
            if kw in symbol:
                return False, f"黑名单标的: {symbol} (包含关键词: {kw})"
        return True, ""

    def _check_amount_limits(self, amount: float, symbol: str) -> tuple[bool, str]:
        if amount < self.hard_limits["min_order_amount"]:
            return False, f"金额不足: {amount:.0f} < {self.hard_limits['min_order_amount']}"
        if amount > self.hard_limits["max_single_order_amount"]:
            return False, f"单票超上限: {amount:,.0f} > {self.hard_limits['max_single_order_amount']:,.0f}"
        return True, ""

    def _check_direction(self, direction: str) -> tuple[bool, str]:
        if direction not in self.hard_limits["allowed_directions"]:
            return False, f"不允许的方向: {direction}, 允许: {self.hard_limits['allowed_directions']}"
        return True, ""

    def _check_price_protection(self, symbol: str, order_price: float, direction: str) -> tuple[bool, str]:
        code_clean = symbol.split(".")[0]
        ref_price = self.latest_prices.get(code_clean, DEFAULT_PRICES.get(code_clean, order_price))
        if not ref_price:
            return True, ""  # 无参考价时跳过检查

        pct = self.hard_limits["price_protection_pct"]
        if direction == "BUY":
            if order_price > ref_price * (1 + pct):
                return False, f"买入价超保护带: {order_price:.4f} > {ref_price * (1 + pct):.4f} ({ref_price:.4f} × {1+pct:.1%})"
        elif direction == "SELL":
            if order_price < ref_price * (1 - pct):
                return False, f"卖出价超保护带: {order_price:.4f} < {ref_price * (1 - pct):.4f} ({ref_price:.4f} × {1-pct:.1%})"
        return True, ""

    def _check_position_concentration(self, symbol: str, amount: float) -> tuple[bool, str]:
        total_value = 0
        positions = self.positions.get("positions", {})
        if isinstance(positions, dict):
            for _code, pos in positions.items():
                if isinstance(pos, dict):
                    qty = pos.get("shares", pos.get("quantity", 0))
                    price = pos.get("est_price", pos.get("avg_cost", 1))
                    total_value += qty * price
        else:
            for pos in positions:
                if isinstance(pos, dict):
                    qty = pos.get("shares", pos.get("quantity", 0))
                    price = pos.get("est_price", pos.get("avg_cost", 1))
                    total_value += qty * price

        if total_value > 0 and amount > total_value * self.hard_limits["max_position_concentration"]:
            return False, f"持仓集中度超上限: {amount:,.0f} > {total_value * 0.3:,.0f} (组合总值: {total_value:,.0f} × 30%)"
        return True, ""

    def validate_order(self, order: dict[str, Any]) -> dict[str, Any]:
        """单条订单硬风控校验"""
        errors: list[str] = []
        warnings: list[str] = []

        symbol = order.get("symbol", "") or order.get("code", "") or order.get("full_code", "")
        amount = order.get("amount", 0) or order.get("estimated_amount", 0)
        direction = order.get("direction", "").upper() or order.get("action", "").upper()
        price = order.get("price", 0) or order.get("ref_price", 0) or order.get("max_buy_price", 0)

        # 黑名单
        ok, msg = self._check_blacklist(str(symbol))
        if not ok:
            errors.append(msg)

        # 方向检查
        ok, msg = self._check_direction(direction)
        if not ok:
            errors.append(msg)

        # 金额限制
        ok, msg = self._check_amount_limits(float(amount), str(symbol))
        if not ok:
            errors.append(msg)

        # 价格保护
        if price > 0:
            ok, msg = self._check_price_protection(str(symbol), float(price), direction)
            if not ok:
                errors.append(msg)

        # 持仓集中度 (仅买入时检查)
        if direction == "BUY":
            ok, msg = self._check_position_concentration(str(symbol), float(amount))
            if not ok:
                errors.append(msg)

        return {
            "passed": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
        }

    # ------------------------------------------------------------------
    # AI 建议与基础指令融合
    # ------------------------------------------------------------------
    def _build_fusion_prompt(self) -> str:
        """构建融合 AI 建议的 Prompt"""
        base_instructions = self.base_instructions.get("instructions", [])
        instructions_summary = []
        for inst in base_instructions[:10]:
            instructions_summary.append({
                "code": inst.get("code"),
                "name": inst.get("name"),
                "action": inst.get("action"),
                "estimated_amount": inst.get("estimated_amount"),
                "ref_price": inst.get("ref_price"),
                "confirm": inst.get("confirm"),
            })

        ai_suggestion_summary = {
            "spot_orders": self.ai_suggestion.get("spot_orders", [])[:10],
            "hedge_orders": self.ai_suggestion.get("hedge_orders", [])[:5],
            "summary": self.ai_suggestion.get("summary", ""),
            "confidence": self.ai_suggestion.get("confidence", 0),
        }

        positions_summary = []
        raw_positions = self.positions.get("positions", {})
        if isinstance(raw_positions, dict):
            position_items = list(raw_positions.items())[:10]
            for code, pos in position_items:
                positions_summary.append({
                    "code": code,
                    "name": pos.get("name"),
                    "quantity": pos.get("shares"),
                    "avg_cost": pos.get("avg_cost"),
                    "est_price": pos.get("est_price"),
                })

        prompt = f"""你是量化交易系统的 AI 决策门审核员。请基于以下信息，对基础交易指令进行审核和优化。

【基础交易指令】(由 daily_trade_executor 生成)
{json.dumps(instructions_summary, ensure_ascii=False)}

【AI 决策沙箱建议】(由 DeepSeek 生成)
{json.dumps(ai_suggestion_summary, ensure_ascii=False)}

【当前持仓】
{json.dumps(positions_summary, ensure_ascii=False)}

【硬风控规则】(不可突破)
- 单票上限: {self.hard_limits['max_single_order_amount']:,.0f}
- 单日总额上限: {self.hard_limits['max_total_amount']:,.0f}
- 价格保护带: ±{self.hard_limits['price_protection_pct']:.1%}
- 黑名单: *ST, ST, 退市
- 允许方向: BUY, SELL, HOLD

【任务】
1. 审核基础指令，判断是否需要调整
2. 参考 AI 沙箱建议，但不得突破硬风控规则
3. 对于 AI 建议中突破规则的部分，给出修正建议
4. 输出最终审核结果

【输出格式】(仅 JSON)
{{
  "date": "{self.trade_date}",
  "summary": "审核总结",
  "approved_instructions": [
    {{"instruction_id": "指令ID", "code": "代码", "name": "名称", "action": "BUY/SELL/HOLD",
      "qty": 数量, "price": 价格, "amount": 金额, "reason": "审核理由", "source": "base/ai/modified"}}
  ],
  "rejected_instructions": [
    {{"instruction_id": "指令ID", "code": "代码", "reason": "拒绝理由"}}
  ],
  "ai_adjustments": [
    {{"original": "...", "adjusted": "...", "reason": "调整原因"}}
  ],
  "risk_alerts": ["风险提示1", "风险提示2"],
  "confidence": 0.0-1.0
}}"""
        return prompt

    def fuse_ai_suggestion(self) -> dict[str, Any]:
        """融合 AI 建议与基础指令"""
        if not _LLM_READY:
            return {
                "date": self.trade_date,
                "model": "NONE",
                "error": "llm_client 不可用，使用基础指令",
                "approved_instructions": [],
                "rejected_instructions": [],
                "ai_adjustments": [],
                "risk_alerts": ["LLM 未就绪"],
                "confidence": 0.0,
            }

        prompt = self._build_fusion_prompt()
        system = "你是量化交易 AI 决策门审核员，输出必须严格 JSON，不要输出非 JSON 内容。"

        raw = _llm_chat(prompt, system=system, temperature=0.2, max_tokens=2000)
        if not raw:
            return {
                "date": self.trade_date,
                "model": "deepseek-chat",
                "error": "LLM 调用失败",
                "approved_instructions": [],
                "rejected_instructions": [],
                "ai_adjustments": [],
                "risk_alerts": ["LLM 返回空"],
                "confidence": 0.0,
            }

        # 解析 JSON
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1:
            return {"_raw": raw}
        try:
            fusion_result = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return {"_raw": raw}

        # 硬风控二次校验
        final_approved = []
        final_rejected = []

        for order in fusion_result.get("approved_instructions", []):
            validation = self.validate_order(order)
            if validation["passed"]:
                order["risk_validation"] = validation
                final_approved.append(order)
            else:
                order["risk_validation"] = validation
                final_rejected.append(order)

        # 汇总检查
        total_amount = sum(float(o.get("amount", 0)) for o in final_approved)
        if total_amount > self.hard_limits["max_total_amount"]:
            fusion_result["risk_alerts"] = fusion_result.get("risk_alerts", [])
            fusion_result["risk_alerts"].append(f"总额超上限: {total_amount:,.0f} > {self.hard_limits['max_total_amount']:,.0f}")

        fusion_result["approved_instructions"] = final_approved
        fusion_result["rejected_instructions"] = final_rejected
        fusion_result["total_amount"] = round(total_amount, 2)
        fusion_result["model"] = "deepseek-chat"

        return fusion_result

    # ------------------------------------------------------------------
    # 生成 Gate 输出
    # ------------------------------------------------------------------
    def generate_gate(self) -> dict[str, Any]:
        """生成 AI 决策门审核结果"""
        print(f"[INFO] AI 决策门启动: {self.trade_date}")

        if not self.load_all():
            print("[WARN] 部分数据未加载，审核可能不完整")

        # 融合 AI 建议
        fusion_result = self.fuse_ai_suggestion()

        # 如果 LLM 不可用或返回空，使用基础指令作为默认
        if not fusion_result.get("approved_instructions") and self.base_instructions.get("instructions"):
            print("[INFO] 使用基础指令作为默认审核结果")
            base_insts = self.base_instructions["instructions"]
            fusion_result["approved_instructions"] = []
            fusion_result["rejected_instructions"] = []
            for inst in base_insts:
                order = {
                    "instruction_id": inst.get("instruction_id", ""),
                    "code": inst.get("code", ""),
                    "full_code": inst.get("full_code", ""),
                    "name": inst.get("name", ""),
                    "action": inst.get("action", ""),
                    "qty": inst.get("qty", 0),
                    "price": inst.get("ref_price", 0),
                    "amount": inst.get("estimated_amount", 0),
                    "reason": "基础指令 (AI未参与)",
                    "source": "base",
                }
                validation = self.validate_order(order)
                order["risk_validation"] = validation
                if validation["passed"]:
                    fusion_result["approved_instructions"].append(order)
                else:
                    fusion_result["rejected_instructions"].append(order)

        # 构建 Gate 输出
        self.gate_output = {
            "meta": {
                "trade_date": self.trade_date,
                "generated_at": self.timestamp,
                "module": "ai_decision_gate",
                "mode": "semi_auto",
                "description": "AI 决策门审核结果，需人工确认 ai_approved=true 后才能执行",
            },
            "hard_limits": self.hard_limits,
            "dynamic_limits_loaded": self.dynamic_limits_loaded,
            "dynamic_limits_path": str(self.dynamic_limits_path) if self.dynamic_limits_loaded else None,
            "fusion_result": fusion_result,
            "ai_approved": False,  # ⚠️ 默认未确认，需人工改为 true
            "ai_approval_time": None,
            "notes": [
                "人工确认步骤: 将 ai_approved 字段改为 true",
                "审核通过的指令会自动进入 post-market 执行",
                "硬风控规则不可突破，突破的指令会被自动拒绝",
            ],
        }

        return self.gate_output

    # ------------------------------------------------------------------
    # 落盘
    # ------------------------------------------------------------------
    def save(self) -> Path:
        filename = f"ai_gate_{self.trade_date}.json"
        out_path = self.instructions_dir / filename
        out_path.write_text(json.dumps(self.gate_output, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[INFO] AI 决策门审核结果已保存: {out_path}")
        return out_path

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    def run(self) -> dict[str, Any]:
        """运行 AI 决策门"""
        try:
            self.generate_gate()
            out_path = self.save()

            approved_count = len(self.gate_output["fusion_result"].get("approved_instructions", []))
            rejected_count = len(self.gate_output["fusion_result"].get("rejected_instructions", []))

            print("[INFO] AI 决策门完成:")
            print(f"[INFO]   - 审核通过: {approved_count} 条")
            print(f"[INFO]   - 审核拒绝: {rejected_count} 条")
            print(f"[INFO]   - 输出文件: {out_path}")
            print("[INFO]   - 状态: 待人工确认 (ai_approved=false)")

            return {
                "status": "generated",
                "trade_date": self.trade_date,
                "output": str(out_path),
                "approved_count": approved_count,
                "rejected_count": rejected_count,
                "total_amount": self.gate_output["fusion_result"].get("total_amount", 0),
                "dynamic_limits_loaded": self.dynamic_limits_loaded,
            }
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            print(f"[ERROR] AI 决策门运行失败: {e}")
            traceback.print_exc()
            return {
                "status": "error",
                "trade_date": self.trade_date,
                "error": str(e),
            }


def run_ai_gate(trade_date: str | None = None) -> dict[str, Any]:
    return AIDecisionGate(trade_date=trade_date).run()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AI 决策门（带硬风控的半自动执行层）")
    parser.add_argument("--date", type=str, default=None, help="交易日期 YYYY-MM-DD")
    args = parser.parse_args()
    result = run_ai_gate(trade_date=args.date)
    print(json.dumps(result, ensure_ascii=False, indent=2))
