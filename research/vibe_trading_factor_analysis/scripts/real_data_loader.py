"""真实 A 股历史数据加载器 - 加载 data_cache/*.parquet 与 cache/ohlcv/*.parquet 为流水线输入

数据源（P1.1+P1.2+P1.3 + 105 标的池扩展后）：
    - OHLCV (优先): data_cache/historical_{code}_5y_base.parquet（baostock 5 年日频前复权, 105 标的池）
    - OHLCV (降级): cache/ohlcv/{symbol}_2y.parquet（baostock 2 年日频前复权, 兼容旧数据）
    - 基准: cache/ohlcv/sh_000300_index.parquet（沪深 300 指数，替代 510300 ETF 与等权代理）
    - 财务: cache/fundamentals/{symbol}_latest.json（baostock 真实 PE/PB/ROE/毛利率/负债率）

标的池（P1.1 扩展）:
    - 优先使用 cache/symbol_universe.py 中的 105 标的池（行业均衡, 8 个板块）
    - 旧 23 标的池自动包含在 105 标的池中（向后兼容）

降级链（保证流水线不会因单一数据源缺失而崩）：
    - OHLCV: 5y 数据缺失时降级到 2y 数据; 都缺失时跳过该标的
    - 基准: 沪深300指数缺失时降级为等权代理
    - 财务: baostock 真实数据缺失时降级为 iFinD，再降级为价量代理

输出符合 AlphaFactorLibrary.compute_all 与 PipelineOrchestrator.run 的输入格式：
    price_data: Dict[str, Dict[str, List[float]]]
        {"000333_SZ": {"closes": [...], "volumes": [...], "highs": [...], "lows": [...]}}
    benchmark_returns: List[float]
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger("real_data_loader")
PROJECT_ROOT = Path(__file__).resolve().parents[3]
OHLCV_DIR = PROJECT_ROOT / "cache" / "ohlcv"
# 数据缓存通过集中配置管理 (支持 QUANT_DATA_ROOT 迁移到 D 盘)
try:
    from utils.path_config import get_data_cache_dir
    DATA_CACHE_DIR = get_data_cache_dir()  # P1.1 扩展: 5 年日 K 数据
except ImportError:
    DATA_CACHE_DIR = PROJECT_ROOT / "data_cache"  # 回退: 项目目录
FUNDAMENTALS_DIR = PROJECT_ROOT / "cache" / "fundamentals"
BENCHMARK_PARQUET = OHLCV_DIR / "sh_000300_index.parquet"


def _load_symbol_universe() -> list[str]:
    """加载 symbol_universe.py 中定义的 105 标的池

    Returns:
        标的代码列表 (如 ["600519_SH", "000858_SZ", ...])
        若加载失败返回空列表
    """
    try:
        import sys
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from cache.symbol_universe import get_universe
        return get_universe()
    except Exception as e:
        logger.warning("[RealDataLoader] symbol_universe 加载失败: %s", e)
        return []


def list_available_symbols() -> list[str]:
    """列出所有可用标的

    优先级：
        1. symbol_universe.get_universe() 中的 105 标的池 (有数据文件的)
        2. data_cache/historical_{code}_5y_base.parquet 文件扫描
        3. cache/ohlcv/{symbol}_2y.parquet 文件扫描 (旧数据兼容)

    Returns:
        标的代码列表 (如 ["600519_SH", "000858_SZ", ...])
    """
    universe = _load_symbol_universe()
    available: list[str] = []

    # 1. 优先从标的池中筛选有数据文件的标的
    if universe:
        for sym in universe:
            code = sym.split("_")[0]
            has_5y = (DATA_CACHE_DIR / f"historical_{code}_5y_base.parquet").exists()
            has_2y = (OHLCV_DIR / f"{sym}_2y.parquet").exists()
            if has_5y or has_2y:
                available.append(sym)
        if available:
            logger.info(
                "[RealDataLoader] 标的池可用 | 池=%d | 有数据=%d",
                len(universe), len(available),
            )
            return available

    # 2. 降级：扫描 data_cache 目录
    if DATA_CACHE_DIR.exists():
        files_5y = sorted(DATA_CACHE_DIR.glob("historical_*_5y_base.parquet"))
        syms_from_5y = []
        for f in files_5y:
            # historical_600519_5y_base.parquet -> 600519
            code = f.stem.replace("historical_", "").replace("_5y_base", "")
            # 推断交易所: 60/68/51/58 开头为 SH, 其余为 SZ
            exchange = "SH" if code.startswith(("60", "68", "51", "58")) else "SZ"
            syms_from_5y.append(f"{code}_{exchange}")
        if syms_from_5y:
            logger.info(
                "[RealDataLoader] data_cache 扫描可用 | %d 个标的",
                len(syms_from_5y),
            )
            return syms_from_5y

    # 3. 最终降级：扫描 cache/ohlcv 目录 (旧 2y 数据)
    if OHLCV_DIR.exists():
        files_2y = sorted(OHLCV_DIR.glob("*_2y.parquet"))
        syms_from_2y = [f.stem.replace("_2y", "") for f in files_2y]
        logger.info(
            "[RealDataLoader] cache/ohlcv 扫描可用 (旧 2y) | %d 个标的",
            len(syms_from_2y),
        )
        return syms_from_2y

    return []


def _load_ohlcv_dataframe(symbol: str) -> pd.DataFrame | None:
    """加载单只标的的 OHLCV DataFrame

    优先级:
        1. data_cache/historical_{code}_5y_base.parquet (5 年日 K, P1.1 扩展)
        2. cache/ohlcv/{symbol}_2y.parquet (2 年日 K, 旧数据兼容)

    Args:
        symbol: 本地格式代码 (如 600519_SH)

    Returns:
        OHLCV DataFrame 或 None
    """
    code = symbol.split("_")[0]

    # 1. 优先 5y 数据
    path_5y = DATA_CACHE_DIR / f"historical_{code}_5y_base.parquet"
    if path_5y.exists():
        try:
            df = pd.read_parquet(path_5y)
            if len(df) > 0:
                return df
        except Exception as e:
            logger.warning("[RealDataLoader] %s 5y 数据读取失败, 降级 2y: %s", symbol, e)

    # 2. 降级 2y 数据
    path_2y = OHLCV_DIR / f"{symbol}_2y.parquet"
    if path_2y.exists():
        try:
            df = pd.read_parquet(path_2y)
            if len(df) > 0:
                return df
        except Exception as e:
            logger.warning("[RealDataLoader] %s 2y 数据读取失败: %s", symbol, e)

    return None


def load_price_data(symbols: list[str] = None) -> dict[str, dict[str, list[float]]]:
    """加载多个标的的 OHLCV 历史

    数据源优先级:
        1. data_cache/historical_{code}_5y_base.parquet (5 年日 K, P1.1 扩展)
        2. cache/ohlcv/{symbol}_2y.parquet (2 年日 K, 旧数据兼容)

    Args:
        symbols: 标的列表，None 表示全部加载

    Returns:
        price_data: {symbol: {"closes": [...], "volumes": [...], "highs": [...], "lows": [...]}}
    """
    if symbols is None:
        symbols = list_available_symbols()

    price_data = {}
    n_from_5y = 0
    n_from_2y = 0

    for sym in symbols:
        code = sym.split("_")[0]
        df = _load_ohlcv_dataframe(sym)
        if df is None:
            logger.warning(
                "[RealDataLoader] %s 数据文件不存在 (5y + 2y 均缺失), 跳过", sym
            )
            continue
        try:
            # 判断数据来源用于统计
            path_5y = DATA_CACHE_DIR / f"historical_{code}_5y_base.parquet"
            source = "5y" if path_5y.exists() and len(df) > 700 else "2y"
            if source == "5y":
                n_from_5y += 1
            else:
                n_from_2y += 1

            # 列名映射：open/high/low/close/volume -> closes/volumes/highs/lows
            price_data[sym] = {
                "closes": df["close"].astype(float).tolist(),
                "volumes": df["volume"].astype(float).tolist(),
                "highs": df["high"].astype(float).tolist(),
                "lows": df["low"].astype(float).tolist(),
                "opens": df["open"].astype(float).tolist(),
            }
            logger.info(
                "[RealDataLoader] %s 加载完成 [%s] | %d 天 | %s -> %s",
                sym, source, len(df),
                df.index[0].date() if hasattr(df.index[0], "date") else df.index[0],
                df.index[-1].date() if hasattr(df.index[-1], "date") else df.index[-1],
            )
        except Exception as e:
            logger.error("[RealDataLoader] %s 加载失败: %s", sym, e)

    logger.info(
        "[RealDataLoader] 总计加载 %d 个标的 | 5y=%d | 2y=%d",
        len(price_data), n_from_5y, n_from_2y,
    )
    return price_data


def load_benchmark_returns() -> list[float]:
    """加载沪深 300 指数基准日收益率（P1.3 改进）

    替代原先的等权代理：
        - 真实基准 = 沪深 300 指数日收益率（来自 baostock sh.000300）
        - 比 ETF 更稳定（无跟踪误差、无管理费）
        - 是 A 股最重要的宽基指数基准

    降级链：
        1. 优先读取 cache/ohlcv/sh_000300_index.parquet
        2. 缺失时降级为等权代理（调用方需提供 price_data）

    Returns:
        benchmark_returns: List[float] 日收益率序列
    """
    if BENCHMARK_PARQUET.exists():
        try:
            df = pd.read_parquet(BENCHMARK_PARQUET)
            closes = df["close"].astype(float).tolist()
            if len(closes) < 2:
                logger.warning("[RealDataLoader] 沪深300数据过短 (%d 天), 降级等权", len(closes))
                return []
            returns = []
            for i in range(1, len(closes)):
                if closes[i - 1] > 0:
                    returns.append(float(closes[i] / closes[i - 1] - 1))
                else:
                    returns.append(0.0)
            cum_ret = (np.prod([1 + r for r in returns]) - 1) * 100
            logger.info(
                "[RealDataLoader] 沪深300基准加载完成 | %d 天 | %s -> %s | 累计收益 %.2f%%",
                len(returns),
                df.index[0].date() if hasattr(df.index[0], "date") else df.index[0],
                df.index[-1].date() if hasattr(df.index[-1], "date") else df.index[-1],
                cum_ret,
            )
            return returns
        except Exception as e:
            logger.warning("[RealDataLoader] 沪深300加载失败, 降级等权: %s", e)
    else:
        logger.warning("[RealDataLoader] sh_000300_index.parquet 不存在, 降级等权代理")

    # 调用方应使用 compute_equal_weight_benchmark(price_data) 作为最终降级
    return []


def compute_equal_weight_benchmark(price_data: dict[str, dict[str, list[float]]]) -> list[float]:
    """计算等权日收益率基准代理（最终降级方案）

    当沪深 300 指数数据不可用时使用，作为兜底基准。

    Args:
        price_data: load_price_data 的输出

    Returns:
        benchmark_returns: List[float]，与最长 closes 序列等长
    """
    # 收集所有标的的日收益率
    all_returns = []
    max_len = 0
    for _sym, data in price_data.items():
        closes = data["closes"]
        if len(closes) < 2:
            continue
        rets = []
        for i in range(1, len(closes)):
            if closes[i - 1] > 0:
                rets.append(float(closes[i] / closes[i - 1] - 1))
            else:
                rets.append(0.0)
        all_returns.append(rets)
        max_len = max(max_len, len(rets))

    if not all_returns or max_len == 0:
        return []

    # 对齐长度（从尾部对齐），计算每日等权平均
    aligned = np.full((len(all_returns), max_len), np.nan)
    for i, rets in enumerate(all_returns):
        # 从尾部对齐
        offset = max_len - len(rets)
        aligned[i, offset:] = rets

    # 每日等权平均（忽略 NaN）
    with np.errstate(invalid="ignore"):
        daily_avg = np.nanmean(aligned, axis=0)

    # 替换 NaN 为 0
    daily_avg = np.where(np.isnan(daily_avg), 0.0, daily_avg)
    bench = daily_avg.tolist()
    logger.info(
        "[RealDataLoader] 等权基准代理生成 | %d 天 | 累计收益 %.2f%%",
        len(bench), (np.prod([1 + r for r in bench]) - 1) * 100,
    )
    return bench


def compute_benchmark_returns(price_data: dict[str, dict[str, list[float]]]) -> list[float]:
    """计算基准日收益率（P1.3 改进后的统一入口）

    优先级：
        1. 沪深 300 指数真实日收益率（baostock 数据，cache/ohlcv/sh_000300_index.parquet）
        2. 等权日收益率代理（最终降级）

    Args:
        price_data: load_price_data 的输出（仅在降级时使用）

    Returns:
        benchmark_returns: List[float]
    """
    # 优先：沪深 300 真实基准
    hs300_returns = load_benchmark_returns()
    if hs300_returns:
        return hs300_returns

    # 降级：等权代理
    return compute_equal_weight_benchmark(price_data)


def load_fundamentals_from_baostock(symbols: list[str]) -> dict[str, dict[str, float]]:
    """从 baostock 下载的真实财务数据加载（P1.2 改进）

    数据源：cache/fundamentals/{symbol}_latest.json
        包含字段：pe/pb/ps/roe/gross_margin/debt_to_equity/market_cap/revenue
        数据质量：real（来自 baostock query_profit_data + query_balance_data + 估值字段）

    Args:
        symbols: 标的列表

    Returns:
        {symbol: {pe, pb, ps, roe, gross_margin, debt_to_equity, market_cap, revenue, data_quality="real"}}
        缺失的标的不会出现在返回字典中
    """
    if not FUNDAMENTALS_DIR.exists():
        return {}

    fundamentals: dict[str, dict[str, float]] = {}
    for sym in symbols:
        json_path = FUNDAMENTALS_DIR / f"{sym}_latest.json"
        if not json_path.exists():
            continue
        try:
            with open(json_path, encoding="utf-8") as f:
                data = json.load(f)
            # 必须包含至少一个真实财务字段（pe > 0 表示 baostock 返回了真实估值数据）
            if data.get("pe", 0) > 0 or data.get("roe", 0) != 0:
                # 标准化字段（保证与 adapter 期望字段一致）
                fund = {
                    "pe": float(data.get("pe", 0)),
                    "pb": float(data.get("pb", 0)),
                    "ps": float(data.get("ps", 0)),
                    "roe": float(data.get("roe", 0)),
                    "gross_margin": float(data.get("gross_margin", 0)),
                    "debt_to_equity": float(data.get("debt_to_equity", 0)),
                    "market_cap": float(data.get("market_cap", 0)),
                    "revenue": float(data.get("revenue", 0)),
                    "revenue_growth": float(data.get("revenue_growth", 0)),
                    "profit_growth": float(data.get("profit_growth", 0)),
                    "data_quality": "real",
                    "report_year": int(data.get("report_year", 0)),
                    "report_quarter": int(data.get("report_quarter", 0)),
                }
                fundamentals[sym] = fund
        except Exception as e:
            logger.warning("[RealDataLoader] %s baostock 财务加载失败: %s", sym, e)

    logger.info(
        "[RealDataLoader] baostock 真实 fundamentals 加载 | %d/%d 个标的",
        len(fundamentals), len(symbols),
    )
    return fundamentals


def load_fundamentals(price_data: dict[str, dict[str, list[float]]]) -> dict[str, dict[str, float]]:
    """加载基本面数据（P1.2 改进后的多级降级链）

    优先级：
        1. baostock 真实数据（cache/fundamentals/*.json）- 来自 P1.2 下载器
        2. iFinD 真实数据（utils.ifind_client）- 旧实现保留作为备选
        3. 价量代理（fallback）- 最终兜底

    字段名与 vibe_trading_factor_adapter 严格对齐:
        pe / pb / ps / roe / gross_margin / debt_to_equity / market_cap / revenue / data_quality

    Returns:
        {symbol: {pe, pb, ps, roe, gross_margin, debt_to_equity, market_cap, revenue, data_quality}}
    """
    symbols = list(price_data.keys())

    # ============ 优先：baostock 真实数据 ============
    baostock_fund = load_fundamentals_from_baostock(symbols)
    if baostock_fund and len(baostock_fund) >= len(symbols) * 0.5:
        logger.info(
            "[RealDataLoader] 使用 baostock 真实 fundamentals | 真实 %d / 总 %d | 真实率 %.1f%%",
            len(baostock_fund), len(symbols),
            100.0 * len(baostock_fund) / max(len(symbols), 1),
        )
        # 对缺失标的补充代理（保证流水线能跑全部标的）
        if len(baostock_fund) < len(symbols):
            missing_syms = [s for s in symbols if s not in baostock_fund]
            proxy_fund = _build_proxy_fundamentals(price_data, missing_syms)
            for sym, fund in proxy_fund.items():
                baostock_fund[sym] = fund
            logger.info(
                "[RealDataLoader] 补充代理数据 | 代理 %d 个 | 真实 %d 个",
                len(proxy_fund), len(baostock_fund) - len(proxy_fund),
            )
        return baostock_fund

    if baostock_fund:
        logger.warning(
            "[RealDataLoader] baostock 真实率过低 (%d/%d), 降级到 iFinD/代理",
            len(baostock_fund), len(symbols),
        )

    # ============ 第二级：iFinD 真实数据（旧实现保留）============
    try:
        from utils.ifind_client import IFindClient
        client = IFindClient()
        # symbol 格式转换: 000333_SZ -> 000333.SZ
        ifind_symbols = [sym.replace("_", ".") for sym in symbols]
        logger.info("[RealDataLoader] 尝试 iFinD 真实 fundamentals | 标的数=%d", len(ifind_symbols))
        real_data = client.get_fundamentals_batch(ifind_symbols)
        if real_data:
            # 转换 key 回 _SZ/_SH 格式
            fundamentals: dict[str, dict[str, float]] = {}
            for sym, fund in real_data.items():
                # iFinD 返回 600519.SH, 转回 600519_SH
                local_sym = sym.replace(".", "_")
                if local_sym in price_data:
                    fund["data_quality"] = "real"
                    fundamentals[local_sym] = fund
            if len(fundamentals) >= len(symbols) * 0.5:
                logger.info(
                    "[RealDataLoader] iFinD 真实 fundamentals 加载成功 | %d/%d",
                    len(fundamentals), len(symbols),
                )
                return fundamentals
            else:
                logger.warning(
                    "[RealDataLoader] iFinD 拉取成功率过低 (%d/%d), 降级到代理",
                    len(fundamentals), len(symbols),
                )
        else:
            logger.warning("[RealDataLoader] iFinD 返回空 (可能配额超限), 降级到代理")
    except Exception as e:
        logger.warning("[RealDataLoader] iFinD fundamentals 加载异常, 降级到代理: %s", e)

    # ============ 最终降级：价量代理 ============
    return _build_proxy_fundamentals(price_data, symbols)


def _build_proxy_fundamentals(
    price_data: dict[str, dict[str, list[float]]],
    symbols: list[str],
) -> dict[str, dict[str, float]]:
    """价量代理 fundamentals（最终降级方案，避免与 close 完全共线）

    代理算法（P1.2b 改进）:
        - pe           = 1 / (close * avg_vol_20)        # 成交额倒数（PE 思路）
        - pb           = 1 / (close * avg_vol_60)        # 60日版本（与 pe 时间窗不同）
        - ps           = 1 / (close * volume_last)        # 当日成交额倒数
        - roe          = (ma5 - ma20) / ma20              # 短期相对中期动量
        - gross_margin = (avg_high_20 - avg_low_20) / close  # 日内波幅代理
        - debt_to_equity = avg_vol_60 / avg_vol_20         # 长短期成交活跃度比
        - market_cap   = close * avg_vol_20                # 流通市值代理
        - revenue      = close * volume_last               # 当日成交额（"营收"代理）
    """
    fundamentals = {}
    for sym in symbols:
        data = price_data.get(sym)
        if not data:
            continue
        closes = data.get("closes", [])
        vols = data.get("volumes", [])
        highs = data.get("highs", [])
        lows = data.get("lows", [])
        if not closes or not vols:
            continue

        last_close = float(closes[-1])
        avg_vol_20 = float(np.mean(vols[-20:])) if len(vols) >= 20 else float(np.mean(vols))
        avg_vol_60 = float(np.mean(vols[-60:])) if len(vols) >= 60 else float(np.mean(vols))
        last_vol = float(vols[-1]) if vols else 1.0

        # 均线
        ma5 = float(np.mean(closes[-5:])) if len(closes) >= 5 else last_close
        ma20 = float(np.mean(closes[-20:])) if len(closes) >= 20 else last_close

        # 日内波幅（毛利率代理）
        avg_high_20 = float(np.mean(highs[-20:])) if len(highs) >= 20 else last_close
        avg_low_20 = float(np.mean(lows[-20:])) if len(lows) >= 20 else last_close

        # 改进代理（避免与 close 完全共线）
        turnover_20 = last_close * avg_vol_20  # 20日平均成交额
        turnover_60 = last_close * avg_vol_60
        turnover_last = last_close * last_vol

        fundamentals[sym] = {
            # 估值类（成交额倒数 - 与 close 不完全共线, 含 volume 维度）
            "pe": float(1.0 / turnover_20) if turnover_20 > 0 else 0.0,
            "pb": float(1.0 / turnover_60) if turnover_60 > 0 else 0.0,
            "ps": float(1.0 / turnover_last) if turnover_last > 0 else 0.0,
            # 盈利能力（动量代理 - 与 close 完全不共线）
            "roe": float((ma5 - ma20) / ma20) if ma20 > 0 else 0.0,
            "gross_margin": float((avg_high_20 - avg_low_20) / last_close) if last_close > 0 else 0.0,
            # 杠杆（长短期成交活跃度比 - 与 close 完全不共线）
            "debt_to_equity": float(avg_vol_60 / avg_vol_20) if avg_vol_20 > 0 else 0.0,
            # 规模（保留原有 market_cap 逻辑）
            "market_cap": turnover_20,
            "revenue": turnover_last,
            # 数据质量标记
            "data_quality": "proxy",
        }

    logger.info(
        "[RealDataLoader] fundamentals 代理生成（改进版, 避免共线）| %d 个标的",
        len(fundamentals),
    )
    return fundamentals


def load_all_for_pipeline(
    symbols: list[str] = None,
) -> tuple[dict[str, dict[str, list[float]]], dict[str, dict[str, float]], list[float]]:
    """一次性加载流水线所需的全部输入

    Returns:
        (price_data, fundamentals, benchmark_returns)
    """
    price_data = load_price_data(symbols=symbols)
    fundamentals = load_fundamentals(price_data)
    benchmark_returns = compute_benchmark_returns(price_data)
    return price_data, fundamentals, benchmark_returns


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s | %(message)s")
    logger.info("=" * 70)
    logger.info("真实 A 股历史数据加载器自检")
    logger.info("=" * 70)

    symbols = list_available_symbols()
    logger.info(f"\n可用标的 ({len(symbols)} 个): {symbols}")

    price_data, fundamentals, bench = load_all_for_pipeline()
    logger.info(f"\nprice_data: {len(price_data)} 个标的")
    sample_sym = list(price_data.keys())[0]
    logger.info(f"  样例 {sample_sym}: closes 长度 {len(price_data[sample_sym]['closes'])}")
    logger.info(f"  首 5 日 close: {price_data[sample_sym]['closes'][:5]}")

    logger.info(f"\nbenchmark_returns: 长度 {len(bench)}")
    logger.info(f"  首 5 日: {bench[:5]}")
    logger.info(f"  末 5 日: {bench[-5:]}")
    logger.info(f"  累计收益: {(np.prod([1+r for r in bench]) - 1) * 100:.2f}%")

    logger.info(f"\nfundamentals: {len(fundamentals)} 个标的")
    sample = list(fundamentals.values())[0]
    logger.info(f"  样例字段: {sample}")
