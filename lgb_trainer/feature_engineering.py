"""扩展特征工程 (B3.5: 从 lgb_enhanced_trainer.py 抽取)

本模块集中以下职责:
  - add_mean_reversion_features: 均值回归特征 (9个, V6)
  - add_regime_aware_features: Regime-Aware 特征 (8个, V7)
  - add_industry_relative_strength_features: 行业相对强度 (5个)
  - add_capital_flow_features: 资金流向 (5个)
  - add_cross_market_features: 跨市场信号 (6个)

设计原则:
  - 所有特征均无前视偏差 (仅用历史数据)
  - 缺失数据时填中性值, 避免训练失败
  - 复用 POSITION_SYMBOLS 的行业映射 (来自 autolearn_trainer)
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger("lgb_enhanced")


# ============================================================
# 行业映射 (从 autolearn_trainer 复用)
# ============================================================
def _build_sector_map() -> dict[str, str]:
    """构建 code→sector 映射 (从 POSITION_SYMBOLS)。

    POSITION_SYMBOLS 由外部注入, 避免循环导入。
    """
    try:
        from autolearn_trainer import POSITION_SYMBOLS
    except ImportError:
        logger.warning("autolearn_trainer 不可用, 行业映射为空")
        return {}
    return {code: sector for code, _, _, _, sector in POSITION_SYMBOLS}


# 大盘代理符号 (与 institutional_pipeline_runner._MARKET_PROXY_SYMBOL 一致)
_REGIME_PROXY_SYMBOL = "510300"


# 跨市场代理标的 (用持仓 ETF/股票作为跨市场信号)
_CROSS_MARKET_PROXIES: dict[str, str] = {
    "gold_safe_haven": "518880",  # 黄金ETF华安 - 避险情绪代理
    "bank_rate_proxy": "600036",  # 招商银行 - 利率/信贷代理
    "tech_growth_proxy": "588000",  # 科创50ETF - 成长风格代理
    "dividend_defensive": "515180",  # 易方达红利ETF - 防御风格代理
}

# 跨市场特征列名 → 代理键 映射 (单一来源, 避免散落 if/else)
_CROSS_MARKET_TREND_COLS: tuple[tuple[str, str], ...] = (
    ("gold_trend_20", "gold_safe_haven"),
    ("bank_trend_20", "bank_rate_proxy"),
    ("tech_style_20", "tech_growth_proxy"),
    ("defensive_style_20", "dividend_defensive"),
)


# ============================================================
# 均值回归特征 (V6)
# ============================================================
def add_mean_reversion_features(
    ohlcv_dict: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    """添加均值回归特征 (V6: 提升震荡市Alpha信号质量)

    动机:
        Window 1 (2023-07~2024-09) 年化仅2.47%, Sharpe 0.28, 是WF Sharpe CV=0.67的主因。
        诊断: LGB模型在bear regime下IC失效, 原因是特征全部为趋势跟踪特征,
              在震荡市中动量信号反向, 导致亏损。
        方案: 添加均值回归特征, 捕捉超买超卖后的价格回归机会。

    新增特征 (9个):
        - rsi_oversold: RSI12<30 超卖信号 (1/0)
        - rsi_overbought: RSI12>70 超买信号 (1/0)
        - price_zscore_20: 价格偏离20日均值的Z-score (正值=高估, 负值=低估)
        - reversal_5d: 5日反转因子 (-return_5), 用于捕捉短期超涨反转
        - reversal_10d: 10日反转因子 (-return_10)
        - boll_oversold: 布林带下轨超卖 (boll_pct<0.2)
        - boll_overbought: 布林带上轨超买 (boll_pct>0.8)
        - volume_surge: 成交量异常放大 (>1.5x 20日均量)
        - vol_compression: 波动率压缩 (20日波动率低于60日20分位)

    Args:
        ohlcv_dict: {code: DataFrame[含技术因子]}

    Returns:
        合并后的字典, 每个 DataFrame 新增 9 个均值回归特征
    """
    out: dict[str, pd.DataFrame] = {}
    for code, df in ohlcv_dict.items():
        new_df = df.copy()
        close = new_df["close"]
        volume = new_df["volume"]

        # === RSI12 极端值信号 (前提: add_technical_features 已生成 rsi12) ===
        if "rsi12" in new_df.columns:
            new_df["rsi_oversold"] = (new_df["rsi12"] < 30).astype(int)
            new_df["rsi_overbought"] = (new_df["rsi12"] > 70).astype(int)
        else:
            # 兜底: 自行计算 RSI12
            delta = close.diff()
            gain = delta.clip(lower=0).rolling(12).mean()
            loss = -delta.clip(upper=0).rolling(12).mean()
            rs = gain / (loss + 1e-9)
            rsi12 = 100 - 100 / (1 + rs)
            new_df["rsi_oversold"] = (rsi12 < 30).astype(int)
            new_df["rsi_overbought"] = (rsi12 > 70).astype(int)

        # === 价格 Z-score (偏离20日均值的标准化距离) ===
        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        new_df["price_zscore_20"] = (close - ma20) / (std20 + 1e-9)

        # === 短期反转因子 (取反收益率, 捕捉均值回归) ===
        if "return_5" in new_df.columns:
            new_df["reversal_5d"] = -new_df["return_5"]
        else:
            new_df["reversal_5d"] = -close.pct_change(5)
        if "return_10" in new_df.columns:
            new_df["reversal_10d"] = -new_df["return_10"]
        else:
            new_df["reversal_10d"] = -close.pct_change(10)

        # === 布林带极端位置信号 ===
        if "boll_pct" in new_df.columns:
            new_df["boll_oversold"] = (new_df["boll_pct"] < 0.2).astype(int)
            new_df["boll_overbought"] = (new_df["boll_pct"] > 0.8).astype(int)
        else:
            # 兜底: 自行计算布林带位置
            boll_lower = ma20 - 2 * std20
            boll_upper = ma20 + 2 * std20
            boll_pct = (close - boll_lower) / (boll_upper - boll_lower + 1e-9)
            new_df["boll_oversold"] = (boll_pct < 0.2).astype(int)
            new_df["boll_overbought"] = (boll_pct > 0.8).astype(int)

        # === 成交量异常放大 (量价背离信号) ===
        vol_ma20 = volume.rolling(20).mean()
        new_df["volume_surge"] = (volume > vol_ma20 * 1.5).astype(int)

        # === 波动率压缩 (低波动后往往有突破, 震荡市关键信号) ===
        if "volatility_20" in new_df.columns:
            vol20 = new_df["volatility_20"]
        else:
            vol20 = close.pct_change().rolling(20).std()
        vol20_q20 = vol20.rolling(60).quantile(0.2)
        new_df["vol_compression"] = (vol20 < vol20_q20).astype(int)

        # 填充 NaN
        for col in [
            "rsi_oversold",
            "rsi_overbought",
            "price_zscore_20",
            "reversal_5d",
            "reversal_10d",
            "boll_oversold",
            "boll_overbought",
            "volume_surge",
            "vol_compression",
        ]:
            new_df[col] = new_df[col].fillna(0)

        out[code] = new_df
    return out


# ============================================================
# Regime-Aware 特征 (V7)
# ============================================================
def add_regime_aware_features(
    ohlcv_dict: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    """添加 Regime-Aware 特征 (V7: 解决 bull regime 下 Alpha 信号失效)

    动机:
        V6.2 基线 WF Sharpe CV=0.55 (未达<0.5), 根因是 Window 1 bull regime 平均 -1.92%。
        2024-06-03 (bull regime): 688017 权重10%但跌34.82%, 300308 权重8%但跌17.34%。
        LGB 给高波动股高权重但信号在 bull regime 失效, 满仓无个股级保护。
        被动风控 (止损/波动率调整) 已证明无法根本解决, 需从模型层让 LGB 学习
        "bull regime 下高波动股风险收益不对称" 的模式。

    方案: 添加 8 个 regime-aware 特征, 让 LGB 在训练阶段就学到 regime 风险
      市场状态识别 (基于 510300 大盘代理):
        - market_regime_bull: 1/0, close>MA60 且 MA60 上行
        - market_regime_bear: 1/0, close<MA60 且 MA60 下行
        - market_regime_choppy: 1/0, close>MA60 且 MA60 下行 (震荡)
        - market_regime_rebound: 1/0, close<MA60 且 MA60 上行 (反弹)
        - market_vol_20: 大盘 20 日实现波动率
        - market_mom_20: 大盘 20 日动量
      Regime × 个股风险交互 (让模型学习 regime 下的个股行为差异):
        - vol20_x_bull: 个股 20 日波动率 × bull regime
        - mom20_x_bull: 个股 20 日动量 × bull regime

    设计原则:
        1. 全部 regime 信号基于大盘 proxy (510300) 计算, 无前视偏差
        2. regime label 在训练样本期间是已知的 (基于历史 MA60), 可用于训练
        3. 交互特征让 LGB 自动学习 "bull 下高波动→低收益" 的模式, 而非硬编码

    Args:
        ohlcv_dict: {code: DataFrame[含技术因子]}

    Returns:
        合并后的字典, 每个 DataFrame 新增 8 个 regime-aware 特征
    """
    proxy_code = _REGIME_PROXY_SYMBOL
    ma_period = 60
    slope_window = 5
    vol_lookback = 20
    mom_lookback = 20

    # === Step 1: 从大盘 proxy 计算 regime 序列 ===
    regime_series: pd.Series | None = None
    market_vol_series: pd.Series | None = None
    market_mom_series: pd.Series | None = None

    if proxy_code in ohlcv_dict:
        proxy_df = ohlcv_dict[proxy_code].copy()
        if hasattr(proxy_df.index, "tz") and proxy_df.index.tz is not None:
            proxy_df.index = proxy_df.index.tz_localize(None)
        proxy_df = proxy_df.sort_index()

        if len(proxy_df) >= ma_period + slope_window:
            close = proxy_df["close"]
            ma = close.rolling(ma_period).mean()
            ma_slope = ma.diff(slope_window)
            above_ma = close > ma
            ma_rising = ma_slope > 0

            # 四态 regime (与 _apply_market_regime_scaling 一致)
            regime = pd.Series("unknown", index=proxy_df.index)
            regime[above_ma & ma_rising] = "bull"
            regime[above_ma & (~ma_rising)] = "choppy"
            regime[(~above_ma) & ma_rising] = "rebound"
            regime[(~above_ma) & (~ma_rising)] = "bear"
            regime_series = regime

            # 大盘波动率
            daily_rets = close.pct_change()
            market_vol_series = daily_rets.rolling(vol_lookback).std()

            # 大盘动量
            market_mom_series = close.pct_change(mom_lookback)

    # === Step 2: 为每个标的添加 regime-aware 特征 ===
    out: dict[str, pd.DataFrame] = {}
    for code, df in ohlcv_dict.items():
        new_df = df.copy()

        # 对齐大盘 regime 序列到个股索引
        if regime_series is not None:
            regime_aligned = regime_series.reindex(new_df.index).ffill().fillna("unknown")
            market_vol_aligned = market_vol_series.reindex(new_df.index).ffill().fillna(0.0)
            market_mom_aligned = market_mom_series.reindex(new_df.index).ffill().fillna(0.0)

            new_df["market_regime_bull"] = (regime_aligned == "bull").astype(int)
            new_df["market_regime_bear"] = (regime_aligned == "bear").astype(int)
            new_df["market_regime_choppy"] = (regime_aligned == "choppy").astype(int)
            new_df["market_regime_rebound"] = (regime_aligned == "rebound").astype(int)
            new_df["market_vol_20"] = market_vol_aligned.astype(float)
            new_df["market_mom_20"] = market_mom_aligned.astype(float)
        else:
            # 无大盘数据时填默认值 (避免训练失败)
            new_df["market_regime_bull"] = 0
            new_df["market_regime_bear"] = 0
            new_df["market_regime_choppy"] = 0
            new_df["market_regime_rebound"] = 0
            new_df["market_vol_20"] = 0.0
            new_df["market_mom_20"] = 0.0

        # === Step 3: Regime × 个股风险交互特征 ===
        # 个股 20 日波动率 (优先复用已有列, 否则现算)
        if "volatility_20" in new_df.columns:
            stock_vol20 = new_df["volatility_20"]
        else:
            stock_vol20 = new_df["close"].pct_change().rolling(vol_lookback).std()
        # 个股 20 日动量 (优先复用已有列)
        if "return_20" in new_df.columns:
            stock_mom20 = new_df["return_20"]
        else:
            stock_mom20 = new_df["close"].pct_change(mom_lookback)

        # 交互特征: bull regime 下个股风险被放大
        # 设计意图: 2024-06 案例中, 688017/300308 在 bull regime 下高波动→大跌,
        #           LGB 通过此特征可学到 "bull × 高波动 → 低未来收益"
        new_df["vol20_x_bull"] = (stock_vol20 * new_df["market_regime_bull"]).fillna(0)
        new_df["mom20_x_bull"] = (stock_mom20 * new_df["market_regime_bull"]).fillna(0)

        # 填充 NaN
        for col in [
            "market_regime_bull",
            "market_regime_bear",
            "market_regime_choppy",
            "market_regime_rebound",
            "market_vol_20",
            "market_mom_20",
            "vol20_x_bull",
            "mom20_x_bull",
        ]:
            new_df[col] = new_df[col].fillna(0)

        out[code] = new_df
    return out


# ============================================================
# 行业相对强度特征
# ============================================================
def add_industry_relative_strength_features(
    ohlcv_dict: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    """行业相对强度特征 (自构建行业基准)

    针对周期股/ETF 的 best_iter=1 问题:
    - 601088 煤炭、600219 铝业、600019 钢铁、000408/000975 矿业
    - 600036 银行、688981 半导体

    用持仓同行业标的的平均收益作为行业基准, 计算:
    - industry_return_5: 行业 5 日平均收益
    - industry_return_20: 行业 20 日平均收益
    - relative_strength_5: 个股 5 日收益 - 行业 5 日收益
    - relative_strength_20: 个股 20 日收益 - 行业 20 日收益
    - industry_rank_20: 个股在行业内的 20 日收益排名分位

    Args:
        ohlcv_dict: {code: DataFrame[open, high, low, close, volume]}

    Returns:
        合并后的字典, 每个 DataFrame 新增 5 个行业相对强度特征
    """
    sector_map = _build_sector_map()

    # 收集所有标的的收盘价和收益率
    closes = pd.DataFrame({code: df["close"] for code, df in ohlcv_dict.items()})
    returns_5 = closes.pct_change(5)
    returns_20 = closes.pct_change(20)

    # 按行业分组构建行业基准 (等权平均)
    sector_codes: dict[str, list[str]] = {}
    for code in ohlcv_dict:
        sector = sector_map.get(code, "其他")
        sector_codes.setdefault(sector, []).append(code)

    # 计算行业基准收益
    industry_ret_5: dict[str, pd.Series] = {}
    industry_ret_20: dict[str, pd.Series] = {}
    for sector, codes in sector_codes.items():
        valid_codes = [c for c in codes if c in returns_5.columns]
        if len(valid_codes) >= 2:
            # 等权行业平均
            industry_ret_5[sector] = returns_5[valid_codes].mean(axis=1)
            industry_ret_20[sector] = returns_20[valid_codes].mean(axis=1)
        elif len(valid_codes) == 1:
            # 只有一个标的的行业, 用自身作为基准 (相对强度=0)
            industry_ret_5[sector] = returns_5[valid_codes[0]]
            industry_ret_20[sector] = returns_20[valid_codes[0]]

    out: dict[str, pd.DataFrame] = {}
    for code, df in ohlcv_dict.items():
        new_df = df.copy()
        sector = sector_map.get(code, "其他")

        ind_ret5 = industry_ret_5.get(sector)
        ind_ret20 = industry_ret_20.get(sector)

        if ind_ret5 is not None and code in returns_5.columns:
            new_df["industry_return_5"] = ind_ret5.reindex(new_df.index).fillna(0)
            new_df["industry_return_20"] = ind_ret20.reindex(new_df.index).fillna(0)
            new_df["relative_strength_5"] = (
                returns_5[code].reindex(new_df.index) - new_df["industry_return_5"]
            ).fillna(0)
            new_df["relative_strength_20"] = (
                returns_20[code].reindex(new_df.index) - new_df["industry_return_20"]
            ).fillna(0)

            # 行业内排名分位 (同一行业内的收益排名)
            sector_codes_list = sector_codes.get(sector, [code])
            valid_codes = [c for c in sector_codes_list if c in returns_20.columns]
            if len(valid_codes) >= 2:
                rank_df = returns_20[valid_codes].rank(axis=1, pct=True)
                new_df["industry_rank_20"] = rank_df[code].reindex(new_df.index).fillna(0.5)
            else:
                new_df["industry_rank_20"] = 0.5
        else:
            # 无行业基准, 填中性值
            for col in [
                "industry_return_5",
                "industry_return_20",
                "relative_strength_5",
                "relative_strength_20",
            ]:
                new_df[col] = 0.0
            new_df["industry_rank_20"] = 0.5

        out[code] = new_df
    return out


# ============================================================
# 资金流向特征
# ============================================================
def add_capital_flow_features(
    ohlcv_dict: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    """资金流向特征 (基于价量关系估算)

    针对周期股 best_iter=1 问题, 增加资金行为维度:
    - capital_flow: 当日资金净流入估算 (价量关系)
    - cumulative_flow_5: 5 日累计资金流向
    - flow_divergence: 量价背离指标
    - smart_money_ratio: 大单净占比估算
    - flow_momentum: 资金流向动量 (5 日变化率)

    估算方法 (无需外部数据):
        capital_flow = (close - open) / (high - low + 1e-9) * volume

    Args:
        ohlcv_dict: {code: DataFrame[open, high, low, close, volume]}

    Returns:
        合并后的字典, 每个 DataFrame 新增 5 个资金流向特征
    """
    out: dict[str, pd.DataFrame] = {}
    for code, df in ohlcv_dict.items():
        new_df = df.copy()

        # 确保有 open/high/low/close/volume
        required = ["open", "high", "low", "close", "volume"]
        if not all(c in new_df.columns for c in required):
            for col in [
                "capital_flow",
                "cumulative_flow_5",
                "flow_divergence",
                "smart_money_ratio",
                "flow_momentum",
            ]:
                new_df[col] = 0.0
            out[code] = new_df
            continue

        o, h, lo, c, v = (
            new_df["open"],
            new_df["high"],
            new_df["low"],
            new_df["close"],
            new_df["volume"],
        )

        # 日内价格位置 (0=最低, 1=最高)
        price_range = (h - lo).replace(0, 1e-9)
        daily_position = (c - lo) / price_range

        # 当日资金净流入估算: 收盘位置 × 成交量 × sign(收涨)
        direction = np.sign(c - o)
        new_df["capital_flow"] = (daily_position * v * direction).fillna(0)

        # 5 日累计资金流向
        new_df["cumulative_flow_5"] = new_df["capital_flow"].rolling(5, min_periods=1).sum()

        # 量价背离: 价格 5 日收益与资金流向的符号差异
        price_ret5 = c.pct_change(5).fillna(0)
        flow_sign = np.sign(new_df["cumulative_flow_5"])
        price_sign = np.sign(price_ret5)
        new_df["flow_divergence"] = (price_sign * flow_sign * -1).fillna(0)

        # 大单净占比估算: 用日内振幅 × 成交量占比
        avg_volume = v.rolling(20, min_periods=1).mean().replace(0, 1e-9)
        vol_ratio = v / avg_volume
        amplitude = (h - lo) / c.replace(0, 1e-9)
        new_df["smart_money_ratio"] = (
            (amplitude * vol_ratio).rolling(5, min_periods=1).mean().fillna(0)
        )

        # 资金流向动量: 5 日累计流向的变化率
        flow_shift5 = new_df["cumulative_flow_5"].shift(5).replace(0, np.nan)
        new_df["flow_momentum"] = (
            (
                (new_df["cumulative_flow_5"] - new_df["cumulative_flow_5"].shift(5))
                / (flow_shift5.abs() + 1e-9)
            )
            .fillna(0)
            .replace([np.inf, -np.inf], 0)
        )

        out[code] = new_df
    return out


# ============================================================
# 跨市场信号特征
# ============================================================
def _attach_proxy_trend(
    new_df: pd.DataFrame, proxy_trend_20: dict[str, pd.Series]
) -> pd.DataFrame:
    """将 4 个代理趋势列合并到 new_df (缺失则填 0.0)。"""
    for col, proxy_key in _CROSS_MARKET_TREND_COLS:
        series = proxy_trend_20.get(proxy_key)
        if series is not None:
            new_df[col] = series.reindex(new_df.index).fillna(0)
        else:
            new_df[col] = 0.0
    return new_df


def _attach_derived_signals(
    new_df: pd.DataFrame,
    style_rotation: pd.Series | None,
    gold_vol_ratio: pd.Series | None,
) -> pd.DataFrame:
    """合并风格轮动信号 + 避险资金流入 (缺失则填 0.0)。"""
    if style_rotation is not None:
        new_df["style_rotation_5"] = style_rotation.reindex(new_df.index).fillna(0)
    else:
        new_df["style_rotation_5"] = 0.0

    if gold_vol_ratio is not None:
        new_df["safe_haven_flow"] = gold_vol_ratio.reindex(new_df.index).fillna(0)
    else:
        new_df["safe_haven_flow"] = 0.0
    return new_df


def _collect_cross_market_proxies(
    ohlcv_dict: dict[str, pd.DataFrame],
) -> tuple[dict[str, pd.Series], dict[str, pd.Series]]:
    """收集代理标的收盘价和成交量序列。"""
    proxy_data: dict[str, pd.Series] = {}  # {proxy_name: close_series}
    proxy_vol: dict[str, pd.Series] = {}
    for proxy_name, code in _CROSS_MARKET_PROXIES.items():
        if code in ohlcv_dict:
            df = ohlcv_dict[code]
            if "close" in df.columns:
                proxy_data[proxy_name] = df["close"]
            if "volume" in df.columns:
                proxy_vol[proxy_name] = df["volume"]
    return proxy_data, proxy_vol


def _compute_proxy_trends(proxy_data: dict[str, pd.Series]) -> dict[str, pd.Series]:
    """计算各代理标的的 20 日趋势 (close/ma20 - 1)。"""
    proxy_trend_20: dict[str, pd.Series] = {}
    for name, close in proxy_data.items():
        if len(close) >= 20:
            ma20 = close.rolling(20, min_periods=1).mean()
            proxy_trend_20[name] = (close / ma20 - 1).fillna(0)
    return proxy_trend_20


def _compute_style_rotation(
    proxy_trend_20: dict[str, pd.Series]
) -> pd.Series | None:
    """风格轮动: 科技 vs 红利 的 20 日趋势差 (5 日变化)。"""
    tech_trend = proxy_trend_20.get("tech_growth_proxy")
    div_trend = proxy_trend_20.get("dividend_defensive")
    if tech_trend is None or div_trend is None:
        return None
    style_diff = tech_trend - div_trend
    return style_diff.diff(5).fillna(0)


def _compute_gold_vol_ratio(proxy_vol: dict[str, pd.Series]) -> pd.Series | None:
    """避险资金流入: 黄金ETF 成交量比 (相对 20 日均量)。"""
    if "gold_safe_haven" not in proxy_vol:
        return None
    gv = proxy_vol["gold_safe_haven"]
    gv_ma = gv.rolling(20, min_periods=1).mean().replace(0, 1e-9)
    return (gv / gv_ma - 1).fillna(0)


def add_cross_market_features(
    ohlcv_dict: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    """跨市场信号特征 (用持仓 ETF/股票作为跨市场代理)

    针对周期股/ETF 的 best_iter=1 问题, 增加跨市场维度:
    - gold_trend_20: 黄金ETF 20 日趋势 (避险情绪)
    - bank_trend_20: 银行股 20 日趋势 (利率/信贷环境)
    - tech_style_20: 科技ETF 20 日趋势 (风险偏好)
    - defensive_style_20: 红利ETF 20 日趋势 (防御风格)
    - style_rotation_5: 风格轮动信号 (科技-红利差值的 5 日变化)
    - safe_haven_flow: 避险资金流入 (黄金ETF成交量比)

    代理逻辑:
        黄金ETF 涨 → 避险情绪上升 → 利空周期股
        银行股 涨 → 利率上行/信贷宽松 → 利好银行股
        科技ETF 涨 → 风险偏好上升 → 利好成长股
        红利ETF 涨 → 防御偏好上升 → 利空周期股

    Args:
        ohlcv_dict: {code: DataFrame[open, high, low, close, volume]}

    Returns:
        合并后的字典, 每个 DataFrame 新增 6 个跨市场特征
    """
    proxy_data, proxy_vol = _collect_cross_market_proxies(ohlcv_dict)
    proxy_trend_20 = _compute_proxy_trends(proxy_data)
    style_rotation = _compute_style_rotation(proxy_trend_20)
    gold_vol_ratio = _compute_gold_vol_ratio(proxy_vol)

    out: dict[str, pd.DataFrame] = {}
    for code, df in ohlcv_dict.items():
        new_df = df.copy()
        _attach_proxy_trend(new_df, proxy_trend_20)
        _attach_derived_signals(new_df, style_rotation, gold_vol_ratio)
        out[code] = new_df
    return out
