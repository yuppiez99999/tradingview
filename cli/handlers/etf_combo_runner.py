"""ETF 期权联动对冲组合策略入口 — 由 `量化策略系统_统一入口_v8.6.py` 的 main() 迁出（item 11 续做）。

迁出原则：字节级等价（仅去掉一级 4 空格缩进 + 补齐 `logger`/`sys` 导入）。
**退出码语义原样保留**：`utils/etf_option_combo` 缺失 → `sys.exit(2)`；执行失败 → `sys.exit(1)`。
两者都刻意非 0，避免 cron/CI 把"模块缺失/执行失败"误判为成功。
"""
from __future__ import annotations

import argparse
import sys

from cli.handlers.support import logger


def run_etf_combo(args: argparse.Namespace) -> None:
    try:
        from utils.etf_option_combo.combo_backtest import ComboBacktest
        from utils.etf_option_combo.combo_orchestrator import ComboOrchestrator
    except ImportError as e:
        logger.error(
            f"\n❌ ETF期权联动模块不可用 (utils/etf_option_combo 未安装或未纳入版本库): {e}"
        )
        sys.exit(2)  # 模块缺失: 非 0 退出, 避免 cron/CI 误判成功
    try:
        if getattr(args, "etf_combo_backtest", False):
            bt = ComboBacktest()
            result = bt.run_backtest("2026-01-01", "2026-09-01")
            m = result["metrics"]
            logger.info(
                "ETF期权联动回测: 年化=%.4f 回撤=%.4f Sharpe=%.4f 对冲效率=%.4f 交易数=%d",
                m["annual_return"], m["max_drawdown"], m["sharpe"],
                result["hedge_efficiency"], result["trade_count"],
            )
            # IV Rank 自适应对比 (collar): static vs adaptive, 作 enabled=true 决策材料
            comparison = bt.run_comparison("2026-01-01", "2026-09-01")
            sm = comparison["static"]["metrics"]
            am = comparison["adaptive"]["metrics"]
            anc = comparison["avg_net_cost"]
            d = comparison["delta"]

            def _fmt_cost(v: float | None) -> str:
                return "N/A" if v is None else f"{v:.2f}"

            logger.info(
                "IV自适应对比[static  ] 回撤=%.4f Sharpe=%.4f 对冲效率=%.4f 均净成本=%s",
                sm["max_drawdown"], sm["sharpe"],
                comparison["static"]["hedge_efficiency"], _fmt_cost(anc["static"]),
            )
            logger.info(
                "IV自适应对比[adaptive] 回撤=%.4f Sharpe=%.4f 对冲效率=%.4f 均净成本=%s",
                am["max_drawdown"], am["sharpe"],
                comparison["adaptive"]["hedge_efficiency"], _fmt_cost(anc["adaptive"]),
            )
            logger.info(
                "IV自适应对比[delta  ] Δ回撤=%+.4f ΔSharpe=%+.4f Δ对冲效率=%+.4f Δ均净成本=%s (负=adaptive更省)",
                d["max_drawdown"], d["sharpe"], d["hedge_efficiency"],
                "N/A" if d["avg_net_cost"] is None else f"{d['avg_net_cost']:+.2f}",
            )
        elif getattr(args, "etf_combo_monitor", False):
            orch = ComboOrchestrator()
            orch.monitor()
            snapshot = orch.get_portfolio_snapshot()
            logger.info("ETF期权联动监控: 策略实例=%d 预算=%s", snapshot["strategy_instances"], snapshot["budgets"])
        elif getattr(args, "etf_combo_roll", False):
            orch = ComboOrchestrator()
            roll_results = orch.roll_all()
            logger.info("ETF期权联动滚仓: %d 策略需滚仓", len(roll_results))
        else:
            orch = ComboOrchestrator()
            results = orch.run_all(market_state={"regime": "calm"})
            total_orders = sum(
                len(r.orders) for combo_list in results.values() for r in combo_list
            )
            logger.info("ETF期权联动运行: %d 标的, %d 订单", len(results), total_orders)
    except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError) as e:
        logger.error(f"\n❌ ETF期权联动执行失败: {e}")
        sys.exit(1)  # 非 0 退出码, 避免自动化脚本误判成功
