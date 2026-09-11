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
import os
import sys
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 项目根目录
PROJECT_ROOT = Path(__file__).parent
# Wave 3 第三阶段: 改用 utils.path_config.setup_sys_path() 统一管理
sys.path.insert(0, str(PROJECT_ROOT))  # bootstrap: 确保 utils 包可导入
from utils.path_config import setup_sys_path  # noqa: E402

setup_sys_path()  # noqa: E402  # 统一注入 v8.3 根 / v8.3 src / utils
from utils.concurrency import atomic_write_json  # noqa: E402  # P0-C1 原子写

# 审计 item 8 (2026-09-10): 业务时间走 now_bj() (naive 北京时间), 消除本机时区依赖
from utils.datetime_utils import now_bj  # noqa: E402

# DTE-1 (2026-08-24): 建仓执行接入 FillsStore 事实源, 使模拟成交可追溯 (不再账本自我记账)
from utils.execution.fills_store import FillsStore  # noqa: E402

# B1.2: 统一使用 utils.trade_calendar 判断交易日 (支持节假日)
from utils.trade_calendar import is_trading_day  # noqa: E402

POSITIONS_FILE = PROJECT_ROOT / "config" / "positions.json"
TRADE_PLAN_FILE = (
    PROJECT_ROOT
    / "v8.3_institutional"
    / "trade_plans"
    / "auto_trade_plan_500w_2026-2030.json"
)
INSTRUCTIONS_DIR = PROJECT_ROOT / "trade_instructions"
PROGRESS_FILE = PROJECT_ROOT / "trade_instructions" / "build_progress.json"

# B-4.5: 风控参数从 config/trade_execution.yaml 加载 (失败回退到硬编码默认值)
# S-1 (2026-09-11, Issue #13): 上行阈值统一走单一事实源 config/risk_thresholds.yaml
# (止损/止盈原为模块内硬编码 8%/15%, 与熔断线 3%/5% 是两套互不相干口径)
from utils.config_manager import get_config as _get_trade_cfg  # noqa: E402
from utils.risk_thresholds import get_stop_loss_config as _get_stop_loss_cfg  # noqa: E402

_stop_loss_cfg = _get_stop_loss_cfg()

_trade_cfg = _get_trade_cfg("trade_execution") or {}

# P1-2 降级闭环 (2026-09-01): trade_execution.yaml 缺失时全部风控参数
# (单日限额/价格保护带/止损熔断线/回撤熔断线) 走硬编码默认值 —
# 原实现仅一条无人看的 ConfigManager 日志。闭环三件套:
#   1) 降级审计落盘 reports/degradation_log.jsonl (事后可查)
#   2) 醒目 WARNING (盘中日志可见)
#   3) QUANT_STRICT_CONFIG=1 时硬失败 (关键任务部署用, 防止基于默认风控线交易)
if not _trade_cfg:
    import os as _os

    from utils.degradation_audit import record_degradation

    record_degradation(
        scope="daily_trade_executor",
        key="config/trade_execution.yaml",
        default=(
            "硬编码风控默认值: daily_amount_limit=200000, price_protection_pct=3%, "
            "daily_loss_stop_pct=3%, portfolio_drawdown_stop_pct=5%"
        ),
        reason="配置文件不存在, 全部风控参数使用硬编码默认值",
    )
    logger.warning(
        "[风控配置降级] config/trade_execution.yaml 不存在 — 单日限额/止损熔断线/"
        "回撤熔断线等全部使用硬编码默认值; 降级事件已记录 reports/degradation_log.jsonl"
    )
    if _os.environ.get("QUANT_STRICT_CONFIG", "").strip().lower() in {"1", "true", "yes"}:
        raise RuntimeError(
            "[QUANT_STRICT_CONFIG] 关键风控配置 config/trade_execution.yaml 缺失, "
            "strict 模式下拒绝以默认风控参数继续执行"
        )


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
DAILY_LOSS_STOP_PCT = _trade_cfg.get(
    "daily_loss_stop_pct", 0.03
)  # 单日累计亏损 -3% 熔断
PORTFOLIO_DRAWDOWN_STOP_PCT = _trade_cfg.get(
    "portfolio_drawdown_stop_pct", 0.05
)  # 组合回撤 -5% 熔断

# 建仓期参数 (phase_1_accumulation)
ACCUMULATION_START = _parse_date_from_cfg(
    _trade_cfg.get("accumulation_start"), date(2026, 7, 10)
)
ACCUMULATION_END = _parse_date_from_cfg(
    _trade_cfg.get("accumulation_end"), date(2026, 12, 31)
)
STOCK_ETF_TARGET = _trade_cfg.get("stock_etf_target", 3_000_000)  # 300万

# 固定日预算起始日 (2026-07-13起改为动态信号加权预算)
FIXED_BUDGET_START = _parse_date_from_cfg(
    _trade_cfg.get("fixed_budget_start"), date(2026, 7, 13)
)
DAILY_FIXED_BUDGET = _trade_cfg.get(
    "daily_fixed_budget", 200_000
)  # 单日金额上限/参考值

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

# ---- 交易成本模型 + WT 执行算法参数 (P2 修复 2026-09-10) ----
# 审计 P2: "滑点 10bp / ADUV=1,000,000 硬编码魔法数字"。
# 此前 slippage_rate/commission_rate/transfer_fee_rate/stamp_duty_rate 与 WT
# 拆分阈值、兜底日均成交额全部内联硬编码, 注释却写"可配置"。现统一收敛为
# 「配置段 > 环境变量 > 内置默认值」三级取值; 缺省值与历史硬编码逐位一致
# (行为零变化), 调参只需改 configs/trade_execution.yaml 的 cost_model 段
# 或设 QUANT_<KEY> 环境变量, 无需改代码。
_cost_model = _trade_cfg.get("cost_model") or {}


def _cost_param(key: str, default: float) -> float:
    """成本参数三级取值: cost_model 段 > 环境变量 QUANT_<KEY> > 内置默认值。

    configs/ 与 config/ 均被 .gitignore 排除 (事实源 = ROADMAP+LOG), 故必须保留
    环境变量通道, 使成本参数在任何部署环境下都可覆盖而不依赖未跟踪文件。
    """
    if key in _cost_model:
        try:
            return float(_cost_model[key])
        except (TypeError, ValueError) as e:
            logger.warning("cost_model.%s 非数值 (%r), 回退默认 %s: %s", key, _cost_model[key], default, e)
    env_raw = os.environ.get(f"QUANT_{key.upper()}", "").strip()
    if env_raw:
        try:
            return float(env_raw)
        except ValueError as e:
            logger.warning("环境变量 QUANT_%s=%r 非数值, 回退默认 %s: %s", key.upper(), env_raw, default, e)
    return default


SLIPPAGE_RATE = _cost_param("slippage_rate", 0.001)  # 滑点 10bp
COMMISSION_RATE = _cost_param("commission_rate", 0.0003)  # 佣金 0.03%
TRANSFER_FEE_RATE = _cost_param("transfer_fee_rate", 0.00001)  # 过户费 0.001%
STAMP_DUTY_RATE = _cost_param("stamp_duty_rate", 0.0005)  # 印花税 0.05%(卖出)
# WT 最小冲击执行算法: 触发拆分的最小金额 与 未注入 ADV 时的兜底日均成交额。
# 兜底值仅用于让拆分算法有输入, 不代表真实流动性 → 单一指令超过该量级时告警。
WT_SPLIT_MIN_AMOUNT = _cost_param("wt_split_min_amount", 50_000)
WT_ASSUMED_ADV = _cost_param("wt_assumed_adv", 1_000_000)


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
    if code.startswith(("6", "5", "9")):
        return f"{code}.SH"
    if code.startswith(("0", "2", "3")) or code.startswith(("159", "16")):
        return f"{code}.SZ"
    if code.startswith("8"):
        return f"{code}.BJ"
    return f"{code}.SH"  # 默认上海


def init_wt_modules() -> dict[str, Any]:
    """初始化 WonderTrader 风格模块

    返回: dict 包含所有WT模块实例, 失败时返回空dict
    """
    wt_modules = {}
    try:
        from utils.wt_contracts_manager import get_contracts_manager
        from utils.wt_execution_algo import (
            MinImpactExecutor,
            TWAPExecutor,
            VWAPExecutor,
        )
        from utils.wt_hedge_strategy import (
            BetaHedgeStrategy,
            HedgeContext,
            TailRiskHedgeStrategy,
        )
        from utils.wt_risk_control import (
            PortfolioRiskAnalyzer,
            RiskControl,
            StopLossManager,
        )

        # P0-4 修复 (2026-09-11): 补齐 RiskControl 默认配置缺的 4 个键
        # (circuit_breaker_enabled/max_daily_volume/stop_loss_enabled/position_limit_enabled)。
        # 原配置缺键 → check_circuit_breaker()/check_position_concentration()
        # 第一行 self.config[...] 直接 KeyError → 被 _run_wt_risk_block_check 的
        # except 吞成"保守阻断", 熔断/集中度/仓位限制三块从未真正执行且不报错。
        # 阈值与 config/trade_execution.yaml 对齐 (单日亏损 3% / 组合回撤 5%)。
        wt_modules["risk_control"] = RiskControl(
            {
                "max_daily_loss_pct": DAILY_LOSS_STOP_PCT,
                "max_portfolio_drawdown_pct": PORTFOLIO_DRAWDOWN_STOP_PCT,
                "max_position_concentration_pct": 0.30,
                "max_single_trade_pct": 0.05,
                "max_daily_trades": 50,
                "max_daily_volume": 10_000_000,
                "circuit_breaker_enabled": True,
                "stop_loss_enabled": True,
                "position_limit_enabled": True,
            }
        )
        # S-1 修复 (2026-09-11): 阈值改从 config/risk_thresholds.yaml 读取
        # (原硬编码 0.08/0.15, 与 trade_execution.yaml 熔断线 3%/5% 两套互不相干口径)
        wt_modules["stop_loss_manager"] = StopLossManager(
            stop_loss_pct=_stop_loss_cfg["stop_loss_pct"],
            take_profit_pct=_stop_loss_cfg["take_profit_pct"],
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
        logger.info(
            "[INFO]   - 风控模块: RiskControl, StopLossManager, PortfolioRiskAnalyzer"
        )
        logger.info(
            "[INFO]   - 执行算法: MinImpactExecutor, TWAPExecutor, VWAPExecutor"
        )
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
                strength = (
                    pred.signal_strength if hasattr(pred, "signal_strength") else 0.0
                )
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
_PREDICTION_PRICES_INDEX: dict[str, list[float]] | None = None
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
        # DTE-3: 止损管理器不可用时, 返回特殊标记项让调用方 L1686 告警可见,
        # 而非静默返回 [] (否则止损风控完全失效且无人察觉)。
        logger.error("[StopLoss] stop_loss_manager 不可用, 止损检查被跳过 (风控降级)")
        return [
            {
                "code": "__manager_unavailable",
                "action": "HOLD",
                "order_info": "stop_loss_manager 不可用, 止损风控降级",
            }
        ]

    triggered = []
    for code, item in positions.items():
        avg_cost = item.get("avg_cost", 0)
        qty = (
            item.get("phase1_shares")
            or item.get("total_shares")
            or item.get("shares", 0)
        )
        current_price = item.get("est_price", 0)
        if avg_cost <= 0 or qty == 0 or current_price <= 0:
            continue

        pure_code = code.split(".")[0]
        if pure_code not in sl_manager.stop_loss_orders:
            sl_manager.set_stop_loss(pure_code, avg_cost, abs(qty))

        action, order_info = sl_manager.check_stop_loss(pure_code, current_price)
        if action != "none" and order_info:
            triggered.append(
                {
                    "code": code,
                    "name": item.get("name", code),
                    "action": action,
                    "current_price": current_price,
                    "stop_price": order_info.get("stop_price", 0),
                    "take_profit_price": order_info.get("take_profit_price", 0),
                    "pnl_pct": round((current_price - avg_cost) / avg_cost, 4),
                }
            )
            # P&L 用 %.1f%% 而非 %+.1%%: 后者经 printf 解析会残留裸 "%" 并抛
            # ValueError: unsupported format character (日志本身再触发异常)
            logger.warning(
                "[StopLoss] %s (%s) 触发 %s @ ¥%.3f (P&L %+.1f%%)",
                item.get("name", code),
                code,
                action,
                current_price,
                ((current_price - avg_cost) / avg_cost) * 100,
            )

    if not triggered:
        logger.info("[StopLoss] 全部持仓在安全范围内, 无触发")
    else:
        logger.warning("[StopLoss] 共 %d 个标的触发止损/止盈", len(triggered))
    return triggered


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

    # P0-4 闭环 (2026-09-11 · 缺口 B): 消费 circuit_breaker, fail-closed。
    cb_block = check_circuit_breaker_gate(risk_checks)
    if cb_block is not None:
        return [], cb_block

    confirmed = [
        i for i in instructions_data.get("instructions", []) if i.get("confirm", False)
    ]
    if not confirmed:
        return [], {
            "status": "no_confirmed",
            "reason": "无已确认指令 (所有 confirm=false)",
            "total_instructions": len(instructions_data.get("instructions", [])),
        }
    return confirmed, None


def _run_wt_risk_block_check(wt_modules: dict, confirmed: list) -> dict | None:
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

        # P0-4 闭环 (2026-09-11): 消费熔断。原实现只调 check_single_trade +
        # check_daily_trade_count —— 配置补键后 check_circuit_breaker /
        # check_position_concentration 仍**从未被本路径执行** (阈值接了但不生效)。
        # 注意: 未喂权益时 check_circuit_breaker 的两个分支短路返回 True,
        # 即"没有数据"不会被这里误判为熔断 (真正的 UNKNOWN 阻断在
        # _check_execution_preconditions 消费 premarket 的 passed 字段)。
        try:
            ok, msg = rc.check_circuit_breaker()
            if not ok:
                logger.warning(f"[BLOCK] WT风控熔断检查未通过: {msg}")
                risk_blocked = True
        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.error(f"[WARN] WT风控熔断检查执行异常: {e}")
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


def _sync_positions_idempotent(
    target_date_str: str, confirmed: list, positions: dict
) -> dict:
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


def _record_build_fill(inst: dict, result: dict, target_date_str: str) -> None:
    """DTE-1 (2026-08-24): 建仓成交落盘 FillsStore 事实源.

    使 daily_trade_executor 的"账本自我记账"成为可追溯成交 (reports/fills/fills_YYYY-MM-DD.jsonl),
    供 PnL / TCA / 影子账户消费, 与 rebalance (strategy=rebalance) / hedge (strategy=hedge)
    执行器一致。仅记录实际成交 (status=FILLED), SKIPPED/异常不落盘。

    观测路径 fail-open: FillsStore.record_fill 本身 fail-open (异常只记日志), 不影响执行链路。
    """
    if result.get("status") != "FILLED":
        return
    qty = float(result.get("qty", 0) or 0)
    fill_price = float(result.get("fill_price", 0) or 0)
    if qty <= 0 or fill_price <= 0:
        return
    symbol = str(inst.get("full_code") or inst.get("code", ""))
    side = str(result.get("action", "BUY"))
    try:
        FillsStore().record_fill(
            symbol=symbol,
            side=side,
            filled_qty=qty,
            avg_price=fill_price,
            broker="SimulatedBroker",
            is_live=False,
            strategy="build",
            source="sim_route",
            date=target_date_str,
            meta={
                "slippage_rate": inst.get("slippage", 0.0),
                "commission": result.get("commission", 0.0),
                "transfer_fee": result.get("transfer_fee", 0.0),
                "stamp_duty": result.get("stamp_duty", 0.0),
            },
        )
        logger.info(
            f"[DTE-1] 建仓成交已落盘 FillsStore: {symbol} {side} {qty:.0f}@{fill_price:.4f}"
        )
    except Exception as e:  # noqa: BLE001  # 观测路径 fail-open, 不阻断建仓执行
        logger.warning(f"[DTE-1] 建仓成交落盘失败 (不影响执行): {e}")


def _execute_single_instruction(
    inst: dict, wt_modules: dict, progress: dict, positions: dict
) -> dict:
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
    # P2 修复 (2026-09-10): 费率改由 configs/trade_execution.yaml cost_model 段驱动
    slippage_rate = SLIPPAGE_RATE
    commission_rate = COMMISSION_RATE
    transfer_fee_rate = TRANSFER_FEE_RATE
    stamp_duty_rate = STAMP_DUTY_RATE
    # 实际成交价 (含滑点): 买入向上, 卖出向下 (B2/S1 修复 — 卖单原错误地恒为加仓+滑点上浮)
    slippage_sign = -1.0 if is_sell else 1.0
    exec_price = round(ref_price * (1.0 + slippage_sign * slippage_rate), 4)

    # v7.8+: 使用 WT 执行算法拆分订单 (大金额订单)
    fill_amount = 0.0

    # IC2 修复: 字段名 "amount" → "estimated_amount" (与指令字典字段名一致)
    inst_amount = inst.get("estimated_amount", 0) or 0
    if wt_modules.get("min_impact_executor") and inst_amount > WT_SPLIT_MIN_AMOUNT:
        if inst_amount > WT_ASSUMED_ADV:
            # 兜底 ADV 只是拆分算法的输入而非真实流动性 → 超量级时显式告警, 避免
            # 用"假装有流动性"的 ADV 算出的低成本静默流入 PnL。
            logger.warning(
                "[WARN] %s 单笔金额 %.0f 超过兜底日均成交额 %.0f, "
                "拆分结果的冲击成本估计偏乐观 (建议注入真实 ADV)",
                inst["code"],
                inst_amount,
                WT_ASSUMED_ADV,
            )
        try:
            splits = wt_modules["min_impact_executor"].calculate_optimal_splits(
                target_amount=inst_amount,
                ref_price=ref_price,
                avg_daily_volume=WT_ASSUMED_ADV,
            )
            fill_amount = round(sum(s["amount"] for s in splits), 2)
            logger.info(
                f"[INFO] WT执行算法: {inst['code']} 拆分为 {len(splits)} 笔, 总金额 {fill_amount:,.0f}"
            )
        except Exception as e:  # noqa: BLE001
            # 第三方 WT 模块异常边界: 拆分失败不阻断成交, fail-open 回退到默认执行
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
        progress["total_built"] = max(
            0.0, progress.get("total_built", 0) - actual_amount
        )
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
                new_avg_cost = round(
                    (old_shares * old_cost + actual_qty * cost_avg) / new_shares, 4
                )
            else:
                new_avg_cost = cost_avg
        pos["shares"] = new_shares
        pos["est_price"] = exec_price  # 含滑点成交价
        pos["avg_cost"] = new_avg_cost

    exec_result = {
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

    # DTE-1 (2026-08-24): 建仓成交落盘 FillsStore 事实源 (观测路径 fail-open)
    try:
        _record_build_fill(inst, exec_result, now_bj().strftime("%Y-%m-%d"))
    except Exception as e:  # noqa: BLE001  # 落盘失败不影响建仓执行
        logger.warning(f"[DTE-1] 建仓成交落盘调用失败 (不影响执行): {e}")

    return exec_result


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
            "executed_at": now_bj().isoformat(),
            "instruction_file": str(instruction_file),
        },
        "summary": {
            "total_instructions": len(instructions_data.get("instructions", [])),
            "confirmed_count": len(confirmed),
            "executed_count": len(execution_results),
            "total_executed_amount": sum(r["fill_amount"] for r in execution_results),
            "total_built": progress["total_built"],
            "remaining": STOCK_ETF_TARGET - progress["total_built"],
            "completion_rate": round(
                progress["total_built"] / STOCK_ETF_TARGET * 100, 2
            ),
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
    # P0-3 修复 (2026-09-11): 执行路径 fail-closed — positions.json 缺失/损坏时
    # 此前静默降级为空持仓, 止损检查循环空转、同步逻辑在空集合上操作,
    # 与"正常空仓"不可区分。现显式阻断并要求人工介入 (决策路径不得静默降级)。
    if not POSITIONS_FILE.exists():
        logger.error(
            "[P0-3] 持仓文件不存在: %s — 止损/同步将在空持仓上静默空转, 阻断执行",
            POSITIONS_FILE,
        )
        return {
            "status": "blocked",
            "message": f"持仓文件缺失: {POSITIONS_FILE}, 阻断执行 (fail-closed)",
            "blocked_reason": "positions.json missing (P0-3 fail-closed)",
        }
    positions_data = load_positions()
    if not positions_data:
        logger.error(
            "[P0-3] 持仓文件存在但无法解析: %s — 阻断执行 (fail-closed)",
            POSITIONS_FILE,
        )
        return {
            "status": "blocked",
            "message": f"持仓文件损坏或为空: {POSITIONS_FILE}, 阻断执行 (fail-closed)",
            "blocked_reason": "positions.json unreadable (P0-3 fail-closed)",
        }
    positions = positions_data.get("positions", {})

    # P0-4 闭环 (2026-09-11 · 缺口 A): 为 RiskControl 喂真实权益, 解除熔断/集中度短路。
    # 置于持仓校验后 (权益口径依赖同一份持仓明细), 见 executor/risk_feed.py。
    _feed_risk_control_equity(wt_modules, positions)

    # P1-1: 盘后止损止盈检查 (此前 StopLossManager 从未被调用)
    # S-1 修复 (2026-09-11, Issue #13): 检测结果从"仅打日志"升级为**阻断性告警**。
    # 原实现只写一条 WARNING 且不再使用 stop_loss_triggered —— 叠加当时
    # StopLossManager 触发即锁死状态机, 导致"错过一条 WARNING = 该标的止损保护永久消失"。
    # 现: 触发 → 阻断本次执行 (需人工确认) + 写入告警明细 + 状态机保持待确认可重试。
    stop_loss_triggered = _run_stop_loss_check(wt_modules, positions)

    # DTE-3 降级标记 (止损管理器不可用) 不是"标的触发止损", 不得走 S-1 阻断路径 ——
    # 否则止损模块缺失会把整条盘后执行链一并阻断 (风控降级误伤主链)。
    stop_loss_degraded = [
        t for t in stop_loss_triggered if t.get("code") == "__manager_unavailable"
    ]
    if stop_loss_degraded:
        logger.error(
            "[StopLoss] 止损模块不可用, 本次未执行任何止损检查 "
            "(降级可见, 不阻断主链; 请修复 stop_loss_manager 初始化)"
        )
    stop_loss_triggered = [
        t for t in stop_loss_triggered if t.get("code") != "__manager_unavailable"
    ]

    if stop_loss_triggered:
        blocking_cfg = _stop_loss_cfg.get("block_on_trigger", True)
        summary = ", ".join(
            f"{t.get('name', t.get('code'))}({t.get('action')}, P&L {t.get('pnl_pct', 0):+.1%})"
            for t in stop_loss_triggered
        )
        logger.warning(
            "[StopLoss] %d 个标的触发止损/止盈, 需人工确认平仓操作: %s",
            len(stop_loss_triggered),
            summary,
        )
        if blocking_cfg:
            result = {
                "status": "blocked",
                "reason": (
                    f"{len(stop_loss_triggered)} 个标的触发止损/止盈, 阻断执行 "
                    f"(S-1 阻断性告警, 需人工确认; 确认后调用 "
                    f"StopLossManager.acknowledge_stop_loss 解除)"
                ),
                "blocked_reason": "stop_loss_triggered (S-1)",
                "stop_loss_alerts": stop_loss_triggered,
            }
            logger.error("[StopLoss] 阻断本次执行 (block_on_trigger=true): %s", summary)
            return result

    if already_executed:
        # 幂等模式: 不重复累加 build_progress, 只补同步 positions.json
        result = _sync_positions_idempotent(target_date_str, confirmed, positions)
        # 保存 positions.json (P0-C1: 原子写, 防并发/崩溃写坏)
        positions_data["positions"] = positions
        atomic_write_json(POSITIONS_FILE, positions_data)
        return result

    # 首次执行: 累加 build_progress + 同步 positions.json
    # GLM-5.2 C1(#16) 修复: 幂等去重, 防进程在 progress 写成功后/positions 写前崩溃导致重跑双重建仓
    # 幂等键 = (full_code, action, qty, ref_price), 已在 progress["executed_instruction_keys"] 持久化的指令直接跳过
    # Bug-5 修复: 幂等键加入 action, 避免同标的同 qty 的买卖指令被误判重复
    # P2 修复 (2026-09-09): 幂等键加入 ref_price, 避免同标的同方向同数量不同价格的合法分批指令被误杀
    executed_keys = set(progress.get("executed_instruction_keys", []))
    execution_results = []
    for inst in confirmed:
        ide_key = (
            f"{inst['full_code']}:{inst.get('action', 'BUY')}:{inst.get('qty', 0)}:{inst.get('ref_price', 0)}"
        )
        if ide_key in executed_keys:
            logger.warning(f"[C1幂等] 指令 {ide_key} 已执行过, 跳过 (防双重建仓)")
            continue
        result = _execute_single_instruction(inst, wt_modules, progress, positions)
        if result.get("status") == "SKIPPED":
            logger.warning(
                "[SKIP] 指令 %s 被跳过，不计入执行结果，允许后续重试", ide_key
            )
        else:
            execution_results.append(result)
            executed_keys.add(ide_key)

    # 记录每日执行
    progress.setdefault("daily_records", []).append(
        {
            "date": target_date_str,
            "executed_count": len(execution_results),
            "total_amount": sum(r["fill_amount"] for r in execution_results),
            # DTE-7: total_built 已在 _execute_single_instruction 累加过 actual_amount,
            # 此处直接读进度值即可, 再加本次成交额会造成"重复累加/虚增当日成交额"。
            "total_built_after": progress.get("total_built", 0),
            "executed_at": now_bj().isoformat(),
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
        logger.error(
            f"[CRITICAL][C1] save_build_progress 失败: {e}, 中止写入 positions 以防双重建仓"
        )
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
        target_date_str,
        instructions_data,
        confirmed,
        execution_results,
        progress,
        instruction_file,
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
    completion_rate = (
        total_built / STOCK_ETF_TARGET * 100 if STOCK_ETF_TARGET > 0 else 0
    )

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
        "avg_daily_needed": (
            round(avg_daily, 2) if isinstance(avg_daily, float) else avg_daily
        ),
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
        "mode",
        choices=[
            "pre-market",
            "post-market",
            "post-market-auto",
            "progress",
            "schedule",
        ],
        help="执行模式",
    )
    parser.add_argument(
        "--date", type=str, default=None, help="指定日期 (YYYY-MM-DD), 默认今天"
    )
    parser.add_argument(
        "--auto-confirm",
        action="store_true",
        help="自动确认所有指令 (跳过人工确认环节)",
    )

    args = parser.parse_args()

    if args.date:
        target_date = args.date
    else:
        target_date = now_bj().strftime("%Y-%m-%d")

    # P0-C1: 跨进程防重入锁 (Windows 计划任务重复触发防护)
    # 只对会写 positions.json/build_progress.json 的执行模式加锁
    if args.mode in ("pre-market", "post-market", "post-market-auto"):
        from utils.concurrency import process_lock

        with process_lock("daily_trade_executor", timeout=5.0) as acquired:
            if not acquired:
                logger.warning(
                    "[WARN] 另一个 daily_trade_executor 实例正在运行, 本次退出"
                )
                sys.exit(1)
            _run_mode(args, target_date)
        return
    _run_mode(args, target_date)


def _run_mode(args: argparse.Namespace, target_date: str) -> None:
    logger.info("=" * 70)
    logger.info(f"每日自动执行交易计划 - {args.mode} - {target_date}")

    # DTE-6 (2026-08-24): --auto-confirm 风控护栏 — 自动确认会绕过人工审核直接下单,
    # 仅在显式声明的演练/生产环境 (TRADING_ENV ∈ {shadow, production}) 下允许;
    # 否则 (默认 ci/sim/未设置) 告警并降级为"不自动确认" (安全默认, 需人工确认)。
    if args.auto_confirm:
        _env = os.environ.get("TRADING_ENV", "sim").strip().lower()
        if _env not in ("shadow", "production"):
            logger.error(
                "[BLOCK] --auto-confirm 需显式 TRADING_ENV=shadow|production (当前=%s), "
                "已降级为不自动确认 (人工确认保护), 跳过全部自动确认",
                _env,
            )
            args.auto_confirm = False
        else:
            logger.info(
                "Auto-confirm mode enabled (TRADING_ENV=%s): all instructions will be confirmed",
                _env,
            )
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
        logger.info(
            json.dumps(execute_result, ensure_ascii=False, indent=2, default=str)
        )

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
                    logger.info(
                        f"[INFO] Auto-confirmed {confirm_count} instructions for next day {next_date}"
                    )

            logger.info(
                json.dumps(next_plan, ensure_ascii=False, indent=2, default=str)
            )

    elif args.mode == "progress":
        result = show_progress()
        logger.info(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    elif args.mode == "schedule":
        result = generate_accumulation_schedule()
        logger.info(json.dumps(result, ensure_ascii=False, indent=2, default=str))


# ---------------------------------------------------------------------------
# 2026-09-10 拆解: 盘前指令生成簇已迁至 executor/premarket.py
# 在此重导出 (三保险):
#   1) ``dte.NAME`` / ``from daily_trade_executor import NAME`` 对外契约不变;
#   2) 宿主命名空间仍是 monkeypatch 的唯一权威入口 —— premarket 内部一律以
#      ``_h().NAME`` 属性式读取, 故 monkeypatch.setattr(dte, NAME, ...) 依旧生效;
#   3) 日志 / 既有测试 / 定时任务无需任何改动。
# 别名 ``X as X`` 是 ruff 认可的"显式重导出"写法, 不会触发 F401。
# ---------------------------------------------------------------------------
from executor.premarket import DEFAULT_PRICES as DEFAULT_PRICES  # noqa: E402
from executor.premarket import _allocate_position as _allocate_position  # noqa: E402
from executor.premarket import _build_instruction_file as _build_instruction_file  # noqa: E402
from executor.premarket import _build_risk_checks as _build_risk_checks  # noqa: E402
from executor.premarket import _collect_pending_positions as _collect_pending_positions  # noqa: E402
from executor.premarket import _compute_price_band as _compute_price_band  # noqa: E402
from executor.premarket import _compute_progress_ratio as _compute_progress_ratio  # noqa: E402
from executor.premarket import (  # noqa: E402
    _precheck_instructions_preconditions as _precheck_instructions_preconditions,
)
from executor.premarket import _refresh_etf_flow as _refresh_etf_flow  # noqa: E402
from executor.premarket import _run_wt_risk_precheck as _run_wt_risk_precheck  # noqa: E402
from executor.premarket import _save_instruction_file as _save_instruction_file  # noqa: E402
from executor.premarket import adjust_allocation_by_signal as adjust_allocation_by_signal  # noqa: E402
from executor.premarket import assess_etf_signal as assess_etf_signal  # noqa: E402
from executor.premarket import calculate_daily_budget as calculate_daily_budget  # noqa: E402
from executor.premarket import confirm_all_instructions as confirm_all_instructions  # noqa: E402
from executor.premarket import generate_instructions as generate_instructions  # noqa: E402
from executor.premarket import generate_next_trading_day_plan as generate_next_trading_day_plan  # noqa: E402
from executor.premarket import is_accumulation_period as is_accumulation_period  # noqa: E402
from executor.premarket import load_latest_prices as load_latest_prices  # noqa: E402
from executor.premarket import load_trade_plan as load_trade_plan  # noqa: E402
from executor.premarket import render_instructions_md as render_instructions_md  # noqa: E402

# ---------------------------------------------------------------------------
# P0-4 闭环 (2026-09-11): 喂数辅助迁出到 executor/risk_feed.py (宿主 1500 行护栏)。
# 同上: 显式 ``X as X`` 重导出, 宿主命名空间仍是 monkeypatch 权威入口。
# ---------------------------------------------------------------------------
from executor.risk_feed import _compute_positions_equity as _compute_positions_equity  # noqa: E402
from executor.risk_feed import _feed_risk_control_equity as _feed_risk_control_equity  # noqa: E402
from executor.risk_feed import (  # noqa: E402
    check_circuit_breaker_gate as check_circuit_breaker_gate,
)

if __name__ == "__main__":
    main()
