"""
动态生成 trade_plan_{YYYYMMDD}.json — 基于 500万建仓计划 + 23 标的新权重
v7.7 增强: 集成对冲基金视角 (Theta/Gamma/KillSwitch/Liquidation)

用法:
    py -3 generate_daily_trade_plan.py [YYYY-MM-DD] [--capital 5000000]

默认:
    - 日期 = 下一个交易日 (跳过周末)
    - 资金 = 5,000,000

输出:
    trade_plans/trade_plan_{YYYYMMDD}.json

阶段逻辑 (集中建仓):
    7/6 ~ 7/31: 每个交易日 20 万现货, 共 20 个交易日, 总 400 万
    对冲资金: 100 万 (期货/期权, 不计入现货建仓)
    7 月末完成全部建仓

v7.7 对冲基金视角集成:
    - Theta引擎: 月度Covered Call订单注入 options_orders
    - Gamma引擎: 尾部对冲触发状态
    - KillSwitch: 熔断级别检查 (L1+触发则停止新开仓)
    - Liquidation: 2030清仓阶段检查
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
PLAN_DIR = BASE / "trade_plans"
PLAN_DIR.mkdir(exist_ok=True)

BUILD_PLAN_FILE = BASE.parent / "500万建仓计划_20260706.json"
REPORTS_DIR = BASE.parent / "reports"


def _get_real_vix_or_default(default: float = 18.5) -> float:
    """G13 修复 (2026-08-06): 从 VixDataSource 获取真实 VIX 替代值.

    硬编码 vix:18.5 已替换为真实数据源, 消除 VolRegime(7.24) 与 daily_pnl(18.5) 口径差异.
    获取失败时降级到默认值 18.5 (正常偏低).
    """
    try:
        _project_root = BASE.parent
        if str(_project_root) not in sys.path:
            sys.path.insert(0, str(_project_root))
        from utils.alpha.vix_data_source import fetch_vix

        vix = fetch_vix(use_cache=True)
        if vix is not None and 5.0 <= vix <= 150.0:
            return float(vix)
    except Exception:  # noqa: BLE001  # VIX 获取 fail-open
        pass
    return default


def _save_alpha_signals_for_drift(trade_date: str) -> None:
    """U9 深层根因修复 (2026-08-06): EOD 自动产出全量 alpha_signals 文件.

    根因: EOD 管道走 generate_daily_trade_plan.py, 不调用 institutional_pipeline_runner,
    导致 reports/pipeline/alpha_signals_*.json 不产出. DriftShadowIntegrator._load_latest_predictions()
    读取到过期/手动文件 (6 标的), observed 计数受限.

    修复: 从 positions.json 加载全量 26 标的, 用 AlphaFactorLibrary 计算因子分数,
    保存到 reports/pipeline/alpha_signals_{timestamp}.json, 供 DriftShadowIntegrator 读取.

    数据链路: positions.json (26标的) → fetch_prices (腾讯K线) → AlphaFactorLibrary.compute_all
             → Z-score标准化等权平均 → alpha_signals_{timestamp}.json

    验收: alpha_signals 文件含 26 标的信号, DriftShadowIntegrator n_predictions 从 6 增长到 26.
    """
    try:
        import numpy as np
        _project_root = BASE.parent
        if str(_project_root) not in sys.path:
            sys.path.insert(0, str(_project_root))

        from utils.alpha_factor.gate1_validation import fetch_prices
        from utils.alpha_factor.library import AlphaFactorLibrary
        from utils.positions_loader import get_positions_list

        # 1. 加载全量持仓标的 (26 个)
        positions = get_positions_list()
        symbols = [p.get("symbol", "") or p.get("code", "") for p in positions if isinstance(p, dict)]
        symbols = [s for s in symbols if s]
        if not symbols:
            return

        # 提取 6 位代码 (fetch_prices 需要)
        clean_symbols = []
        for sym in symbols:
            code = str(sym).strip()
            if "." in code:
                code = code.split(".", 1)[0]
            if len(code) > 6:
                code = code[-6:]
            clean_symbols.append(code)

        # 2. 拉取历史价格数据 (腾讯K线, 250日)
        price_data = fetch_prices(clean_symbols, days=250, use_cache=True)
        if not price_data:
            return

        # 3. 计算因子分数
        lib = AlphaFactorLibrary()
        factor_result = lib.compute_all(price_data=price_data)
        factors = getattr(factor_result, "factors", None)
        if not factors:
            return

        # 4. Z-score 标准化 + 等权平均 (复用 AlphaPipeline._factor_result_to_scores 逻辑)
        standardized = []
        for fval in factors.values():
            vals = getattr(fval, "values", None)
            if not vals:
                continue
            arr = np.array(list(vals.values()), dtype=float)
            if arr.std() > 1e-12:
                z = (arr - arr.mean()) / arr.std()
                standardized.append(dict(zip(vals.keys(), z.tolist())))

        if not standardized:
            return

        n = len(standardized)
        scores = {}
        for fvals in standardized:
            for sym, val in fvals.items():
                scores.setdefault(sym, 0.0)
                scores[sym] += val
        for sym in scores:
            scores[sym] /= n

        # 5. 裁剪到 [-1, 1]
        signals = {sym: float(np.clip(val, -1, 1)) for sym, val in scores.items() if sym in clean_symbols}
        confidence = {sym: 0.5 for sym in signals}

        # 6. 保存到 reports/pipeline/alpha_signals_{timestamp}.json
        report_dir = _project_root / "reports" / "pipeline"
        report_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report = {
            "model": "eod_alpha_signals_u9fix",
            "training_date": trade_date,
            "n_stocks": len(signals),
            "model_metrics": {"status": "ok", "source": "generate_daily_trade_plan_u9"},
            "signals": signals,
            "confidence": confidence,
        }
        report_path = report_dir / f"alpha_signals_{timestamp}.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    except Exception:  # noqa: BLE001  # fail-safe
        pass

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
    pass

MACRO_CUT_MIN_SCORE = 1.15
MACRO_CUT_FACTOR = 0.0
MACRO_WHITELIST_CODES = {"601088", "159915", "sh601088", "sz159915"}

def _load_hedge_execution_plan(trade_date: str, hedge_capital: float = 1_060_000) -> dict:
    """加载当日对冲执行单 (来自 hedge_execution_orders.py 生成的文件)

    Args:
        trade_date: 交易日期 YYYY-MM-DD
        hedge_capital: 对冲资金总额 (默认 ¥2,000,000)

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
        return result

    try:
        with open(hedge_file, encoding="utf-8") as f:
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
        within_budget = total_cost <= hedge_capital

        result["execution_timing"]["reserve_amount"] = reserve_amount

        result["budget_check"] = {
            "total_cost": total_cost,
            "total_premium": total_premium,
            "futures_margin": futures_margin,
            "safe_haven": total_safe_haven,
            "reserve_amount": reserve_amount,
            "remaining": remaining,
            "within_budget": within_budget,
        }

        if not within_budget:
            pass

        return result
    except Exception:
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
    pass


def _load_hedge_fund_overlays(trade_date: str) -> dict:
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
    overlays: dict = {
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
        overlays["kill_switch"] = {"available": False, "error": str(e), "build_allowed": True}

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
                    with open(theta_plan_path, encoding="utf-8") as f:
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
        overlays["liquidation"] = {"available": False, "error": str(e), "phase": 0, "build_allowed": True}

    return overlays


# ============================================================
# 建仓计划 (v8.0 顶级对冲基金重构)
# ============================================================
# 交易日数: 30 (7/6 周一 ~ 8/15 周五, 跳过周末)
# 每日建仓: 150,000 元 (现货)
# 总建仓金额: 4,500,000 元 (90%)
# 对冲资金: 1,500,000 元 (30%, 用于期货/期权)
# 目标: 年化>=8%, 回撤<=15%
PHASES = [
    {"phase": 1, "name": "集中建仓期",
     "start": "2026-07-06", "end": "2026-08-15",
     "capital_ratio": 0.90, "phase_capital": 4_500_000,
     "duration_days": 30, "daily_capital": 150_000,
     "strategy": "每日 15 万现货建仓 + 150 万对冲资金, 8/15 完成建仓"},
]


def next_trading_day(date: datetime) -> datetime:
    """获取下一个交易日 (跳过周末)"""
    d = date + timedelta(days=1)
    while d.weekday() >= 5:  # 5=周六, 6=周日
        d += timedelta(days=1)
    return d


def get_phase(date: datetime) -> dict:
    """根据日期判断当前阶段"""
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
            return {**p, "day_index": day_index}
    # 默认返回第一阶段
    return {**PHASES[0], "day_index": 1}


def load_build_plan() -> dict:
    """加载 500万建仓计划 (含 23 标的新权重)"""
    with open(BUILD_PLAN_FILE, encoding="utf-8") as f:
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


def _load_real_time_prices() -> dict[str, float]:
    """从 positions.json 加载实时价格"""
    positions_file = BASE.parent / "config" / "positions.json"
    if not positions_file.exists():
        return {}
    try:
        with open(positions_file, encoding="utf-8") as f:
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


def generate_orders(trade_date: str, phase: dict, build_plan: dict,
                    stock_capital: float = 3_000_000) -> dict:
    """生成当日买卖订单 (上午 + 下午批次)

    策略:
        - 每日建仓资金 = daily_capital (固定 20 万现货)
        - 按 23 标的权重分配
        - 每个标的按 100 股整数倍取整
        - 上午 50% / 下午 50%
    """
    real_time_prices = _load_real_time_prices()

    if "daily_capital" in phase:
        stock_day_capital = float(phase["daily_capital"])
    else:
        day_capital = phase["phase_capital"] / phase["duration_days"]
        stock_day_capital = day_capital * (stock_capital / (stock_capital + 1_060_000))

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
                pass
            target_portfolio = filtered_portfolio or target_portfolio

            if not target_portfolio:
                target_portfolio = build_plan.get("target_portfolio", {})
        except Exception:
            pass

    # 归一化剩余权重
    if target_portfolio:
        total_weight = sum(float(v.get("weight", 0)) for v in target_portfolio.values())
        if total_weight > 0:
            for info in target_portfolio.values():
                info["weight"] = round(float(info.get("weight", 0)) / total_weight, 6)

    morning_orders: list[dict] = []
    afternoon_orders: list[dict] = []
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

        shares * est_price
        round(est_price * 1.008, 3)  # +0.8% 限价缓冲

        # 上午/下午拆分 (50% / 50%)
        morning_shares = shares // 2
        afternoon_shares = shares - morning_shares

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


def generate_trade_plan(trade_date: str, capital: float = 5_000_000) -> dict:
    """生成完整 trade_plan 字典"""
    dt = datetime.strptime(trade_date, "%Y-%m-%d")
    weekday_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][dt.weekday()]
    phase = get_phase(dt)
    build_plan = load_build_plan()

    stock_capital = int(capital * 0.6)
    hedge_capital = int(capital * 0.4)

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
            "options_orders_allowed": True,  # Theta引擎订单一般可继续 (备兑无额外保证金)
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
            "version": "v7.7_institutional_hedge_fund",
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
            "vix": _get_real_vix_or_default(),
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
            "layers": {
                "layer1_futures": {
                    "action": "SHORT_FUTURES",
                    "ratio": 0.15,
                    "target_beta": 0.25,
                    "instrument": "IF (沪深300股指期货)",
                    "capital": int(hedge_capital * 0.5),
                },
                "layer2_options": {
                    "action": "PUT_SPREAD_COLLAR",
                    "long_put_strike": 0.95,
                    "short_put_strike": 0.85,
                    "short_call_strike": 1.10,
                    "capital": int(hedge_capital * 0.5),
                },
                "layer3_volatility": "监控模式 (IV/RV 偏离 > 5% 时小仓位试单)",
                "layer4_absolute_return": "准备配对池, 暂不交易",
                "layer5_covered_call": "v7.7 已由 Theta引擎自动生成月度计划" if overlays["theta"].get("plan_loaded") else "不启动 (建仓初期)",
            },
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
            "options_orders": overlays["options_orders"],
            "options_orders_count": len(overlays["options_orders"]),
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
        # === v7.7+: 期货期权对冲执行单 (集成到每日交易计划) ===
        "futures_options_hedge": {
            "loaded": hedge_exec_plan.get("loaded", False),
            "portfolio_beta": hedge_exec_plan.get("portfolio_beta", 0.0),
            "hedge_pct": hedge_exec_plan.get("hedge_pct", 0.0),
            "execution_timing": hedge_exec_plan.get("execution_timing", {
                "options_window": "09:30-10:00",
                "futures_window": "10:30-11:00",
                "reserve_ratio": 0.3,
                "reserve_amount": 0.0,
            }),
            "orders": hedge_exec_plan.get("hedge_orders", []),
            "orders_count": len(hedge_exec_plan.get("hedge_orders", [])),
            "total_premium": hedge_exec_plan.get("budget_check", {}).get("total_premium", 0),
            "total_notional": sum(o.get("notional", 0) for o in hedge_exec_plan.get("hedge_orders", []) if o.get("type") == "FUTURES"),
            "total_safe_haven": hedge_exec_plan.get("budget_check", {}).get("safe_haven", 0),
            "budget_check": hedge_exec_plan.get("budget_check", {}),
            "notes": [
                "期权执行时机: 开盘后30分钟内(09:30-10:00)完成期权买入, 避免权利金成本波动",
                "期货执行时机: 上午10:30-11:00完成IF期货开仓, 此时市场流动性较好",
                "资金预留: 对冲账户保留30%资金作为动态调整空间",
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


    plan = generate_trade_plan(trade_date, args.capital)

    # 保存 JSON
    date_compact = trade_date.replace("-", "")
    json_path = PLAN_DIR / f"trade_plan_{date_compact}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)


    # === U9 深层根因修复 (2026-08-06): EOD 自动产出全量 alpha_signals ===
    # 根因: EOD 管道走 generate_daily_trade_plan.py, 不调用 institutional_pipeline_runner,
    # 导致 reports/pipeline/alpha_signals_*.json 不产出, DriftShadowIntegrator 读取到
    # 过期/手动文件 (6 标的). 修复: trade_plan 生成后自动产出全量 26 标的 alpha_signals.
    try:
        _save_alpha_signals_for_drift(trade_date)
    except Exception as _e:  # noqa: BLE001  # fail-safe, 不阻断 trade_plan 生成
        pass


    # === v7.7: 打印对冲基金模块状态 ===
    hf = plan.get("hedge_fund_overlays", {})
    hf.get("kill_switch", {})
    hf.get("theta_engine", {})
    hf.get("gamma_vega_engine", {})
    hf.get("liquidation_protocol", {})

    notes = hf.get("v77_notes", {})
    if notes.get("reason_if_blocked"):
        pass

    for _o in plan["execution_plan"]["morning_orders"][:5]:
        pass

    for _o in plan["execution_plan"]["options_orders"][:5]:
        pass

    # === v7.7+: 打印期货期权对冲执行单 ===
    foh = plan.get("futures_options_hedge", {})
    if foh.get("loaded", False):
        foh.get("execution_timing", {})
        foh.get("budget_check", {})
        for _i, o in enumerate(foh.get("orders", []), 1):
            o_type = o.get("type", "")
            o.get("action", "")
            o.get("instrument", "")
            o.get("execution_window", "")
            if o_type == "OPTIONS" or o_type == "FUTURES" or o_type == "SAFE_HAVEN":
                pass


if __name__ == "__main__":
    main()
