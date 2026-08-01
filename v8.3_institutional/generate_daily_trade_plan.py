# -*- coding: utf-8 -*-
"""
动态生成 trade_plan_{YYYYMMDD}.json — 基于 500万建仓计划 + 23 标的新权重 (v8.4 OPTIONS_ONLY)
v8.4 增强: 纯期权对冲模式 (OTC Put全覆盖 + Covered Call增收 + Put Spread阶梯)

用法:
    py -3 generate_daily_trade_plan.py [YYYY-MM-DD] [--capital 5000000]

默认:
    - 日期 = 下一个交易日 (跳过周末)
    - 资金 = 5,000,000 (资金比例从 portfolio.yaml 读取, 默认 400万现货 + 100万期权对冲)

输出:
    trade_plans/trade_plan_{YYYYMMDD}.json

阶段逻辑 (集中建仓):
    7/13 ~ 8/21: 每个交易日 15 万现货, 共 30 个交易日, 总 300 万
    对冲资金: 100 万 (纯期权对冲: 82.5万权利金 + 17.5万滚仓缓冲, 与 portfolio.yaml 对齐)
    不持有IF期货空头, Beta对冲全部通过ETF认沽期权组合实现

v8.4 对冲模式:
    - Theta引擎: 月度Covered Call备兑增收
    - Put尾部保护: 4大ETF OTM 5% Put全覆盖 (总138张)
    - Put Spread: 阶梯降低成本
    - KillSwitch: 熔断级别检查 (L1+触发则停止新开仓)
    - Liquidation: 2030清仓阶段检查
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

BASE = Path(__file__).resolve().parent
PLAN_DIR = BASE / "trade_plans"
PLAN_DIR.mkdir(exist_ok=True)

BUILD_PLAN_FILE = BASE.parent / "500万建仓计划_20260706.json"
REPORTS_DIR = BASE.parent / "reports"

# v8.6.8 P0-01 FIX (2026-07-26): portfolio.yaml 为资金配置单一事实源
# 原代码硬编码 60/40 拆分 (stock=3M / hedge=2M) 与 portfolio.yaml 不一致 (4M / 1M)
# 导致 trade_plan 顶层 hedge_capital=2M 与 portfolio.yaml=1M 漂移, 后续 hedge_execution
# 预算检查误判 within_budget=true (按 2M 预算放行 1.82M 订单), 实际超 1M 真实预算 82%
PORTFOLIO_YAML = BASE / "config" / "portfolio.yaml"


def _load_capital_config(total_capital: float = 5_000_000) -> Tuple[int, int]:
    """从 portfolio.yaml 读取资金配置 (单一事实源, v8.6.8 P0-01 + P1-Q8 ConfigManager 集成)

    优先级:
        1. ConfigManager 统一入口 (支持 QUANT_CONFIG_DIR 环境变量覆盖)
        2. 直接读取 PORTFOLIO_YAML (v8.3 唯一事实源)
        3. 降级: 60/40 拆分 (兼容旧部署, 仅在 yaml 不可用时使用)

    Returns:
        (stock_etf_capital, hedge_capital) — 与 portfolio.yaml 严格对齐
    """
    # 路径 1: ConfigManager 统一加载
    cfg = None
    try:
        # 确保 utils 模块可访问
        _project_root = BASE.parent
        if str(_project_root) not in sys.path:
            sys.path.insert(0, str(_project_root))
        from utils.config_manager import get_portfolio_config
        cfg = get_portfolio_config()
    except Exception as e:
        print(f"[DEBUG] ConfigManager 不可用, 回退直接读取: {e}", file=sys.stderr)

    # 路径 2: 直接读取 PORTFOLIO_YAML
    if not cfg:
        try:
            import yaml
            with open(PORTFOLIO_YAML, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        except FileNotFoundError:
            print(
                f"[WARN] portfolio.yaml 不存在: {PORTFOLIO_YAML}, 降级 60/40 拆分",
                file=sys.stderr,
            )
            cfg = None
        except Exception as e:
            print(
                f"[WARN] 读取 portfolio.yaml 失败, 降级 60/40 拆分: {e}",
                file=sys.stderr,
            )
            cfg = None

    if cfg:
        accts = cfg.get("account_structure", {}) or {}
        stock = float(accts.get("stock_etf_capital", 4_000_000))
        hedge = float(accts.get("hedge_capital", 1_000_000))
        total_in_cfg = float(accts.get("total_capital", stock + hedge))
        # 一致性校验: stock + hedge 必须等于 total_capital
        if abs((stock + hedge) - total_in_cfg) > 1.0:
            print(
                f"[WARN] portfolio.yaml 资金配置不一致: stock={stock:,.0f} + "
                f"hedge={hedge:,.0f} != total={total_in_cfg:,.0f}",
                file=sys.stderr,
            )
        return int(stock), int(hedge)

    # 路径 3: 降级 60/40 拆分
    print("[WARN] 使用 60/40 降级拆分", file=sys.stderr)
    return int(total_capital * 0.6), int(total_capital * 0.4)

# ============================================================
# 宏观政策/康波评分 (可选导入, 失败降级)
# ============================================================
MACRO_SCORE_READY = False
try:
    MACRO_SCORE_DIR = BASE.parent / "ms_strategy" / "src" / "macro"
    if str(MACRO_SCORE_DIR) not in sys.path:
        sys.path.insert(0, str(MACRO_SCORE_DIR))
    from macro_policy_scoring import macro_score_to_factor, score_macro_policy
    MACRO_SCORE_READY = True
except Exception as _e:
    print(f"[WARN] 宏观评分模块导入失败 (降级模式): {_e}", file=sys.stderr)

MACRO_CUT_MIN_SCORE = 1.15
MACRO_CUT_FACTOR = 0.0
MACRO_WHITELIST_CODES = {"601088", "159915", "sh601088", "sz159915"}

def _load_hedge_execution_plan(trade_date: str, hedge_capital: float = 1_000_000) -> Dict:
    """加载当日对冲执行单 (来自 hedge_execution_orders.py 生成的文件)

    Args:
        trade_date: 交易日期 YYYY-MM-DD
        hedge_capital: 对冲资金总额 (默认 ¥1,000,000, v8.6.8 P0-01 与 portfolio.yaml 对齐)

    Returns:
        {
            "loaded": bool,
            "portfolio_beta": float,
            "hedge_pct": float,
            "hedge_orders": [...],
            "execution_timing": {
                "options_window": "09:30-10:00",
                "futures_window": "10:30-11:00",
                "reserve_ratio": 0.3,
                "reserve_amount": float,
            },
            "budget_check": {
                "total_cost": float,
                "total_premium": float,
                "futures_margin": float,
                "safe_haven": float,
                "reserve_amount": float,
                "remaining": float,
                "within_budget": bool,
            },
        }
    """
    result = {
        "loaded": False,
        "portfolio_beta": 0.0,
        "hedge_pct": 0.0,
        "hedge_orders": [],
        "execution_timing": {
            "options_window": "09:30-10:00",
            "futures_window": "10:30-11:00",
            "reserve_ratio": 0.3,
            "reserve_amount": 0.0,
        },
        "budget_check": {
            "total_cost": 0.0,
            "total_premium": 0.0,
            "futures_margin": 0.0,
            "safe_haven": 0.0,
            "reserve_amount": 0.0,
            "remaining": 0.0,
            "within_budget": True,
        },
    }

    date_compact = trade_date.replace("-", "")
    hedge_file = REPORTS_DIR / f"hedge_execution_orders_{date_compact}.json"

    if not hedge_file.exists():
        print(f"[WARN] 未找到对冲执行单: {hedge_file}", file=sys.stderr)
        return result

    try:
        with open(hedge_file, "r", encoding="utf-8") as f:
            hedge_data = json.load(f)

        result["loaded"] = True
        result["portfolio_beta"] = hedge_data.get("portfolio_beta", 0.0)
        result["hedge_pct"] = hedge_data.get("hedge_pct", 0.0)

        total_premium = 0.0
        total_notional = 0.0
        total_safe_haven = 0.0

        orders_with_window = []
        for o in hedge_data.get("orders", []):
            o_copy = o.copy()
            o_type = o.get("type", "")

            if o_type == "OPTIONS":
                o_copy["execution_window"] = "09:30-10:00"
                total_premium += o.get("premium_budget", 0)
            elif o_type == "FUTURES":
                o_copy["execution_window"] = "10:30-11:00"
                total_notional += o.get("notional", 0)
            elif o_type == "SAFE_HAVEN":
                o_copy["execution_window"] = "09:30-15:00"
                total_safe_haven += o.get("amount", 0)

            orders_with_window.append(o_copy)

        result["hedge_orders"] = orders_with_window

        futures_margin = total_notional * 0.12
        reserve_amount = hedge_capital * 0.3
        total_cost = total_premium + futures_margin + total_safe_haven
        remaining = hedge_capital - total_cost
        # v8.6.13 P1 FIX (2026-08-01 AI 扫描):
        # 原代码 within_budget = total_cost <= hedge_capital, 但 reserve_amount (30%)
        # 已被预留为滚仓缓冲, 实际可用预算应为 hedge_capital - reserve_amount.
        # 当 total_cost = hedge_capital * 0.85 时, 原判断 within_budget=True (0.85 <= 1.0),
        # 但实际已占用全部预留缓冲资金 (0.85 > 0.7 可用), 下游误判预算充足导致滚仓时资金不足.
        available_capital = hedge_capital - reserve_amount
        within_budget = total_cost <= available_capital

        result["execution_timing"]["reserve_amount"] = reserve_amount

        result["budget_check"] = {
            "total_cost": total_cost,
            "total_premium": total_premium,
            "futures_margin": futures_margin,
            "safe_haven": total_safe_haven,
            "reserve_amount": reserve_amount,
            "available_capital": available_capital,  # v8.6.13: 实际可用预算 (扣除 reserve)
            "remaining": remaining,
            "within_budget": within_budget,
        }

        if not within_budget:
            print(f"[WARN] 对冲成本超出预算! 总成本 ¥{total_cost:,.0f} > 可用预算 ¥{available_capital:,.0f} (hedge_capital ¥{hedge_capital:,.0f} - reserve ¥{reserve_amount:,.0f})", file=sys.stderr)

        return result
    except Exception as e:
        print(f"[WARN] 加载对冲执行单失败: {e}", file=sys.stderr)
        return result


# ============================================================
# 对冲基金视角模块 (v7.7) — 可选导入, 失败降级
# ============================================================
HEDGE_FUND_READY = False
try:
    sys.path.insert(0, str(BASE.parent / "utils"))
    from gamma_engine import GammaEngine
    from kill_switch import KillSwitch
    from liquidation_scheduler import LiquidationScheduler
    from theta_engine import ThetaEngine
    HEDGE_FUND_READY = True
except ImportError as _e:
    print(f"[WARN] 对冲基金模块导入失败 (降级模式): {_e}", file=sys.stderr)


def _load_hedge_fund_overlays(trade_date: str) -> Dict:
    """加载对冲基金视角的4个模块状态 (v7.7)

    Returns:
        {
            "kill_switch": {...},
            "theta": {...},
            "gamma": {...},
            "liquidation": {...},
            "options_orders": [...],  # Theta引擎生成的Covered Call订单
        }
    """
    overlays: Dict = {
        "kill_switch": {"available": False, "level": 0, "level_name": "正常"},
        "theta": {"available": False, "plan_loaded": False, "positions": []},
        "gamma": {"available": False, "triggered": False},
        "liquidation": {"available": False, "phase": 0, "phase_name": "正常运行期"},
        "options_orders": [],
    }

    if not HEDGE_FUND_READY:
        return overlays

    # === 1. KillSwitch 检查 ===
    try:
        ks = KillSwitch()
        ks_status = ks.check_margin_status()
        ks_level = int(ks_status.get("level", 0)) if isinstance(ks_status, dict) else 0
        overlays["kill_switch"] = {
            "available": True,
            "level": ks_level,
            "level_name": ks_status.get("level_name", "正常") if isinstance(ks_status, dict) else "正常",
            "margin_usage_ratio": ks_status.get("margin_usage_ratio", 0) if isinstance(ks_status, dict) else 0,
            "actions": ks_status.get("actions", []) if isinstance(ks_status, dict) else [],
            "build_allowed": ks_level == 0,  # L1+ 触发则禁止新开仓
        }
    except Exception as e:
        # v8.6.13 P0 FIX (2026-08-01 AI 扫描):
        # 原代码 fail-open (build_allowed=True), 风控模块异常时无法检测熔断信号,
        # 仍允许继续建仓, 在市场熔断或保证金不足时风控完全失效, 直接导致资金损失.
        # 顶级对冲基金标准: 风控模块必须 fail-closed, 模块异常时按最严重场景处理.
        overlays["kill_switch"] = {"available": False, "error": str(e), "build_allowed": False}

    # === 2. Theta引擎 — 加载月度Covered Call计划 ===
    try:
        theta = ThetaEngine()
        theta_cfg = getattr(theta, "config", {}) or {}
        if not theta_cfg.get("enabled", False):
            overlays["theta"] = {"available": True, "enabled": False, "plan_loaded": False}
        else:
            # 优先尝试加载已有计划
            date_compact = trade_date.replace("-", "")
            theta_plan_path = BASE.parent / "reports" / "theta_plans" / f"theta_plan_{date_compact}.json"
            theta_plan = None
            if theta_plan_path.exists():
                try:
                    with open(theta_plan_path, "r", encoding="utf-8") as f:
                        theta_plan = json.load(f)
                except Exception:
                    theta_plan = None

            # 不存在则生成
            if theta_plan is None:
                theta_plan = theta.generate_monthly_plan()

            positions = theta_plan.get("positions", [])
            # 将 Theta positions 转换为 options_orders
            options_orders = []
            for pos in positions:
                options_orders.append({
                    "code": pos.get("code"),
                    "name": f"{pos.get('code')}_CoveredCall",
                    "direction": "SELL_CALL",
                    "underlying": pos.get("code"),
                    "spot_price": pos.get("spot_price"),
                    "strike": pos.get("strike"),
                    "strike_otm_pct": pos.get("strike_otm_pct"),
                    "contracts": pos.get("contracts"),
                    "est_premium_per_unit": pos.get("est_premium_per_unit"),
                    "est_premium_total": pos.get("est_premium_total"),
                    "collateral": pos.get("collateral"),
                    "annualized_return": pos.get("annualized_return"),
                    "iv_estimate": pos.get("iv_estimate"),
                    "order_type": "LIMIT",
                    "session": "morning",
                    "note": f"Theta引擎月度Covered Call (DTE={theta_plan.get('dte')}天, 到期={theta_plan.get('expiry_date')})",
                })

            overlays["theta"] = {
                "available": True,
                "enabled": True,
                "plan_loaded": True,
                "plan_date": theta_plan.get("generate_date"),
                "expiry_date": theta_plan.get("expiry_date"),
                "dte": theta_plan.get("dte"),
                "positions_count": len(positions),
                "total_premium": theta_plan.get("total_est_premium", 0),
                "portfolio_yield_monthly": theta_plan.get("portfolio_yield_monthly", 0),
                "portfolio_yield_annualized": theta_plan.get("portfolio_yield_annualized", 0),
                "positions": positions,
            }
            overlays["options_orders"] = options_orders
    except Exception as e:
        overlays["theta"] = {"available": False, "error": str(e), "plan_loaded": False}

    # === 3. Gamma引擎 — 尾部危机监控状态 ===
    try:
        gamma = GammaEngine()
        monitor_result = gamma.monitor()
        overlays["gamma"] = {
            "available": True,
            "triggered": bool(monitor_result.get("triggered", False)),
            "trigger_type": monitor_result.get("trigger_type"),
            "ma60_status": monitor_result.get("ma60_status"),
            "iv_percentile": monitor_result.get("iv_percentile"),
            "budget": monitor_result.get("budget", 0),
        }
    except Exception as e:
        overlays["gamma"] = {"available": False, "error": str(e), "triggered": False}

    # === 4. LiquidationScheduler — 2030清仓阶段 ===
    try:
        ls = LiquidationScheduler()
        current_phase = ls.get_current_phase()
        phase_num = current_phase.get("phase", 0) if current_phase else 0
        phase_name = current_phase.get("name", "未知") if current_phase else "未知"
        days_to_next = current_phase.get("days_to_next_phase") if current_phase else None
        overlays["liquidation"] = {
            "available": True,
            "phase": phase_num,
            "phase_name": phase_name,
            "days_to_next": days_to_next,
            "build_allowed": phase_num == 0,  # 进入清仓阶段后不允许新建仓
        }
    except Exception as e:
        # v8.6.13 P0 FIX (2026-08-01 AI 扫描):
        # 原代码 fail-open (build_allowed=True), 清仓调度器异常时可能在清仓阶段继续新建仓.
        # 风控模块必须 fail-closed, 模块异常时禁止新建仓, 等待人工介入.
        overlays["liquidation"] = {"available": False, "error": str(e), "phase": 0, "build_allowed": False}

    return overlays


# ============================================================
# 建仓计划 (v8.4 OPTIONS_ONLY 纯期权对冲)
# ============================================================
# 交易日数: 30 (7/13 周一 ~ 8/21 周五, 跳过周末)
# 每日建仓: 由 portfolio.yaml.stock_etf_capital / duration_days 动态计算
# 总建仓金额: 由 portfolio.yaml.stock_etf_capital 决定 (当前 4,000,000)
# 对冲资金: 由 portfolio.yaml.hedge_capital 决定 (当前 1,000,000, 纯期权对冲)
# 目标: 年化>=8%, 回撤<=15%
# v8.6.8 P0-09 FIX (2026-07-26): phase_capital 从 portfolio.yaml 动态读取
# 原始 bug: PHASES 硬编码 phase_capital=3_000_000, capital_ratio=0.60
# 但 portfolio.yaml.stock_etf_capital=4_000_000, 导致 phase 资金与顶层 stock_etf_capital 不一致
PHASES = [
    {"phase": 1, "name": "集中建仓期",
     "start": "2026-07-13", "end": "2026-08-21",
     "capital_ratio": None,  # 动态计算: stock_etf_capital / total_capital
     "phase_capital": None,   # 动态计算: 从 portfolio.yaml 读取
     "duration_days": 30,
     "daily_capital": None,   # 动态计算: phase_capital / duration_days
     "strategy": "每日建仓 + 100万纯期权对冲, 8/21 完成建仓 (v8.6.8 P0-09: 资金从 portfolio.yaml 动态读取)"},
]


def _get_dynamic_phase_config(stock_etf_capital: float, total_capital: float = 5_000_000) -> Dict:
    """动态计算 phase 配置 (v8.6.8 P0-09)

    Args:
        stock_etf_capital: portfolio.yaml 读取的现货资金 (4,000,000)
        total_capital: 总资金 (5,000,000)

    Returns:
        动态填充后的 PHASES[0] 副本
    """
    phase = PHASES[0].copy()
    phase['phase_capital'] = int(stock_etf_capital)
    phase['capital_ratio'] = round(stock_etf_capital / total_capital, 4) if total_capital > 0 else 0.6
    phase['daily_capital'] = round(stock_etf_capital / phase['duration_days'], 2)
    return phase


def next_trading_day(date: datetime) -> datetime:
    """获取下一个交易日 (跳过周末)"""
    d = date + timedelta(days=1)
    while d.weekday() >= 5:  # 5=周六, 6=周日
        d += timedelta(days=1)
    return d


def get_phase(date: datetime, stock_etf_capital: Optional[float] = None, total_capital: float = 5_000_000) -> Dict:
    """根据日期判断当前阶段 (v8.6.8 P0-09: 动态填充 phase_capital/daily_capital)"""
    date_str = date.strftime("%Y-%m-%d")
    for p in PHASES:
        if p["start"] <= date_str <= p["end"]:
            # 计算 day_index
            start = datetime.strptime(p["start"], "%Y-%m-%d")
            # 仅计算工作日
            day_index = 0
            cur = start
            while cur <= date:
                if cur.weekday() < 5:
                    day_index += 1
                cur += timedelta(days=1)
            # v8.6.8 P0-09: 若 stock_etf_capital 提供, 动态填充资金字段
            if stock_etf_capital is not None and p.get('phase_capital') is None:
                p = _get_dynamic_phase_config(stock_etf_capital, total_capital)
            return {**p, "day_index": day_index}
    # 默认返回第一阶段
    default_phase = PHASES[0]
    if stock_etf_capital is not None and default_phase.get('phase_capital') is None:
        default_phase = _get_dynamic_phase_config(stock_etf_capital, total_capital)
    return {**default_phase, "day_index": 1}


def load_build_plan() -> Dict:
    """加载 500万建仓计划 (含 23 标的新权重)"""
    with open(BUILD_PLAN_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# ============================================================
# 标的基础信息 (est_price / 风格 / 风险)
# ============================================================
SYMBOL_INFO = {
    "588000": {"name": "科创50ETF华夏", "est_price": 2.21, "style": "高端制造", "risk": "高", "lots": 100},
    "512480": {"name": "半导体ETF国泰", "est_price": 1.45, "style": "高端制造", "risk": "高", "lots": 100},
    "516160": {"name": "高端装备ETF南方", "est_price": 1.12, "style": "高端制造", "risk": "高", "lots": 100},
    "515030": {"name": "新能源车ETF华夏", "est_price": 1.67, "style": "高端制造", "risk": "高", "lots": 100},
    "159915": {"name": "创业板ETF易方达", "est_price": 3.86, "style": "高端制造", "risk": "高", "lots": 100},
    "159992": {"name": "创新药ETF银华", "est_price": 0.92, "style": "防御", "risk": "高", "lots": 100},
    "512010": {"name": "医药ETF易方达", "est_price": 0.58, "style": "防御", "risk": "中", "lots": 100},
    "511260": {"name": "十年国债ETF国泰", "est_price": 102.5, "style": "防御", "risk": "低", "lots": 100},
    "511520": {"name": "政金债ETF富国", "est_price": 101.2, "style": "防御", "risk": "低", "lots": 100},
    "511360": {"name": "短融ETF海富通", "est_price": 100.05, "style": "防御", "risk": "低", "lots": 100},
    "512400": {"name": "有色金属ETF南方", "est_price": 1.18, "style": "资源", "risk": "高", "lots": 100},
    "518880": {"name": "黄金ETF华安", "est_price": 8.54, "style": "资源", "risk": "中", "lots": 100},
    "601088": {"name": "中国神华", "est_price": 42.04, "style": "顺周期", "risk": "中", "lots": 100},
    "512100": {"name": "中证1000ETF南方", "est_price": 2.65, "style": "宽基", "risk": "中", "lots": 100},
    "510500": {"name": "中证500ETF南方", "est_price": 6.2, "style": "宽基", "risk": "中", "lots": 100},
    "588200": {"name": "科创板芯片ETF嘉实", "est_price": 1.05, "style": "高端制造", "risk": "高", "lots": 100},
    "159516": {"name": "半导体材料设备ETF国泰", "est_price": 0.85, "style": "高端制造", "risk": "高", "lots": 100},

}


def _load_real_time_prices() -> Dict[str, float]:
    """从 positions.json 加载实时价格"""
    positions_file = BASE.parent / "config" / "positions.json"
    if not positions_file.exists():
        return {}
    try:
        with open(positions_file, "r", encoding="utf-8") as f:
            positions = json.load(f)
        prices = {}
        for code, pos in positions.get("positions", {}).items():
            est_price = pos.get("est_price", pos.get("avg_cost", 0))
            if est_price > 0:
                prices[code] = est_price
                prices[code.replace(".SH", "").replace(".SZ", "")] = est_price
        return prices
    except Exception:
        return {}


def _load_hedge_mode() -> str:
    """从 positions.json 加载对冲模式 (v8.4 Phase 2: 支持 FUTURES_AND_OPTIONS)

    Returns:
        "FUTURES_AND_OPTIONS" 或 "OPTIONS_ONLY" (默认)
    """
    positions_file = BASE.parent / "config" / "positions.json"
    if not positions_file.exists():
        return "OPTIONS_ONLY"
    try:
        with open(positions_file, "r", encoding="utf-8") as f:
            positions = json.load(f)
        mode = str(positions.get("meta", {}).get("hedge_mode", "OPTIONS_ONLY")).upper()
        if mode in ("FUTURES_AND_OPTIONS", "MIXED", "OPTIONS_ONLY", "FUTURES_ONLY"):
            return mode
        return "OPTIONS_ONLY"
    except Exception:
        return "OPTIONS_ONLY"


def generate_orders(trade_date: str, phase: Dict, build_plan: Dict,
                    stock_capital: float = 3_000_000) -> Dict:
    """生成当日买卖订单 (上午 + 下午批次)

    策略:
        - 每日建仓资金 = daily_capital (固定 15 万现货)
        - 按 23 标的权重分配
        - 每个标的按 100 股整数倍取整
        - 上午 50% / 下午 50%
    """
    real_time_prices = _load_real_time_prices()

    if "daily_capital" in phase:
        stock_day_capital = float(phase["daily_capital"])
    else:
        day_capital = phase["phase_capital"] / phase["duration_days"]
        stock_day_capital = day_capital * (stock_capital / (stock_capital + 2_000_000))

    target_portfolio = build_plan.get("target_portfolio", {})

    # === 剔除不符合国家十五五规划 / 康波周期的标的 ===
    if MACRO_SCORE_READY and target_portfolio:
        try:
            macro_scores = score_macro_policy(list(target_portfolio.keys()))
            filtered_portfolio = {}
            removed = []
            for symbol, info in target_portfolio.items():
                # 白名单直接保留
                if symbol in MACRO_WHITELIST_CODES or info.get("name", "") in {"中国神华", "创业板ETF易方达"}:
                    filtered_portfolio[symbol] = info
                    continue

                score_obj = macro_scores.get(symbol)
                combined = getattr(score_obj, "combined_score", 1.0)
                factor = macro_score_to_factor(combined, cut_max=MACRO_CUT_MIN_SCORE)
                if factor > 0.0:
                    filtered_portfolio[symbol] = info
                else:
                    removed.append({
                        "code": symbol,
                        "name": info.get("name", symbol),
                        "combined_score": combined,
                        "reason": "不符合十五五/康波周期要求",
                    })
            if removed:
                print(f"[INFO] 宏观筛选剔除 {len(removed)} 个标的: {[r['name'] for r in removed]}")
            target_portfolio = filtered_portfolio or target_portfolio

            if not target_portfolio:
                print("[WARN] 宏观筛选后无剩余标的，回退使用原组合", file=sys.stderr)
                target_portfolio = build_plan.get("target_portfolio", {})
        except Exception as e:
            print(f"[WARN] 宏观评分筛选失败，回退使用原组合: {e}", file=sys.stderr)

    # 归一化剩余权重
    if target_portfolio:
        total_weight = sum(float(v.get("weight", 0)) for v in target_portfolio.values())
        if total_weight > 0:
            for info in target_portfolio.values():
                info["weight"] = round(float(info.get("weight", 0)) / total_weight, 6)

    morning_orders: List[Dict] = []
    afternoon_orders: List[Dict] = []
    priority = 1
    total_amount = 0.0

    for symbol, info in target_portfolio.items():
        weight = info["weight"]
        target_amount = stock_day_capital * weight

        base_info = SYMBOL_INFO.get(symbol, {})
        est_price = real_time_prices.get(symbol, base_info.get("est_price", 10.0))
        lots_size = base_info.get("lots", 100)
        name = base_info.get("name", info.get("name", symbol))
        style = base_info.get("style", "其他")
        risk = base_info.get("risk", "中")

        # 计算股数 (按手数取整)
        raw_shares = int(target_amount / est_price)
        shares = (raw_shares // lots_size) * lots_size
        if shares <= 0:
            shares = lots_size  # 最少 1 手

        # v8.6.13 P3 FIX: 删除死代码 (计算结果未赋值)
        # shares * est_price
        # round(est_price * 1.008, 3)

        # 上午/下午拆分 (50% / 50%)
        # v8.6.13 P1 FIX (2026-08-01 AI 扫描):
        # 原代码 morning_shares = shares // 2 后可能不再是 100 的整数倍,
        # 如 shares=100 → morning=50 (50 股无法下单), shares=300 → morning=150,
        # A 股买入最小单位 100 股, 拆分后非 100 整数倍的订单会被交易所拒绝.
        # 修复: 拆分后重新按 lots_size 取整; 若 morning_shares=0 (shares<2*lots_size),
        # 全部放到下午 (避免上午订单数为 0 但下午有 1 手的拆分错误).
        morning_shares = (shares // 2 // lots_size) * lots_size
        afternoon_shares = shares - morning_shares
        # 若拆分后上午为 0 (总股数 < 2 手), 全部放下午
        if morning_shares == 0:
            morning_shares = 0
            afternoon_shares = shares

        if morning_shares > 0:
            morning_orders.append({
                "priority": priority,
                "code": symbol,
                "name": name,
                "session": "morning",
                "shares": morning_shares,
                "est_price": est_price,
                "limit_price": round(est_price * 1.008, 3),
                "est_amount": round(morning_shares * est_price, 2),
                "side": "BUY",
                "order_type": "LIMIT",
                "style": style,
                "risk": risk,
                "note": f"上午批次 09:30-10:30 (阶段{phase['phase']} 第{phase['day_index']}天)",
                "technical_alpha": 1.0,
            })
            priority += 1
            total_amount += morning_shares * est_price

        if afternoon_shares > 0:
            afternoon_orders.append({
                "priority": priority,
                "code": symbol,
                "name": name,
                "session": "afternoon",
                "shares": afternoon_shares,
                "est_price": est_price,
                "limit_price": round(est_price * 1.008, 3),
                "est_amount": round(afternoon_shares * est_price, 2),
                "side": "BUY",
                "order_type": "LIMIT",
                "style": style,
                "risk": risk,
                "note": f"下午批次 14:00-14:30 (阶段{phase['phase']} 第{phase['day_index']}天)",
                "technical_alpha": 1.0,
            })
            priority += 1
            total_amount += afternoon_shares * est_price

    return {
        "morning_orders": morning_orders,
        "afternoon_orders": afternoon_orders,
        "total_orders": len(morning_orders) + len(afternoon_orders),
        "total_amount": round(total_amount, 2),
        "day_capital": round(stock_day_capital, 2),
        "asset_count": len(target_portfolio),
    }


def generate_trade_plan(trade_date: str, capital: float = 5_000_000) -> Dict:
    """生成完整 trade_plan 字典"""
    dt = datetime.strptime(trade_date, "%Y-%m-%d")
    weekday_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][dt.weekday()]

    # v8.6.8 P0-01 FIX: 从 portfolio.yaml 读取资金配置 (单一事实源)
    # 原代码硬编码 stock=capital*0.6, hedge=capital*0.4 与 portfolio.yaml 漂移
    stock_capital, hedge_capital = _load_capital_config(capital)

    # v8.4 Phase 2: 动态加载对冲模式 (FUTURES_AND_OPTIONS / OPTIONS_ONLY)
    hedge_mode = _load_hedge_mode()
    is_futures_enabled = hedge_mode in ("FUTURES_AND_OPTIONS", "MIXED", "FUTURES_ONLY")

    # v8.6.8 P0-09 FIX: get_phase 传入 stock_etf_capital, 动态填充 phase_capital/daily_capital
    phase = get_phase(dt, stock_etf_capital=stock_capital, total_capital=capital)
    build_plan = load_build_plan()

    orders = generate_orders(trade_date, phase, build_plan, stock_capital)

    # === v7.7: 加载对冲基金视角 overlays ===
    overlays = _load_hedge_fund_overlays(trade_date)

    # === v7.7+: 加载对冲执行单 (期货期权对冲) ===
    hedge_exec_plan = _load_hedge_execution_plan(trade_date, hedge_capital)

    # === v7.7: 综合判断是否允许新开仓 ===
    ks = overlays["kill_switch"]
    liq = overlays["liquidation"]
    gamma_triggered = overlays["gamma"].get("triggered", False)
    # 市场允许建仓 = KillSwitch未触发 AND 未进入清仓阶段 AND Gamma未触发尾部对冲
    build_allowed = ks.get("build_allowed", True) and liq.get("build_allowed", True)
    # 若 Gamma 触发尾部对冲, 仅允许对冲端建仓, 现货端暂停
    spot_build_allowed = build_allowed and not gamma_triggered

    # 若 KillSwitch 触发, 清空现货订单 (仅保留平仓指令)
    morning_orders = orders["morning_orders"] if spot_build_allowed else []
    afternoon_orders = orders["afternoon_orders"] if spot_build_allowed else []
    # 若 KillSwitch 触发, 将订单 side 改为 HOLD (不执行新开仓)
    if not spot_build_allowed and (morning_orders or afternoon_orders):
        # 仅保留订单列表作为参考, 标记为 HOLD
        for o in morning_orders + afternoon_orders:
            o["side"] = "HOLD"
            o["note"] = f"[KillSwitch L{ks.get('level', 0)}] 暂停新开仓, 仅供记录"

    # v8.6.8 P0-04 FIX (2026-07-26): spot_build_allowed=False 时拦截 Theta Covered Call
    # 原始 bug: spot_build_allowed=false 仅清空现货订单, 但 execution_plan.options_orders
    # 仍包含 Theta 引擎生成的 6 笔 SELL_CALL 订单. Covered Call 必须先持有现货才能卖出,
    # 无现货时执行 SELL_CALL = 裸卖出 Call, 风险无限 (类似 GME 逼空事件).
    # 顶级对冲基金标准: 备兑策略必须有底层多头支撑, 否则一律禁止
    theta_options_orders = overlays.get("options_orders", []) or []
    theta_blocked_reason = None
    if not spot_build_allowed and theta_options_orders:
        theta_blocked_reason = (
            f"spot_build_allowed=False (KillSwitch L{ks.get('level', 0)}), "
            f"已拦截 {len(theta_options_orders)} 笔 Theta Covered Call (裸卖出 Call 风险无限)"
        )
        print(f"[WARN] [P0-04] {theta_blocked_reason}", file=sys.stderr)
        theta_options_orders = []  # 清空 Covered Call 订单
        # 同步 theta_engine 状态
        overlays["theta"]["blocked_reason"] = theta_blocked_reason
        overlays["theta"]["positions_count"] = 0
        overlays["theta"]["total_premium"] = 0

    total_orders = len(morning_orders) + len(afternoon_orders)
    total_amount = sum(o.get("est_amount", 0) for o in morning_orders + afternoon_orders)

    # === v7.7: 合成 hedge_fund_overlays 字段 ===
    hedge_fund_overlays = {
        "kill_switch": ks,
        "theta_engine": overlays["theta"],
        "gamma_vega_engine": overlays["gamma"],
        "liquidation_protocol": liq,
        "v77_notes": {
            "build_allowed": build_allowed,
            "spot_build_allowed": spot_build_allowed,
            # v8.6.13 P1 FIX (2026-08-01 AI 扫描):
            # 原代码硬编码 True, 但 P0-04 修复在 spot_build_allowed=False 时会清空 Theta
            # Covered Call 订单 (无现货时 SELL_CALL = 裸卖出, 风险无限).
            # 字段值与实际行为不一致, 下游模块误以为期权订单可执行, 可能绕过 P0-04 拦截.
            # 修复: 与现货建仓同步, Covered Call 需要底层多头支撑.
            "options_orders_allowed": spot_build_allowed,
            "gamma_triggered": gamma_triggered,
            "reason_if_blocked": (
                f"KillSwitch L{ks.get('level', 0)}" if not ks.get("build_allowed", True)
                else f"清仓阶段 Phase {liq.get('phase', 0)}" if not liq.get("build_allowed", True)
                else "Gamma尾部对冲触发" if gamma_triggered
                else None
            ),
        },
    }

    return {
        "trade_date": trade_date,
        "weekday": weekday_cn,
        "capital": capital,
        "stock_etf_capital": stock_capital,
        "hedge_capital": hedge_capital,
        "execution_mode": "MOCK_BROKER",
        "strategy": "康波第六轮周期 × 十五五规划 × v7.7对冲基金视角融合 (Theta+Gamma+KillSwitch+Liquidation)",
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "source_plan": "500万建仓计划_20260706.json",
            "source_build_plan": str(BUILD_PLAN_FILE),
            # v8.6.8 P0-10 FIX (2026-07-26): 版本号同步至最新 (原 v8.6.1 过时)
            "version": "v8.6.8_institutional_hedge_fund_live_ready",
            "note": f"动态生成 — 阶段{phase['phase']} 第{phase['day_index']}天, 23 标的新权重, "
                    f"对冲基金模块={'ON' if HEDGE_FUND_READY else 'OFF'}",
        },
        "phase": {
            "phase_number": phase["phase"],
            "name": phase["name"],
            "start_date": phase["start"],
            "end_date": phase["end"],
            "duration_days": phase["duration_days"],
            "day_index": phase["day_index"],
            "capital_ratio": phase["capital_ratio"],
            "phase_capital": phase["phase_capital"],
            "daily_capital": phase.get("daily_capital", round(phase["phase_capital"] / phase["duration_days"], 2)),
            "day_capital": round(phase["phase_capital"] / phase["duration_days"], 2),
            "asset_count": orders.get("asset_count", len(build_plan.get("target_portfolio", {}))),
            "strategy": phase["strategy"],
        },
        "market_state": {
            "vix": 18.5,
            "circuit_level": "NORMAL",
            "build_allowed": build_allowed,
            "spot_build_allowed": spot_build_allowed,
            "notes": (
                f"v7.7 对冲基金视角: KillSwitch=L{ks.get('level', 0)}, "
                f"清仓阶段=Phase {liq.get('phase', 0)}, "
                f"Gamma触发={gamma_triggered}"
            ),
        },
        "risk_controls": {
            "yellow_warning": -0.06,
            "orange_warning": -0.08,
            "red_stop": -0.10,
            "single_day_loss_pause": -0.02,
            "var_95_limit": 0.04,
            "var_99_limit": 0.06,
            "max_futures_margin_pct": 0.12,
            "max_option_premium_yearly_pct": 0.025,
            "stop_loss_rules": {
                "high_risk": -0.10,
                "medium_risk": -0.10,
                "low_risk": -0.05,
            },
            "target_annual_return": 0.08,
            "max_drawdown_limit": 0.15,
        },
        "hedge_config": {
            "total_hedge_capital": hedge_capital,
            # v8.4 Phase 2: 根据 positions.json hedge_mode 动态生成 layers
            "hedge_mode": hedge_mode,
            "layers": (
                {
                    "layer1_futures": (
                        {
                            "action": "SHORT_FUTURES",
                            "ratio": 0.50,
                            "target_beta": 0.30,
                            "instruments": ["IF_futures", "IC_futures"],
                            "capital": int(hedge_capital * 0.50),
                            "reason": f"hedge_mode={hedge_mode}, 期货空头管理Beta, 目标组合Beta≤0.30",
                        }
                        if is_futures_enabled
                        else {
                            "action": "DISABLED_BY_OPTIONS_ONLY",
                            "ratio": 0.0,
                            "target_beta": None,
                            "instrument": "DISABLED — Beta 对冲通过 ETF Put 组合实现",
                            "capital": 0,
                            "reason": f"hedge_mode={hedge_mode}, 不持有 IF 期货空头",
                        }
                    ),
                    "layer2_options": {
                        "action": "PUT_PROTECTION_FULL",
                        "long_put_strike": 0.95,
                        "short_put_strike": None,
                        "short_call_strike": None,
                        "capital": hedge_capital if not is_futures_enabled else int(hedge_capital * 0.50),
                        "instruments": ["510050 Put", "588080 Put", "159915 Put", "510300 Put"],
                        "total_premium_budget": int((hedge_capital if not is_futures_enabled else hedge_capital * 0.50) * 0.825),
                        "buffer_for_roll": int((hedge_capital if not is_futures_enabled else hedge_capital * 0.50) * 0.175),
                    },
                    "layer3_volatility": "监控模式 (IV/RV 偏离 > 5% 时小仓位试单)",
                    "layer4_absolute_return": "准备配对池, 暂不交易",
                    "layer5_covered_call": "v7.7 已由 Theta引擎自动生成月度计划" if overlays["theta"].get("plan_loaded") else "不启动 (建仓初期)",
                }
            ),
        },
        # === v7.7 新增: 对冲基金视角融合字段 ===
        "hedge_fund_overlays": hedge_fund_overlays,
        "execution_plan": {
            "broker": "MockBroker",
            "morning_window": "09:30-10:30",
            "afternoon_window": "14:00-14:30",
            "price_buffer": 0.008,
            "price_deviation_skip": 0.1,
            "session_split": 0.5,
            "morning_orders": morning_orders,
            "afternoon_orders": afternoon_orders,
            "total_orders": total_orders,
            "total_amount": round(total_amount, 2),
            "day_capital": orders["day_capital"],
            # === v7.7 新增: 期权订单 (Theta引擎 Covered Call) ===
            # v8.6.8 P0-04: spot_build_allowed=False 时已清空 theta_options_orders
            "options_orders": theta_options_orders,
            "options_orders_count": len(theta_options_orders),
            "options_total_premium": overlays["theta"].get("total_premium", 0),
        },
        "options_execution": {
            "enabled": True,
            "hedge_capital": hedge_capital,
            "margin_usage_max": int(hedge_capital * 0.6),
            "liquidity_buffer_min": int(hedge_capital * 0.4),
            "roll_day": "每月第一个交易日",
            "collar_review_day": "每日收盘后",
            "vega_event_review": "事件前3个交易日",
            "event_calendar": [
                "年底中央经济工作会议",
                "十五五中期评估",
                "美联储议息会议",
                "国内重要政策发布会",
            ],
            "modules": [
                {
                    "name": "covered_call_overlay",
                    "alias": "备兑增强策略",
                    "capital_required": 0,
                    "underlyings": [
                        {
                            "code": "588080.SH",
                            "direction": "SELL_CALL",
                            "strike_rule": "OTM_5pct_to_8pct",
                            "expiry_rule": "每月初选择次月到期",
                        }
                    ],
                },
                {
                    "name": "risk_reversal_collar",
                    "alias": "双反向不对称组合",
                    "trigger_conditions": [
                        "科技股PE分位数>85%",
                        "宏观流动性收紧信号",
                        "市场跌破120日均线",
                        "VIX/隐含波动率骤升",
                    ],
                },
                {
                    "name": "vega_event_driven",
                    "alias": "波动率套利与事件驱动",
                    "event_calendar": [
                        "年底中央经济工作会议",
                        "十五五中期评估",
                        "美联储议息会议",
                        "国内重要政策发布会",
                    ],
                },
            ],
        },
        "hedge_account": {
            "capital": hedge_capital,
            "strategy": "multi_strategy_options_overlay",
            "margin_usage_max": int(hedge_capital * 0.6),
            "liquidity_buffer_min": int(hedge_capital * 0.4),
            "modules": [
                {
                    "name": "covered_call_overlay",
                    "alias": "备兑增强策略",
                    "capital_required": 0,
                    "underlyings": [
                        {
                            "code": "588080.SH",
                            "direction": "SELL_CALL",
                            "strike_rule": "OTM_5pct_to_8pct",
                            "expiry_rule": "每月初选择次月到期",
                        }
                    ],
                },
                {
                    "name": "risk_reversal_collar",
                    "alias": "双反向不对称组合",
                    "trigger_conditions": [
                        "科技股PE分位数>85%",
                        "宏观流动性收紧信号",
                        "市场跌破120日均线",
                        "VIX/隐含波动率骤升",
                    ],
                },
                {
                    "name": "vega_event_driven",
                    "alias": "波动率套利与事件驱动",
                    "event_calendar": [
                        "年底中央经济工作会议",
                        "十五五中期评估",
                        "美联储议息会议",
                        "国内重要政策发布会",
                    ],
                },
            ],
        },
        # === v8.4 Phase 2: 期货+期权双路径对冲执行单 ===
        "futures_options_hedge": {
            "hedge_mode": hedge_mode,
            "loaded": hedge_exec_plan.get("loaded", False),
            "portfolio_beta": hedge_exec_plan.get("portfolio_beta", 0.0),
            "hedge_pct": hedge_exec_plan.get("hedge_pct", 0.0),
            "execution_timing": hedge_exec_plan.get("execution_timing", {
                "futures_window": "09:30-09:45",
                "options_window": "09:45-10:15",
                "reserve_ratio": 0.175,
                "reserve_amount": int(hedge_capital * 0.175),
            }),
            "orders": hedge_exec_plan.get("hedge_orders", []),
            "orders_count": len(hedge_exec_plan.get("hedge_orders", [])),
            "total_premium": hedge_exec_plan.get("budget_check", {}).get("total_premium", 0),
            "total_notional": 0,
            "total_safe_haven": hedge_exec_plan.get("budget_check", {}).get("safe_haven", 0),
            "budget_check": hedge_exec_plan.get("budget_check", {}),
            "notes": [
                (
                    f"对冲模式: {hedge_mode} — IF/IC期货空头管理Beta (目标≤0.30), "
                    f"4份ETF Put保护尾部风险"
                    if is_futures_enabled
                    else "对冲模式: OPTIONS_ONLY — 不持有IF期货空头，系统性Beta风险全部通过ETF认沽期权组合管理"
                ),
                "期权执行时机: 开盘后30-45分钟内完成期权建仓, 避免权利金成本波动",
                (
                    f"总预算: {hedge_capital/1e4:.0f}万 "
                    f"(期货保证金 {hedge_capital*0.50/1e4:.1f}万 + "
                    f"期权权利金 {hedge_capital*0.50*0.825/1e4:.1f}万 + "
                    f"滚仓/保证金缓冲 {hedge_capital*0.50*0.175/1e4:.1f}万, v8.4 Phase 2)"
                    if is_futures_enabled
                    else f"总预算: {hedge_capital/1e4:.0f}万 ({hedge_capital*0.825/1e4:.1f}万权利金 + {hedge_capital*0.175/1e4:.1f}万滚仓/保证金缓冲, v8.6.8 P0-01 与 portfolio.yaml 对齐)"
                ),
                "510050 Put (60张) + 510300 Put (25张) = 沪深300 + 上证50 Beta对冲主力层",
                "588080 Put (25张) + 159915 Put (25张) = 科技/成长尾部保护层",
                "Covered Call Theta引擎 + Put Spread阶梯 = 保险成本回收与精确风控",
            ] if is_futures_enabled else [
                "对冲模式: OPTIONS_ONLY — 不持有IF期货空头，系统性Beta风险全部通过ETF认沽期权组合管理",
                "期权执行时机: 开盘后30分钟内(09:30-10:00)完成期权建仓, 避免权利金成本波动",
                f"总预算: {hedge_capital/1e4:.0f}万 ({hedge_capital*0.825/1e4:.1f}万权利金 + {hedge_capital*0.175/1e4:.1f}万滚仓/保证金缓冲)",
                "510050 Put (60张) + 510300 Put (25张) = 沪深300 + 上证50 Beta对冲主力层",
                "588080 Put (25张) + 159915 Put (25张) = 科技/成长尾部保护层",
                "Covered Call Theta引擎 + Put Spread阶梯 = 保险成本回收与精确风控",
            ],
        },
    }


def main():
    parser = argparse.ArgumentParser(description="动态生成 trade_plan_{YYYYMMDD}.json")
    parser.add_argument("date", nargs="?", default=None,
                        help="交易日期 YYYY-MM-DD (默认: 下一交易日)")
    parser.add_argument("--capital", type=float, default=5_000_000,
                        help="总资金 (默认: 5000000)")
    args = parser.parse_args()

    if args.date:
        trade_date = args.date
    else:
        trade_date = next_trading_day(datetime.now()).strftime("%Y-%m-%d")

    print(f"生成交易计划: {trade_date}")
    print(f"总资金: Y{args.capital:,.0f}")

    plan = generate_trade_plan(trade_date, args.capital)

    # 保存 JSON
    date_compact = trade_date.replace("-", "")
    json_path = PLAN_DIR / f"trade_plan_{date_compact}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)

    print(f"\n✓ 已生成: {json_path}")
    print("\n--- 计划摘要 (v8.4 纯期权对冲模式) ---")
    print(f"交易日: {plan['trade_date']} ({plan['weekday']})")
    print(f"阶段: {plan['phase']['name']} (第 {plan['phase']['day_index']}/{plan['phase']['duration_days']} 天)")
    print(f"当日资金: ¥{plan['execution_plan']['day_capital']:,.0f}")
    print(f"现货订单: {plan['execution_plan']['total_orders']} (上午 {len(plan['execution_plan']['morning_orders'])} + 下午 {len(plan['execution_plan']['afternoon_orders'])})")
    print(f"现货总额: ¥{plan['execution_plan']['total_amount']:,.0f}")
    print(f"期权订单: {plan['execution_plan']['options_orders_count']} 个 (Theta引擎 Covered Call)")
    print(f"期权权利金: ¥{plan['execution_plan']['options_total_premium']:,.0f}")

    # === v7.7: 打印对冲基金模块状态 ===
    hf = plan.get("hedge_fund_overlays", {})
    ks = hf.get("kill_switch", {})
    theta = hf.get("theta_engine", {})
    gamma = hf.get("gamma_vega_engine", {})
    liq = hf.get("liquidation_protocol", {})
    print("\n--- 对冲基金模块状态 ---")
    print(f"KillSwitch: L{ks.get('level', 0)} ({ks.get('level_name', '正常')}) | 保证金占用率: {ks.get('margin_usage_ratio', 0)*100:.1f}%")
    print(f"Theta引擎: {'ON' if theta.get('plan_loaded') else 'OFF'} | 头寸数: {theta.get('positions_count', 0)} | 月度收益: {theta.get('portfolio_yield_monthly', 0)*100:.2f}% | 年化: {theta.get('portfolio_yield_annualized', 0)*100:.2f}%")
    print(f"Gamma引擎: 触发={'是' if gamma.get('triggered') else '否'} | MA60={gamma.get('ma60_status')} | IV分位={gamma.get('iv_percentile')}")
    print(f"清仓阶段: Phase {liq.get('phase', 0)} ({liq.get('phase_name', '未知')}) | 距下一阶段: {liq.get('days_to_next', 'N/A')} 天")

    notes = hf.get("v77_notes", {})
    print(f"\n建仓许可: 现货={notes.get('spot_build_allowed')}, 期权={notes.get('options_orders_allowed')}")
    if notes.get("reason_if_blocked"):
        print(f"  阻断原因: {notes['reason_if_blocked']}")

    print("\n--- 前 5 订单 (上午) ---")
    for o in plan["execution_plan"]["morning_orders"][:5]:
        print(f"  {o['priority']}. {o['code']} {o['name']:<12} {o['shares']:>5} 股 @ {o['est_price']:<7.2f} = ¥{o['est_amount']:>10,.0f}")

    print("\n--- 期权订单 (Theta引擎 Covered Call) ---")
    for o in plan["execution_plan"]["options_orders"][:5]:
        print(f"  {o['code']} | 行权价 {o['strike']:.3f} (OTM {o['strike_otm_pct']*100:.1f}%) | {o['contracts']} 张 | 权利金 ¥{o['est_premium_total']:,.0f} | 年化 {o['annualized_return']*100:.2f}%")

    # === v7.7+: 打印期货期权对冲执行单 ===
    foh = plan.get("futures_options_hedge", {})
    if foh.get("loaded", False):
        timing = foh.get("execution_timing", {})
        budget = foh.get("budget_check", {})
        print("\n--- 期货期权对冲执行单 (v7.7+) ---")
        print(f"  组合Beta: {foh.get('portfolio_beta', 0.0):.4f}")
        print(f"  对冲比例: {foh.get('hedge_pct', 0.0)*100:.2f}%")
        print(f"  执行时机: 期权={timing.get('options_window')}, 期货={timing.get('futures_window')}")
        print(f"  资金预留: {timing.get('reserve_ratio', 0)*100:.0f}% (¥{timing.get('reserve_amount', 0):,.0f})")
        print(f"  订单数: {foh.get('orders_count', 0)}")
        print(f"  期权权利金合计: ¥{foh.get('total_premium', 0):,.0f}")
        print(f"  期货名义价值合计: ¥{foh.get('total_notional', 0):,.0f}")
        print("\n  预算检查:")
        print(f"    总成本: ¥{budget.get('total_cost', 0):,.0f}")
        print(f"    期权权利金: ¥{budget.get('total_premium', 0):,.0f}")
        print(f"    期货保证金: ¥{budget.get('futures_margin', 0):,.0f}")
        print(f"    避险资产: ¥{budget.get('safe_haven', 0):,.0f}")
        print(f"    剩余资金: ¥{budget.get('remaining', 0):,.0f}")
        print(f"    预算充足: {'是' if budget.get('within_budget', True) else '否'}")
        print("\n  对冲订单明细:")
        for i, o in enumerate(foh.get("orders", []), 1):
            o_type = o.get("type", "")
            action = o.get("action", "")
            instrument = o.get("instrument", "")
            window = o.get("execution_window", "")
            if o_type == "OPTIONS":
                print(f"    [{i}] {o_type} | {action} | {instrument} | 窗口: {window}")
                print(f"         张数: {o.get('contracts', 0)} | 权利金预算: ¥{o.get('premium_budget', 0):,.0f}")
            elif o_type == "FUTURES":
                print(f"    [{i}] {o_type} | {action} | {instrument} | 窗口: {window}")
                print(f"         手数: {o.get('contracts', 0)} | 名义价值: ¥{o.get('notional', 0):,.0f}")
            elif o_type == "SAFE_HAVEN":
                print(f"    [{i}] {o_type} | {action} | {instrument} | 窗口: {window}")
                print(f"         金额: ¥{o.get('amount', 0):,.0f}")


if __name__ == "__main__":
    main()
