"""Baostock 数据下载器（P1.1 + P1.2 + P1.3 改进）

数据源：baostock（免费、无配额、稳定）
    - OHLCV: query_history_k_data_plus (日频, 前复权)
    - 财务: query_profit_data (ROE/毛利率/净利率) + query_balance_data (负债率)
    - 估值: query_history_k_data_plus 含 peTTM/pbMRQ/psTTM 字段
    - 基准: sh.000300 沪深 300 指数（替代 510300 ETF, baostock 不支持 ETF）

输出格式：
    cache/ohlcv/{symbol}_2y.parquet          - 个股 OHLCV (2年日频)
    cache/ohlcv/sh_000300_index.parquet       - 沪深 300 指数（基准）
    cache/fundamentals/{symbol}_latest.json   - 个股最新财务指标

设计要点：
    1. baostock 单连接, 顺序拉取（避免并发冲突）
    2. 增量更新: 已存在且数据完整的跳过
    3. 错误隔离: 单只失败不阻断整体
    4. 进度显示: 每 10 只打印一次进度
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

logger = logging.getLogger("data_downloader")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OHLCV_DIR = PROJECT_ROOT / "cache" / "ohlcv"
FUNDAMENTALS_DIR = PROJECT_ROOT / "cache" / "fundamentals"


# ============================================================
# 代码格式转换
# ============================================================
def to_baostock_code(local_sym: str) -> str:
    """本地格式 -> baostock 格式

    本地格式: 600519_SH / 000333_SZ / 510300_SH
    baostock: sh.600519 / sz.000333 / sh.510300
    """
    if "_" not in local_sym:
        return local_sym
    code, exchange = local_sym.split("_", 1)
    prefix = "sh" if exchange == "SH" else "sz"
    return f"{prefix}.{code}"


def from_baostock_code(bs_code: str) -> str:
    """baostock 格式 -> 本地格式"""
    if "." not in bs_code:
        return bs_code
    prefix, code = bs_code.split(".", 1)
    exchange = "SH" if prefix == "sh" else "SZ"
    return f"{code}_{exchange}"


# ============================================================
# OHLCV 下载
# ============================================================
def download_ohlcv(
    symbol: str,
    days: int = 730,
    skip_if_exists: bool = True,
) -> pd.DataFrame | None:
    """下载单只标的的 OHLCV

    Args:
        symbol: 本地格式代码 (如 600519_SH)
        days: 历史天数（默认 730 = 2 年）
        skip_if_exists: 已存在且完整则跳过

    Returns:
        OHLCV DataFrame, 失败返回 None
    """
    parquet_path = OHLCV_DIR / f"{symbol}_2y.parquet"
    if skip_if_exists and parquet_path.exists():
        try:
            df_existing = pd.read_parquet(parquet_path)
            # P2-1 修复 (2026-09-01): 双校验 — 行数按交易日口径 (原 days*0.85 用日历天数,
            # 730 天实际约 490 个交易日, 永远达不到 620 阈值 → skip_if_exists 从不生效);
            # 并加新鲜度校验 (最后一行距今 ≤ 7 天历日, 防陈旧文件被直接复用)
            min_rows = int(days * 252 / 365 * 0.85)  # 预期交易日数, 容许 15% 缺失
            fresh_cutoff = pd.Timestamp.now() - pd.Timedelta(days=7)
            last_date = pd.to_datetime(df_existing.index[-1])
            if len(df_existing) >= min_rows and last_date >= fresh_cutoff:
                return df_existing
            logger.info(
                "[Download] %s 缓存不达标, 重新下载: rows=%d (需≥%d), last=%s (cutoff=%s)",
                symbol,
                len(df_existing),
                min_rows,
                last_date.date() if hasattr(last_date, "date") else last_date,
                fresh_cutoff.date(),
            )
        except Exception:  # noqa: BLE001
            pass  # 文件损坏, 重新下载

    import baostock as bs
    bs_code = to_baostock_code(symbol)

    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    rs = bs.query_history_k_data_plus(
        bs_code,
        "date,code,open,high,low,close,volume,amount",
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d"),
        frequency="d",
        adjustflag="2",  # 前复权
    )
    if rs.error_code != '0':
        logger.warning("[Download] %s (%s) 失败: %s", symbol, bs_code, rs.error_msg)
        return None

    rows: list[list[str]] = []
    while (rs.error_code == '0') and rs.next():
        rows.append(rs.get_row_data())

    if not rows:
        logger.warning("[Download] %s (%s) 返回 0 行", symbol, bs_code)
        return None

    df = pd.DataFrame(rows, columns=rs.fields)
    # 类型转换
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    # 移除空值行
    df = df.dropna(subset=["close"])

    # 保存
    OHLCV_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(parquet_path)
    logger.info(
        "[Download] %s (%s) OK | %d 天 | %s -> %s",
        symbol, bs_code, len(df),
        df.index[0].date() if len(df) else "N/A",
        df.index[-1].date() if len(df) else "N/A",
    )
    return df


def download_ohlcv_batch(
    symbols: list[str],
    days: int = 730,
    skip_if_exists: bool = True,
    progress_every: int = 10,
) -> tuple[int, int, list[str]]:
    """批量下载 OHLCV

    Returns:
        (success_count, failed_count, failed_symbols)
    """
    import baostock as bs

    lg = bs.login()
    if lg.error_code != '0':
        logger.error("[Download] baostock login failed: %s", lg.error_msg)
        return 0, len(symbols), list(symbols)

    success = 0
    failed: list[str] = []
    try:
        for i, sym in enumerate(symbols, 1):
            try:
                df = download_ohlcv(sym, days=days, skip_if_exists=skip_if_exists)
                if df is not None and len(df) > 0:
                    success += 1
                else:
                    failed.append(sym)
            except Exception as e:  # noqa: BLE001
                logger.warning("[Download] %s 异常: %s", sym, e)
                failed.append(sym)

            if i % progress_every == 0:
                logger.info(f"  进度: {i}/{len(symbols)} | 成功 {success} | 失败 {len(failed)}")
                # 每 20 只 sleep 0.5s 避免被限流
                if i % 20 == 0:
                    time.sleep(0.5)
    finally:
        bs.logout()

    return success, len(failed), failed


# ============================================================
# 基准下载（沪深 300 指数）
# ============================================================
def download_benchmark(
    days: int = 730,
    skip_if_exists: bool = True,
) -> pd.DataFrame | None:
    """下载沪深 300 指数作为基准（P1.3 改进：替代 510300 ETF）

    baostock 不支持 ETF 数据, 改用沪深 300 指数 (sh.000300) 作为基准
    优势: 比 ETF 更稳定（无跟踪误差、无管理费），是 A 股最重要的基准指数
    """
    parquet_path = OHLCV_DIR / "sh_000300_index.parquet"
    if skip_if_exists and parquet_path.exists():
        try:
            df_existing = pd.read_parquet(parquet_path)
            if len(df_existing) >= days * 0.85:
                logger.info("[Benchmark] 沪深300指数已存在, 跳过 | %d 天", len(df_existing))
                return df_existing
        except Exception:
            logger.warning("Unexpected error in data_downloader.py", exc_info=True)

    import baostock as bs
    lg = bs.login()
    if lg.error_code != '0':
        logger.error("[Benchmark] baostock login failed")
        return None

    try:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        rs = bs.query_history_k_data_plus(
            "sh.000300",
            "date,code,open,high,low,close,volume,amount",
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
            frequency="d",
        )
        if rs.error_code != '0':
            logger.error("[Benchmark] 查询失败: %s", rs.error_msg)
            return None

        rows = []
        while (rs.error_code == '0') and rs.next():
            rows.append(rs.get_row_data())
        if not rows:
            logger.error("[Benchmark] 返回 0 行")
            return None

        df = pd.DataFrame(rows, columns=rs.fields)
        for col in ["open", "high", "low", "close", "volume", "amount"]:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index().dropna(subset=["close"])

        OHLCV_DIR.mkdir(parents=True, exist_ok=True)
        df.to_parquet(parquet_path)
        logger.info(
            "[Benchmark] 沪深300指数下载完成 | %d 天 | %s -> %s",
            len(df), df.index[0].date(), df.index[-1].date(),
        )
        return df
    finally:
        bs.logout()


def compute_benchmark_returns() -> list[float]:
    """从沪深 300 指数计算基准日收益率（替代等权代理）

    Returns:
        benchmark_returns: List[float] 日收益率序列
    """
    parquet_path = OHLCV_DIR / "sh_000300_index.parquet"
    if not parquet_path.exists():
        logger.warning("[Benchmark] 沪深300指数文件不存在, 请先 download_benchmark()")
        return []

    df = pd.read_parquet(parquet_path)
    closes = df["close"].tolist()
    if len(closes) < 2:
        return []

    # 日收益率: close[t] / close[t-1] - 1
    returns = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            returns.append(float(closes[i] / closes[i - 1] - 1))
        else:
            returns.append(0.0)
    return returns


# ============================================================
# 财务数据下载（P1.2 改进）
# ============================================================
def _find_latest_valid_quarter(
    bs_code: str,
    max_lookback: int = 8,
) -> tuple[int, int, dict, dict] | None:
    """搜索最新一个有数据的财报季度

    baostock query_profit_data / query_balance_data 在财报未公布时返回空行，
    需要按 (year, quarter) 顺序向前搜索，直到找到第一个有 ROE/负债率数据的季度。

    Args:
        bs_code: baostock 格式代码 (如 sh.600519)
        max_lookback: 最多向前搜索多少个季度（默认 8 = 2 年）

    Returns:
        (year, quarter, profit_data, balance_data) 或 None
    """
    import baostock as bs  # 函数内 import（避免模块级依赖）

    now = datetime.now()
    # 从当前季度开始向前搜索
    year = now.year
    quarter = (now.month - 1) // 3 + 1  # 1-3月=Q1, 4-6月=Q2, ...

    for _ in range(max_lookback):
        # 先试 profit_data
        profit_data: dict = {}
        rs = bs.query_profit_data(code=bs_code, year=year, quarter=quarter)
        if rs.error_code == '0':
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            if rows:
                profit_data = dict(zip(rs.fields, rows[0], strict=False))

        # 再试 balance_data
        balance_data: dict = {}
        rs = bs.query_balance_data(code=bs_code, year=year, quarter=quarter)
        if rs.error_code == '0':
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            if rows:
                balance_data = dict(zip(rs.fields, rows[0], strict=False))

        # 判断是否有有效数据（ROE 非空 或 负债率非空）
        roe_val = profit_data.get("roeAvg", "")
        liab_val = balance_data.get("liabilityToAsset", "")
        try:
            roe_f = float(roe_val) if roe_val else 0.0
            liab_f = float(liab_val) if liab_val else 0.0
            # 必须至少有一个真实非零数据（避免空行被默认 0）
            # roe_val/liab_val 必须是非空字符串且能转 float
            if (roe_val and roe_val != "" and roe_f == roe_f and roe_f > 0) or \
               (liab_val and liab_val != "" and liab_f == liab_f and liab_f > 0):
                return year, quarter, profit_data, balance_data
        except (ValueError, TypeError):
            pass

        # 向前回退一个季度
        quarter -= 1
        if quarter < 1:
            quarter = 4
            year -= 1

    return None


def download_fundamentals(  # noqa: C901
    symbol: str,
    year: int | None = None,
    quarter: int | None = None,
    skip_if_exists: bool = True,
    force_refresh: bool = False,
) -> dict | None:
    """下载单只标的的财务指标（P1.2 改进版：自动多季度回退）

    Args:
        symbol: 本地格式代码 (如 600519_SH)
        year: 报告年（None = 自动搜索最新可用季度）
        quarter: 报告季（None = 自动搜索）
        skip_if_exists: 已存在则跳过（force_refresh 优先）
        force_refresh: 强制重新下载（忽略 skip_if_exists）

    Returns:
        {pe, pb, ps, roe, gross_margin, debt_to_equity, market_cap, revenue, ...}
        失败返回 None
    """
    json_path = FUNDAMENTALS_DIR / f"{symbol}_latest.json"

    # 检查是否需要重新下载
    if skip_if_exists and not force_refresh and json_path.exists():
        try:
            with open(json_path, encoding="utf-8") as f:
                existing = json.load(f)
            # P1.2 修复：旧文件如果 ROE=0（即下载时取了未公布季度），需要重新下载
            if existing.get("roe", 0) != 0 or existing.get("gross_margin", 0) != 0:
                # P1.2 改进3: 若旧文件 profit_growth=0（YoY 计算被覆盖的 bug），需重新下载
                # 注意: 部分 ETF/新股可能确实无法计算 YoY（profit_growth=0 合法），但绝大多数股票应有非零 YoY
                # 这里用 roe > 0 且 profit_growth == 0 作为「需要重下」的判据
                if existing.get("roe", 0) > 0 and existing.get("profit_growth", 0) == 0:
                    logger.info("[Fundamentals] %s 旧文件 profit_growth=0 (bug 覆盖), 重新下载", symbol)
                else:
                    return existing
            else:
                logger.info("[Fundamentals] %s 旧文件 ROE=0, 重新下载", symbol)
        except Exception:
            logger.warning("Unexpected error in data_downloader.py", exc_info=True)

    import baostock as bs
    bs_code = to_baostock_code(symbol)

    result: dict = {
        "symbol": symbol,
        "bs_code": bs_code,
        "data_quality": "real",  # baostock 是真实财务数据
    }

    try:
        # 1. 搜索最新可用财报季度
        if year is not None and quarter is not None:
            # 显式指定季度
            profit_data: dict = {}
            balance_data: dict = {}
            rs = bs.query_profit_data(code=bs_code, year=year, quarter=quarter)
            if rs.error_code == '0':
                rows = []
                while rs.next():
                    rows.append(rs.get_row_data())
                if rows:
                    profit_data = dict(zip(rs.fields, rows[0], strict=False))
            rs = bs.query_balance_data(code=bs_code, year=year, quarter=quarter)
            if rs.error_code == '0':
                rows = []
                while rs.next():
                    rows.append(rs.get_row_data())
                if rows:
                    balance_data = dict(zip(rs.fields, rows[0], strict=False))
        else:
            # 自动搜索最新可用季度
            found = _find_latest_valid_quarter(bs_code, max_lookback=8)
            if found is None:
                logger.warning("[Fundamentals] %s (%s) 未找到有效财报季度", symbol, bs_code)
                profit_data, balance_data = {}, {}
                year, quarter = 0, 0
            else:
                year, quarter, profit_data, balance_data = found

        result["report_year"] = year
        result["report_quarter"] = quarter

        def _to_float(s: object) -> float:
            try:
                if s is None or s == "":
                    return 0.0
                v = float(s)
                return v if v == v else 0.0  # NaN check
            except (ValueError, TypeError):
                return 0.0

        # 2. 盈利能力数据
        if profit_data:
            result["roe"] = _to_float(profit_data.get("roeAvg"))
            result["net_margin"] = _to_float(profit_data.get("npMargin"))
            result["gross_margin"] = _to_float(profit_data.get("gpMargin"))
            result["net_profit"] = _to_float(profit_data.get("netProfit"))
            result["eps_ttm"] = _to_float(profit_data.get("epsTTM"))
            result["total_share"] = _to_float(profit_data.get("totalShare"))
            result["liqa_share"] = _to_float(profit_data.get("liqaShare"))

        # 3. 偿债能力数据
        if balance_data:
            liab_to_asset = _to_float(balance_data.get("liabilityToAsset"))
            result["liability_to_assets"] = liab_to_asset
            result["debt_to_equity"] = (
                liab_to_asset / (1 - liab_to_asset)
                if 0 < liab_to_asset < 1 else 0.0
            )
            result["current_ratio"] = _to_float(balance_data.get("currentRatio"))
            result["quick_ratio"] = _to_float(balance_data.get("quickRatio"))

        # 3.5 P1.2 改进：计算 YoY 净利润增长率（解锁 VT_GROWTH_COMPOSITE 因子）
        # 拉取去年同季的 net_profit，对比计算 profit_growth
        prev_year = year - 1
        try:
            rs_prev = bs.query_profit_data(
                code=bs_code, year=prev_year, quarter=quarter,
            )
            logger.info(
                "[Fundamentals] %s YoY query: year=%d Q%d prev_year=%d error=%s msg=%s",
                symbol, year, quarter, prev_year, rs_prev.error_code, rs_prev.error_msg,
            )
            if rs_prev.error_code == '0':
                rows_prev = []
                while rs_prev.next():
                    rows_prev.append(rs_prev.get_row_data())
                if rows_prev:
                    prev_data = dict(zip(rs_prev.fields, rows_prev[0], strict=False))
                    prev_net_profit = _to_float(prev_data.get("netProfit"))
                    cur_net_profit = result.get("net_profit", 0)
                    logger.info(
                        "[Fundamentals] %s YoY: prev_np=%.2f cur_np=%.2f",
                        symbol, prev_net_profit, cur_net_profit,
                    )
                    if prev_net_profit > 0 and cur_net_profit != 0:
                        # profit_growth = (本期 - 去年同期) / |去年同期|
                        # 注意：净利率为负时不影响 growth 计算
                        result["profit_growth"] = float(
                            (cur_net_profit - prev_net_profit) / abs(prev_net_profit)
                        )
                        # revenue_growth 近似用 profit_growth（baostock 季频数据无 revenue 字段）
                        result["revenue_growth"] = result["profit_growth"]
                    else:
                        result["profit_growth"] = 0.0
                        result["revenue_growth"] = 0.0
                else:
                    result["profit_growth"] = 0.0
                    result["revenue_growth"] = 0.0
            else:
                logger.warning(
                    "[Fundamentals] %s YoY query 失败: %s (year=%d Q%d)",
                    symbol, rs_prev.error_msg, prev_year, quarter,
                )
                result["profit_growth"] = 0.0
                result["revenue_growth"] = 0.0
        except Exception as e:  # noqa: BLE001
            logger.warning("[Fundamentals] %s YoY growth 计算失败: %s", symbol, e)
            result["profit_growth"] = 0.0
            result["revenue_growth"] = 0.0

        # 4. 估值指标 (PE/PB/PS) - 取最近一日
        end_date = datetime.now()
        start_date = end_date - timedelta(days=10)
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,code,close,peTTM,pbMRQ,psTTM,pcfNcfTTM,turn",
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
            frequency="d"
        )
        if rs.error_code == '0':
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            if rows:
                val_data = dict(zip(rs.fields, rows[-1], strict=False))  # 取最新一天
                result["pe"] = _to_float(val_data.get("peTTM"))
                result["pb"] = _to_float(val_data.get("pbMRQ"))
                result["ps"] = _to_float(val_data.get("psTTM"))
                result["pcf"] = _to_float(val_data.get("pcfNcfTTM"))
                result["turnover_rate"] = _to_float(val_data.get("turn"))

                # 市值估算: close * total_share
                close = _to_float(val_data.get("close"))
                total_share = result.get("total_share", 0)
                if total_share > 0:
                    result["market_cap"] = close * total_share
                else:
                    # 退化: 用 close * eps_ttm 估算 total_share (eps_ttm = net_profit / total_share)
                    eps = max(result.get("eps_ttm", 0), 0.01)
                    np = result.get("net_profit", 0)
                    if np > 0:
                        estimated_shares = np / eps
                        result["market_cap"] = close * estimated_shares
                    else:
                        result["market_cap"] = 0.0

                # 营收估算: net_profit / net_margin
                np = result.get("net_profit", 0)
                nm = result.get("net_margin", 0)
                if nm > 0 and np > 0:
                    result["revenue"] = np / nm
                else:
                    result["revenue"] = 0.0

                # 注: revenue_growth / profit_growth 已在 step 3.5 通过 YoY 计算填入
                # 这里不再覆盖为 0（P1.2 改进2: 解锁 VT_GROWTH_COMPOSITE 因子）

        # 5. 持久化
        FUNDAMENTALS_DIR.mkdir(parents=True, exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        # 判断是否成功获取核心财务数据
        has_core_data = (
            result.get("roe", 0) != 0 or
            result.get("gross_margin", 0) != 0 or
            result.get("liability_to_assets", 0) != 0
        )
        if has_core_data:
            logger.info(
                "[Fundamentals] %s (%s) OK | %dQ%d | ROE=%.4f 毛利率=%.4f PE=%.2f PB=%.2f",
                symbol, bs_code, year, quarter,
                result.get("roe", 0),
                result.get("gross_margin", 0),
                result.get("pe", 0),
                result.get("pb", 0),
            )
        else:
            logger.warning(
                "[Fundamentals] %s (%s) 仅有估值数据 | PE=%.2f (核心财务数据为空)",
                symbol, bs_code, result.get("pe", 0),
            )
        return result

    except Exception as e:  # noqa: BLE001
        logger.warning("[Fundamentals] %s (%s) 异常: %s", symbol, bs_code, e)
        return None


def download_fundamentals_batch(
    symbols: list[str],
    year: int | None = None,
    quarter: int | None = None,
    skip_if_exists: bool = True,
    progress_every: int = 10,
    refresh_stale: bool = True,
) -> tuple[int, int, list[str]]:
    """批量下载财务指标

    Args:
        refresh_stale: 若 True, ROE=0 的旧文件会被强制重新下载（修复 P1.2 季度回退问题）

    Returns:
        (success_count, failed_count, failed_symbols)
    """
    import baostock as bs
    lg = bs.login()
    if lg.error_code != '0':
        logger.error("[Fundamentals] baostock login failed")
        return 0, len(symbols), list(symbols)

    success = 0
    failed: list[str] = []
    try:
        for i, sym in enumerate(symbols, 1):
            try:
                result = download_fundamentals(
                    sym, year=year, quarter=quarter,
                    skip_if_exists=skip_if_exists,
                    force_refresh=False,  # download_fundamentals 内部已检测 ROE=0 触发重下
                )
                # 成功标准：ROE 或 毛利率 非零（即取到了真实财报数据）
                if result and (result.get("roe", 0) != 0 or result.get("gross_margin", 0) != 0):
                    success += 1
                else:
                    failed.append(sym)
            except Exception as e:  # noqa: BLE001
                logger.warning("[Fundamentals] %s 异常: %s", sym, e)
                failed.append(sym)

            if i % progress_every == 0:
                logger.info(f"  进度: {i}/{len(symbols)} | 成功 {success} | 失败 {len(failed)}")
                if i % 20 == 0:
                    time.sleep(0.5)
    finally:
        bs.logout()

    return success, len(failed), failed


# ============================================================
# P2.2 质量变化类因子支持：历史季度数据下载
# ============================================================
def download_fundamentals_history(  # noqa: C901
    symbol: str,
    n_quarters: int = 8,
    skip_if_exists: bool = True,
    force_refresh: bool = False,
) -> dict:
    """下载单只标的的过去 N 个季度财务指标历史（P2.2 质量变化因子支持）

    用于计算 ROE/毛利率/负债率/增长率的 YoY 变化（Q vs Q-4）。
    数据缓存至 cache/fundamentals/{symbol}_history.json

    P2.2 改进版新增字段（用于改进 VT_QUALTREND_GROWTH_ACCEL）：
        - revenue: 主营营业收入（来自 baostock MBRevenue, 单位:百万元）
        - yoy_pni: 扣非净利润同比增长率（来自 baostock query_growth_data YOYPNI, %）
        - yoy_ni: 净利润同比增长率（来自 baostock query_growth_data YOYNI, %）
        - yoy_eps: 基本每股收益同比增长率（来自 baostock query_growth_data YOYEPSBasic, %）

    Args:
        symbol: 标的代码（如 600519 / sh.600519）
        n_quarters: 下载多少个季度（默认 8 = 2 年，支持 YoY 计算）
        skip_if_exists: 若缓存文件存在且有效则跳过
        force_refresh: 强制重新下载（忽略 skip_if_exists，用于补齐新字段）

    Returns:
        {
            "symbol": str,
            "quarters": [
                {"year": 2025, "quarter": 2, "roe": ..., "gross_margin": ..., ...},
                ...
            ],
            "n_valid": int,
            "data_quality": "real" | "missing",
        }
    """
    bs_code = to_baostock_code(symbol)

    cache_dir = PROJECT_ROOT / "cache" / "fundamentals"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{symbol}_history.json"

    # 缓存检查（P2.2 改进：除 n_valid >= 4 外，还验证最新季度是否已含 revenue/yoy_pni 字段）
    # 用 schema_version 字段避免无限重下：标记为 v2 表示已尝试过补齐新字段
    SCHEMA_VERSION = 2  # noqa: N806
    if skip_if_exists and not force_refresh and cache_path.exists():
        try:
            with open(cache_path, encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("n_valid", 0) >= 4:
                # P2.2 改进：检查 schema_version，v2 表示已尝试补齐 revenue/yoy_pni
                if cached.get("schema_version", 1) >= SCHEMA_VERSION:
                    return cached
                logger.info(
                    "[FundHistory] %s 旧缓存 schema_version=v1，升级到 v2 补齐 revenue/yoy_pni",
                    symbol,
                )
        except Exception:
            logger.warning("Unexpected error in data_downloader.py", exc_info=True)

    import baostock as bs

    result = {
        "symbol": symbol,
        "quarters": [],
        "n_valid": 0,
        "data_quality": "missing",
        "schema_version": 2,  # P2.2 改进：v2 含 revenue/yoy_pni 字段
    }

    # 从当前季度向前拉取 n_quarters 个季度
    now = datetime.now()
    year = now.year
    quarter = (now.month - 1) // 3 + 1

    for _ in range(n_quarters + 2):  # 多拉 2 个季度作为缓冲
        q_data = {"year": year, "quarter": quarter}

        # profit_data（P2.2 改进：增加 revenue=MBRevenue）
        try:
            rs = bs.query_profit_data(code=bs_code, year=year, quarter=quarter)
            if rs.error_code == '0':
                rows = []
                while rs.next():
                    rows.append(rs.get_row_data())
                if rows:
                    pd = dict(zip(rs.fields, rows[0], strict=False))
                    q_data["roe"] = _safe_float(pd.get("roeAvg"))
                    q_data["net_margin"] = _safe_float(pd.get("npMargin"))
                    q_data["gross_margin"] = _safe_float(pd.get("gpMargin"))
                    q_data["net_profit"] = _safe_float(pd.get("netProfit"))
                    q_data["eps_ttm"] = _safe_float(pd.get("epsTTM"))
                    # P2.2 新增：营收字段（用于计算营收增长率加速）
                    q_data["revenue"] = _safe_float(pd.get("MBRevenue"))
        except Exception as e:  # noqa: BLE001
            logger.debug("[FundHistory] %s Q%d %d profit 异常: %s", symbol, quarter, year, e)

        # balance_data
        try:
            rs = bs.query_balance_data(code=bs_code, year=year, quarter=quarter)
            if rs.error_code == '0':
                rows = []
                while rs.next():
                    rows.append(rs.get_row_data())
                if rows:
                    bd = dict(zip(rs.fields, rows[0], strict=False))
                    q_data["debt_to_equity"] = _safe_float(bd.get("liabilityToAsset"))
                    q_data["current_ratio"] = _safe_float(bd.get("currentRatio"))
        except Exception as e:  # noqa: BLE001
            logger.debug("[FundHistory] %s Q%d %d balance 异常: %s", symbol, quarter, year, e)

        # P2.2 新增：growth_data（含扣非净利润同比增长率 YOYPNI）
        try:
            rs = bs.query_growth_data(code=bs_code, year=year, quarter=quarter)
            if rs.error_code == '0':
                rows = []
                while rs.next():
                    rows.append(rs.get_row_data())
                if rows:
                    gd = dict(zip(rs.fields, rows[0], strict=False))
                    # YOYPNI: 归母扣非净利润同比增长率(%)
                    q_data["yoy_pni"] = _safe_float(gd.get("YOYPNI"))
                    # YOYNI: 净利润同比增长率(%)
                    q_data["yoy_ni"] = _safe_float(gd.get("YOYNI"))
                    # YOYEPSBasic: 基本每股收益同比增长率(%)
                    q_data["yoy_eps"] = _safe_float(gd.get("YOYEPSBasic"))
        except Exception as e:  # noqa: BLE001
            logger.debug("[FundHistory] %s Q%d %d growth 异常: %s", symbol, quarter, year, e)

        # 只保留有有效数据的季度（ROE 或 毛利率 非零）
        if q_data.get("roe", 0) != 0 or q_data.get("gross_margin", 0) != 0:
            result["quarters"].append(q_data)

        # 向前回退一个季度
        quarter -= 1
        if quarter < 1:
            quarter = 4
            year -= 1

    result["n_valid"] = len(result["quarters"])
    result["data_quality"] = "real" if result["n_valid"] >= 4 else "missing"

    # 持久化
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False, default=str)
    except Exception as e:  # noqa: BLE001
        logger.warning("[FundHistory] %s 持久化失败: %s", symbol, e)

    return result


def download_fundamentals_history_batch(
    symbols: list[str],
    n_quarters: int = 8,
    skip_if_exists: bool = True,
    progress_every: int = 10,
    force_refresh: bool = False,
) -> tuple[int, int, list[str]]:
    """批量下载历史季度财务指标（P2.2 质量变化因子支持）

    Args:
        force_refresh: 强制重新下载（用于补齐新字段 revenue/yoy_pni）

    Returns:
        (success_count, failed_count, failed_symbols)
    """
    import baostock as bs
    lg = bs.login()
    if lg.error_code != '0':
        logger.error("[FundHistory] baostock login failed")
        return 0, len(symbols), list(symbols)

    success = 0
    failed: list[str] = []
    try:
        for i, sym in enumerate(symbols, 1):
            try:
                result = download_fundamentals_history(
                    sym, n_quarters=n_quarters,
                    skip_if_exists=skip_if_exists,
                    force_refresh=force_refresh,
                )
                if result and result.get("n_valid", 0) >= 4:
                    success += 1
                else:
                    failed.append(sym)
            except Exception as e:  # noqa: BLE001
                logger.warning("[FundHistory] %s 异常: %s", sym, e)
                failed.append(sym)

            if i % progress_every == 0:
                logger.info(f"  进度: {i}/{len(symbols)} | 成功 {success} | 失败 {len(failed)}")
                if i % 20 == 0:
                    time.sleep(0.5)
    finally:
        bs.logout()

    return success, len(failed), failed


def _safe_float(s: object) -> float:
    """安全转换为 float，失败返回 0.0"""
    try:
        if s is None or s == "":
            return 0.0
        return float(s)
    except (TypeError, ValueError):
        return 0.0


# ============================================================
# 主入口
# ============================================================
def download_all(
    symbols: list[str],
    days: int = 730,
    include_benchmark: bool = True,
    include_fundamentals: bool = True,
    skip_if_exists: bool = True,
) -> dict:
    """下载全部数据（OHLCV + 基准 + 财务）

    Returns:
        {
            "ohlcv": {"success": int, "failed": int, "failed_symbols": [...]},
            "benchmark": {...},
            "fundamentals": {...},
        }
    """
    logger.info("=" * 70)
    logger.info(f"Baostock 数据下载（标的 {len(symbols)} 个, {days} 天历史）")
    logger.info("=" * 70)

    result: dict = {}

    # 1. OHLCV
    logger.info(f"\n[1/3] 下载 OHLCV（{len(symbols)} 个标的）")
    s, f, failed_syms = download_ohlcv_batch(
        symbols, days=days, skip_if_exists=skip_if_exists
    )
    result["ohlcv"] = {
        "success": s, "failed": f, "failed_symbols": failed_syms,
    }
    logger.info(f"  OHLCV 完成: 成功 {s} / 失败 {f}")

    # 2. 基准（沪深300指数）
    if include_benchmark:
        logger.info("\n[2/3] 下载沪深300指数基准")
        bench_df = download_benchmark(days=days, skip_if_exists=skip_if_exists)
        if bench_df is not None:
            logger.info(f"  基准完成: {len(bench_df)} 天")
            result["benchmark"] = {"success": 1, "failed": 0}
        else:
            logger.info("  基准失败")
            result["benchmark"] = {"success": 0, "failed": 1}

    # 3. 财务数据
    if include_fundamentals:
        logger.info(f"\n[3/3] 下载财务指标（{len(symbols)} 个标的）")
        s, f, failed_syms = download_fundamentals_batch(
            symbols, skip_if_exists=skip_if_exists
        )
        result["fundamentals"] = {
            "success": s, "failed": f, "failed_symbols": failed_syms,
        }
        logger.info(f"  财务完成: 成功 {s} / 失败 {f}")

    logger.info("\n" + "=" * 70)
    logger.info("全部下载完成")
    logger.info("=" * 70)
    return result


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s | %(message)s",
    )

    from cache.symbol_universe import get_industry_distribution, get_universe

    universe = get_universe()
    logger.info(f"标的池: {len(universe)} 个")
    logger.info(f"行业分布: {get_industry_distribution()}")

    # 全量下载（OHLCV + 基准 + 财务）
    result = download_all(
        symbols=universe,
        days=730,
        include_benchmark=True,
        include_fundamentals=True,
        skip_if_exists=True,
    )

    # 失败重试一次
    all_failed = (
        result.get("ohlcv", {}).get("failed_symbols", []) +
        result.get("fundamentals", {}).get("failed_symbols", [])
    )
    if all_failed:
        logger.info(f"\n=== 失败重试（{len(set(all_failed))} 个）===")
        unique_failed = sorted(set(all_failed))
        for s in unique_failed[:10]:
            logger.info(f"  {s}")
