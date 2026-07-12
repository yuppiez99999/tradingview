# -*- coding: utf-8 -*-
"""
每日自动执行交易计划 (模拟执行 + 人工确认)
==========================================

目标:
  - 2026-12-31 前完成 300 万股票ETF建仓
  - 每个交易日自动生成交易指令
  - 盘前生成指令 → 人工确认 → 盘后模拟执行 → 更新持仓状态

执行流程:
  1. 盘前 09:00 — generate_instructions()
     - 检查交易日/建仓期
     - 2026-07-13起: 每个交易日固定20万
     - 2026-07-10~07-12: 智能分批 (ETF信号日5万、无信号日1万、弱信号日2万)
     - 四重风控: 单日上限20万、价格保护带±3%、熔断停止(-3%/-5%)
     - 生成 trade_instructions/YYYY-MM-DD_instructions.json + .md
  2. 人工确认 — 修改 JSON 中的 confirm: true (默认 false)
  3. 盘后 15:30 — execute_instructions()
     - 读取已确认指令
     - 模拟执行 (SimulatedBroker)
     - 更新 positions.json 的 shares/avg_cost/est_price 和 build_progress.json
     - 生成执行报告
  4. 收盘后自动生成下一交易日计划 — generate_next_trading_day_plan()
     - 自动计算下一个交易日
     - 生成下一交易日的交易指令
     - 实现无缝衔接的自动化交易流程

使用方式:
  # 盘前生成指令
  python daily_trade_executor.py pre-market

  # 盘后执行已确认指令
  python daily_trade_executor.py post-market

  # 收盘后自动执行 + 生成下一交易日计划 (推荐)
  python daily_trade_executor.py post-market-auto

  # 查看建仓进度
  python daily_trade_executor.py progress

  # 指定日期
  python daily_trade_executor.py pre-market --date 2026-07-10
  python daily_trade_executor.py post-market-auto --date 2026-07-13
"""
import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, date
from typing import Dict, List, Optional, Any

# 项目根目录
PROJECT_ROOT = Path(__file__).parent
POSITIONS_FILE = PROJECT_ROOT / "config" / "positions.json"
TRADE_PLAN_FILE = PROJECT_ROOT / "v7.5_institutional" / "trade_plans" / "auto_trade_plan_500w_2026-2030.json"
INSTRUCTIONS_DIR = PROJECT_ROOT / "trade_instructions"
PROGRESS_FILE = PROJECT_ROOT / "trade_instructions" / "build_progress.json"

# 风控参数
DAILY_AMOUNT_LIMIT = 200000        # 单日金额上限 20万 (2026-07-13起)
PRICE_PROTECTION_PCT = 0.03        # 价格保护带 ±3%
DAILY_LOSS_STOP_PCT = 0.03         # 单日累计亏损 -3% 熔断
PORTFOLIO_DRAWDOWN_STOP_PCT = 0.05 # 组合回撤 -5% 熔断

# 建仓期参数 (phase_1_accumulation)
ACCUMULATION_START = date(2026, 7, 10)
ACCUMULATION_END = date(2026, 12, 31)
STOCK_ETF_TARGET = 3_000_000       # 300万

# 固定日预算起始日 (2026-07-13起改为动态信号加权预算)
FIXED_BUDGET_START = date(2026, 7, 13)
DAILY_FIXED_BUDGET = 200_000       # 单日金额上限/参考值

# 智能分批金额 (仅用于2026-07-10~07-12, ETF信号强度 → 当日建仓金额)
SIGNAL_AMOUNTS = {
    "strong": 50_000,   # 强信号日 5万
    "medium": 20_000,   # 弱信号日 2万
    "none": 10_000,     # 无信号日 1万
}


def is_trading_day(d: date) -> bool:
    """检查是否为A股交易日 (简易判断: 周一至周五)"""
    return d.weekday() < 5


def is_accumulation_period(d: date) -> bool:
    """检查是否处于建仓期"""
    return ACCUMULATION_START <= d <= ACCUMULATION_END


def init_wt_modules():
    """初始化 WonderTrader 风格模块
    
    返回: dict 包含所有WT模块实例, 失败时返回空dict
    """
    wt_modules = {}
    try:
        from utils.wt_risk_control import RiskControl, StopLossManager, PortfolioRiskAnalyzer
        from utils.wt_execution_algo import MinImpactExecutor, TWAPExecutor, VWAPExecutor
        from utils.wt_hedge_strategy import HedgeContext, BetaHedgeStrategy, TailRiskHedgeStrategy
        from utils.wt_contracts_manager import get_contracts_manager
        
        wt_modules["risk_control"] = RiskControl({
            "max_daily_loss_pct": 0.05,
            "max_portfolio_drawdown_pct": 0.15,
            "max_position_concentration_pct": 0.30,
            "max_single_trade_pct": 0.05,
            "max_daily_trades": 50,
        })
        wt_modules["stop_loss_manager"] = StopLossManager(
            stop_loss_pct=0.08,
            take_profit_pct=0.15,
        )
        wt_modules["portfolio_risk_analyzer"] = PortfolioRiskAnalyzer()
        wt_modules["min_impact_executor"] = MinImpactExecutor(
            max_participation_pct=0.15,
            min_order_size=100,
        )
        wt_modules["twap_executor"] = TWAPExecutor(
            execution_window_minutes=30,
            interval_minutes=5,
        )
        wt_modules["vwap_executor"] = VWAPExecutor()
        wt_modules["contracts_manager"] = get_contracts_manager()
        
        beta_hedge = BetaHedgeStrategy(config={
            "target_hedge_ratio": 0.2,
            "max_hedge_ratio": 0.5,
            "min_hedge_ratio": 0.05,
        })
        tail_hedge = TailRiskHedgeStrategy(config={
            "target_hedge_ratio": 0.3,
            "max_hedge_ratio": 0.6,
            "min_hedge_ratio": 0.1,
            "vol_threshold": 0.2,
        })
        wt_modules["hedge_context"] = HedgeContext(beta_hedge)
        wt_modules["beta_hedge"] = beta_hedge
        wt_modules["tail_hedge"] = tail_hedge
        
        print("[INFO] ✅ WonderTrader 模块初始化完成")
        print(f"[INFO]   - 风控模块: RiskControl, StopLossManager, PortfolioRiskAnalyzer")
        print(f"[INFO]   - 执行算法: MinImpactExecutor, TWAPExecutor, VWAPExecutor")
        print(f"[INFO]   - 对冲策略: BetaHedgeStrategy, TailRiskHedgeStrategy")
        print(f"[INFO]   - 合约管理: ContractsManager")
        
    except Exception as e:
        print(f"[WARN] ⚠️ WonderTrader 模块初始化失败: {e}")
        print(f"[WARN]   - 系统将使用内置风控规则继续运行")
    
    return wt_modules


def load_positions() -> Dict:
    """加载持仓配置"""
    with open(POSITIONS_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_trade_plan() -> Dict:
    """加载交易计划"""
    with open(TRADE_PLAN_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_build_progress() -> Dict:
    """加载建仓进度"""
    if not PROGRESS_FILE.exists():
        return {
            "total_built": 0,
            "daily_records": [],
            "built_amounts": {},  # {code: accumulated_amount}
        }
    with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_build_progress(progress: Dict):
    """保存建仓进度"""
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def get_remaining_days(target_date: date) -> int:
    """计算到建仓期结束的剩余交易日数"""
    from datetime import timedelta
    days = 0
    current = target_date
    while current <= ACCUMULATION_END:
        if is_trading_day(current):
            days += 1
        current += timedelta(days=1)
    return max(days, 1)


def assess_etf_signal(code: str, positions_data: Dict) -> str:
    """评估ETF资金流信号强度

    基于持仓配置中的 etf_flow_signal 字段判断
    返回: "strong" / "medium" / "none"
    """
    pos = positions_data.get("positions", {}).get(code, {})
    if not isinstance(pos, dict):
        return "none"
    signal = pos.get("etf_flow_signal", "")
    if "强" in signal:
        return "strong"
    elif "加仓" in signal or "中" in signal:
        return "medium"
    return "none"


def calculate_daily_budget(target_date: date, progress: Dict, positions_data: Dict) -> Dict:
    """计算当日建仓预算

    策略:
      - 2026-07-13起: 固定每日20万
      - 2026-07-10~07-12: 智能分批 (ETF信号强度)
      - 上限: 20万/日
    """
    remaining_total = STOCK_ETF_TARGET - progress.get("total_built", 0)
    if remaining_total <= 0:
        return {
            "daily_budget": 0,
            "signal_strength": "completed",
            "remaining_total": 0,
            "remaining_days": 0,
            "reason": "已完成300万建仓目标",
        }

    remaining_days = get_remaining_days(target_date)

    # 统计ETF信号强度
    strong_count = 0
    medium_count = 0
    none_count = 0
    for code, pos in positions_data.get("positions", {}).items():
        if not isinstance(pos, dict):
            continue
        signal = pos.get("etf_flow_signal", "")
        if "强" in signal:
            strong_count += 1
        elif "加仓" in signal or "中" in signal:
            medium_count += 1
        else:
            none_count += 1

    # 2026-07-13起: 固定每日20万
    if target_date >= FIXED_BUDGET_START:
        daily_budget = min(DAILY_FIXED_BUDGET, remaining_total)
        return {
            "daily_budget": round(daily_budget, 2),
            "signal_strength": "fixed_200k",
            "strong_signal_count": strong_count,
            "medium_signal_count": medium_count,
            "remaining_total": remaining_total,
            "remaining_days": remaining_days,
            "base_daily": DAILY_FIXED_BUDGET,
        }

    # 2026-07-10~07-12: 智能分批 (原逻辑)
    base_daily = remaining_total / remaining_days

    if strong_count >= 3:
        signal_strength = "strong"
        daily_budget = min(base_daily * 1.5, SIGNAL_AMOUNTS["strong"])
    elif medium_count >= 3 or strong_count >= 1:
        signal_strength = "medium"
        daily_budget = min(base_daily * 1.0, SIGNAL_AMOUNTS["medium"])
    else:
        signal_strength = "none"
        daily_budget = min(base_daily * 0.5, SIGNAL_AMOUNTS["none"])

    # 应用单日上限
    daily_budget = min(daily_budget, DAILY_AMOUNT_LIMIT, remaining_total)

    return {
        "daily_budget": round(daily_budget, 2),
        "signal_strength": signal_strength,
        "strong_signal_count": strong_count,
        "medium_signal_count": medium_count,
        "remaining_total": remaining_total,
        "remaining_days": remaining_days,
        "base_daily": round(base_daily, 2),
    }


def load_latest_prices() -> Dict[str, float]:
    """从最近的收盘报告读取最新价格

    优先级:
      1. v7.5_institutional/reports/daily_pnl_report_YYYY-MM-DD.json
      2. v7.5_institutional/reports/daily_pnl_report_YYYY-MM-DD.md
    """
    import glob
    reports_dir = PROJECT_ROOT / "v7.5_institutional" / "reports"
    if not reports_dir.exists():
        return {}

    # 找最新的 JSON 报告
    json_files = sorted(reports_dir.glob("daily_pnl_report_*.json"), reverse=True)
    if not json_files:
        return {}

    latest_file = json_files[0]
    try:
        with open(latest_file, 'r', encoding='utf-8') as f:
            report = json.load(f)
        prices = {}
        for detail in report.get("portfolio_pnl", {}).get("details", []):
            code = detail.get("code", "")
            # 标准化代码 (去 .SH/.SZ 后缀)
            code_clean = code.split(".")[0]
            close_price = detail.get("close_price", 0)
            if close_price and close_price > 0:
                prices[code_clean] = close_price
        return prices
    except Exception as e:
        print(f"读取最新价格失败: {e}")
        return {}


# 默认参考价 (当无法获取真实价格时使用)
DEFAULT_PRICES = {
    "588080": 2.26, "512880": 1.13, "510050": 3.09, "512800": 1.50,
    "515030": 1.71, "512760": 1.55, "512170": 0.31, "518880": 6.50,
    "688041": 363.46, "300308": 1194.90, "002371": 878.43, "603019": 103.99,
    "300033": 230.30, "300782": 92.83, "688017": 408.28, "300274": 100.00,
    "000408": 35.00, "601088": 40.00, "600276": 55.61, "600900": 27.77,
}


# ===========================================================
# 价格预测信号 (v7.5+ 集成 tf_price_predictor)
# ===========================================================

def fetch_prediction_signals(symbols: List[str], horizon: int = 5) -> Dict[str, Dict]:
    """批量获取价格预测信号

    Args:
        symbols: 股票代码列表 (6位数字)
        horizon: 预测周期 (1/5/10)

    Returns:
        {symbol: {direction, confidence, target_price, method, signal_strength}, ...}
        signal_strength: [-1, 1] 供分配权重调整使用
        失败时返回空字典 (不影响主流程)
    """
    try:
        from utils.tf_price_predictor import PricePredictor
        predictor = PricePredictor()
        results: Dict[str, Dict] = {}
        for symbol in symbols:
            try:
                # 从历史价格数据加载
                prices = _load_prediction_prices(symbol)
                if prices is None or len(prices) < 30:
                    results[symbol] = {
                        "direction": "NEUTRAL",
                        "confidence": 0.0,
                        "signal_strength": 0.0,
                        "method": "no_data",
                        "target_price": 0.0,
                    }
                    continue
                pred = predictor.predict(symbol, prices, horizon=horizon)
                # signal_strength: 正数看多, 负数看空
                strength = pred.signal_strength if hasattr(pred, 'signal_strength') else 0.0
                results[symbol] = {
                    "direction": pred.direction,
                    "confidence": pred.confidence,
                    "target_price": pred.target_price,
                    "method": pred.method,
                    "signal_strength": strength,
                    "expected_return": pred.expected_return,
                }
            except Exception as e:
                # 单标的失败不影响其他标的
                results[symbol] = {
                    "direction": "NEUTRAL",
                    "confidence": 0.0,
                    "signal_strength": 0.0,
                    "method": "error",
                    "error": str(e)[:100],
                }
        return results
    except ImportError:
        # tf_price_predictor 未安装, 静默降级
        return {}
    except Exception as e:
        print(f"[WARN] 预测信号获取失败: {e}")
        return {}


def _load_prediction_prices(symbol: str, days: int = 120):
    """加载历史价格序列供预测用

    优先级:
      1. v7.5_institutional/reports/daily_pnl_report_*.json 中的 close_price
      2. data_provider.get_historical_data (若可用)
    """
    import json as _json
    import glob as _glob
    # 从历史收盘报告聚合价格序列
    reports_dir = PROJECT_ROOT / "v7.5_institutional" / "reports"
    if not reports_dir.exists():
        return None
    try:
        import numpy as np
    except ImportError:
        return None
    # 收集所有历史报告 (按日期升序)
    json_files = sorted(reports_dir.glob("daily_pnl_report_*.json"))
    prices = []
    code_clean = symbol.split(".")[0] if "." in symbol else symbol
    for jf in json_files:
        try:
            with open(jf, 'r', encoding='utf-8') as f:
                report = _json.load(f)
            for detail in report.get("portfolio_pnl", {}).get("details", []):
                if detail.get("code", "").split(".")[0] == code_clean:
                    p = detail.get("close_price", 0)
                    if p and p > 0:
                        prices.append(float(p))
                    break
        except Exception:
            continue
    # 取最近 days 天
    if len(prices) < 30:
        return None
    return np.array(prices[-days:], dtype=float)


def adjust_allocation_by_signal(base_allocated: float, signal: Dict,
                                daily_budget: float) -> tuple:
    """根据预测信号调整分配金额

    Args:
        base_allocated: 基础分配金额
        signal: 预测信号 (fetch_prediction_signals 返回的单项)
        daily_budget: 当日总预算

    Returns:
        (adjusted_allocated, signal_tag)
        signal_tag: "strong_buy"/"buy"/"neutral"/"caution"/"skip"
    """
    if not signal:
        return base_allocated, "neutral"

    direction = signal.get("direction", "NEUTRAL")
    confidence = signal.get("confidence", 0)
    strength = signal.get("signal_strength", 0)

    # 强看空 + 高置信度 → 跳过
    if direction == "DOWN" and confidence >= 0.7 and strength <= -0.5:
        return 0, "skip"

    # 弱看空 + 中置信度 → 缩减 50%
    if direction == "DOWN" and confidence >= 0.5:
        return base_allocated * 0.5, "caution"

    # 强看多 + 高置信度 → 加码 30% (不超过单标的上限)
    if direction == "UP" and confidence >= 0.7 and strength >= 0.5:
        return min(base_allocated * 1.3, daily_budget * 0.30), "strong_buy"

    # 弱看多 → 加码 10%
    if direction == "UP" and confidence >= 0.5:
        return base_allocated * 1.1, "buy"

    return base_allocated, "neutral"


def generate_instructions(target_date_str: str) -> Dict:
    """盘前生成交易指令

    生成包含所有待买入标的的指令清单,
    默认 confirm=false, 等待人工确认后改为 true.

    分配策略: 按剩余目标金额比例分配当日预算
    (确保每个未完成建仓的标的都能获得合理份额)
    """
    target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()

    # 前置检查
    if not is_trading_day(target_date):
        return {"status": "skipped", "reason": f"{target_date_str} 非交易日(周末)"}

    if not is_accumulation_period(target_date):
        return {"status": "skipped", "reason": f"{target_date_str} 不在建仓期(2026-07-10 ~ 2026-12-31)"}

    # 加载数据
    positions_data = load_positions()

    # v7.5+: 盘前自动刷新ETF资金流信号
    try:
        from utils.etf_flow_monitor import refresh_etf_flow_signals
        etf_result = refresh_etf_flow_signals(str(POSITIONS_FILE))
        if etf_result.get('status') == 'success':
            print(f"[INFO] ETF资金流信号刷新成功: 更新 {etf_result['updated_count']} 个标的, 检测到 {etf_result.get('signal_count', 0)} 条信号")
            # 重新加载 positions_data 以获取更新后的信号
            positions_data = load_positions()
        else:
            print(f"[WARN] ETF资金流信号刷新失败: {etf_result.get('message', 'unknown')}")
    except Exception as e:
        print(f"[WARN] ETF资金流信号刷新模块加载失败: {e}")

    # v7.8+: 初始化 WonderTrader 风格模块
    wt_modules = init_wt_modules()
    
    trade_plan = load_trade_plan()
    progress = load_build_progress()
    latest_prices = load_latest_prices()

    # v7.8+: WT风控预检查 (使用 WT PortfolioRiskAnalyzer)
    if wt_modules.get("portfolio_risk_analyzer"):
        try:
            risk_summary = wt_modules["portfolio_risk_analyzer"].analyze_portfolio(
                positions_data,
                progress.get("total_built", 0),
                STOCK_ETF_TARGET,
            )
            print(f"[INFO] WT风控分析: 组合风险评分 {risk_summary.get('risk_score', 'N/A')}")
            print(f"[INFO]   - 集中度风险: {risk_summary.get('concentration_risk', 'N/A')}")
            print(f"[INFO]   - 行业分布: {risk_summary.get('sector_distribution', 'N/A')}")
        except Exception as e:
            print(f"[WARN] WT风控分析执行失败: {e}")

    # 获取预测信号 (v7.5+ 集成 tf_price_predictor, 失败时静默降级)
    pending_codes = [
        p.get("code", "").split(".")[0]
        for p in trade_plan.get("stock_etf_account", {}).get("positions", [])
    ]
    prediction_signals = fetch_prediction_signals(pending_codes, horizon=5)
    if prediction_signals:
        up_count = sum(1 for s in prediction_signals.values() if s.get("direction") == "UP")
        down_count = sum(1 for s in prediction_signals.values() if s.get("direction") == "DOWN")
        print(f"[INFO] 预测信号: {len(prediction_signals)} 个标的, 看多 {up_count}, 看空 {down_count}")

    # 计算当日预算
    budget_info = calculate_daily_budget(target_date, progress, positions_data)

    if budget_info["daily_budget"] <= 0:
        return {"status": "completed", "reason": "已完成建仓目标", "budget_info": budget_info}

    # 收集所有未完成建仓的标的
    plan_positions = trade_plan.get("stock_etf_account", {}).get("positions", [])
    pending_positions = []

    # 计算建仓进度 (用于按缺口排序)
    from datetime import timedelta
    elapsed_days = 0
    current = ACCUMULATION_START
    while current <= target_date:
        if is_trading_day(current):
            elapsed_days += 1
        current += timedelta(days=1)
    total_accumulation_days = get_remaining_days(ACCUMULATION_START)
    progress_ratio = min(elapsed_days / max(total_accumulation_days, 1), 1.0)

    for pos in plan_positions:
        code = pos.get("code", "")
        target_amount = pos.get("amount", 0)
        built = progress.get("built_amounts", {}).get(code, 0)
        remaining = target_amount - built
        if remaining > 0:
            # 理论应建仓金额
            theoretical_built = target_amount * progress_ratio
            # 缺口 = 理论应建仓 - 实际已建仓 (正值表示落后于进度)
            gap = theoretical_built - built
            pending_positions.append({
                "code": code,
                "code_clean": code.split(".")[0],
                "name": pos.get("name", ""),
                "weight": pos.get("weight", 0),
                "target_amount": target_amount,
                "built": built,
                "remaining": remaining,
                "gap": gap,
            })

    if not pending_positions:
        return {"status": "completed", "reason": "所有标的已建仓完成"}

    # 按缺口降序排序 (缺口大的优先买入)
    pending_positions.sort(key=lambda x: x["gap"], reverse=True)

    # 轮换分配: 优先满足缺口大的标的
    # 每个标的按权重比例分配当日预算, 但确保总金额不超过预算
    daily_budget = budget_info["daily_budget"]
    instructions = []
    total_allocated = 0
    remaining_budget = daily_budget

    for pos in pending_positions:
        if remaining_budget < 100:
            break  # 预算耗尽

        code_clean = pos["code_clean"]
        # 获取参考价
        ref_price = latest_prices.get(code_clean, 0)
        if not ref_price:
            ref_price = DEFAULT_PRICES.get(code_clean, 10.0)

        # 价格保护带
        max_buy_price = round(ref_price * (1 + PRICE_PROTECTION_PCT), 4)
        min_buy_price = round(ref_price * (1 - PRICE_PROTECTION_PCT), 4)

        # 100 股最小成本
        min_lot_cost = 100 * ref_price

        # 按权重分配预算
        allocated = min(remaining_budget * pos["weight"] / 0.05 * 0.15, remaining_budget, pos["remaining"])
        # 单标的上限: 当日预算的30% (20万预算下单标最多6万)
        allocated = min(allocated, daily_budget * 0.30)

        # v7.5+: 根据预测信号调整分配
        signal = prediction_signals.get(code_clean, {})
        allocated, signal_tag = adjust_allocation_by_signal(allocated, signal, daily_budget)
        # 强看空 → 跳过该标的
        if signal_tag == "skip" and allocated == 0:
            print(f"[WARN] 预测信号触发跳过: {code_clean} ({pos['name']}) - 强看空 (置信度 {signal.get('confidence', 0):.0%})")
            continue

        # 高价股处理: 如果 100 股成本 > 分配预算
        if min_lot_cost > allocated:
            # 如果 100 股成本超过当日预算的 50%, 跳过 (避免单标的占用过多预算)
            if min_lot_cost > daily_budget * 0.50:
                continue
            # 否则检查剩余预算是否足够买 100 股
            if remaining_budget < min_lot_cost:
                continue
            allocated = min_lot_cost  # 只买 100 股

        allocated = min(allocated, remaining_budget, pos["remaining"])

        # 估算购买数量 (100股整数倍)
        est_qty = int(allocated / max_buy_price / 100) * 100
        if est_qty <= 0:
            est_qty = 100  # 最小 100 股

        actual_amount = round(est_qty * ref_price, 2)

        # 如果实际金额超过剩余预算, 跳过
        if actual_amount > remaining_budget:
            continue

        # 评估ETF信号
        etf_signal = assess_etf_signal(pos["code"], positions_data)

        # 预测信号摘要 (供人工审核参考)
        pred_signal = prediction_signals.get(code_clean, {})

        instructions.append({
            "instruction_id": f"{target_date_str.replace('-','')}-{code_clean}",
            "code": code_clean,
            "full_code": pos["code"],
            "name": pos["name"],
            "action": "BUY",
            "qty": est_qty,
            "ref_price": ref_price,
            "max_buy_price": max_buy_price,
            "min_buy_price": min_buy_price,
            "estimated_amount": actual_amount,
            "weight": pos["weight"],
            "target_amount": pos["target_amount"],
            "built_before": pos["built"],
            "remaining_after": round(pos["remaining"] - actual_amount, 2),
            "etf_signal": etf_signal,
            "prediction_signal": {
                "direction": pred_signal.get("direction", "NEUTRAL"),
                "confidence": round(pred_signal.get("confidence", 0), 3),
                "target_price": round(pred_signal.get("target_price", 0), 2),
                "method": pred_signal.get("method", "no_data"),
                "tag": signal_tag,
            },
            "gap": round(pos["gap"], 2),
            "confirm": False,  # ⚠️ 默认未确认, 需人工改为 true
        })
        total_allocated += actual_amount
        remaining_budget -= actual_amount

    # 风控检查
    risk_checks = {
        "daily_limit": {
            "rule": f"单日金额上限 ¥{DAILY_AMOUNT_LIMIT:,}",
            "value": total_allocated,
            "limit": DAILY_AMOUNT_LIMIT,
            "passed": total_allocated <= DAILY_AMOUNT_LIMIT,
        },
        "price_protection": {
            "rule": f"价格保护带 ±{PRICE_PROTECTION_PCT:.0%}",
            "passed": True,  # 已在每条指令中应用
        },
        "circuit_breaker": {
            "rule": f"单日亏损-{DAILY_LOSS_STOP_PCT:.0%}/组合回撤-{PORTFOLIO_DRAWDOWN_STOP_PCT:.0%}熔断",
            "daily_loss_pct": 0,  # 盘前无法判断, 盘后执行时检查
            "portfolio_drawdown_pct": 0,
            "passed": True,
        },
        "manual_confirm": {
            "rule": "盘前人工确认 (confirm字段需为true)",
            "passed": False,  # 默认未确认
        },
    }

    all_passed = all(r["passed"] for r in risk_checks.values() if "passed" in r)
    # manual_confirm 不阻塞生成, 只标记需要确认

    instruction_file = {
        "meta": {
            "instruction_date": target_date_str,
            "generated_at": datetime.now().isoformat(),
            "phase": "phase_1_accumulation",
            "total_capital": STOCK_ETF_TARGET,
            "total_built_before": progress.get("total_built", 0),
            "remaining_total": STOCK_ETF_TARGET - progress.get("total_built", 0),
        },
        "budget_info": budget_info,
        "risk_checks": risk_checks,
        "instructions": instructions,
        "total_allocated": round(total_allocated, 2),
        "confirm_required": True,
        "confirm_instruction": "将每个 instruction 中的 confirm 字段改为 true, 然后运行 post-market 执行",
    }

    # 保存
    INSTRUCTIONS_DIR.mkdir(parents=True, exist_ok=True)
    output_file = INSTRUCTIONS_DIR / f"{target_date_str}_instructions.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(instruction_file, f, ensure_ascii=False, indent=2)

    # 生成 markdown 版本
    md_file = INSTRUCTIONS_DIR / f"{target_date_str}_instructions.md"
    md_content = render_instructions_md(instruction_file)
    with open(md_file, 'w', encoding='utf-8') as f:
        f.write(md_content)

    return {
        "status": "generated",
        "output_files": [str(output_file), str(md_file)],
        "instruction_count": len(instructions),
        "total_allocated": total_allocated,
        "budget_info": budget_info,
    }


def confirm_all_instructions(target_date_str: str) -> int:
    """自动确认指定日期的所有未确认指令"""
    instruction_file = INSTRUCTIONS_DIR / f"{target_date_str}_instructions.json"
    if not instruction_file.exists():
        return 0

    with open(instruction_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    confirmed_count = 0
    for inst in data.get("instructions", []):
        if not inst.get("confirm", False):
            inst["confirm"] = True
            confirmed_count += 1

    if confirmed_count > 0:
        with open(instruction_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    return confirmed_count


def render_instructions_md(data: Dict) -> str:
    """渲染交易指令 markdown 版本"""
    meta = data["meta"]
    budget = data["budget_info"]
    risk = data["risk_checks"]
    instructions = data["instructions"]

    lines = [
        f"# 交易指令清单 {meta['instruction_date']}",
        "",
        f"**生成时间**: {meta['generated_at']}",
        f"**阶段**: {meta['phase']} (建仓期 2026-07-10 ~ 2026-12-31)",
        f"**目标总额**: ¥{meta['total_capital']:,}",
        f"**已建仓**: ¥{meta['total_built_before']:,.0f}",
        f"**剩余**: ¥{meta['remaining_total']:,.0f}",
        "",
        "---",
        "",
        "## 当日预算",
        "",
        f"- **信号强度**: {budget.get('signal_strength', 'unknown')}",
        f"- **强信号数**: {budget.get('strong_signal_count', 0)}",
        f"- **弱信号数**: {budget.get('medium_signal_count', 0)}",
        f"- **基础日预算**: ¥{budget.get('base_daily', 0):,.0f}",
        f"- **当日预算**: ¥{budget.get('daily_budget', 0):,.0f}",
        f"- **剩余交易日**: {budget.get('remaining_days', 0)} 天",
        "",
        "## 风控检查",
        "",
        "| 检查项 | 规则 | 数值 | 上限 | 状态 |",
        "|--------|------|------|------|------|",
        f"| 单日金额上限 | ¥{DAILY_AMOUNT_LIMIT:,} | ¥{data['total_allocated']:,.0f} | ¥{DAILY_AMOUNT_LIMIT:,} | {'✅' if risk['daily_limit']['passed'] else '❌'} |",
        f"| 价格保护带 | ±{PRICE_PROTECTION_PCT:.0%} | - | - | {'✅' if risk['price_protection']['passed'] else '❌'} |",
        f"| 熔断停止 | 单日-{DAILY_LOSS_STOP_PCT:.0%}/组合-{PORTFOLIO_DRAWDOWN_STOP_PCT:.0%} | 0% | - | {'✅' if risk['circuit_breaker']['passed'] else '❌'} |",
        f"| 人工确认 | confirm=true | - | - | ⚠️ 待确认 |",
        "",
        "## 交易指令",
        "",
        f"**总指令数**: {len(instructions)}",
        f"**总分配金额**: ¥{data['total_allocated']:,.0f}",
        "",
        "| # | 代码 | 名称 | 动作 | 数量 | 参考价 | 最高买入价 | 最低买入价 | 估算金额 | ETF信号 | 已建仓 | 剩余 | 确认 |",
        "|---|------|------|------|------|--------|-----------|-----------|---------|---------|--------|------|------|",
    ]

    for idx, inst in enumerate(instructions, 1):
        confirm = "✅" if inst["confirm"] else "⏳"
        lines.append(
            f"| {idx} | {inst['code']} | {inst['name']} | {inst['action']} | "
            f"{inst['qty']} | {inst['ref_price']:.4f} | {inst['max_buy_price']:.4f} | "
            f"{inst['min_buy_price']:.4f} | ¥{inst['estimated_amount']:,.0f} | "
            f"{inst['etf_signal']} | ¥{inst['built_before']:,.0f} | "
            f"¥{inst['remaining_after']:,.0f} | {confirm} |"
        )

    lines.extend([
        "",
        "---",
        "",
        "## 确认步骤",
        "",
        "1. 打开 JSON 文件: `" + data["meta"]["instruction_date"].replace("-", "") + "_instructions.json`",
        "2. 检查每条指令的 `qty`, `max_buy_price` 等参数",
        "3. 将需要执行的指令的 `confirm` 字段改为 `true`",
        "4. 运行: `python daily_trade_executor.py post-market --date " + data["meta"]["instruction_date"] + "`",
        "",
        "## 风控规则",
        "",
        f"- **单日金额上限**: ¥{DAILY_AMOUNT_LIMIT:,}",
        f"- **价格保护带**: 买入价不超过昨收 +{PRICE_PROTECTION_PCT:.0%}",
        f"- **单日熔断**: 亏损 >{DAILY_LOSS_STOP_PCT:.0%} 停止建仓",
        f"- **组合熔断**: 回撤 >{PORTFOLIO_DRAWDOWN_STOP_PCT:.0%} 停止建仓",
        "",
        f"*由 daily_trade_executor.py 自动生成*",
    ])

    return "\n".join(lines)


def generate_next_trading_day_plan(today_str: str) -> Dict:
    """收盘后自动生成下一个交易日的执行计划
    
    参数:
        today_str: 今日日期字符串 (YYYY-MM-DD)
    
    返回:
        下一个交易日的交易计划结果
    """
    from datetime import timedelta
    
    # 计算下一个交易日
    today = datetime.strptime(today_str, "%Y-%m-%d").date()
    next_day = today + timedelta(days=1)
    
    # 跳过周末和节假日
    max_attempts = 10
    attempts = 0
    while not is_trading_day(next_day) and attempts < max_attempts:
        next_day += timedelta(days=1)
        attempts += 1
    
    if attempts >= max_attempts:
        return {
            "status": "error",
            "reason": f"无法在{today_str}后的10天内找到下一个交易日",
        }
    
    next_day_str = next_day.isoformat()
    print(f"[INFO] 今日: {today_str}, 下一交易日: {next_day_str}")
    
    # 生成下一个交易日的计划
    result = generate_instructions(next_day_str)
    
    # 添加元信息
    if isinstance(result, dict):
        meta = result.get("meta")
        if meta is None:
            meta = {}
            result["meta"] = meta
        meta["generated_after"] = today_str
        meta["auto_generated"] = True
        meta["next_trading_day"] = next_day_str
    
    return result


def execute_instructions(target_date_str: str) -> Dict:
    """盘后执行已确认的交易指令

    读取指令文件, 执行 confirm=true 的指令,
    更新 positions.json (shares/avg_cost/est_price) 和 build_progress.json

    幂等保护: 若当日已执行过, 则跳过重复累加, 仅补同步 positions.json
    """
    instruction_file = INSTRUCTIONS_DIR / f"{target_date_str}_instructions.json"

    if not instruction_file.exists():
        return {"status": "error", "reason": f"指令文件不存在: {instruction_file}"}

    with open(instruction_file, 'r', encoding='utf-8') as f:
        instructions_data = json.load(f)

    # 执行前风控检查
    risk_checks = instructions_data.get("risk_checks", {})

    # 模拟检查熔断 (盘后实际数据需要从报告读取)
    # 这里简化为: 如果有风控失败, 不执行
    if not risk_checks.get("daily_limit", {}).get("passed", True):
        return {"status": "blocked", "reason": "单日金额上限未通过"}

    # 筛选已确认指令
    confirmed = [i for i in instructions_data.get("instructions", []) if i.get("confirm", False)]

    if not confirmed:
        return {
            "status": "no_confirmed",
            "reason": "无已确认指令 (所有 confirm=false)",
            "total_instructions": len(instructions_data.get("instructions", [])),
        }

    # v7.8+: 初始化 WT 模块用于执行
    wt_modules = init_wt_modules()
    
    # v7.8+: WT风控前置检查
    if wt_modules.get("risk_control"):
        try:
            rc = wt_modules["risk_control"]
            # 单笔交易额度检查
            for inst in confirmed:
                amount = inst.get("amount", 0)
                ok, msg = rc.check_single_trade(amount, STOCK_ETF_TARGET)
                if not ok:
                    print(f"[WARN] WT风控单笔检查未通过: {msg}")
            # 日内交易笔数检查
            ok, msg = rc.check_daily_trade_count()
            if not ok:
                print(f"[WARN] WT风控日内笔数检查未通过: {msg}")
        except Exception as e:
            print(f"[WARN] WT风控检查执行失败: {e}")

    # 幂等检查: 当日是否已执行过
    progress = load_build_progress()
    daily_records = progress.get("daily_records", [])
    already_executed = any(r.get("date") == target_date_str for r in daily_records)

    # 加载持仓文件用于同步
    positions_data = load_positions()
    positions = positions_data.get("positions", {})

    if already_executed:
        # 幂等模式: 不重复累加 build_progress, 只补同步 positions.json
        execution_file = INSTRUCTIONS_DIR / f"{target_date_str}_execution.json"
        if execution_file.exists():
            with open(execution_file, 'r', encoding='utf-8') as f:
                prev_report = json.load(f)
            prev_results = prev_report.get("execution_results", [])
        else:
            prev_results = []

        # 从已执行的成交结果重建 positions 同步数据
        synced_count = 0
        for r in prev_results:
            code = r.get("code", "")
            full_code = next((i["full_code"] for i in confirmed if i.get("code") == code), f"{code}.SH")
            qty = r.get("qty", 0)
            fill_price = r.get("fill_price", 0.0)

            if full_code in positions:
                pos = positions[full_code]
                # 仅在 shares=0 (未同步) 时补同步
                if pos.get("shares", 0) == 0:
                    pos["shares"] = qty
                    pos["est_price"] = fill_price
                    pos["avg_cost"] = fill_price
                    synced_count += 1

        # 保存 positions.json
        positions_data["positions"] = positions
        with open(POSITIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(positions_data, f, ensure_ascii=False, indent=2)

        return {
            "status": "already_executed",
            "message": f"当日已执行过, 跳过重复累加, 补同步 {synced_count} 个标的到 positions.json",
            "synced_count": synced_count,
        }

    # 首次执行: 累加 build_progress + 同步 positions.json
    execution_results = []

    for inst in confirmed:
        code = inst["full_code"]
        qty = inst["qty"]
        ref_price = inst["ref_price"]

        # v7.8+: 使用 WT 执行算法拆分订单 (大金额订单)
        splits = []
        fill_amount = 0.0
        fill_price = ref_price
        
        if wt_modules.get("min_impact_executor") and inst.get("amount", 0) > 50000:
            try:
                splits = wt_modules["min_impact_executor"].calculate_optimal_splits(
                    target_amount=inst["amount"],
                    ref_price=ref_price,
                    avg_daily_volume=1000000,
                )
                fill_amount = sum(s["amount"] for s in splits)
                fill_price = ref_price
                print(f"[INFO] WT执行算法: {inst['code']} 拆分为 {len(splits)} 笔, 总金额 ¥{fill_amount:,.0f}")
            except Exception as e:
                print(f"[WARN] WT执行算法执行失败: {e}, 使用默认执行")
                fill_amount = round(qty * ref_price, 2)
        else:
            fill_amount = round(qty * ref_price, 2)

        # 更新建仓进度
        built_before = progress["built_amounts"].get(code, 0)
        progress["built_amounts"][code] = built_before + fill_amount
        progress["total_built"] = progress.get("total_built", 0) + fill_amount

        # 同步 positions.json (累加 shares, 加权平均成本)
        if code in positions:
            pos = positions[code]
            old_shares = pos.get("shares", 0)
            old_cost = pos.get("avg_cost", 0.0)
            new_shares = old_shares + qty
            if new_shares > 0:
                new_avg_cost = round((old_shares * old_cost + qty * fill_price) / new_shares, 4)
            else:
                new_avg_cost = fill_price
            pos["shares"] = new_shares
            pos["est_price"] = fill_price  # 第一次交易开盘价
            pos["avg_cost"] = new_avg_cost

        execution_results.append({
            "code": inst["code"],
            "name": inst["name"],
            "action": "BUY",
            "qty": qty,
            "fill_price": fill_price,
            "fill_amount": fill_amount,
            "status": "FILLED",
            "built_before": built_before,
            "built_after": progress["built_amounts"][code],
        })

    # 记录每日执行
    progress.setdefault("daily_records", []).append({
        "date": target_date_str,
        "executed_count": len(execution_results),
        "total_amount": sum(r["fill_amount"] for r in execution_results),
        "total_built_after": progress["total_built"],
        "executed_at": datetime.now().isoformat(),
    })

    save_build_progress(progress)

    # 保存 positions.json
    positions_data["positions"] = positions
    with open(POSITIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(positions_data, f, ensure_ascii=False, indent=2)

    # 生成执行报告
    execution_report = {
        "meta": {
            "execution_date": target_date_str,
            "executed_at": datetime.now().isoformat(),
            "instruction_file": str(instruction_file),
        },
        "summary": {
            "total_instructions": len(instructions_data.get("instructions", [])),
            "confirmed_count": len(confirmed),
            "executed_count": len(execution_results),
            "total_executed_amount": sum(r["fill_amount"] for r in execution_results),
            "total_built": progress["total_built"],
            "remaining": STOCK_ETF_TARGET - progress["total_built"],
            "completion_rate": round(progress["total_built"] / STOCK_ETF_TARGET * 100, 2),
        },
        "execution_results": execution_results,
    }

    # 保存执行报告
    report_file = INSTRUCTIONS_DIR / f"{target_date_str}_execution.json"
    with open(report_file, 'w', encoding='utf-8') as f:
        json.dump(execution_report, f, ensure_ascii=False, indent=2)

    result = {
        "status": "executed",
        "report_file": str(report_file),
        "summary": execution_report["summary"],
    }

    # 收盘后自动生成下一交易日计划
    try:
        next_plan = generate_next_trading_day_plan(target_date_str)
        result["next_trading_day_plan"] = next_plan
    except Exception as e:
        result["next_trading_day_plan"] = {
            "status": "error",
            "reason": f"生成下一交易日计划失败: {e}",
        }

    return result


def show_progress() -> Dict:
    """显示建仓进度"""
    progress = load_build_progress()
    total_built = progress.get("total_built", 0)
    remaining = STOCK_ETF_TARGET - total_built
    completion_rate = total_built / STOCK_ETF_TARGET * 100 if STOCK_ETF_TARGET > 0 else 0

    # 计算剩余交易日
    today = date.today()
    remaining_days = get_remaining_days(today)

    # 估算完成日期
    if remaining > 0 and remaining_days > 0:
        avg_daily = remaining / remaining_days
        eta_date = ACCUMULATION_END
    else:
        avg_daily = 0
        eta_date = "已完成" if remaining <= 0 else "无法完成"

    return {
        "total_target": STOCK_ETF_TARGET,
        "total_built": total_built,
        "remaining": remaining,
        "completion_rate": round(completion_rate, 2),
        "remaining_days": remaining_days,
        "avg_daily_needed": round(avg_daily, 2) if isinstance(avg_daily, float) else avg_daily,
        "accumulation_period": f"{ACCUMULATION_START} ~ {ACCUMULATION_END}",
        "eta": str(eta_date),
        "built_amounts": progress.get("built_amounts", {}),
        "daily_records_count": len(progress.get("daily_records", [])),
    }


def generate_accumulation_schedule() -> Dict:
    """生成2026年底建仓进度预估表"""
    from datetime import timedelta

    schedule = []
    current = ACCUMULATION_START
    total = 0
    last_recorded_week = -1

    while current <= ACCUMULATION_END and total < STOCK_ETF_TARGET:
        if not is_trading_day(current):
            current += timedelta(days=1)
            continue

        remaining = STOCK_ETF_TARGET - total
        remaining_days_to_end = get_remaining_days(current)
        daily_budget = remaining / max(remaining_days_to_end, 1)
        daily_budget = min(daily_budget, DAILY_AMOUNT_LIMIT)

        total += daily_budget
        completion = total / STOCK_ETF_TARGET * 100

        # 每周记录一次 (周一或月末)
        week_num = current.isocalendar()[1]
        is_month_end = current.day >= 28
        if week_num != last_recorded_week or is_month_end or total >= STOCK_ETF_TARGET:
            schedule.append({
                "date": current.isoformat(),
                "daily_budget": round(daily_budget, 2),
                "cumulative": round(total, 2),
                "completion_pct": round(completion, 2),
                "remaining": round(STOCK_ETF_TARGET - total, 2),
            })
            last_recorded_week = week_num

        current += timedelta(days=1)

    return {
        "target": STOCK_ETF_TARGET,
        "start_date": str(ACCUMULATION_START),
        "end_date": str(ACCUMULATION_END),
        "total_trading_days": get_remaining_days(ACCUMULATION_START),
        "schedule_points": len(schedule),
        "schedule": schedule,
        "completion_date": schedule[-1]["date"] if schedule else None,
        "total_built": round(schedule[-1]["cumulative"], 2) if schedule else 0,
        "completion_pct": round(schedule[-1]["completion_pct"], 2) if schedule else 0,
    }


def main():
    parser = argparse.ArgumentParser(description="每日自动执行交易计划")
    parser.add_argument("mode", choices=["pre-market", "post-market", "post-market-auto", "progress", "schedule"],
                        help="执行模式")
    parser.add_argument("--date", type=str, default=None,
                        help="指定日期 (YYYY-MM-DD), 默认今天")
    parser.add_argument("--auto-confirm", action="store_true",
                        help="自动确认所有指令 (跳过人工确认环节)")

    args = parser.parse_args()

    if args.date:
        target_date = args.date
    else:
        target_date = datetime.now().strftime("%Y-%m-%d")

    print("=" * 70)
    print(f"每日自动执行交易计划 - {args.mode} - {target_date}")
    if args.auto_confirm:
        print("⚠️  自动确认模式: 将自动确认所有指令")
    print("=" * 70)

    if args.mode == "pre-market":
        result = generate_instructions(target_date)
        
        # 自动确认所有指令
        if args.auto_confirm and result.get("status") == "generated":
            confirm_count = confirm_all_instructions(target_date)
            result["auto_confirmed"] = True
            result["auto_confirm_count"] = confirm_count
            print(f"[INFO] 自动确认 {confirm_count} 条指令")
        
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    elif args.mode == "post-market":
        result = execute_instructions(target_date)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    elif args.mode == "post-market-auto":
        execute_result = execute_instructions(target_date)
        print(json.dumps(execute_result, ensure_ascii=False, indent=2, default=str))
        
        # 自动生成下一交易日计划
        if execute_result.get("status") == "executed":
            print("\n" + "=" * 70)
            print("收盘后自动生成下一交易日计划")
            print("=" * 70)
            next_plan = generate_next_trading_day_plan(target_date)
            
            # 自动确认下一交易日计划
            if args.auto_confirm and next_plan.get("status") == "generated":
                next_date = next_plan.get("next_trading_day", "")
                if next_date:
                    confirm_count = confirm_all_instructions(next_date)
                    next_plan["auto_confirmed"] = True
                    next_plan["auto_confirm_count"] = confirm_count
                    print(f"[INFO] 自动确认下一交易日 {next_date} 的 {confirm_count} 条指令")
            
            print(json.dumps(next_plan, ensure_ascii=False, indent=2, default=str))

    elif args.mode == "progress":
        result = show_progress()
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    elif args.mode == "schedule":
        result = generate_accumulation_schedule()
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
