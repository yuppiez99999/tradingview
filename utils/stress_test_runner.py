# -*- coding: utf-8 -*-
"""
压力测试自动化模块 (Stress Test Runner)
========================================

基于 v10.0 投资计划手册第四章 4.4 节, 实现四种压力测试场景:
    1. 2015 式股灾: 指数 -40%/3个月/V 型反弹 → 预期组合回撤 -8.5%
    2. 2018 式慢熊: 指数 -25%/12个月/阴跌 → 预期组合回撤 -13.9% (无干预) / -8.0% (有干预)
    3. 2020 式冲击: 指数 -15%/1个月/V 型反弹 → 预期组合回撤 -0.4%
    4. 流动性危机: 相关性 → 1, 对冲效率降至 30% → 预期组合回撤 -18.6%

季度执行, 输出报告到 reports/stress_test_{date}.json

用法:
    from utils.stress_test_runner import StressTestRunner
    runner = StressTestRunner()
    result = runner.run_all_scenarios(positions, portfolio_value=5_000_000)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger("stress_test")

BASE_DIR = Path(__file__).resolve().parent.parent
REPORT_DIR = BASE_DIR / "reports"


# 四大压力测试场景定义 (v10.0 手册)
STRESS_SCENARIOS = {
    "crash_2015": {
        "name": "2015式股灾",
        "description": "指数 -40%/3个月/V型反弹",
        "index_drop_pct": -0.40,
        "duration_months": 3,
        "shape": "V",
        "asset_impacts": {
            "stock": -0.40,        # 股票 -40%
            "etf": -0.35,          # ETF -35%
            "quant_neutral": -0.15, # 量化中性 -15%
            "options_tail": 0.75,   # 尾部期权 +75% (put payoff)
            "futures_hedge": 0.30,  # 期货对冲回收 30%
            "cash": 0.0,           # 现金无影响
        },
        "expected_portfolio_dd": -0.085,
        "limit": -0.15,
    },
    "slow_bear_2018": {
        "name": "2018式慢熊",
        "description": "指数 -25%/12个月/阴跌",
        "index_drop_pct": -0.25,
        "duration_months": 12,
        "shape": "downside",
        "asset_impacts": {
            "stock": -0.25,
            "etf": -0.20,
            "quant_neutral": -0.08,
            "options_tail": -1.0,   # 期权全部到期归零
            "futures_hedge": 0.30,  # 对冲回收 30% (有干预时)
            "cash": 0.0,
        },
        "expected_portfolio_dd": -0.139,
        "expected_with_intervention": -0.08,
        "limit": -0.15,
    },
    "v_shock_2020": {
        "name": "2020式冲击",
        "description": "指数 -15%/1个月/V型反弹",
        "index_drop_pct": -0.15,
        "duration_months": 1,
        "shape": "V",
        "asset_impacts": {
            "stock": -0.15,
            "etf": -0.12,
            "quant_neutral": -0.03,
            "options_tail": 0.30,
            "futures_hedge": 0.15,
            "cash": 0.0,
        },
        "expected_portfolio_dd": -0.004,
        "limit": -0.15,
    },
    "liquidity_crisis": {
        "name": "流动性危机",
        "description": "相关性→1, 对冲效率降至30%",
        "index_drop_pct": -0.20,
        "duration_months": 1,
        "shape": "sharp_drop",
        "asset_impacts": {
            "stock": -0.35,
            "etf": -0.30,
            "quant_neutral": -0.20,  # 中性策略失效
            "options_tail": 0.40,    # 对冲效率降至 30%
            "futures_hedge": 0.10,   # 对冲效率降至 30%
            "cash": 0.0,
        },
        "expected_portfolio_dd": -0.186,
        "limit": -0.15,
        "pre_action": "VIX>40 或基差>2% 时提前 1 天降至 40% 仓位, 可将回撤压至 -14%",
        "pre_action_dd_with_intervention": -0.14,
    },
}


class StressTestRunner:
    """压力测试自动化执行器"""

    def __init__(self):
        REPORT_DIR.mkdir(parents=True, exist_ok=True)

    def run_all_scenarios(self, positions: List[Dict[str, Any]],
                          portfolio_value: float = 5_000_000,
                          with_intervention: bool = True) -> Dict[str, Any]:
        """运行所有压力测试场景

        Args:
            positions: 持仓列表 [{"code", "name", "amount", "style", "strategy"}, ...]
            portfolio_value: 组合净值
            with_intervention: 是否包含干预措施 (对冲+减仓)

        Returns:
            {
                "timestamp": "...",
                "portfolio_value": 5000000,
                "scenarios": {...},
                "all_pass": True/False,
                "worst_scenario": "...",
                "report_path": "..."
            }
        """
        results: Dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "portfolio_value": portfolio_value,
            "with_intervention": with_intervention,
            "scenarios": {},
        }

        all_pass = True
        worst_dd = 0.0
        worst_scenario = ""

        for scenario_id, scenario_def in STRESS_SCENARIOS.items():
            scenario_result = self._run_scenario(
                scenario_id, scenario_def, positions, portfolio_value, with_intervention
            )
            results["scenarios"][scenario_id] = scenario_result

            actual_dd = scenario_result["actual_portfolio_dd"]
            limit = scenario_def["limit"]

            scenario_result["pass"] = actual_dd >= limit
            if actual_dd < limit:
                all_pass = False

            if actual_dd < worst_dd:
                worst_dd = actual_dd
                worst_scenario = scenario_id

        results["all_pass"] = all_pass
        results["worst_scenario"] = worst_scenario
        results["worst_dd"] = worst_dd

        # 保存报告
        report_path = self._save_report(results)
        results["report_path"] = str(report_path)

        return results

    def _run_scenario(self, scenario_id: str, scenario_def: Dict,
                      positions: List[Dict], portfolio_value: float,
                      with_intervention: bool) -> Dict[str, Any]:
        """运行单个压力测试场景"""
        impacts = scenario_def["asset_impacts"]

        # 计算各资产类别的影响
        asset_class_pnl: Dict[str, float] = {}
        total_pnl = 0.0

        for pos in positions:
            strategy = pos.get("strategy", pos.get("style", ""))
            amount = float(pos.get("amount", pos.get("market_value", 0)))

            # 确定资产类别影响
            impact_pct = 0.0
            if "stock" in strategy.lower() or pos.get("style") in ("科技", "制造", "医药", "新能源", "军工"):
                impact_pct = impacts.get("stock", 0)
            elif "etf" in strategy.lower() or "ETF" in pos.get("name", ""):
                impact_pct = impacts.get("etf", 0)
            elif "neutral" in strategy.lower() or "中性" in strategy:
                impact_pct = impacts.get("quant_neutral", 0)
            elif "option" in strategy.lower() or "期权" in strategy:
                impact_pct = impacts.get("options_tail", 0)
            elif "future" in strategy.lower() or "期货" in strategy:
                impact_pct = impacts.get("futures_hedge", 0)
            elif "cash" in strategy.lower() or "现金" in strategy:
                impact_pct = impacts.get("cash", 0)

            pos_pnl = amount * impact_pct
            total_pnl += pos_pnl

            cat = strategy if strategy else "other"
            if cat not in asset_class_pnl:
                asset_class_pnl[cat] = 0
            asset_class_pnl[cat] += pos_pnl

        # 干预措施影响 (有干预时, 慢熊和流动性危机改善)
        intervention_benefit = 0.0
        if with_intervention:
            if scenario_id == "slow_bear_2018":
                # 有干预: 对冲回收 30%, 实际减仓 20%
                intervention_benefit = abs(total_pnl) * 0.42  # -13.9% → -8.0%
            elif scenario_id == "liquidity_crisis":
                # 有干预: 提前降至 40% 仓位
                intervention_benefit = abs(total_pnl) * 0.25  # -18.6% → -14%

        actual_pnl = total_pnl + intervention_benefit
        actual_dd = actual_pnl / portfolio_value if portfolio_value > 0 else 0

        expected_dd = scenario_def.get("expected_portfolio_dd", 0)
        if with_intervention:
            if scenario_id == "slow_bear_2018":
                expected_dd = scenario_def.get("expected_with_intervention", expected_dd)
            elif scenario_id == "liquidity_crisis":
                expected_dd = scenario_def.get("pre_action_dd_with_intervention", expected_dd)

        return {
            "scenario_id": scenario_id,
            "scenario_name": scenario_def["name"],
            "description": scenario_def["description"],
            "index_drop_pct": scenario_def["index_drop_pct"],
            "duration_months": scenario_def["duration_months"],
            "shape": scenario_def["shape"],
            "total_pnl_no_intervention": total_pnl,
            "intervention_benefit": intervention_benefit,
            "actual_pnl": actual_pnl,
            "actual_portfolio_dd": actual_dd,
            "expected_portfolio_dd": expected_dd,
            "limit": scenario_def["limit"],
            "pass": actual_dd >= scenario_def["limit"],
            "asset_class_pnl": asset_class_pnl,
            "with_intervention": with_intervention,
            "pre_action": scenario_def.get("pre_action"),
        }

    def _save_report(self, results: Dict) -> Path:
        """保存压力测试报告"""
        date_str = datetime.now().strftime("%Y%m%d")
        report_path = REPORT_DIR / f"stress_test_{date_str}.json"
        try:
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2, default=str)
            logger.info(f"压力测试报告已保存: {report_path}")
        except Exception as e:
            logger.error(f"保存压力测试报告失败: {e}")
        return report_path


# ============================================================
# CLI 入口
# ============================================================
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="压力测试自动化")
    parser.add_argument("--portfolio", type=float, default=5_000_000, help="组合净值")
    parser.add_argument("--simulate", action="store_true", help="使用模拟持仓测试")
    args = parser.parse_args()

    runner = StressTestRunner()

    if args.simulate:
        # 模拟持仓
        positions = [
            {"code": "stock", "name": "股票多头", "amount": 1_800_000, "strategy": "stock_long"},
            {"code": "etf", "name": "ETF组合", "amount": 500_000, "strategy": "etf"},
            {"code": "quant", "name": "量化中性", "amount": 700_000, "strategy": "quant_neutral"},
            {"code": "option", "name": "期权尾部", "amount": 200_000, "strategy": "options_tail"},
            {"code": "future", "name": "期货对冲", "amount": 500_000, "strategy": "futures_hedge"},
            {"code": "cash", "name": "现金管理", "amount": 1_300_000, "strategy": "cash"},
        ]

        result = runner.run_all_scenarios(positions, args.portfolio, with_intervention=True)

        print("\n" + "=" * 60)
        print("压力测试结果")
        print("=" * 60)
        for sid, sres in result["scenarios"].items():
            status = "✅ 通过" if sres["pass"] else "❌ 超限"
            print(f"\n{sres['scenario_name']} ({sid})")
            print(f"  指数跌幅: {sres['index_drop_pct']:.0%}")
            print(f"  预期回撤: {sres['expected_portfolio_dd']:.1%}")
            print(f"  实际回撤: {sres['actual_portfolio_dd']:.1%}")
            print(f"  限额:     {sres['limit']:.1%}")
            print(f"  状态:     {status}")
            if sres.get("pre_action"):
                print(f"  预警:     {sres['pre_action']}")

        print(f"\n{'=' * 60}")
        print(f"总体: {'✅ 全部通过' if result['all_pass'] else '❌ 有场景超限'}")
        print(f"最差场景: {result['worst_scenario']} (回撤 {result['worst_dd']:.1%})")
        print(f"报告: {result['report_path']}")
