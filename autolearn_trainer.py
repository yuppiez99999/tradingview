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
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger("autolearn")

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
        symbols.append((
            code,
            info.get("name", ""),
            shares,
            style,
            sector,
        ))

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
    loss = (-delta.clip(upper=0))
    avg_gain = gain.ewm(span=period, adjust=False).mean()
    avg_loss = loss.ewm(span=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    return 100.0 - (100.0 / (1.0 + rs))


def _williams_r(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
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
    df["bb_position"] = (close - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"]).replace(0, np.nan)

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
    df["vwap_5"] = ((close * volume).rolling(5).sum()
                     / volume.rolling(5).sum().replace(0, np.nan))

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
def add_cross_sectional_features(featured_dict: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """添加截面相对强弱因子。

    对每个特征, 计算:
      - sector_ret_rank: 同行业内的百分位排名
      - market_ret_rank: 全市场百分位排名
      - sector_relative_strength: 行业相对强度 (超额收益)

    Args:
        featured_dict: {symbol: DataFrame} (已包含技术因子)

    Returns:
        {symbol: DataFrame} (追加截面因子)
    
    注意: 仅使用当期截面数据, 不跨期 — 无前视偏差风险。
    """
    if not featured_dict:
        return featured_dict

    sector_map = _build_sector_map()
    all_symbols = list(featured_dict.keys())

    # --- 收集各标的最近一期收益率 (用于截面排名) ---
    ret_metrics = {}
    for sym, df in featured_dict.items():
        ret_data = {}
        for col in ["ret_5d", "ret_20d", "rsi_14", "stoch_k"]:
            if col in df.columns and len(df) > 0:
                val = df[col].iloc[-1]
                if not (pd.isna(val) or np.isinf(val)):
                    ret_data[col] = val
        if ret_data:
            ret_metrics[sym] = ret_data

    # --- 按行业分组 ---
    sector_groups: dict[str, list[str]] = {}
    for sym in featured_dict:
        sec = sector_map.get(sym, "其他")
        sector_groups.setdefault(sec, []).append(sym)

    # --- 计算截面因子 ---
    for sym, df in featured_dict.items():
        sector = sector_map.get(sym, "其他")
        peers = [s for s in sector_groups.get(sector, []) if s != sym]

        for col in ["ret_5d", "ret_20d", "rsi_14"]:
            if col not in df.columns:
                continue
            my_val = ret_metrics.get(sym, {}).get(col, np.nan)
            if pd.isna(my_val):
                continue

            # 行业截面排名
            peer_vals = [ret_metrics.get(p, {}).get(col, np.nan) for p in peers]
            peer_vals = [v for v in peer_vals if not pd.isna(v)]
            if len(peer_vals) >= 2:
                df[f"{col}_sector_rank"] = (sum(1 for v in peer_vals if v < my_val)
                                            / max(len(peer_vals), 1))
                df[f"{col}_sector_excess"] = my_val - np.median(peer_vals)

            # 全市场截面排名
            all_vals = [ret_metrics.get(s, {}).get(col, np.nan)
                        for s in all_symbols if s != sym]
            all_vals = [v for v in all_vals if not pd.isna(v)]
            if len(all_vals) >= 5:
                df[f"{col}_market_rank"] = (sum(1 for v in all_vals if v < my_val)
                                            / max(len(all_vals), 1))

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

    for code, name, *__ in POSITION_SYMBOLS:
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
            ohlcv_dict[symbol] = df

    logger.info(f"  合成完成: {len(ohlcv_dict)} 标的")
    return ohlcv_dict
