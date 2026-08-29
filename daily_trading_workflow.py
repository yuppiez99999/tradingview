"""每日三阶段交易工作流 — 模拟数据版

用于完整运行 daily_workflow.py 的端到端验证。
使用 positions.json 真实配置 + 模拟行情数据，调用真实的对冲执行引擎。

三阶段:
    1. premarket  — 盘前计划生成 (读取配置, 生成交易计划JSON)
    2. intraday   — 盘中策略扫描 (模拟行情, 扫描信号)
    3. postmarket — 盘后报告生成 (计算盈亏, 调用对冲引擎)

用法:
    直接运行:  python daily_trading_workflow.py
    被调用:    from daily_trading_workflow import run_all
"""

from __future__ import annotations

import json
import logging
import os
import random
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict

logger = logging.getLogger("daily_trading_workflow")
logging.basicConfig(level=logging.INFO, format="%(message)s")

BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = BASE_DIR / "config"
TRADE_PLANS_DIR = BASE_DIR / "trade_plans"
REPORTS_DIR = BASE_DIR / "reports"
STATE_DIR = BASE_DIR / "reports" / "workflow_state"

# 确保输出目录存在
TRADE_PLANS_DIR.mkdir(exist_ok=True)
REPORTS_DIR.mkdir(exist_ok=True)
STATE_DIR.mkdir(parents=True, exist_ok=True)

TODAY = datetime.now().strftime("%Y%m%d")

# 模拟行情数据来源标识 (避免硬编码散落)
MOCK_SOURCE = "模拟数据 (random seed=42)"


# ============================================================
# 类型定义 (类型契约)
# ============================================================
class PositionDict(TypedDict, total=False):
    """持仓字典类型契约"""

    code: str
    name: str
    shares: int
    avg_cost: float
    est_price: float
    strategy: str  # H-4 预防: 持仓归属策略


class MarketDataDict(TypedDict):
    """行情数据字典类型契约"""

    open: float
    high: float
    low: float
    close: float
    volume: int
    change_pct: float


class SignalDict(TypedDict):
    """信号字典类型契约"""

    code: str
    name: str
    shares: int
    cost: float
    close: float
    market_value: float
    pnl: float
    pnl_pct: float
    change_pct: float
    signal: str


class PortfolioSummaryDict(TypedDict):
    """组合摘要类型契约"""

    total_capital: float
    total_market_value: float
    total_cost: float
    total_pnl: float
    total_pnl_pct: float
    position_count: int


# ============================================================
# 状态持久化 (预防 H-3/H-5: 风控/回撤状态丢失)
# ============================================================
def _save_workflow_state(state: dict[str, Any], state_name: str) -> Path:
    """保存工作流状态到磁盘

    Args:
        state: 状态字典
        state_name: 状态名称 (如 'risk_state', 'drawdown_state')

    Returns:
        保存的文件路径
    """
    state_file = STATE_DIR / f"{state_name}_{TODAY}.json"
    try:
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        logger.debug("状态已保存: %s (%d 键)", state_file.name, len(state))
    except Exception as e:
        logger.warning("保存状态失败 %s: %s", state_file, e)
    return state_file


def _load_workflow_state(state_name: str) -> dict[str, Any]:
    """从磁盘加载工作流状态

    Args:
        state_name: 状态名称

    Returns:
        状态字典, 不存在则返回空字典
    """
    state_file = STATE_DIR / f"{state_name}_{TODAY}.json"
    if not state_file.exists():
        return {}
    try:
        with open(state_file, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("加载状态失败 %s: %s", state_file, e)
        return {}


def _load_positions() -> dict[str, Any]:
    """读取 positions.json"""
    positions_file = _resolve_path("config/positions.json")
    with open(positions_file, encoding="utf-8") as f:
        return json.load(f)


def _resolve_path(relative_path: str) -> Path:
    """解析相对路径为绝对路径 (预防路径配置化问题)

    Args:
        relative_path: 相对于项目根目录的路径

    Returns:
        绝对 Path 对象
    """
    # 支持正斜杠/反斜杠混用
    normalized = relative_path.replace("/", os.sep).replace("\\", os.sep)
    path = BASE_DIR / normalized
    if not path.exists():
        # 尝试直接解析 (如果已经是绝对路径)
        path = Path(relative_path)
    return path


# ============================================================
# 模拟行情数据加载
# ============================================================
def _generate_mock_market_data(
    positions: dict[str, Any],
) -> dict[str, dict[str, float]]:
    """读取模拟行情数据文件，若不存在则内部生成

    优先读取 config/mock_market_data_{TODAY}.json 外部文件,
    文件不存在时降级为内部随机生成 (seed=42, 可复现)。

    Args:
        positions: positions.json 中的 positions 字典

    Returns:
        {code: {open, high, low, close, volume, change_pct}}
    """
    # 优先读取外部行情数据文件
    mock_file = CONFIG_DIR / f"mock_market_data_{TODAY}.json"
    if mock_file.exists():
        try:
            with open(mock_file, encoding="utf-8") as f:
                mock = json.load(f)
            quotes = mock.get("quotes", {})
            market_data: dict[str, dict[str, float]] = {}
            for code, q in quotes.items():
                market_data[code] = {
                    "open": float(q.get("open", 0)),
                    "high": float(q.get("high", 0)),
                    "low": float(q.get("low", 0)),
                    "close": float(q.get("close", 0)),
                    "volume": int(q.get("volume", 0)),
                    "change_pct": float(q.get("change_pct", 0)),
                }
            logger.info(
                "已加载外部行情数据: %s (%d 只标的)", mock_file.name, len(market_data)
            )
            return market_data
        except Exception as e:
            logger.warning("读取外部行情文件失败, 降级为内部生成: %s", e)

    # 降级: 内部随机生成 (seed=42, 可复现)
    # 修复: 使用局部 Random 实例, 避免污染进程全局 random 状态 (影响其他模块)
    logger.info("使用内部模拟行情 (seed=42)")
    _rng = random.Random(42)
    market_data = {}

    for code, pos in positions.items():
        if not isinstance(pos, dict):
            continue
        base_price = float(pos.get("est_price", 10.0))
        if base_price <= 0:
            continue

        # 模拟日内波动: -3% ~ +3%
        change_pct = _rng.uniform(-0.03, 0.03)
        open_price = base_price
        close_price = round(base_price * (1 + change_pct), 3)
        high_price = round(
            max(open_price, close_price) * (1 + _rng.uniform(0, 0.015)), 3
        )
        low_price = round(
            min(open_price, close_price) * (1 - _rng.uniform(0, 0.015)), 3
        )
        volume = _rng.randint(1000, 50000)

        market_data[code] = {
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "volume": volume,
            "change_pct": round(change_pct * 100, 2),
        }

    return market_data


def _load_positions() -> dict[str, Any]:
    """读取 positions.json"""
    positions_file = _resolve_path("config/positions.json")
    with open(positions_file, encoding="utf-8") as f:
        return json.load(f)


# ============================================================
# 阶段一: 盘前计划生成
# ============================================================
def run_premarket() -> dict[str, Any]:
    """盘前计划生成 — 读取配置, 输出交易计划 JSON

    Returns:
        盘前计划字典
    """

    data = _load_positions()
    meta = data.get("meta", {})
    positions = data.get("positions", {})
    hedge = data.get("hedge_positions", {})

    # 持仓摘要
    position_count = len([k for k, v in positions.items() if isinstance(v, dict)])
    total_market_value = sum(
        float(v.get("amount", 0)) for v in positions.values() if isinstance(v, dict)
    )

    # 对冲配置摘要
    hedge_mode = hedge.get("hedge_mode", "UNKNOWN")
    budget = hedge.get("budget_summary", {})
    put_premium = budget.get("total_put_premium", 0)
    call_income = budget.get("estimated_annual_call_income", 0)
    net_cost = budget.get("net_annual_hedge_cost", 0)

    # Covered Call 标的
    cc_underlyings = hedge.get("covered_call_overlay", {}).get("underlyings", [])
    # Put 保护标的
    put_protection = hedge.get("risk_reversal_collar", {}).get("put_protection", {})
    put_list = [
        {
            "instrument": v.get("instrument", ""),
            "contracts": v.get("target_contracts", 0),
            "strike": v.get("strike", ""),
            "budget": v.get("premium_budget", 0),
        }
        for v in put_protection.values()
        if isinstance(v, dict)
    ]

    plan = {
        "date": TODAY,
        "phase": "premarket",
        "generated_at": datetime.now().isoformat(),
        "meta": {
            "total_capital": meta.get("total_capital", 0),
            "stock_etf_capital": meta.get("stock_etf_capital", 0),
            "hedge_capital": meta.get("hedge_capital", 0),
            "hedge_mode": hedge_mode,
        },
        "positions_summary": {
            "count": position_count,
            "total_market_value": round(total_market_value, 2),
        },
        "hedge_plan": {
            "mode": hedge_mode,
            "permission_level": hedge.get("permission_level", "LEVEL_1"),
            "covered_call_targets": [
                {
                    "code": u.get("code", ""),
                    "name": u.get("name", ""),
                    "direction": u.get("direction", ""),
                    "strike_rule": u.get("strike_rule", ""),
                }
                for u in cc_underlyings
                if isinstance(u, dict)
            ],
            "put_protection": put_list,
            "bear_put_spread_enabled": hedge.get("risk_reversal_collar", {})
            .get("bear_put_spread", {})
            .get("enabled", False),
            "vega_enabled": hedge.get("vega_event_driven", {}).get("enabled", False),
            "budget": {
                "put_premium": put_premium,
                "call_income_annual": call_income,
                "net_cost_annual": net_cost,
            },
        },
        "actions": [
            "09:35 买入Put保护 (OTM 5%, 证券账户内)",
            "10:05 Covered Call备兑开仓 (OTM 5-8%)",
            "14:50 尾盘复核持仓和权利金收支",
        ],
    }

    # 写入 trade_plans/
    plan_file = TRADE_PLANS_DIR / f"trade_plan_{TODAY}.json"
    with open(plan_file, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)

    return plan


# ============================================================
# 阶段二: 盘中策略扫描
# ============================================================
def run_intraday() -> dict[str, Any]:
    """盘中策略扫描 — 模拟行情, 扫描持仓信号

    Returns:
        盘中扫描结果字典
    """

    data = _load_positions()
    positions = data.get("positions", {})
    market_data = _generate_mock_market_data(positions)

    # 扫描每个持仓的信号
    signals: list[dict[str, Any]] = []
    total_pnl = 0.0
    winners = 0
    losers = 0

    for code, pos in positions.items():
        if not isinstance(pos, dict):
            continue
        name = pos.get("name", code)
        shares = int(pos.get("shares", 0))
        cost = float(pos.get("avg_cost", 0))
        md = market_data.get(code, {})
        close = md.get("close", cost)

        market_value = shares * close
        cost_value = shares * cost
        pnl = market_value - cost_value
        pnl_pct = (pnl / cost_value * 100) if cost_value > 0 else 0
        total_pnl += pnl

        if pnl > 0:
            winners += 1
        elif pnl < 0:
            losers += 1

        # 模拟信号: 基于涨跌幅
        change = md.get("change_pct", 0)
        if change < -2:
            signal = "WATCH"  # 跌幅较大, 关注
        else:
            signal = "HOLD"  # 涨幅较大或平稳, 持有

        signals.append(
            {
                "code": code,
                "name": name,
                "shares": shares,
                "cost": cost,
                "close": close,
                "market_value": round(market_value, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "change_pct": change,
                "signal": signal,
            }
        )

    result = {
        "date": TODAY,
        "phase": "intraday",
        "generated_at": datetime.now().isoformat(),
        "market_data_source": MOCK_SOURCE,
        "summary": {
            "total_positions": len(signals),
            "winners": winners,
            "losers": losers,
            "total_pnl": round(total_pnl, 2),
        },
        "signals": signals,
    }

    sorted_signals = sorted(signals, key=lambda x: x["change_pct"], reverse=True)
    for _s in sorted_signals[:3]:
        pass

    return result


# ============================================================
# 阶段三: 盘后报告生成
# ============================================================
def run_postmarket() -> dict[str, Any]:
    """盘后报告生成 — 计算盈亏, 调用对冲执行引擎

    Returns:
        盘后报告字典
    """

    data = _load_positions()
    positions = data.get("positions", {})
    meta = data.get("meta", {})
    market_data = _generate_mock_market_data(positions)

    # 计算组合盈亏
    total_market_value = 0.0
    total_cost = 0.0
    position_details: list[dict[str, Any]] = []

    for code, pos in positions.items():
        if not isinstance(pos, dict):
            continue
        shares = int(pos.get("shares", 0))
        cost = float(pos.get("avg_cost", 0))
        name = pos.get("name", code)
        md = market_data.get(code, {})
        close = md.get("close", cost)

        mv = shares * close
        cv = shares * cost
        pnl = mv - cv
        total_market_value += mv
        total_cost += cv

        position_details.append(
            {
                "code": code,
                "name": name,
                "shares": shares,
                "cost": cost,
                "close": close,
                "market_value": round(mv, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round((pnl / cv * 100) if cv > 0 else 0, 2),
            }
        )

    total_pnl = total_market_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0

    # 调用对冲执行引擎
    hedge_orders: dict[str, Any] = {}
    try:
        from utils.hedge_execution_engine import HedgeExecutionEngine

        engine = HedgeExecutionEngine()
        hedge_result = engine.generate_hedge_orders(drawdown_level=0)

        futures_orders = hedge_result.get("futures_orders", [])
        options_orders = hedge_result.get("options_orders", [])
        covered_call_orders = hedge_result.get("covered_call_orders", [])
        cost_summary = hedge_result.get("cost_summary", {})

        hedge_orders = {
            "futures_orders_count": len(futures_orders),
            "futures_orders": futures_orders,
            "options_orders_count": len(options_orders),
            "options_orders": options_orders,
            "covered_call_orders_count": len(covered_call_orders),
            "covered_call_orders": covered_call_orders,
            "total_margin": cost_summary.get("total_margin_required", 0),
            "total_premium": cost_summary.get("total_premium_budget", 0),
            "total_call_income": cost_summary.get("total_call_income", 0),
            "net_cost": cost_summary.get("total_cost", 0),
            "hedge_mode": data.get("hedge_positions", {}).get("hedge_mode", ""),
        }

        for oo in options_orders:
            oo.get("instrument", "?")
            oo.get("contracts", 0)
            oo.get("strike_rule", "?")
            oo.get("premium_budget", 0)
        for cc in covered_call_orders:
            cc.get("instrument", "?")
            cc.get("contracts", 0)
            cc.get("otm_pct", 0)
            cc.get("est_strike_price", 0)
            cc.get("est_total_premium", 0)
        cost_summary.get("total_call_income", 0)
        cost_summary.get("total_premium_budget", 0)
        cost_summary.get("total_cost", 0)

    except Exception as e:
        import traceback

        traceback.print_exc()
        hedge_orders = {"error": str(e)}

    # ★ 期权对冲订单执行器 (2026-08-06 P0 修复: 补齐"订单→撮合"闭环)
    # 在 hedge_orders 生成后执行 PENDING 期权订单, 产生 hedge_execution_fill_*.json
    hedge_execution_result: dict[str, Any] = {}
    try:
        from hedge_order_executor import execute_hedge_orders

        hedge_execution_result = execute_hedge_orders(trade_date=TODAY)
        hedge_execution_result.get("filled_count", 0)
    except Exception as e:
        hedge_execution_result = {"error": str(e)}

    # ★ 再平衡撮合执行器 (G2/G4 修复: 补齐"订单→撮合→成交回报→持仓回写"闭环)
    # 在 hedge_orders 生成后执行再平衡 PENDING 订单, 产出 FillsStore + 回写 positions.json
    rebalance_execution_result: dict[str, Any] = {}
    try:
        from rebalance_order_executor import execute_rebalance_orders

        rebalance_execution_result = execute_rebalance_orders(date=TODAY)
        rebalance_execution_result.get("filled", 0)
        rebalance_execution_result.get("positions_updated", 0)
    except Exception as e:
        rebalance_execution_result = {"error": str(e)}

    # 生成报告
    report = {
        "date": TODAY,
        "phase": "postmarket",
        "generated_at": datetime.now().isoformat(),
        "market_data_source": MOCK_SOURCE,
        "portfolio_summary": {
            "total_capital": meta.get("total_capital", 0),
            "total_market_value": round(total_market_value, 2),
            "total_cost": round(total_cost, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl_pct, 2),
            "position_count": len(position_details),
        },
        "positions": position_details,
        "hedge_orders": hedge_orders,
        "hedge_execution": hedge_execution_result,
    }

    # 写入 reports/
    report_file = REPORTS_DIR / f"daily_report_{TODAY}.json"
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # 生成 Markdown 报告
    md_file = REPORTS_DIR / f"daily_report_{TODAY}.md"
    _generate_markdown_report(report, md_file)

    return report


def _generate_markdown_report(report: dict[str, Any], md_file: Path) -> None:
    """生成 Markdown 格式的盘后报告"""
    ps = report.get("portfolio_summary", {})
    ho = report.get("hedge_orders", {})
    positions = report.get("positions", [])

    lines: list[str] = [
        f"# 每日交易报告 {TODAY}",
        "",
        f"> 生成时间: {report.get('generated_at', '')}",
        f"> 数据来源: {report.get('market_data_source', '模拟数据')}",
        "",
        "## 一、组合概览",
        "",
        "| 指标 | 数值 |",
        "|---|---:|",
        f"| 总资金 | {ps.get('total_capital', 0):,.0f} 元 |",
        f"| 持仓市值 | {ps.get('total_market_value', 0):,.0f} 元 |",
        f"| 持仓成本 | {ps.get('total_cost', 0):,.0f} 元 |",
        f"| **总盈亏** | **{ps.get('total_pnl', 0):+,.0f} 元 ({ps.get('total_pnl_pct', 0):+.2f}%)** |",
        f"| 持仓数量 | {ps.get('position_count', 0)} 只 |",
        "",
        "## 二、对冲执行结果",
        "",
        "| 项目 | 结果 |",
        "|---|---|",
        f"| 对冲模式 | {ho.get('hedge_mode', 'N/A')} |",
        f"| 期货订单 | {ho.get('futures_orders_count', 0)} 笔 {'✅ 无期货' if ho.get('futures_orders_count', 0) == 0 else '⚠️'} |",
        f"| 期权订单 | {ho.get('options_orders_count', 0)} 组 |",
        f"| 期货保证金 | {ho.get('total_margin', 0):,.0f} 元 |",
        f"| 期权权利金 | {ho.get('total_premium', 0):,.0f} 元 |",
        "",
    ]

    # 期权订单明细
    options = ho.get("options_orders", [])
    if options:
        lines.extend(
            [
                "### 期权保护订单明细",
                "",
                "| 标的 | 张数 | 行权价 | 预算 |",
                "|---|---:|---|---:|",
            ]
        )
        for oo in options:
            lines.append(
                f"| {oo.get('instrument', '?')} | {oo.get('contracts', 0)} | "
                f"{oo.get('strike_rule', '?')} | {oo.get('premium_budget', 0):,} |"
            )
        lines.append("")

    # 持仓明细 TOP 10
    if positions:
        sorted_pos = sorted(positions, key=lambda x: abs(x.get("pnl", 0)), reverse=True)
        lines.extend(
            [
                "## 三、持仓明细 (按|盈亏|排序 TOP 10)",
                "",
                "| 代码 | 名称 | 持仓 | 成本 | 收盘 | 盈亏 | 盈亏% |",
                "|---|---|---:|---:|---:|---:|---:|",
            ]
        )
        for p in sorted_pos[:10]:
            lines.append(
                f"| {p['code']} | {p['name']} | {p['shares']} | {p['cost']:.3f} | "
                f"{p['close']:.3f} | {p['pnl']:+,.0f} | {p['pnl_pct']:+.2f}% |"
            )
        lines.append("")

    lines.append("---")
    lines.append("> 本报告由 daily_trading_workflow.py (模拟数据版) 自动生成")

    with open(md_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ============================================================
# 全流程执行
# ============================================================
def run_all() -> None:
    """全流程执行: 盘前 → 盘中 → 盘后"""

    run_premarket()
    run_intraday()
    run_postmarket()


# ============================================================
# 入口
# ============================================================
if __name__ == "__main__":
    run_all()
