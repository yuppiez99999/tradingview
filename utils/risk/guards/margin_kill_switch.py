"""风控守卫 Guard 5 — 保证金/持仓集中度熔断 (KillSwitch 三级协议) mixin

审计 item 11 (2026-09-10) 拆自 ``utils/risk_guard_integrator.py``: **零行为变更**,
仅搬移方法体与所属分隔注释, 逻辑/字段/落盘路径/日志文本逐字节保持原样。

契约: 路径与 logger 一律经 ``plan_context.XXX`` 属性式访问; 重量级引擎依赖保持方法体内惰性 import。
"""

from __future__ import annotations

from typing import Any

from utils.risk.guards.kill_switch_level import (
    KillSwitchLevel,
    parse_kill_switch_level,
)
from utils.risk.guards.plan_context import PlanContextMixin


class MarginKillSwitchGuardMixin(PlanContextMixin):
    # ============================================================
    # Guard 5: 保证金/持仓集中度熔断 (KillSwitch 三级协议)
    # ============================================================

    @staticmethod
    def _import_kill_switch_cls() -> type[Any] | None:
        """导入 KillSwitch 类, 失败返回 None."""
        try:
            from utils.kill_switch import KillSwitch as _Cls
            return _Cls
        except ImportError:
            return None

    def _calc_margin_usage(
        self, pnl_summary: dict[str, Any], ks: Any | None
    ) -> float:
        """计算保证金使用率, 缺失时回退到 _estimate_margin_from_positions."""
        margin_used = pnl_summary.get("margin_used")
        total_equity = pnl_summary.get("total_equity")

        if margin_used is None or total_equity is None or total_equity <= 0:
            if ks:
                try:
                    margin_usage = ks._estimate_margin_from_positions()
                    self._log(
                        f"[KillSwitch] [P0-D FIX] pnl_report 字段缺失 "
                        f"(margin_used={margin_used}, total_equity={total_equity}), "
                        f"回退到 _estimate_margin_from_positions() = {margin_usage:.1%}"
                    )
                except Exception as e:  # noqa: BLE001
                    self._log(f"[KillSwitch] [P0-D FIX] 回退失败: {e}, 使用保守值 0.50")
                    margin_usage = 0.50
            else:
                margin_usage = 0.50
                self._log("[KillSwitch] [P0-D FIX] KillSwitch 模块不可用, 使用保守值 0.50")
        else:
            margin_usage = margin_used / total_equity if total_equity > 0 else 0
        return margin_usage

    @staticmethod
    def _get_margin_status_degraded(margin_usage: float) -> dict[str, Any]:
        """降级保证金状态检查 (KillSwitch 模块不可用时)."""
        if margin_usage >= 0.95:
            return {"level": "L3", "can_trade": False, "can_open": False,
                    "action": "MARGIN_CRITICAL: 保证金≥95% (extreme_call)"}
        if margin_usage >= 0.75:
            return {"level": "L2", "can_trade": True, "can_open": False,
                    "action": "MARGIN_LIMIT: 保证金≥75%"}
        if margin_usage >= 0.50:
            return {"level": "L1", "can_trade": True, "can_open": True,
                    "action": "MARGIN_WATCH: 保证金≥50%"}
        return {"level": "OK", "can_trade": True, "can_open": True, "action": "正常"}

    def _check_concentration(
        self, ks: Any | None, pnl_report: dict[str, Any]
    ) -> dict[str, Any] | None:
        """持仓集中度检查, 返回 status dict 或 None."""
        positions_list = self._extract_positions(pnl_report) if pnl_report else []
        if not ks or not positions_list:
            return None

        positions_dict: dict[str, Any] = {}
        for p in positions_list:
            if not isinstance(p, dict):
                continue
            code = p.get("code") or p.get("symbol") or ""
            if not code:
                continue
            mv = p.get("market_value")
            if mv is None:
                mv = p.get("est_market_value")
            if mv is None:
                amount = p.get("amount", 0) or 0
                est_price = p.get("est_price", 0) or 0
                mv = amount * est_price
            positions_dict[code] = mv

        if not positions_dict:
            return None

        try:
            status = ks.check_concentration(positions_dict)
            self._log(
                f"[KillSwitch] 集中度检查: {status.get('level', 'OK')} "
                f"({status.get('max_concentration', 0):.1%} @ "
                f"{status.get('max_concentration_code', '')})"
            )
            return status
        except (
            ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError
        ) as e:
            self._log(f"[KillSwitch] 集中度检查异常: {e}")
            return None

    def _apply_ks_response(
        self, plan: dict[str, Any], ks_level_enum: Any
    ) -> dict[str, Any] | None:
        """执行 L3/L2/L1 响应动作, 返回 plan 表示终止, None 表示继续."""
        if ks_level_enum >= KillSwitchLevel.L3:
            plan["market_state"] = plan.get("market_state", {})
            plan["market_state"]["circuit_level"] = "CRITICAL"
            plan["market_state"]["build_allowed"] = False
            plan["market_state"]["spot_build_allowed"] = False
            plan["execution_plan"] = plan.get("execution_plan", {})
            plan["execution_plan"]["morning_orders"] = []
            plan["execution_plan"]["afternoon_orders"] = []
            self._log(f"[KillSwitch] [EMERGENCY] {ks_level_enum.name} 触发: 全面停止交易, 仅允许平仓")
            return plan

        if ks_level_enum == KillSwitchLevel.L2:
            plan["market_state"] = plan.get("market_state", {})
            if plan["market_state"].get("circuit_level") != "CRITICAL":
                plan["market_state"]["circuit_level"] = "WARNING"
            plan["market_state"]["spot_build_allowed"] = False
            plan["market_state"]["build_allowed"] = False
            plan["execution_plan"] = plan.get("execution_plan", {})
            for session in ["morning_orders", "afternoon_orders"]:
                orders = plan["execution_plan"].get(session, [])
                plan["execution_plan"][session] = [
                    o
                    for o in orders
                    if o.get("side", "").upper() != "BUY"
                    and o.get("direction", "").upper() not in ("BUY", "BUY_OPEN", "BUY_PUT")
                ]
            self._log("[KillSwitch] [WARN] L2 触发: 禁止开仓, 仅允许平仓 (保留 SELL 订单)")
            return plan

        if ks_level_enum == KillSwitchLevel.L1:
            plan["market_state"] = plan.get("market_state", {})
            if plan["market_state"].get("circuit_level") not in ("CRITICAL", "WARNING"):
                plan["market_state"]["circuit_level"] = "WATCH"
            self._log("[KillSwitch] [WATCH] L1 触发: 保证金预警, 不修改订单")

        return None

    def guard_kill_switch(self, pnl_report: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        """全局熔断检查 - 集成 utils/kill_switch 三级协议

        L1 (Watch): 保证金≥50% 或 单票集中度≥25% → 预警标记
        L2 (Limit): 保证金≥75% 或 单票集中度≥35% → 禁止开仓
        L3 (Critical): 保证金≥95% (extreme_call) 或 单票集中度≥50% → 强制平仓

        v8.6.13 P0 FIX (2026-08-01 AI 扫描):
            1. 降级逻辑阈值与 kill_switch.py 主体对齐 (L3=0.95/L2=0.75/L1=0.50)
               原降级用 L3=0.75/L2=0.65, 与主体 L3=0.95 不一致, 模块可用性变化导致风控行为剧变
            2. 集中度检查结果合并到 ks_level_enum 判断 (取 margin 和 concentration 较高 level)
               原代码计算出 concentration_status 后只 log, 死代码, 集中度熔断完全失效
            3. positions 提取用 _extract_positions (处理 list/dict), 转 dict 给 check_concentration
               原代码 pnl_summary.get("positions", ...) 可能返回 list, check_concentration 用 .items() 会 AttributeError
        """
        _KillSwitchCls = self._import_kill_switch_cls()
        if _KillSwitchCls is None:
            self._log("[KillSwitch] 模块导入失败，使用降级检查")

        ks = _KillSwitchCls() if _KillSwitchCls is not None else None
        pnl_summary = self._get_pnl_summary(pnl_report)

        # 1. 保证金使用率检查
        margin_usage = self._calc_margin_usage(pnl_summary, ks)

        if ks:
            margin_status = ks.check_margin_status(margin_usage)
        else:
            margin_status = self._get_margin_status_degraded(margin_usage)

        self._log(f"[KillSwitch] 保证金 {margin_usage:.1%} → {margin_status['level']}: {margin_status['action']}")

        # 2. 持仓集中度检查
        concentration_status = self._check_concentration(ks, pnl_report)

        # 3. 响应动作
        plan.setdefault("risk_guard", {})["kill_switch"] = {
            "level": margin_status.get("level"),
            "margin_usage": round(margin_usage, 4),
            "can_trade": margin_status.get("can_trade", True),
            "can_open": margin_status.get("can_open", True),
        }
        if concentration_status:
            plan["risk_guard"]["kill_switch"]["concentration"] = {
                "level": concentration_status.get("level", "OK"),
                "max_concentration": round(concentration_status.get("max_concentration", 0), 4),
                "max_concentration_code": concentration_status.get("max_concentration_code", ""),
            }

        ks_level_raw = margin_status.get("level", 0)
        ks_level_enum = parse_kill_switch_level(ks_level_raw, margin_usage)

        # 集中度级别合并 (取 margin 和 concentration 较高 level)
        if concentration_status:
            conc_level_enum = parse_kill_switch_level(concentration_status.get("level", "OK"), 0.0)
            if conc_level_enum > ks_level_enum:
                self._log(
                    f"[KillSwitch] [WARNING] 集中度 {conc_level_enum.name} 高于保证金 "
                    f"{ks_level_enum.name}, 升级到 {conc_level_enum.name}"
                )
                ks_level_enum = conc_level_enum
                plan["risk_guard"]["kill_switch"]["level"] = conc_level_enum.name

        result = self._apply_ks_response(plan, ks_level_enum)
        return result if result is not None else plan
