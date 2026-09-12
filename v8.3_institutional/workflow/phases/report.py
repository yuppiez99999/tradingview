"""Phase 7: 盘后报告生成 (从 daily_workflow.py 拆出, 零行为变更)。

原位置: daily_workflow.py L2596-L3254 (phase_report, 659 行)
拆分日期: 2026-08-28

self -> ctx 替换说明:
- WorkflowContext.__getattr__ 代理所有未显式定义的属性到 wf 实例
- ctx._get_portfolio_positions_for_stress_test 通过 __getattr__ 返回 wf 的 bound method
- getattr(ctx, ...) / hasattr(ctx, ...) 与 getattr(wf, ...) / hasattr(wf, ...) 语义一致
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from utils.datetime_utils import now_bj
from workflow.context import get_dw_module

logger = logging.getLogger("v75.daily_workflow")

_dw = get_dw_module()


def phase_report(ctx) -> Path:
    """盘后报告生成"""
    logger.info("=" * 60)
    logger.info("Phase 7: 盘后报告生成")
    logger.info("=" * 60)

    # 报告路径 (统一使用 YYYY-MM-DD 格式，与 run_all_modules.py 一致)
    report_dir = ctx.config.REPORT_DIR / ctx.trade_date
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = (
        report_dir / f"v75_daily_workflow_{ctx.trade_date.replace('-', '')}.md"
    )

    # === 报告头部 ===
    lines = [
        f"# v7.5 每日交易工作流报告 — {ctx.trade_date}",
        "",
        f"**生成时间**: {now_bj():%Y-%m-%d %H:%M:%S}",
        f"**资金规模**: {ctx.capital:,.0f}",
        f"**执行模式**: {'DRY-RUN' if ctx.dry_run else ('模拟盘' if getattr(ctx, '_sim_mode_requested', getattr(ctx, 'sim_mode', False)) else 'MOCK_EXECUTION')}",  # noqa: E501
        "**策略**: 康波第六轮周期 × 十五五规划 × v7.0期货期权双层对冲",
        "",
        "## 阶段执行摘要",
        "",
        "| 阶段 | 状态 |",
        "|------|------|",
    ]
    for phase_name, phase_data in ctx.state["phases"].items():
        status = (
            phase_data.get("status", "N/A") if isinstance(phase_data, dict) else "N/A"
        )
        lines.append(f"| {phase_name} | {status} |")

    # === 十五五年度阶段摘要 ===
    if ctx.current_phase_info is not None:
        pi = ctx.current_phase_info
        lines.extend(
            [
                "",
                "## 十五五年度阶段",
                "",
                f"- **年度**: {pi.year}",
                f"- **阶段**: {pi.phase_name}",
                f"- **周期**: {pi.period}",
                f"- **当前季度**: {pi.current_quarter}",
                f"- **目标年化收益**: {pi.target_return:.1%}",
                f"- **最大回撤限制**: {pi.max_drawdown:.1%}",
                f"- **杠杆目标**: {pi.leverage_target:.2f}x",
                f"- **风险关注**: {pi.risk_focus}",
            ]
        )
        if pi.is_liquidation_year and pi.liquidation_actions:
            lines.extend(
                [
                    "",
                    f"### ⚠️ 2030 清仓 {pi.current_quarter}",
                    f"- **动作**: {pi.liquidation_actions.get('name', '')}",
                ]
            )
            for action in pi.liquidation_actions.get("actions", []):
                lines.append(f"  - {action}")
        # 季度评估结果 (如果在 v10_risk 阶段执行了)
        v10_result = ctx.state.get("phases", {}).get("v10_risk", {})
        quarterly = (
            v10_result.get("quarterly_review", {})
            if isinstance(v10_result, dict)
            else {}
        )
        if quarterly.get("executed"):
            lines.extend(
                [
                    "",
                    "### 季度评估结果",
                    f"- **季度**: {quarterly.get('quarter', '')}",
                    f"- **压测触发**: {'是' if quarterly.get('stress_test_triggered') else '否'}",
                    f"- **调仓需要**: {'是' if quarterly.get('rebalance_needed') else '否'}",
                    f"- **动作数**: {len(quarterly.get('actions', []))}",
                ]
            )
            for action in quarterly.get("actions", []):
                lines.append(f"  - {action}")

    # === Phase 1: 自检 ===
    if "check" in ctx.state["phases"]:
        lines.extend(["", "## 1. 系统自检", ""])
        checks = ctx.state["phases"]["check"].get("checks", {})
        lines.append("| 检查项 | 状态 |")
        lines.append("|--------|------|")
        for k, v in checks.items():
            lines.append(f"| {k} | {'✓' if v else '✗'} |")

    # === Phase 1.5: 收益预测动态校准 (新增) ===
    if "calibrate" in ctx.state["phases"]:
        lines.extend(["", "## 1.5 收益预测动态校准", ""])
        cal = ctx.state["phases"]["calibrate"]
        lines.append(f"- 状态: {cal.get('status', 'N/A')}")
        if cal.get("status") in ("PASS", "DEGRADED"):
            wf = cal.get("wind_fetch", {})
            rz = cal.get("realized", {})
            cb = cal.get("calibration", {})
            if cal.get("status") == "DEGRADED":
                lines.append(f"- ⚠️ 降级原因: {wf.get('degraded_reason', 'N/A')}")
                lines.append(
                    f"- Wind 拉取: {wf.get('success', 0)} 成功 / "
                    f"{wf.get('fail', 0)} 失败 (配额耗尽或网络异常)"
                )
            else:
                lines.append(
                    f"- Wind 拉取: {wf.get('success', 0)} 成功 / "
                    f"{wf.get('fail', 0)} 失败 "
                    f"({wf.get('total_days', 0)} 日 × "
                    f"{wf.get('total_symbols', 0)} 标的)"
                )
            lines.append(
                f"- 真实历史: {rz.get('start_date','')} → "
                f"{rz.get('end_date','')} "
                f"({rz.get('years', 0):.3f} 年)"
            )
            lines.append(
                f"- 持仓加权年化: "
                f"{rz.get('portfolio_weighted_annualized', 0)*100:+.2f}% "
                f"(覆盖权重 {rz.get('portfolio_weight_total', 0)*100:.2f}%)"
            )
            lines.append(
                f"- 基准年化: {rz.get('market_annualized', 0)*100:+.2f}%, "
                f"夏普 {rz.get('market_sharpe', 0):.2f}"
            )
            lines.append(f"- 校准原因: {cb.get('calibration_reason', 'N/A')}")
            lines.append(f"- 原概率权重: {cb.get('original_weights', {})}")
            lines.append(f"- 新概率权重: {cb.get('calibrated_weights', {})}")
            lines.append(
                f"- **新期望年化: "
                f"{cb.get('calibrated_expected_annualized', 0):.2f}%**"
            )
            lines.append(
                f"- 新期望期末金额: " f"¥{cb.get('calibrated_expected_final', 0):,.0f}"
            )
        elif cal.get("status") == "SKIP":
            lines.append(f"- 原因: {cal.get('reason', 'N/A')}")
        else:
            lines.append(f"- 错误: {cal.get('error', 'N/A')[:200]}")

    # === Phase 2: 市场状态 ===
    if "market" in ctx.state["phases"]:
        lines.extend(["", "## 2. 市场状态", ""])
        m = ctx.state["phases"]["market"]
        lines.append(f"- VIX: {m.get('vix', 'N/A')}")
        lines.append(f"- 熔断级别: {m.get('circuit_level', 'N/A')}")
        lines.append(f"- 允许建仓: {m.get('build_allowed', False)}")

    # === Phase 3: 风险预算 (组合级别) ===
    if "risk" in ctx.state["phases"]:
        lines.extend(["", "## 3. 风险预算 (组合级别 — 500万 4阶段)", ""])
        r = ctx.state["phases"]["risk"]
        lines.append(f"- 组合净值: {r.get('equity', 0):,.0f}")
        lines.append(f"- 风险模式: {r.get('mode', 'N/A')}")
        lines.append(f"- 仓位系数: {r.get('position_factor', 0):.2f}")
        lines.append(f"- 股票组合资金: {r.get('stock_capital', 0):,.0f} (60%)")
        lines.append(f"- 期权对冲资金: {r.get('hedge_capital', 0):,.0f} (40%)")
        lines.append(
            f"- 当日建仓资金: {r.get('day_capital', 0):,.0f} "
            f"(占股票组合 {r.get('build_ratio', 0):.2%})"
        )
        lines.append(f"  - 上午批次: {r.get('morning_total', 0):,.0f}")
        lines.append(f"  - 下午批次: {r.get('afternoon_total', 0):,.0f}")
        lines.append(f"  - 合计: {r.get('grand_total', 0):,.0f}")
        lines.append(
            f"- 四级风控: 黄色 {r.get('yellow_warning', 0):.2%} / "
            f"橙色 {r.get('orange_warning', 0):.2%} / "
            f"红色 {r.get('red_warning', 0):.2%} / "
            f"全部止损 {r.get('full_stop', 0):.2%}"
        )
        lines.append(
            f"- VaR 预算: 95% < {r.get('var_95_limit', 0):.0%}, "
            f"99% < {r.get('var_99_limit', 0):.0%}"
        )

    # === Phase 4: 对冲评估 ===
    if "hedge" in ctx.state["phases"]:
        lines.extend(["", "## 4. 对冲评估", ""])
        h = ctx.state["phases"]["hedge"]
        lines.append(f"- 总对冲比例: {h.get('total_hedge_pct', 0):.2%}")
        actions = h.get("actions", {})
        if isinstance(actions, dict):
            for k, v in actions.items():
                lines.append(f"  - {k}: {v}")
        elif isinstance(actions, list):
            for action in actions:
                lines.append(
                    f"  - {action.get('type', '')}: {action.get('action', 'N/A')}"
                )

    # === Phase 5: 交易信号 (计划标的) ===
    if "signal" in ctx.state["phases"]:
        lines.extend(["", "## 5. 交易信号 (计划标的)", ""])
        s = ctx.state["phases"]["signal"]
        action = s.get("action", "")
        if action == "BUILD_PLAN":
            lines.append(
                f"- 阶段: {s.get('phase_name', '')} " f"(第 {s.get('day_index', 0)} 日)"
            )
            lines.append(
                f"- 上午批次: {s.get('morning_count', 0)} 笔, "
                f"金额 {s.get('morning_amount', 0):,.0f} "
                f"({s.get('morning_window', '')})"
            )
            lines.append(
                f"- 下午批次: {s.get('afternoon_count', 0)} 笔, "
                f"金额 {s.get('afternoon_amount', 0):,.0f} "
                f"({s.get('afternoon_window', '')})"
            )
            lines.append(
                f"- 单日合计: {s.get('total_orders', 0)} 笔, "
                f"金额 {s.get('grand_amount', 0):,.0f}"
            )
            if s.get("position_factor", 1.0) < 1.0:
                lines.append(
                    f"- **DEFENSE 模式**: 仓位系数 {s.get('position_factor', 0):.2f}"
                )
        else:
            lines.append(f"- 动作: {action}")

        # Qlib 信号摘要
        if s.get("qlib_adjusted"):
            lines.append(
                f"- **Qlib 信号已调整**: 加仓={s.get('qlib_boost_count', 0)}, "
                f"减仓={s.get('qlib_cut_count', 0)}, 跳过={s.get('qlib_skip_count', 0)}"
            )
            qlib_signals = s.get("qlib_signals", {})
            if qlib_signals:
                lines.append("  - 信号详情:")
                for code, value in list(qlib_signals.items())[:10]:
                    lines.append(f"    - {code}: {value:+.4f}")

        # iFinD 新闻摘要
        if s.get("ifind_adjusted"):
            lines.append(
                f"- **iFinD 新闻已调整**: 加仓={s.get('ifind_boost_count', 0)}, "
                f"减仓={s.get('ifind_cut_count', 0)}, 跳过={s.get('ifind_skip_count', 0)}"
            )
            # 输出研判原因详情，方便人工复核
            morning_orders = s.get("morning_orders", [])
            afternoon_orders = s.get("afternoon_orders", [])
            ifind_details = []
            for order in morning_orders + afternoon_orders:
                reasons = order.get("ifind_reasons")
                if reasons:
                    ifind_details.append(
                        f"- {order.get('code', '')} {order.get('name', '')}: "
                        f"{order.get('ifind_direction', '')} "
                        f"confidence={order.get('ifind_confidence', 0):.2f} "
                        f"factor={order.get('ifind_factor', 1.0):.2f} "
                        f"-> {', '.join(reasons)}"
                    )
            if ifind_details:
                lines.append("  - **研判原因详情**:")
                lines.extend(f"    {detail}" for detail in ifind_details[:20])

        # AnySearch 实时新闻回顾
        if "market" in ctx.state["phases"]:
            anysearch_news = ctx.state["phases"]["market"].get("anysearch_news", [])
            if anysearch_news:
                lines.append("")
                lines.append("  - **AnySearch 实时新闻**:")
                for item in anysearch_news:
                    title = item.get("title", "")[:50]
                    url = item.get("url", "")[:80]
                    lines.append(f"    - {title} -> {url}")

    # === 执行记录 (计划标的) ===
    if "execute" in ctx.state["phases"]:
        lines.extend(["", "## 6. 执行记录 (计划标的)", ""])
        e = ctx.state["phases"]["execute"]
        mode = (
            "模拟盘"
            if getattr(ctx, "_sim_mode_requested", e.get("sim_mode", False))
            else ("DRY-RUN" if ctx.dry_run else "MockBroker")
        )
        lines.append(f"- **执行模式**: {mode}")
        order_summary = e.get("order_summary", [])
        fills = e.get("fills", [])
        if order_summary:
            total_filled_amount = sum(
                item.get("filled_amount", 0) for item in order_summary
            )
            lines.append(
                f"**成交汇总**: {len(order_summary)} 笔订单, "
                f"总金额 {total_filled_amount:,.0f}"
            )
            lines.append("")
            lines.append("### 上午批次")
            lines.append("")
            lines.append(
                "| # | 代码 | 名称 | 风格 | 风险 | 方向 | 股数 | 预估价 | 成交价 | 金额 | 滑点 | 状态 |"
            )
            lines.append(
                "|---|------|------|------|------|------|------|------|------|------|------|------|"
            )
            morning_orders = [
                item for item in order_summary if item.get("session") == "morning"
            ]
            for i, item in enumerate(morning_orders, 1):
                lines.append(
                    f"| {i} | {item.get('symbol', '')} | {item.get('name', '')} | "
                    f"{item.get('style', '')} | {item.get('risk', '')} | "
                    f"{item.get('side', '')} | {item.get('qty', 0)} | "
                    f"{item.get('est_price', 0):.4f} | {item.get('avg_price', 0):.4f} | "
                    f"{item.get('filled_amount', 0):,.0f} | "
                    f"{item.get('max_slippage_pct', 0):.4%} | "
                    f"{item.get('status', '')} |"
                )
            morning_total = sum(item.get("filled_amount", 0) for item in morning_orders)
            lines.append(f"| | | | | | | | | | **合计** | | **{morning_total:,.0f}** |")

            lines.append("")
            lines.append("### 下午批次")
            lines.append("")
            lines.append(
                "| # | 代码 | 名称 | 风格 | 风险 | 方向 | 股数 | 预估价 | 成交价 | 金额 | 滑点 | 状态 |"
            )
            lines.append(
                "|---|------|------|------|------|------|------|------|------|------|------|------|"
            )
            afternoon_orders = [
                item for item in order_summary if item.get("session") == "afternoon"
            ]
            for i, item in enumerate(afternoon_orders, 1):
                lines.append(
                    f"| {i} | {item.get('symbol', '')} | {item.get('name', '')} | "
                    f"{item.get('style', '')} | {item.get('risk', '')} | "
                    f"{item.get('side', '')} | {item.get('qty', 0)} | "
                    f"{item.get('est_price', 0):.4f} | {item.get('avg_price', 0):.4f} | "
                    f"{item.get('filled_amount', 0):,.0f} | "
                    f"{item.get('max_slippage_pct', 0):.4%} | "
                    f"{item.get('status', '')} |"
                )
            afternoon_total = sum(
                item.get("filled_amount", 0) for item in afternoon_orders
            )
            lines.append(
                f"| | | | | | | | | | **合计** | | **{afternoon_total:,.0f}** |"
            )

            lines.append("")
            lines.append(
                f"**单日总计**: 上午 {morning_total:,.0f} + "
                f"下午 {afternoon_total:,.0f} = "
                f"**{morning_total + afternoon_total:,.0f}**"
            )
        else:
            lines.append("无成交")

        # 子成交明细（审计用）
        if fills:
            lines.extend(["", "### 子成交明细", ""])
            lines.append(
                "| 代码 | 名称 | 批次 | 方向 | 股数 | 价格 | 金额 | 滑点 | 状态 |"
            )
            lines.append(
                "|------|------|------|------|------|------|------|------|------|"
            )
            for f in fills:
                lines.append(
                    f"| {f.get('symbol', '')} | {f.get('name', '')} | "
                    f"{f.get('session', '')} | {f.get('side', '')} | "
                    f"{f.get('qty', 0)} | {f.get('price', 0):.4f} | "
                    f"{f.get('amount', 0):,.0f} | "
                    f"{f.get('slippage_pct', 0):.4%} | "
                    f"{f.get('status', '')} |"
                )

    # === 总结 ===
    execute_phase = ctx.state.get("phases", {}).get("execute", {})
    order_summary = execute_phase.get("order_summary", [])
    total_orders = len(order_summary)
    total_amount = sum(item.get("filled_amount", 0) for item in order_summary)
    # === 对冲基金视角: P&L 八维归因分析 ===
    if ctx.pnl_attribution_engine is not None:
        try:
            positions = (
                ctx._get_portfolio_positions_for_stress_test()
                if hasattr(ctx, "_get_portfolio_positions_for_stress_test")
                else []
            )
            # 当日成交
            fills = ctx.state.get("phases", {}).get("execute", {}).get("fills", [])
            trading_costs = sum(
                float(f.get("amount", 0)) * 0.001 for f in fills  # 估算 10bps 综合成本
            )
            # 对冲盈亏
            hedge_pnl = float(
                ctx.state.get("phases", {}).get("hedge", {}).get("hedge_pnl", 0.0)
            )
            # 组合与基准收益 (基于持仓盈亏的简化估算) — 转换为收益序列
            portfolio_value = float(getattr(ctx, "capital", 5_000_000))
            # 当日盈亏 = Σ(持仓市值 × 当日涨幅) 简化: 使用 phase_market 中的 beta/涨幅代理
            market_phase = ctx.state.get("phases", {}).get("market", {})
            portfolio_ret_today = (
                float(market_phase.get("portfolio_return", 0.0)) or 0.0
            )
            benchmark_ret_today = (
                float(market_phase.get("benchmark_return", 0.0)) or 0.0
            )
            if abs(portfolio_ret_today) < 1e-6 and positions:
                # 回退: 使用持仓总市值 vs 资金比例估算
                total_mv = sum(float(p.get("amount", 0)) for p in positions)
                portfolio_ret_today = (
                    (total_mv - portfolio_value * 0.5) / (portfolio_value * 0.5) * 0.005
                )  # 0.5% 假设日收益
            # 包装为长度=1 的收益序列 (单日)
            portfolio_returns = [portfolio_ret_today]
            benchmark_returns = [benchmark_ret_today]
            market_returns = benchmark_returns  # 沪深300代理
            # 简化: 因子与行业收益沿用组合收益（实盘接入后由 Barra 模型填充）
            factor_returns = {
                "momentum": [portfolio_ret_today * 0.3],
                "reversal": [-portfolio_ret_today * 0.1],
                "volatility": [portfolio_ret_today * 0.1],
                "liquidity": [portfolio_ret_today * 0.05],
                "earnings_quality": [portfolio_ret_today * 0.2],
                "growth": [portfolio_ret_today * 0.15],
                "valuation": [portfolio_ret_today * 0.1],
            }
            sector_returns = {
                "高端制造": [portfolio_ret_today * 0.4],
                "顺周期": [portfolio_ret_today * 0.2],
                "资源": [portfolio_ret_today * 0.2],
                "防御": [portfolio_ret_today * 0.2],
            }
            attribution = ctx.pnl_attribution_engine.attribute(
                positions=positions,
                portfolio_returns=portfolio_returns,
                benchmark_returns=benchmark_returns,
                market_returns=market_returns,
                factor_returns=factor_returns,
                sector_returns=sector_returns,
                trading_costs=trading_costs,
                funding_cost=0.0,
                hedge_pnl=hedge_pnl,
            )
            ctx.state["phases"]["report_pnl_attribution"] = {
                "total_pnl": attribution.total_pnl,
                "total_return_pct": attribution.total_return_pct,
                "alpha_pnl": attribution.alpha_pnl,
                "beta_pnl": attribution.beta_pnl,
                "style_pnl": attribution.style_pnl,
                "sector_pnl": attribution.sector_pnl,
                "timing_pnl": attribution.timing_pnl,
                "hedge_pnl": attribution.hedge_pnl,
                "trading_cost": attribution.trading_cost,
                "funding_cost": attribution.funding_cost,
                "sharpe_ratio": attribution.sharpe_ratio,
                "information_ratio": attribution.information_ratio,
                "tracking_error": attribution.tracking_error,
                "anomalies": attribution.anomalies,
            }
            lines.extend(
                [
                    "",
                    "## P&L 归因分析 (对冲基金视角)",
                    "",
                    f"- **总 P&L**: ¥{attribution.total_pnl:,.0f} ({attribution.total_return_pct:.2%})",
                    f"- **Alpha 贡献**: ¥{attribution.alpha_pnl:,.0f}",
                    f"- **Beta 贡献**: ¥{attribution.beta_pnl:,.0f}",
                    f"- **风格因子**: ¥{attribution.style_pnl:,.0f}",
                    f"- **行业配置**: ¥{attribution.sector_pnl:,.0f}",
                    f"- **择时**: ¥{attribution.timing_pnl:,.0f}",
                    f"- **对冲**: ¥{attribution.hedge_pnl:,.0f}",
                    f"- **交易成本**: ¥{attribution.trading_cost:,.0f}",
                    f"- **资金成本**: ¥{attribution.funding_cost:,.0f}",
                    "",
                    "### 风险调整收益指标",
                    "",
                    f"- **Sharpe Ratio (年化)**: {attribution.sharpe_ratio:.3f}",
                    f"- **Information Ratio**: {attribution.information_ratio:.3f}",
                    f"- **Tracking Error (年化)**: {attribution.tracking_error:.2%}",
                    "",
                ]
            )
            if attribution.anomalies:
                lines.extend(
                    [
                        "### 异常检测告警",
                        "",
                    ]
                )
                for a in attribution.anomalies:
                    lines.append(f"- ⚠️ {a}")
                lines.append("")
            # 风格因子贡献明细
            if attribution.style_factors:
                lines.extend(
                    [
                        "### 风格因子贡献明细",
                        "",
                        "| 因子 | 暴露 | 因子收益 | 贡献 |",
                        "|------|------|----------|------|",
                    ]
                )
                for fc in attribution.style_factors:
                    lines.append(
                        f"| {fc.factor_name} | {fc.exposure:.4f} | {fc.factor_return:.4f} | ¥{fc.contribution:,.0f} |"
                    )
                lines.append("")
        except Exception as exc:  # fail-safe
            logger.error("[PnLAttribution] 归因失败: %s", exc, exc_info=True)
            lines.extend(["", f"**P&L 归因失败**: {exc}", ""])

    # === 机构级: Barra 风险因子暴露分解 ===
    if ctx.barra_decomposer is not None:
        try:
            positions = (
                ctx._get_portfolio_positions_for_stress_test()
                if hasattr(ctx, "_get_portfolio_positions_for_stress_test")
                else []
            )
            logger.info("[Barra] 持仓数量: %d", len(positions))
            if positions:
                barra_result = ctx.barra_decomposer.decompose_from_positions(
                    positions=positions,
                    risk_budget=0.05,
                )
                logger.info(
                    "[Barra] 分解完成: TE=%.2f%%, IR=%.3f, 风险预算利用=%.1f%%",
                    barra_result.active_risk * 100,
                    barra_result.information_ratio,
                    barra_result.risk_budget_utilization * 100,
                )
                ctx.state["phases"]["report_barra"] = {
                    "active_risk": barra_result.active_risk,
                    "factor_risk": barra_result.factor_risk,
                    "specific_risk": barra_result.specific_risk,
                    "factor_risk_pct": barra_result.factor_risk_pct,
                    "active_return": barra_result.active_return,
                    "information_ratio": barra_result.information_ratio,
                    "risk_budget_used": barra_result.risk_budget_used,
                    "risk_budget_remaining": barra_result.risk_budget_remaining,
                    "risk_budget_utilization": barra_result.risk_budget_utilization,
                    "concentrated_factors": barra_result.concentrated_factors,
                    "missing_factors": barra_result.missing_factors,
                    "industry_exposures": barra_result.industry_exposures,
                }
                lines.extend(
                    [
                        "",
                        "## Barra 风险因子暴露分解 (AQR 风格)",
                        "",
                        f"- **主动风险 (跟踪误差)**: {barra_result.active_risk:.2%}",
                        f"  - 因子风险: {barra_result.factor_risk:.2%} ({barra_result.factor_risk_pct:.1%})",
                        f"  - 个股特异性风险: {barra_result.specific_risk:.2%}",
                        f"- **主动收益**: {barra_result.active_return:.2%}",
                        f"- **信息比率 (IR)**: {barra_result.information_ratio:.3f}",
                        f"  - 因子 IR: {barra_result.factor_ir:.3f}",
                        f"  - 个股 IR: {barra_result.specific_ir:.3f}",
                        "",
                        "### 风险预算审计",
                        "",
                        f"- 已使用: {barra_result.risk_budget_used:.2%}",
                        f"- 剩余: {barra_result.risk_budget_remaining:.2%}",
                        f"- 利用率: {barra_result.risk_budget_utilization:.1%}",
                        "",
                        "### 10 个风格因子暴露",
                        "",
                        "| 因子 | 主动暴露 | 因子收益 | 收益贡献 | 风险贡献 |",
                        "|------|----------|----------|----------|----------|",
                    ]
                )
                for fe in barra_result.style_factor_exposures:
                    lines.append(
                        f"| {fe.factor_name} | {fe.exposure:+.4f} | {fe.factor_return:+.4f} | "
                        f"{fe.contribution_to_active_return:+.4f} | {fe.contribution_to_active_risk:.4f} |"
                    )
                lines.append("")
                # 行业暴露
                if barra_result.industry_exposures:
                    lines.extend(
                        [
                            "### 行业主动暴露",
                            "",
                            "| 行业 | 主动权重 |",
                            "|------|----------|",
                        ]
                    )
                    for ind, w in sorted(
                        barra_result.industry_exposures.items(),
                        key=lambda x: abs(x[1]),
                        reverse=True,
                    ):
                        lines.append(f"| {ind} | {w:+.2%} |")
                    lines.append("")
                # 诊断告警
                if barra_result.concentrated_factors:
                    lines.append(
                        f"⚠️ **因子集中**: {', '.join(barra_result.concentrated_factors)}"
                    )
                if barra_result.missing_factors:
                    lines.append(
                        f"ℹ️ **因子缺失**: {', '.join(barra_result.missing_factors)}"
                    )
                if barra_result.risk_budget_utilization > 0.9:
                    lines.append(
                        f"⚠️ **风险预算紧张**: 利用率 {barra_result.risk_budget_utilization:.1%}"
                    )
                lines.append("")
        except Exception as exc:  # fail-safe
            logger.error("[Barra] 风险分解失败: %s", exc, exc_info=True)
            lines.extend(["", f"**Barra 风险分解失败**: {exc}", ""])

    all_pass = all(
        p.get("status") == "PASS"
        for p in ctx.state.get("phases", {}).values()
        if isinstance(p, dict)
    )
    lines.extend(
        [
            "",
            "## 总结",
            "",
            f"- 工作流执行 {'成功' if all_pass else '部分失败'}",
            f"- 总成交笔数: {total_orders}",
            f"- 总成交金额: {total_amount:,.0f}",
            f"- 数据源: 2026年交易计划.md + trade_plan_{ctx.trade_date.replace('-', '')}.json",
            "",
        ]
    )

    report_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"报告已生成: {report_path}")

    # 同时保存 JSON 状态
    json_path = report_path.with_suffix(".json")
    try:
        # 使用 json.dump 流式写入文件，避免 json.dumps 在内存中构建巨大字符串导致 MemoryError
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(ctx.state, f, ensure_ascii=False, indent=2, default=str)
        logger.info(f"状态 JSON: {json_path}")
    except (MemoryError, OSError) as e:
        logger.warning(f"状态 JSON 保存失败（内存不足），尝试无缩进模式: {e}")
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(ctx.state, f, ensure_ascii=False, default=str)
            logger.info(f"状态 JSON (无缩进): {json_path}")
        except Exception as e2:  # fail-safe
            logger.error(f"状态 JSON 保存彻底失败: {e2}")

    return report_path
