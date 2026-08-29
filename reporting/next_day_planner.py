"""次日交易计划 (B3.2 拆分自 PortfolioAnalyzer)

提供以下纯函数:
  - generate_next_day_plan: 生成第二天交易计划 (基于 auto_trade_plan_500w_2026-2030.json 的4阶段)
"""

import json
from datetime import datetime, timedelta
from pathlib import Path as _Path
from typing import Any, Optional

# 阶段中文名映射
_PHASE_NAMES: dict[str, str] = {
    "phase_1_accumulation": "建仓期",
    "phase_2_holding": "持有期",
    "phase_3_reduction": "减仓期",
    "phase_4_clearance": "清仓期",
}

# 阶段键列表
_PHASE_KEYS: tuple[str, ...] = (
    "phase_1_accumulation",
    "phase_2_holding",
    "phase_3_reduction",
    "phase_4_clearance",
)


def _compute_next_trading_day(
    report_date: Optional[str],
) -> tuple[str, Optional[datetime], str, Optional[str]]:
    """计算下一交易日及其星期信息。

    Args:
        report_date: 报告日期 'YYYY-MM-DD', None 表示今天

    Returns:
        (next_day, next_dt, weekday_cn, error) 元组, error 非 None 表示失败
    """
    try:
        from utils.trade_calendar import is_trading_day, next_trading_day  # noqa: F401
    except ImportError as e:
        return "", None, "", f"trade_calendar import failed: {e}"

    if report_date is None:
        report_date = datetime.now().strftime("%Y-%m-%d")
    next_day = next_trading_day(report_date)
    try:
        next_dt = datetime.strptime(next_day, "%Y-%m-%d")
        weekday_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][
            next_dt.weekday()
        ]
    except Exception:
        next_dt = None
        weekday_cn = ""
    return next_day, next_dt, weekday_cn, None


def _load_trade_plan(
    project_root: Optional[_Path],
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """加载 auto_trade_plan_500w_2026-2030.json。

    Args:
        project_root: 项目根目录

    Returns:
        (plan, error) 元组, error 非 None 表示失败
    """
    if project_root is None:
        project_root = _Path(__file__).resolve().parent.parent
    plan_file = (
        project_root
        / "v8.3_institutional"
        / "trade_plans"
        / "auto_trade_plan_500w_2026-2030.json"
    )
    if not plan_file.exists():
        return None, f"plan file not found: {plan_file}"

    try:
        with open(plan_file, encoding="utf-8") as f:
            return json.load(f), None
    except Exception as e:
        return None, f"plan load failed: {e}"


def _determine_phase(
    exec_plan: dict[str, Any], next_day: str, next_dt: Optional[datetime]
) -> tuple[str, dict[str, Any]]:
    """判断次日所属阶段并计算 day_index。

    Args:
        exec_plan: execution_plan 字典
        next_day: 下一交易日字符串
        next_dt: 下一交易日 datetime

    Returns:
        (phase_key, phase_info) 元组
    """
    phase_info: dict[str, Any] = {}
    phase_key = "phase_1_accumulation"

    for key in _PHASE_KEYS:
        phase = exec_plan.get(key, {})
        period = phase.get("period", "")
        parts = period.split(" to ")
        if len(parts) == 2 and parts[0].strip() <= next_day <= parts[1].strip():
            phase_info = phase
            phase_key = key
            break
    else:
        phase_info = exec_plan.get("phase_1_accumulation", {})

    # 计算 day_index (在当前阶段中的第几个交易日, 跳过周末)
    period = phase_info.get("period", "")
    parts = period.split(" to ")
    day_index = 1
    if len(parts) == 2 and next_dt:
        try:
            start_dt = datetime.strptime(parts[0].strip(), "%Y-%m-%d")
            cur = start_dt
            while cur < next_dt:
                if cur.weekday() < 5:
                    day_index += 1
                cur += timedelta(days=1)
        except Exception:
            pass

    # 把 day_index 附加到 phase_info 上以便后续使用
    phase_info_with_idx = dict(phase_info)
    phase_info_with_idx["day_index"] = day_index
    return phase_key, phase_info_with_idx


def _generate_phase_actions(
    phase_key: str,
    phase_info: dict[str, Any],
    next_day: str,
) -> tuple[list[str], float]:
    """根据阶段生成次日交易动作列表和当日资金。

    Args:
        phase_key: 阶段键
        phase_info: 阶段信息
        next_day: 下一交易日

    Returns:
        (daily_actions, daily_capital) 元组, daily_capital 正为买入, 负为卖出
    """
    daily_actions: list[str] = []
    daily_capital = 0.0

    if phase_key == "phase_1_accumulation":
        monthly_inv = phase_info.get("monthly_investment", 500000)
        daily_capital = monthly_inv / 20  # 简化: 每月20交易日
        daily_actions.append(
            f"建仓期: 月投 {monthly_inv:,.0f} 元, 当日预算约 {daily_capital:,.0f} 元"
        )
        daily_actions.append(
            "执行策略: VWAP+TWAP混合算法, 单日最大买入不超过月度计划的50%"
        )
        daily_actions.append("ETF资金流触发: 强信号+3%加仓, 中信号+1%加仓, 反转-3%减仓")
        daily_actions.append("建仓期止损线: -18%, 止盈线: +40%")
    elif phase_key == "phase_2_holding":
        daily_actions.append("持有期: 季度再平衡, 偏差>5%触发")
        daily_actions.append("ETF资金流连续3日反转触发减仓")
        daily_actions.append("单标的权重上限15%/下限2%; 止盈+50%部分止盈, 止损-15%")
        daily_actions.append("组合整体回撤>12%触发对冲加仓")
    elif phase_key == "phase_3_reduction":
        monthly_red = phase_info.get("monthly_reduction", 250000)
        daily_capital = -monthly_red / 20
        daily_actions.append(
            f"减仓期: 月减 {monthly_red:,.0f} 元, 当日减仓约 {abs(daily_capital):,.0f} 元"
        )
        daily_actions.append(
            "优先减仓: 估值分位>80%标的 → 次优先: 科技板块 → 最后: 防御+黄金"
        )
        daily_actions.append("减仓期间停止新建仓")
    elif phase_key == "phase_4_clearance":
        try:
            nd = datetime.strptime(next_day[:10], "%Y-%m-%d")
        except Exception:
            nd = datetime(2030, 7, 1)

        if nd >= datetime(2030, 12, 1):
            daily_actions.append("清仓期: 12月清仓对冲仓位, 12月31日100%现金")
        elif nd >= datetime(2030, 11, 1):
            daily_actions.append("清仓期: 11月清仓全部ETF")
        elif nd >= datetime(2030, 10, 1):
            daily_actions.append("清仓期: 10月清仓全部股票")
        else:
            daily_actions.append("清仓期: 7-9月清仓全部股票")
        daily_actions.append("清仓期不再触发任何加仓信号")
        daily_actions.append("2030-12-15起强制平仓全部对冲仓位")

    return daily_actions, daily_capital


def _compute_position_action(
    phase_key: str,
    ptype: str,
    weight: float,
    daily_capital: float,
    next_day: str,
) -> tuple[str, float]:
    """计算单个标的的当日动作和金额。

    Args:
        phase_key: 阶段键
        ptype: 标的类型 (STOCK/ETF)
        weight: 权重
        daily_capital: 当日资金 (正买入, 负卖出)
        next_day: 下一交易日

    Returns:
        (action, daily_amount) 元组
    """
    if phase_key == "phase_1_accumulation":
        return "BUY", daily_capital * weight / 0.90 if daily_capital else 0.0
    if phase_key == "phase_2_holding":
        return "HOLD", 0.0
    if phase_key == "phase_3_reduction":
        return "SELL", -daily_capital * weight / 0.90 if daily_capital else 0.0
    if phase_key == "phase_4_clearance":
        if ptype == "STOCK" and next_day >= "2030-10-01":
            return "SELL_ALL", 0.0
        if ptype == "ETF" and next_day >= "2030-11-01":
            return "SELL_ALL", 0.0
        return "HOLD", 0.0
    return "HOLD", 0.0


def _build_target_positions_detail(
    stock_account: dict[str, Any],
    phase_key: str,
    daily_capital: float,
    next_day: str,
) -> list[dict[str, Any]]:
    """构建次日标的明细列表。

    Args:
        stock_account: 股票账户配置
        phase_key: 阶段键
        daily_capital: 当日资金
        next_day: 下一交易日

    Returns:
        标的明细列表
    """
    target_positions_detail: list[dict[str, Any]] = []
    plan_positions = stock_account.get("positions", [])
    if not plan_positions:
        return target_positions_detail

    for pos in plan_positions:
        code = pos.get("code", "")
        name = pos.get("name", "")
        ptype = pos.get("type", "")
        weight = pos.get("weight", 0)
        amount = pos.get("amount", 0)
        style = pos.get("style", pos.get("sector", ""))
        etf_sig = pos.get("etf_flow_signal", "")
        etf_inflow = pos.get("etf_inflow")
        reason = pos.get("reason", "")

        action, daily_amount = _compute_position_action(
            phase_key, ptype, weight, daily_capital, next_day
        )

        target_positions_detail.append(
            {
                "code": code,
                "name": name,
                "type": ptype,
                "weight": weight,
                "amount": amount,
                "daily_amount": round(daily_amount, 2),
                "action": action,
                "style": style,
                "etf_flow_signal": etf_sig,
                "etf_inflow": etf_inflow if etf_inflow is not None else "",
                "reason": reason,
            }
        )
    return target_positions_detail


def _build_hedge_positions_detail(
    project_root: _Path,
) -> list[dict[str, Any]]:
    """从 positions.json 读取 hedge_positions 明细。

    Args:
        project_root: 项目根目录

    Returns:
        对冲持仓明细列表
    """
    hedge_positions_detail: list[dict[str, Any]] = []
    try:
        positions_file = project_root / "config" / "positions.json"
        if not positions_file.exists():
            return hedge_positions_detail

        with open(positions_file, encoding="utf-8") as f:
            pos_data = json.load(f)
        hedge_pos = pos_data.get("hedge_positions", {})
        for key, hp in hedge_pos.items():
            instrument = hp.get("instrument", key)
            contracts = hp.get("target_contracts", 0)
            multiplier = hp.get("multiplier", 0)
            premium_budget = hp.get("premium_budget", 0)

            # 计算名义价值
            notional = 0
            if multiplier and contracts:
                if "put" in key.lower() or "option" in key.lower():
                    notional = premium_budget
                else:
                    notional = multiplier * contracts * 4000  # 假设指数4000点

            hedge_positions_detail.append(
                {
                    "key": key,
                    "instrument": instrument,
                    "exchange": hp.get("exchange", ""),
                    "direction": hp.get("direction", ""),
                    "target_contracts": contracts,
                    "multiplier": multiplier,
                    "margin_rate": hp.get("margin_rate", 0),
                    "target_beta_reduction": hp.get("target_beta_reduction", 0),
                    "strike": hp.get("strike", ""),
                    "premium_budget": premium_budget,
                    "estimated_notional": notional,
                    "reason": hp.get("reason", ""),
                }
            )
    except Exception:
        pass
    return hedge_positions_detail


def _build_hedge_action(
    hedge_account: dict[str, Any], project_root: _Path
) -> dict[str, Any]:
    """构建对冲账户策略。

    Args:
        hedge_account: 对冲账户配置
        project_root: 项目根目录

    Returns:
        hedge_action 字典
    """
    hedge_strategy = hedge_account.get("hedge_strategy", {})
    hedge_instruments = [
        {
            "instrument": inst.get("instrument", ""),
            "direction": inst.get("direction", ""),
            "target_contracts": inst.get("target_contracts", 0),
            "reason": inst.get("reason", ""),
        }
        for inst in hedge_account.get("target_instruments", [])
    ]
    hedge_positions_detail = _build_hedge_positions_detail(project_root)

    return {
        "mode": hedge_strategy.get("mode", "dynamic"),
        "trigger_threshold": hedge_strategy.get("trigger_threshold", 0.05),
        "rebalance_frequency": hedge_strategy.get("rebalance_frequency", "每周五"),
        "instruments": hedge_instruments,
        "hedge_positions_detail": hedge_positions_detail,
    }


def generate_next_day_plan(
    report_date: Optional[str] = None,
    project_root: Optional[_Path] = None,
) -> dict[str, Any]:
    """生成第二天交易计划 (基于 auto_trade_plan_500w_2026-2030.json 的4阶段)。

    根据 auto_trade_plan_500w_2026-2030.json 的 4 阶段执行计划,
    推算次日所属阶段、当日预算、目标动作、对冲策略、风控指令。

    Args:
        report_date: 报告日期 'YYYY-MM-DD', None 表示今天
        project_root: 项目根目录 Path, None 表示使用本文件所在目录的父目录

    Returns:
        dict, 含 next_trading_day, phase, daily_actions, hedge_action, risk_controls 等
    """
    # 1. 计算下一交易日
    next_day, next_dt, weekday_cn, error = _compute_next_trading_day(report_date)
    if error:
        return {"error": error}

    # 2. 加载交易计划
    if project_root is None:
        project_root = _Path(__file__).resolve().parent.parent
    plan, error = _load_trade_plan(project_root)
    if error:
        return {
            "next_trading_day": next_day,
            "weekday": weekday_cn,
            "error": error,
        }
    assert plan is not None  # 此时 plan 必非 None

    exec_plan = plan.get("execution_plan", {})
    stock_account = plan.get("stock_etf_account", {})
    hedge_account = plan.get("hedge_account", {})
    risk_mgmt = plan.get("risk_management", {})

    # 3. 判断阶段
    phase_key, phase_info = _determine_phase(exec_plan, next_day, next_dt)
    day_index = phase_info.pop("day_index", 1)
    phase_name_cn = _PHASE_NAMES.get(phase_key, "建仓期")

    # 4. 生成阶段动作和当日资金
    daily_actions, daily_capital = _generate_phase_actions(
        phase_key, phase_info, next_day
    )

    # 5. 构建标的明细
    target_positions_detail = _build_target_positions_detail(
        stock_account, phase_key, daily_capital, next_day
    )

    # 6. 对冲策略
    hedge_action = _build_hedge_action(hedge_account, project_root)

    # 7. 风控配置
    stop_loss_rules = risk_mgmt.get("stop_loss_rules", {})
    take_profit_rules = risk_mgmt.get("take_profit_rules", {})
    position_limits = risk_mgmt.get("position_limits", {})

    from utils.trade_calendar import is_trading_day

    return {
        "next_trading_day": next_day,
        "weekday": weekday_cn,
        "is_trading_day": is_trading_day(next_day),
        "phase": {
            "key": phase_key,
            "name_cn": phase_name_cn,
            "period": phase_info.get("period", ""),
            "day_index": day_index,
            "description": phase_info.get("description", ""),
            "strategy": phase_info.get("strategy", ""),
        },
        "total_capital": plan.get("meta", {}).get("total_capital", 5000000),
        "stock_etf_capital": stock_account.get(
            "capital", plan.get("meta", {}).get("stock_etf_capital", 3000000)
        ),
        "stock_etf_account": {
            "capital": stock_account.get(
                "capital", plan.get("meta", {}).get("stock_etf_capital", 3000000)
            ),
            "target_positions": stock_account.get("target_positions", 20),
            "daily_capital": round(daily_capital, 2),
            "daily_actions": daily_actions,
            "target_positions_detail": target_positions_detail,
        },
        "hedge_account": hedge_action,
        "etf_flow_monitoring": risk_mgmt.get("etf_flow_monitoring", {}),
        "risk_controls": {
            "stop_loss_single": stop_loss_rules.get("single_position", -0.15),
            "stop_loss_portfolio": stop_loss_rules.get("portfolio", -0.12),
            "take_profit_single": take_profit_rules.get("single_position", 0.50),
            "take_profit_portfolio": take_profit_rules.get("portfolio", 0.30),
            "max_single_position": position_limits.get("max_single_position", 0.10),
            "max_sector_exposure": position_limits.get("max_sector_exposure", 0.30),
        },
        "expected_performance": plan.get("expected_performance", {}),
    }
