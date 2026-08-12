"""
价格预测模块 v1.0 — TensorFlow LSTM + TimesFM 零样本预测
============================================================

特性:
  1. TimesFM 零样本预测 (优先, 无需训练, Google 预训练模型)
  2. TensorFlow LSTM 深度学习预测 (可选, 需 pip install tensorflow)
  3. 传统统计模型兜底 (ARIMA, statsmodels)
  4. 优雅降级: 依赖缺失时自动回退到下一级

预测目标:
  - T+1 / T+5 / T+10 收盘价预测
  - 涨跌方向信号 (上涨概率)
  - 预测置信区间 (q10/q90)

数据源:
  - 历史K线数据 (来自 data_provider 或本地缓存)
  - 最少需要 60 个交易日的历史数据

使用方式:
  predictor = PricePredictor()
  result = predictor.predict("600276.SH", horizon=5)
  logger.info(result.direction, result.confidence, result.target_price)

依赖 (按优先级降级):
  - timesfm[torch] (P0, 零样本预训练模型, 推荐)
  - tensorflow (P1, LSTM 深度学习, 可选)
  - statsmodels (P2, ARIMA 统计模型, 兜底)
  - numpy/pandas (P3, 必需基础库)
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Tuple, cast

import numpy as np

logger = logging.getLogger(__name__)

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
MODEL_CACHE_DIR = PROJECT_ROOT / "models" / "tf_models"
MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class PredictionResult:
    """价格预测结果"""

    symbol: str
    horizon: int  # 预测周期 (1/5/10)
    current_price: float  # 当前价格
    target_price: float  # 目标价格 (中位数预测)
    price_low: float = 0.0  # 预测下限 (q10)
    price_high: float = 0.0  # 预测上限 (q90)
    direction: str = "NEUTRAL"  # UP/DOWN/NEUTRAL
    confidence: float = 0.0  # 置信度 [0, 1]
    expected_return: float = 0.0  # 预期收益率
    signal_strength: float = 0.0  # 信号强度 [-1, 1]
    method: str = "unknown"  # timesfm/tensorflow/arima/fallback
    quantiles: Dict[str, List[float]] = field(default_factory=dict)  # 分位数预测 (每个 key → horizon 长度的 list)
    forecast_timestamp: str = ""

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "horizon": self.horizon,
            "current_price": self.current_price,
            "target_price": self.target_price,
            "price_low": self.price_low,
            "price_high": self.price_high,
            "direction": self.direction,
            "confidence": self.confidence,
            "expected_return": self.expected_return,
            "signal_strength": self.signal_strength,
            "method": self.method,
            "quantiles": self.quantiles,
            "forecast_timestamp": self.forecast_timestamp,
        }


class TimesFMForecaster:
    """TimesFM 零样本时间序列预测器

    基于 Google Research 的预训练模型, 无需训练即可预测。
    适用于单变量时间序列 (价格、成交量、宏观指标等)。

    模型: google/timesfm-2.5-200m-pytorch (200M 参数)
    上下文: 最多 16384 点 (默认 1024)
    """

    def __init__(self, model_id: str = "google/timesfm-2.5-200m-pytorch"):
        self.model_id = model_id
        self._model: Optional[Any] = None
        self._available: bool = False
        self._initialize()

    def _initialize(self):
        """初始化 TimesFM 模型 (延迟加载)"""
        try:
            import timesfm
            import torch

            # 设置浮点精度 (Ampere+ GPU 必须)
            torch.set_float32_matmul_precision("high")

            # 加载预训练模型
            self._model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(self.model_id)
            if self._model is None:
                logger.warning("TimesFM 模型加载返回 None")
                return

            # 编译配置
            config = timesfm.ForecastConfig(
                max_context=1024,
                max_horizon=256,
                normalize_inputs=True,
                use_continuous_quantile_head=True,
                force_flip_invariance=True,
                infer_is_positive=True,  # 价格 >= 0
                fix_quantile_crossing=True,
            )
            self._model.compile(config)

            self._available = True
            logger.info(f"TimesFM 初始化成功: {self.model_id}")

        except ImportError:
            logger.warning("timesfm 未安装, 跳过. 安装: pip install timesfm[torch]")
        except (ImportError, AttributeError) as e:
            logger.warning(f"TimesFM 初始化失败: {e}")

    @property
    def available(self) -> bool:
        return self._available

    def forecast(self, prices: np.ndarray, horizon: int = 5) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        """预测未来 horizon 步

        Args:
            prices: 历史价格序列 (1-D numpy array)
            horizon: 预测步数

        Returns:
            (point_forecast, quantile_forecast) 或 None
            point_forecast shape: (horizon,)
            quantile_forecast shape: (horizon, 10) — [mean, q10, q20, ..., q90]
        """
        if not self._available or self._model is None:
            return None

        try:
            # TimesFM 输入: list of 1-D arrays
            inputs = [np.asarray(prices, dtype=np.float32)]

            point, quantiles = self._model.forecast(
                horizon=horizon,
                inputs=inputs,
            )

            # point shape: (1, horizon) → (horizon,)
            point_forecast = point[0]
            # quantiles shape: (1, horizon, 10) → (horizon, 10)
            quantile_forecast = quantiles[0]

            return point_forecast, quantile_forecast

        except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
            logger.error(f"TimesFM 预测失败: {e}")
            return None


class TensorflowLSTMPredictor:
    """TensorFlow LSTM 价格预测器

    使用 LSTM 神经网络预测价格走势。
    需要安装: pip install tensorflow

    模型结构:
      - Input: (sequence_length, n_features)
      - LSTM(64, return_sequences=True)
      - LSTM(32)
      - Dense(16, relu)
      - Dense(horizon)

    训练数据:
      - 从历史K线生成滑动窗口样本
      - 特征: 收盘价归一化 + 技术指标 (RSI/MACD/MA)
    """

    _tf_warned: ClassVar[bool] = False

    def __init__(self, sequence_length: int = 60):
        self.sequence_length: int = sequence_length
        self._tf: Optional[Any] = None
        self._model: Optional[Any] = None
        self._available: bool = False
        self._initialize()

    def _initialize(self):
        """初始化 TensorFlow"""
        try:
            import tensorflow as tf

            self._tf = tf
            # 设置 GPU 内存增长
            gpus = tf.config.list_physical_devices("GPU")
            for gpu in gpus:
                tf.config.experimental.set_memory_growth(gpu, True)
            self._available = True
            logger.info(f"TensorFlow {tf.__version__} 初始化成功 (GPU: {len(gpus)})")
        except ImportError:
            if not self.__class__._tf_warned:
                logger.warning("tensorflow 未安装, 跳过 LSTM 预测. 安装: pip install tensorflow")
                self.__class__._tf_warned = True
        except (ImportError, AttributeError) as e:
            if not self.__class__._tf_warned:
                logger.warning(f"TensorFlow 初始化失败: {e}")
                self.__class__._tf_warned = True

    @property
    def available(self) -> bool:
        return self._available

    def _build_model(self, n_features: int, horizon: int):
        """构建 LSTM 模型"""
        tf = self._tf
        if tf is None:
            raise RuntimeError("TensorFlow 未初始化, 无法构建模型")
        model = tf.keras.Sequential(
            [
                tf.keras.layers.LSTM(
                    64,
                    return_sequences=True,
                    input_shape=(self.sequence_length, n_features),
                ),
                tf.keras.layers.Dropout(0.2),
                tf.keras.layers.LSTM(32),
                tf.keras.layers.Dropout(0.2),
                tf.keras.layers.Dense(16, activation="relu"),
                tf.keras.layers.Dense(horizon),
            ]
        )
        model.compile(optimizer="adam", loss="mse", metrics=["mae"])
        return model

    def _prepare_data(self, prices: np.ndarray, horizon: int) -> Tuple[np.ndarray, np.ndarray]:
        """准备训练数据: 滑动窗口"""
        # 归一化
        mean = prices.mean()
        std = prices.std()
        if std == 0:
            std = 1
        normalized = (prices - mean) / std

        X, y = [], []
        for i in range(len(normalized) - self.sequence_length - horizon + 1):
            X.append(normalized[i : i + self.sequence_length])
            y.append(normalized[i + self.sequence_length : i + self.sequence_length + horizon])

        return np.array(X), np.array(y)

    def train_and_predict(
        self, prices: np.ndarray, horizon: int = 5, epochs: int = 50, batch_size: int = 32
    ) -> Optional[np.ndarray]:
        """训练并预测

        Args:
            prices: 历史价格序列
            horizon: 预测步数
            epochs: 训练轮数
            batch_size: 批大小

        Returns:
            预测价格数组 或 None
        """
        if not self._available:
            return None

        try:
            # 准备数据
            X, y = self._prepare_data(prices, horizon)
            if len(X) < 10:
                logger.warning(f"训练样本不足: {len(X)} (需要至少 10)")
                return None

            # 构建模型
            self._model = self._build_model(n_features=1, horizon=horizon)
            model = self._model
            if model is None:
                logger.warning("LSTM 模型构建失败")
                return None

            # 训练
            model.fit(
                X.reshape((*X.shape, 1)),  # (samples, seq_len, 1)
                y,
                epochs=epochs,
                batch_size=batch_size,
                verbose=0,
                validation_split=0.2,
            )

            # 预测
            last_sequence = prices[-self.sequence_length :]
            mean = prices.mean()
            std = prices.std() if prices.std() > 0 else 1
            normalized_input = (last_sequence - mean) / std
            input_3d = normalized_input.reshape(1, self.sequence_length, 1)

            prediction_normalized = model.predict(input_3d, verbose=0)[0]
            # 反归一化
            prediction = prediction_normalized * std + mean

            return cast(np.ndarray, prediction)

        except (AttributeError, TypeError, ValueError, OSError) as e:
            logger.error(f"LSTM 训练预测失败: {e}")
            return None


class StatisticalForecaster:
    """统计模型预测器 (兜底方案)

    使用 ARIMA / 简单移动平均 / 动量外推。
    无需额外依赖 (仅 numpy)。
    """

    def __init__(self):
        self._statsmodels_available = False
        try:
            from statsmodels.tsa.arima.model import ARIMA  # noqa: F401

            self._statsmodels_available = True
        except ImportError:
            logger.info("statsmodels 未安装, 使用简单移动平均兜底")

    @property
    def available(self) -> bool:
        return True  # 始终可用

    def forecast(self, prices: np.ndarray, horizon: int = 5) -> Tuple[np.ndarray, Dict[str, List[float]]]:
        """统计模型预测

        Returns:
            (point_forecast, quantiles_dict)
        """
        if self._statsmodels_available and len(prices) >= 30:
            return self._arima_forecast(prices, horizon)
        return self._ma_momentum_forecast(prices, horizon)

    def _arima_forecast(self, prices: np.ndarray, horizon: int) -> Tuple[np.ndarray, Dict[str, List[float]]]:
        """ARIMA 预测"""
        try:
            from statsmodels.tsa.arima.model import ARIMA

            # 自动选择 (p, d, q) — 简化版
            model = ARIMA(prices, order=(5, 1, 0))
            fitted = model.fit()
            forecast = fitted.forecast(steps=horizon)

            # 置信区间
            conf_int = fitted.get_forecast(steps=horizon).conf_int(alpha=0.2)
            q10 = conf_int.iloc[:, 0].values
            q90 = conf_int.iloc[:, 1].values

            quantiles = {
                "q10": q10.tolist(),
                "q50": forecast.tolist(),
                "q90": q90.tolist(),
            }

            return forecast, quantiles

        except (ImportError, AttributeError) as e:
            logger.warning(f"ARIMA 预测失败, 回退到移动平均: {e}")
            return self._ma_momentum_forecast(prices, horizon)

    def _ma_momentum_forecast(self, prices: np.ndarray, horizon: int) -> Tuple[np.ndarray, Dict[str, List[float]]]:
        """移动平均 + 动量外推 (最终兜底)"""
        # 5日均线
        ma5 = np.mean(prices[-5:]) if len(prices) >= 5 else np.mean(prices)
        # 20日均线
        ma20 = np.mean(prices[-20:]) if len(prices) >= 20 else np.mean(prices)

        # 动量: 最近5日变化率
        if len(prices) >= 6:
            momentum = (prices[-1] - prices[-6]) / prices[-6]
        else:
            momentum = 0.0

        # 趋势: MA5 vs MA20
        trend = (ma5 - ma20) / ma20 if ma20 > 0 else 0

        # 外推: 均线 + 动量*衰减
        current = float(prices[-1])
        forecast_list: List[float] = []
        for i in range(horizon):
            # 衰减因子
            decay = 0.8 ** (i + 1)
            # 预测 = 当前价 + 趋势*衰减 + 动量*衰减
            pred = current * (1 + trend * 0.3 * decay + momentum * 0.5 * decay)
            forecast_list.append(float(pred))
            current = pred

        forecast_arr = np.array(forecast_list, dtype=np.float64)

        # 简单置信区间 (±5%)
        quantiles: Dict[str, List[float]] = {
            "q10": (forecast_arr * 0.95).tolist(),
            "q50": forecast_arr.tolist(),
            "q90": (forecast_arr * 1.05).tolist(),
        }

        return forecast_arr, quantiles


class PricePredictor:
    """价格预测器 — 多模型融合

    优先级:
      1. TimesFM (零样本预训练, 最准确)
      2. TensorFlow LSTM (深度学习, 需训练)
      3. ARIMA (统计模型)
      4. 移动平均+动量 (最终兜底)
    """

    def __init__(self):
        self.timesfm = TimesFMForecaster()
        self.lstm = TensorflowLSTMPredictor()
        self.statistical = StatisticalForecaster()
        logger.info(
            f"PricePredictor 初始化: "
            f"TimesFM={'✓' if self.timesfm.available else '✗'} "
            f"LSTM={'✓' if self.lstm.available else '✗'} "
            f"Statistical=✓"
        )

    def predict(
        self, symbol: str, prices: np.ndarray, horizon: int = 5, current_price: Optional[float] = None
    ) -> PredictionResult:
        """预测价格

        Args:
            symbol: 股票代码 (如 "600276.SH")
            prices: 历史收盘价序列 (至少 30 个数据点)
            horizon: 预测周期 (1/5/10)
            current_price: 当前价格 (默认取 prices[-1])

        Returns:
            PredictionResult
        """
        prices = np.asarray(prices, dtype=np.float64)
        if len(prices) < 10:
            logger.warning(f"{symbol} 历史数据不足 ({len(prices)} < 10), 返回中性预测")
            return self._fallback_result(symbol, horizon, current_price or prices[-1] if len(prices) else 0)

        current = float(current_price or prices[-1])
        forecast: Optional[np.ndarray] = None
        quantiles: Dict[str, List[float]] = {}
        method = "fallback"

        # 优先级 1: TimesFM
        if self.timesfm.available:
            tfm_result = self.timesfm.forecast(prices, horizon)
            if tfm_result is not None:
                forecast, q_array = tfm_result
                quantiles = {
                    "q10": q_array[:, 1].tolist(),
                    "q50": q_array[:, 5].tolist(),
                    "q90": q_array[:, 9].tolist(),
                }
                method = "timesfm"

        # 优先级 2: TensorFlow LSTM
        if forecast is None and self.lstm.available:
            lstm_result = self.lstm.train_and_predict(prices, horizon)
            if lstm_result is not None:
                forecast = lstm_result
                method = "tensorflow_lstm"
                # LSTM 不提供分位数, 用 ±5% 近似
                quantiles = {
                    "q10": (forecast * 0.95).tolist(),
                    "q50": forecast.tolist(),
                    "q90": (forecast * 1.05).tolist(),
                }

        # 优先级 3: 统计模型 (兜底)
        if forecast is None:
            forecast, quantiles = self.statistical.forecast(prices, horizon)
            method = "arima" if self.statistical._statsmodels_available else "ma_momentum"

        # 构建结果
        target_price = float(forecast[-1]) if len(forecast) > 0 else current
        price_low = float(quantiles.get("q10", [current * 0.95])[-1]) if quantiles else current * 0.95
        price_high = float(quantiles.get("q90", [current * 1.05])[-1]) if quantiles else current * 1.05

        # 方向判断
        expected_return = (target_price - current) / current if current > 0 else 0
        if expected_return > 0.01:
            direction = "UP"
        elif expected_return < -0.01:
            direction = "DOWN"
        else:
            direction = "NEUTRAL"

        # 置信度: 基于分位数宽度
        if price_high > price_low and current > 0:
            interval_width = (price_high - price_low) / current
            confidence = max(0.0, min(1.0, 1.0 - interval_width))
        else:
            confidence = 0.5

        # 信号强度 [-1, 1]
        signal_strength = max(-1.0, min(1.0, expected_return * 10))

        return PredictionResult(
            symbol=symbol,
            horizon=horizon,
            current_price=current,
            target_price=round(target_price, 4),
            price_low=round(price_low, 4),
            price_high=round(price_high, 4),
            direction=direction,
            confidence=round(confidence, 4),
            expected_return=round(expected_return, 4),
            signal_strength=round(signal_strength, 4),
            method=method,
            quantiles=quantiles,
            forecast_timestamp=datetime.now().isoformat(),
        )

    def _fallback_result(self, symbol: str, horizon: int, current: float) -> PredictionResult:
        """数据不足时的中性预测"""
        return PredictionResult(
            symbol=symbol,
            horizon=horizon,
            current_price=current,
            target_price=current,
            direction="NEUTRAL",
            confidence=0.0,
            method="fallback",
            forecast_timestamp=datetime.now().isoformat(),
        )

    def batch_predict(self, symbols_prices: Dict[str, np.ndarray], horizon: int = 5) -> Dict[str, PredictionResult]:
        """批量预测

        Args:
            symbols_prices: {symbol: prices_array}
            horizon: 预测周期

        Returns:
            {symbol: PredictionResult}
        """
        results = {}
        for symbol, prices in symbols_prices.items():
            try:
                results[symbol] = self.predict(symbol, prices, horizon)
            except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
                logger.error(f"预测 {symbol} 失败: {e}")
                results[symbol] = self._fallback_result(symbol, horizon, 0)
        return results

    def get_signal_for_fusion(self, symbol: str, prices: np.ndarray, horizon: int = 5) -> float:
        """获取用于信号融合的标准化信号 [-1, 1]

        供 signal_fusion.py 调用。
        """
        result = self.predict(symbol, prices, horizon)
        return result.signal_strength


def load_price_history(symbol: str, days: int = 120) -> Optional[np.ndarray]:
    """从本地缓存加载历史价格

    优先级:
      1. v7.5_institutional/reports/daily_pnl_report_*.json (最新收盘价)
      2. config/price_history.jsonl
      3. 返回 None (调用方负责获取)
    """
    # 从最新收盘报告读取
    reports_dir = PROJECT_ROOT / "v7.5_institutional" / "reports"
    if reports_dir.exists():
        json_files = sorted(reports_dir.glob("daily_pnl_report_*.json"), reverse=True)
        prices = []
        for f in json_files[:days]:
            try:
                report = json.load(open(f, encoding="utf-8"))
                for detail in report.get("portfolio_pnl", {}).get("details", []):
                    if detail.get("code", "").split(".")[0] == symbol.split(".")[0]:
                        close = detail.get("close_price", 0)
                        if close and close > 0:
                            prices.append(close)
                        break
            except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError):
                continue
        if prices:
            return np.array(list(reversed(prices)))

    return None


# 模块自检
if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("价格预测模块自检")
    logger.info("=" * 60)

    predictor = PricePredictor()

    # 模拟价格数据 (120个交易日)
    np.random.seed(42)
    base_price = 50.0
    returns = np.random.normal(0.001, 0.02, 120)
    prices = base_price * np.cumprod(1 + returns)

    logger.info(f"\n模拟价格: {len(prices)} 个数据点")
    logger.info(f"起始价: {prices[0]:.2f}, 最新价: {prices[-1]:.2f}")

    for horizon in [1, 5, 10]:
        result = predictor.predict("TEST.SH", prices, horizon)
        logger.info(f"\n--- T+{horizon} 预测 ---")
        logger.info(f"方法: {result.method}")
        logger.info(f"当前价: {result.current_price:.2f}")
        logger.info(f"目标价: {result.target_price:.2f}")
        logger.info(f"预期收益: {result.expected_return:.2%}")
        logger.info(f"方向: {result.direction}")
        logger.info(f"置信度: {result.confidence:.2%}")
        logger.info(f"信号强度: {result.signal_strength:.4f}")
        if result.quantiles:
            q10_list = result.quantiles.get("q10", [0.0])
            q90_list = result.quantiles.get("q90", [0.0])
            q10 = q10_list[-1]
            q90 = q90_list[-1]
            logger.info(f"区间: [{q10:.2f}, {q90:.2f}]")

    logger.info("\n✅ 自检完成")