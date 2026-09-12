"""风控守卫 Guard 8 — 重大负面新闻熔断 mixin

审计 item 11 (2026-09-10) 拆自 ``utils/risk_guard_integrator.py``: **零行为变更**,
仅搬移方法体与所属分隔注释, 逻辑/字段/落盘路径/日志文本逐字节保持原样。

契约: 路径与 logger 一律经 ``plan_context.XXX`` 属性式访问; 重量级引擎依赖保持方法体内惰性 import。
"""

from __future__ import annotations

from typing import Any

from utils.risk.guards.plan_context import PlanContextMixin


class SentimentGuardMixin(PlanContextMixin):
    # ============================================================
    # Guard 8: 重大负面新闻熔断 (P2-增强, v8.4 2026-07-30)
    # ============================================================
    # 顶级对冲基金标准: 重大负面新闻 (财务造假/监管立案/重大事故) 必须 fail-closed
    # 触发条件: 任一持仓标的 IFinDNewsAnalyzer 返回 direction="negative" 且 confidence>=0.9
    # 响应动作:
    #   1. market_state.build_allowed = False        (禁止新建仓)
    #   2. market_state.spot_build_allowed = False  (禁止现货建仓)
    #   3. market_state.circuit_level = "WARNING"   (预警级别, 不强制平仓)
    #   4. risk_guard.sentiment_breaking_news 记录触发详情
    # 降级策略:
    #   - iFinD MCP 不可用 → 跳过 (不阻塞主流程, 记录 SKIP)
    #   - 任一标的研判异常 → 跳过该标的 (不阻塞其他标的)
    #   - 整个 Guard 崩溃 → fail-open (负面新闻检查失败不阻塞交易, 避免误杀)
    #     *理由: 负面新闻检查是 P2-增强项, 不应阻断核心 P0/P1 风控链路
    # Feature Flag: USE_SENTIMENT_GUARD (默认 True, 显式 set False 可禁用)
    # ============================================================
    def guard_sentiment_breaking_news(self, pnl_report: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        """重大负面新闻 Guard - 检测持仓标的的极端负面新闻

        触发条件:
            任一持仓标的出现 direction="negative" 且 confidence>=0.9 的新闻

        Args:
            pnl_report: 当日盈亏报告 (用于提取持仓列表)
            plan: 次日交易计划

        Returns:
            修改后的交易计划 (写入 risk_guard.sentiment_breaking_news)
        """
        # Feature Flag 检查
        import os

        flag_val = os.getenv("USE_SENTIMENT_GUARD", "True")
        if flag_val.lower() not in ("true", "1", "yes", "on"):
            self._log("[负面新闻] Feature Flag USE_SENTIMENT_GUARD=False, 跳过")
            plan.setdefault("risk_guard", {})["sentiment_breaking_news"] = {
                "status": "DISABLED",
                "reason": "feature_flag_off",
            }
            return plan

        # 提取持仓标的列表 (从 pnl_report 或 plan)
        symbols_to_check = self._extract_holding_symbols(pnl_report, plan)
        if not symbols_to_check:
            self._log("[负面新闻] 未找到持仓标的, 跳过")
            plan.setdefault("risk_guard", {})["sentiment_breaking_news"] = {
                "status": "SKIP",
                "reason": "no_holdings",
            }
            return plan

        # 限制检查数量, 避免 iFinD API 配额耗尽 (单次最多 10 只)
        symbols_to_check = symbols_to_check[:10]
        self._log(f"[负面新闻] 检查 {len(symbols_to_check)} 只持仓: {symbols_to_check}")

        try:
            from utils.ifind_news_analyzer import IFinDNewsAnalyzer
        except ImportError:
            self._log("[负面新闻] IFinDNewsAnalyzer 导入失败, 跳过")
            plan.setdefault("risk_guard", {})["sentiment_breaking_news"] = {
                "status": "SKIP",
                "reason": "import_failed",
            }
            return plan

        try:
            analyzer = IFinDNewsAnalyzer()
            if not analyzer.available():
                self._log("[负面新闻] iFinD MCP 不可用, 跳过 (P2 数据源降级)")
                plan.setdefault("risk_guard", {})["sentiment_breaking_news"] = {
                    "status": "SKIP",
                    "reason": "ifind_mcp_unavailable",
                }
                return plan

            # 批量研判 (size=3, days=1: 仅最近1天的最近3条新闻, 控制配额)
            insights = analyzer.batch_analyze(symbols_to_check, size=3, days=1)
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            self._log(f"[负面新闻] 研判异常, fail-open: {e}")
            plan.setdefault("risk_guard", {})["sentiment_breaking_news"] = {
                "status": "ERROR",
                "error": str(e),
            }
            # fail-open: 不阻塞交易, 仅记录
            return plan

        # 扫描重大负面新闻 (confidence >= 0.9)
        critical_negatives = []
        for insight in insights:
            if str(insight.direction).lower() == "negative" and float(insight.confidence) >= 0.9:
                critical_negatives.append(
                    {
                        "symbol": insight.symbol,
                        "name": insight.name,
                        "direction": insight.direction,
                        "confidence": round(float(insight.confidence), 3),
                        "reasons": insight.reasons[:3],  # 保留前3条原因
                        "news_count": insight.news_count,
                    }
                )

        if not critical_negatives:
            self._log(f"[负面新闻] 正常: 检查 {len(insights)} 只标的, 无 confidence>=0.9 的负面新闻")
            plan.setdefault("risk_guard", {})["sentiment_breaking_news"] = {
                "status": "OK",
                "checked_count": len(insights),
                "triggered": False,
            }
            return plan

        # 触发重大负面新闻熔断
        triggered_symbols = [n["symbol"] for n in critical_negatives]
        self._log(
            f"[负面新闻] [WARNING] 检测到 {len(critical_negatives)} 只标的重大负面新闻: "
            f"{triggered_symbols}, 立即暂停建仓"
        )

        # 写入风险守卫记录
        plan.setdefault("risk_guard", {})["sentiment_breaking_news"] = {
            "status": "TRIGGERED",
            "triggered": True,
            "triggered_symbols": triggered_symbols,
            "details": critical_negatives,
            "threshold": "confidence>=0.9 AND direction=negative",
            "action": "pause_build",
        }

        # 修改 market_state: 禁止新建仓 (但不强制平仓, 让 KillSwitch/Drawdown 决定)
        ms = plan.setdefault("market_state", {})
        ms["build_allowed"] = False
        ms["spot_build_allowed"] = False
        # 仅升级到 WARNING (不升级到 CRITICAL, 避免与 KillSwitch 冲突)
        # 如果已有 CRITICAL, 保持 CRITICAL (不降级)
        current_circuit = str(ms.get("circuit_level", "NORMAL")).upper()
        if current_circuit == "NORMAL":
            ms["circuit_level"] = "WARNING"
            ms["circuit_reason"] = "重大负面新闻触发 (confidence>=0.9)"

        # 清空现货建仓订单 (保留平仓订单)
        exec_plan = plan.get("execution_plan", {}) or {}
        for session_key in ["morning_orders", "afternoon_orders"]:
            orders = exec_plan.get(session_key, []) or []
            # 仅保留平仓方向 (SELL), 清掉建仓方向 (BUY)
            kept_orders = [
                o for o in orders if str(o.get("action", o.get("direction", ""))).upper() in ("SELL", "REDUCE")
            ]
            removed_count = len(orders) - len(kept_orders)
            if removed_count > 0:
                exec_plan[session_key] = kept_orders
                self._log(
                    f"[负面新闻] 已拦截 {session_key} 中 {removed_count} 笔建仓订单 "
                    f"(保留 {len(kept_orders)} 笔平仓订单)"
                )

        # 同步 hedge_fund_overlays.v77_notes
        hfo = plan.get("hedge_fund_overlays", {})
        notes = hfo.setdefault("v77_notes", {})
        notes["build_allowed"] = False
        notes["spot_build_allowed"] = False
        existing_reason = notes.get("reason_if_blocked", "")
        new_reason = f"重大负面新闻: {triggered_symbols}"
        notes["reason_if_blocked"] = f"{existing_reason} | {new_reason}" if existing_reason else new_reason

        return plan

    def _extract_holding_symbols(self, pnl_report: dict[str, Any], plan: dict[str, Any]) -> list[Any]:
        """从 pnl_report 或 plan 中提取持仓标的代码列表

        优先级:
            1. pnl_report.positions[].code (完整格式)
            2. plan.execution_plan.morning_orders[].code + afternoon_orders
            3. plan.positions (简化格式)

        Returns:
            标的代码列表 (6位代码, 如 ["600519", "000858"])
        """
        import re

        symbols = []

        # 1. 从 pnl_report 提取
        positions = self._extract_positions(pnl_report)
        for pos in positions:
            code = pos.get("code") or pos.get("symbol") or ""
            if code:
                # 提取 6 位代码 (去掉后缀 .SH/.SZ)
                match = re.search(r"(\d{6})", str(code))
                if match:
                    symbols.append(match.group(1))

        # 2. 如果 pnl_report 没有数据, 从 plan 提取
        if not symbols:
            exec_plan = plan.get("execution_plan", {}) or {}
            for session_key in ["morning_orders", "afternoon_orders"]:
                orders = exec_plan.get(session_key, []) or []
                for o in orders:
                    code = o.get("code") or o.get("symbol") or ""
                    if code:
                        match = re.search(r"(\d{6})", str(code))
                        if match:
                            symbols.append(match.group(1))

        # 3. 从 plan.positions 提取 (简化格式)
        if not symbols:
            plan_positions = plan.get("positions", {})
            if isinstance(plan_positions, dict):
                for code in plan_positions.keys():
                    match = re.search(r"(\d{6})", str(code))
                    if match:
                        symbols.append(match.group(1))
            elif isinstance(plan_positions, list):
                for pos in plan_positions:
                    code = pos.get("code") or pos.get("symbol") or ""
                    if code:
                        match = re.search(r"(\d{6})", str(code))
                        if match:
                            symbols.append(match.group(1))

        # 去重, 保持顺序
        seen = set()
        unique = []
        for s in symbols:
            if s not in seen:
                seen.add(s)
                unique.append(s)
        return unique
