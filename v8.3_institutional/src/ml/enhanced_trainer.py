"""
ML 增强训练引擎 v2.0 — 四项核心优化

1. 标签工程: T+1/T+5/T+10 三分类 → 过滤震荡日 → 二分类 (涨>1% vs 跌<-1%)
2. 扩展预测窗口: 支持 T+5 / T+10 中期趋势预测
3. 特征增强: 行业相对强弱 + 市场宽度 + 北向资金 + PE/PB/ROE 基本面
4. 样本加权: 时间衰减权重 × 波动率权重

设计原则：
- 兼容现有 auto_train_optimized.py 的输出格式
- 模型可被 ml_predictor.py 无缝加载
- 支持 Optuna 超参数优化管线
"""

import json
import logging
import os
import sys
import warnings
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import joblib
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

logger = logging.getLogger(__name__)

# ── ML 依赖 ──
from sklearn.ensemble import (  # noqa: E402
    ExtraTreesClassifier,
    GradientBoostingClassifier,
    RandomForestClassifier,
)
from sklearn.feature_selection import SelectKBest, f_classif, mutual_info_classif  # noqa: E402
from sklearn.metrics import (  # noqa: E402
    accuracy_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import TimeSeriesSplit  # noqa: E402

try:
    import xgboost as xgb

    _HAS_XGB = True
except ImportError:
    _HAS_XGB = False

try:
    import lightgbm as lgb

    _HAS_LGB = True
except ImportError:
    _HAS_LGB = False

try:
    import optuna
    from optuna.pruners import MedianPruner
    from optuna.samplers import TPESampler

    _HAS_OPTUNA = True
except ImportError:
    _HAS_OPTUNA = False

# ── Purged K-Fold (P0-3 信息泄露防护) ──
try:
    from utils.purged_kfold import purged_timeseries_split, validate_embargo

    _HAS_PURGED_CV = True
except ImportError:
    try:
        _project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        if _project_root not in sys.path:
            sys.path.insert(0, _project_root)
        from utils.purged_kfold import purged_timeseries_split, validate_embargo  # noqa: F401

        _HAS_PURGED_CV = True
    except ImportError:
        _HAS_PURGED_CV = False


# ═══════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════

# 行业分类映射 (申万一级 → 标的代码)
INDUSTRY_MAP = {
    "电子": ["002916", "688041", "002371", "688981"],
    "电力设备": ["300750"],
    "机械设备": ["000425"],
    "煤炭": ["601088"],
    "有色金属": ["600219", "000408"],
    "钢铁": ["600019"],
    "医药生物": ["600276", "603259", "002422"],
    "通信": ["300308"],
}

# 行业ETF映射 (用于计算行业相对强弱)
INDUSTRY_ETF_MAP = {
    "电子": "159997",  # 电子ETF
    "电力设备": "516160",  # 新能源ETF
    "机械设备": "516960",  # 机械ETF
    "煤炭": "515220",  # 煤炭ETF
    "有色金属": "512400",  # 有色ETF
    "钢铁": "515210",  # 钢铁ETF
    "医药生物": "512010",  # 医药ETF
    "通信": "515050",  # 5GETF
}

# 市场指数映射
BENCHMARK_INDICES = {
    "CSI300": "000300",  # 沪深300
    "CSI500": "000905",  # 中证500
    "SSE50": "000016",  # 上证50
}


class EnhancedFeatureEngineer:
    """增强特征工程 — 技术面 + 行业 + 市场 + 基本面"""

    def __init__(self, data_dir: str = "data/cache", fundamentals_path: Optional[str] = None):
        """
        Args:
            data_dir: K线缓存目录
            fundamentals_path: 基本面数据文件路径 (CSV/Parquet)
        """
        self.data_dir = data_dir
        self.fundamentals_path = fundamentals_path
        self._fundamentals_cache: Dict[str, Dict] = {}
        self._load_fundamentals()

    def _load_fundamentals(self):
        """加载预缓存的基本面数据"""
        if self.fundamentals_path and os.path.exists(self.fundamentals_path):
            try:
                df = pd.read_parquet(self.fundamentals_path)
                for _, row in df.iterrows():
                    code = str(row.get("code", ""))
                    if code:
                        self._fundamentals_cache[code] = {
                            "pe": row.get("pe", np.nan),
                            "pb": row.get("pb", np.nan),
                            "roe": row.get("roe", np.nan),
                            "debt_ratio": row.get("debt_ratio", np.nan),
                            "market_cap": row.get("market_cap", np.nan),
                        }
            except Exception as e:
                logger.warning(f"基本面数据加载失败: {e}")

    def get_fundamentals(self, code: str) -> Dict:
        """获取单只标的基本面数据"""
        return self._fundamentals_cache.get(code, {})

    def build_enhanced_features(
        self,
        df: pd.DataFrame,
        market_data: Optional[Dict[str, pd.DataFrame]] = None,
        northbound_data: pd.DataFrame = None,
        prediction_horizon: int = 1,
    ) -> pd.DataFrame:
        """
        构建增强特征集 — 技术面 + 行业RS + 市场宽度 + 北向 + 基本面 + 多窗口标签

        Args:
            df: 单只标的K线 DataFrame (需含 close/open/high/low/volume)
            market_data: {code: DataFrame} 全市场K线字典 (用于计算市场宽度/行业RS)
            northbound_data: 北向资金日数据
            prediction_horizon: 预测窗口 T+N

        Returns:
            含全部特征和标签的 DataFrame
        """
        data = df.copy()
        code = data.get("code", pd.Series(["UNKNOWN"] * len(data))).iloc[0] if "code" in data.columns else "UNKNOWN"
        code = str(code)

        # ── 1. 基础技术指标 (约60个特征) ──
        data = self._build_technical_features(data)

        # ── 2. 行业相对强弱 ──
        data = self._build_industry_rs(data, code, market_data)

        # ── 3. 市场宽度 ──
        data = self._build_market_breadth(data, market_data)

        # ── 4. 北向资金 ──
        if northbound_data is not None and not northbound_data.empty:
            data = self._build_northbound_features(data, northbound_data)

        # ── 5. 基本面 ──
        data = self._build_fundamental_features(data, code)

        # ── 6. 多窗口标签 (T+1 / T+5 / T+10) ──
        data = self._build_multi_horizon_labels(data, prediction_horizon)

        # ── 7. 样本权重 ──
        data = self._build_sample_weights(data)

        return data

    def _build_technical_features(self, data: pd.DataFrame) -> pd.DataFrame:
        """构建技术面特征 (扩展版, ~70个)"""
        d = data.copy()

        # 基础
        d["returns"] = d["close"].pct_change()
        d["log_returns"] = np.log(d["close"] / d["close"].shift(1))
        d["abs_returns"] = d["returns"].abs()

        # 均线
        for p in [5, 10, 20, 60, 120]:
            d[f"ma_{p}"] = d["close"].rolling(p).mean()
        d["ma5_ma20"] = d["ma_5"] / d["ma_20"]
        d["ma20_ma60"] = d["ma_20"] / d["ma_60"]
        d["ma60_ma120"] = d["ma_60"] / d["ma_120"]
        d["ma5_ma10"] = d["ma_5"] / d["ma_10"]
        d["ma10_ma20"] = d["ma_10"] / d["ma_20"]

        # 波动率
        for p in [5, 10, 20, 60]:
            d[f"vol_{p}"] = d["returns"].rolling(p).std()
        d["vol_ratio_5_20"] = d["vol_5"] / d["vol_20"]
        d["vol_ratio_20_60"] = d["vol_20"] / d["vol_60"]

        # RSI
        for p in [7, 14, 21]:
            d[f"rsi_{p}"] = self._calc_rsi(d["close"], p)
        d["rsi_diff_7_21"] = d["rsi_7"] - d["rsi_21"]

        # MACD
        ema12 = d["close"].ewm(span=12).mean()
        ema26 = d["close"].ewm(span=26).mean()
        d["macd"] = ema12 - ema26
        d["macd_signal"] = d["macd"].ewm(span=9).mean()
        d["macd_hist"] = d["macd"] - d["macd_signal"]
        d["macd_divergence"] = d["macd"] / d["close"]

        # 布林带
        d["boll_mid"] = d["ma_20"]
        boll_std = d["close"].rolling(20).std()
        d["boll_upper"] = d["boll_mid"] + 2 * boll_std
        d["boll_lower"] = d["boll_mid"] - 2 * boll_std
        d["boll_width"] = (d["boll_upper"] - d["boll_lower"]) / d["boll_mid"]
        d["boll_pct"] = (d["close"] - d["boll_lower"]) / (d["boll_upper"] - d["boll_lower"] + 1e-10)

        # 成交量
        if "volume" in d.columns or "VOLUME" in d.columns:
            vol_col = "volume" if "volume" in d.columns else "VOLUME"
            d[vol_col] = pd.to_numeric(d[vol_col], errors="coerce")
            d["volume_ma_5"] = d[vol_col].rolling(5).mean()
            d["volume_ma_20"] = d[vol_col].rolling(20).mean()
            d["volume_ratio"] = d[vol_col] / d["volume_ma_5"]
            d["volume_ma_ratio"] = d["volume_ma_5"] / d["volume_ma_20"]
            d["volume_change"] = d[vol_col].pct_change()
            d["volume_trend"] = d[vol_col].rolling(20).mean() / d[vol_col].rolling(60).mean()
            _vol = d.get("volume", d.get("VOLUME", pd.Series(1, index=d.index)))
        else:
            _vol = pd.Series(1, index=d.index)
            d["volume_ratio"] = 1.0

        # VWAP
        d["vwap"] = (d["close"] * _vol).rolling(5).sum() / _vol.rolling(5).sum()
        d["price_vwap_diff"] = d["close"] - d["vwap"]
        d["price_vwap_ratio"] = d["close"] / d["vwap"]

        # 动量
        for p in [5, 10, 20, 60]:
            d[f"momentum_{p}"] = d["returns"].rolling(p).sum()
        d["momentum_acceleration"] = d["momentum_10"] - d["momentum_20"]

        # 趋势
        for p in [5, 20, 60]:
            d[f"trend_{p}"] = (d["close"] - d["close"].shift(p)) / d["close"].shift(p)
        d["trend_strength"] = d["trend_20"] / (d["vol_20"] + 1e-10)

        # K线形态
        if "high" in d.columns and "low" in d.columns and "open" in d.columns:
            d["range"] = d["high"] - d["low"]
            d["range_ratio"] = d["range"] / d["close"]
            d["upper_shadow"] = d["high"] - d[["open", "close"]].max(axis=1)
            d["lower_shadow"] = d[["open", "close"]].min(axis=1) - d["low"]
            d["body"] = (d["close"] - d["open"]).abs()
            d["body_ratio"] = d["body"] / d["range"]
            d["gap"] = d["open"] - d["close"].shift(1)
            d["gap_ratio"] = d["gap"] / d["close"].shift(1)

        # ATR
        d["atr_14"] = self._calc_atr(d, 14)
        d["atr_ratio"] = d["atr_14"] / d["close"]

        # CCI
        d["cci_14"] = self._calc_cci(d, 14)

        # WR
        d["wr_14"] = self._calc_wr(d, 14)

        # OBV
        d["obv"] = self._calc_obv(d)
        d["obv_ma_10"] = d["obv"].rolling(10).mean()
        d["obv_ratio"] = d["obv"] / d["obv_ma_10"]

        # MFI
        d["mfi_14"] = self._calc_mfi(d, 14)

        # 一目均衡
        if "high" in d.columns and "low" in d.columns:
            d["ichimoku_conversion"] = (d["high"].rolling(9).max() + d["low"].rolling(9).min()) / 2
            d["ichimoku_base"] = (d["high"].rolling(26).max() + d["low"].rolling(26).min()) / 2
            d["ichimoku_diff"] = d["close"] - d["ichimoku_base"]

        # 特征交叉
        d["rsi_vol"] = d.get("rsi_14", 50) * d["vol_20"]
        d["mom_vol"] = d["momentum_20"] * d["vol_20"]
        d["macd_rsi"] = d["macd"] * d.get("rsi_14", 50)
        d["boll_mom"] = d["boll_width"] * d["momentum_20"]
        d["trend_rsi"] = d["trend_20"] * d.get("rsi_14", 50)

        # 涨跌比率
        d["up_days_5"] = (d["returns"] > 0).rolling(5).sum() / 5
        d["up_days_20"] = (d["returns"] > 0).rolling(20).sum() / 20

        # 连涨连跌
        d["consecutive_up"] = self._calc_consecutive(d["returns"] > 0)
        d["consecutive_down"] = self._calc_consecutive(d["returns"] < 0)

        return d

    def _build_industry_rs(
        self, data: pd.DataFrame, code: str, market_data: Optional[Dict[str, pd.DataFrame]] = None
    ) -> pd.DataFrame:
        """构建行业相对强弱特征"""
        d = data.copy()

        industry = None
        for ind, codes in INDUSTRY_MAP.items():
            if code in codes:
                industry = ind
                break

        if industry is None or market_data is None:
            d["industry_rs_5"] = 0.0
            d["industry_rs_20"] = 0.0
            d["industry_momentum_5"] = 0.0
            d["stock_vs_industry_5"] = 0.0
            d["stock_vs_industry_20"] = 0.0
            return d

        peer_codes = INDUSTRY_MAP.get(industry, [])
        peer_returns = []

        for pc in peer_codes:
            if pc in market_data:
                mdf = market_data[pc]
                if "close" in mdf.columns and len(mdf) >= 20:
                    ret = mdf["close"].pct_change()
                    try:
                        ret = ret.reindex(d.index, method="ffill")
                    except TypeError:
                        ret = ret.reset_index(drop=True)
                        ret = ret.reindex(range(len(d)), method="ffill")
                    peer_returns.append(ret)

        if peer_returns:
            try:
                avg_ret = pd.concat(peer_returns, axis=1).mean(axis=1)
            except Exception as e:
                logger.warning(f"行业收益率合并失败: {e}")
                avg_ret = pd.Series(0.0, index=d.index)
            avg_ret = avg_ret.reindex(d.index, method="ffill")
            d["industry_rs_5"] = avg_ret.rolling(5).sum()
            d["industry_rs_20"] = avg_ret.rolling(20).sum()
            d["industry_momentum_5"] = avg_ret.rolling(5).mean()
        else:
            d["industry_rs_5"] = 0.0
            d["industry_rs_20"] = 0.0
            d["industry_momentum_5"] = 0.0

        d["stock_vs_industry_5"] = d["momentum_5"] - d["industry_rs_5"]
        d["stock_vs_industry_20"] = d["momentum_20"] - d["industry_rs_20"]

        return d

    def _build_market_breadth(
        self, data: pd.DataFrame, market_data: Optional[Dict[str, pd.DataFrame]] = None
    ) -> pd.DataFrame:
        """构建市场宽度特征"""
        d = data.copy()

        if market_data is None or not market_data:
            d["breadth_up_ratio"] = 0.5
            d["breadth_ma_up"] = 0.5
            d["breadth_macd_bull"] = 0.5
            d["advance_decline_line"] = 0.0
            return d

        up_ratios = []
        for _cm, mdf in market_data.items():
            if "close" not in mdf.columns or len(mdf) < 5:
                continue
            ret = mdf["close"].pct_change()
            up = (ret > 0).astype(int)
            try:
                up = up.reindex(d.index, method="ffill")
            except TypeError:
                up = up.reset_index(drop=True)
                up = up.reindex(range(len(d)), method="ffill")
            up_ratios.append(up)

        if up_ratios:
            try:
                breadth = pd.concat(up_ratios, axis=1).mean(axis=1)
            except Exception as e:
                logger.warning(f"市场宽度数据合并失败: {e}")
                breadth = pd.Series(0.5, index=d.index)
            breadth = breadth.reindex(d.index, method="ffill")
            d["breadth_up_ratio"] = breadth
            d["breadth_ma_up"] = breadth.rolling(5).mean()
            d["breadth_macd_bull"] = breadth.rolling(20).mean()
            d["advance_decline_line"] = (breadth - 0.5).rolling(5).sum()
        else:
            d["breadth_up_ratio"] = 0.5
            d["breadth_ma_up"] = 0.5
            d["breadth_macd_bull"] = 0.5
            d["advance_decline_line"] = 0.0

        return d

    def _build_northbound_features(self, data: pd.DataFrame, northbound_data: pd.DataFrame) -> pd.DataFrame:
        """构建北向资金特征"""
        d = data.copy()

        if northbound_data is None or northbound_data.empty:
            d["northbound_net_flow"] = 0.0
            d["northbound_flow_5d"] = 0.0
            d["northbound_flow_20d"] = 0.0
            d["northbound_trend"] = 0.0
            return d

        # 尝试匹配日期
        nb = northbound_data.copy()
        if "date" in nb.columns:
            nb["date"] = pd.to_datetime(nb["date"])
            nb = nb.set_index("date")

        flow_col = None
        for col in ["net_flow", "net_inflow", "net_buy", "北向净流入"]:
            if col in nb.columns:
                flow_col = col
                break

        if flow_col is None:
            d["northbound_net_flow"] = 0.0
            d["northbound_flow_5d"] = 0.0
            return d

        nb_flow = nb[flow_col]
        nb_flow = nb_flow.reindex(d.index, method="ffill").fillna(0)

        d["northbound_net_flow"] = nb_flow
        d["northbound_flow_5d"] = nb_flow.rolling(5).sum()
        d["northbound_flow_20d"] = nb_flow.rolling(20).sum()
        d["northbound_trend"] = d["northbound_flow_5d"] - d["northbound_flow_20d"].shift(15)

        return d

    def _build_fundamental_features(self, data: pd.DataFrame, code: str) -> pd.DataFrame:
        """构建基本面特征"""
        d = data.copy()
        fund = self.get_fundamentals(code)

        d["pe"] = fund.get("pe", np.nan)
        d["pb"] = fund.get("pb", np.nan)
        d["roe"] = fund.get("roe", np.nan)
        d["debt_ratio"] = fund.get("debt_ratio", np.nan)
        d["market_cap"] = fund.get("market_cap", np.nan)

        # PE/PB分位数特征 (如果数据充足, 计算相对于历史的百分位)
        d["pe_percentile"] = d["pe"].rank(pct=True) if d["pe"].notna().sum() > 10 else 0.5
        d["pb_percentile"] = d["pb"].rank(pct=True) if d["pb"].notna().sum() > 10 else 0.5

        # PE * PB 复合估值
        d["pe_pb_product"] = d["pe"] * d["pb"]

        return d

    def _build_multi_horizon_labels(self, data: pd.DataFrame, primary_horizon: int = 1) -> pd.DataFrame:
        """
        构建多窗口标签
        - label_t1: T+1 三分类 (0=跌<-1%, 1=震荡, 2=涨>1%)
        - label_t5: T+5 三分类
        - label_t10: T+10 三分类
        - label_binary: 过滤震荡日后的二分类 (0=跌, 1=涨) — 用于高信号质量模型
        - label_direction: 最简方向 (0=跌, 1=涨, 忽略幅度) — 兼容旧版
        """
        d = data.copy()
        threshold = 0.01  # 1%

        # 保护：close 必须是数值类型
        if d["close"].dtype.kind not in ("f", "i", "u"):
            try:
                d["close"] = pd.to_numeric(d["close"], errors="coerce")
            except Exception as e:
                logger.warning(f"close列类型转换失败: {d['close'].dtype}, error={e}")
                return d

        for horizon, col_prefix in [(1, "t1"), (5, "t5"), (10, "t10")]:
            try:
                # T+N 收益率
                future_ret = d["close"].shift(-horizon) / d["close"] - 1
                future_ret = pd.to_numeric(future_ret, errors="coerce")
                d[f"future_ret_{col_prefix}"] = future_ret

                # 三分类标签
                d[f"label_{col_prefix}"] = pd.cut(
                    future_ret, bins=[-np.inf, -threshold, threshold, np.inf], labels=[0, 1, 2]
                ).astype(float)

                # 过滤震荡日后的二分类标签 (震荡日 = NaN，训练时丢弃)
                d[f"label_{col_prefix}_binary"] = np.where(
                    future_ret > threshold, 1, np.where(future_ret < -threshold, 0, np.nan)
                )

                # 方向标签 (兼容旧版)
                d[f"label_{col_prefix}_direction"] = (future_ret > 0).astype(int)
            except Exception as e:
                logger.warning(f"构建标签失败 horizon={horizon}: {e}")
                continue

        return d

    def _build_sample_weights(
        self, data: pd.DataFrame, time_decay_halflife: int = 126, vol_weight_power: float = 0.5
    ) -> pd.DataFrame:
        """
        构建样本权重

        权重 = 时间衰减权重 × 波动率权重

        - 时间衰减: 近期样本指数衰减, 半衰期126个交易日 (~半年)
        - 波动率权重: 高波动日权重更高 (大波动日包含更多信息)
        """
        d = data.copy()
        n = len(d)

        # 时间衰减权重
        time_weights = np.exp(-np.log(2) * np.arange(n)[::-1] / time_decay_halflife)
        time_weights = time_weights / time_weights.mean()  # 均值归一化

        # 波动率权重
        if "abs_returns" in d.columns:
            vol_weights = d["abs_returns"].rolling(20).mean().fillna(d["abs_returns"].mean())
            vol_weights = vol_weights**vol_weight_power
            vol_weights = vol_weights / vol_weights.mean()
        else:
            vol_weights = np.ones(n)

        d["sample_weight"] = time_weights * vol_weights.values

        return d

    # ── 指标计算工具 ──

    @staticmethod
    def _calc_rsi(prices, period=14):
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
        rs = gain / (loss + 1e-10)
        return 100 - (100 / (1 + rs))

    @staticmethod
    def _calc_atr(data, period=14):
        high = data.get("high", data["close"])
        low = data.get("low", data["close"])
        prev_close = data["close"].shift(1)
        tr = pd.concat(
            [
                high - low,
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        return tr.rolling(period).mean()

    @staticmethod
    def _calc_cci(data, period=14):
        tp = (data.get("high", data["close"]) + data.get("low", data["close"]) + data["close"]) / 3
        return (tp - tp.rolling(period).mean()) / (0.015 * tp.rolling(period).std() + 1e-10)

    @staticmethod
    def _calc_wr(data, period=14):
        high = data.get("high", data["close"])
        low = data.get("low", data["close"])
        hh = high.rolling(period).max()
        ll = low.rolling(period).min()
        return (hh - data["close"]) / (hh - ll + 1e-10) * -100

    @staticmethod
    def _calc_obv(data):
        vol = data.get("volume", data.get("VOLUME", 1))
        direction = data["returns"].apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
        return (vol * direction).cumsum()

    @staticmethod
    def _calc_mfi(data, period=14):
        tp = (data.get("high", data["close"]) + data.get("low", data["close"]) + data["close"]) / 3
        vol = data.get("volume", data.get("VOLUME", 1))
        rmf = tp * vol
        pos = rmf.where(data["returns"] > 0, 0).rolling(period).sum()
        neg = rmf.where(data["returns"] < 0, 0).rolling(period).sum().abs()
        return 100 - (100 / (1 + pos / (neg + 1e-10)))

    @staticmethod
    def _calc_consecutive(condition: pd.Series) -> pd.Series:
        """计算连续满足条件的天数"""
        result = pd.Series(0, index=condition.index)
        cnt = 0
        for i in range(len(condition)):
            if condition.iloc[i]:
                cnt += 1
            else:
                cnt = 0
            result.iloc[i] = cnt
        return result


class EnhancedMLTrainer:
    """增强版 ML 训练器 — 四维优化"""

    def __init__(
        self,
        output_dir: str = "models",
        label_threshold: float = 0.01,
        prediction_horizon: int = 1,
        filter_oscillation: bool = True,
        use_purged_cv: bool = True,
        embargo_days: int = 5,
        n_cv_splits: int = 5,
    ):
        """
        Args:
            output_dir: 模型输出目录
            label_threshold: 三分类阈值 (默认1%)
            prediction_horizon: 预测窗口 T+N (1/5/10)
            filter_oscillation: 是否过滤震荡日 (True=只用涨跌两端, 信号更干净)
            use_purged_cv: 是否使用 Purged K-Fold (P0-3 修复, 消除前视偏差, 默认True)
            embargo_days: Purged K-Fold 禁运期天数 (默认5天, De Prado建议)
            n_cv_splits: Purged K-Fold 折数 (默认5)
        """
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        self.label_threshold = label_threshold
        self.prediction_horizon = prediction_horizon
        self.filter_oscillation = filter_oscillation
        self.use_purged_cv = use_purged_cv
        self.embargo_days = embargo_days
        self.n_cv_splits = n_cv_splits

        self.models: Dict[str, Any] = {}
        self.results: Dict[str, Any] = {}
        self.feature_cols: List[str] = []
        self.selected_features: List[str] = []
        self.feature_selector = None
        self.scaler = None
        self.pca = None
        self.feature_engineer = EnhancedFeatureEngineer()

    # ── 数据加载 ──

    def load_all_data(
        self, data_dir: str = "data/cache", northbound_path: Optional[str] = None
    ) -> Tuple[pd.DataFrame, Dict[str, pd.DataFrame]]:
        """加载全部K线数据"""
        print("[DATA] 加载历史数据...")
        print(f"[PATH] 数据目录: {os.path.abspath(data_dir)}")

        all_data = []
        market_data = {}
        file_count = 0

        for fname in os.listdir(data_dir):
            if not (fname.startswith("kline_") and fname.endswith(".parquet")):
                continue
            filepath = os.path.join(data_dir, fname)
            try:
                df = pd.read_parquet(filepath)
                code = fname.replace("kline_", "").replace("_daily.parquet", "")
                df["code"] = code
                all_data.append(df)
                market_data[code] = df.copy()
                file_count += 1
            except Exception as e:
                logger.warning(f"{fname} 加载失败: {e}")

        if not all_data:
            print("\n[ERROR] 未找到任何parquet数据文件!")
            return None, {}

        combined = pd.concat(all_data, ignore_index=True)
        print(f"[DATA] 共 {file_count} 个文件, {len(combined)} 条记录")
        return combined, market_data

    def load_northbound_data(self, path: Optional[str] = None) -> Optional[pd.DataFrame]:
        """加载北向资金数据"""
        if path is None:
            # 尝试自动查找
            search_paths = [
                os.path.join("data", "northbound.parquet"),
                os.path.join("data", "northbound.csv"),
                os.path.join("data", "cache", "northbound.parquet"),
            ]
            for sp in search_paths:
                if os.path.exists(sp):
                    path = sp
                    break

        if path is None:
            return None

        try:
            if path.endswith(".parquet"):
                return pd.read_parquet(path)
            else:
                return pd.read_csv(path)
        except Exception as e:
            logger.warning(f"北向数据加载失败: {e}")
            return None

    # ── 特征构建 ──

    def prepare_dataset(
        self,
        df: pd.DataFrame,
        market_data: Optional[Dict[str, pd.DataFrame]] = None,
        northbound_data: pd.DataFrame = None,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, List[str]]:
        """
        准备训练数据集

        Returns:
            X: 特征矩阵
            y: 标签 (二分类: 过滤震荡日)
            sample_weight: 样本权重
            feature_names: 特征名列表
        """
        print(
            f"\n[FEATURE] 构建增强特征 (horizon=T+{self.prediction_horizon}, "
            f"filter_oscillation={self.filter_oscillation})..."
        )

        features_list = []

        for _code, group in df.groupby("code"):
            if len(group) < 120:
                continue
            group = group.reset_index(drop=True)
            feat = self.feature_engineer.build_enhanced_features(
                group,
                market_data=market_data,
                northbound_data=northbound_data,
                prediction_horizon=self.prediction_horizon,
            )
            features_list.append(feat)

        if not features_list:
            print("[ERROR] 无有效特征数据")
            return None, None, None, []

        combined = pd.concat(features_list, ignore_index=True)
        del features_list

        if self.filter_oscillation:
            label_col = f"label_t{self.prediction_horizon}_binary"
        else:
            label_col = f"label_t{self.prediction_horizon}"

        if label_col not in combined.columns:
            print(f"[ERROR] 标签列 {label_col} 不存在")
            return None, None, None, []

        if self.filter_oscillation:
            valid = combined[label_col].notna()
            total_before = len(combined)
            combined = combined[valid].copy()
            print(f"[LABEL] 过滤震荡日后样本: {len(combined)} (过滤前: {total_before})")
        else:
            combined = combined.dropna(subset=[label_col])

        exclude_patterns = ["label_", "future_ret_", "sample_weight", "code", "date", "_DATE", "DATE"]
        feature_names = [
            c
            for c in combined.columns
            if not any(p in c for p in exclude_patterns)
            and combined[c].dtype in ("float64", "float32", "int64", "int32")
            and combined[c].notna().sum() > len(combined) * 0.5
        ]

        combined[feature_names] = combined[feature_names].fillna(0).astype(np.float32)
        combined[feature_names] = combined[feature_names].replace([np.inf, -np.inf], 0)

        X = combined[feature_names].values
        y = combined[label_col].values.astype(int)
        w = (
            combined["sample_weight"].values.astype(np.float32)
            if "sample_weight" in combined.columns
            else np.ones(len(X), dtype=np.float32)
        )

        del combined

        unique, counts = np.unique(y, return_counts=True)
        print(f"[LABEL] 分布 (horizon=T+{self.prediction_horizon}):")
        for u, c in zip(unique, counts):
            name = {0: "跌", 1: "涨"}.get(u, str(u))
            print(f"  {name}(label={u}): {c:>6} ({c / len(y):.1%})")

        print(f"[FEATURE] 特征数: {len(feature_names)}, 样本数: {len(X)}")

        self.feature_cols = feature_names
        return X, y, w, feature_names

    # ── 特征选择 ──

    def select_features(self, X: np.ndarray, y: np.ndarray, k: int = 30, method: str = "mutual_info") -> np.ndarray:
        """SelectKBest 特征选择"""
        print(f"\n[FEATURE SEL] SelectKBest ({method}), top={k}")

        scorer = mutual_info_classif if method == "mutual_info" else f_classif
        self.feature_selector = SelectKBest(score_func=scorer, k=min(k, X.shape[1]))

        X_sel = self.feature_selector.fit_transform(X, y)
        selected_idx = self.feature_selector.get_support(indices=True)
        self.selected_features = [self.feature_cols[i] for i in selected_idx]
        scores = self.feature_selector.scores_

        print("[FEATURE SEL] Top 15 特征:")
        ranked = sorted(zip(self.feature_cols, scores), key=lambda x: x[1], reverse=True)
        for i, (name, score) in enumerate(ranked[:15], 1):
            star = "★" if name in self.selected_features else " "
            print(f"  {star} {i:2d}. {name:<30} {score:.4f}")

        return X_sel

    # ── 模型训练 ──

    def train_all_models(
        self,
        X: np.ndarray,
        y: np.ndarray,
        sample_weight: np.ndarray = None,
        use_optuna: bool = False,
        n_trials: int = 50,
    ) -> Dict[str, Any]:
        """训练所有模型并评估"""
        print("\n[TRAIN] 训练模型 (时序分割, 80/20)...")
        split_idx = int(len(X) * 0.8)
        X_train, X_test = X[:split_idx], X[split_idx:]
        y_train, y_test = y[:split_idx], y[split_idx:]
        w_train = sample_weight[:split_idx] if sample_weight is not None else None
        sample_weight[split_idx:] if sample_weight is not None else None

        print(f"  [SPLIT] 训练: {len(X_train)} | 测试: {len(X_test)}")
        print(f"  [WEIGHT] 样本加权: {'启用' if sample_weight is not None else '未启用'}")

        # 模型配置
        model_configs = {
            "GradientBoosting": GradientBoostingClassifier(
                n_estimators=500,
                max_depth=6,
                learning_rate=0.03,
                subsample=0.8,
                min_samples_split=10,
                random_state=42,
            ),
            "ExtraTrees": ExtraTreesClassifier(
                n_estimators=500,
                max_depth=12,
                random_state=42,
                n_jobs=-1,
                class_weight="balanced",
                min_samples_split=10,
            ),
            "RandomForest": RandomForestClassifier(
                n_estimators=500,
                max_depth=10,
                random_state=42,
                n_jobs=-1,
                class_weight="balanced",
                min_samples_split=10,
            ),
        }

        if _HAS_XGB:
            model_configs["XGBoost"] = xgb.XGBClassifier(
                n_estimators=500,
                max_depth=6,
                learning_rate=0.03,
                subsample=0.8,
                random_state=42,
                n_jobs=-1,
                eval_metric="logloss",
            )

        if _HAS_LGB:
            model_configs["LightGBM"] = lgb.LGBMClassifier(
                n_estimators=500,
                max_depth=8,
                learning_rate=0.03,
                subsample=0.8,
                random_state=42,
                n_jobs=-1,
                verbose=-1,
                force_col_wise=True,
            )

        results = {}
        best_model_name = None
        best_f1 = 0

        for name, model in model_configs.items():
            print(f"\n  [{name}] 训练...")
            try:
                # 带样本权重训练
                fit_kwargs = {}
                if w_train is not None and hasattr(model, "fit"):
                    fit_kwargs["sample_weight"] = w_train

                model.fit(X_train, y_train, **fit_kwargs)

                y_pred = model.predict(X_test)
                y_prob = model.predict_proba(X_test) if hasattr(model, "predict_proba") else None

                acc = accuracy_score(y_test, y_pred)
                f1 = f1_score(y_test, y_pred, average="binary", zero_division=0)
                auc = roc_auc_score(y_test, y_prob[:, 1]) if y_prob is not None and y_prob.shape[1] > 1 else 0.5
                prec = precision_score(y_test, y_pred, zero_division=0)
                rec = recall_score(y_test, y_pred, zero_division=0)

                print(f"    Acc={acc:.4f}  F1={f1:.4f}  AUC={auc:.4f}  Prec={prec:.4f}  Rec={rec:.4f}")

                self.models[name] = model
                n_test = len(y_test)
                n_correct = int(sum(y_test == y_pred))
                results[name] = {
                    "accuracy": float(acc),
                    "f1": float(f1),
                    "auc": float(auc),
                    "precision": float(prec),
                    "recall": float(rec),
                    "model_path": "",
                    "n_test": n_test,
                    "n_correct": n_correct,
                }

                # P0-2: 统计显著性检验
                try:
                    stat_result = EnhancedMLTrainer.run_statistical_significance_test(
                        y_test, y_pred, y_prob if y_prob is not None else None, n_bootstrap=1000, n_permutation=100
                    )
                    results[name]["statistical_validation"] = stat_result
                except Exception as stat_err:
                    logger.warning(f"[{name}] 统计显著性检验失败: {stat_err}")

                if f1 > best_f1:
                    best_f1 = f1
                    best_model_name = name

            except Exception as e:
                logger.error(f"[{name}] 模型训练失败: {e}")

        self.results = results

        if best_model_name:
            print(f"\n[RESULT] 最佳模型: {best_model_name} (F1={best_f1:.4f})")

        # P0-2 修复: 统计显著性检验
        print(f"\n{'=' * 60}")
        print("  [P0-2] 统计显著性检验")
        for name, _r in results.items():
            if name in self.models:
                model = self.models[name]
                # 从训练循环重新获取 y_pred/y_prob (需要存储中间值)
                # 使用存储的测试集预测结果
                pass  # 由 run_statistical_significance_test 统一处理

        return results

    @staticmethod
    def run_statistical_significance_test(
        y_test: np.ndarray,
        y_pred: np.ndarray,
        y_prob: np.ndarray = None,
        n_bootstrap: int = 1000,
        n_permutation: int = 100,
        random_seed: int = 42,
    ) -> Dict[str, Any]:
        """P0-2 修复: 统计显著性检验 (Rank IC + Bootstrap CI + 置换检验)

        Renaissance标准: 单一信号 t-statistic > 2.0 才纳入组合。

        Args:
            y_test: 真实标签 (0/1)
            y_pred: 模型预测标签 (0/1)
            y_prob: 预测概率 [:, 1] (可选，用于Rank IC)
            n_bootstrap: Bootstrap 抽样次数
            n_permutation: 置换检验次数
            random_seed: 随机种子

        Returns:
            stat_results: 包含 rank_ic, ic_ir, bootstrap_ci, permutation_p 等
        """
        rng = np.random.RandomState(random_seed)
        n = len(y_test)
        acc = accuracy_score(y_test, y_pred)
        f1 = f1_score(y_test, y_pred, average="binary", zero_division=0)
        n_correct = int((y_test == y_pred).sum())

        # ── 1. 二项检验 (Baseline: 50% 抛硬币) ──
        try:
            from scipy.stats import binomtest as _binom_test

            binom_p = _binom_test(n_correct, n, p=0.5, alternative="greater").pvalue
        except ImportError:
            try:
                from scipy.stats import binom_test as _binom_test  # scipy < 1.10

                binom_p = _binom_test(n_correct, n, p=0.5, alternative="greater")
            except ImportError:
                binom_p = None

        # ── 2. Rank IC (Spearman 相关性, 需要概率输出) ──
        rank_ic = None
        ic_ir = None
        if y_prob is not None and len(y_prob) > 1:
            if y_prob.ndim > 1 and y_prob.shape[1] > 1:
                prob_vec = y_prob[:, 1]
            else:
                prob_vec = y_prob.flatten() if y_prob.ndim > 1 else y_prob
            from scipy.stats import spearmanr

            rank_ic, _ = spearmanr(prob_vec, y_test)

        # ── 3. Bootstrap 置信区间 ──
        bootstrap_accs = []
        bootstrap_f1s = []
        for _ in range(n_bootstrap):
            idx = rng.randint(0, n, n)
            bs_acc = accuracy_score(y_test[idx], y_pred[idx])
            bs_f1 = f1_score(y_test[idx], y_pred[idx], average="binary", zero_division=0)
            bootstrap_accs.append(bs_acc)
            bootstrap_f1s.append(bs_f1)
        bootstrap_accs = np.array(bootstrap_accs)
        bootstrap_f1s = np.array(bootstrap_f1s)
        acc_ci_low = float(np.percentile(bootstrap_accs, 2.5))
        acc_ci_high = float(np.percentile(bootstrap_accs, 97.5))
        f1_ci_low = float(np.percentile(bootstrap_f1s, 2.5))
        f1_ci_high = float(np.percentile(bootstrap_f1s, 97.5))

        # ── 4. 置换检验 (简化版: 打乱标签, 快速评估) ──
        from sklearn.ensemble import RandomForestClassifier

        perm_accs = []
        for i in range(n_permutation):
            y_shuffled = rng.permutation(y_test.copy())
            # 使用80/20分割做快速验证
            split_idx = int(n * 0.8)
            rf = RandomForestClassifier(n_estimators=50, max_depth=5, random_state=random_seed + i, n_jobs=-1)
            rf.fit(y_pred[:split_idx].reshape(-1, 1), y_shuffled[:split_idx])
            perm_acc = accuracy_score(y_shuffled[split_idx:], rf.predict(y_pred[split_idx:].reshape(-1, 1)))
            perm_accs.append(perm_acc)
        perm_accs = np.array(perm_accs)
        permutation_p = float((perm_accs >= acc).mean())  # null分布中≥真实准确率的比例

        # ── 5. t统计量检验 ──
        # H0: accuracy = 0.5, H1: accuracy > 0.5
        if n > 1:
            se = np.sqrt(acc * (1 - acc) / n)
            t_stat = (acc - 0.5) / se if se > 0 else 0.0
        else:
            t_stat = 0.0

        # ── 6. IC_IR (若有多期数据可从历史推算, 此处用单期估计) ──
        if rank_ic is not None:
            ic_ir = rank_ic / max(bootstrap_accs.std(), 0.0001)  # 标准差估计

        # ── 7. 显著性判定 ──
        is_significant = (
            (binom_p is not None and binom_p < 0.05) or (permutation_p < 0.05) or (t_stat > 2.0) or (acc_ci_low > 0.5)
        )

        result = {
            "accuracy": float(acc),
            "f1": float(f1),
            "n_test": n,
            "n_correct": n_correct,
            # Rank IC
            "rank_ic": float(rank_ic) if rank_ic is not None else None,
            "ic_ir": float(ic_ir) if ic_ir is not None else None,
            # Bootstrap
            "bootstrap": {
                "n_iterations": n_bootstrap,
                "accuracy_95ci": [acc_ci_low, acc_ci_high],
                "f1_95ci": [f1_ci_low, f1_ci_high],
                "acc_std": float(bootstrap_accs.std()),
            },
            # 统计检验
            "binomial_test": {
                "p_value": float(binom_p) if binom_p is not None else None,
                "null_hypothesis": "accuracy = 0.5 (coin flip)",
            },
            "permutation_test": {
                "n_iterations": n_permutation,
                "p_value": permutation_p,
                "null_mean_accuracy": float(perm_accs.mean()),
                "null_std_accuracy": float(perm_accs.std()),
            },
            "t_statistic": float(t_stat),
            "is_significant": is_significant,
            "verdict": "PASS (显著优于随机)" if is_significant else "FAIL (未能拒绝H0: 准确率=50%)",
        }

        # 打印报告
        print(f"    Rank IC: {result['rank_ic']:.4f}" if result["rank_ic"] else "    Rank IC: N/A (无概率输出)")
        print(f"    IC_IR:   {result['ic_ir']:.4f}" if result["ic_ir"] else "    IC_IR:   N/A")
        print(f"    Acc 95%CI: [{acc_ci_low:.4f}, {acc_ci_high:.4f}]")
        print(f"    二项检验 p={result['binomial_test']['p_value']:.4f}" if binom_p else "    二项检验: N/A")
        print(f"    置换检验 p={permutation_p:.4f} (n={n_permutation})")
        print(f"    t统计量: {t_stat:.2f} (Renaissance标准: > 2.0)")
        print(f"    判定: {result['verdict']}")

        return result

    # ── Optuna 优化 ──

    def optimize_with_optuna(
        self, X: np.ndarray, y: np.ndarray, sample_weight: np.ndarray = None, n_trials: int = 50
    ) -> Dict[str, Any]:
        """使用 Optuna 进行贝叶斯超参数优化"""
        if not _HAS_OPTUNA:
            print("[WARN] Optuna 未安装，跳过优化")
            return self.train_all_models(X, y, sample_weight)

        print(f"\n[OPTUNA] 贝叶斯优化 (n_trials={n_trials})...")

        split_idx = int(len(X) * 0.8)
        X_train, X_test = X[:split_idx], X[split_idx:]
        y_train, y_test = y[:split_idx], y[split_idx:]
        w_train = sample_weight[:split_idx] if sample_weight is not None else None
        sample_weight[split_idx:] if sample_weight is not None else None

        # 优化目标函数 — 风险约束版本
        def objective(trial, model_type="lightgbm"):
            if model_type == "lightgbm" and _HAS_LGB:
                params = {
                    "n_estimators": trial.suggest_int("n_estimators", 100, 600),
                    "max_depth": trial.suggest_int("max_depth", 2, 8),
                    "num_leaves": trial.suggest_int("num_leaves", 8, 64),
                    "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.15, log=True),
                    "subsample": trial.suggest_float("subsample", 0.6, 1.0),
                    "colsample_bytree": trial.suggest_float("colsample_bytree", 0.6, 1.0),
                    "min_child_samples": trial.suggest_int("min_child_samples", 10, 50),
                    "reg_alpha": trial.suggest_float("reg_alpha", 0.1, 5.0),
                    "reg_lambda": trial.suggest_float("reg_lambda", 1.0, 10.0, log=True),
                    "random_state": 42,
                    "n_jobs": -1,
                    "verbose": -1,
                    "class_weight": "balanced",
                }
                model = lgb.LGBMClassifier(**params)
            elif model_type == "xgboost" and _HAS_XGB:
                params = {
                    "n_estimators": trial.suggest_int("n_estimators", 100, 500),
                    "max_depth": trial.suggest_int("max_depth", 2, 8),
                    "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.15, log=True),
                    "subsample": trial.suggest_float("subsample", 0.6, 1.0),
                    "colsample_bytree": trial.suggest_float("colsample_bytree", 0.6, 1.0),
                    "min_child_weight": trial.suggest_int("min_child_weight", 3, 15),
                    "gamma": trial.suggest_float("gamma", 0, 2),
                    "reg_alpha": trial.suggest_float("reg_alpha", 0.1, 5.0),
                    "reg_lambda": trial.suggest_float("reg_lambda", 1.0, 10.0, log=True),
                    "random_state": 42,
                    "n_jobs": -1,
                    "eval_metric": "logloss",
                    "scale_pos_weight": 1.0,
                }
                model = xgb.XGBClassifier(**params)
            else:
                params = {
                    "n_estimators": trial.suggest_int("n_estimators", 100, 500),
                    "max_depth": trial.suggest_int("max_depth", 2, 8),
                    "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.15, log=True),
                    "subsample": trial.suggest_float("subsample", 0.6, 1.0),
                    "min_samples_split": trial.suggest_int("min_samples_split", 10, 30),
                    "random_state": 42,
                }
                model = GradientBoostingClassifier(**params)

            # P0-4 FIX: 使用 Purged K-Fold 替代标准 TimeSeriesSplit
            # 消除标签重叠泄漏: train/test间禁运期防止未来信息污染
            if self.use_purged_cv and _HAS_PURGED_CV:
                cv_splits = purged_timeseries_split(
                    len(X_train), n_splits=min(5, self.n_cv_splits), embargo_pct=0.01, min_train_pct=0.3
                )
            else:
                # 回退方案: 标准 TimeSeriesSplit
                tscv = TimeSeriesSplit(n_splits=3)
                cv_splits = list(tscv.split(X_train))
            f1_scores = []
            precision_scores = []
            prob_std_scores = []

            for ti, vi in cv_splits:
                fit_kwargs = {}
                if w_train is not None:
                    fit_kwargs["sample_weight"] = w_train[ti]
                model.fit(X_train[ti], y_train[ti], **fit_kwargs)
                y_vp = model.predict(X_train[vi])
                y_vprob = model.predict_proba(X_train[vi])[:, 1]

                f1 = f1_score(y_train[vi], y_vp, average="binary", zero_division=0)
                prec = precision_score(y_train[vi], y_vp, zero_division=0)
                prob_std = np.std(y_vprob)

                f1_scores.append(f1)
                precision_scores.append(prec)
                prob_std_scores.append(prob_std)

            avg_f1 = np.mean(f1_scores)
            avg_precision = np.mean(precision_scores)
            avg_prob_std = np.mean(prob_std_scores)

            risk_penalty = avg_prob_std * 0.5

            combined_score = 0.6 * avg_f1 + 0.3 * avg_precision - risk_penalty

            return combined_score

        # 并行优化多个模型
        optuna_results = {}
        model_types = []
        if _HAS_LGB:
            model_types.append("lightgbm")
        if _HAS_XGB:
            model_types.append("xgboost")
        model_types.append("gradient_boosting")

        for mt in model_types:
            print(f"\n  🔍 Optuna 优化 {mt}...")
            sampler = TPESampler(seed=42)
            pruner = MedianPruner(n_startup_trials=5, n_warmup_steps=3)
            study = optuna.create_study(direction="maximize", sampler=sampler, pruner=pruner)

            study.optimize(
                lambda trial, _mt=mt: objective(trial, _mt),
                n_trials=n_trials,
                show_progress_bar=False,
            )

            # 用最佳参数训练最终模型
            best_params = study.best_params.copy()
            best_params["random_state"] = 42

            if mt == "lightgbm" and _HAS_LGB:
                best_params.update({"n_jobs": -1, "verbose": -1})
                final_model = lgb.LGBMClassifier(**best_params)
            elif mt == "xgboost" and _HAS_XGB:
                best_params.update({"n_jobs": -1, "eval_metric": "logloss"})
                final_model = xgb.XGBClassifier(**best_params)
            else:
                final_model = GradientBoostingClassifier(**best_params)

            fit_kwargs = {}
            if w_train is not None:
                fit_kwargs["sample_weight"] = w_train
            final_model.fit(X_train, y_train, **fit_kwargs)

            y_pred = final_model.predict(X_test)
            y_prob = final_model.predict_proba(X_test) if hasattr(final_model, "predict_proba") else None

            f1 = f1_score(y_test, y_pred, average="binary", zero_division=0)
            auc = roc_auc_score(y_test, y_prob[:, 1]) if y_prob is not None and y_prob.shape[1] > 1 else 0.5

            model_name = f"{mt}_optuna"
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            model_path = os.path.join(self.output_dir, f"{model_name}_{ts}.pkl")
            joblib.dump(final_model, model_path)

            optuna_results[model_name] = {
                "accuracy": float(accuracy_score(y_test, y_pred)),
                "f1": float(f1),
                "auc": float(auc),
                "best_params": best_params,
                "best_cv_f1": float(study.best_value),
                "n_trials": len(study.trials),
                "model_path": model_path,
            }

            # P0-2: 统计显著性检验
            try:
                stat_result = EnhancedMLTrainer.run_statistical_significance_test(
                    y_test, y_pred, y_prob if y_prob is not None else None, n_bootstrap=1000, n_permutation=100
                )
                optuna_results[model_name]["statistical_validation"] = stat_result
            except Exception as e:
                logger.warning(f"[{model_name}] Optuna统计显著性检验失败: {e}")

            self.models[model_name] = final_model

            print(f"    F1={f1:.4f}  AUC={auc:.4f}  Best_CV_F1={study.best_value:.4f}")

        # 合并到总结果
        self.results.update(optuna_results)
        return self.results

    # ── 保存 ──

    def save_all(self, feature_names: List[str]) -> str:
        """保存所有模型和元数据"""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        # 保存每个模型
        for name, model in self.models.items():
            if name not in [k for k, v in self.results.items() if v.get("model_path")]:
                model_path = os.path.join(self.output_dir, f"{name}_{ts}.pkl")
                joblib.dump(model, model_path)
                if name in self.results:
                    self.results[name]["model_path"] = model_path

        # 保存特征选择器
        if self.feature_selector is not None:
            sel_path = os.path.join(self.output_dir, f"feature_selector_{ts}.pkl")
            joblib.dump(self.feature_selector, sel_path)

        # 保存Scaler和PCA
        if self.scaler is not None:
            scaler_path = os.path.join(self.output_dir, f"scaler_{ts}.pkl")
            joblib.dump(self.scaler, scaler_path)
        if self.pca is not None:
            pca_path = os.path.join(self.output_dir, f"pca_{ts}.pkl")
            joblib.dump(self.pca, pca_path)

        # 保存元数据
        best_model_name = max(self.results, key=lambda k: self.results[k].get("f1", 0)) if self.results else "unknown"

        meta = {
            "timestamp": ts,
            "best_model": best_model_name,
            "features": self.selected_features if self.selected_features else feature_names,
            "feature_count": len(self.selected_features or feature_names),
            "results": self.results,
            "config": {
                "label_threshold": self.label_threshold,
                "prediction_horizon": self.prediction_horizon,
                "filter_oscillation": self.filter_oscillation,
                "has_sample_weight": True,
                "use_purged_cv": self.use_purged_cv,
                "feature_version": "v2.0_enhanced",
                "p0_3_fix": "PurgedWalkForward validation available via utils.purged_cv",
            },
        }

        meta_path = os.path.join(self.output_dir, f"training_metadata_enhanced_v2_{ts}.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2, default=str)

        print(f"\n[SAVE] 元数据: {meta_path}")
        return meta_path


def run_enhanced_training(
    data_dir: str = "data/cache",
    model_dir: str = "models",
    prediction_horizon: int = 1,
    filter_oscillation: bool = True,
    use_optuna: bool = False,
    n_trials: int = 50,
    n_features: int = 30,
    northbound_path: Optional[str] = None,
    fundamentals_path: Optional[str] = None,
    use_purged_cv: bool = True,
) -> Dict[str, Any]:
    """
    一键运行增强训练管线

    Args:
        data_dir: K线缓存目录
        model_dir: 模型输出目录
        prediction_horizon: 预测窗口 (1/5/10)
        filter_oscillation: 过滤震荡日
        use_optuna: 是否使用Optuna优化
        n_trials: Optuna试验次数
        n_features: SelectKBest保留特征数
        northbound_path: 北向资金数据路径
        fundamentals_path: 基本面数据路径
        use_purged_cv: 使用 Purged K-Fold 验证 (P0-3, 默认True)
    """
    trainer = EnhancedMLTrainer(
        output_dir=model_dir,
        prediction_horizon=prediction_horizon,
        filter_oscillation=filter_oscillation,
        use_purged_cv=use_purged_cv,
    )

    # 可自定义基本面路径
    if fundamentals_path:
        trainer.feature_engineer = EnhancedFeatureEngineer(
            data_dir=data_dir,
            fundamentals_path=fundamentals_path,
        )

    # 加载数据
    combined, market_data = trainer.load_all_data(data_dir)
    if combined is None:
        return {"error": "数据加载失败"}

    # 加载北向资金
    northbound = trainer.load_northbound_data(northbound_path)

    # 准备数据集
    X, y, w, _feature_names = trainer.prepare_dataset(combined, market_data, northbound)
    if X is None:
        return {"error": "特征构建失败"}

    # P0-3 修复：先进行时间序列分割，再在训练集上进行特征选择（避免数据泄漏）
    split_idx = int(len(X) * 0.8)
    X_train, X_test = X[:split_idx], X[split_idx:]
    y_train, _y_test = y[:split_idx], y[split_idx:]
    w_train = w[:split_idx] if w is not None else None
    print(f"  [SPLIT] 训练: {len(X_train)} | 测试: {len(X_test)}")

    # 特征选择（仅在训练集上进行）
    X_train_sel = trainer.select_features(X_train, y_train, k=n_features)
    X_test_sel = trainer.feature_selector.transform(X_test)
    np.vstack([X_train_sel, X_test_sel])
    print(f"  [FEATURE SEL] 完成: {X_train_sel.shape[1]} 特征")

    # 训练
    if use_optuna:
        trainer.optimize_with_optuna(X_train_sel, y_train, w_train, n_trials=n_trials)
    else:
        trainer.train_all_models(X_train_sel, y_train, w_train)

    # 保存
    meta_path = trainer.save_all(trainer.selected_features)

    # 返回摘要
    best_name = max(trainer.results, key=lambda k: trainer.results[k].get("f1", 0))
    best = trainer.results[best_name]

    return {
        "success": True,
        "meta_path": meta_path,
        "best_model": best_name,
        "best_f1": best["f1"],
        "best_auc": best.get("auc", 0),
        "best_accuracy": best.get("accuracy", 0),
        "n_features": len(trainer.selected_features),
        "n_samples": len(X),
        "prediction_horizon": prediction_horizon,
        "filter_oscillation": filter_oscillation,
        "results": {k: {"f1": v["f1"], "auc": v.get("auc", 0)} for k, v in trainer.results.items()},
    }


# ═══════════════════════════════════════════════════════════════
# 快速测试
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="ML增强训练引擎 v2.0")
    parser.add_argument("--data-dir", default="data/cache", help="K线缓存目录")
    parser.add_argument("--model-dir", default="models", help="模型输出目录")
    parser.add_argument("--horizon", type=int, default=1, choices=[1, 5, 10], help="预测窗口 T+N")
    parser.add_argument("--filter-oscillation", action="store_true", default=True, help="过滤震荡日")
    parser.add_argument("--optuna", action="store_true", help="使用Optuna优化")
    parser.add_argument("--trials", type=int, default=50, help="Optuna试验次数")
    parser.add_argument("--features", type=int, default=30, help="SelectKBest特征数")
    parser.add_argument("--northbound", type=str, default=None, help="北向资金数据路径")
    parser.add_argument("--fundamentals", type=str, default=None, help="基本面数据路径")

    args = parser.parse_args()

    print("=" * 70)
    print("  ML 增强训练引擎 v2.0")
    print(
        "  "
        + " | ".join(
            [
                f"窗口=T+{args.horizon}",
                f"过滤震荡={args.filter_oscillation}",
                f"Optuna={args.optuna}",
                f"特征数={args.features}",
            ]
        )
    )
    print("=" * 70)

    result = run_enhanced_training(
        data_dir=args.data_dir,
        model_dir=args.model_dir,
        prediction_horizon=args.horizon,
        filter_oscillation=args.filter_oscillation,
        use_optuna=args.optuna,
        n_trials=args.trials,
        n_features=args.features,
        northbound_path=args.northbound,
        fundamentals_path=args.fundamentals,
    )

    if "error" in result:
        print(f"\n❌ {result['error']}")
    else:
        print("\n✅ 训练完成!")
        print(f"  最佳模型: {result['best_model']}")
        print(f"  F1: {result['best_f1']:.4f}")
        print(f"  AUC: {result['best_auc']:.4f}")
        print(f"  准确率: {result['best_accuracy']:.4f}")
        print(f"  特征数: {result['n_features']}")
        print(f"  样本数: {result['n_samples']}")
        print(f"  元数据: {result['meta_path']}")
