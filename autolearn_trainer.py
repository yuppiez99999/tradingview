"""AutoLearn Trainer - 核心模块 (B3.5重建)

本模块提供自动训练器的核心组件:
  - POSITION_SYMBOLS: 持仓标的信息清单
  - add_technical_features: 技术因子工程 (30+因子, 无前视偏差)
  - add_cross_sectional_features: 截面相对强弱因子
  - load_returns_history: 从Wind MCP加载历史收益率
  - synthesize_ohlcv_from_returns: 从收益率合成OHLCV

外部依赖:
  - tools.wind_mcp_fetcher: Wind MCP数据获取
  - config/positions.json: 持仓配置

历史: 原始文件丢失于v8.5重构。2026-08-02从lgb_trainer/trainer.py、
lgb_tscv_trainer.py、lgb_enhanced_trainer.py三方的导入需求中完整重建。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger("autolearn")


# ============================================================
# post_train_callback 钩子 (ER-1.1, Wave 7-ERL Sprint 1)
# ============================================================
def invoke_post_train_callback(
    callback: Callable[[dict[str, Any]], None] | None,
    result: dict[str, Any],
    *,
    logger_name: str = "autolearn",
) -> None:
    """fail-safe 调用 post_train_callback 钩子。

    训练完成后调用，回调异常不阻断训练主流程（优雅降级）。
    用于 ER-1.1 训练→再平衡联动：训练完成 → 回调 → EvolutionOrchestratorV2.run_cycle() → run_daily_rebalance()。

    Args:
        callback: 训练后回调函数，接收训练结果字典；None 时直接返回（向后兼容）
        result: 训练结果字典 (含 status/total/trained/skipped/failed/results/signals)
        logger_name: 日志器名称

    设计原则:
        - 向后兼容: callback=None 时训练行为不变
        - fail-safe: 回调异常仅 logger.warning，不阻断训练主流程
        - 乘子约束: 回调产出的 weight_adjustments 仍限 [0.5, 2.0] (由调用方保证)
    """
    if callback is None:
        return
    cb_logger = logging.getLogger(logger_name)
    try:
        callback(result)
        cb_logger.info("post_train_callback 执行成功")
    except Exception as e:  # noqa: BLE001  # fail-safe: 回调异常不阻断训练
        cb_logger.warning(f"post_train_callback 失败 (fail-safe 降级): {e}")


# ============================================================
# 路径
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "data" / "downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# POSITION_SYMBOLS — 从 positions.json 动态加载
# ============================================================
def _load_position_symbols() -> list[tuple]:
    """从 config/positions.json 加载持仓标的, 格式:
    [(code, name, shares, style, sector), ...]
    """
    pos_file = BASE_DIR / "config" / "positions.json"
    if not pos_file.exists():
        logger.error(f"持仓文件不存在: {pos_file}")
        return []

    data = json.loads(pos_file.read_text(encoding="utf-8"))
    positions = data.get("positions", {})
    symbols = []
    for code, info in sorted(positions.items()):
        if not isinstance(info, dict):
            continue
        shares = (
            info.get("shares", 0)
            or info.get("total_shares", 0)
            or info.get("phase1_shares", 0)
            or 0
        )
        style = info.get("style", "") or info.get("type", "") or "其他"
        sector = info.get("sector", "") or style or "其他"
        symbols.append(
            (
                code,
                info.get("name", ""),
                shares,
                style,
                sector,
            )
        )

    logger.info(f"POSITION_SYMBOLS: 加载 {len(symbols)} 个持仓标的")
    return symbols


POSITION_SYMBOLS = _load_position_symbols()


# ============================================================
# 行业映射 (从 POSITION_SYMBOLS 构建)
# ============================================================
def _build_sector_map() -> dict[str, str]:
    """构建 code→sector 映射."""
    return {s[0]: s[4] for s in POSITION_SYMBOLS}


# ============================================================
# 辅助函数: 技术指标计算
# ============================================================
def _rsi(series: pd.Series, period: int) -> pd.Series:
    """RSI (Relative Strength Index) — 无前视偏差."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(span=period, adjust=False).mean()
    avg_loss = loss.ewm(span=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    return 100.0 - (100.0 / (1.0 + rs))


def _williams_r(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int
) -> pd.Series:
    """Williams %R — 无前视偏差."""
    highest = high.rolling(period).max()
    lowest = low.rolling(period).min()
    denom = highest - lowest
    denom = denom.replace(0, 1e-10)
    return -100.0 * (highest - close) / denom


def _true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """True Range."""
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


# ============================================================
# add_technical_features — 技术因子工程 (35+因子)
# ============================================================
def add_technical_features(df: pd.DataFrame) -> pd.DataFrame:
    """为 OHLCV DataFrame 添加 35+ 技术因子。

    Args:
        df: DataFrame, 必须包含列: open, high, low, close, volume

    Returns:
        DataFrame, 在原列基础上追加特征列

    严格无前视偏差: 所有滚动窗口/移动平均仅用历史数据,
    不含当期收盘价 (若当期用于决策, 则信号需滞后一期使用)。

    NaN 值保留 (训练时由 trainer 的 nan_to_num 处理),
    避免填充默认值掩盖数据缺失信号。
    """
    df = df.copy()

    # 列名校验
    required = ["open", "high", "low", "close", "volume"]
    for col in required:
        if col not in df.columns:
            logger.warning(f"add_technical_features: 缺少列 {col}, 跳过")
            return df

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    _open = df["open"].astype(float)
    volume = df["volume"].astype(float)

    # ---- 收益率 (Return) ----
    df["ret_1d"] = close.pct_change(1)
    df["ret_5d"] = close.pct_change(5)
    df["ret_10d"] = close.pct_change(10)
    df["ret_20d"] = close.pct_change(20)
    df["ret_60d"] = close.pct_change(60)
    # 对数收益率 (更适合建模)
    df["log_ret_1d"] = np.log(close / close.shift(1))
    df["log_ret_5d"] = np.log(close / close.shift(5))

    # ---- 均线系统 (Trend) ----
    for w in [5, 10, 20, 60]:
        df[f"ma_{w}"] = close.rolling(w).mean()
    df["ma_ratio_5_20"] = df["ma_5"] / df["ma_20"].replace(0, np.nan)
    df["ma_ratio_10_60"] = df["ma_10"] / df["ma_60"].replace(0, np.nan)
    df["price_to_ma20"] = close / df["ma_20"].replace(0, np.nan)
    df["price_to_ma60"] = close / df["ma_60"].replace(0, np.nan)
    # 均线排列 (金叉/死叉信号)
    df["ma_5_10_cross"] = (df["ma_5"] - df["ma_10"]) / df["ma_10"].replace(0, np.nan)

    # ---- 波动率 (Volatility) ----
    for w in [5, 10, 20, 60]:
        df[f"volatility_{w}d"] = df["ret_1d"].rolling(w).std()
    df["vol_ratio_5_20"] = df["volatility_5d"] / df["volatility_20d"].replace(0, np.nan)

    # ---- 动量 (Momentum) ----
    df["rsi_6"] = _rsi(close, 6)
    df["rsi_14"] = _rsi(close, 14)
    df["rsi_28"] = _rsi(close, 28)
    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]
    df["macd_hist_pct"] = df["macd_hist"] / close.replace(0, np.nan)

    # ---- 布林带 (Bollinger Bands) ----
    df["bb_mid"] = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    df["bb_upper"] = df["bb_mid"] + 2 * bb_std
    df["bb_lower"] = df["bb_mid"] - 2 * bb_std
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"].replace(0, np.nan)
    df["bb_position"] = (close - df["bb_lower"]) / (
        df["bb_upper"] - df["bb_lower"]
    ).replace(0, np.nan)

    # ---- 成交量 (Volume) ----
    df["volume_ma_5"] = volume.rolling(5).mean()
    df["volume_ma_20"] = volume.rolling(20).mean()
    df["volume_ratio"] = volume / df["volume_ma_20"].replace(0, np.nan)
    df["volume_trend_5_20"] = df["volume_ma_5"] / df["volume_ma_20"].replace(0, np.nan)
    # OBV简化: 成交量方向累计
    df["volume_direction"] = volume * np.sign(df["ret_1d"]).fillna(0)
    df["obv_5d"] = df["volume_direction"].rolling(5).sum()

    # ---- 价格-成交量关系 ----
    # VWAP (5日量价均)
    df["vwap_5"] = (close * volume).rolling(5).sum() / volume.rolling(5).sum().replace(
        0, np.nan
    )

    # ---- 日内波动 ----
    df["hl_ratio"] = (high - low) / close.replace(0, np.nan)
    df["hl_ratio_ma5"] = df["hl_ratio"].rolling(5).mean()
    df["oc_ratio"] = (close - _open) / _open.replace(0, np.nan)

    # ---- ATR (Average True Range) ----
    tr = _true_range(high, low, close)
    df["atr_14"] = tr.rolling(14).mean()
    df["atr_pct"] = df["atr_14"] / close.replace(0, np.nan)

    # ---- Williams %R & Stochastic ----
    df["willr_14"] = _williams_r(high, low, close, 14)
    low_14 = low.rolling(14).min()
    high_14 = high.rolling(14).max()
    stoch_denom = high_14 - low_14
    stoch_denom = stoch_denom.replace(0, np.nan)
    df["stoch_k"] = 100.0 * (close - low_14) / stoch_denom
    df["stoch_d"] = df["stoch_k"].rolling(3).mean()

    # ---- ROC (Rate of Change) ----
    df["roc_10"] = close.pct_change(10) * 100
    df["roc_20"] = close.pct_change(20) * 100

    # ---- KDJ ----
    df["kdj_j"] = 3 * df["stoch_k"] - 2 * df["stoch_d"]

    # ---- CMO (Chande Momentum Oscillator) ----
    delta = close.diff()
    up_sum = delta.clip(lower=0).rolling(14).sum()
    down_sum = (-delta.clip(upper=0)).rolling(14).sum()
    total = up_sum + down_sum
    df["cmo_14"] = 100.0 * (up_sum - down_sum) / total.replace(0, np.nan)

    # ---- 筹码分布替代指标 ----
    # 高低价差与波动率的关系
    df["price_range_20d"] = (high.rolling(20).max() - low.rolling(20).min()) / close

    # 清理无穷值
    df = df.replace([np.inf, -np.inf], np.nan)
    return df


# ============================================================
# add_cross_sectional_features — 截面相对强弱因子
# ============================================================
def add_cross_sectional_features(
    featured_dict: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    """添加截面相对强弱因子。

    对每个特征, 逐日期计算:
      - sector_rank: 同行业内的百分位排名 (当日)
      - market_rank: 全市场百分位排名 (当日)
      - sector_excess: 行业相对强度 (当日超额)

    Args:
        featured_dict: {symbol: DataFrame} (已包含技术因子)

    Returns:
        {symbol: DataFrame} (追加截面因子)

    无前视偏差说明 (2026-09-12 修复):
        原实现只取每只标的**最后一行**的因子值做截面排名, 再把这一个标量
        广播写入整列历史 — 训练样本的每个历史日期都携带"今天的截面信息"
        (前视偏差), 且常数列会挤占 top_n_features 名额。现改为把各标的
        因子序列按日期对齐成 wide 矩阵, **逐日期**计算截面排名/超额,
        每个日期只用当日截面, 行为与实盘一致。
    """
    if not featured_dict:
        return featured_dict

    sector_map = _build_sector_map()
    all_symbols = list(featured_dict.keys())

    # --- 按行业分组 + 各标的同业名单 ---
    sector_groups: dict[str, list[str]] = {}
    for sym in featured_dict:
        sec = sector_map.get(sym, "其他")
        sector_groups.setdefault(sec, []).append(sym)
    peers_of: dict[str, list[str]] = {
        sym: [s for s in sector_groups.get(sector_map.get(sym, "其他"), []) if s != sym]
        for sym in all_symbols
    }

    # --- 逐日期截面计算 ---
    for col in ["ret_5d", "ret_20d", "rsi_14"]:
        series_per_sym = {
            sym: df[col]
            for sym, df in featured_dict.items()
            if col in df.columns and len(df) > 0
        }
        if len(series_per_sym) < 2:
            continue
        # wide: index=日期, columns=symbol (按日期对齐, 缺失为 NaN)
        wide = pd.DataFrame(series_per_sym)
        if wide.empty:
            continue

        for sym in list(series_per_sym):
            df = featured_dict[sym]
            my = wide[sym]

            # 行业截面: 同业中位 (逐日期) + 超额 + 排名 (排除自身, 与原口径一致)
            peers = [p for p in peers_of.get(sym, []) if p in wide.columns]
            if peers:
                peer_matrix = wide[peers]
                peer_valid = peer_matrix.notna().sum(axis=1)
                peer_median = peer_matrix.median(axis=1)
                peer_less = (peer_matrix.lt(my, axis=0)).sum(axis=1)
                sector_rank = peer_less / peer_valid.where(peer_valid > 0)
                # 原口径: 有效同业 < 2 个时不生成该日截面值
                sector_rank = sector_rank.where(peer_valid >= 2)
                sector_excess = (my - peer_median).where(peer_valid >= 2)

                df[f"{col}_sector_rank"] = sector_rank.reindex(df.index)
                df[f"{col}_sector_excess"] = sector_excess.reindex(df.index)

            # 全市场截面: 其余全部标的 (排除自身)
            others = [s for s in wide.columns if s != sym]
            if others:
                other_matrix = wide[others]
                other_valid = other_matrix.notna().sum(axis=1)
                other_less = (other_matrix.lt(my, axis=0)).sum(axis=1)
                market_rank = other_less / other_valid.where(other_valid > 0)
                # 原口径: 有效样本 < 5 个时不生成该日截面值
                market_rank = market_rank.where(other_valid >= 5)
                df[f"{col}_market_rank"] = market_rank.reindex(df.index)

    return featured_dict


# ============================================================
# load_returns_history — 加载历史收益率 (lgb_tscv_trainer 用)
# ============================================================
def load_returns_history(lookback_years: int = 2) -> pd.DataFrame:
    """从 Wind MCP 加载历史日收益率。

    Args:
        lookback_years: 回看年数

    Returns:
        DataFrame, index=日期, columns=标的纯代码(不含后缀)
    """
    logger.info(f"从 Wind MCP 加载 {lookback_years} 年历史收益率...")

    ret_dict: dict[str, pd.Series] = {}
    failed = 0

    for code, _name, *__ in POSITION_SYMBOLS:
        clean = code.replace(".SZ", "").replace(".SH", "").replace(".BJ", "")
        try:
            from tools.wind_mcp_fetcher import wind_get_kline

            klines = wind_get_kline(code, days=252 * lookback_years)
            if not klines:
                logger.warning(f"  {code}: 无K线")
                failed += 1
                continue

            df = pd.DataFrame(klines)
            df["trade_date"] = pd.to_datetime(df["TIME"])
            df["close"] = pd.to_numeric(df["MATCH"], errors="coerce")
            df = df.set_index("trade_date").sort_index()
            df["return"] = df["close"].pct_change()
            ret_dict[clean] = df["return"].dropna()
        except ImportError:
            logger.error("  Wind MCP 不可用, 回退为空")
            break
        except Exception as e:
            logger.warning(f"  {code}: {e}")
            failed += 1

    if not ret_dict:
        raise RuntimeError(
            f"无可用历史数据 (共 {len(POSITION_SYMBOLS)} 标的, "
            f"全部失败)。请检查 Wind MCP 连接。"
        )

    returns_df = pd.DataFrame(ret_dict).sort_index()
    logger.info(
        f"  历史数据: {returns_df.shape[0]} 日 × "
        f"{returns_df.shape[1]} 标的 (失败 {failed})"
    )
    return returns_df


# ============================================================
# synthesize_ohlcv_from_returns — 合成OHLCV (lgb_tscv_trainer 用)
# ============================================================
def synthesize_ohlcv_from_returns(returns_df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """从日收益率合成 OHLCV 数据。

    合成规则:
      - close: 从累计收益率复利计算 (起始价=100)
      - open: 前一日 close
      - high: max(open, close) * (1 + 0~1% 噪声)
      - low:  min(open, close) * (1 - 0~1% 噪声)
      - volume: 基于波动率的模拟成交量

    P0-M4 (2026-09-13): 合成帧携带 ``attrs["synthetic"]=True`` 标记 —
    训练链路消费方据此拒绝在伪造 OHLCV 上训练 (除非显式授权合成)。
    本函数只应用于冒烟/管线验证, 不用于产出真实模型。

    Args:
        returns_df: DataFrame, index=日期, columns=纯代码

    Returns:
        {code: DataFrame[open, high, low, close, volume]}
    """
    logger.info(f"合成 OHLCV: {returns_df.shape[0]} 日 × {returns_df.shape[1]} 标的")

    ohlcv_dict: dict[str, pd.DataFrame] = {}
    # 复利累计 → 价格序列 (起始价 100)
    price_df = (1 + returns_df.fillna(0)).cumprod() * 100.0

    rng = np.random.RandomState(42)  # 固定种子, 可复现

    for symbol in returns_df.columns:
        if symbol not in price_df.columns:
            continue

        df = pd.DataFrame(index=price_df.index)
        df["close"] = price_df[symbol].astype(float)
        df["open"] = df["close"].shift(1).fillna(df["close"])
        # 日内波动量: 基于日收益率的绝对值
        noise = rng.uniform(0.001, 0.01, size=len(df))
        df["high"] = df[["open", "close"]].max(axis=1) * (1 + noise)
        noise2 = rng.uniform(0.001, 0.01, size=len(df))
        df["low"] = df[["open", "close"]].min(axis=1) * (1 - noise2)
        # 模拟成交量: 与波动率正相关
        vol = returns_df[symbol].rolling(20).std().fillna(0.02)
        df["volume"] = (1_000_000 * (1 + 5 * np.abs(vol))).astype(float)
        df = df.dropna()
        if len(df) >= 50:
            df.attrs["synthetic"] = True  # P0-M4: 合成数据标记 (消费方校验)
            df.attrs["data_source"] = "synthetic_from_returns"
            ohlcv_dict[symbol] = df

    logger.info(f"  合成完成: {len(ohlcv_dict)} 标的")
    return ohlcv_dict


def load_ohlcv_history(lookback_years: int = 2) -> dict[str, pd.DataFrame]:
    """从 Wind MCP 加载**真实**历史日 K 线 (P0-M4, 2026-09-13)。

    背景: 此前 lgb_tscv_trainer 用 ``load_returns_history`` 取真实收盘价算
    收益率, 再经 ``synthesize_ohlcv_from_returns`` 用随机噪声伪造 OHLC —
    真实 K 线在取数点被丢弃, ATR/振幅类因子建立在伪造数据上。

    列名兼容: 服务端列名大小写/别名不确定, 按候选名宽松映射
    (open/OPEN, high/HIGH, low/LOW, close/CLOSE/MATCH, volume/VOLUME)。
    任一 OHLCV 列缺失的标的不进入返回集 (由调用方决定跳过或授权合成)。

    Returns:
        {纯代码: DataFrame[open, high, low, close, volume], attrs["data_source"]="wind_real"}
    """
    logger.info(f"从 Wind MCP 加载 {lookback_years} 年真实历史 K 线...")

    _CANDIDATES = {
        "open": ("open", "OPEN"),
        "high": ("high", "HIGH"),
        "low": ("low", "LOW"),
        "close": ("close", "CLOSE", "MATCH"),
        "volume": ("volume", "VOLUME"),
    }
    ohlcv_dict: dict[str, pd.DataFrame] = {}
    failed = 0

    for code, _name, *__ in POSITION_SYMBOLS:
        clean = code.replace(".SZ", "").replace(".SH", "").replace(".BJ", "")
        try:
            from tools.wind_mcp_fetcher import wind_get_kline

            klines = wind_get_kline(code, days=252 * lookback_years)
            if not klines:
                logger.warning(f"  {code}: 无K线")
                failed += 1
                continue

            df = pd.DataFrame(klines)
            df["trade_date"] = pd.to_datetime(df["TIME"])
            df = df.set_index("trade_date").sort_index()

            mapped: dict[str, pd.Series] = {}
            missing = []
            for std_name, candidates in _CANDIDATES.items():
                col = next((c for c in candidates if c in df.columns), None)
                if col is None:
                    missing.append(std_name)
                else:
                    mapped[std_name] = pd.to_numeric(df[col], errors="coerce")
            if missing:
                logger.warning(f"  {code}: K线缺列 {missing}, 不纳入真实 OHLCV 集")
                failed += 1
                continue

            out = pd.DataFrame(mapped, index=df.index).dropna()
            if len(out) < 50:
                logger.warning(f"  {code}: 有效 K 线不足 50 根 ({len(out)})")
                failed += 1
                continue
            out.attrs["synthetic"] = False
            out.attrs["data_source"] = "wind_real"
            ohlcv_dict[clean] = out
        except ImportError:
            logger.error("  Wind MCP 不可用, 回退为空")
            break
        except Exception as e:
            logger.warning(f"  {code}: {e}")
            failed += 1

    logger.info(
        f"  真实 OHLCV: {len(ohlcv_dict)} 标的 (失败 {failed})"
    )
    return ohlcv_dict
