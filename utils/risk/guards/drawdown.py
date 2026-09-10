"""风控守卫 Guard 1 — 回撤强制响应 mixin

审计 item 11 (2026-09-10) 拆自 ``utils/risk_guard_integrator.py``: **零行为变更**,
仅搬移方法体与所属分隔注释, 逻辑/字段/落盘路径/日志文本逐字节保持原样。

契约: 路径与 logger 一律经 ``plan_context.XXX`` 属性式访问 (属性式访问使测试的
``monkeypatch.setattr(plan_context, ...)`` 生效); 重量级引擎依赖保持方法体内惰性 import。
"""

from __future__ import annotations


from typing import Any

from utils.risk.guards.plan_context import PlanContextMixin


class DrawdownGuardMixin(PlanContextMixin):
    # ============================================================
    # Guard 1: 回撤强制响应
    # ============================================================
    def guard_drawdown(self, pnl_report: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        """回撤检查并强制修改交易计划。

        Level 1 (5%): 预警，不改计划
        Level 2 (8%): 减仓20% + 对冲加码50%
        Level 3 (12%): 减仓40% + 对冲加码80% + 暂停建仓
        Level 4 (15%): 停止建仓 + 只做平仓

        Args:
            pnl_report: 当日盈亏报告字典
            plan: 次日交易计划字典

        Returns:
            修改后的交易计划 (写入 risk_guard.drawdown_level/action 字段)
        """
        try:
            from utils.drawdown_controller import DrawdownController
        except ImportError:
            self._log("[回撤] DrawdownController 导入失败，跳过")
            return plan

        dc = DrawdownController()

        # 从报告中提取总成本和当前市值 (v7.7修正: 使用 _get_pnl_summary 适配 portfolio_pnl.summary)
        pnl_summary = self._get_pnl_summary(pnl_report)
        total_cost = pnl_summary.get("total_cost", 0)
        total_value = pnl_summary.get("total_market_value", 0)

        if total_cost <= 0:
            self._log("[回撤] 无有效成本数据，跳过回撤检查")
            return plan

        # v7.7修正: 建仓期峰值 = max(已投入成本, 当前市值)
        # 不再与 total_capital 比较, 避免建仓期误报 -55% 回撤
        peak = max(total_cost, total_value)
        current = total_cost + pnl_summary.get("total_pnl", 0)

        result = dc.check_drawdown(peak_value=peak, current_value=current)
        level = result.get("level", 0)
        dd_pct = result.get("drawdown_pct", 0)

        self._log(f"[回撤] 当前回撤: {dd_pct:.2%}, 级别: Level {level}")

        if level == 0:
            plan.setdefault("risk_guard", {})["drawdown_level"] = 0
            plan["risk_guard"]["drawdown_action"] = "NORMAL"
            return plan

        # Level 1: 预警，不改计划但标记
        if level == 1:
            plan.setdefault("risk_guard", {})["drawdown_level"] = 1
            plan["risk_guard"]["drawdown_action"] = "WARNING"
            plan["risk_guard"]["drawdown_note"] = f"回撤{dd_pct:.2%}达到预警线，建议关注"
            self._log(f"[回撤] [WARNING] Level 1 预警: 回撤{dd_pct:.2%}")
            return plan

        # Level 2: 减仓20% + 对冲加码
        if level == 2:
            plan = self._apply_budget_cut(plan, cut_ratio=0.20)
            plan = self._apply_hedge_boost(plan, boost_pct=0.50)
            plan.setdefault("risk_guard", {})["drawdown_level"] = 2
            plan["risk_guard"]["drawdown_action"] = "REDUCE_20PCT"
            plan["risk_guard"]["drawdown_note"] = f"回撤{dd_pct:.2%}触发Level2: 预算-20%, 对冲+50%"
            self._log("[回撤] [WARN] Level 2 触发: 预算缩减20%, 对冲加码50%")
            return plan

        # Level 3: 减仓40% + 暂停建仓
        if level == 3:
            plan = self._apply_budget_cut(plan, cut_ratio=0.60)
            plan = self._apply_hedge_boost(plan, boost_pct=0.80)
            plan.setdefault("risk_guard", {})["drawdown_level"] = 3
            plan["risk_guard"]["drawdown_action"] = "REDUCE_60PCT_PAUSE_BUILD"
            plan["risk_guard"]["drawdown_note"] = f"回撤{dd_pct:.2%}触发Level3: 预算-60%, 暂停新建仓"
            # 清空建仓订单
            plan["execution_plan"] = plan.get("execution_plan", {})
            plan["execution_plan"]["morning_orders"] = []
            plan["execution_plan"]["afternoon_orders"] = []
            plan["market_state"] = plan.get("market_state", {})
            plan["market_state"]["spot_build_allowed"] = False
            self._log("[回撤] [CRITICAL] Level 3 触发: 暂停全部建仓, 对冲加码80%")
            return plan

        # Level 4: 停止一切 + 只做平仓
        if level >= 4:
            plan.setdefault("risk_guard", {})["drawdown_level"] = 4
            plan["risk_guard"]["drawdown_action"] = "FULL_STOP_LIQUIDATE"
            plan["risk_guard"]["drawdown_note"] = f"回撤{dd_pct:.2%}触发Level4: 全面止损, 只做平仓"
            plan["execution_plan"] = plan.get("execution_plan", {})
            plan["execution_plan"]["morning_orders"] = []
            plan["execution_plan"]["afternoon_orders"] = []
            plan["market_state"] = plan.get("market_state", {})
            plan["market_state"]["spot_build_allowed"] = False
            plan["market_state"]["build_allowed"] = False
            plan["market_state"]["circuit_level"] = "CRITICAL"
            self._log("[回撤] [EMERGENCY] Level 4 触发: 全面停止, 仅允许平仓+对冲")
            return plan

        return plan

    def _apply_budget_cut(self, plan: dict[str, Any], cut_ratio: float) -> dict[str, Any]:
        """缩减建仓预算"""
        # v8.6.13 P0 FIX (2026-08-01 AI 扫描):
        # 原代码 plan.get("phase", {}) 不存回 plan, 后续 plan["phase"] KeyError.
        # 改用 setdefault 链式访问, 保证嵌套结构存在.
        phase = plan.setdefault("phase", {})
        original_budget = phase.get("daily_capital", phase.get("day_capital", 150000))
        new_budget = original_budget * (1 - cut_ratio)
        phase["daily_capital"] = new_budget
        phase["day_capital"] = new_budget
        phase["budget_cut_reason"] = f"drawdown_cut_{cut_ratio:.0%}"

        # 缩减订单金额
        exec_plan = plan.setdefault("execution_plan", {})
        for session in ["morning_orders", "afternoon_orders"]:
            orders = exec_plan.get(session, [])
            for order in orders:
                if "shares" in order:
                    order["shares"] = int(order["shares"] * (1 - cut_ratio))
                if "est_amount" in order:
                    order["est_amount"] = order["est_amount"] * (1 - cut_ratio)

        return plan

    def _apply_hedge_boost(self, plan: dict[str, Any], boost_pct: float) -> dict[str, Any]:
        """加码对冲"""
        # v8.6.13 P0 FIX (2026-08-01 AI 扫描):
        # 原代码 plan.get("hedge_config", {}) 不存回 plan, 后续 plan["hedge_config"] KeyError.
        # 改用 setdefault 链式访问, 保证嵌套结构存在.
        hedge_config = plan.setdefault("hedge_config", {})
        layers = hedge_config.setdefault("layers", {})

        # 增加期货对冲比例
        layer1 = layers.setdefault("layer1_futures", {})
        current_ratio = layer1.get("ratio", 0.15)
        layer1["ratio"] = min(current_ratio * (1 + boost_pct), 0.60)
        layer1["boost_reason"] = f"drawdown_boost_{boost_pct:.0%}"

        return plan

