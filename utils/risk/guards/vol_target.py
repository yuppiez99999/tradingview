"""风控守卫 Guard 2 — 波动率目标缩仓 mixin

审计 item 11 (2026-09-10) 拆自 ``utils/risk_guard_integrator.py``: **零行为变更**,
仅搬移方法体与所属分隔注释, 逻辑/字段/落盘路径/日志文本逐字节保持原样。

契约: 路径与 logger 一律经 ``plan_context.XXX`` 属性式访问 (属性式访问使测试的
``monkeypatch.setattr(plan_context, ...)`` 生效); 重量级引擎依赖保持方法体内惰性 import。
"""

from __future__ import annotations

import json
from typing import Any

from utils.risk.guards import plan_context
from utils.risk.guards.plan_context import PlanContextMixin


class VolTargetGuardMixin(PlanContextMixin):
    # ============================================================
    # Guard 2: 波动率目标缩仓
    # ============================================================
    def guard_vol_target(self, pnl_report: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        """波动率目标控制 - 缩减建仓预算。

        当 vol_scale < 0.80 时按比例缩减次日建仓预算与订单金额。

        Args:
            pnl_report: 当日盈亏报告字典
            plan: 次日交易计划字典

        Returns:
            修改后的交易计划 (写入 risk_guard.vol_scale/vol_action 字段)
        """
        try:
            from utils.vol_target_controller import VolTargetController
        except ImportError:
            self._log("[波动率] VolTargetController 导入失败，跳过")
            return plan

        vtc = VolTargetController()

        # 尝试从价格更新获取日收益率
        daily_returns = self._extract_daily_returns(pnl_report)

        # 先计算已实现波动率，再计算 vol_scale
        realized_vol = vtc.calc_realized_vol(daily_returns) if daily_returns else None
        # None 守卫 — 消除 [union-attr]
        if realized_vol is None:
            vol_scale = None
        else:
            vol_scale = vtc.calc_vol_scale(realized_vol)

        if vol_scale is None or vol_scale >= 0.80:
            self._log(f"[波动率] vol_scale={vol_scale or 'N/A'}, 无需缩仓")
            plan.setdefault("risk_guard", {})["vol_scale"] = vol_scale
            plan["risk_guard"]["vol_action"] = "NORMAL"
            return plan

        # 缩减预算
        # P0 bug 修复 (2026-09-01): 原实现 `plan.get("phase", {})` 在 plan 无
        # "phase" 键时返回脱离 plan 的临时 dict — original_daily_capital 写在
        # 临时 dict 上丢失, 而下方 plan["phase"][...] 直接 KeyError 崩溃。
        # setdefault 保证 phase 挂回 plan (无则创建, 有则复用原引用)。
        phase = plan.setdefault("phase", {})
        original_budget = phase.get("daily_capital", phase.get("day_capital", 150000))
        adjusted_budget = original_budget * max(vol_scale, 0.30)

        # v8.6.8 P0-03b FIX (2026-07-26): 保存原始预算字段便于审计追溯
        # 原始 bug: 直接覆写 phase.daily_capital 导致验证脚本无法判断 vol_scale 是否已应用
        # 修复: 在 phase 中保留 original_daily_capital 字段, 缩减后 daily_capital 仍可追溯
        if "original_daily_capital" not in phase:
            phase["original_daily_capital"] = round(original_budget, 2)
        plan["phase"]["daily_capital"] = round(adjusted_budget, 2)
        plan["phase"]["day_capital"] = round(adjusted_budget, 2)
        plan["phase"]["vol_scale_applied"] = round(max(vol_scale, 0.30), 4)
        plan.setdefault("risk_guard", {})["vol_scale"] = vol_scale
        plan["risk_guard"]["vol_action"] = f"SCALE_DOWN_{vol_scale:.2f}"
        plan["risk_guard"][
            "vol_note"
        ] = f"波动率目标控制: vol_scale={vol_scale:.3f}, 预算 {original_budget:,.0f} → {adjusted_budget:,.0f}"

        # v8.6.8 P0-03 FIX (2026-07-26): vol_scale 必须应用到 execution_plan 订单金额
        # 原始 bug: 仅缩减 phase.daily_capital, 但 execution_plan.morning_orders/afternoon_orders
        # 中的 shares 和 est_amount 保持原值, 导致:
        #   - trade_plan.phase.daily_capital=60000 (缩减后)
        #   - trade_plan.execution_plan.total_amount=219977.9 (未缩减, 仍按原 200K)
        #   - 实盘执行器按 total_amount 下单, 严重超预算 267%
        # 修复: 同步缩减订单 shares (按 vol_scale 比例) 和 est_amount, 并更新 execution_plan.day_capital
        # v8.6.8 P0-03a-fix (2026-07-26): L2 触发后 BUY 订单已被 overnight_gap 清空,
        # 此时无订单可缩减是预期行为, vol_scale 只缩减 phase.daily_capital,
        # 通过 vol_scale_executed_summary 记录无订单缩减原因 (避免验证脚本误判)
        scale_factor = max(vol_scale, 0.30) / 1.0  # vol_scale 已经过 max(vol_scale, 0.30) 处理
        exec_plan = plan.get("execution_plan", {})
        exec_plan["day_capital"] = round(adjusted_budget, 2)
        exec_plan["original_day_capital"] = round(original_budget, 2)
        total_amount_after = 0.0
        scaled_buy_count = 0
        for session_key in ("morning_orders", "afternoon_orders"):
            orders = exec_plan.get(session_key, []) or []
            for o in orders:
                # 仅缩减现货 BUY 订单 (side=BUY), 保留 SELL 平仓订单原值
                if str(o.get("side", "")).upper() == "BUY":
                    scaled_buy_count += 1
                    if "shares" in o:
                        original_shares = int(o["shares"])
                        new_shares = max(int(original_shares * scale_factor), 0)
                        o["shares"] = new_shares
                        # 标记缩减原因 (审计追溯)
                        o["vol_scale_applied"] = round(scale_factor, 4)
                        o["original_shares"] = original_shares
                    if "est_amount" in o:
                        o["est_amount"] = round(float(o["est_amount"]) * scale_factor, 2)
                    if "limit_price" in o:
                        # 限价保持原值 (限价是价格, 与数量独立), 不缩减
                        pass
                total_amount_after += float(o.get("est_amount", 0))
        # 更新 execution_plan.total_amount 和 day_capital
        if "total_amount" in exec_plan:
            exec_plan["total_amount"] = round(total_amount_after, 2)
        # v8.6.8 P0-03a-fix: 记录 vol_scale 应用状态摘要 (审计追溯)
        # 区分 "L2 触发后无订单可缩减" vs "vol_scale 未应用" 两种场景
        plan["risk_guard"]["vol_scale_executed_summary"] = {
            "scale_factor": round(scale_factor, 4),
            "original_budget": round(original_budget, 2),
            "adjusted_budget": round(adjusted_budget, 2),
            "scaled_buy_orders": scaled_buy_count,
            "note": (
                "L2 触发后 BUY 订单已被 overnight_gap 清空, vol_scale 仅缩减 phase.daily_capital"
                if scaled_buy_count == 0
                else f"已缩减 {scaled_buy_count} 笔 BUY 订单的 shares 和 est_amount"
            ),
        }
        plan["risk_guard"]["vol_note"] = (
            f"波动率目标控制: vol_scale={vol_scale:.3f}, "
            f"预算 {original_budget:,.0f} → {adjusted_budget:,.0f}, "
            f"订单金额已同步缩减 (factor={scale_factor:.3f})"
        )

        self._log(
            f"[波动率] vol_scale={vol_scale:.3f}, 预算缩减: "
            f"{original_budget:,.0f} → {adjusted_budget:,.0f}, "
            f"订单金额已同步缩减 (factor={scale_factor:.3f})"
        )
        return plan

    def _extract_daily_returns(self, pnl_report: dict[str, Any]) -> list[Any]:
        """从报告提取日收益率序列"""
        # 尝试从 v76 增强报告获取历史日收益率
        try:
            report_files = sorted(plan_context.REPORTS_DIR.glob("daily_pnl_report_*.json"))
            returns = []
            for rf in report_files[-22:]:  # 最近22个交易日
                with open(rf, encoding="utf-8") as f:
                    data = json.load(f)
                # v7.7修正: 适配 portfolio_pnl.summary 嵌套结构
                pnl_sum = data.get("portfolio_pnl", {}).get("summary", {})
                if not pnl_sum:
                    pnl_sum = data.get("pnl_summary", {})  # 兼容旧格式
                pnl_pct = pnl_sum.get("total_pnl_pct", 0)
                returns.append(pnl_pct / 100.0)  # 转为小数
            return returns if len(returns) >= 5 else []
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError):
            return []

