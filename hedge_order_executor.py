"""
期权对冲订单执行器 (Hedge Order Executor)
==========================================
创建日期: 2026-08-06
创建原因: 顶级对冲基金审计 P0 — 期权对冲"只生成不执行"架构断链
    HedgeExecutionEngine.generate_hedge_orders() 生成 PENDING 订单,
    但系统从未有执行器把订单送入撮合引擎完成成交, 导致:
    - trade_plan 期权订单永远停留在 PENDING
    - 组合 Delta 从未因期权对冲下降
    - daily_pnl 读取不到 hedge_execution_fill_*.json, 对冲数据恒为空

本模块补齐"订单→撮合→成交→持仓/Delta 更新"完整闭环:

    流程:
        1. 读取当日 trade_plan (v8.3_institutional/trade_plans/trade_plan_{date}.json)
           提取 PENDING 状态的期权订单:
           - hedge_execution.options_orders        (BUY_PUT 认沽保护)
           - hedge_execution.covered_call_orders   (SELL_CALL_COVERED 备兑开仓)
           - futures_options_hedge.orders          (兼容字段, 含 PUT/CC)
           - hedge_execution.active_orders         (positions.json 里的 PENDING 订单)
        2. 通过内置期权模拟撮合器 (OptionsSimBroker) 撮合成交:
           - BUY_PUT:          支付权利金 (premium_total / premium_budget)
           - SELL_CALL_COVERED: 收取权利金 (est_total_premium)
           - 合约乘数 multiplier = 10000 (1张 = 10000份)
        3. 成交回报写入 hedge_execution_fill_{date}.json (data contract 兼容 daily_pnl)
           + hedge_execution_orders_{date}.json
        4. 更新 positions.json hedge_positions.active_orders → FILLED,
           并记录 actual_positions (实际成交的期权持仓)
        5. 计算并输出期权对冲后的组合 Delta/Beta 变化
        6. 调用 HedgeExecutionEngine.on_fill() 做 TCA 执行后归因

用法:
    # 撮合执行当日 PENDING 期权订单
    python hedge_order_executor.py --date 2026-08-06

    # 干跑模式 (仅生成成交计划, 不落盘不更新持仓)
    python hedge_order_executor.py --date 2026-08-06 --dry-run

    # 人工确认模式 (仅输出待确认订单, 不撮合)
    python hedge_order_executor.py --date 2026-08-06 --confirm-only

数据契约 (hedge_execution_fill_*.json, 兼容 daily_pnl):
    {
        "trade_date": "2026-08-06",
        "generated_at": "...",
        "portfolio_beta": 0.5126,        # 对冲前组合 Beta
        "beta_after_hedge": 0.4812,      # 对冲后组合 Beta (期权 Delta 影响)
        "total_hedge_pct": 0.0,          # 对冲比例
        "total_cost": 12345.0,           # 净成本 (Put支出 - Call收入)
        "hedge_enabled": true,
        "orders": [ { fill 明细 } ]
    }
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.path_config import setup_sys_path

setup_sys_path()

PROJECT_ROOT = Path(__file__).resolve().parent
REPORTS_DIR = PROJECT_ROOT / "reports"
CONFIG_DIR = PROJECT_ROOT / "config"
POSITIONS_FILE = CONFIG_DIR / "positions.json"
# 与 HedgeExecutionEngine 对齐: 生产 trade_plan 在 v8.3_institutional/trade_plans/
V83_ROOT = PROJECT_ROOT / "v8.3_institutional"
TRADE_PLANS_DIR = V83_ROOT / "trade_plans"
V83_REPORTS_DIR = V83_ROOT / "reports"
ARCHIVE_DIR = PROJECT_ROOT / "每日报告归档"

logger = logging.getLogger("hedge_order_executor")

# 期权合约乘数: 上交所/深交所 ETF 期权 1张 = 10000份
OPTION_MULTIPLIER = 10000

# 期权 Delta 估算 (OTM 程度近似):
#  - 认沽 OTM 5% 的 Delta ≈ -0.25 (每张覆盖 10000 份标的)
#  - 备兑卖出 Call OTM 5-8% 的 Delta ≈ -0.20 (卖出虚值Call的 Delta 为负)
# 用于计算期权对冲对组合 Beta / Delta 的影响
PUT_OTM5_DELTA = -0.25
COVERED_CALL_OTM_DELTA = -0.20


class OptionsSimBroker:
    """内置期权模拟撮合器.

    与 v8.3_institutional/.../SimulatedBroker 语义一致, 但针对期权
    (以 premium 权利金定价, multiplier=10000). 支持:
        - BUY_PUT:            买入认沽, 支付权利金
        - SELL_CALL_COVERED:  卖出备兑认购, 收取权利金
    返回成交回报 (fill), 模拟 ±0.05% 权利金滑点.
    """

    MOCK_SLIPPAGE = 0.0005  # 权利金滑点 ±0.05%

    def __init__(self) -> None:
        self.filled_orders: List[Dict[str, Any]] = []
        self._orders: Dict[str, Dict[str, Any]] = {}

    def place(self, order: Dict[str, Any]) -> str:
        """登记订单, 返回 order_id."""
        oid = f"HEDGE-{order.get('order_id', uuid.uuid4().hex[:8])}"
        self._orders[oid] = dict(order)
        return oid

    def wait_fill(self, oid: str) -> Optional[Dict[str, Any]]:
        """撮合成交, 返回 fill."""
        order = self._orders.get(oid)
        if order is None:
            return None

        direction = order.get("direction", order.get("type", ""))
        is_buy = direction.upper() in ("BUY_PUT", "BUY", "BUY_OPEN")

        # 权利金定价
        if is_buy:
            base_premium = float(order.get("premium_total")
                                 or order.get("premium_budget")
                                 or order.get("est_total_premium") or 0)
            premium = base_premium * (1.0 + self.MOCK_SLIPPAGE)
        else:
            # 优先取显式总权利金; 缺失时回退按 每张权利金 * 张数 计算, 避免 est_premium_per_contract 默认 0 造成的死路径
            est_total = order.get("est_total_premium")
            if est_total not in (None, 0, ""):
                base_premium = float(est_total)
            else:
                per_contract = float(order.get("est_premium_per_contract", 0) or 0)
                contracts = int(order.get("contracts", 0) or 0)
                base_premium = per_contract * contracts
                if base_premium == 0:
                    import logging as _logging
                    _logging.getLogger(__name__).warning(
                        "备兑Call权利金缺失 est_total_premium 且 est_premium_per_contract*contracts=0, "
                        "订单 %s 权利金记为 0", order.get("order_id", "?"))
            premium = base_premium * (1.0 - self.MOCK_SLIPPAGE)

        multiplier = float(order.get("multiplier") or OPTION_MULTIPLIER)
        contracts = int(order.get("contracts", 0))

        fill = {
            "order_id": order.get("order_id", ""),
            "type": order.get("type", "OPTIONS"),
            "instrument": order.get("instrument", ""),
            "underlying": order.get("underlying", order.get("underlying_code", "")),
            "direction": order.get("direction", ""),
            "exchange": order.get("exchange", ""),
            "contracts": contracts,
            "strike": order.get("strike", order.get("strike_rule", "")),
            "strike_rule": order.get("strike_rule", ""),
            "premium_total": round(premium, 2),
            "premium_per_unit": round(premium / multiplier / contracts, 6) if contracts > 0 else 0.0,
            "multiplier": multiplier,
            "slippage": self.MOCK_SLIPPAGE,
            "fill_time": datetime.now().isoformat(),
            "status": "FILLED",
            "delta": order.get("delta_estimate", 0.0),
            "beta_reduction": order.get("beta_reduction", 0.0),
        }
        self.filled_orders.append(fill)
        return fill


def _load_json(path: Path) -> dict:
    """安全加载 JSON 文件."""
    try:
        if path.exists():
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    except Exception as exc:  # noqa: BLE001
        logger.error("读取 %s 失败: %s", path, exc)
    return {}


def _atomic_write_json(path: Path, data: dict) -> None:
    """原子写入 JSON (先写临时文件再替换), 遵循不可变性."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _resolve_date(trade_date: Optional[str]) -> str:
    """解析目标日期. 默认当前日期."""
    if trade_date:
        return trade_date
    return datetime.now().strftime("%Y-%m-%d")


def _collect_pending_orders(plan: dict) -> List[Dict[str, Any]]:
    """从 trade_plan 提取所有 PENDING 状态的期权订单 (去重).

    来源 (按优先级):
        1. plan["hedge_execution"]["options_orders"]         — BUY_PUT
        2. plan["hedge_execution"]["covered_call_orders"]    — SELL_CALL_COVERED
        3. plan["futures_options_hedge"]["orders"]           — 兼容汇总字段
        4. plan["hedge_execution"]["active_orders"]          — positions.json 回流
    """
    seen_ids: set = set()
    orders: List[Dict[str, Any]] = []

    def _add(order: Dict[str, Any]) -> None:
        if not isinstance(order, dict):
            return
        status = str(order.get("status", "PENDING")).upper()
        if status != "PENDING":
            return
        # 仅处理期权订单
        order_type = str(order.get("type", "")).upper()
        direction = str(order.get("direction", "")).upper()
        instrument = str(order.get("instrument", "")).lower()
        is_option = (
            "OPTION" in order_type
            or "PUT" in direction
            or "CALL" in direction
            or "put" in instrument
            or "call" in instrument
        )
        if not is_option:
            return
        oid = order.get("order_id") or f"{order.get('instrument')}-{order.get('contracts')}-{order.get('strike_rule')}"
        if oid in seen_ids:
            return
        seen_ids.add(oid)
        orders.append(order)

    he = plan.get("hedge_execution", {})
    if isinstance(he, dict):
        for o in he.get("options_orders", []) or []:
            _add(o)
        for o in he.get("covered_call_orders", []) or []:
            _add(o)
        # active_orders 真实落盘为嵌套字典: {date, status, put_protection:[...], covered_call:[...]}
        # 必须与 _update_positions_state 的写入结构对齐, 绝不能当扁平列表迭代 (否则对键字符串调 .get 崩)
        active_orders = he.get("active_orders")
        if isinstance(active_orders, dict):
            for sub_key in ("put_protection", "covered_call"):
                for o in active_orders.get(sub_key, []) or []:
                    _add(o)
        elif isinstance(active_orders, list):
            # 兼容旧版扁平列表契约
            for o in active_orders:
                _add(o)

    foh = plan.get("futures_options_hedge", {})
    if isinstance(foh, dict):
        for o in foh.get("orders", []) or []:
            _add(o)

    return orders


def _compute_option_delta(order: Dict[str, Any]) -> float:
    """估算单张期权 Delta.

    BUY_PUT:        OTM 5% Put Delta ≈ -0.25 (负 Delta, 对冲组合多头)
    SELL_CALL:      卖出虚值 Call Delta ≈ -0.20
    返回"每张"的 Delta (标的价格单位).
    """
    direction = str(order.get("direction", "")).upper()
    if "PUT" in direction:
        return PUT_OTM5_DELTA
    if "CALL" in direction:
        return COVERED_CALL_OTM_DELTA
    return 0.0


def _extract_underlying_code(order: Dict[str, Any]) -> str:
    """提取期权标的基础代码 (如 '510050 Put' → '510050', '510300.SH' → '510300').

    优先级:
        1. order.underlying / order.underlying_code / order.code (可能带 .SH/.SZ 后缀)
        2. order.instrument 前缀 (如 '510050 Put' → '510050')
    """
    code = order.get("underlying") or order.get("underlying_code") or order.get("code") or ""
    if code:
        # 去掉交易所后缀: 510300.SH → 510300
        return code.split(".")[0]
    instrument = str(order.get("instrument", ""))
    # 从 instrument 提取纯数字代码 (如 '510050 Put' → '510050', '159915 Put' → '159915')
    import re as _re
    m = _re.match(r"\s*(\d{6})", instrument)
    if m:
        return m.group(1)
    return ""


def _load_underlying_price(positions_data: dict, order: Dict[str, Any]) -> float:
    """从持仓中读取期权标的的当前价格."""
    base_code = _extract_underlying_code(order)
    if not base_code:
        return 0.0
    positions = positions_data.get("positions", {})
    # 匹配持仓 (支持带后缀的完整代码)
    for code, pos in positions.items():
        if isinstance(pos, dict) and code.split(".")[0] == base_code:
            return float(pos.get("est_price", 0) or 0)
    return 0.0


def execute_hedge_orders(
    trade_date: str,
    dry_run: bool = False,
    confirm_only: bool = False,
) -> Dict[str, Any]:
    """执行当日 PENDING 期权对冲订单.

    Args:
        trade_date: 目标交易日 YYYY-MM-DD
        dry_run: 仅生成成交计划, 不落盘不更新持仓
        confirm_only: 仅输出待确认订单, 不撮合

    Returns:
        执行结果 dict (含 fills / 汇总 / 状态)
    """
    date_compact = trade_date.replace("-", "")

    # 1. 读取 trade_plan (v8.3 路径优先, 兼容项目根)
    plan = {}
    for cand in (TRADE_PLANS_DIR / f"trade_plan_{date_compact}.json",
                 PROJECT_ROOT / f"trade_plan_{date_compact}.json"):
        if cand.exists():
            plan = _load_json(cand)
            if plan:
                break

    if not plan:
        logger.warning("未找到 trade_plan: %s, 无订单可执行", trade_date)
        return {"trade_date": trade_date, "hedge_enabled": False, "orders": [], "reason": "no_trade_plan"}

    # 读取持仓配置 (含 hedge_positions)
    positions_data = _load_json(POSITIONS_FILE)
    hedge_positions = positions_data.get("hedge_positions", {}) or {}

    # 2. 收集 PENDING 期权订单
    pending_orders = _collect_pending_orders(plan)
    logger.info("收集到 PENDING 期权订单: %d 笔", len(pending_orders))

    if confirm_only:
        return {
            "trade_date": trade_date,
            "hedge_enabled": False,
            "orders": pending_orders,
            "status": "CONFIRM_ONLY",
            "pending_count": len(pending_orders),
            "message": "仅输出待确认订单, 未撮合. 请核对后去掉 --confirm-only 执行.",
        }

    # 3. 计算对冲前组合 Beta
    from utils.hedge_execution_engine import HedgeExecutionEngine
    engine = HedgeExecutionEngine()
    portfolio_beta = engine.calc_portfolio_beta()
    portfolio_value = engine.calc_portfolio_market_value()
    logger.info("对冲前组合 Beta=%.4f, 市值=¥%.0f", portfolio_beta, portfolio_value)

    # 4. 撮合执行
    broker = OptionsSimBroker()
    fills: List[Dict[str, Any]] = []
    beta_reduction_total = 0.0
    total_put_cost = 0.0
    total_call_income = 0.0

    for order in pending_orders:
        contracts = int(order.get("contracts", 0))
        if contracts <= 0:
            continue
        direction = str(order.get("direction", "")).upper()

        # 计算标的市值与 Delta 影响
        underlying_price = _load_underlying_price(positions_data, order)
        multiplier = float(order.get("multiplier") or OPTION_MULTIPLIER)
        notional_per_contract = underlying_price * multiplier if underlying_price > 0 else 0

        # Delta 影响 (张数 × 单张Delta × 标的市值折算到组合Beta)
        delta_per_contract = _compute_option_delta(order)
        order_delta = delta_per_contract * contracts
        # Beta 影响 ≈ (Delta 名义覆盖 / 组合市值) × 标的Beta(用1近似)
        order_notional = notional_per_contract * contracts if notional_per_contract > 0 else 0
        beta_impact = (order_notional / portfolio_value * 1.0 * abs(delta_per_contract)
                       if portfolio_value > 0 and delta_per_contract != 0 else 0.0)
        # BUY_PUT 降低组合Beta; 卖出Call也降低(备兑) — 均为负Delta贡献
        beta_impact = -abs(beta_impact) if delta_per_contract < 0 else abs(beta_impact)

        # 补全订单字段
        order.setdefault("multiplier", multiplier)
        order.setdefault("delta_estimate", round(delta_per_contract, 4))
        order.setdefault("beta_reduction", round(beta_impact, 4))
        order.setdefault("underlying", _extract_underlying_code(order))

        # 撮合
        oid = broker.place(order)
        fill = broker.wait_fill(oid)
        if fill is None:
            fills.append({**order, "status": "FAILED", "reason": "无成交回报"})
            continue

        if "PUT" in direction:
            total_put_cost += float(fill["premium_total"])
        else:
            total_call_income += float(fill["premium_total"])
        beta_reduction_total += beta_impact

        # 调用 TCA 归因 (仅非 dry-run)
        if not dry_run:
            try:
                engine.on_fill({
                    "symbol": fill.get("instrument", ""),
                    "side": "BUY" if "PUT" in direction else "SELL",
                    "shares": contracts,
                    "price": fill["premium_total"],
                    "timestamp": fill["fill_time"],
                    "order_id": fill["order_id"],
                    "broker": "OptionsSimBroker",
                    "venue": fill.get("exchange", ""),
                })
            except Exception as exc:  # noqa: BLE001
                logger.warning("TCA 归因失败 (fail-safe): %s", exc)

        fills.append(fill)

    beta_after = max(portfolio_beta + beta_reduction_total, 0.0)
    total_cost = total_put_cost - total_call_income
    hedge_enabled = len(fills) > 0

    result: Dict[str, Any] = {
        "trade_date": trade_date,
        "generated_at": datetime.now().isoformat(),
        "portfolio_beta": round(portfolio_beta, 4),
        "beta_after_hedge": round(beta_after, 4),
        "total_hedge_pct": round(total_put_cost / portfolio_value, 4) if portfolio_value > 0 else 0.0,
        "total_cost": round(total_cost, 2),
        "put_cost": round(total_put_cost, 2),
        "call_income": round(total_call_income, 2),
        "beta_reduction": round(beta_reduction_total, 4),
        "hedge_enabled": hedge_enabled,
        "orders": fills,
        "filled_count": len(fills),
        "status": "DRY_RUN" if dry_run else "FILLED",
    }

    if dry_run:
        logger.info("[DRY-RUN] 期权撮合完成: %d 笔成交, 净成本 ¥%.2f, 未落盘未更新持仓",
                    len(fills), total_cost)
        return result

    # 5. 落盘 hedge_execution_fill_{date}.json (data contract)
    fill_payload = {
        "trade_date": trade_date,
        "generated_at": result["generated_at"],
        "portfolio_beta": result["portfolio_beta"],
        "beta_after_hedge": result["beta_after_hedge"],
        "total_hedge_pct": result["total_hedge_pct"],
        "total_cost": result["total_cost"],
        "hedge_enabled": hedge_enabled,
        "orders": fills,
    }
    fill_path = REPORTS_DIR / f"hedge_execution_fill_{trade_date}.json"
    _atomic_write_json(fill_path, fill_payload)
    logger.info("对冲成交记录已落盘: %s", fill_path)

    # 同步归档到 v8.3 路径 (daily_pnl 也扫描此处)
    v83_fill_path = V83_REPORTS_DIR / f"hedge_execution_fill_{trade_date}.json"
    _atomic_write_json(v83_fill_path, fill_payload)

    # 归档到每日报告归档
    archive_path = ARCHIVE_DIR / trade_date / f"对冲执行单_{date_compact}.json"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(archive_path, {
        "date": trade_date,
        "action": "HEDGE_EXECUTE",
        "portfolio_beta": result["portfolio_beta"],
        "beta_after_hedge": result["beta_after_hedge"],
        "orders": fills,
    })

    # 6. 更新 positions.json hedge_positions 状态 (不可变)
    _update_positions_state(positions_data, fills, trade_date)

    return result


def _update_positions_state(positions_data: dict, fills: List[Dict[str, Any]], trade_date: str) -> None:
    """更新 positions.json hedge_positions:
      - active_orders 中已成交订单 status → FILLED
      - 记录 actual_positions (实际期权持仓)

    遵循不可变性: 新建 dict, 不原地修改.
    """
    if not fills:
        return

    new_data = json.loads(json.dumps(positions_data))  # 深拷贝
    hedge_positions = new_data.setdefault("hedge_positions", {})
    active_orders = hedge_positions.setdefault("active_orders", {})

    if isinstance(active_orders, dict):
        for key in ("put_protection", "covered_call"):
            orders = active_orders.get(key, [])
            if not isinstance(orders, list):
                continue
            for o in orders:
                if not isinstance(o, dict):
                    continue
                oid = o.get("order_id")
                inst = str(o.get("instrument", "")).lower()
                direction = str(o.get("direction", "")).upper()
                for fill in fills:
                    fill_inst = str(fill.get("instrument", "")).lower()
                    fill_dir = str(fill.get("direction", "")).upper()
                    # 精确匹配: order_id 相同, 或 instrument 完全一致(含Call/Put区分),
                    # 且方向一致 (BUY_PUT<->BUY_PUT, SELL_CALL<->SELL_CALL)
                    same_inst = (fill_inst == inst and inst != "")
                    same_dir = (direction == fill_dir)
                    if (oid and fill.get("order_id") == oid) or (same_inst and same_dir):
                        o["status"] = "FILLED"
                        o["fill_time"] = fill.get("fill_time", "")
                        o["premium_total"] = fill.get("premium_total", 0)
                        o["delta"] = fill.get("delta", 0)
                        o["beta_reduction"] = fill.get("beta_reduction", 0)
                        break

    # 记录实际成交期权持仓
    actual = []
    for fill in fills:
        if fill.get("status") != "FILLED":
            continue
        actual.append({
            "instrument": fill.get("instrument", ""),
            "underlying": fill.get("underlying", ""),
            "direction": fill.get("direction", ""),
            "contracts": fill.get("contracts", 0),
            "strike": fill.get("strike", fill.get("strike_rule", "")),
            "premium_total": fill.get("premium_total", 0),
            "delta": fill.get("delta", 0),
            "beta_reduction": fill.get("beta_reduction", 0),
            "fill_time": fill.get("fill_time", ""),
            "expiry": "每月首个交易日次月到期",
        })
    if actual:
        hedge_positions["actual_positions"] = actual
        hedge_positions["actual_positions_date"] = trade_date

    hedge_positions["last_hedge_execution"] = {
        "date": trade_date,
        "filled_count": len(fills),
        "executed_at": datetime.now().isoformat(),
    }

    # 原子写回
    _atomic_write_json(POSITIONS_FILE, new_data)
    logger.info("positions.json hedge_positions 已更新: %d 笔成交, actual_positions=%d",
                len(fills), len(actual))


def print_result(result: Dict[str, Any]) -> None:
    """打印执行结果."""
    print("=" * 70)
    print("期权对冲订单执行结果")
    print("=" * 70)
    print(f"日期: {result.get('trade_date', '')}")
    print(f"状态: {result.get('status', '')}")
    print(f"对冲前组合 Beta: {result.get('portfolio_beta', 0):.4f}")
    print(f"对冲后组合 Beta: {result.get('beta_after_hedge', 0):.4f}")
    print(f"Beta 降低: {result.get('beta_reduction', 0):.4f}")
    print(f"Put 支出: RMB {result.get('put_cost', 0):,.2f}")
    print(f"Call 收入: RMB {result.get('call_income', 0):,.2f}")
    print(f"净成本: RMB {result.get('total_cost', 0):,.2f}")
    print(f"成交笔数: {result.get('filled_count', 0)}")

    orders = result.get("orders", [])
    if orders:
        print("-" * 70)
        for o in orders:
            print(f"  [{o.get('status','?')}] {o.get('direction','?'):<20} "
                  f"{o.get('instrument','?'):<12} {o.get('contracts',0)}张 "
                  f"权利金RMB{o.get('premium_total',0):,.0f} "
                  f"strike={o.get('strike_rule', o.get('strike',''))}")
    print("=" * 70)


def main() -> None:
    parser = argparse.ArgumentParser(description="期权对冲订单执行器")
    parser.add_argument("--date", default=None, help="目标交易日 YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="干跑, 不落盘不更新持仓")
    parser.add_argument("--confirm-only", action="store_true", help="仅输出待确认订单, 不撮合")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    trade_date = _resolve_date(args.date)
    result = execute_hedge_orders(
        trade_date=trade_date,
        dry_run=args.dry_run,
        confirm_only=args.confirm_only,
    )
    print_result(result)


if __name__ == "__main__":
    main()
