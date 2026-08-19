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

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 项目根目录
PROJECT_ROOT = Path(__file__).parent
# Wave 3 第三阶段: 改用 utils.path_config.setup_sys_path() 统一管理
sys.path.insert(0, str(PROJECT_ROOT))  # bootstrap: 确保 utils 包可导入
from utils.path_config import setup_sys_path  # noqa: E402

setup_sys_path()  # noqa: E402  # 统一注入 v8.3 根 / v8.3 src / utils
from utils.concurrency import atomic_write_json  # noqa: E402  # P0-C1 原子写

# B1.2: 统一使用 utils.trade_calendar 判断交易日 (支持节假日)
from utils.trade_calendar import is_trading_day  # noqa: E402

POSITIONS_FILE = PROJECT_ROOT / "config" / "positions.json"
TRADE_PLAN_FILE = PROJECT_ROOT / "v8.3_institutional" / "trade_plans" / "auto_trade_plan_500w_2026-2030.json"
INSTRUCTIONS_DIR = PROJECT_ROOT / "trade_instructions"
PROGRESS_FILE = PROJECT_ROOT / "trade_instructions" / "build_progress.json"

# B-4.5: 风控参数从 config/trade_execution.yaml 加载 (失败回退到硬编码默认值)
from utils.config_manager import get_config as _get_trade_cfg  # noqa: E402

_trade_cfg = _get_trade_cfg("trade_execution") or {}


def _parse_date_from_cfg(s: str | None, default: date) -> date:
    """从 yaml 字符串解析日期, 失败返回默认值"""
    if not s:
        return default
    try:
        return datetime.strptime(str(s), "%Y-%m-%d").date()
    except (ValueError, TypeError) as e:  # P2-1: 收敛为具体异常类型 + 日志
        logger.debug("日期解析失败 (返回默认值 %s): %s", default, e)
        return default


# 风控参数
DAILY_AMOUNT_LIMIT = _trade_cfg.get("daily_amount_limit", 200000)  # 单日金额上限 20万
PRICE_PROTECTION_PCT = _trade_cfg.get("price_protection_pct", 0.03)  # 价格保护带 ±3%
DAILY_LOSS_STOP_PCT = _trade_cfg.get("daily_loss_stop_pct", 0.03)  # 单日累计亏损 -3% 熔断
PORTFOLIO_DRAWDOWN_STOP_PCT = _trade_cfg.get("portfolio_drawdown_stop_pct", 0.05)  # 组合回撤 -5% 熔断

# 建仓期参数 (phase_1_accumulation)
ACCUMULATION_START = _parse_date_from_cfg(_trade_cfg.get("accumulation_start"), date(2026, 7, 10))
ACCUMULATION_END = _parse_date_from_cfg(_trade_cfg.get("accumulation_end"), date(2026, 12, 31))
STOCK_ETF_TARGET = _trade_cfg.get("stock_etf_target", 3_000_000)  # 300万

# 固定日预算起始日 (2026-07-13起改为动态信号加权预算)
FIXED_BUDGET_START = _parse_date_from_cfg(_trade_cfg.get("fixed_budget_start"), date(2026, 7, 13))
DAILY_FIXED_BUDGET = _trade_cfg.get("daily_fixed_budget", 200_000)  # 单日金额上限/参考值

# 智能分批金额 (仅用于2026-07-10~07-12, ETF信号强度 → 当日建仓金额)
_signal_cfg = _trade_cfg.get("signal_amounts", {})
SIGNAL_AMOUNTS = {
    "strong": _signal_cfg.get("strong", 50_000),  # 强信号日 5万
    "medium": _signal_cfg.get("medium", 20_000),  # 弱信号日 2万
    "none": _signal_cfg.get("none", 10_000),  # 无信号日 1万
}

# 白酒单票上限 (占当日预算比例)
BAIJIU_CAP_PCT = _trade_cfg.get("baijiu_cap_pct", 0.10)  # 白酒单票不超过当日预算的 10%
BAIJIU_CODES = set(_trade_cfg.get("baijiu_codes", ["600519", "000858"]))


def _infer_suffix(code: str) -> str:
    """根据代码前缀推断交易所后缀

    QMT 规范:
    - 6xxxxx / 5xxxxx / 9xxxxx → .SH (上海)
    - 0xxxxx / 2xxxxx / 3xxxxx → .SZ (深圳)
    - 159xxx / 16xxxx → .SZ (深圳 ETF / LOF)
    - 8xxxxx → .BJ (北交所)
    - 1xxxxx (其他) → .SH (上海)
    """
    code = str(code).split(".")[0].zfill(6)
    if code.startswith(('6', '5', '9')):
        return f"{code}.SH"
    elif code.startswith(('0', '2', '3')):
        return f"{code}.SZ"
    elif code.startswith(('159', '16')):
        return f"{code}.SZ"
    elif code.startswith('8'):
        return f"{code}.BJ"
    return f"{code}.SH"  # 默认上海


def is_accumulation_period(d: date) -> bool:
    """检查是否处于建仓期"""
    return ACCUMULATION_START <= d <= ACCUMULATION_END


def init_wt_modules() -> dict[str, Any]:
    """初始化 WonderTrader 风格模块

    返回: dict 包含所有WT模块实例, 失败时返回空dict
    """
    wt_modules = {}
    try:
        from utils.wt_contracts_manager import get_contracts_manager
        from utils.wt_execution_algo import MinImpactExecutor, TWAPExecutor, VWAPExecutor
        from utils.wt_hedge_strategy import BetaHedgeStrategy, HedgeContext, TailRiskHedgeStrategy
        from utils.wt_risk_control import PortfolioRiskAnalyzer, RiskControl, StopLossManager

        wt_modules["risk_control"] = RiskControl(
            {
                "max_daily_loss_pct": 0.05,
                "max_portfolio_drawdown_pct": 0.15,
                "max_position_concentration_pct": 0.30,
                "max_single_trade_pct": 0.05,
                "max_daily_trades": 50,
            }
        )
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

        beta_hedge = BetaHedgeStrategy(
            config={
                "target_hedge_ratio": 0.2,
                "max_hedge_ratio": 0.5,
                "min_hedge_ratio": 0.05,
            }
        )
        tail_hedge = TailRiskHedgeStrategy(
            config={
                "target_hedge_ratio": 0.3,
                "max_hedge_ratio": 0.6,
                "min_hedge_ratio": 0.1,
                "vol_threshold": 0.2,
            }
        )
        wt_modules["hedge_context"] = HedgeContext(beta_hedge)
        wt_modules["beta_hedge"] = beta_hedge
        wt_modules["tail_hedge"] = tail_hedge

        logger.info("[INFO] WonderTrader 模块初始化完成")
        logger.info("[INFO]   - 风控模块: RiskControl, StopLossManager, PortfolioRiskAnalyzer")
        logger.info("[INFO]   - 执行算法: MinImpactExecutor, TWAPExecutor, VWAPExecutor")
        logger.info("[INFO]   - 对冲策略: BetaHedgeStrategy, TailRiskHedgeStrategy")
        logger.info("[INFO]   - 合约管理: ContractsManager")

    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        logger.error(f"[WARN] WonderTrader 模块初始化失败: {e}")
        logger.warning("[WARN]   - 系统将使用内置风控规则继续运行")

    return wt_modules


def load_positions() -> dict:
    """加载持仓配置 (B1.7: 委托给 utils.positions_loader 统一入口)"""
    from utils.positions_loader import load_positions as _load_positions_shared
    return _load_positions_shared(POSITIONS_FILE)


def load_trade_plan() -> dict:
    """加载交易计划"""
    if not TRADE_PLAN_FILE.exists():
        return {"stock_etf_account": {"positions": []}}
    with open(TRADE_PLAN_FILE, encoding="utf-8") as f:
        return json.load(f)


def load_build_progress() -> dict:
    """加载建仓进度"""
    if not PROGRESS_FILE.exists():
        return {
            "total_built": 0,
            "daily_records": [],
            "built_amounts": {},  # {code: accumulated_amount}
        }
    with open(PROGRESS_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_build_progress(progress: dict) -> None:
    """保存建仓进度 (P0-C1: 原子写)"""
    atomic_write_json(PROGRESS_FILE, progress)


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


def assess_etf_signal(code: str, positions_data: dict) -> str:
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


def calculate_daily_budget(target_date: date, progress: dict, positions_data: dict) -> dict:
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
    for _code, pos in positions_data.get("positions", {}).items():
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
    base_daily = remaining_total / max(remaining_days, 1)  # 防除零

    if strong_count >= 3:
        signal_strength = "strong"
        daily_budget = min(base_daily * 1.5, SIGNAL_AMOUNTS["strong"])
    elif medium_count >= 3 or strong_count >= 1:
        signal_strength = "medium"
        daily_budget = min(base_daily * 1.0, SIGNAL_AMOUNTS["medium"])
    else:
        signal_strength = "none"
        daily_budget = min(base_daily * 0.5, SIGNAL_AMOUNTS["none"])

    # 应用单日上限 + 可用资金校验 (防止超资金下单)
    daily_budget = min(daily_budget, DAILY_AMOUNT_LIMIT, remaining_total)
    if daily_budget <= 0:
        daily_budget = 0
        signal_strength = "insufficient_budget"

    return {
        "daily_budget": round(daily_budget, 2),
        "signal_strength": signal_strength,
        "strong_signal_count": strong_count,
        "medium_signal_count": medium_count,
        "remaining_total": remaining_total,
        "remaining_days": remaining_days,
        "base_daily": round(base_daily, 2),
    }


def load_latest_prices() -> dict[str, float]:
    """从最近的收盘报告读取最新价格

    优先级:
      1. v8.3_institutional/reports/daily_pnl_report_YYYY-MM-DD.json
      2. v8.3_institutional/reports/daily_pnl_report_YYYY-MM-DD.md
    """
    reports_dir = PROJECT_ROOT / "v8.3_institutional" / "reports"
    if not reports_dir.exists():
        return {}

    # 找最新的 JSON 报告
    json_files = sorted(reports_dir.glob("daily_pnl_report_*.json"), reverse=True)
    if not json_files:
        return {}

    latest_file = json_files[0]
    try:
        with open(latest_file, encoding="utf-8") as f:
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
    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        logger.error(f"读取最新价格失败: {e}")
        return {}


# 默认参考价 (二级兜底: 仅当 load_latest_prices() 收盘报告也无该代码时使用)
# Q-5 标注: 个股价格为历史快照会 stale, 正常路径走 load_latest_prices() 动态读取;
# ETF 价格相对稳定。未来可改为仅保留 ETF 兜底 + 个股无价时跳过 (需同步更新测试)。
DEFAULT_PRICES = {
    "588080": 2.26,
    "512880": 1.13,
    "510050": 3.09,
    "512800": 1.50,
    "515030": 1.71,
    "512760": 1.55,
    "512170": 0.31,
    "518880": 6.50,
    "688041": 363.46,
    "300308": 1194.90,
    "002371": 878.43,
    "603019": 103.99,
    "300033": 230.30,
    "300782": 92.83,
    "688017": 408.28,
    "300274": 100.00,
    "000408": 35.00,
    "601088": 40.00,
    "600276": 55.61,
    "600900": 27.77,
}


# ===========================================================
# 价格预测信号 (v7.5+ 集成 tf_price_predictor)
# ===========================================================


def fetch_prediction_signals(symbols: list[str], horizon: int = 5) -> dict[str, dict]:
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
        results: dict[str, dict] = {}
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
                strength = pred.signal_strength if hasattr(pred, "signal_strength") else 0.0
                results[symbol] = {
                    "direction": pred.direction,
                    "confidence": pred.confidence,
                    "target_price": pred.target_price,
                    "method": pred.method,
                    "signal_strength": strength,
                    "expected_return": pred.expected_return,
                }
            except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
                # 单标的失败不影响其他标的
                logger.exception(f"预测信号生成失败 {symbol}, 已降级 NEUTRAL: {e}")
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
    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        logger.error(f"[WARN] 预测信号获取失败: {e}")
        return {}


def _load_prediction_prices(symbol: str, days: int = 120) -> Any:
    """加载历史价格序列供预测用 (B2.5: 从内存索引读取, O(1) 查询)

    优先级:
      1. v8.3_institutional/reports/daily_pnl_report_*.json 中的 close_price
         (通过 _get_prediction_prices_index 单次扫描构建进程级缓存索引)
      2. 数据不可用时返回 None

    B2.5 优化前: 每次 fetch_prediction_signals 调用本函数都会重新扫描所有 PnL 报告,
                 O(N×M) 次 json.load (N=symbol 数, M=报告文件数).
    B2.5 优化后: 首次调用时单次扫描所有报告构建 {symbol: [prices]} 索引 (O(M)),
                 后续查询直接读内存索引 (O(1)).
    """
    try:
        import numpy as np
    except ImportError:
        return None

    index = _get_prediction_prices_index()
    code_clean = symbol.split(".")[0] if "." in symbol else symbol
    prices = index.get(code_clean, [])

    # 取最近 days 天; 少于 30 个样本视为数据不足
    if len(prices) < 30:
        return None
    return np.array(prices[-days:], dtype=float)


# ===========================================================
# B2.5: PnL 报告价格索引 (进程级单次扫描缓存)
# ===========================================================

# 进程级缓存: 首次构建后, 同一进程内所有 fetch_prediction_signals 调用复用
_PREDICTION_PRICES_INDEX: Optional[dict[str, list[float]]] = None
_PREDICTION_PRICES_INDEX_LOCK = threading.Lock()


def _get_prediction_prices_index() -> dict[str, list[float]]:
    """构建 {symbol_code: [close_price, ...]} 索引 (单次扫描所有 PnL 报告, 进程级缓存)

    B2.5: 替代 _load_prediction_prices 的 O(N×M) 重复扫描.
    所有 daily_pnl_report_*.json 只读取一次, 构建索引后 O(1) 查询.

    语义保持 (与 B2.5 前一致):
      - 报告文件按文件名升序处理 (文件名含日期, 等价于按日期升序)
      - 每个报告文件中, 同一 symbol 只取第一个匹配的 detail (原 break 语义)
      - close_price <= 0 或无法转 float 的样本跳过
      - 解析异常的文件跳过, 不影响其他文件
    """
    global _PREDICTION_PRICES_INDEX
    if _PREDICTION_PRICES_INDEX is not None:
        return _PREDICTION_PRICES_INDEX

    with _PREDICTION_PRICES_INDEX_LOCK:
        # 双检: 持锁期间其他线程可能已完成构建
        if _PREDICTION_PRICES_INDEX is not None:
            return _PREDICTION_PRICES_INDEX

        import json as _json
        import logging

        index: dict[str, list[float]] = {}
        reports_dir = PROJECT_ROOT / "v8.3_institutional" / "reports"
        if not reports_dir.exists():
            _PREDICTION_PRICES_INDEX = index
            return index

        json_files = sorted(reports_dir.glob("daily_pnl_report_*.json"))
        for jf in json_files:
            try:
                with open(jf, encoding="utf-8") as f:
                    report = _json.load(f)
                # 每个文件内, 同一 symbol 只取第一个匹配 (保持原 break 语义)
                seen_in_file: set = set()
                for detail in report.get("portfolio_pnl", {}).get("details", []):
                    code_raw = detail.get("code", "")
                    code_clean = code_raw.split(".")[0] if "." in code_raw else code_raw
                    if not code_clean or code_clean in seen_in_file:
                        continue
                    seen_in_file.add(code_clean)
                    p = detail.get("close_price", 0)
                    if p and p > 0:
                        try:
                            index.setdefault(code_clean, []).append(float(p))
                        except (ValueError, TypeError):
                            continue
            except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
                logging.getLogger(__name__).exception(
                    "解析PnL报告异常,跳过文件: %s", jf
                )
                continue

        _PREDICTION_PRICES_INDEX = index
        return index


def _reset_prediction_prices_index() -> None:
    """重置价格索引缓存 (仅供测试使用: 在不同测试用例间隔离缓存状态)"""
    global _PREDICTION_PRICES_INDEX
    with _PREDICTION_PRICES_INDEX_LOCK:
        _PREDICTION_PRICES_INDEX = None


def adjust_allocation_by_signal(base_allocated: float, signal: dict, daily_budget: float) -> tuple:
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


def _precheck_instructions_preconditions(target_date_str: str, target_date: date) -> Optional[dict]:
    """前置检查: 交易日和建仓期。

    Args:
        target_date_str: 目标日期字符串 (YYYY-MM-DD)
        target_date: 目标日期 date 对象

    Returns:
        未通过返回跳过结果 dict; 通过返回 None
    """
    if not is_trading_day(target_date):
        return {"status": "skipped", "reason": f"{target_date_str} 非交易日(周末)"}
    if not is_accumulation_period(target_date):
        return {"status": "skipped", "reason": f"{target_date_str} 不在建仓期({ACCUMULATION_START} ~ {ACCUMULATION_END})"}
    return None


def _refresh_etf_flow(positions_file: Path) -> dict:
    """刷新 ETF 资金流信号并重新加载持仓配置。

    Args:
        positions_file: positions.json 路径

    Returns:
        重新加载后的 positions_data (失败时也返回当前持仓)
    """
    try:
        from utils.etf_flow_monitor import refresh_etf_flow_signals

        etf_result = refresh_etf_flow_signals(str(positions_file))
        if etf_result.get("status") == "success":
            logger.info(
                f"[INFO] ETF资金流信号刷新成功: 更新 {etf_result['updated_count']} 个标的, 检测到 {etf_result.get('signal_count', 0)} 条信号"
            )
            return load_positions()
        logger.error(f"[WARN] ETF资金流信号刷新失败: {etf_result.get('message', 'unknown')}")
    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        logger.error(f"[WARN] ETF资金流信号刷新模块加载失败: {e}")
    return load_positions()


def _run_stop_loss_check(wt_modules: dict, positions: dict) -> list:
    """盘后止损止盈检查 — 对每个持仓调用 StopLossManager.check_stop_loss

    P1-1 修复 (2026-08-14): 此前 StopLossManager 被实例化但 check_stop_loss 从未被调用,
    导致止损止盈完全失效。现在在 execute_instructions 执行前对全部持仓检查,
    触发的标的记入返回列表供后续处理。

    Args:
        wt_modules: WonderTrader 模块 dict (含 stop_loss_manager)
        positions: 当前持仓 dict

    Returns:
        触发列表 [{code, action, order_info}, ...]
    """
    sl_manager = wt_modules.get("stop_loss_manager")
    if not sl_manager:
        logger.warning("[StopLoss] stop_loss_manager 不可用, 跳过止损检查")
        return []

    triggered = []
    for code, item in positions.items():
        avg_cost = item.get("avg_cost", 0)
        qty = item.get("phase1_shares") or item.get("total_shares") or item.get("shares", 0)
        current_price = item.get("est_price", 0)
        if avg_cost <= 0 or qty == 0 or current_price <= 0:
            continue

        pure_code = code.split(".")[0]
        if pure_code not in sl_manager.stop_loss_orders:
            sl_manager.set_stop_loss(pure_code, avg_cost, abs(qty))

        action, order_info = sl_manager.check_stop_loss(pure_code, current_price)
        if action != "none" and order_info:
            triggered.append({
                "code": code,
                "name": item.get("name", code),
                "action": action,
                "current_price": current_price,
                "stop_price": order_info.get("stop_price", 0),
                "take_profit_price": order_info.get("take_profit_price", 0),
                "pnl_pct": round((current_price - avg_cost) / avg_cost, 4),
            })
            logger.warning(
                "[StopLoss] %s (%s) 触发 %s @ ¥%.3f (P&L %+.1%%)",
                item.get("name", code), code, action, current_price,
                ((current_price - avg_cost) / avg_cost) * 100,
            )

    if not triggered:
        logger.info("[StopLoss] 全部持仓在安全范围内, 无触发")
    else:
        logger.warning("[StopLoss] 共 %d 个标的触发止损/止盈", len(triggered))
    return triggered


def _run_wt_risk_precheck(wt_modules: dict, positions_data: dict, progress: dict) -> Optional[dict]:
    """WT 风控预检查 (盘前阻断级)。

    P1-5 修复: 此前仅打印 risk_score 不阻断, 高风险组合仍生成指令。
    现在当 risk_score 超阈值或集中度超限时返回阻断标记,
    generate_instructions 据此过滤或标注 HIGH_RISK。

    Args:
        wt_modules: WonderTrader 模块 dict
        positions_data: 持仓配置
        progress: 建仓进度

    Returns:
        None 表示通过; dict 表示阻断 (含 reason/risk_score)
    """
    analyzer = wt_modules.get("portfolio_risk_analyzer")
    if not analyzer:
        # P1-5: wt_modules 初始化失败时显式告警, 不静默放行
        logger.warning("[WARN] WT风控分析器不可用 (wt_modules 未初始化), 盘前风控降级为无检查")
        try:
            from utils.notify import send_alert
            send_alert(
                "[WARN] WT风控分析器不可用",
                "portfolio_risk_analyzer 未初始化, 盘前风控降级。请检查 wt_modules 初始化。",
                severity="WARN",
            )
        except Exception as e:  # noqa: BLE001  # notify fail-open, 不阻断交易
            logger.exception(f"发送 WT 风控不可用告警失败, 已 fail-open: {e}")
        return None
    try:
        risk_summary = analyzer.analyze_portfolio(
            positions_data,
            progress.get("total_built", 0),
            STOCK_ETF_TARGET,
        )
        risk_score = risk_summary.get("risk_score", 0)
        concentration = risk_summary.get("concentration_risk", 0)
        logger.info(f"[INFO] WT风控分析: 组合风险评分 {risk_score}")
        logger.info(f"[INFO]   - 集中度风险: {concentration}")
        logger.info(f"[INFO]   - 行业分布: {risk_summary.get('sector_distribution', 'N/A')}")

        # P1-5: 盘前阻断级校验 (阈值与 _run_wt_risk_block_check 的集中度逻辑对齐)
        # risk_score >= 80 或集中度 >= 0.3 (30%) 时阻断
        try:
            score_val = float(risk_score) if risk_score is not None else 0.0
        except (TypeError, ValueError):
            score_val = 0.0
        try:
            conc_val = float(concentration) if concentration is not None else 0.0
        except (TypeError, ValueError):
            conc_val = 0.0

        if score_val >= 80 or conc_val >= 0.3:
            logger.warning(
                "[BLOCK] WT盘前风控阻断: risk_score=%.1f, concentration=%.3f (超阈值)",
                score_val,
                conc_val,
            )
            return {
                "status": "blocked",
                "reason": f"盘前风控超限: risk_score={score_val:.1f}, concentration={conc_val:.3f}",
                "risk_score": score_val,
                "concentration": conc_val,
            }
    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        logger.error(f"[WARN] WT风控分析执行失败: {e}")
    return None


def _compute_progress_ratio(target_date: date) -> float:
    """计算建仓进度比例 (已过交易日 / 总交易日)。

    Args:
        target_date: 目标日期

    Returns:
        进度比例 [0, 1]
    """
    from datetime import timedelta

    elapsed_days = 0
    current = ACCUMULATION_START
    while current <= target_date:
        if is_trading_day(current):
            elapsed_days += 1
        current += timedelta(days=1)
    total_accumulation_days = get_remaining_days(ACCUMULATION_START)
    return min(elapsed_days / max(total_accumulation_days, 1), 1.0)


def _collect_pending_positions(plan_positions: list, progress: dict, progress_ratio: float) -> list:
    """收集所有未完成建仓的标的, 并计算缺口 (缺口大者优先)。

    Args:
        plan_positions: 交易计划中的标的列表
        progress: 建仓进度
        progress_ratio: 建仓进度比例

    Returns:
        未完成建仓的标的列表 (含 gap 字段, 已按缺口降序)
    """
    pending = []
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
            pending.append(
                {
                    "code": code,
                    "code_clean": code.split(".")[0],
                    "name": pos.get("name", ""),
                    "weight": pos.get("weight", 0),
                    "target_amount": target_amount,
                    "built": built,
                    "remaining": remaining,
                    "gap": gap,
                }
            )
    # 按缺口降序排序 (缺口大的优先买入)
    pending.sort(key=lambda x: x["gap"], reverse=True)
    return pending


def _compute_price_band(ref_price: float) -> tuple:
    """计算价格保护带 (最大/最小买入价)。

    Args:
        ref_price: 参考价

    Returns:
        (max_buy_price, min_buy_price) 元组
    """
    max_buy_price = round(ref_price * (1 + PRICE_PROTECTION_PCT), 4)
    min_buy_price = round(ref_price * (1 - PRICE_PROTECTION_PCT), 4)
    return max_buy_price, min_buy_price


def _allocate_position(
    pos: dict,
    target_date_str: str,
    daily_budget: float,
    remaining_budget: float,
    latest_prices: dict,
    prediction_signals: dict,
    positions_data: dict,
) -> Optional[tuple]:
    """为单个标的分配预算并构建买入指令。

    分配策略: 按权重比例分配, 受单标的上限(当日预算30%)和价格保护带约束,
    高价股特殊处理 (100股最小手数)。

    Args:
        pos: 标的持仓信息 (含 code_clean/weight/remaining 等)
        target_date_str: 目标日期字符串
        daily_budget: 当日总预算
        remaining_budget: 剩余预算
        latest_prices: 最新价格字典
        prediction_signals: 预测信号字典
        positions_data: 持仓配置

    Returns:
        (instruction_dict, actual_amount) 元组; None 表示跳过该标的
    """
    code_clean = pos["code_clean"]
    ref_price = latest_prices.get(code_clean, 0)
    if not ref_price:
        ref_price = DEFAULT_PRICES.get(code_clean, 10.0)

    max_buy_price, min_buy_price = _compute_price_band(ref_price)
    min_lot_cost = 100 * ref_price

    # 按权重分配预算 (权重10% → 分配剩余预算的10%)
    # 注: 旧公式 `* weight / 0.05 * 0.15` 等价于 weight*3, 会过度分配, 已修正为纯权重比例
    allocated = min(remaining_budget * pos["weight"], remaining_budget, pos["remaining"])
    # 单标的上限: 当日预算的30% (20万预算下单标最多6万)
    allocated = min(allocated, daily_budget * 0.30)

    # v7.5+: 根据预测信号调整分配
    signal = prediction_signals.get(code_clean, {})
    allocated, signal_tag = adjust_allocation_by_signal(allocated, signal, daily_budget)
    # 强看空 → 跳过该标的
    if signal_tag == "skip" and allocated == 0:
        logger.warning(
            f"[WARN] 预测信号触发跳过: {code_clean} ({pos['name']}) - 强看空 (置信度 {signal.get('confidence', 0):.0%})"
        )
        return None

    # 高价股处理: 如果 100 股成本 > 分配预算
    if min_lot_cost > allocated:
        # 如果 100 股成本超过当日预算的 50%, 跳过 (避免单标的占用过多预算)
        if min_lot_cost > daily_budget * 0.50:
            return None
        # 否则检查剩余预算是否足够买 100 股
        if remaining_budget < min_lot_cost:
            return None
        allocated = min_lot_cost  # 只买 100 股

    allocated = min(allocated, remaining_budget, pos["remaining"])

    # 估算购买数量 (100股整数倍)
    est_qty = int(allocated / max_buy_price / 100) * 100
    if est_qty <= 0:
        est_qty = 100  # 最小 100 股

    actual_amount = round(est_qty * ref_price, 2)

    # 如果实际金额超过剩余预算, 跳过
    if actual_amount > remaining_budget:
        return None

    # 评估ETF信号 + 预测信号摘要 (供人工审核参考)
    etf_signal = assess_etf_signal(pos["code"], positions_data)
    pred_signal = prediction_signals.get(code_clean, {})

    instruction = {
        "instruction_id": f"{target_date_str.replace('-', '')}-{code_clean}",
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
        "confirm": False,  # 默认未确认, 需人工改为 true
    }
    return instruction, actual_amount


def _build_risk_checks(total_allocated: float) -> dict:
    """构建风控检查字典。

    Args:
        total_allocated: 当日已分配总额

    Returns:
        风控检查字典
    """
    return {
        "daily_limit": {
            "rule": f"单日金额上限 {DAILY_AMOUNT_LIMIT:,}",
            "value": total_allocated,
            "limit": DAILY_AMOUNT_LIMIT,
            "passed": total_allocated <= DAILY_AMOUNT_LIMIT,
        },
        "price_protection": {
            "rule": f"价格保护带 {PRICE_PROTECTION_PCT:.0%}",
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


def _build_instruction_file(
    target_date_str: str,
    progress: dict,
    budget_info: dict,
    risk_checks: dict,
    instructions: list,
    total_allocated: float,
) -> dict:
    """构建指令文件字典 (含 meta/budget/risk/instructions)。

    Args:
        target_date_str: 目标日期字符串
        progress: 建仓进度
        budget_info: 预算信息
        risk_checks: 风控检查
        instructions: 指令列表
        total_allocated: 已分配总额

    Returns:
        指令文件字典
    """
    return {
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


def _save_instruction_file(target_date_str: str, instruction_file: dict) -> tuple:
    """保存指令文件 (JSON + Markdown 两个版本)。

    Args:
        target_date_str: 目标日期字符串
        instruction_file: 指令文件字典

    Returns:
        (output_file, md_file) 路径元组
    """
    INSTRUCTIONS_DIR.mkdir(parents=True, exist_ok=True)
    output_file = INSTRUCTIONS_DIR / f"{target_date_str}_instructions.json"
    # 原子写入指令文件, 防止进程中断导致文件损坏
    atomic_write_json(output_file, instruction_file)

    # 生成 markdown 版本
    md_file = INSTRUCTIONS_DIR / f"{target_date_str}_instructions.md"
    md_content = render_instructions_md(instruction_file)
    with open(md_file, "w", encoding="utf-8") as f:
        f.write(md_content)
    return output_file, md_file


def generate_instructions(target_date_str: str) -> dict:
    """盘前生成交易指令

    生成包含所有待买入标的的指令清单,
    默认 confirm=false, 等待人工确认后改为 true.

    分配策略: 按剩余目标金额比例分配当日预算
    (确保每个未完成建仓的标的都能获得合理份额)
    """
    target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()

    # 前置检查: 交易日 / 建仓期
    skip = _precheck_instructions_preconditions(target_date_str, target_date)
    if skip:
        return skip

    # 加载数据 (v7.5+: 盘前自动刷新ETF资金流信号)
    positions_data = _refresh_etf_flow(POSITIONS_FILE)
    wt_modules = init_wt_modules()
    trade_plan = load_trade_plan()
    progress = load_build_progress()
    latest_prices = load_latest_prices()

    # v7.8+: WT风控预检查 (使用 WT PortfolioRiskAnalyzer)
    # P1-5: 盘前风控超阈值时阻断, 不再仅打印
    precheck_result = _run_wt_risk_precheck(wt_modules, positions_data, progress)
    if precheck_result and precheck_result.get("status") == "blocked":
        logger.warning("[BLOCK] 盘前风控阻断, 停止生成指令: %s", precheck_result.get("reason"))
        return {
            "status": "blocked",
            "reason": precheck_result.get("reason", "WT盘前风控阻断"),
            "risk_score": precheck_result.get("risk_score"),
            "budget_info": {"daily_budget": 0},
        }

    # 获取预测信号 (v7.5+ 集成 tf_price_predictor, 失败时静默降级)
    pending_codes = [
        p.get("code", "").split(".")[0] for p in trade_plan.get("stock_etf_account", {}).get("positions", [])
    ]
    prediction_signals = fetch_prediction_signals(pending_codes, horizon=5)
    if prediction_signals:
        up_count = sum(1 for s in prediction_signals.values() if s.get("direction") == "UP")
        down_count = sum(1 for s in prediction_signals.values() if s.get("direction") == "DOWN")
        logger.info(f"[INFO] 预测信号: {len(prediction_signals)} 个标的, 看多 {up_count}, 看空 {down_count}")

    # 计算当日预算
    budget_info = calculate_daily_budget(target_date, progress, positions_data)
    if budget_info["daily_budget"] <= 0:
        return {"status": "completed", "reason": "已完成建仓目标", "budget_info": budget_info}

    # 收集所有未完成建仓的标的 (按缺口降序, 缺口大的优先买入)
    progress_ratio = _compute_progress_ratio(target_date)
    plan_positions = trade_plan.get("stock_etf_account", {}).get("positions", [])
    pending_positions = _collect_pending_positions(plan_positions, progress, progress_ratio)

    if not pending_positions:
        return {"status": "completed", "reason": "所有标的已建仓完成"}

    # 轮换分配: 优先满足缺口大的标的
    daily_budget = budget_info["daily_budget"]
    instructions = []
    total_allocated = 0
    remaining_budget = daily_budget

    for pos in pending_positions:
        if remaining_budget < 100:
            break  # 预算耗尽
        result = _allocate_position(
            pos,
            target_date_str,
            daily_budget,
            remaining_budget,
            latest_prices,
            prediction_signals,
            positions_data,
        )
        if result is None:
            continue
        instruction, actual_amount = result
        instructions.append(instruction)
        total_allocated += actual_amount
        remaining_budget -= actual_amount

    # 风控检查 + 构建指令文件 (manual_confirm 不阻塞生成, 只标记需要确认)
    risk_checks = _build_risk_checks(total_allocated)
    instruction_file = _build_instruction_file(
        target_date_str, progress, budget_info, risk_checks, instructions, total_allocated,
    )
    output_file, md_file = _save_instruction_file(target_date_str, instruction_file)

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

    with open(instruction_file, encoding="utf-8") as f:
        data = json.load(f)

    confirmed_count = 0
    for inst in data.get("instructions", []):
        if not inst.get("confirm", False):
            inst["confirm"] = True
            confirmed_count += 1

    if confirmed_count > 0:
        atomic_write_json(instruction_file, data)

    return confirmed_count


def render_instructions_md(data: dict) -> str:
    """渲染交易指令 markdown 版本"""
    meta = data["meta"]
    budget = data["budget_info"]
    risk = data["risk_checks"]
    instructions = data["instructions"]

    lines = [
        f"# 交易指令清单 {meta['instruction_date']}",
        "",
        f"**生成时间**: {meta['generated_at']}",
        f"**阶段**: {meta['phase']} (建仓期 {ACCUMULATION_START} ~ {ACCUMULATION_END})",
        f"**目标总额**: {meta['total_capital']:,}",
        f"**已建仓**: {meta['total_built_before']:,.0f}",
        f"**剩余**: {meta['remaining_total']:,.0f}",
        "",
        "---",
        "",
        "## 当日预算",
        "",
        f"- **信号强度**: {budget.get('signal_strength', 'unknown')}",
        f"- **强信号数**: {budget.get('strong_signal_count', 0)}",
        f"- **弱信号数**: {budget.get('medium_signal_count', 0)}",
        f"- **基础日预算**: {budget.get('base_daily', 0):,.0f}",
        f"- **当日预算**: {budget.get('daily_budget', 0):,.0f}",
        f"- **剩余交易日**: {budget.get('remaining_days', 0)} 天",
        "",
        "## 风控检查",
        "",
        "| 检查项 | 规则 | 数值 | 上限 | 状态 |",
        "|--------|------|------|------|------|",
        f"| 单日金额上限 | {DAILY_AMOUNT_LIMIT:,} | {data['total_allocated']:,.0f} | {DAILY_AMOUNT_LIMIT:,} | {'PASS' if risk['daily_limit']['passed'] else 'FAIL'} |",
        f"| 价格保护带 | {PRICE_PROTECTION_PCT:.0%} | - | - | {'PASS' if risk['price_protection']['passed'] else 'FAIL'} |",
        f"| 熔断停止 | 单日-{DAILY_LOSS_STOP_PCT:.0%}/组合-{PORTFOLIO_DRAWDOWN_STOP_PCT:.0%} | 0% | - | {'PASS' if risk['circuit_breaker']['passed'] else 'FAIL'} |",
        "| 人工确认 | confirm=true | - | - | PENDING |",
        "",
        "## 交易指令",
        "",
        f"**总指令数**: {len(instructions)}",
        f"**总分配金额**: {data['total_allocated']:,.0f}",
        "",
        "| # | 代码 | 名称 | 动作 | 数量 | 参考价 | 最高买入价 | 最低买入价 | 估算金额 | ETF信号 | 已建仓 | 剩余 | 确认 |",
        "|---|------|------|------|------|--------|-----------|-----------|---------|---------|--------|------|------|",
    ]

    for idx, inst in enumerate(instructions, 1):
        confirm = "OK" if inst["confirm"] else "PENDING"
        lines.append(
            f"| {idx} | {inst['code']} | {inst['name']} | {inst['action']} | "
            f"{inst['qty']} | {inst['ref_price']:.4f} | {inst['max_buy_price']:.4f} | "
            f"{inst['min_buy_price']:.4f} | {inst['estimated_amount']:,.0f} | "
            f"{inst['etf_signal']} | {inst['built_before']:,.0f} | "
            f"{inst['remaining_after']:,.0f} | {confirm} |"
        )

    lines.extend(
        [
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
            f"- **单日金额上限**: {DAILY_AMOUNT_LIMIT:,}",
            f"- **价格保护带**: 买入价不超过昨收 +{PRICE_PROTECTION_PCT:.0%}",
            f"- **单日熔断**: 亏损 >{DAILY_LOSS_STOP_PCT:.0%} 停止建仓",
            f"- **组合熔断**: 回撤 >{PORTFOLIO_DRAWDOWN_STOP_PCT:.0%} 停止建仓",
            "",
            "*由 daily_trade_executor.py 自动生成*",
        ]
    )

    return "\n".join(lines)


def generate_next_trading_day_plan(today_str: str) -> dict:
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
    logger.info(f"[INFO] 今日: {today_str}, 下一交易日: {next_day_str}")

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


def _check_execution_preconditions(instructions_data: dict) -> tuple:
    """执行前风控检查 + 筛选已确认指令。

    Args:
        instructions_data: 指令文件数据

    Returns:
        (confirmed_list, error_result) 元组; error_result 非 None 表示应直接返回
    """
    # 模拟检查熔断 (盘后实际数据需要从报告读取); 如有风控失败则不执行
    risk_checks = instructions_data.get("risk_checks", {})
    if not risk_checks.get("daily_limit", {}).get("passed", True):
        return [], {"status": "blocked", "reason": "单日金额上限未通过"}

    confirmed = [i for i in instructions_data.get("instructions", []) if i.get("confirm", False)]
    if not confirmed:
        return [], {
            "status": "no_confirmed",
            "reason": "无已确认指令 (所有 confirm=false)",
            "total_instructions": len(instructions_data.get("instructions", [])),
        }
    return confirmed, None


def _run_wt_risk_block_check(wt_modules: dict, confirmed: list) -> Optional[dict]:
    """WT 风控前置检查 (单笔额度 + 日内笔数), 未通过时阻断执行。

    IC6 修复: 风控未通过时阻断执行 (原逻辑仅打印 WARN, 违反"风控一票否决"原则)。

    Args:
        wt_modules: WonderTrader 模块 dict
        confirmed: 已确认指令列表

    Returns:
        阻断结果 dict; None 表示通过
    """
    rc = wt_modules.get("risk_control")
    if not rc:
        return None
    try:
        risk_blocked = False
        # 单笔交易额度检查
        for inst in confirmed:
            # IC2 修复: 字段名 "amount" → "estimated_amount" (原字段名不匹配, 永远返回 0, 风控形同虚设)
            amount = inst.get("estimated_amount", 0) or 0
            ok, msg = rc.check_single_trade(amount, STOCK_ETF_TARGET)
            if not ok:
                logger.info(f"[BLOCK] WT风控单笔检查未通过: {msg}")
                risk_blocked = True
        # 日内交易笔数检查
        ok, msg = rc.check_daily_trade_count()
        if not ok:
            logger.info(f"[BLOCK] WT风控日内笔数检查未通过: {msg}")
            risk_blocked = True

        # IC6 修复: 风控未通过时立即返回, 不继续执行
        if risk_blocked:
            return {
                "status": "blocked",
                "message": "WT风控检查未通过, 执行已被阻断 (风控一票否决)",
                "blocked_reason": "WT risk control check failed",
            }
    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        logger.error(f"[WARN] WT风控检查执行失败: {e}")
        # 风控检查本身崩溃时保守拒绝 (Fail-Safe)
        return {
            "status": "blocked",
            "message": f"WT风控检查执行异常, 保守阻断: {e}",
            "blocked_reason": "WT risk control check error",
        }
    return None


def _sync_positions_idempotent(target_date_str: str, confirmed: list, positions: dict) -> dict:
    """幂等模式: 当日已执行过, 跳过重复累加, 仅补同步 positions.json。

    从已执行的 execution.json 读取成交结果, 仅对 shares=0 的标的补同步。

    Args:
        target_date_str: 目标日期字符串
        confirmed: 已确认指令列表 (用于 full_code 反查)
        positions: 持仓字典 (原地修改)

    Returns:
        幂等结果 dict (含 synced_count)
    """
    execution_file = INSTRUCTIONS_DIR / f"{target_date_str}_execution.json"
    if execution_file.exists():
        with open(execution_file, encoding="utf-8") as f:
            prev_report = json.load(f)
        prev_results = prev_report.get("execution_results", [])
    else:
        prev_results = []

    # 从已执行的成交结果重建 positions 同步数据
    synced_count = 0
    for r in prev_results:
        code = r.get("code", "")
        # P2-5 修复: 原硬编码 f"{code}.SH" 对 .SZ/.BJ 标的补同步失败。
        # 改用 _infer_suffix 动态推断交易所后缀。
        full_code = next(
            (i["full_code"] for i in confirmed if i.get("code") == code),
            _infer_suffix(code),
        )
        qty = r.get("qty", 0)
        fill_price = r.get("fill_price", 0.0)

        if full_code in positions:
            pos = positions[full_code]
            # 仅在 shares=0 (未同步) 时补同步
            if (pos.get("shares") or 0) == 0:
                pos["shares"] = qty
                pos["est_price"] = fill_price
                pos["avg_cost"] = fill_price
                synced_count += 1

    return {
        "status": "already_executed",
        "message": f"当日已执行过, 跳过重复累加, 补同步 {synced_count} 个标的到 positions.json",
        "synced_count": synced_count,
    }


def _execute_single_instruction(inst: dict, wt_modules: dict, progress: dict, positions: dict) -> dict:
    """执行单条已确认指令 (WT 拆分 + 更新建仓进度 + 同步 positions)。

    根据 inst["action"] (默认 BUY) 区分买卖:
      - BUY:  滑点上浮成交价, 持仓股数累加 + 加权均价, 建仓进度累加; 无印花税。
      - SELL: 滑点下调成交价, 持仓股数减少且成本基础不变, 建仓进度减少;
              total_cost 仅含费用 (佣金+过户费+印花税 0.05%), fill_amount 为成交毛额。

    Args:
        inst: 已确认指令 (含 action/full_code/qty/ref_price/estimated_amount)
        wt_modules: WonderTrader 模块 dict
        progress: 建仓进度 (原地修改 built_amounts/total_built)
        positions: 持仓字典 (原地修改 shares/avg_cost/est_price)

    Returns:
        execution_result dict (含 action/fill_price/fill_amount/stamp_duty/
        total_cost/built_before/built_after)
    """
    code = inst["full_code"]
    qty = inst["qty"]
    ref_price = inst["ref_price"]
    action = str(inst.get("action", "BUY")).upper()
    is_sell = action == "SELL"

    # P2-3 交易成本建模 (A股):
    #   - 买入: 佣金(双边 0.03%) + 过户费(双边 0.001%), 无印花税
    #   - 卖出: 佣金 + 过户费 + 印花税(单边 0.05%, 2023起)
    #   - 滑点: 按 ref_price 上浮 (买入) / 下调 (卖出), 默认 10bp
    slippage_rate = 0.001         # 滑点 10bp (可配置)
    commission_rate = 0.0003      # 佣金 0.03%
    transfer_fee_rate = 0.00001   # 过户费 0.001%
    stamp_duty_rate = 0.0005      # 印花税 0.05% (仅卖出单边)
    # 实际成交价 (含滑点): 买入向上, 卖出向下 (B2/S1 修复 — 卖单原错误地恒为加仓+滑点上浮)
    slippage_sign = -1.0 if is_sell else 1.0
    exec_price = round(ref_price * (1.0 + slippage_sign * slippage_rate), 4)

    # v7.8+: 使用 WT 执行算法拆分订单 (大金额订单)
    fill_amount = 0.0

    # IC2 修复: 字段名 "amount" → "estimated_amount" (与指令字典字段名一致)
    inst_amount = inst.get("estimated_amount", 0) or 0
    if wt_modules.get("min_impact_executor") and inst_amount > 50000:
        try:
            splits = wt_modules["min_impact_executor"].calculate_optimal_splits(
                target_amount=inst_amount,
                ref_price=ref_price,
                avg_daily_volume=1000000,
            )
            fill_amount = round(sum(s["amount"] for s in splits), 2)
            logger.info(f"[INFO] WT执行算法: {inst['code']} 拆分为 {len(splits)} 笔, 总金额 {fill_amount:,.0f}")
        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.error(f"[WARN] WT执行算法执行失败: {e}, 使用默认执行")
            fill_amount = round(qty * ref_price, 2)
    else:
        fill_amount = round(qty * ref_price, 2)

    # P2-3: 以「实际成交金额 + 含滑点成交价」推导实际成交股数, 保证 shares 与 fill_amount 一致。
    # 若 fill_amount 为 0 (异常) 则回退到目标股数按含滑点价计算。
    if fill_amount <= 0 or exec_price <= 0:
        actual_qty = int(qty)
        actual_amount = round(qty * exec_price, 2)
    else:
        # WT 部分成交时 fill_amount < qty*ref_price, 按实际金额/含滑点价取整股
        actual_qty = int(fill_amount / exec_price)
        actual_amount = round(actual_qty * exec_price, 2)
    if actual_qty < 0:
        actual_qty = 0

    # P1-4 修复: actual_qty == 0 时 (fill_amount < exec_price 取整为 0) 不能标记 FILLED。
    # 此前 0 股也返回 status:FILLED 并计入 execution_results, 造成静默假成交。
    # 现在返回 SKIPPED, 不进 execution_results/daily_records, 不更新 positions/progress。
    if actual_qty <= 0:
        logger.warning(
            "[SKIP] %s 成交股数=0 (fill_amount=%.2f < exec_price=%.4f), 标记 SKIPPED 不计假成交",
            inst.get("code", "?"),
            fill_amount,
            exec_price,
        )
        return {
            "code": inst["code"],
            "name": inst["name"],
            "action": action,
            "qty": 0,
            "fill_price": exec_price,
            "fill_amount": 0.0,
            "commission": 0.0,
            "transfer_fee": 0.0,
            "stamp_duty": 0.0,
            "total_cost": 0.0,
            "status": "SKIPPED",
            "reason": "fill_amount_below_min_unit",
            "built_before": progress["built_amounts"].get(code, 0),
            "built_after": progress["built_amounts"].get(code, 0),
        }

    # P2-3: 计入交易成本
    commission = round(actual_amount * commission_rate, 2)
    transfer_fee = round(actual_amount * transfer_fee_rate, 2)
    # 印花税仅卖出单边 (B2/S1 修复: 卖出成本建模)
    stamp_duty = round(actual_amount * stamp_duty_rate, 2) if is_sell else 0.0
    if is_sell:
        # 卖出: total_cost 仅记录费用支出 (佣金+过户费+印花税);
        # fill_amount 为成交毛额, 净收入 = fill_amount - total_cost.
        total_cost = round(commission + transfer_fee + stamp_duty, 2)
        cost_avg = exec_price  # 卖出不重算剩余持仓均价 (见下方持仓同步)
    else:
        total_cost = actual_amount + commission + transfer_fee  # 含成本的买入总支出
        # 含成本均价 (用于 avg_cost, 真实持仓成本)
        cost_avg = round(total_cost / actual_qty, 4) if actual_qty > 0 else exec_price

    # 更新建仓进度 (用实际成交金额): 买入累加, 卖出减少 (B2 修复 — 卖单原恒为加)
    built_before = progress["built_amounts"].get(code, 0)
    if is_sell:
        progress["built_amounts"][code] = max(0.0, built_before - actual_amount)
        progress["total_built"] = max(0.0, progress.get("total_built", 0) - actual_amount)
    else:
        progress["built_amounts"][code] = built_before + actual_amount
        progress["total_built"] = progress.get("total_built", 0) + actual_amount

    # 同步 positions.json: 买入累加股数+加权均价, 卖出减少股数且成本基础不变 (B2 修复)
    if code in positions and actual_qty > 0:
        pos = positions[code]
        old_shares = pos.get("shares") or 0
        old_cost = pos.get("avg_cost") or 0.0
        if is_sell:
            new_shares = old_shares - actual_qty
            # 卖出后剩余持仓的成本基础不变 (已实现盈亏另计), 仅清仓时归零
            new_avg_cost = old_cost if new_shares > 0 else 0.0
        else:
            new_shares = old_shares + actual_qty
            if new_shares > 0:
                new_avg_cost = round((old_shares * old_cost + actual_qty * cost_avg) / new_shares, 4)
            else:
                new_avg_cost = cost_avg
        pos["shares"] = new_shares
        pos["est_price"] = exec_price  # 含滑点成交价
        pos["avg_cost"] = new_avg_cost

    return {
        "code": inst["code"],
        "name": inst["name"],
        "action": action,  # B2 修复: 透传指令 action (原硬编码 "BUY")
        "qty": actual_qty,
        "fill_price": exec_price,
        "fill_amount": actual_amount,
        "commission": commission,
        "transfer_fee": transfer_fee,
        "stamp_duty": stamp_duty,  # B2/S1: 卖出单边印花税 (买入为 0.0)
        "total_cost": total_cost,
        "status": "FILLED",
        "built_before": built_before,
        "built_after": progress["built_amounts"][code],
    }


def _build_and_save_execution_report(
    target_date_str: str,
    instructions_data: dict,
    confirmed: list,
    execution_results: list,
    progress: dict,
    instruction_file: Path,
) -> tuple:
    """构建并保存执行报告 (含 meta/summary/execution_results)。

    Args:
        target_date_str: 目标日期字符串
        instructions_data: 指令文件数据
        confirmed: 已确认指令列表
        execution_results: 执行结果列表
        progress: 建仓进度 (已更新)
        instruction_file: 指令文件路径

    Returns:
        (result_dict, report_file) 元组
    """
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

    # 保存执行报告 (原子写入, 防止进程中断导致文件损坏)
    report_file = INSTRUCTIONS_DIR / f"{target_date_str}_execution.json"
    atomic_write_json(report_file, execution_report)

    result = {
        "status": "executed",
        "report_file": str(report_file),
        "summary": execution_report["summary"],
    }
    return result, report_file


def execute_instructions(target_date_str: str) -> dict:
    """盘后执行已确认的交易指令

    读取指令文件, 执行 confirm=true 的指令,
    更新 positions.json (shares/avg_cost/est_price) 和 build_progress.json

    幂等保护: 若当日已执行过, 则跳过重复累加, 仅补同步 positions.json
    """
    instruction_file = INSTRUCTIONS_DIR / f"{target_date_str}_instructions.json"

    if not instruction_file.exists():
        return {"status": "error", "reason": f"指令文件不存在: {instruction_file}"}

    with open(instruction_file, encoding="utf-8") as f:
        instructions_data = json.load(f)

    # 执行前风控检查 + 筛选已确认指令
    confirmed, error = _check_execution_preconditions(instructions_data)
    if error:
        return error

    # v7.8+: 初始化 WT 模块用于执行
    wt_modules = init_wt_modules()

    # v7.8+: WT风控前置检查 (IC6: 风控一票否决)
    block_result = _run_wt_risk_block_check(wt_modules, confirmed)
    if block_result:
        return block_result

    # 幂等检查: 当日是否已执行过
    progress = load_build_progress()
    already_executed = any(
        r.get("date") == target_date_str for r in progress.get("daily_records", [])
    )

    # 加载持仓文件用于同步
    positions_data = load_positions()
    positions = positions_data.get("positions", {})

    # P1-1: 盘后止损止盈检查 (此前 StopLossManager 从未被调用)
    stop_loss_triggered = _run_stop_loss_check(wt_modules, positions)
    if stop_loss_triggered:
        logger.warning(
            "[StopLoss] %d 个标的触发止损/止盈, 需人工确认平仓操作",
            len(stop_loss_triggered),
        )

    if already_executed:
        # 幂等模式: 不重复累加 build_progress, 只补同步 positions.json
        result = _sync_positions_idempotent(target_date_str, confirmed, positions)
        # 保存 positions.json (P0-C1: 原子写, 防并发/崩溃写坏)
        positions_data["positions"] = positions
        atomic_write_json(POSITIONS_FILE, positions_data)
        return result

    # 首次执行: 累加 build_progress + 同步 positions.json
    # GLM-5.2 C1(#16) 修复: 幂等去重, 防进程在 progress 写成功后/positions 写前崩溃导致重跑双重建仓
    # 幂等键 = (full_code, action, qty), 已在 progress["executed_instruction_keys"] 持久化的指令直接跳过
    # Bug-5 修复: 幂等键加入 action, 避免同标的同 qty 的买卖指令被误判重复
    executed_keys = set(progress.get("executed_instruction_keys", []))
    execution_results = []
    for inst in confirmed:
        ide_key = f"{inst['full_code']}:{inst.get('action', 'BUY')}:{inst.get('qty', 0)}"
        if ide_key in executed_keys:
            logger.warning(f"[C1幂等] 指令 {ide_key} 已执行过, 跳过 (防双重建仓)")
            continue
        result = _execute_single_instruction(inst, wt_modules, progress, positions)
        if result.get("status") == "SKIPPED":
            logger.warning("[SKIP] 指令 %s 被跳过，不计入执行结果，允许后续重试", ide_key)
        else:
            execution_results.append(result)
            executed_keys.add(ide_key)

    # 记录每日执行
    progress.setdefault("daily_records", []).append(
        {
            "date": target_date_str,
            "executed_count": len(execution_results),
            "total_amount": sum(r["fill_amount"] for r in execution_results),
            "total_built_after": progress.get("total_built", 0) + sum(r.get("fill_amount", 0) for r in execution_results),
            "executed_at": datetime.now().isoformat(),
        }
    )
    # 持久化已执行指令键 (必须在 save_build_progress 前写入, 使 progress 写成功即代表已执行)
    progress["executed_instruction_keys"] = list(executed_keys)

    # GLM-5.2 C1(#16) 修复: fail-closed 语义
    # 保存顺序: 先 progress (执行日志+幂等键, 可重放) 后 positions (最终状态)
    # 若 progress 写失败 → 必须 fail-closed 中断, 禁止继续写 positions.
    #   原因: progress 未记录 executed_instruction_keys, 若此时更新 positions 会导致重跑时
    #         progress 判 already_executed=False → 重新执行全部指令 → 双重建仓 (致命).
    #   正确行为: progress 写失败则本次执行整体回滚, 留出人工/自动恢复窗口, 而非带病前进.
    # 若 positions 写失败 → progress 已记录 executed_instruction_keys, 重跑跳过已执行指令 (C1修复)
    try:
        save_build_progress(progress)
    except Exception as e:  # noqa: BLE001
        logger.error(f"[CRITICAL][C1] save_build_progress 失败: {e}, 中止写入 positions 以防双重建仓")
        return {
            "status": "error",
            "reason": f"build_progress 持久化失败: {e}",
            "executed_keys": list(executed_keys),
        }
    # 保存 positions.json (P0-C1: 原子写, 防并发/崩溃写坏)
    positions_data["positions"] = positions
    atomic_write_json(POSITIONS_FILE, positions_data)

    # 生成并保存执行报告
    result, _ = _build_and_save_execution_report(
        target_date_str, instructions_data, confirmed, execution_results, progress, instruction_file,
    )

    # 收盘后自动生成下一交易日计划
    try:
        next_plan = generate_next_trading_day_plan(target_date_str)
        result["next_trading_day_plan"] = next_plan
    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        logger.exception(f"生成下一交易日计划失败, 已降级记录错误: {e}")
        result["next_trading_day_plan"] = {
            "status": "error",
            "reason": f"生成下一交易日计划失败: {e}",
        }

    return result


def show_progress() -> dict:
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


def generate_accumulation_schedule() -> dict:
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
            schedule.append(
                {
                    "date": current.isoformat(),
                    "daily_budget": round(daily_budget, 2),
                    "cumulative": round(total, 2),
                    "completion_pct": round(completion, 2),
                    "remaining": round(STOCK_ETF_TARGET - total, 2),
                }
            )
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


def main() -> None:
    parser = argparse.ArgumentParser(description="每日自动执行交易计划")
    parser.add_argument(
        "mode", choices=["pre-market", "post-market", "post-market-auto", "progress", "schedule"], help="执行模式"
    )
    parser.add_argument("--date", type=str, default=None, help="指定日期 (YYYY-MM-DD), 默认今天")
    parser.add_argument("--auto-confirm", action="store_true", help="自动确认所有指令 (跳过人工确认环节)")

    args = parser.parse_args()

    if args.date:
        target_date = args.date
    else:
        target_date = datetime.now().strftime("%Y-%m-%d")

    # P0-C1: 跨进程防重入锁 (Windows 计划任务重复触发防护)
    # 只对会写 positions.json/build_progress.json 的执行模式加锁
    if args.mode in ("pre-market", "post-market", "post-market-auto"):
        from utils.concurrency import process_lock

        with process_lock("daily_trade_executor", timeout=5.0) as acquired:
            if not acquired:
                logger.warning("[WARN] 另一个 daily_trade_executor 实例正在运行, 本次退出")
                sys.exit(1)
            _run_mode(args, target_date)
        return
    _run_mode(args, target_date)


def _run_mode(args: argparse.Namespace, target_date: str) -> None:
    logger.info("=" * 70)
    logger.info(f"每日自动执行交易计划 - {args.mode} - {target_date}")
    if args.auto_confirm:
        logger.info("Auto-confirm mode: all instructions will be confirmed")
    logger.info("=" * 70)

    if args.mode == "pre-market":
        result = generate_instructions(target_date)

        # 自动确认所有指令
        if args.auto_confirm and result.get("status") == "generated":
            confirm_count = confirm_all_instructions(target_date)
            result["auto_confirmed"] = True
            result["auto_confirm_count"] = confirm_count
            logger.info(f"[INFO] Auto-confirmed {confirm_count} instructions")

        logger.info(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    elif args.mode == "post-market":
        result = execute_instructions(target_date)
        logger.info(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    elif args.mode == "post-market-auto":
        execute_result = execute_instructions(target_date)
        logger.info(json.dumps(execute_result, ensure_ascii=False, indent=2, default=str))

        # 自动生成下一交易日计划
        if execute_result.get("status") == "executed":
            logger.info("\n" + "=" * 70)
            logger.info("Generating next trading day plan")
            logger.info("=" * 70)
            next_plan = generate_next_trading_day_plan(target_date)

            # 自动确认下一交易日计划
            if args.auto_confirm and next_plan.get("status") == "generated":
                next_date = next_plan.get("next_trading_day", "")
                if next_date:
                    confirm_count = confirm_all_instructions(next_date)
                    next_plan["auto_confirmed"] = True
                    next_plan["auto_confirm_count"] = confirm_count
                    logger.info(f"[INFO] Auto-confirmed {confirm_count} instructions for next day {next_date}")

            logger.info(json.dumps(next_plan, ensure_ascii=False, indent=2, default=str))

    elif args.mode == "progress":
        result = show_progress()
        logger.info(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    elif args.mode == "schedule":
        result = generate_accumulation_schedule()
        logger.info(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
