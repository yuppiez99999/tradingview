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
        # 前置 Optional[Type] 注解 — 消除 import 失败分支 None 赋值 [assignment]
        _KillSwitchCls: type[Any] | None
        try:
            from utils.kill_switch import KillSwitch as _KillSwitchCls
        except ImportError:
            self._log("[KillSwitch] 模块导入失败，使用降级检查")
            _KillSwitchCls = None

        ks = _KillSwitchCls() if _KillSwitchCls is not None else None
        pnl_summary = self._get_pnl_summary(pnl_report)

        # 1. 保证金使用率检查
        # P0-D 修复 (2026-07-26 v8.6.5): 当 pnl_report 中 margin_used/total_equity 为 None 时
        # 回退到 KillSwitch._estimate_margin_from_positions() 真实估算 (基于 positions.json)
        # 原始 bug: pnl_summary.get('margin_used', 0) 在字段存在但值为 None 时返回 None (非默认值 0)
        # 导致 None/None 抛 TypeError 被外层 try/except 吞掉, trade_plan 显示 level=0
        # 但同期 kill_switch_events.jsonl 显示 L2 已触发 (margin_usage=80.36%) — EOD Guard 失效
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
                except Exception as e:  # noqa: BLE001  # P0-7: fail-safe 兜底必须捕获所有异常
                    # [2026-08-30 P0-7 FIX] P0-D 兜底是 fail-safe 路径, 应捕获所有异常
                    # (含 positions.json missing 等裸 Exception) 降级到保守值 0.50,
                    # 而非让异常传播导致 EOD 流程中断. 原元组仅含 5 个子类, 漏掉 Exception 基类.
                    self._log(f"[KillSwitch] [P0-D FIX] 回退失败: {e}, 使用保守值 0.50")
                    margin_usage = 0.50
            else:
                margin_usage = 0.50
                self._log("[KillSwitch] [P0-D FIX] KillSwitch 模块不可用, 使用保守值 0.50")
        else:
            margin_usage = margin_used / total_equity if total_equity > 0 else 0

        if ks:
            margin_status = ks.check_margin_status(margin_usage)
        else:
            # v8.6.13 P0 FIX: 降级阈值与 kill_switch.py 主体对齐
            # 原降级用 L3=0.75/L2=0.65, 与主体 L3=0.95/L2=0.75 不一致
            # 模块可用性变化 (如导入失败) 会导致同一保证金占用率触发不同熔断级别
            if margin_usage >= 0.95:
                margin_status = {
                    "level": "L3",
                    "can_trade": False,
                    "can_open": False,
                    "action": "MARGIN_CRITICAL: 保证金≥95% (extreme_call)",
                }
            elif margin_usage >= 0.75:
                margin_status = {
                    "level": "L2",
                    "can_trade": True,
                    "can_open": False,
                    "action": "MARGIN_LIMIT: 保证金≥75%",
                }
            elif margin_usage >= 0.50:
                margin_status = {
                    "level": "L1",
                    "can_trade": True,
                    "can_open": True,
                    "action": "MARGIN_WATCH: 保证金≥50%",
                }
            else:
                margin_status = {
                    "level": "OK",
                    "can_trade": True,
                    "can_open": True,
                    "action": "正常",
                }

        self._log(f"[KillSwitch] 保证金 {margin_usage:.1%} → {margin_status['level']}: {margin_status['action']}")

        # 2. 持仓集中度检查
        # v8.6.13 P0 FIX: 用 _extract_positions 提取 (兼容 list/dict), 转 dict 给 check_concentration
        concentration_status = None
        positions_list = self._extract_positions(pnl_report) if pnl_report else []
        if ks and positions_list:
            # check_concentration 期望 {code: {'market_value': float}} 或 {code: float}
            # 从 list 构造 dict, 优先用 code, 其次 symbol
            positions_dict: dict[str, Any] = {}
            for p in positions_list:
                if not isinstance(p, dict):
                    continue
                code = p.get("code") or p.get("symbol") or ""
                if not code:
                    continue
                # market_value 优先, 其次 est_market_value, 最后用 amount*est_price 估算
                mv = p.get("market_value")
                if mv is None:
                    mv = p.get("est_market_value")
                if mv is None:
                    amount = p.get("amount", 0) or 0
                    est_price = p.get("est_price", 0) or 0
                    mv = amount * est_price
                positions_dict[code] = mv
            if positions_dict:
                try:
                    concentration_status = ks.check_concentration(positions_dict)
                    self._log(
                        f"[KillSwitch] 集中度检查: {concentration_status.get('level', 'OK')} "
                        f"({concentration_status.get('max_concentration', 0):.1%} @ "
                        f"{concentration_status.get('max_concentration_code', '')})"
                    )
                except (
                    ValueError,
                    KeyError,
                    TypeError,
                    AttributeError,
                    OSError,
                    RuntimeError,
                ) as e:
                    self._log(f"[KillSwitch] 集中度检查异常: {e}")
                    concentration_status = None

        # 3. 响应动作
        # v8.6.13 P0 FIX: 集中度检查结果合并到 ks_level_enum 判断
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

        # v8.6.7 修复 (P1): 用 level 判断 L2/L3, 而非 can_trade
        # 原始 bug: can_trade = (level < 2), 所以 L2 时 can_trade=False,
        # 被误判为 L3 执行清空所有订单 (应只过滤 BUY)
        # 正确逻辑: L3 清空所有, L2 过滤 BUY 保留 SELL, L1 仅预警
        ks_level_raw = margin_status.get("level", 0)

        # P1-Q6 修复 (2026-07-26): 用 IntEnum + 专用解析函数替代嵌套三元运算符
        ks_level_enum = parse_kill_switch_level(ks_level_raw, margin_usage)

        # v8.6.13 P0 FIX: 集中度级别合并 (取 margin 和 concentration 较高 level)
        if concentration_status:
            conc_level_enum = parse_kill_switch_level(concentration_status.get("level", "OK"), 0.0)
            if conc_level_enum > ks_level_enum:
                self._log(
                    f"[KillSwitch] [WARNING] 集中度 {conc_level_enum.name} 高于保证金 "
                    f"{ks_level_enum.name}, 升级到 {conc_level_enum.name}"
                )
                ks_level_enum = conc_level_enum
                plan["risk_guard"]["kill_switch"]["level"] = conc_level_enum.name

        # L3: 强制停止一切 (清空所有订单)
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

        # L2: 禁止开仓 (过滤 BUY, 保留 SELL)
        if ks_level_enum == KillSwitchLevel.L2:
            plan["market_state"] = plan.get("market_state", {})
            # 只在 circuit_level 未被更高优先级 Guard 设为 CRITICAL 时才设为 WARNING
            if plan["market_state"].get("circuit_level") != "CRITICAL":
                plan["market_state"]["circuit_level"] = "WARNING"
            plan["market_state"]["spot_build_allowed"] = False
            # v8.6.8 P0-02 FIX (2026-07-26): L2 同步 build_allowed=False
            # 原代码只设 spot_build_allowed=False, build_allowed 仍为 True, 与一致性校验矛盾
            plan["market_state"]["build_allowed"] = False
            plan["execution_plan"] = plan.get("execution_plan", {})
            # v8.6.8 P0-02 FIX: 修复字段名 bug
            # 原代码 o.get('direction') == 'SELL' 但 trade_plan 现货订单字段是 'side' (无 direction)
            # 所有订单 direction=None, None == 'SELL' 为 False, 导致所有订单被清空 (包括 SELL)
            # 实际 trade_plan 现货订单全部是 side=BUY, 所以这里应过滤 side=BUY 保留 side=SELL
            # 同时检查 direction 字段 (对冲订单可能用 direction)
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

        # L1: 仅预警, 不修改订单
        if ks_level_enum == KillSwitchLevel.L1:
            plan["market_state"] = plan.get("market_state", {})
            if plan["market_state"].get("circuit_level") not in ("CRITICAL", "WARNING"):
                plan["market_state"]["circuit_level"] = "WATCH"
            self._log("[KillSwitch] [WATCH] L1 触发: 保证金预警, 不修改订单")

        return plan
