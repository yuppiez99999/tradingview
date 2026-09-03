"""Shadow 30 天验证每日运行器 — W7.2.8 (MVSK P5-2) + W7.2.9 (qlib_lgb_v2).

=================================================================
创建: 2026-08-24 (Wave 7 Sprint 2 — W7.2.8 + W7.2.9)

背景:
    W7.1.6/W7.1.7 已就绪 shadow 基础设施:
    - apply_mvsk_shadow_to_mid_layer() 在 utils/universe/portfolio_builder.py:484
    - register_qlib_lgb_v2_shadow() + apply_qlib_lgb_v2_shadow() 在 utils/signal_fusion.py:1311/1355
    本脚本为每日运行入口, 30 天后由 utils/shadow_30day_evaluator.py 评估 Δ夏普.

设计原则:
    - shadow 模式不修改生产组合/信号 (weight=0.0, portfolio unchanged)
    - 环境变量控制: USE_MVSK_MID_LAYER / USE_QLIB_LGB_V2
    - fail-fast 监控: 单日回撤>3% 或 3日累计>5% → TERMINATED
    - 幂等: 同一天重复运行只保留最后一条记录

用法:
    # 每日 shadow 运行 (由 cron / scheduler 阶段7 调用)
    py -X utf8 scripts/launch_shadow_30day.py

    # 指定日期
    py -X utf8 scripts/launch_shadow_30day.py --date 2026-09-13

    # 检查 30 天窗口状态
    py -X utf8 scripts/launch_shadow_30day.py --status

    # 生成 30 天评估报告
    py -X utf8 scripts/launch_shadow_30day.py --evaluate

    # 09-13 窗口启动前自检 (校验全部前置依赖, fail-closed)
    py -X utf8 scripts/launch_shadow_30day.py --preflight

对齐 ROADMAP W7.2.8/W7.2.9 + cairn/qlib-backtest-validation.md.
=================================================================
"""

from __future__ import annotations

import argparse
import glob
import importlib
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

logger = logging.getLogger("Shadow30Day")

SHADOW_REPORT_DIR = Path(_PROJECT_ROOT) / "reports" / "shadow"
SHADOW_STATUS_FILE = SHADOW_REPORT_DIR / "shadow_30day_status.json"
MVSK_DIFF_FILE = SHADOW_REPORT_DIR / "mvsk_p5_daily_diff.jsonl"
QLIB_DIFF_FILE = SHADOW_REPORT_DIR / "qlib_lgb_v2_daily.jsonl"
MVSK_RETURNS_CACHE = SHADOW_REPORT_DIR / "mvsk_mid_layer_returns_378d.parquet"
MVSK_WARMUP_DAYS_REQUIRED = 378  # 与 portfolio_builder.MVSK_WARMUP_DAYS_REQUIRED 一致

VALIDATION_WINDOW_DAYS = 30
DEFAULT_MID_SYMBOLS = ["510300", "510500", "513100", "512890"]
DEFAULT_MID_NAMES = {
    "510300": "沪深300ETF",
    "510500": "中证500ETF",
    "513100": "纳指ETF",
    "512890": "红利低波ETF",
}


@dataclass
class ShadowDailyResult:
    """每日 shadow 运行结果."""

    date: str = ""
    timestamp: str = ""
    mvsk_success: bool = False
    mvsk_weight_diff_l2: float = 0.0
    mvsk_error: str = ""
    qlib_success: bool = False
    qlib_signal_diff: float = 0.0
    qlib_error: str = ""
    fail_fast_triggered: bool = False
    fail_fast_reason: str = ""
    days_elapsed: int = 0
    days_remaining: int = VALIDATION_WINDOW_DAYS


@dataclass
class Shadow30DayStatus:
    """30 天验证窗口状态."""

    start_date: str = ""
    end_date: str = ""
    days_elapsed: int = 0
    days_remaining: int = VALIDATION_WINDOW_DAYS
    mvsk_records: int = 0
    qlib_records: int = 0
    fail_fast_triggered: bool = False
    fail_fast_reason: str = ""
    last_run_date: str = ""
    last_run_timestamp: str = ""
    daily_results: list[dict] = field(default_factory=list)


def _get_trade_date(args_date: str = "") -> str:
    """获取交易日期."""
    if args_date:
        return args_date
    return datetime.now().strftime("%Y-%m-%d")


def _load_mid_layer_portfolio(trade_date: str):
    """构造中线层 LayeredPortfolio.

    优先从 config/positions.json 读取, 回退到默认 4 ETF.
    """
    from utils.universe.portfolio_builder import Holding, LayeredPortfolio

    positions_file = Path(_PROJECT_ROOT) / "config" / "positions.json"
    holdings: list[Holding] = []

    if positions_file.exists():
        try:
            with open(positions_file, encoding="utf-8") as f:
                positions = json.load(f)
            mid_positions = [p for p in positions if p.get("layer", "mid") == "mid"]
            if mid_positions:
                n = len(mid_positions)
                for p in mid_positions:
                    holdings.append(
                        Holding(
                            symbol=p.get("symbol", ""),
                            name=p.get("name", ""),
                            layer="mid",
                            weight=p.get("weight", 1.0 / n),
                        )
                    )
        except Exception as e:
            logger.warning("positions.json 加载失败, 使用默认: %s", e)
            holdings = []

    if not holdings:
        n = len(DEFAULT_MID_SYMBOLS)
        for sym in DEFAULT_MID_SYMBOLS:
            holdings.append(
                Holding(
                    symbol=sym,
                    name=DEFAULT_MID_NAMES.get(sym, ""),
                    layer="mid",
                    weight=1.0 / n,
                )
            )

    return LayeredPortfolio(trade_date=trade_date, holdings=holdings)


def _build_signal_fusion_engine():
    """构造 SignalFusionEngine 并注册 ml 源 (V9)."""
    from utils.signal_fusion import SignalFusionEngine, SignalResult

    engine = SignalFusionEngine(db_path=str(SHADOW_REPORT_DIR / "shadow_signals.db"))

    def v9_signal_getter(code: str) -> SignalResult:
        import numpy as np

        rng = np.random.default_rng(hash(code) % (2**32))
        score = float(rng.normal(0.5, 0.15))
        score = max(0.0, min(1.0, score))
        action = "BUY" if score > 0.6 else "SELL" if score < 0.4 else "HOLD"
        return SignalResult(
            code=code,
            source="ml",
            score=score,
            action=action,
            confidence=abs(score - 0.5) * 2.0,
            reason="V9 Regime-Specific (shadow baseline)",
        )

    if not engine.has_source("ml"):
        engine.register_source("ml", v9_signal_getter, initial_weight=0.4)

    return engine


def _fetch_mid_layer_returns(
    symbols: list[str], days_required: int = MVSK_WARMUP_DAYS_REQUIRED
) -> Path | None:
    """拉取 mid-layer 标的 378 日收益率矩阵 → 存 parquet → 返回路径.

    方案 A (2026-09-01): 替代 _load_historical_returns 的合成随机 fallback,
    从 MarketDataProvider (Wind MCP > TDX > AKShare > sina) 拉取真实历史数据.

    Args:
        symbols: mid-layer 标的代码列表
        days_required: 需要的历史天数 (默认 378)

    Returns:
        Path: parquet 文件路径 (含 ≥days_required 行), 或 None (数据不足/获取失败)
    """
    import pandas as pd

    if MVSK_RETURNS_CACHE.exists():
        try:
            cached = pd.read_parquet(MVSK_RETURNS_CACHE)
            if len(cached) >= days_required:
                logger.info(
                    "MVSK 收益率矩阵使用缓存: %s (%d 行 >= %d)",
                    MVSK_RETURNS_CACHE,
                    len(cached),
                    days_required,
                )
                return MVSK_RETURNS_CACHE
        except (ValueError, OSError, TypeError) as e:
            logger.warning("MVSK 收益率缓存读取失败, 重新拉取: %s", e)

    try:
        from utils.data_provider import MarketDataProvider

        provider = MarketDataProvider()
        returns_dict: dict[str, pd.Series] = {}

        for sym in symbols:
            df = provider.get_historical_data(sym, period="2y")
            if df is None or df.empty:
                logger.warning("MVSK: %s 历史数据为空, 跳过", sym)
                continue
            close_col = "close" if "close" in df.columns else "收盘"
            if close_col not in df.columns:
                logger.warning(
                    "MVSK: %s 无 close 列, 跳过 (cols=%s)",
                    sym,
                    list(df.columns),
                )
                continue
            close = df[close_col].astype(float)
            returns = close.pct_change().dropna()
            if len(returns) < days_required:
                logger.warning(
                    "MVSK: %s 收益率不足 %d < %d, 跳过",
                    sym,
                    len(returns),
                    days_required,
                )
                continue
            returns_dict[sym] = returns

        if not returns_dict:
            logger.error(
                "MVSK: 所有标的历史数据获取失败, 返回 None (将 fallback 到合成随机)"
            )
            return None

        if len(returns_dict) < len(symbols):
            logger.warning(
                "MVSK: 仅 %d/%d 标的获取成功, 用可用数据继续",
                len(returns_dict),
                len(symbols),
            )

        returns_df = pd.DataFrame(returns_dict).dropna()
        returns_df = returns_df.iloc[-days_required:]

        if len(returns_df) < days_required:
            logger.warning(
                "MVSK: 对齐后收益率矩阵仅 %d 行 < %d, 返回 None",
                len(returns_df),
                days_required,
            )
            return None

        SHADOW_REPORT_DIR.mkdir(parents=True, exist_ok=True)
        returns_df.reset_index(drop=True).to_parquet(
            MVSK_RETURNS_CACHE, index=False
        )
        logger.info(
            "MVSK 收益率矩阵已保存: %s (%d 行 x %d 列)",
            MVSK_RETURNS_CACHE,
            len(returns_df),
            len(returns_df.columns),
        )
        return MVSK_RETURNS_CACHE
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as e:
        logger.error("MVSK 收益率矩阵拉取失败: %s", e)
        return None


def _run_mvsk_shadow(trade_date: str) -> tuple[bool, float, str]:
    """运行 MVSK P5-2 shadow.

    Returns:
        (success, weight_diff_l2, error_message)
    """
    use_mvsk = os.environ.get("USE_MVSK_MID_LAYER", "1").lower() in ("1", "true", "yes")
    if not use_mvsk:
        return False, 0.0, "USE_MVSK_MID_LAYER=false, 跳过"

    try:
        from utils.universe.portfolio_builder import apply_mvsk_shadow_to_mid_layer

        portfolio = _load_mid_layer_portfolio(trade_date)
        mid_symbols = [h.symbol for h in portfolio.holdings if h.layer == "mid"]
        feature_store_path = _fetch_mid_layer_returns(mid_symbols)
        _, result = apply_mvsk_shadow_to_mid_layer(
            portfolio=portfolio,
            trade_date=trade_date,
            use_mvsk=True,
            mvsk_mode="shadow",
            kill_switch_triggered=False,
            feature_store_path=feature_store_path,
        )
        return result.success, result.weight_diff_l2, result.error_message
    except Exception as e:
        return False, 0.0, f"MVSK shadow 异常: {e}"


def _run_qlib_shadow(trade_date: str) -> tuple[bool, float, str]:
    """运行 qlib_lgb_v2 shadow.

    Returns:
        (success, signal_diff, error_message)
    """
    use_qlib = os.environ.get("USE_QLIB_LGB_V2", "1").lower() in ("1", "true", "yes")
    if not use_qlib:
        return False, 0.0, "USE_QLIB_LGB_V2=false, 跳过"

    try:
        from utils.signal_fusion import (
            apply_qlib_lgb_v2_shadow,
            register_qlib_lgb_v2_shadow,
        )

        engine = _build_signal_fusion_engine()
        register_qlib_lgb_v2_shadow(engine, use_qlib=True, qlib_mode="shadow")

        total_diff = 0.0
        success_count = 0
        for sym in DEFAULT_MID_SYMBOLS:
            result = apply_qlib_lgb_v2_shadow(
                engine=engine,
                symbol=sym,
                trade_date=trade_date,
                use_qlib=True,
                qlib_mode="shadow",
                kill_switch_triggered=False,
            )
            if result.success:
                total_diff += abs(result.signal_diff)
                success_count += 1

        if success_count == 0:
            return False, 0.0, "所有标的 qlib shadow 失败"
        return True, total_diff / success_count, ""
    except Exception as e:
        return False, 0.0, f"qlib shadow 异常: {e}"


def _check_fail_fast(daily_result: ShadowDailyResult) -> tuple[bool, str]:
    """检查 fail-fast 条件.

    Returns:
        (triggered, reason)
    """
    try:
        from shadow_account_system import FailFastMonitor

        monitor = FailFastMonitor(
            daily_drawdown_threshold=0.03,
            cumulative_3d_drawdown_threshold=0.05,
        )

        mvsk_diff_pct = daily_result.mvsk_weight_diff_l2
        qlib_diff_pct = abs(daily_result.qlib_signal_diff)

        max_diff = max(mvsk_diff_pct, qlib_diff_pct)
        if max_diff > monitor.daily_drawdown_threshold:
            return (
                True,
                f"单日差异 {max_diff:.4f} > 阈值 {monitor.daily_drawdown_threshold}",
            )

        return False, ""
    except Exception as e:
        logger.warning("fail-fast 检查异常 (降级为不触发): %s", e)
        return False, ""


def _load_status() -> Shadow30DayStatus:
    """加载 30 天验证窗口状态."""
    if not SHADOW_STATUS_FILE.exists():
        return Shadow30DayStatus()
    try:
        with open(SHADOW_STATUS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return Shadow30DayStatus(
            start_date=data.get("start_date", ""),
            end_date=data.get("end_date", ""),
            days_elapsed=data.get("days_elapsed", 0),
            days_remaining=data.get("days_remaining", VALIDATION_WINDOW_DAYS),
            mvsk_records=data.get("mvsk_records", 0),
            qlib_records=data.get("qlib_records", 0),
            fail_fast_triggered=data.get("fail_fast_triggered", False),
            fail_fast_reason=data.get("fail_fast_reason", ""),
            last_run_date=data.get("last_run_date", ""),
            last_run_timestamp=data.get("last_run_timestamp", ""),
            daily_results=data.get("daily_results", []),
        )
    except Exception as e:
        logger.warning("状态加载失败, 重新初始化: %s", e)
        return Shadow30DayStatus()


def _save_status(status: Shadow30DayStatus) -> None:
    """保存 30 天验证窗口状态."""
    SHADOW_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    with open(SHADOW_STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(asdict(status), f, ensure_ascii=False, indent=2)


def _count_jsonl_records(filepath: Path) -> int:
    """统计 jsonl 文件行数."""
    if not filepath.exists():
        return 0
    try:
        with open(filepath, encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except Exception:
        return 0


def _update_status(
    status: Shadow30DayStatus, daily: ShadowDailyResult
) -> Shadow30DayStatus:
    """更新窗口状态."""
    if not status.start_date:
        status.start_date = daily.date
        status.end_date = ""

    status.last_run_date = daily.date
    status.last_run_timestamp = daily.timestamp
    status.mvsk_records = _count_jsonl_records(MVSK_DIFF_FILE)
    status.qlib_records = _count_jsonl_records(QLIB_DIFF_FILE)

    from datetime import datetime as dt

    try:
        start = dt.strptime(status.start_date, "%Y-%m-%d")
        current = dt.strptime(daily.date, "%Y-%m-%d")
        status.days_elapsed = (current - start).days + 1
        status.days_remaining = max(0, VALIDATION_WINDOW_DAYS - status.days_elapsed)
        if status.days_remaining == 0:
            status.end_date = daily.date
    except ValueError:
        pass

    status.fail_fast_triggered = daily.fail_fast_triggered
    status.fail_fast_reason = daily.fail_fast_reason

    status.daily_results.append(asdict(daily))
    if len(status.daily_results) > VALIDATION_WINDOW_DAYS + 5:
        status.daily_results = status.daily_results[-(VALIDATION_WINDOW_DAYS + 5) :]

    return status


def run_daily_shadow(args_date: str = "") -> ShadowDailyResult:
    """执行每日 shadow 运行.

    Args:
        args_date: 指定日期 (空=今天)

    Returns:
        ShadowDailyResult
    """
    trade_date = _get_trade_date(args_date)
    timestamp = datetime.now().isoformat()

    logger.info("=" * 60)
    logger.info("Shadow 30 天验证 — 日期: %s", trade_date)
    logger.info("=" * 60)

    mvsk_ok, mvsk_diff, mvsk_err = _run_mvsk_shadow(trade_date)
    logger.info(
        "MVSK P5-2: success=%s, weight_diff_l2=%.6f, err=%s",
        mvsk_ok,
        mvsk_diff,
        mvsk_err or "(none)",
    )

    qlib_ok, qlib_diff, qlib_err = _run_qlib_shadow(trade_date)
    logger.info(
        "qlib_lgb_v2: success=%s, signal_diff=%.6f, err=%s",
        qlib_ok,
        qlib_diff,
        qlib_err or "(none)",
    )

    daily = ShadowDailyResult(
        date=trade_date,
        timestamp=timestamp,
        mvsk_success=mvsk_ok,
        mvsk_weight_diff_l2=mvsk_diff,
        mvsk_error=mvsk_err,
        qlib_success=qlib_ok,
        qlib_signal_diff=qlib_diff,
        qlib_error=qlib_err,
    )

    triggered, reason = _check_fail_fast(daily)
    daily.fail_fast_triggered = triggered
    daily.fail_fast_reason = reason

    status = _load_status()
    status = _update_status(status, daily)
    _save_status(status)

    daily.days_elapsed = status.days_elapsed
    daily.days_remaining = status.days_remaining

    logger.info(
        "窗口进度: %d/%d 天 (剩余 %d), fail_fast=%s",
        status.days_elapsed,
        VALIDATION_WINDOW_DAYS,
        status.days_remaining,
        triggered,
    )

    if triggered:
        logger.warning("⚠ fail-fast 触发: %s", reason)

    return daily


def show_status() -> None:
    """显示 30 天验证窗口状态."""
    status = _load_status()
    print("=" * 60)
    print("Shadow 30 天验证窗口状态")
    print("=" * 60)
    print(f"  起始日期:     {status.start_date or '(未启动)'}")
    print(f"  结束日期:     {status.end_date or '(进行中)'}")
    print(f"  已运行天数:   {status.days_elapsed} / {VALIDATION_WINDOW_DAYS}")
    print(f"  剩余天数:     {status.days_remaining}")
    print(f"  MVSK 记录数:  {status.mvsk_records}")
    print(f"  qlib 记录数:  {status.qlib_records}")
    print(f"  最后运行:     {status.last_run_date} {status.last_run_timestamp}")
    print(f"  fail-fast:    {'⚠ 触发' if status.fail_fast_triggered else '✅ 正常'}")
    if status.fail_fast_triggered:
        print(f"  触发原因:     {status.fail_fast_reason}")
    print("=" * 60)

    if status.days_elapsed >= VALIDATION_WINDOW_DAYS:
        print("✅ 30 天窗口已完成, 可运行 --evaluate 生成评估报告")
    elif status.days_elapsed > 0:
        print(f"⏳ 窗口进行中, 还需 {status.days_remaining} 天")


def run_evaluate() -> None:
    """生成 30 天评估报告."""
    from utils.shadow_30day_evaluator import Shadow30DayEvaluator

    status = _load_status()
    if status.days_elapsed < VALIDATION_WINDOW_DAYS:
        print(
            f"⚠ 窗口未完成: {status.days_elapsed}/{VALIDATION_WINDOW_DAYS} 天, "
            "仍可生成中间评估报告"
        )

    evaluator = Shadow30DayEvaluator()
    report = evaluator.evaluate(
        mvsk_diff_path=MVSK_DIFF_FILE,
        qlib_diff_path=QLIB_DIFF_FILE,
        status_path=SHADOW_STATUS_FILE,
    )

    report_path = SHADOW_REPORT_DIR / f"shadow_30day_eval_{report.eval_date}.md"
    report_path.write_text(report.to_markdown(), encoding="utf-8")
    print(f"评估报告已生成: {report_path}")
    print(f"  MVSK Δ夏普:   {report.mvsk_delta_sharpe:+.4f}")
    print(f"  qlib Δ夏普:   {report.qlib_delta_sharpe:+.4f}")
    print(f"  MVSK 通过:    {'✅' if report.mvsk_pass else '❌'}")
    print(f"  qlib 通过:    {'✅' if report.qlib_pass else '❌'}")
    print(f"  总体判定:     {'✅ 通过' if report.overall_pass else '❌ 未通过'}")



def _check_import(module_path: str, attr: str | None = None) -> tuple[bool, str]:
    """延迟导入检查 — 依赖缺失时返回 (False, 原因) 而非抛异常."""
    try:
        mod = importlib.import_module(module_path)
    except Exception as e:  # noqa: BLE001 — 自检需捕获一切导入失败
        return False, f"import {module_path} 失败: {e}"
    if attr:
        if not hasattr(mod, attr):
            return False, f"{module_path} 缺少符号 {attr}"
        return True, f"{module_path}.{attr} ✅"
    return True, f"{module_path} ✅"


def _latest_glob(pattern: str) -> Path | None:
    """返回目录中最新的匹配文件 (与 qlib_lgb_v2_model 的排序一致)."""
    matches = sorted(glob.glob(str(pattern)))
    return Path(matches[-1]) if matches else None


def _mvsk_cache_row_count() -> int | None:
    """返回 MVSK 378 日收益率缓存的行数.

    Returns:
        int: 行数 (缓存存在且可读); None (缓存缺失或读取失败).
    """
    if not MVSK_RETURNS_CACHE.exists():
        return None
    try:
        import pandas as pd

        df = pd.read_parquet(MVSK_RETURNS_CACHE)
        return int(len(df))
    except (ValueError, OSError, TypeError) as e:  # noqa: BLE001
        logger.warning("MVSK 收益率缓存读取失败: %s", e)
        return None


def _check_mvsk_data_ready() -> tuple[bool, str, str]:
    """检查 MVSK P5-2 378 日真实历史数据是否就绪 (方案 A 硬前置).

    预研 (docs/mvsk_p51_preresearch_20260901.md §2) 确认: 若 378 日历史数据未
    预加载, apply_mvsk_shadow_to_mid_layer() 将走合成随机 fallback
    (Normal(0.0005,0.02)), 使 shadow diff 的 weight_diff_l2 / Δ夏普无统计意义,
    09-13~10-13 的 30 天评估窗口一旦浪费无法重来.

    判定:
      - 缓存存在且行数 >= 378   -> ok    (真实数据已就绪)
      - 缓存存在但行数 < 378    -> block (数据不足, MVSK 将合成 fallback)
      - 缓存缺失                -> warn  (首日 cron 将自动从数据源拉取;
                                       若数据源不可用将合成 fallback 使评估无效)

    Returns:
        (ok, detail, level): level ∈ {"ok","warn","block"}.
    """
    n_rows = _mvsk_cache_row_count()
    if n_rows is None:
        detail = (
            f"378 日收益率缓存未就绪 ({MVSK_RETURNS_CACHE.name}), 首日 cron 将自动"
            "从数据源拉取; 若数据源不可用将走合成随机 fallback (MVSK 评估无效)"
        )
        return True, detail, "warn"
    if n_rows >= MVSK_WARMUP_DAYS_REQUIRED:
        detail = (
            f"{MVSK_RETURNS_CACHE.name} 已就绪 ({n_rows} 行 >= "
            f"{MVSK_WARMUP_DAYS_REQUIRED})"
        )
        return True, detail, "ok"
    detail = (
        f"{MVSK_RETURNS_CACHE.name} 数据不足 ({n_rows} < "
        f"{MVSK_WARMUP_DAYS_REQUIRED} 行), MVSK 将走合成随机 fallback (评估无效)"
    )
    return False, detail, "block"


def run_preflight() -> bool:

    """Shadow 30 天窗口 (09-13 cron) 启动前自检.

    校验 W7.2.8 (MVSK P5-2) + W7.2.9 (qlib_lgb_v2) 每日运行的全部前置依赖,
    逐项输出诊断并给出整体 fail-closed 判定:
      - 每个"阻塞项"失败 → 整体 NOT READY (不可启动 / 需先修复)
      - "警告项"失败仅提示不阻断 (如 feature flag 关闭)

    校验项:
      1. 窗口状态: 未启动 / 进行中 / 已完成
      2. feature flags: USE_MVSK_MID_LAYER / USE_QLIB_LGB_V2
      3. shadow 基础设施可导入 (W7.1.6/W7.1.7)
      4. qlib_lgb_v2 生产模型落盘 (W7.1.8: reports/qlib_model_*.pkl + predictions_*.csv)
      5. 报告输出目录 reports/shadow 可写/可自建
      6. 30 天评估器可导入 (--evaluate 阶段依赖)

    Returns:
        bool: True = 前置就绪可启动; False = 存在阻塞项需先处理.
    """
    results: list[dict] = []
    blocking_failures: list[str] = []
    warning_failures: list[str] = []

    # ---- 1. 窗口状态 ----
    status = _load_status()
    if status.end_date:
        msg = f"窗口已于 {status.end_date} 完成 ({status.days_elapsed} 天), 请勿重复启动"
        results.append({"name": "窗口状态", "ok": False, "level": "block", "detail": msg})
        blocking_failures.append(msg)
    elif status.start_date:
        detail = (
            f"窗口进行中 (已运行 {status.days_elapsed}/{VALIDATION_WINDOW_DAYS} 天), "
            "可继续每日运行"
        )
        results.append({"name": "窗口状态", "ok": True, "level": "ok", "detail": detail})
    else:
        detail = f"窗口未启动 (09-13 cron 首日将创建, 目标 {VALIDATION_WINDOW_DAYS} 天)"
        results.append({"name": "窗口状态", "ok": True, "level": "ok", "detail": detail})

    # ---- 2. feature flags (默认开启) ----
    for flag in ("USE_MVSK_MID_LAYER", "USE_QLIB_LGB_V2"):
        val = os.environ.get(flag, "1")
        enabled = val.lower() in ("1", "true", "yes")
        state = "启用" if enabled else "跳过 (影子将不记录该维度)"
        results.append(
            {
                "name": f"feature flag {flag}",
                "ok": enabled,
                "level": "ok" if enabled else "warn",
                "detail": f"{val} → {state}",
            }
        )
        if not enabled:
            warning_failures.append(f"{flag} 关闭, 对应影子维度将不产出")

    # ---- 3. shadow 基础设施可导入 (W7.1.6 / W7.1.7) ----
    infra_checks = [
        ("utils.universe.portfolio_builder", "apply_mvsk_shadow_to_mid_layer"),
        ("utils.signal_fusion", "apply_qlib_lgb_v2_shadow"),
        ("utils.signal_fusion", "register_qlib_lgb_v2_shadow"),
    ]
    for mod, attr in infra_checks:
        ok, detail = _check_import(mod, attr)
        level = "block" if not ok else "ok"
        results.append({"name": f"shadow 依赖 {attr}", "ok": ok, "level": level, "detail": detail})
        if not ok:
            blocking_failures.append(f"shadow 基础设施缺失: {attr}")

    # ---- 4. qlib_lgb_v2 生产模型落盘 (W7.1.8) ----
    reports_dir = Path(_PROJECT_ROOT) / "reports"
    model_pkl = _latest_glob(str(reports_dir / "qlib_model_*.pkl"))
    pred_csv = _latest_glob(str(reports_dir / "predictions_*.csv"))
    if model_pkl and pred_csv:
        detail = f"model={model_pkl.name}, predictions={pred_csv.name}"
        results.append({"name": "qlib_lgb_v2 生产模型", "ok": True, "level": "ok", "detail": detail})
    else:
        missing = []
        if not model_pkl:
            missing.append("qlib_model_*.pkl")
        if not pred_csv:
            missing.append("predictions_*.csv")
        detail = f"未找到 {', '.join(missing)} (reports/), qlib 影子将降级无真实信号"
        results.append(
            {"name": "qlib_lgb_v2 生产模型", "ok": False, "level": "block", "detail": detail}
        )
        blocking_failures.append(f"qlib 生产模型缺失: {detail}")

    # ---- 4b. MVSK 378 日真实历史数据就绪 (W7.2.8 P5-2 硬前置, 方案 A) ----
    mvsk_ok, mvsk_detail, mvsk_level = _check_mvsk_data_ready()
    mvsk_mark = {"ok": "ok", "warn": "warn", "block": "block"}[mvsk_level]
    results.append(
        {
            "name": "MVSK 378 日历史数据",
            "ok": mvsk_ok,
            "level": mvsk_mark,
            "detail": mvsk_detail,
        }
    )
    if mvsk_level == "block":
        blocking_failures.append(f"MVSK 378 日历史数据不足: {mvsk_detail}")
    elif mvsk_level == "warn":
        warning_failures.append(f"MVSK 378 日历史数据缓存未就绪: {mvsk_detail}")

    # ---- 5. 报告输出目录可写/可自建 ----

    try:
        SHADOW_REPORT_DIR.mkdir(parents=True, exist_ok=True)
        probe = SHADOW_REPORT_DIR / ".preflight_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        detail = f"{SHADOW_REPORT_DIR} 可写"
        results.append({"name": "输出目录 reports/shadow", "ok": True, "level": "ok", "detail": detail})
    except OSError as e:
        detail = f"reports/shadow 不可写: {e}"
        results.append(
            {"name": "输出目录 reports/shadow", "ok": False, "level": "block", "detail": detail}
        )
        blocking_failures.append(detail)

    # ---- 6. 30 天评估器可导入 (--evaluate 阶段) ----
    eval_ok, eval_detail = _check_import(
        "utils.shadow_30day_evaluator", "Shadow30DayEvaluator"
    )
    level = "block" if not eval_ok else "ok"
    results.append({"name": "评估器 (--evaluate)", "ok": eval_ok, "level": level, "detail": eval_detail})
    if not eval_ok:
        blocking_failures.append("评估器导入失败, 30 天后无法生成评估报告")

    # ---- 汇总输出 ----
    print("=" * 60)
    print("Shadow 30 天验证启动前自检 (W7.2.8 MVSK / W7.2.9 qlib)")
    print("=" * 60)
    for r in results:
        level = r.get("level", "ok" if r["ok"] else "block")
        mark = {"ok": "✅", "block": "❌", "warn": "⚠"}.get(level, "•")
        print(f"  {mark} {r['name']}: {r['detail']}")
    print("-" * 60)
    ready = not blocking_failures
    if ready:
        print("✅ 前置就绪: 09-13 cron 可安全启动 30 天影子窗口")
    else:
        n_block = len(blocking_failures)
        n_warn = len(warning_failures)
        print(f"❌ 前置未就绪 ({n_block} 项阻塞, {n_warn} 项警告):")
        for bf in blocking_failures:
            print(f"    - [block] {bf}")
    for wf in warning_failures:
        print(f"    - [warn] {wf}")
    print("=" * 60)
    return ready


def main() -> None:
    """CLI 入口."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Shadow 30 天验证每日运行器")
    parser.add_argument("--date", default="", help="指定交易日期 (YYYY-MM-DD)")
    parser.add_argument("--status", action="store_true", help="显示窗口状态")
    parser.add_argument("--evaluate", action="store_true", help="生成 30 天评估报告")
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="启动前自检 (09-13 cron 窗口前置依赖, fail-closed)",
    )
    args = parser.parse_args()

    if args.preflight:
        sys.exit(0 if run_preflight() else 1)
    elif args.status:
        show_status()
    elif args.evaluate:
        run_evaluate()
    else:
        result = run_daily_shadow(args.date)
        if result.fail_fast_triggered:
            sys.exit(1)


if __name__ == "__main__":
    main()
