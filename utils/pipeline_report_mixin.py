"""PipelineReportMixin — 报告生成 + Kill Switch 检查 step (从 institutional_pipeline_runner.py 拆出)。

拆分日期: 2026-08-28
原位置: institutional_pipeline_runner.py
  - _step_report_generation (89行)
  - _report_ai_review_section (20行)
  - _report_dashboard_section (25行)
  - _step_kill_switch_check (138行)

Mixin 方法保持 self 接口, 由 InstitutionalPipelineRunner 继承。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("institutional_pipeline")

try:
    from utils.kill_switch import KillSwitch

    _HAS_KILL_SWITCH = True
except ImportError:
    _HAS_KILL_SWITCH = False


class PipelineReportMixin:
    """报告生成 + Kill Switch 检查 step 的 Mixin。"""

    def _step_kill_switch_check(self, decision: dict) -> dict[str, Any]:
        """P0-13: KillSwitch 熔断检查 (生产 pipeline 集成)

        审计问题: 生产 pipeline 未集成 KillSwitch, L1/L2/L3 熔断对 pipeline 无效。
        修复: 在风险预算检查后、执行路由前, 检查 KillSwitch 级别:
          - L1: 过滤 BUY trades (停止新开仓, 仅保留 SELL)
          - L2+: 阻止全部 trades (返回空 trades 列表)
          - 异常: fail-closed (按 L3 处理, 阻止全部)

        Returns:
            {
                "level": int,
                "can_trade": bool,
                "can_open": bool,
                "filtered_trades": list,
                "blocked_trades_count": int,
                "fail_closed": bool,
            }
        """
        result = {
            "level": 0,
            "can_trade": True,
            "can_open": True,
            "filtered_trades_count": (
                len(decision.trades) if hasattr(decision, "trades") else 0
            ),
            "blocked_trades_count": 0,
            "fail_closed": False,
        }

        if not _HAS_KILL_SWITCH:
            # BUG-06 修复 (2026-07-31): 模块加载失败时 fail-closed, 而非 fail-open
            # 原代码: level=-1, can_trade=True, can_open=True → 调用方 `level >= 2` 不触发,
            #         风控核心模块缺失却允许所有交易通过 (fail-open, 极端市场灾难性风险).
            # 修复: 视为 L3 (最高风险), fail_closed=True, 阻止全部交易.
            #       smoke/backtest 模式保留 trades (保持可测试性, 由调用方判定不阻塞).
            is_test_mode = self.ctx.mode in ("smoke", "backtest")
            if is_test_mode:
                logger.warning(
                    "[KillSwitch] 模块未加载 (测试模式, 保留 trades 不阻塞). "
                    "生产模式将 fail-closed. 请检查 utils/kill_switch.py 依赖."
                )
                result["level"] = 0
                result["note"] = "module_not_loaded_test_mode"
                return result
            logger.critical(
                "[KillSwitch] 模块未加载! fail-closed 视为 L3 (阻止全部交易). "
                "请检查 utils/kill_switch.py 依赖."
            )
            result["level"] = 3
            result["can_trade"] = False
            result["can_open"] = False
            result["fail_closed"] = True
            result["note"] = "module_not_loaded_fail_closed"
            if hasattr(decision, "trades"):
                result["blocked_trades_count"] = len(decision.trades)
                decision.trades = []
                result["filtered_trades_count"] = 0
            return result

        try:
            ks = KillSwitch()
            ks_status = ks.check_margin_status()
            ks_level = (
                int(ks_status.get("level", 0)) if isinstance(ks_status, dict) else 0
            )
            result["level"] = ks_level
            result["can_trade"] = bool(ks_status.get("can_trade", ks_level < 2))
            result["can_open"] = bool(ks_status.get("can_open", ks_level == 0))
            result["margin_usage_ratio"] = float(ks_status.get("margin_usage_ratio", 0))

            logger.info(
                "[KillSwitch] 生产 pipeline 熔断检查: L%d, can_trade=%s, can_open=%s, margin=%.1f%%",
                ks_level,
                result["can_trade"],
                result["can_open"],
                result["margin_usage_ratio"] * 100,
            )

            if ks_level == 0 or not hasattr(decision, "trades"):
                return result

            # L1: 过滤 BUY trades (停止新开仓)
            if ks_level == 1:
                original_count = len(decision.trades)
                decision.trades = [
                    t
                    for t in decision.trades
                    if str(t.get("side", "BUY")).upper() != "BUY"
                    or t.get("change", 0) < 0
                ]
                result["blocked_trades_count"] = original_count - len(decision.trades)
                result["filtered_trades_count"] = len(decision.trades)
                logger.warning(
                    "[KillSwitch L1] 停止新开仓! 过滤 %d 笔 BUY trades (保留 %d 笔 SELL)",
                    result["blocked_trades_count"],
                    len(decision.trades),
                )

            # L2+: 阻止全部 trades
            elif ks_level >= 2:
                result["blocked_trades_count"] = len(decision.trades)
                decision.trades = []
                result["filtered_trades_count"] = 0
                logger.error(
                    "[KillSwitch L%d] 阻止全部 %d 笔 trades!",
                    ks_level,
                    result["blocked_trades_count"],
                )
                # 触发熔断执行 (L2 强平 / L3 变现)
                try:
                    if ks_level >= 3:
                        logger.critical("[KillSwitch L3] 触发紧急变现协议!")
                        ks.execute_kill_switch(3)
                    else:
                        logger.error("[KillSwitch L2] 触发强平协议!")
                        ks.execute_kill_switch(2)
                except RuntimeError as e:
                    logger.error(f"[KillSwitch] 熔断执行失败 (无 broker_callback): {e}")
                    result["execute_error"] = str(e)

        except Exception as e:
            # P0-11: fail-closed — 异常时阻止全部交易
            logger.critical(
                "[KillSwitch] 检查异常! fail-closed 阻止全部 trades: %s",
                e,
                exc_info=True,
            )
            result["fail_closed"] = True
            result["level"] = 3
            result["can_trade"] = False
            result["can_open"] = False
            if hasattr(decision, "trades"):
                result["blocked_trades_count"] = len(decision.trades)
                decision.trades = []
                result["filtered_trades_count"] = 0

        return result

    def _step_report_generation(self, result: dict[str, Any]) -> Path:
        """盘后报告生成 (phase_report)。

        生成 Markdown 格式的 pipeline 运行报告, 含各步骤状态/权重/风险/执行计划。
        v86 集成: AI_DECISION_INTEGRATED=1 时追加 AI 复盘 + dashboard 章节。
        """
        date = self.ctx.report_date
        mode = self.ctx.mode
        report_path = self.ctx.output_path / f"pipeline_report_{mode}_{date}.md"

        lines: list[str] = []
        lines.append(f"# 机构级量化闭环报告 — {date}")
        lines.append("")
        lines.append(
            f"> 模式: `{mode}` | 标的: {', '.join(self.ctx.symbols)} "
            f"| 资金: {self.ctx.total_capital:,.0f}"
        )
        lines.append(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")

        steps = result.get("steps", {})

        lines.append("## 步骤状态摘要")
        lines.append("")
        lines.append("| 步骤 | 状态 |")
        lines.append("|------|------|")
        step_names = [
            ("data_gate", "1. 数据门控"),
            ("alpha_evaluation", "2. Alpha 评估"),
            ("signal_fusion", "3. 信号融合"),
            ("portfolio_decision", "4. 组合优化"),
            ("market_regime", "4.5 市场状态"),
            ("v72_bull_regime_cap", "V7.2 Bull Cap"),
            ("drawdown_breaker", "回撤熔断"),
            ("risk_budget", "5. 风险预算"),
            ("kill_switch", "KillSwitch"),
            ("trades_sync", "trades 同步"),
            ("execution_plans", "6. 执行路由"),
            ("ai_eod_review", "6.5 AI 复盘"),
        ]
        for key, name in step_names:
            if key in steps:
                step_data = steps[key]
                if isinstance(step_data, dict):
                    status = step_data.get("status", "OK")
                elif isinstance(step_data, list):
                    status = f"{len(step_data)} 项"
                else:
                    status = "OK"
                lines.append(f"| {name} | {status} |")
        lines.append("")

        portfolio = steps.get("portfolio_decision", {})
        if isinstance(portfolio, dict) and portfolio.get("target_weights"):
            lines.append("## 目标权重")
            lines.append("")
            lines.append("| 标的 | 权重 |")
            lines.append("|------|------|")
            for sym, w in portfolio["target_weights"].items():
                lines.append(f"| {sym} | {w:.2%} |")
            lines.append("")

        risk = steps.get("risk_budget", {})
        if isinstance(risk, dict):
            lines.append("## 风险预算")
            lines.append("")
            lines.append(f"- 允许: {risk.get('allowed', 'N/A')}")
            if risk.get("portfolio_var95") is not None:
                lines.append(f"- 组合 VaR95: {risk['portfolio_var95']:.4f}")
            if risk.get("max_weight_used") is not None:
                lines.append(f"- 最大权重: {risk['max_weight_used']:.2%}")
            lines.append("")

        exec_plans = steps.get("execution_plans", [])
        if exec_plans:
            lines.append("## 执行计划")
            lines.append("")
            lines.append(f"共 {len(exec_plans)} 笔执行计划")
            lines.append("")

        self._report_ai_review_section(lines, steps.get("ai_eod_review", {}), date)
        self._report_dashboard_section(result, lines, date)

        lines.append("---")
        lines.append("*由 institutional_pipeline_runner.py 自动生成 | v8.6 EOD 闭环*")

        report_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("[Pipeline] 盘后报告已生成: %s", report_path)
        return report_path

    def _report_ai_review_section(
        self, lines: list[str], review: Any, date: str
    ) -> None:
        """报告 AI 复盘章节 (降级/正常两分支)."""
        if isinstance(review, dict) and review.get("error"):
            lines.append("## AI EOD 复盘 (降级)")
            lines.append("")
            lines.append(f"降级原因: {review.get('error', 'N/A')}")
            lines.append("")
        elif isinstance(review, dict) and review:
            lines.append("## AI EOD 复盘")
            lines.append("")
            lines.append(f"- 日期: {review.get('date', date)}")
            alerts = review.get("alerts")
            if alerts is not None:
                if isinstance(alerts, list):
                    lines.append(f"- 告警数: {len(alerts)}")
                elif isinstance(alerts, dict):
                    lines.append(f"- 告警: {alerts}")
            lines.append("")

    def _report_dashboard_section(
        self, result: dict[str, Any], lines: list[str], date: str
    ) -> None:
        """报告 dashboard 章节 (v86 集成, feature flag 控制, 失败降级)."""
        if os.environ.get("AI_DECISION_INTEGRATED") != "1" or self.ctx.mode == "smoke":
            return
        try:
            from ai_decision.dashboard import DashboardGenerator

            dash_gen = DashboardGenerator()
            dash_report = dash_gen.generate_daily_dashboard(date)
            lines.append("## AI 决策看板")
            lines.append("")
            lines.append(f"- 看板已生成 (date={date})")
            dash_alerts = dash_report.get("alerts")
            if dash_alerts is not None:
                lines.append(f"- 看板告警: {dash_alerts}")
            lines.append("")
            result["steps"]["ai_dashboard"] = dash_report
        except Exception as e:  # noqa: BLE001  # fail-safe
            logger.warning("[Pipeline] AI 看板生成失败，降级: %s", e)
            lines.append("## AI 决策看板 (降级)")
            lines.append("")
            lines.append(f"降级原因: {e}")
            lines.append("")
