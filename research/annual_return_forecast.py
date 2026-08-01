# -*- coding: utf-8 -*-
"""
年化收益测算器 (Annual Return Forecast)
======================================

实现 README v8.1 中的「年化收益测算」模块:
  - 多情景分析 (保守 / 中性 / 悲观)
  - 内置夏普比率与最大回撤预测
  - 综合考虑: 现货涨跌 + 对冲影响 + Covered Call 权利金 + 现金利息 + 期权成本
  - 输出可被写入 trade_plan.json 的 `annual_return_forecast` 字段

数据来源:
  - v8.3_institutional/trade_plans/trade_plan_YYYYMMDD.json  (theta_engine + hedge_config)
  - v8.3_institutional/reports/daily_pnl_report_YYYY-MM-DD.json  (持仓 + 净值 + 风险)

用法:
  python annual_return_forecast.py                    # 使用最新 trade_plan + 最新 pnl 报告
  python annual_return_forecast.py 2026-07-20         # 指定交易日
  python annual_return_forecast.py 2026-07-20 --write # 写入 trade_plan
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# ============================================================
# 路径常量
# ============================================================
# 注意: 本文件位于 research/ 子目录, PROJECT_ROOT 必须上溯一级到项目根目录.
# 旧代码用 Path(__file__).resolve().parent 指向 research/, 导致
# PLAN_DIR/REPORTS_DIR 变成 research/v8.3_institutional/... (不存在),
# 进而 trade_plan 与 pnl_report 全部加载失败, stock_ratio 恒为 0.0.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PLAN_DIR = PROJECT_ROOT / "v8.3_institutional" / "trade_plans"
REPORTS_DIR = PROJECT_ROOT / "v8.3_institutional" / "reports"
CONFIG_DIR = PROJECT_ROOT / "config"

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ============================================================
# 目标参数 (与 README 对齐: 年化>=8%, 回撤<15%)
# ============================================================
TARGET_ANNUAL_RETURN = 0.08
# B1.3: 从 config/risk_params.yaml 统一读取 (fail-safe 兜底 0.15)
from utils.risk_params import get_max_drawdown_limit as _get_max_drawdown_limit  # noqa: E402

MAX_DRAWDOWN_LIMIT = _get_max_drawdown_limit()
RF_RATE = 0.025  # 无风险利率 (10年国债)
TARGET_SHARPE = 1.0

# ============================================================
# 核心策略假设
# ============================================================
DEFAULT_CC_YIELD = 0.065          # Covered Call 年化权利金率 (基于现货市值, 近月平值 Call, 参考值)
DEFAULT_TARGET_STOCK_RATIO = 0.90 # 建仓完成度目标 90% (留 10% 现金应对追加保证金/再平衡)
DEFAULT_CASH_INTEREST = 0.011     # 货币基金/逆回购年化 1.1%


# ============================================================
# 工具函数
# ============================================================
def _safe_float(value: Any, default: float = 0.0) -> float:
    """安全转换为 float, 失败时返回 default"""
    try:
        v = float(value)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _extract_date_str(stem: str, prefix: str) -> str:
    """从文件名提取日期字符串 (YYYYMMDD), 兼容 YYYY-MM-DD 与 YYYYMMDD"""
    raw = stem.replace(prefix, "").lstrip("_")
    return raw.replace("-", "")


def _find_latest_trade_plan(target_date: Optional[str] = None) -> Optional[Path]:
    """查找最新的 trade_plan 文件 (可指定日期)"""
    if not PLAN_DIR.exists():
        return None
    if target_date:
        target_compact = target_date.replace("-", "")
        candidates = [
            PLAN_DIR / f"trade_plan_{target_compact}.json",
            PLAN_DIR / f"trade_plan_{target_date}.json",
        ]
        for c in candidates:
            if c.exists():
                return c
    files = sorted(
        PLAN_DIR.glob("trade_plan_*.json"),
        key=lambda p: _extract_date_str(p.stem, "trade_plan_"),
        reverse=True,
    )
    return files[0] if files else None


def _find_latest_pnl_report(target_date: Optional[str] = None) -> Optional[Path]:
    """查找最新的 daily_pnl_report 文件"""
    if not REPORTS_DIR.exists():
        return None
    files = [f for f in REPORTS_DIR.glob("daily_pnl_report_*.json") if "--" not in f.stem]
    if not files:
        return None
    if target_date:
        target_compact = target_date.replace("-", "")
        for f in files:
            if _extract_date_str(f.stem, "daily_pnl_report_") == target_compact:
                return f
    files.sort(key=lambda p: _extract_date_str(p.stem, "daily_pnl_report_"), reverse=True)
    return files[0]


def _load_json(path: Optional[Path]) -> Optional[Dict[str, Any]]:
    """读取 JSON 文件"""
    if not path or not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ============================================================
# 基线数据提取
# ============================================================
def _extract_baseline(trade_plan: Dict[str, Any], pnl_report: Dict[str, Any]) -> Dict[str, float]:
    """提取组合基线数据

    优先级: trade_plan.hedge_fund_overlays.theta_engine > daily_pnl_report.summary > positions
    CC 权利金率默认 6.5% (基于现货市值, 近月平值 Call, 理论参考值)
    """
    theta_engine = (trade_plan.get("hedge_fund_overlays") or {}).get("theta_engine", {})
    cc_premium_annual = _safe_float(theta_engine.get("portfolio_yield_annualized"), 0.0)
    cc_premium_monthly = _safe_float(theta_engine.get("total_premium"), 0.0)

    portfolio_pnl = pnl_report.get("portfolio_pnl", {}).get("summary", {})
    stock_market_value = _safe_float(portfolio_pnl.get("total_market_value"), 0.0)
    stock_cost = _safe_float(portfolio_pnl.get("total_cost"), stock_market_value)

    capital = _safe_float(trade_plan.get("capital"), 5_000_000.0)
    cash_unallocated = max(capital - stock_market_value, 0.0)

    hedge_summary = (pnl_report.get("hedge_position") or {}).get("summary", {})
    put_premium_cost = _safe_float(hedge_summary.get("total_premium_budget"), 0.0)

    # 现金利息: 货币基金/逆回购 年化 1.1%
    cash_interest_annual = DEFAULT_CASH_INTEREST

    # Covered Call 月度权利金 → 年化率 (基于市值)
    if cc_premium_annual <= 0 and cc_premium_monthly > 0 and stock_market_value > 0:
        cc_premium_annual = (cc_premium_monthly * 12) / stock_market_value

    # 若既无portfolio_yield_annualized也无monthly_premium，使用默认参考值
    # 注意：DEFAULT_CC_YIELD 是近月平值Call的理论参考值，不代表实际配置
    if cc_premium_annual <= 0:
        cc_premium_annual = DEFAULT_CC_YIELD

    return {
        "portfolio_base": capital,
        "stock_market_value": stock_market_value,
        "stock_cost": stock_cost,
        "cash_unallocated": cash_unallocated,
        "cc_premium_annual": cc_premium_annual,           # 基于市值的年化权利金率
        "cc_premium_monthly": cc_premium_monthly,
        "cash_interest_annual": cash_interest_annual,
        "put_premium_cost": put_premium_cost,
        "actual_stock_ratio": stock_market_value / capital if capital > 0 else 0.0,  # 当前实际仓位占比
        "target_stock_ratio": DEFAULT_TARGET_STOCK_RATIO,  # 建仓完成度目标 90%
    }


# ============================================================
# 情景定义 — 基于历史分布的场景分析（非目标导向）
# ============================================================
# 情景参数来自A股历史统计：
# - 沪深300年化收益分布：均值~8%, 标准差~25%
# - 保守: 均值-1σ = 8%-25% = -17%（实际下行风险）
# - 中性: 均值 = 8%
# - 悲观: 均值-2σ = 8%-50% = -42%（极端年份如2008/2018）
# 注意：回撤是模拟输出而非预设输入，此处标注为合理估计范围
SCENARIO_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "name": "保守情景",
        "spot_annual_return": -0.17,       # 现货下跌17%（均值-1σ，弱市年份）
        "hedge_impact": 0.07,              # 对冲贡献+7%（IF+Put在下跌中提供保护，约40%对冲效率）
        "max_drawdown": -0.16,             # 回撤估计 -16%（破15%红线）
        "sharpe_target": 0.4,
        "description": "现货下跌17%，对冲吸收7%，CC权利金提供缓冲，综合年化约-3%~-4%",
    },
    {
        "name": "中性情景",
        "spot_annual_return": 0.08,        # 现货上涨8%（A股长期均值）
        "hedge_impact": -0.005,            # 对冲成本0.5%（上涨时对冲工具亏损）
        "max_drawdown": -0.12,             # 回撤估计 -12%（上涨年份回调较浅）
        "sharpe_target": 1.2,
        "description": "现货上涨8%，对冲工具小幅拖累，CC权利金增厚，综合年化约+14%",
    },
    {
        "name": "悲观情景",
        "spot_annual_return": -0.42,       # 现货暴跌42%（均值-2σ，2008/2018级别）
        "hedge_impact": 0.18,              # 对冲贡献+18%（约43%对冲效率，不足以完全覆盖）
        "max_drawdown": -0.27,             # 回撤估计 -27%（大幅超过15%红线）
        "sharpe_target": -0.2,
        "description": "现货暴跌42%，对冲部分吸收，综合年化约-17%，回撤大幅突破15%红线",
    },
]


def _calc_scenario(spot_annual_return: float,
                   hedge_impact: float,
                   baseline: Dict[str, float],
                   max_drawdown: float = -0.15) -> Dict[str, Any]:
    """计算单情景下的综合年化收益

    综合收益 = spot_return × stock_ratio + hedge_impact + cc_yield × stock_ratio + cash_interest × cash_ratio

    使用 target_stock_ratio (建仓完成度 90%) 评估"建仓完成后"的年化收益。

    回撤约束: max_drawdown 超过 -15% 时触发风险预警
    """
    baseline["portfolio_base"]

    # 使用当前实际仓位 (若尚未满仓则保守估计)
    # 同时记录目标仓位用于参考
    actual_stock_ratio = baseline.get("actual_stock_ratio", baseline.get("target_stock_ratio", DEFAULT_TARGET_STOCK_RATIO))
    stock_ratio = min(actual_stock_ratio, baseline.get("target_stock_ratio", DEFAULT_TARGET_STOCK_RATIO))
    cash_ratio = max(1.0 - stock_ratio, 0.0)

    # CC 权利金率 (基于市值) → 组合层面贡献
    cc_yield = baseline["cc_premium_annual"]

    spot_contribution = spot_annual_return * stock_ratio
    hedge_contribution = hedge_impact
    cc_contribution = cc_yield * stock_ratio
    cash_contribution = baseline["cash_interest_annual"] * cash_ratio
    put_cost = baseline.get("put_premium_cost", 0.0)  # Put保护权利金年化成本

    total_annual_return = (spot_contribution + hedge_contribution
                         + cc_contribution + cash_contribution - put_cost)

    # 估算波动率: 现货年化波动 ~18%, 对冲后降至 ~12%; 现金 1%
    portfolio_vol = 0.12 * stock_ratio + 0.01 * cash_ratio
    sharpe_ratio = (total_annual_return - RF_RATE) / portfolio_vol if portfolio_vol > 1e-6 else 0.0

    meets_target = total_annual_return >= TARGET_ANNUAL_RETURN
    drawdown_breached = max_drawdown <= -MAX_DRAWDOWN_LIMIT  # 回撤是否突破 -15% 红线

    return {
        "spot_annual_return": round(spot_annual_return, 4),
        "stock_ratio": round(stock_ratio, 4),
        "spot_contribution": round(spot_contribution, 4),
        "hedge_impact": round(hedge_impact, 4),
        "cc_yield_on_stocks": round(cc_yield, 4),
        "cc_contribution": round(cc_contribution, 4),
        "cash_interest_annual": round(baseline["cash_interest_annual"] * cash_ratio, 4),
        "put_premium_cost": round(put_cost, 4),
        "total_annual_return": round(total_annual_return, 4),
        "portfolio_volatility": round(portfolio_vol, 4),
        "sharpe_ratio": round(sharpe_ratio, 3),
        "max_drawdown": round(max_drawdown, 4),
        "meets_target": bool(meets_target),
        "drawdown_breached": bool(drawdown_breached),
    }


# ============================================================
# 风险识别
# ============================================================
def _extract_key_risks(trade_plan: Dict[str, Any], pnl_report: Dict[str, Any]) -> List[str]:
    """从当前状态识别关键风险"""
    risks: List[str] = []

    theta_engine = (trade_plan.get("hedge_fund_overlays") or {}).get("theta_engine", {})
    if theta_engine.get("positions_count", 0) > 0:
        risks.append("Covered Call 若大涨被行权，收益 capped")

    hedge_config = trade_plan.get("hedge_config", {})
    if hedge_config.get("layers", {}).get("layer1_futures"):
        risks.append("IF 期货对冲可能无法完全覆盖个股风险")

    max_dd = _safe_float(pnl_report.get("risk_metrics", {}).get("max_drawdown_pct"), 0.0)
    if abs(max_dd) > 1:
        max_dd = max_dd / 100.0
    if max_dd <= -0.10:
        risks.append(f"当前回撤 {max_dd:.2%} 已接近 -15% 红线, 警戒级别提升")

    capital = _safe_float(trade_plan.get("capital"), 5_000_000.0)
    stock_mv = _safe_float(pnl_report.get("portfolio_pnl", {}).get("summary", {}).get("total_market_value"), 0.0)
    if capital > 0 and stock_mv / capital < 0.5:
        risks.append("建仓期未完成, 现金占比过高拖累收益")

    return risks


# ============================================================
# 主测算函数
# ============================================================
def forecast_annual_return(target_date: Optional[str] = None) -> Dict[str, Any]:
    """生成多情景年化收益测算

    Args:
        target_date: 目标交易日 (YYYY-MM-DD). None 表示使用最新数据

    Returns:
        测算结果字典, 结构与 trade_plan.annual_return_forecast 字段一致
    """
    plan_path = _find_latest_trade_plan(target_date)
    pnl_path = _find_latest_pnl_report(target_date)

    trade_plan = _load_json(plan_path) or {}
    pnl_report = _load_json(pnl_path) or {}

    baseline = _extract_baseline(trade_plan, pnl_report)
    scenarios: List[Dict[str, Any]] = []
    for definition in SCENARIO_DEFINITIONS:
        result = _calc_scenario(
            spot_annual_return=definition["spot_annual_return"],
            hedge_impact=definition["hedge_impact"],
            baseline=baseline,
            max_drawdown=definition["max_drawdown"],
        )
        result["name"] = definition["name"]
        result["description"] = definition["description"]
        scenarios.append(result)

    key_risks = _extract_key_risks(trade_plan, pnl_report)

    # 回撤红线硬约束验证
    drawdown_breaches = [s["name"] for s in scenarios if s.get("drawdown_breached")]
    if drawdown_breaches:
        key_risks.insert(0, f"⚠️ 回撤红线突破: {', '.join(drawdown_breaches)} 情景回撤 >= -15%, 需调整对冲比例")

    # 8% 目标硬约束验证
    target_breaches = [s["name"] for s in scenarios if not s.get("meets_target")]
    if target_breaches:
        key_risks.append(f"⚠️ 8% 目标未达成: {', '.join(target_breaches)} 情景年化收益 < 8%")

    forecast: Dict[str, Any] = {
        "generated_at": datetime.now().isoformat(),
        "source": f"{plan_path.name if plan_path else 'none'} + {pnl_path.name if pnl_path else 'none'}",
        "methodology": "Covered Call权利金 + 现金利息 + 现货涨跌 + 对冲影响 (基于建仓完成度 90%)",
        "target": {
            "annual_return": TARGET_ANNUAL_RETURN,
            "max_drawdown": MAX_DRAWDOWN_LIMIT,
            "rf_rate": RF_RATE,
        },
        "scenarios": scenarios,
        "baseline": baseline,
        "key_risks": key_risks,
        "summary": _build_summary(scenarios),
        "hard_constraints": {
            "annual_return_target_met": all(s.get("meets_target") for s in scenarios[:2]),  # 保守+中性必须达标
            "drawdown_limit_held": all(not s.get("drawdown_breached") for s in scenarios),  # 所有情景回撤 < 15%
            "target_return": TARGET_ANNUAL_RETURN,
            "max_drawdown_limit": MAX_DRAWDOWN_LIMIT,
        },
    }
    return forecast


def _build_summary(scenarios: List[Dict[str, Any]]) -> Dict[str, Any]:
    """汇总三情景结论"""
    if not scenarios:
        return {}
    returns = [s["total_annual_return"] for s in scenarios]
    sharpes = [s["sharpe_ratio"] for s in scenarios]
    meets = sum(1 for s in scenarios if s["meets_target"])
    drawdowns = [s["max_drawdown"] for s in scenarios]
    worst_drawdown = min(drawdowns) if drawdowns else 0.0

    # 硬约束: 保守+中性必须达标, 所有情景回撤 < -15%
    conservative_neutral_met = all(s.get("meets_target") for s in scenarios[:2])
    drawdown_held = all(not s.get("drawdown_breached") for s in scenarios)

    return {
        "best_case": round(max(returns), 4),
        "worst_case": round(min(returns), 4),
        "average": round(sum(returns) / len(returns), 4),
        "best_sharpe": round(max(sharpes), 3),
        "worst_drawdown": round(worst_drawdown, 4),
        "scenarios_meeting_target": meets,
        "scenarios_total": len(scenarios),
        "target_achievement_rate": round(meets / len(scenarios), 2),
        "hard_constraints": {
            "annual_return_8pct_met": bool(conservative_neutral_met),
            "drawdown_under_15pct": bool(drawdown_held),
            "all_constraints_met": bool(conservative_neutral_met and drawdown_held),
        },
    }


# ============================================================
# 写入 trade_plan
# ============================================================
def write_to_trade_plan(forecast: Dict[str, Any], target_date: Optional[str] = None) -> Optional[Path]:
    """将测算结果写入 trade_plan.json 的 annual_return_forecast 字段

    Returns:
        写入的文件路径, 失败返回 None
    """
    plan_path = _find_latest_trade_plan(target_date)
    if not plan_path or not plan_path.exists():
        return None
    try:
        with open(plan_path, "r", encoding="utf-8") as f:
            plan = json.load(f)
        plan["annual_return_forecast"] = forecast
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        return plan_path
    except Exception:
        return None


# ============================================================
# 终端输出 (Markdown 表格)
# ============================================================
def print_forecast(forecast: Dict[str, Any]) -> None:
    """以 Markdown 表格形式输出测算结果"""
    print("=" * 80)
    print("年化收益测算 (Annual Return Forecast) — 硬性目标: 年化 >= 8%, 回撤 < 15%")
    print(f"生成时间: {forecast.get('generated_at', '')}")
    print(f"数据来源: {forecast.get('source', '')}")
    print(f"方法论: {forecast.get('methodology', '')}")
    print("=" * 80)

    target = forecast.get("target", {})
    print(f"目标: 年化 >= {target.get('annual_return', 0):.2%} | 最大回撤 < {target.get('max_drawdown', 0):.2%} | RF = {target.get('rf_rate', 0):.2%}")
    print()

    baseline = forecast.get("baseline", {})
    print("基线数据 (策略假设):")
    print(f"  组合基础规模:       ¥{baseline.get('portfolio_base', 0):,.0f}")
    print(f"  现货持仓市值:       ¥{baseline.get('stock_market_value', 0):,.0f}")
    print(f"  目标建仓完成度:     {baseline.get('target_stock_ratio', 0.9):.0%}")
    print(f"  CC 权利金率(市值):  {baseline.get('cc_premium_annual', 0):.2%}")
    print(f"  现金利息年化:       {baseline.get('cash_interest_annual', 0):.2%}")
    print(f"  Put 权利金成本:     ¥{baseline.get('put_premium_cost', 0):,.0f}")
    print()

    print("多情景测算 (建仓完成度 90%):")
    print(f"{'情景':<10} {'现货':>7} {'现货贡献':>9} {'对冲':>7} {'CC贡献':>8} {'现金':>7} {'综合':>8} {'夏普':>7} {'回撤':>7} {'达标':>5} {'回撤合规':>8}")
    print("-" * 95)
    for s in forecast.get("scenarios", []):
        print(
            f"{s['name']:<10} "
            f"{s['spot_annual_return']:>+6.2%} "
            f"{s['spot_contribution']:>+8.2%} "
            f"{s['hedge_impact']:>+6.2%} "
            f"{s['cc_contribution']:>+7.2%} "
            f"{s['cash_interest_annual']:>+6.2%} "
            f"{s['total_annual_return']:>+7.2%} "
            f"{s['sharpe_ratio']:>6.3f} "
            f"{s['max_drawdown']:>+6.2%} "
            f"{'✅' if s['meets_target'] else '❌':>5} "
            f"{'✅' if not s.get('drawdown_breached') else '🚨':>8}"
        )
    print()

    summary = forecast.get("summary", {})
    if summary:
        print("综合结论:")
        print(f"  最佳/最差/平均收益: {summary.get('best_case', 0):+.2%} / {summary.get('worst_case', 0):+.2%} / {summary.get('average', 0):+.2%}")
        print(f"  最佳夏普比率:       {summary.get('best_sharpe', 0):.3f}")
        print(f"  最差回撤:           {summary.get('worst_drawdown', 0):+.2%}")
        print(f"  达标情景:           {summary.get('scenarios_meeting_target', 0)} / {summary.get('scenarios_total', 0)} ({summary.get('target_achievement_rate', 0):.0%})")

        hc = summary.get("hard_constraints", {})
        print()
        print("硬约束验证 (必须全部为 True):")
        print(f"  {'✅' if hc.get('annual_return_8pct_met') else '🚨'} 年化 8% 目标 (保守+中性情景): {hc.get('annual_return_8pct_met', False)}")
        print(f"  {'✅' if hc.get('drawdown_under_15pct') else '🚨'} 回撤 < 15% (所有情景):          {hc.get('drawdown_under_15pct', False)}")
        print(f"  {'✅' if hc.get('all_constraints_met') else '🚨'} 全部约束达成:                    {hc.get('all_constraints_met', False)}")
    print()

    risks = forecast.get("key_risks", [])
    if risks:
        print("关键风险:")
        for r in risks:
            print(f"  - {r}")
    print("=" * 80)


# ============================================================
# CLI 入口
# ============================================================
def main() -> int:
    parser = argparse.ArgumentParser(description="年化收益测算器 (多情景分析)")
    parser.add_argument("date", nargs="?", default=None, help="目标交易日 YYYY-MM-DD (默认: 最新)")
    parser.add_argument("--write", action="store_true", help="写入 trade_plan.json")
    parser.add_argument("--json", action="store_true", help="以 JSON 格式输出")
    args = parser.parse_args()

    forecast = forecast_annual_return(args.date)

    if args.json:
        print(json.dumps(forecast, ensure_ascii=False, indent=2))
    else:
        print_forecast(forecast)

    if args.write:
        path = write_to_trade_plan(forecast, args.date)
        if path:
            print(f"[OK] 已写入: {path}")
        else:
            print("[WARN] 写入失败: 未找到 trade_plan 或写入异常")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
