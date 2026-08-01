"""
v7.5 Qlib 信号适配器

将微软 Qlib 的模型预测信号接入 v7.5 信号融合系统。

架构：
    Qlib (模型训练 + 预测)
        ↓ 本适配器 (数据桥接 + 信号标准化)
    SignalFusion.inject_qlib_signal()
        ↓
    v7.5 订单生成 / 对冲决策

设计原则：
    1. 优雅降级：Qlib 不可用时自动回退到本地 LightGBM
    2. 零侵入：不改动 Qlib 源码，仅通过标准接口交互
    3. 信号标准化：输出 pd.Series[-1, 1]，与现有信号源对齐
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from typing import Any, Dict, Optional

import pandas as pd

logger = logging.getLogger("v75.qlib.adapter")

# Qlib 路径
QLIB_ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..", "qlib")
if QLIB_ROOT not in sys.path:
    sys.path.insert(0, os.path.abspath(QLIB_ROOT))

# Qlib 可用性标记
_QLIB_AVAILABLE = False
_qlib_init_error: Optional[str] = None

try:
    import qlib
    from qlib.config import REG_CN  # noqa: F401
    from qlib.data.dataset import DatasetH
    from qlib.data.dataset.handler import DataHandlerLP  # noqa: F401

    _QLIB_AVAILABLE = True
    logger.info("Qlib 基础模块导入成功")
except ImportError as e:
    _qlib_init_error = str(e)
    logger.warning(f"Qlib 不可用，将回退到本地模型: {e}")


_LSTM_AVAILABLE = False
_LGB_MODEL_AVAILABLE = False
_TRANSFORMER_AVAILABLE = False

LSTM = None
LGBModel = None
Transformer = None

if _QLIB_AVAILABLE:
    try:
        from qlib.contrib.model.lightgbm import LGBModel

        _LGB_MODEL_AVAILABLE = True
        logger.info("Qlib LightGBM 模型导入成功")
    except Exception as e:
        logger.warning(f"Qlib LightGBM 模型导入失败: {e}")

    try:
        from qlib.contrib.model.pytorch_lstm import LSTM

        _LSTM_AVAILABLE = True
        logger.info("Qlib LSTM 模型导入成功")
    except Exception as e:
        logger.warning(f"Qlib LSTM 模型导入失败 (PyTorch可能不可用): {e}")

    try:
        from qlib.contrib.model.pytorch_transformer import Transformer

        _TRANSFORMER_AVAILABLE = True
        logger.info("Qlib Transformer 模型导入成功")
    except Exception as e:
        logger.warning(f"Qlib Transformer 模型导入失败: {e}")


# ============================================================
# 配置
# ============================================================

DEFAULT_QLIB_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "qlib_data")

# 可用模型映射
MODEL_MAP = {
    "lstm": LSTM if _LSTM_AVAILABLE else None,
    "lightgbm": LGBModel if _LGB_MODEL_AVAILABLE else None,
    "transformer": Transformer if _TRANSFORMER_AVAILABLE else None,
}

# 默认模型
DEFAULT_MODEL = "lightgbm"


# ============================================================
# 初始化
# ============================================================


def init_qlib(provider_uri: Optional[str] = None, region: str = "cn") -> bool:
    """
    安全初始化 Qlib

    Args:
        provider_uri: Qlib 数据目录 (binit 格式)
        region: 市场区域

    Returns:
        True 表示初始化成功
    """
    if not _QLIB_AVAILABLE:
        logger.warning(f"Qlib 不可用: {_qlib_init_error}")
        return False

    try:
        uri = provider_uri or os.environ.get("QLIB_PROVIDER_URI", DEFAULT_QLIB_DIR)
        qlib.init(provider_uri=uri, region=region)
        logger.info(f"Qlib 初始化成功: uri={uri}")
        return True
    except Exception as e:
        logger.error(f"Qlib 初始化失败: {e}")
        return False


def is_qlib_available() -> bool:
    """检查 Qlib 是否可用"""
    return _QLIB_AVAILABLE


# ============================================================
# 数据转换
# ============================================================


def v75_to_qlib_features(df: pd.DataFrame, symbol: str, feature_cols: Optional[list] = None) -> pd.DataFrame:
    """
    将 v7.5 格式 DataFrame 转换为 Qlib 特征格式

    Qlib 期望列名: open, high, low, close, volume, vwap, ...

    Args:
        df: v7.5 数据 DataFrame，需包含 datetime index
        symbol: 标的代码
        feature_cols: 使用的特征列

    Returns:
        Qlib 格式 DataFrame
    """
    if feature_cols is None:
        feature_cols = ["open", "high", "low", "close", "volume"]

    # 复制并标准化列名
    qlib_df = df.copy()

    # 列名映射 (v7.5 → Qlib)
    col_mapping = {
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "volume": "volume",
    }

    for old_col, new_col in col_mapping.items():
        if old_col in qlib_df.columns and old_col != new_col:
            qlib_df.rename(columns={old_col: new_col}, inplace=True)

    # 添加 instrument 列 (Qlib 需要)
    qlib_df["instrument"] = symbol

    # 确保 datetime index
    if not isinstance(qlib_df.index, pd.DatetimeIndex):
        if "datetime" in qlib_df.columns:
            qlib_df.set_index("datetime", inplace=True)
        elif "date" in qlib_df.columns:
            qlib_df.set_index("date", inplace=True)

    # 只保留需要的列
    keep_cols = [c for c in feature_cols if c in qlib_df.columns] + ["instrument"]
    qlib_df = qlib_df[keep_cols].copy()

    return qlib_df


def prepare_qlib_dataset(
    df: pd.DataFrame,
    symbol: str,
    train_start: str,
    train_end: str,
    valid_start: str,
    valid_end: str,
    test_start: str,
    test_end: str,
    feature_cols: Optional[list] = None,
) -> Optional[DatasetH]:
    """
    准备 Qlib DatasetH

    Args:
        df: v7.5 数据
        symbol: 标的代码
        train/valid/test_start/end: 时间区间
        feature_cols: 特征列

    Returns:
        DatasetH 或 None (失败时)
    """
    if not _QLIB_AVAILABLE:
        logger.warning("Qlib 不可用，无法准备数据集")
        return None

    try:
        v75_to_qlib_features(df, symbol, feature_cols)

        handler = {
            "class": "Alpha158",
            "module_path": "qlib.contrib.data.handler",
            "kwargs": {
                "instruments": symbol,
                "start_time": train_start,
                "end_time": test_end,
            },
        }

        segments = {
            "train": (train_start, train_end),
            "valid": (valid_start, valid_end),
            "test": (test_start, test_end),
        }

        dataset = DatasetH(handler=handler, segments=segments)
        logger.info(f"Qlib 数据集准备完成: {symbol}, 特征数={len(handler['kwargs'])}")
        return dataset

    except Exception as e:
        logger.error(f"Qlib 数据集准备失败: {e}")
        return None


# ============================================================
# 模型训练与预测
# ============================================================


def train_qlib_model(dataset: DatasetH, model_type: str = DEFAULT_MODEL, **model_kwargs) -> Optional[Any]:
    """
    训练 Qlib 模型

    Args:
        dataset: Qlib DatasetH
        model_type: 模型类型 ('lstm', 'lightgbm', 'transformer')
        **model_kwargs: 模型参数

    Returns:
        训练好的模型或 None
    """
    if not _QLIB_AVAILABLE:
        logger.warning("Qlib 不可用，无法训练模型")
        return None

    try:
        model_class = MODEL_MAP.get(model_type)
        if model_class is None:
            logger.error(f"未知模型类型: {model_type}")
            return None

        model = model_class(**model_kwargs)
        model.fit(dataset)
        logger.info(f"Qlib {model_type} 模型训练完成")
        return model

    except Exception as e:
        logger.error(f"Qlib 模型训练失败: {e}")
        return None


def predict_qlib_signal(model: Any, dataset: DatasetH, segment: str = "test") -> Optional[pd.Series]:
    """
    使用 Qlib 模型生成信号

    Args:
        model: 训练好的 Qlib 模型
        dataset: Qlib DatasetH
        segment: 预测区间 ('train', 'valid', 'test')

    Returns:
        标准化信号 pd.Series[-1, 1]，或 None
    """
    if not _QLIB_AVAILABLE or model is None:
        return None

    try:
        pred = model.predict(dataset, segment=segment)

        # 标准化到 [-1, 1]
        if isinstance(pred, pd.Series):
            signal = pred.copy()
        elif isinstance(pred, pd.DataFrame):
            signal = pred.iloc[:, 0].copy() if len(pred.columns) > 0 else pd.Series()
        else:
            signal = pd.Series(pred)

        # Min-Max 标准化到 [-1, 1]
        if len(signal) > 0 and signal.std() > 0:
            signal = 2 * (signal - signal.min()) / (signal.max() - signal.min()) - 1

        signal.name = "qlib_signal"
        logger.info(f"Qlib 信号生成完成: mean={signal.mean():.4f}, std={signal.std():.4f}")
        return signal

    except Exception as e:
        logger.error(f"Qlib 信号预测失败: {e}")
        return None


# ============================================================
# iFinD 数据获取
# ============================================================


def fetch_ifind_historical(symbol: str, days: int = 120) -> Optional[pd.DataFrame]:
    """
    从 iFinD MCP 获取历史 OHLCV 数据

    Args:
        symbol: 标的代码 (如 '601088', '000001')
        days: 获取天数

    Returns:
        DataFrame with columns [open, high, low, close, volume] 或 None
    """
    try:
        # 尝试导入 iFinD MCP 客户端
        import importlib.util

        # 计算 utils 目录绝对路径
        _qlib_adapter_dir = os.path.dirname(os.path.abspath(__file__))
        _project_root = os.path.abspath(os.path.join(_qlib_adapter_dir, "..", "..", ".."))
        _utils_dir = os.path.join(_project_root, "utils")

        # 将 utils 加入 sys.path，确保 data_provider 内部的 from utils.xxx 能找到
        if _utils_dir not in sys.path:
            sys.path.insert(0, _utils_dir)

        # 同时将 project_root 加入 sys.path，确保顶层模块可导入
        if _project_root not in sys.path:
            sys.path.insert(0, _project_root)

        data_provider_path = os.path.join(_utils_dir, "data_provider.py")

        spec = importlib.util.spec_from_file_location("data_provider", data_provider_path)
        if spec and spec.loader:
            data_provider = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(data_provider)
            MarketDataProvider = data_provider.MarketDataProvider
        else:
            raise ImportError(f"无法加载 data_provider: {data_provider_path}")

        # 根据 days 选择 period，支持更长周期
        if days <= 20:
            period = "1m"
        elif days <= 60:
            period = "3m"
        elif days <= 120:
            period = "6m"
        elif days <= 252:
            period = "1y"
        elif days <= 504:
            period = "2y"
        elif days <= 756:
            period = "3y"
        else:
            period = "5y"

        provider = MarketDataProvider()
        df = provider.get_historical_data(symbol, period=period)

        if df is None or df.empty:
            logger.warning(f"iFinD 未返回数据: {symbol}")
            return None

        # 标准化列名
        col_mapping = {
            "open": "open",
            "Open": "open",
            "high": "high",
            "High": "high",
            "low": "low",
            "Low": "low",
            "close": "close",
            "Close": "close",
            "volume": "volume",
            "Volume": "volume",
            "日期": "date",
            "date": "date",
            "Date": "date",
        }

        df = df.copy()
        for old, new in col_mapping.items():
            if old in df.columns and old != new:
                df.rename(columns={old: new}, inplace=True)

        # 确保 datetime index
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            df.set_index("date", inplace=True)
        elif not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)

        # 只保留最近 N 天
        if len(df) > days:
            df = df.tail(days)

        # 确保 OHLCV 列存在
        required_cols = ["open", "high", "low", "close", "volume"]
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            logger.warning(f"iFinD 数据缺少列 {missing}: {symbol}")
            return None

        # 转换数据类型
        for col in required_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df[required_cols].dropna()
        if len(df) < 60:
            logger.warning(f"iFinD 数据量不足: {symbol} ({len(df)} 行)")
            return None

        logger.info(f"iFinD 数据获取成功: {symbol}, {len(df)} 行")
        return df

    except Exception as e:
        logger.warning(f"iFinD 获取历史数据失败 [{symbol}]: {e}")
        return None


def fetch_ifind_ohlcv(symbol: str, days: int = 120) -> Optional[pd.DataFrame]:
    """
    便捷接口：获取 iFinD 历史数据 (与 v7.5 DataProvider 对齐)

    Args:
        symbol: 标的代码
        days: 天数

    Returns:
        DataFrame 或 None
    """
    return fetch_ifind_historical(symbol, days)


def generate_qlib_signal(
    df: pd.DataFrame, symbol: str, model_type: str = DEFAULT_MODEL, retrain: bool = False, **kwargs
) -> Optional[pd.Series]:
    """
    一键生成 Qlib 信号（训练 + 预测）

    Args:
        df: v7.5 数据 DataFrame，需包含 datetime index 和 OHLCV
        symbol: 标的代码
        model_type: 模型类型
        retrain: 是否重新训练
        **kwargs: 传递给 train_qlib_model 的参数

    Returns:
        标准化信号 pd.Series[-1, 1]，或 None (Qlib 不可用)
    """
    if not _QLIB_AVAILABLE:
        logger.warning("Qlib 不可用，返回 None")
        return None

    # 时间区间划分 (默认 70% train, 15% valid, 15% test)
    n = len(df)
    if n < 100:
        logger.warning(f"数据量不足: {n} 行，需要至少 100 行")
        return None

    train_end = int(n * 0.70)
    valid_end = int(n * 0.85)

    dates = df.index
    train_start = str(dates[0].date())
    train_end = str(dates[train_end].date())
    valid_start = train_end
    valid_end = str(dates[valid_end].date())
    test_start = valid_end
    test_end = str(dates[-1].date())

    # 准备数据集
    dataset = prepare_qlib_dataset(df, symbol, train_start, train_end, valid_start, valid_end, test_start, test_end)
    if dataset is None:
        return None

    # 训练模型
    model = train_qlib_model(dataset, model_type=model_type, **kwargs)
    if model is None:
        return None

    # 预测信号
    signal = predict_qlib_signal(model, dataset, segment="test")
    return signal


# ============================================================
# 本地回退模型 (Qlib 不可用时使用)
# ============================================================

import json  # noqa: E402
from pathlib import Path  # noqa: E402

import joblib  # noqa: E402

# 模型保存目录
MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "models" / "qlib_local"
MODEL_DIR.mkdir(parents=True, exist_ok=True)


def _add_technical_features(data: pd.DataFrame) -> pd.DataFrame:
    """
    添加技术指标特征

    Args:
        data: 包含 OHLCV 的 DataFrame，需有 datetime index

    Returns:
        添加了技术指标的 DataFrame
    """
    df = data.copy()

    # ---------- 移动平均线 ----------
    df["ma5"] = df["close"].rolling(5).mean()
    df["ma10"] = df["close"].rolling(10).mean()
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma60"] = df["close"].rolling(60).mean()

    # ---------- MACD ----------
    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    # ---------- RSI ----------
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0).rolling(6).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(6).mean()
    rs = gain / (loss + 1e-10)
    df["rsi6"] = 100 - (100 / (1 + rs))

    gain = delta.where(delta > 0, 0).rolling(12).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(12).mean()
    rs = gain / (loss + 1e-10)
    df["rsi12"] = 100 - (100 / (1 + rs))

    gain = delta.where(delta > 0, 0).rolling(24).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(24).mean()
    rs = gain / (loss + 1e-10)
    df["rsi24"] = 100 - (100 / (1 + rs))

    # ---------- 布林带 ----------
    boll_mid = df["close"].rolling(20).mean()
    boll_std = df["close"].rolling(20).std()
    df["boll_upper"] = boll_mid + 2 * boll_std
    df["boll_mid"] = boll_mid
    df["boll_lower"] = boll_mid - 2 * boll_std
    df["boll_width"] = (df["boll_upper"] - df["boll_lower"]) / (boll_mid + 1e-10)

    # ---------- ATR ----------
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean()

    # ---------- OBV ----------
    obv = (df["volume"] * df["close"].pct_change().where(df["close"].diff() > 0, -1)).cumsum()
    df["obv"] = obv

    # ---------- 量比（量能变化率）----------
    df["vol_ratio_5"] = df["volume"] / (df["volume"].rolling(5).mean() + 1e-10)
    df["vol_ratio_20"] = df["volume"] / (df["volume"].rolling(20).mean() + 1e-10)

    # ---------- 价格位置（距均线偏离度）----------
    df["price_vs_ma5"] = (df["close"] - df["ma5"]) / (df["ma5"] + 1e-10)
    df["price_vs_ma20"] = (df["close"] - df["ma20"]) / (df["ma20"] + 1e-10)
    df["price_vs_ma60"] = (df["close"] - df["ma60"]) / (df["ma60"] + 1e-10)

    # ---------- 波动率特征 ----------
    df["volatility_5"] = df["close"].pct_change().rolling(5).std()
    df["volatility_20"] = df["close"].pct_change().rolling(20).std()
    df["atr_ratio"] = df["atr"] / (df["close"] + 1e-10)

    # ---------- 量价背离特征 ----------
    df["price_change_5"] = df["close"].pct_change(5)
    df["volume_change_5"] = df["volume"].pct_change(5)
    df["price_volume_divergence"] = df["price_change_5"] - df["volume_change_5"]

    # ---------- 高低价位置 ----------
    df["high_low_ratio"] = (df["high"] - df["low"]) / (df["close"] + 1e-10)
    df["close_position"] = (df["close"] - df["low"]) / ((df["high"] - df["low"]) + 1e-10)

    # 修复 BUG-R4: 删除 bfill(), 仅用 ffill()
    # bfill() 会用未来数据填充历史 NaN, 引入前视偏差
    # rolling 窗口前的 NaN 应丢弃而非后向填充
    df = df.ffill()

    return df


def _local_lightgbm_signal(
    df: pd.DataFrame,
    symbol: str,
    feature_cols: Optional[list] = None,
    save_model: bool = False,
    model_path: Optional[str] = None,
) -> Dict:
    """
    本地 LightGBM 信号生成 (Qlib 不可用时的回退方案)

    使用简单的 LightGBM 回归预测未来收益率

    Returns:
        {
            'signal': pd.Series[-1, 1],
            'model': trained LGBM model (or None),
            'metrics': dict with train/test metrics
        }
    """
    try:
        import lightgbm as lgb
    except ImportError:
        logger.error("LightGBM 未安装")
        return {"signal": None, "model": None, "metrics": {}}

    # 默认使用增强特征（OHLCV + 技术指标）
    if feature_cols is None:
        feature_cols = [
            # OHLCV 基础特征
            "open",
            "high",
            "low",
            "close",
            "volume",
            # 技术指标特征
            "ma5",
            "ma10",
            "ma20",
            "ma60",
            "macd",
            "macd_signal",
            "macd_hist",
            "rsi6",
            "rsi12",
            "rsi24",
            "boll_upper",
            "boll_mid",
            "boll_lower",
            "boll_width",
            "atr",
            "obv",
            # 量比特征
            "vol_ratio_5",
            "vol_ratio_20",
            # 价格位置特征
            "price_vs_ma5",
            "price_vs_ma20",
            "price_vs_ma60",
            # 波动率特征
            "volatility_5",
            "volatility_20",
            "atr_ratio",
            # 量价背离特征
            "price_change_5",
            "volume_change_5",
            "price_volume_divergence",
            # 高低价位置
            "high_low_ratio",
            "close_position",
        ]

    try:
        # 准备特征
        data = df.copy()

        # ============================================================
        # 特征工程：技术指标
        # ============================================================
        data = _add_technical_features(data)

        # 预测目标：次日收益率
        data["target"] = data["close"].pct_change().shift(-1)
        data = data.dropna(subset=[*feature_cols, "target"])

        if len(data) < 50:
            logger.warning(f"数据量不足: {len(data)} 行")
            return {"signal": None, "model": None, "metrics": {}}

        X = data[feature_cols].values
        y = data["target"].values

        # 训练/测试分割
        split = int(len(X) * 0.8)
        X_train, X_test = X[:split], X[split:]
        y_train, y_test = y[:split], y[split:]

        # 训练 - 添加正则化防止过拟合
        model = lgb.LGBMRegressor(
            n_estimators=100,
            learning_rate=0.05,  # 降低学习率
            max_depth=3,  # 降低树深度
            num_leaves=15,  # 减少叶子节点
            min_child_samples=20,  # 增加最小样本数
            subsample=0.8,  # 行采样
            colsample_bytree=0.8,  # 列采样
            reg_alpha=0.1,  # L1 正则
            reg_lambda=0.1,  # L2 正则
            verbose=-1,
        )
        model.fit(X_train, y_train)

        # 预测
        pred = model.predict(X_test)

        # 计算指标
        train_score = model.score(X_train, y_train)
        test_score = model.score(X_test, y_test)

        # 标准化到 [-1, 1]
        signal = pd.Series(pred, index=data.index[split:])
        if signal.std() > 0:
            signal = 2 * (signal - signal.min()) / (signal.max() - signal.min()) - 1

        signal.name = "local_lgb_signal"

        metrics = {
            "train_r2": round(float(train_score), 4),
            "test_r2": round(float(test_score), 4),
            "data_points": len(data),
            "feature_cols": feature_cols,
        }

        # 保存模型
        if save_model:
            save_path = model_path or str(MODEL_DIR / f"{symbol}_lgb_model.pkl")
            joblib.dump(model, save_path)
            # 保存元数据
            meta_path = str(MODEL_DIR / f"{symbol}_lgb_meta.json")
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "symbol": symbol,
                        "feature_cols": feature_cols,
                        "metrics": metrics,
                        "model_path": save_path,
                        "trained_at": datetime.now().isoformat(),
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            logger.info(f"模型已保存: {save_path}")

        logger.info(f"本地 LightGBM 训练完成 [{symbol}]: train_r2={train_score:.4f}, test_r2={test_score:.4f}")

        return {
            "signal": signal,
            "model": model,
            "metrics": metrics,
        }

    except Exception as e:
        logger.error(f"本地 LightGBM 信号生成失败: {e}")
        return {"signal": None, "model": None, "metrics": {}}


def load_local_model(symbol: str) -> Optional[Dict]:
    """
    加载已训练的本地 LightGBM 模型

    Args:
        symbol: 标的代码

    Returns:
        {'model': model, 'meta': dict} 或 None
    """
    try:
        import joblib

        model_path = MODEL_DIR / f"{symbol}_lgb_model.pkl"
        meta_path = MODEL_DIR / f"{symbol}_lgb_meta.json"

        if not model_path.exists():
            logger.warning(f"模型文件不存在: {model_path}")
            return None

        model = joblib.load(str(model_path))

        meta = {}
        if meta_path.exists():
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)

        logger.info(f"模型加载成功: {symbol}")
        return {"model": model, "meta": meta}

    except Exception as e:
        logger.error(f"模型加载失败 [{symbol}]: {e}")
        return None


def generate_signal(
    df: pd.DataFrame,
    symbol: str,
    model_type: str = DEFAULT_MODEL,
    use_cache: bool = True,
    save_model: bool = True,
    **kwargs,
) -> Optional[pd.Series]:
    """
    统一信号生成接口

    优先使用 Qlib，不可用时回退到本地 LightGBM。
    本地模型支持持久化：先加载已保存模型，不存在时训练并保存。

    Args:
        df: v7.5 数据 DataFrame
        symbol: 标的代码
        model_type: 模型类型
        use_cache: 是否优先使用已保存的本地模型
        save_model: 训练后是否保存模型到磁盘
        **kwargs: 其他参数

    Returns:
        标准化信号 pd.Series[-1, 1]，或 None
    """
    # 优先 Qlib（仅当模型可用时）
    if _QLIB_AVAILABLE and MODEL_MAP.get(model_type) is not None:
        signal = generate_qlib_signal(df, symbol, model_type, **kwargs)
        if signal is not None and len(signal) > 0:
            return signal

    # 回退到本地 LightGBM
    logger.info("回退到本地 LightGBM 信号")

    # 1) 尝试加载已保存的模型，直接预测
    if use_cache:
        cached = load_local_model(symbol)
        if cached is not None:
            model = cached.get("model")
            meta = cached.get("meta", {})
            feature_cols = meta.get("feature_cols")
            try:
                data = _add_technical_features(df.copy())
                if feature_cols is None:
                    feature_cols = [
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                        "ma5",
                        "ma10",
                        "ma20",
                        "ma60",
                        "macd",
                        "macd_signal",
                        "macd_hist",
                        "rsi6",
                        "rsi12",
                        "rsi24",
                        "boll_upper",
                        "boll_mid",
                        "boll_lower",
                        "boll_width",
                        "atr",
                        "obv",
                        "vol_ratio_5",
                        "vol_ratio_20",
                        "price_vs_ma5",
                        "price_vs_ma20",
                        "price_vs_ma60",
                        "volatility_5",
                        "volatility_20",
                        "atr_ratio",
                        "price_change_5",
                        "volume_change_5",
                        "price_volume_divergence",
                        "high_low_ratio",
                        "close_position",
                    ]
                data = data.dropna(subset=feature_cols)
                if len(data) > 0:
                    pred = model.predict(data[feature_cols].values)
                    signal = pd.Series(pred, index=data.index)
                    if signal.std() > 0:
                        signal = 2 * (signal - signal.min()) / (signal.max() - signal.min()) - 1
                    signal.name = "local_lgb_signal"
                    logger.info(f"使用缓存模型预测 [{symbol}]: {len(signal)} 个信号点")
                    return signal
            except Exception as e:
                logger.warning(f"缓存模型预测失败 [{symbol}]: {e}，重新训练")

    # 2) 训练新模型
    result = _local_lightgbm_signal(df, symbol, save_model=save_model)
    if isinstance(result, dict):
        return result.get("signal")
    return result
