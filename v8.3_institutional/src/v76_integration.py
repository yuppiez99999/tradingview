# v7.6 集成桥接 — 连接 6 个新模块到现有日度工作流
# 用法: 在 daily_workflow.py 的 Stage 2 (风险) 和 Stage 5 (对冲) 之间插入
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict

logger = logging.getLogger("v76.integration")


class v76IntegrationBridge:
    """一键集成: 波动率缩放 + 现金管理 + 对冲验证 + 拥挤检测 + BL模型 + PnL归因"""

    def __init__(self, total_capital: float = 5_000_000):
        self.total_capital = total_capital
        self._modules = {}

    def initialize(self):
        """惰性加载所有模块"""
        from src.hedging.hedge_commander import HedgeExecutionCommander
        from src.pnl.pnl_attribution import PnLAttributionEngine
        from src.portfolio.black_litterman import BlackLittermanEngine
        from src.risk.vol_targeting import VolTargetingEngine
        from src.signals.crowding_detector import SignalCrowdingDetector
        from src.treasury.cash_yield import CashYieldManager

        self._modules = {
            "vol_target": VolTargetingEngine(),
            "cash_yield": CashYieldManager(),
            "hedge_commander": HedgeExecutionCommander(),
            "crowding": SignalCrowdingDetector(),
            "black_litterman": BlackLittermanEngine(),
            "pnl_attr": PnLAttributionEngine(),
        }
        logger.info("v7.6 全部模块加载完成 [6/6]")
        return self

    def run_daily_enhanced(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """增强版日度流程 — 返回全局决策向量

        context 需包含:
            daily_return: float          — 当日 (NAV_t / NAV_{t-1} - 1)
            drawdown_from_hwm: float     — 回撤 (0.05 = 5%)
            portfolio_beta: float        — 现货 Beta
            hedge_beta_offset: float     — 对冲 Beta 降低量
            futures_contracts: int       — 当前期货手数
            total_cash: float            — 现金余额
            portfolio_market_value: float — 组合市值
            etf_flows: dict              — {'证券': 6.7e9, '科创50': 5.7e9, ...}
            positions: list              — PositionSnapshot 列表
            total_pnl: float             — 当日总盈亏
        """
        results = {}
        actions = []

        # ---- Stage A: 波动率 → 仓位缩放 ----
        vt = self._modules["vol_target"]
        vol_scale = vt.update(context.get("daily_return", 0.0), context.get("drawdown_from_hwm", 0.0))
        vol_report = vt.report()
        results["vol_scale"] = vol_scale
        results["vol_report"] = vol_report

        if vol_scale < 0.80:
            actions.append(f"减仓: Vol比率={vol_report['vol_ratio']}x, 缩放至{vol_scale:.2f}")
        elif vol_scale > 1.20:
            actions.append(f"可加仓: 缩放{vol_scale:.2f}")

        # ---- Stage B: 现金管理 ----
        cy = self._modules["cash_yield"]
        cash_result = cy.optimize(context.get("total_cash", 0))
        results["cash"] = cash_result

        if cash_result["deploy_repo"] > 100_000:
            actions.append(
                f"现金管理: 逆回购 {cash_result['deploy_repo'] / 1e4:.0f}万, "
                f"日收益 {cash_result['expected_daily_income']:.0f}元"
            )

        # ---- Stage C: 对冲校验 ----
        hc = self._modules["hedge_commander"]
        hedge_result = hc.assess(
            actual_portfolio_beta=context.get("portfolio_beta", 0),
            current_hedge_beta_offset=context.get("hedge_beta_offset", 0),
            current_futures_contracts=context.get("futures_contracts", 0),
            portfolio_notional=context.get("portfolio_market_value", self.total_capital),
        )
        results["hedge"] = hedge_result

        if hedge_result["force_execute"]:
            actions.append(
                f"紧急对冲: {hedge_result['action_text']} "
                f"(Beta {hedge_result['effective_beta']:.2f}→{hedge_result['target_beta']:.2f})"
            )

        # ---- Stage D: 拥挤检测 ----
        cd = self._modules["crowding"]
        crowd = cd.update(context.get("etf_flows", {}))
        results["crowding"] = crowd

        crowded = [s for s, d in crowd.items() if d["status"] != "NORMAL"]
        if crowded:
            names = ",".join(f"{s}(×{crowd[s]['signal_multiplier']:.0%})" for s in crowded)
            actions.append(f"拥挤度调整: {names}")

        # ---- Stage E: 归因 ----
        pa = self._modules["pnl_attr"]
        attrib = pa.attribute_daily(
            positions=context.get("positions", []),
            total_pnl=context.get("total_pnl", 0),
            total_nav=context.get("portfolio_market_value", self.total_capital),
        )
        results["pnl_attribution"] = attrib

        if attrib.get("top3_contributors"):
            top = attrib["top3_contributors"][0]
            actions.append(f"最大贡献: {top[0]} +{top[1]}%")

        # ---- 汇总 ----
        summary = {
            "timestamp": datetime.now().isoformat(),
            "vol_scale": float(vol_scale),
            "cash_income_today": cash_result["expected_daily_income"],
            "hedge_urgency": hedge_result["urgency"],
            "crowded_sectors": crowded,
            "alpha_bps_today": attrib.get("alpha_pct", 0),
            "actions": actions,
            "modules": results,
        }

        logger.info("v7.6 日度增强完成: %d 操作", len(actions))
        return summary

    def report(self) -> dict:
        reports = {}
        for name, mod in self._modules.items():
            if hasattr(mod, "report"):
                reports[name] = mod.report()
        return reports
