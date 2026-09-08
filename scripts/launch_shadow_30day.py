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

    # fail-fast 一次性锁存终止后, 人工修复完复位重启窗口 (状态归档 .bak)
    py -X utf8 scripts/launch_shadow_30day.py --reset

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

VALIDATION_WINDOW_DAYS = 30
# R-6 (2026-09-07) 口径: qlib_lgb_v2 停跑归档 + 正式评估窗 (ROADMAP 09-13~10-12)
#   - qlib 归档: 评估/展示仅 MVSK P5-2; USE_QLIB_LGB_V2=1 可强制重开 (需先修复接线)
#   - 评估窗: 30 自然日正式样本区间; 09-07~09-12 首触发/预热记录落盘留档但【不】计入
#     正式评估样本 (evaluate 按 window_start=09-13 过滤; --status/--evaluate 展示一致口径)
EVAL_WINDOW_START = os.environ.get("SHADOW30_EVAL_START", "2026-09-13")
EVAL_WINDOW_END = os.environ.get("SHADOW30_EVAL_END", "2026-10-12")
QLIB_ARCHIVED = True
QLIB_ARCHIVED_REASON = (
    "qlib_lgb_v2 已按 R-6 (2026-09-07) 停跑归档, "
    "USE_QLIB_LGB_V2=1 可强制重开 (需先修复信号-消费端接线错配)"
)
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
    # 一次性 fail-fast (2026-09-07): 首触即锁存终止窗口, 避免每日重复 exit 1
    # 淹没有效告警; 窗口在 terminated 状态下冻结, 人工 --reset 后重启.
    terminated: bool = False
    terminated_date: str = ""
    terminated_reason: str = ""
    last_run_date: str = ""
    last_run_timestamp: str = ""
    daily_results: list[dict] = field(default_factory=list)


def _get_trade_date(args_date: str = "") -> str:
    """获取交易日期."""
    if args_date:
        return args_date
    return datetime.now().strftime("%Y-%m-%d")


def _today_str() -> str:
    """当前本地日期字符串 (YYYY-MM-DD)."""
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
                data = json.load(f)
            # 治理③ (2026-09-07): positions.json 现为 {meta, positions, hedge_positions}
            # 结构, positions 段是 {code: {...}} dict (14 ETF + 12 股票, 无 layer 字段);
            # 兼容历史 list 结构. 旧实现直接遍历顶层 dict → p 为 "meta" 等 str key,
            # 抛 'str' object has no attribute 'get' → 回退默认 4 ETF.
            raw = data.get("positions", data) if isinstance(data, dict) else data
            records = list(raw.values()) if isinstance(raw, dict) else list(raw)
            mid_positions = [
                p for p in records if isinstance(p, dict) and p.get("layer", "mid") == "mid"
            ]
            if mid_positions:
                total_amt = sum(float(p.get("amount", 0.0) or 0.0) for p in mid_positions)
                n = len(mid_positions)
                for p in mid_positions:
                    code = str(p.get("code") or p.get("symbol") or "").split(".")[0].strip()
                    if not code:
                        continue
                    amt = float(p.get("amount", 0.0) or 0.0)
                    if total_amt > 0 and amt > 0:
                        weight = amt / total_amt
                    else:
                        weight = float(p.get("weight", 0.0) or 0.0) or (1.0 / n)
                    holdings.append(
                        Holding(
                            symbol=code,
                            name=str(p.get("name", "") or ""),
                            layer="mid",
                            style=str(p.get("style") or p.get("sector") or "").strip(),
                            weight=weight,
                        )
                    )
                logger.info(
                    "MVSK mid 组合: positions.json 加载 %d 个真实持仓 (市值归一权重), 标的=%s",
                    len(holdings),
                    ", ".join(h.symbol for h in holdings),
                )
        except Exception as e:  # noqa: BLE001 — positions 解析失败按既有兜底处理
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


def _fetch_mid_layer_returns(symbols: list[str], days_required: int = 378) -> Path | None:
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
            cached_cols = {str(c) for c in cached.columns}
            if len(cached) >= days_required and set(symbols).issubset(cached_cols):
                logger.info(
                    "MVSK 收益率矩阵使用缓存: %s (%d 行 >= %d)",
                    MVSK_RETURNS_CACHE,
                    len(cached),
                    days_required,
                )
                return MVSK_RETURNS_CACHE
            missing = sorted(set(symbols) - cached_cols)
            logger.info(
                "MVSK 缓存未覆盖当前标的 (缺 %d 列, 前几列=%s), 重建真实持仓矩阵",
                len(missing),
                ", ".join(missing[:6]),
            )
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
            logger.error("MVSK: 所有标的历史数据获取失败, 返回 None (将 fallback 到合成随机)")
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
        returns_df.reset_index(drop=True).to_parquet(MVSK_RETURNS_CACHE, index=False)
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

    R-6 (2026-09-07) 归档停跑: USE_QLIB_LGB_V2 默认 "0", 不再产出
    qlib_lgb_v2_daily.jsonl; 显式 =1 可强制重开 (需先修复 R-6 记录的
    信号-消费端接线错配).

    Returns:
        (success, signal_diff, error_message)
    """
    use_qlib = os.environ.get("USE_QLIB_LGB_V2", "0").lower() in ("1", "true", "yes")
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

    量纲说明 (2026-09-04 修复): shadow 30 天验证不产生真实 NAV 回撤
    (weight=0 不实际交易), fail-fast 监控的是 shadow 差异本身, 两个维度
    各用独立阈值 — 不能与 FailFastMonitor 的回撤百分比阈值 (0.03) 混比
    (signal_diff 常态 0.3~0.6, 混比会每日误触发 + 评估必 FAIL).

    Returns:
        (triggered, reason)
    """
    # MVSK: weight_diff_l2 >0.50 = 组合权重接近完全背离 (完全翻转 L2≈1.0)。
    # 2026-09-08 选项 A 拍板 (docs/weight_diff_l2阈值口径复核_20260908.md):
    # 0.30→0.50 — 治理⑤ (2026-09-07) 起 L2 为可优化子集归一化口径, 较旧
    # 全组合口径放大约 4.25×, 09-07 实测健康样本 0.2423 已达旧阈值 0.30 的
    # 80.8%; 0.50 恰对应旧口径健康区间上限 (0.50/4.25≈0.118)。旧注释
    # "健康值 ~0.0-0.1" 系旧口径产物, 已失真作废。
    MVSK_DIFF_THRESHOLD = 0.50
    # qlib: signal_diff 量纲 [-1,1] (两独立模型信号差), >0.80 = 信号完全反向
    QLIB_DIFF_THRESHOLD = 0.80

    if daily_result.mvsk_weight_diff_l2 > MVSK_DIFF_THRESHOLD:
        return (
            True,
            f"MVSK 权重差异 {daily_result.mvsk_weight_diff_l2:.4f} > 阈值 {MVSK_DIFF_THRESHOLD}",
        )
    if abs(daily_result.qlib_signal_diff) > QLIB_DIFF_THRESHOLD:
        return (
            True,
            f"qlib 信号差异 {abs(daily_result.qlib_signal_diff):.4f} > 阈值 {QLIB_DIFF_THRESHOLD}",
        )
    return False, ""


def _load_status() -> Shadow30DayStatus:
    """加载 30 天验证窗口状态."""
    if not SHADOW_STATUS_FILE.exists():
        return Shadow30DayStatus()
    try:
        with open(SHADOW_STATUS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        status = Shadow30DayStatus(
            start_date=data.get("start_date", ""),
            end_date=data.get("end_date", ""),
            days_elapsed=data.get("days_elapsed", 0),
            days_remaining=data.get("days_remaining", VALIDATION_WINDOW_DAYS),
            mvsk_records=data.get("mvsk_records", 0),
            qlib_records=data.get("qlib_records", 0),
            fail_fast_triggered=data.get("fail_fast_triggered", False),
            fail_fast_reason=data.get("fail_fast_reason", ""),
            terminated=bool(data.get("terminated", False)),
            terminated_date=data.get("terminated_date", ""),
            terminated_reason=data.get("terminated_reason", ""),
            last_run_date=data.get("last_run_date", ""),
            last_run_timestamp=data.get("last_run_timestamp", ""),
            daily_results=data.get("daily_results", []),
        )
        # 加载净化 (2026-09-05): 历史污染状态文件可能残留负进度/越界剩余天数,
        # 直接展示会导致 --status/--preflight 显示 -8/30 天之类假象.
        if status.days_elapsed < 0:
            logger.warning(
                "状态文件 days_elapsed=%d < 0 (历史污染), 已净化归零; 下次真实运行将重置窗口起点",
                status.days_elapsed,
            )
            status.days_elapsed = 0
        status.days_remaining = min(
            VALIDATION_WINDOW_DAYS,
            max(0, status.days_remaining),
        )
        return status
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


def _update_status(status: Shadow30DayStatus, daily: ShadowDailyResult) -> Shadow30DayStatus:
    """更新窗口状态.

    防污染规则 (2026-09-04 修复 + 2026-09-05 加固):
      1. 未来日期运行 (daily.date > 今天) 一律不触碰状态: 不锚定窗口起点、
         不推进 days_elapsed、不追加 daily_results、不刷新 last_run —
         即使绕过 run_daily_shadow 的前置拦截 (preview 分支) 也被本函数
         拒绝 (调用层 + 函数层双防线).
      2. 空/非法日期 (非 YYYY-MM-DD) fail-closed: 不写任何状态字段.
      3. days_elapsed 恒 >= 0, days_remaining 恒在 [0, VALIDATION_WINDOW_DAYS]:
         状态文件残留负进度或 start_date 为未来/非法时, 重置窗口起点并
         统一 clamp, 绝不留负值展示.
      4. daily_results 幂等: 同 date 旧记录被替换而非追加; 窗口起点重置时
         同步剔除起点之后的历史脏记录 (与 jsonl 幂等语义一致).
    """
    # ---- 入口防线 1: 空/非法日期 fail-closed (不写任何状态) ----
    if not daily.date:
        logger.warning("跳过 shadow 状态更新: daily.date 为空")
        return status
    try:
        current = datetime.strptime(daily.date, "%Y-%m-%d")
    except ValueError:
        logger.warning("跳过 shadow 状态更新: 非法日期 %r (需 YYYY-MM-DD)", daily.date)
        return status

    # ---- 入口防线 2: 未来日期 = 演练, 不锚定窗口/不推进状态 ----
    today = _today_str()
    if daily.date > today:
        logger.info(
            "演练运行 date=%s (晚于今天 %s): 不锚定窗口起点/不推进状态",
            daily.date,
            today,
        )
        return status

    status.last_run_date = daily.date
    status.last_run_timestamp = daily.timestamp
    status.mvsk_records = _count_jsonl_records(MVSK_DIFF_FILE)
    status.qlib_records = _count_jsonl_records(QLIB_DIFF_FILE)

    # 窗口锚定: 仅窗口未启动时由 (非未来) 运行锚定
    if not status.start_date:
        status.start_date = daily.date
        status.end_date = ""

    # 进度推进: start_date 晚于本次运行日 (历史演练污染) 或非法 (脏数据) 均重置起点
    try:
        start = datetime.strptime(status.start_date, "%Y-%m-%d")
        if current < start:
            logger.warning(
                "窗口 start_date(%s) 晚于本次运行日(%s), 重置窗口起点为 %s",
                status.start_date,
                daily.date,
                daily.date,
            )
            status.start_date = daily.date
            start = current
            # 同步剔除窗口起点之后的脏记录 (旧版未来演练可能误写入)
            status.daily_results = [r for r in status.daily_results if r.get("date", "") <= daily.date]
        status.days_elapsed = (current - start).days + 1
    except ValueError:
        logger.warning(
            "窗口 start_date(%s) 非法 (非 YYYY-MM-DD), 重置窗口起点为 %s",
            status.start_date,
            daily.date,
        )
        status.start_date = daily.date
        status.days_elapsed = 1

    # 负值/越界兜底: days_elapsed 恒 >= 0, days_remaining 恒在 [0, 30]
    status.days_elapsed = max(0, status.days_elapsed)
    status.days_remaining = min(
        VALIDATION_WINDOW_DAYS,
        max(0, VALIDATION_WINDOW_DAYS - status.days_elapsed),
    )
    if status.days_remaining == 0:
        status.end_date = daily.date

    status.fail_fast_triggered = daily.fail_fast_triggered
    status.fail_fast_reason = daily.fail_fast_reason

    # 落盘前把 daily 快照进度同步为窗口最新值, 使 daily_results 内记录与
    # top-level 一致 (2026-09-07 真实重跑发现: 旧代码 asdict(daily) 在
    # run_daily_shadow 末尾同步前执行, daily 仍是默认 0/30 → daily_results
    # 内 days_elapsed=0 而 top-level=1, 状态文件自相矛盾)
    daily.days_elapsed = status.days_elapsed
    daily.days_remaining = status.days_remaining

    # 幂等替换: 同 date 只保留最后一条
    status.daily_results = [r for r in status.daily_results if r.get("date") != daily.date]
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

    # 一次性 fail-fast (2026-09-07): 窗口已 terminated → cron (args_date="")
    # 直接跳过, exit 0 不再重复红. 置于交易日门控之前: 终止后周末/节假日
    # 也不产生任何行为. 人工 --reset 复位后才重新推进.
    # (显式 --date 仍放行, 供补跑/诊断/预热)
    if not args_date:
        _early_status = _load_status()
        if _early_status.terminated:
            logger.warning(
                "窗口已于 %s fail-fast 终止 (%s) — 本次 cron 跳过 shadow 计算, "
                "exit 0 不重复告警; 请人工处理后在窗口重新验证或执行 --reset 复位",
                _early_status.terminated_date or "?",
                _early_status.terminated_reason or "未知原因",
            )
            return ShadowDailyResult(date=trade_date, timestamp=timestamp, fail_fast_triggered=False)

    # 交易日门控: 非交易日 (周末/节假日) 不记录, 防止污染 30 天窗口统计
    # (--date 显式指定时放行, 供补跑历史交易日)
    if not args_date:
        try:
            from utils.trade_calendar import is_trading_day

            if not is_trading_day(trade_date):
                logger.info("非交易日 (%s), 跳过 shadow 记录", trade_date)
                return ShadowDailyResult(date=trade_date, timestamp=timestamp)
        except Exception as e:  # noqa: BLE001 — 日历不可用时按交易日跑 (fail-open)
            logger.warning("交易日历不可用, 按交易日继续: %s", e)

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

    status = _load_status()

    # 未来日期运行 = 演练/预热 (如预生成 MVSK 缓存), 不得锚定窗口起点、
    # 不得累积 daily_results、也不触发 fail-fast 退出 (2026-09-04 修复:
    # 历史 --date 未来日期演练曾把 start_date 锚到 2026-09-13, 导致真实
    # 运行 days_elapsed 恒为负, 窗口进度显示 -8/30 天)
    if daily.date > _today_str():
        logger.info(
            "预演运行 date=%s (晚于今天 %s): 计算 shadow 差异但不推进窗口状态",
            daily.date,
            _today_str(),
        )
        daily.days_elapsed = status.days_elapsed
        daily.days_remaining = status.days_remaining
        return daily

    triggered, reason = _check_fail_fast(daily)
    daily.fail_fast_triggered = triggered
    daily.fail_fast_reason = reason

    # 一次性 fail-fast (2026-09-07): 首触即锁存 terminated 窗口 (区别于旧的每日
    # 重复 exit 1). 触发当次仍 exit 1 报警; 此后 cron 每日由 run_daily_shadow
    # 顶部 terminated 检查跳过 (exit 0), 避免 30 天窗口内每日常红淹没有效告警.
    if triggered and not status.terminated:
        status.terminated = True
        status.terminated_date = trade_date
        status.terminated_reason = reason
        logger.warning(
            "fail-fast 首触, 窗口锁存终止: date=%s, reason=%s — "
            "后续 cron 将跳过 (exit 0); 修复后可 --reset 复位重启",
            trade_date,
            reason,
        )

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
    print(f"  qlib 记录数:  {status.qlib_records} (R-6 归档停跑, 冻结)")
    print(f"  评估窗:       {EVAL_WINDOW_START} ~ {EVAL_WINDOW_END} (正式样本区间, R-6 口径)")
    print(f"  最后运行:     {status.last_run_date} {status.last_run_timestamp}")
    if status.terminated:
        print(
            f"  fail-fast:    ⛔ 窗口已终止 @ {status.terminated_date or '?'} (一次性锁存)"
        )
        print(f"  终止原因:     {status.terminated_reason or status.fail_fast_reason}")
        print("  处置:         人工修复后执行 --reset 复位重启窗口")
    else:
        print(f"  fail-fast:    {'⚠ 触发' if status.fail_fast_triggered else '✅ 正常'}")
        if status.fail_fast_triggered:
            print(f"  触发原因:     {status.fail_fast_reason}")
    print("=" * 60)

    if status.terminated:
        print("⛔ 窗口已因 fail-fast 终止, 不推进 30 天验证; 修复后 --reset 复位")
    elif status.days_elapsed >= VALIDATION_WINDOW_DAYS:
        print("✅ 30 天窗口已完成, 可运行 --evaluate 生成评估报告")
    elif status.days_elapsed > 0:
        print(f"⏳ 窗口进行中, 还需 {status.days_remaining} 天")


def run_reset() -> None:
    """复位 30 天验证窗口 (fail-fast 一次性锁存后人工修复完重启).

    - 状态文件归档为 .bak (保留审计, 不物理删除)
    - 状态清零: start_date/daily_results/fail_fast/terminated 全复位
    - jsonl 差异记录保留 (幂等键 date 会在下次运行时覆盖对应日期),
      不删除原始数据
    """
    archive = SHADOW_STATUS_FILE.with_name(
        f"shadow_30day_status.json.{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
    )
    try:
        if SHADOW_STATUS_FILE.exists():
            SHADOW_STATUS_FILE.replace(archive)
        fresh = Shadow30DayStatus()
        _save_status(fresh)
        print(f"✅ 窗口已复位 (旧状态归档: {archive.name})")
        print("   下次运行将重新锚定窗口起点; jsonl 差异记录保留 (按 date 幂等覆盖)")
    except OSError as e:
        logger.error("复位失败: %s", e)
        sys.exit(1)


def run_evaluate() -> None:
    """生成评估报告 (评估窗 = ROADMAP R-6 口径: EVAL_WINDOW_START ~ EVAL_WINDOW_END)."""
    from utils.shadow_30day_evaluator import Shadow30DayEvaluator

    status = _load_status()
    if status.days_elapsed < VALIDATION_WINDOW_DAYS:
        print(f"⚠ 窗口未完成: {status.days_elapsed}/{VALIDATION_WINDOW_DAYS} 天, 仍可生成中间评估报告")
    if _today_str() < EVAL_WINDOW_END:
        print(
            f"⚠ 尚未到评估窗收尾日 {EVAL_WINDOW_END}, 本次为中间评估 "
            f"(正式判定样本区间 {EVAL_WINDOW_START} ~ {EVAL_WINDOW_END})"
        )

    evaluator = Shadow30DayEvaluator()
    report = evaluator.evaluate(
        mvsk_diff_path=MVSK_DIFF_FILE,
        qlib_diff_path=QLIB_DIFF_FILE,
        status_path=SHADOW_STATUS_FILE,
        window_start=EVAL_WINDOW_START,
        window_end=EVAL_WINDOW_END,
        qlib_archived=QLIB_ARCHIVED,
    )

    report_path = SHADOW_REPORT_DIR / f"shadow_30day_eval_{report.eval_date}.md"
    report_path.write_text(report.to_markdown(), encoding="utf-8")
    print(f"评估报告已生成: {report_path}")
    print(f"  MVSK Δ夏普:   {report.mvsk_delta_sharpe:+.4f}")
    print(f"  MVSK 通过:    {'✅' if report.mvsk_pass else '❌'}")
    if report.qlib_archived:
        print(f"  qlib:         {QLIB_ARCHIVED_REASON}")
    else:
        print(f"  qlib Δ夏普:   {report.qlib_delta_sharpe:+.4f}")
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


def run_preflight() -> bool:
    """Shadow 30 天窗口启动前自检 (默认评估窗 2026-09-13~2026-10-12; 可用
    SHADOW30_EVAL_START / SHADOW30_EVAL_END 环境变量覆盖).

    R-6 (2026-09-07) 后 qlib_lgb_v2 停跑归档: 每日运行/自检仅要求 W7.2.8
    (MVSK P5-2) 前置依赖; qlib 相关项仅在 USE_QLIB_LGB_V2=1 强制启用时校验.
    逐项输出诊断并给出整体 fail-closed 判定:
      - 每个"阻塞项"失败 → 整体 NOT READY (不可启动 / 需先修复)
      - "警告项"失败仅提示不阻断 (如 feature flag 关闭)

    校验项:
      1. 窗口状态: 未启动 / 进行中 / 已完成
      2. feature flags: USE_MVSK_MID_LAYER (默认开) / USE_QLIB_LGB_V2 (默认关=R-6 归档)
      3. shadow 基础设施可导入 (W7.1.6/W7.1.7; qlib 符号仅强制启用时校验)
      4. qlib_lgb_v2 生产模型 (R-6 归档默认不要求; USE_QLIB_LGB_V2=1 时 block 校验)
      5. 报告输出目录 reports/shadow 可写/可自建
      6. 30 天评估器可导入 (--evaluate 阶段依赖)
      7. MVSK 378d 收益率缓存: reports/shadow/mvsk_mid_layer_returns_378d.parquet
         存在且 ≥378 行且覆盖当前 mid 标的集 (否则运行期自动重建)

    Returns:
        bool: True = 前置就绪可启动; False = 存在阻塞项需先处理.
    """
    results: list[dict] = []
    blocking_failures: list[str] = []
    warning_failures: list[str] = []

    # ---- 1. 窗口状态 ----
    status = _load_status()
    if status.terminated:
        msg = (
            f"窗口已于 {status.terminated_date or '?'} fail-fast 终止"
            f"({status.terminated_reason or status.fail_fast_reason or '未知原因'}); "
            "修复后先 --reset 复位再重启"
        )
        results.append({"name": "窗口状态", "ok": False, "level": "block", "detail": msg})
        blocking_failures.append(msg)
    elif status.end_date:
        msg = f"窗口已于 {status.end_date} 完成 ({status.days_elapsed} 天), 请勿重复启动"
        results.append({"name": "窗口状态", "ok": False, "level": "block", "detail": msg})
        blocking_failures.append(msg)
    elif status.start_date:
        detail = f"窗口进行中 (已运行 {status.days_elapsed}/{VALIDATION_WINDOW_DAYS} 天), 可继续每日运行"
        results.append({"name": "窗口状态", "ok": True, "level": "ok", "detail": detail})
    else:
        detail = f"窗口未启动 (评估窗首日 {EVAL_WINDOW_START} 起推进, 目标 {VALIDATION_WINDOW_DAYS} 天)"
        results.append({"name": "窗口状态", "ok": True, "level": "ok", "detail": detail})

    # ---- 2. feature flags (USE_MVSK 默认开; USE_QLIB 默认关 = R-6 归档停跑) ----
    flag_defaults = {"USE_MVSK_MID_LAYER": "1", "USE_QLIB_LGB_V2": "0"}
    for flag in ("USE_MVSK_MID_LAYER", "USE_QLIB_LGB_V2"):
        val = os.environ.get(flag, flag_defaults[flag])
        enabled = val.lower() in ("1", "true", "yes")
        if flag == "USE_QLIB_LGB_V2" and not enabled:
            state = "R-6 归档停跑 (重开需显式 =1 且修复接线)"
        else:
            state = "启用" if enabled else "跳过 (影子将不记录该维度)"
        results.append(
            {
                "name": f"feature flag {flag}",
                "ok": enabled,
                "level": "ok" if enabled else "warn",
                "detail": f"{val} → {state}",
            }
        )
        if not enabled and flag != "USE_QLIB_LGB_V2":
            warning_failures.append(f"{flag} 关闭, 对应影子维度将不产出")

    # ---- 3. shadow 基础设施可导入 (W7.1.6 / W7.1.7; qlib 符号仅强制启用时校验) ----
    qlib_forced = os.environ.get("USE_QLIB_LGB_V2", "0").lower() in ("1", "true", "yes")
    infra_checks = [
        ("utils.universe.portfolio_builder", "apply_mvsk_shadow_to_mid_layer"),
    ]
    if qlib_forced:
        infra_checks += [
            ("utils.signal_fusion", "apply_qlib_lgb_v2_shadow"),
            ("utils.signal_fusion", "register_qlib_lgb_v2_shadow"),
        ]
    for mod, attr in infra_checks:
        ok, detail = _check_import(mod, attr)
        level = "block" if not ok else "ok"
        results.append({"name": f"shadow 依赖 {attr}", "ok": ok, "level": level, "detail": detail})
        if not ok:
            blocking_failures.append(f"shadow 基础设施缺失: {attr}")

    # ---- 4. qlib_lgb_v2 生产模型落盘 (R-6 归档: 仅 USE_QLIB_LGB_V2=1 时要求) ----
    reports_dir = Path(_PROJECT_ROOT) / "reports"
    model_pkl = _latest_glob(str(reports_dir / "qlib_model_*.pkl"))
    pred_csv = _latest_glob(str(reports_dir / "predictions_*.csv"))
    if not qlib_forced:
        detail = "R-6 (2026-09-07) 已归档停跑, 不要求生产模型 (USE_QLIB_LGB_V2=1 强制启用时才校验)"
        results.append({"name": "qlib_lgb_v2 生产模型 (归档)", "ok": True, "level": "ok", "detail": detail})
    elif model_pkl and pred_csv:
        detail = f"model={model_pkl.name}, predictions={pred_csv.name}"
        results.append(
            {"name": "qlib_lgb_v2 生产模型 (强制启用)", "ok": True, "level": "ok", "detail": detail}
        )
    else:
        missing = []
        if not model_pkl:
            missing.append("qlib_model_*.pkl")
        if not pred_csv:
            missing.append("predictions_*.csv")
        detail = f"未找到 {', '.join(missing)} (reports/), 强制启用 qlib 但模型缺失"
        results.append(
            {"name": "qlib_lgb_v2 生产模型 (强制启用)", "ok": False, "level": "block", "detail": detail}
        )
        blocking_failures.append(f"qlib 生产模型缺失 (USE_QLIB_LGB_V2=1): {detail}")

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
        results.append({"name": "输出目录 reports/shadow", "ok": False, "level": "block", "detail": detail})
        blocking_failures.append(detail)

    # ---- 6. 30 天评估器可导入 (--evaluate 阶段) ----
    eval_ok, eval_detail = _check_import("utils.shadow_30day_evaluator", "Shadow30DayEvaluator")
    level = "block" if not eval_ok else "ok"
    results.append({"name": "评估器 (--evaluate)", "ok": eval_ok, "level": level, "detail": eval_detail})
    if not eval_ok:
        blocking_failures.append("评估器导入失败, 30 天后无法生成评估报告")

    # ---- 7. MVSK 378d 收益率缓存 (W7.2.8 首日启动不再依赖实时拉取) ----
    mvsk_cache_ok = False
    mvsk_cache_detail = ""
    if MVSK_RETURNS_CACHE.exists():
        try:
            import pandas as pd

            cached = pd.read_parquet(MVSK_RETURNS_CACHE)
            n_rows = len(cached)
            mvsk_cache_ok = n_rows >= 378
            mvsk_cache_detail = f"{MVSK_RETURNS_CACHE.name} ({n_rows} 行 x {len(cached.columns)} 列" + (
                "; 须覆盖当前 mid 标的集, 否则运行期自动重建)" if mvsk_cache_ok
                else ", <378 行, 需重新预生成)"
            )
        except Exception as e:  # noqa: BLE001 — 自检需捕获一切读取失败
            mvsk_cache_detail = f"缓存读取失败: {e}"
    else:
        mvsk_cache_detail = f"缓存不存在: {MVSK_RETURNS_CACHE.name}"
    level = "block" if not mvsk_cache_ok else "ok"
    results.append(
        {
            "name": "MVSK 378d 收益率缓存",
            "ok": mvsk_cache_ok,
            "level": level,
            "detail": mvsk_cache_detail,
        }
    )
    if not mvsk_cache_ok:
        blocking_failures.append(
            f"MVSK 378d 收益率缓存缺失: {mvsk_cache_detail} — 首日 cron 将依赖"
            "实时拉取 2y 行情 (有失败风险), 应先手动预生成: "
            f"py -X utf8 scripts/launch_shadow_30day.py --date <T> 触发落缓存"
        )

    # ---- 汇总输出 ----
    print("=" * 60)
    print(f"Shadow 30 天验证启动前自检 (MVSK P5-2; qlib R-6 已归档; 评估窗 {EVAL_WINDOW_START}~{EVAL_WINDOW_END})")
    print("=" * 60)
    for r in results:
        key = "ok" if r["ok"] else r["level"]
        mark = {"ok": "✅", "block": "❌", "warn": "⚠"}.get(key, "•")
        print(f"  {mark} {r['name']}: {r['detail']}")
    print("-" * 60)
    ready = not blocking_failures
    if ready:
        print(f"✅ 前置就绪: 评估窗首日 {EVAL_WINDOW_START} cron 可安全启动 30 天影子窗口")
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
    parser.add_argument(
        "--reset",
        action="store_true",
        help="复位 30 天窗口 (fail-fast 一次性锁存终止后, 人工修复完重启; 状态归档 .bak)",
    )
    args = parser.parse_args()

    if args.preflight:
        sys.exit(0 if run_preflight() else 1)
    elif args.status:
        show_status()
    elif args.evaluate:
        run_evaluate()
    elif args.reset:
        run_reset()
    else:
        result = run_daily_shadow(args.date)
        # 一次性 fail-fast: 仅"首触当次" exit 1 报警; 窗口已 terminated 时
        # run_daily_shadow 顶部直接返回 fail_fast_triggered=False (exit 0)
        if result.fail_fast_triggered:
            sys.exit(1)


if __name__ == "__main__":
    main()
