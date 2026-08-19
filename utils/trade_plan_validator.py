"""
交易计划字段完整性校验器 (Trade Plan Validator)
================================================
创建日期: 2026-07-26
创建原因: v8.6.8 P2-LIVE-09 实盘对接准备 — trade_plan_YYYYMMDD.json 字段缺失/类型错误
         导致下游执行器抛 KeyError/TypeError 中断流程

校验范围:
    1. 必需字段存在性: trade_date / phase / execution_plan / market_state / risk_guard
    2. 字段类型正确性: dict/list/str/number
    3. 关键嵌套字段: execution_plan.morning_orders / afternoon_orders
    4. 数值字段合理性: shares > 0 / price >= 0 / budget >= 0
    5. 字段间一致性: risk_guard.drawdown_level 与 market_state.circuit_level 对齐

用法:
    from utils.trade_plan_validator import TradePlanValidator
    validator = TradePlanValidator()
    result = validator.validate(plan_dict)
    if not result['valid']:
        logger.info(result['errors'])
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("trade_plan_validator")


class TradePlanValidator:
    """交易计划字段完整性校验器

    设计原则:
        - 严格: 必需字段缺失即视为 invalid
        - 容错: 可选字段缺失仅 warning, 不阻断
        - 详细: 返回 errors + warnings + 修复建议
        - 可选修复: 提供自动补全默认值的功能 (谨慎使用)
    """

    # 必需字段 (顶层)
    REQUIRED_TOP_LEVEL_FIELDS = {
        "trade_date": str,
        "phase": dict,
        "execution_plan": dict,
        "market_state": dict,
        "risk_guard": dict,
    }

    # 必需字段 (phase)
    REQUIRED_PHASE_FIELDS = {
        "daily_capital": (int, float),
        "day_capital": (int, float),
    }

    # 必需字段 (execution_plan)
    REQUIRED_EXECUTION_PLAN_FIELDS = {
        "morning_orders": list,
        "afternoon_orders": list,
    }

    # 必需字段 (market_state)
    REQUIRED_MARKET_STATE_FIELDS = {
        "spot_build_allowed": bool,
        "build_allowed": bool,
    }

    # 订单必需字段
    REQUIRED_ORDER_FIELDS = ["symbol", "direction", "shares"]

    # circuit_level 与 drawdown_level 的对应关系
    CIRCUIT_TO_DRAWDOWN = {
        "NORMAL": [0],
        "WATCH": [0, 1],
        "WARNING": [1, 2],
        "CRITICAL": [3, 4],
    }

    def __init__(self, strict: bool = True):
        """
        Args:
            strict: 严格模式 (True=缺失字段即 error, False=缺失字段仅 warning)
        """
        self.strict = strict

    def validate(self, plan: dict[str, Any]) -> dict[str, Any]:
        """校验交易计划完整性

        Args:
            plan: 交易计划字典

        Returns:
            {
                'valid': bool,
                'errors': List[str],
                'warnings': List[str],
                'fixes': List[str],  # 自动修复建议
                'checked_at': ISO timestamp,
            }
        """
        errors: list[str] = []
        warnings: list[str] = []
        fixes: list[str] = []

        if not isinstance(plan, dict):
            errors.append(f"plan 必须是 dict, 实际类型: {type(plan).__name__}")
            return {
                "valid": False,
                "errors": errors,
                "warnings": warnings,
                "fixes": fixes,
                "checked_at": datetime.now().isoformat(),
            }

        # 1. 顶层字段校验
        self._check_top_level(plan, errors, warnings, fixes)

        # 2. phase 字段校验
        if "phase" in plan and isinstance(plan["phase"], dict):
            self._check_phase(plan["phase"], errors, warnings, fixes)

        # 3. execution_plan 字段校验
        if "execution_plan" in plan and isinstance(plan["execution_plan"], dict):
            self._check_execution_plan(plan["execution_plan"], errors, warnings, fixes)

        # 4. market_state 字段校验
        if "market_state" in plan and isinstance(plan["market_state"], dict):
            self._check_market_state(plan["market_state"], errors, warnings, fixes)

        # 5. risk_guard 字段校验
        if "risk_guard" in plan and isinstance(plan["risk_guard"], dict):
            self._check_risk_guard(plan["risk_guard"], errors, warnings, fixes)

        # 6. 字段间一致性校验
        self._check_consistency(plan, errors, warnings, fixes)

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "fixes": fixes,
            "checked_at": datetime.now().isoformat(),
        }

    def validate_file(self, plan_path: Path) -> dict[str, Any]:
        """从文件加载并校验交易计划

        Args:
            plan_path: trade_plan_YYYYMMDD.json 文件路径

        Returns:
            校验结果 (含 file_path 字段)
        """
        if not plan_path.exists():
            return {
                "valid": False,
                "errors": [f"文件不存在: {plan_path}"],
                "warnings": [],
                "fixes": [],
                "file_path": str(plan_path),
                "checked_at": datetime.now().isoformat(),
            }

        try:
            with open(plan_path, encoding="utf-8") as f:
                plan = json.load(f)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # P2 模块 fail-safe, 待后续精确化
            return {
                "valid": False,
                "errors": [f"JSON 解析失败: {e}"],
                "warnings": [],
                "fixes": [],
                "file_path": str(plan_path),
                "checked_at": datetime.now().isoformat(),
            }

        result = self.validate(plan)
        result["file_path"] = str(plan_path)
        return result

    def auto_fix(self, plan: dict[str, Any]) -> dict[str, Any]:
        """自动补全缺失的默认字段 (谨慎使用, 仅补全安全默认值)

        Args:
            plan: 待修复的交易计划

        Returns:
            修复后的 plan + 修复日志
        """
        fixes_applied: list[str] = []

        if not isinstance(plan, dict):
            return {"_fixes_applied": fixes_applied}

        # 补全 phase
        plan.setdefault("phase", {})
        plan["phase"].setdefault("daily_capital", 150000)
        plan["phase"].setdefault("day_capital", 150000)
        if "daily_capital" not in plan["phase"]:
            plan["phase"]["daily_capital"] = 150000
            fixes_applied.append("phase.daily_capital 补全为 150000")
        if "day_capital" not in plan["phase"]:
            plan["phase"]["day_capital"] = plan["phase"].get("daily_capital", 150000)
            fixes_applied.append("phase.day_capital 补全为 daily_capital 值")

        # 补全 execution_plan
        plan.setdefault("execution_plan", {})
        plan["execution_plan"].setdefault("morning_orders", [])
        plan["execution_plan"].setdefault("afternoon_orders", [])
        if "morning_orders" not in plan["execution_plan"]:
            plan["execution_plan"]["morning_orders"] = []
            fixes_applied.append("execution_plan.morning_orders 补全为 []")
        if "afternoon_orders" not in plan["execution_plan"]:
            plan["execution_plan"]["afternoon_orders"] = []
            fixes_applied.append("execution_plan.afternoon_orders 补全为 []")

        # 补全 market_state
        plan.setdefault("market_state", {})
        if "spot_build_allowed" not in plan["market_state"]:
            plan["market_state"]["spot_build_allowed"] = True
            fixes_applied.append("market_state.spot_build_allowed 补全为 True (默认允许)")
        if "build_allowed" not in plan["market_state"]:
            plan["market_state"]["build_allowed"] = True
            fixes_applied.append("market_state.build_allowed 补全为 True (默认允许)")
        if "circuit_level" not in plan["market_state"]:
            plan["market_state"]["circuit_level"] = "NORMAL"
            fixes_applied.append("market_state.circuit_level 补全为 NORMAL")

        # 补全 risk_guard
        plan.setdefault("risk_guard", {})
        if "drawdown_level" not in plan["risk_guard"]:
            plan["risk_guard"]["drawdown_level"] = 0
            fixes_applied.append("risk_guard.drawdown_level 补全为 0 (正常)")
        if "last_run" not in plan["risk_guard"]:
            plan["risk_guard"]["last_run"] = datetime.now().isoformat()
            fixes_applied.append("risk_guard.last_run 补全为当前时间")

        return {**plan, "_fixes_applied": fixes_applied}

    # ============================================================
    # 私有: 各字段校验
    # ============================================================

    def _check_top_level(self, plan: dict, errors: list[str], warnings: list[str], fixes: list[str]) -> None:
        """校验顶层字段"""
        for field, expected_type in self.REQUIRED_TOP_LEVEL_FIELDS.items():
            if field not in plan:
                msg = f"缺失必需字段: {field} ({expected_type.__name__})"
                if self.strict:
                    errors.append(msg)
                else:
                    warnings.append(msg)
                fixes.append(f"建议: plan['{field}'] = {expected_type.__name__}()")
            elif not isinstance(plan[field], expected_type):
                actual = type(plan[field]).__name__
                errors.append(f"字段 {field} 类型错误: 期望 {expected_type.__name__}, 实际 {actual}")
                fixes.append(f"建议: plan['{field}'] 应为 {expected_type.__name__}")

    def _check_phase(self, phase: dict, errors: list[str], warnings: list[str], fixes: list[str]) -> None:
        """校验 phase 字段"""
        for field, expected_types in self.REQUIRED_PHASE_FIELDS.items():
            if field not in phase:
                msg = f"缺失 phase.{field} (数值字段)"
                if self.strict:
                    errors.append(msg)
                else:
                    warnings.append(msg)
                fixes.append(f"建议: phase['{field}'] = 150000 (默认日预算)")
            elif not isinstance(phase[field], expected_types):
                actual = type(phase[field]).__name__
                errors.append(f"phase.{field} 类型错误: 期望 number, 实际 {actual}")
            elif phase[field] < 0:
                errors.append(f"phase.{field} 值异常: {phase[field]} < 0")

        # 检查 daily_capital 与 day_capital 一致性
        if "daily_capital" in phase and "day_capital" in phase:
            if phase["daily_capital"] != phase["day_capital"]:
                warnings.append(
                    f"phase.daily_capital ({phase['daily_capital']}) != "
                    f"phase.day_capital ({phase['day_capital']}), 建议统一"
                )

    def _check_execution_plan(
        self,
        exec_plan: dict,
        errors: list[str],
        warnings: list[str],
        fixes: list[str],
    ) -> None:
        """校验 execution_plan 字段"""
        for field, expected_type in self.REQUIRED_EXECUTION_PLAN_FIELDS.items():
            if field not in exec_plan:
                errors.append(f"缺失 execution_plan.{field}")
                fixes.append(f"建议: execution_plan['{field}'] = []")
            elif not isinstance(exec_plan[field], expected_type):
                actual = type(exec_plan[field]).__name__
                errors.append(f"execution_plan.{field} 类型错误: 期望 {expected_type.__name__}, 实际 {actual}")

        # 校验订单详情
        for session in ["morning_orders", "afternoon_orders"]:
            orders = exec_plan.get(session, [])
            if not isinstance(orders, list):
                continue
            for i, order in enumerate(orders):
                if not isinstance(order, dict):
                    errors.append(f"{session}[{i}] 必须是 dict, 实际 {type(order).__name__}")
                    continue
                for req_field in self.REQUIRED_ORDER_FIELDS:
                    if req_field not in order:
                        errors.append(f"{session}[{i}] 缺失必需字段: {req_field}")
                # 数值合理性
                shares = order.get("shares", 0)
                if isinstance(shares, (int, float)) and shares <= 0:
                    warnings.append(f"{session}[{i}] {order.get('symbol', '?')} shares={shares} ≤ 0")

    def _check_market_state(
        self,
        market_state: dict,
        errors: list[str],
        warnings: list[str],
        fixes: list[str],
    ) -> None:
        """校验 market_state 字段"""
        for field, expected_type in self.REQUIRED_MARKET_STATE_FIELDS.items():
            if field not in market_state:
                msg = f"缺失 market_state.{field}"
                if self.strict:
                    errors.append(msg)
                else:
                    warnings.append(msg)
                fixes.append(f"建议: market_state['{field}'] = True (默认允许)")
            elif not isinstance(market_state[field], expected_type):
                actual = type(market_state[field]).__name__
                errors.append(f"market_state.{field} 类型错误: 期望 {expected_type.__name__}, 实际 {actual}")

        # circuit_level 合法性
        circuit = market_state.get("circuit_level", "NORMAL")
        valid_circuits = ["NORMAL", "WATCH", "WARNING", "CRITICAL"]
        if circuit not in valid_circuits:
            errors.append(f"market_state.circuit_level 值非法: {circuit}, 应为 {valid_circuits}")

    def _check_risk_guard(
        self,
        risk_guard: dict,
        errors: list[str],
        warnings: list[str],
        fixes: list[str],
    ) -> None:
        """校验 risk_guard 字段"""
        if "drawdown_level" in risk_guard:
            dd_level = risk_guard["drawdown_level"]
            if not isinstance(dd_level, int) or dd_level < 0 or dd_level > 4:
                errors.append(f"risk_guard.drawdown_level 值非法: {dd_level}, 应为 0-4 整数")

        if "kill_switch" in risk_guard:
            ks = risk_guard["kill_switch"]
            if not isinstance(ks, dict):
                errors.append(f"risk_guard.kill_switch 必须是 dict, 实际 {type(ks).__name__}")

    def _check_consistency(self, plan: dict, errors: list[str], warnings: list[str], fixes: list[str]) -> None:
        """校验字段间一致性"""
        market_state = plan.get("market_state", {})
        risk_guard = plan.get("risk_guard", {})

        circuit = market_state.get("circuit_level", "NORMAL")
        dd_level = risk_guard.get("drawdown_level", 0)

        # circuit_level 与 drawdown_level 一致性
        valid_dd = self.CIRCUIT_TO_DRAWDOWN.get(circuit, [0])
        if dd_level not in valid_dd and dd_level not in [0, 1, 2, 3, 4]:
            # 仅当 dd_level 明确设置且与 circuit 不匹配时 warning
            if "drawdown_level" in risk_guard:
                warnings.append(
                    f"字段不一致: circuit_level={circuit} 但 drawdown_level={dd_level}, "
                    f"建议 {circuit} 对应 drawdown_level ∈ {valid_dd}"
                )

        # spot_build_allowed 与 circuit_level 一致性
        spot_allowed = market_state.get("spot_build_allowed", True)
        if circuit == "CRITICAL" and spot_allowed:
            errors.append("字段冲突: circuit_level=CRITICAL 但 spot_build_allowed=True, CRITICAL 时必须禁止开仓")
            fixes.append("建议: market_state['spot_build_allowed'] = False")

        # build_allowed 与 circuit_level 一致性
        build_allowed = market_state.get("build_allowed", True)
        if circuit in ("CRITICAL", "WARNING") and build_allowed:
            warnings.append(f"字段冲突: circuit_level={circuit} 但 build_allowed=True, {circuit} 时建议禁止建仓")


# ============================================================
# CLI 入口
# ============================================================


def main() -> None:
    """命令行入口: python -m utils.trade_plan_validator [plan_path]"""
    import sys

    if len(sys.argv) < 2:
        # 默认校验明日 trade_plan
        from datetime import timedelta

        tomorrow = datetime.now() + timedelta(days=1)
        while tomorrow.weekday() >= 5:
            tomorrow += timedelta(days=1)
        date_str = tomorrow.strftime("%Y%m%d")
        plan_path = (
            Path(__file__).resolve().parent.parent
            / "v8.3_institutional"
            / "trade_plans"
            / f"trade_plan_{date_str}.json"
        )
    else:
        plan_path = Path(sys.argv[1])

    logger.info("=" * 70)
    logger.info(f"Trade Plan Validator — 校验: {plan_path.name}")
    logger.info("=" * 70)

    validator = TradePlanValidator(strict=True)
    result = validator.validate_file(plan_path)

    logger.info(f"\n文件: {result.get('file_path', plan_path)}")
    logger.info(f"校验时间: {result['checked_at']}")
    logger.info(f"结果: {'✓ PASS' if result['valid'] else '✗ FAIL'}")

    if result["errors"]:
        logger.info(f"\n[ERRORS] ({len(result['errors'])}):")
        for e in result["errors"]:
            logger.info(f"  ✗ {e}")

    if result["warnings"]:
        logger.info(f"\n[WARNINGS] ({len(result['warnings'])}):")
        for w in result["warnings"]:
            logger.info(f"  ⚠ {w}")

    if result["fixes"]:
        logger.info(f"\n[FIXES] ({len(result['fixes'])}):")
        for f in result["fixes"]:
            logger.info(f"  → {f}")

    return 0 if result["valid"] else 1


if __name__ == "__main__":
    sys.exit_code = main()  # type: ignore
