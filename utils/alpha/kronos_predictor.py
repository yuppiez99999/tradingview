"""Kronos 时序基础模型预测器 — AB 测试 Challenger.

模块整合 8.4 — GitHub 热门项目集成 §3.1
Flag: USE_KRONOS_PREDICTOR (默认 False, 双签启用)

设计原则:
    1. 懒加载: 首次 predict 时才从 HuggingFace 下载模型, 避免导入即失败
    2. 失败安全: 模型不可用时返回空字典, 调用方 (AB 框架) 自动回退到 LGBM Champion
    3. Flag 透传 (HC-1): USE_KRONOS_PREDICTOR=False 时直接返回空结果
    4. 审计: 每次预测写入 reports/kronos_predictions/{symbol}_{date}.jsonl
    5. 线程安全: 模型加载使用锁, 避免并发重复下载
    6. 不可变性: 所有返回均为新对象, 不修改输入 DataFrame

API:
    from utils.alpha.kronos_predictor import KronosPredictor, KronosPredictConfig

    predictor = KronosPredictor()
    if predictor.available:
        signals = predictor.predict(df, symbol="600519.SH")
        # signals = {"KRONOS_RET_5D": 0.03, "KRONOS_DIR": 1.0, "KRONOS_CONF": 12.5, ...}

注意:
    - Kronos 是时序预测模型 (OHLCV -> 未来 OHLCV), 与 LGBM 因子分类器不同构
    - 本模块作为 Alpha 信号生成器, 输出因子字典供 multi_factor_signal 融合
    - AB 测试对比的是 "注入 Kronos 因子" vs "不注入", 通过 IC/DSR 评估

硬约束:
    - HC-1: Feature Flag 默认 False, 关闭时不加载模型不消耗资源
"""

from __future__ import annotations

import json
import logging
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from utils.infra.feature_flags import is_enabled

logger = logging.getLogger("kronos_predictor")

# ============================================================
# 路径常量
# ============================================================
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_PREDICTIONS_DIR = _PROJECT_ROOT / "reports" / "kronos_predictions"
_PREDICTIONS_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# 配置数据类
# ============================================================
@dataclass
class KronosPredictConfig:
    """Kronos 预测配置.

    Attributes:
        model_size: 模型规模 (mini/small/base), small 为默认平衡点
        lookback: 输入历史天数 (Kronos-small 最大 512)
        pred_len: 预测天数
        device: 推理设备 (auto/cpu/cuda)
        temperature: 采样温度 (1.0=标准, <1.0=更确定)
        top_p: 核采样概率
        audit_enabled: 是否写审计日志
    """

    model_size: str = "small"
    lookback: int = 400
    pred_len: int = 20
    device: str = "auto"
    temperature: float = 1.0
    top_p: float = 0.9
    audit_enabled: bool = True


# ============================================================
# 模型名称映射 (HuggingFace Hub)
# ============================================================
_MODEL_REGISTRY: dict[str, dict[str, Any]] = {
    "mini": {
        "tokenizer": "NeoQuasar/Kronos-Tokenizer-2k",
        "model": "NeoQuasar/Kronos-mini",
        "max_context": 2048,
        "params_m": 7.8,
    },
    "small": {
        "tokenizer": "NeoQuasar/Kronos-Tokenizer-base",
        "model": "NeoQuasar/Kronos-small",
        "max_context": 512,
        "params_m": 24.7,
    },
    "base": {
        "tokenizer": "NeoQuasar/Kronos-Tokenizer-base",
        "model": "NeoQuasar/Kronos-base",
        "max_context": 512,
        "params_m": 48.0,
    },
}


# ============================================================
# Kronos 预测器
# ============================================================
class KronosPredictor:
    """Kronos 时序预测器 — AB 测试 Challenger.

    契约:
        - predict(df, symbol) -> Dict[str, float]: 返回因子信号字典
        - 失败安全: 模型不可用或 flag 关闭时返回空字典
        - 审计: 每次预测写入 reports/kronos_predictions/{symbol}_{date}.jsonl
        - 线程安全: 模型加载使用锁

    使用:
        predictor = KronosPredictor()
        signals = predictor.predict(df, symbol="600519.SH")
    """

    _instance: KronosPredictor | None = None
    _instance_lock = threading.Lock()

    def __init__(self, config: KronosPredictConfig | None = None) -> None:
        self.config = config or KronosPredictConfig()
        self._model_wrapper: Any = None
        self._available: bool | None = None
        self._init_error: str | None = None
        self._init_latency_ms: float = 0.0
        self._predict_count: int = 0
        self._error_count: int = 0

    @classmethod
    def get_instance(cls) -> KronosPredictor:
        """获取单例 (线程安全, 推荐生产使用)."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例 (仅测试用)."""
        with cls._instance_lock:
            cls._instance = None

    # ============================================================
    # 可用性检查 + 模型加载
    # ============================================================
    @property
    def available(self) -> bool:
        """检查 Kronos 是否可用 (懒加载, 线程安全).

        Returns:
            True 如果 flag 启用且模型加载成功
        """
        if not is_enabled("USE_KRONOS_PREDICTOR"):
            logger.debug("USE_KRONOS_PREDICTOR=False, 跳过模型加载")
            return False
        if self._available is None:
            self._try_init_model()
        return bool(self._available)

    @property
    def error_msg(self) -> str | None:
        """初始化失败时的错误信息 (诊断用)."""
        return self._init_error

    @property
    def init_latency_ms(self) -> float:
        """模型初始化耗时 (毫秒)."""
        return self._init_latency_ms

    def _try_init_model(self) -> None:
        """尝试加载 Kronos 模型 (失败安全)."""
        with self._instance_lock:
            if self._available is not None:
                return  # 已被其他线程初始化
            t0 = time.perf_counter()
            try:
                self._available = self._load_kronos_model()
                self._init_latency_ms = (time.perf_counter() - t0) * 1000
                if self._available:
                    logger.info(
                        "Kronos-%s 加载成功 (耗时 %.0fms)",
                        self.config.model_size,
                        self._init_latency_ms,
                    )
                else:
                    # 兜底: 若 _load_kronos_model 返回 False 但未设置错误信息, 补默认
                    if self._init_error is None:
                        self._init_error = "模型加载失败 (原因未记录)"
                    logger.warning("Kronos-%s 不可用: %s", self.config.model_size, self._init_error)
            except Exception as e:  # noqa: BLE001  # 模块 fail-safe, 任何加载失败都降级
                self._available = False
                self._init_error = f"{type(e).__name__}: {e}"
                self._init_latency_ms = (time.perf_counter() - t0) * 1000
                logger.error("Kronos 加载异常: %s", e)
                logger.debug(traceback.format_exc())

    def _load_kronos_model(self) -> bool:
        """加载 Kronos 模型 (内部, 不捕获异常).

        Returns:
            True 加载成功
        Raises:
            ImportError: Kronos 依赖未安装
            RuntimeError: 模型下载失败
        """
        model_info = _MODEL_REGISTRY.get(self.config.model_size)
        if model_info is None:
            self._init_error = f"未知 model_size: {self.config.model_size}"
            return False

        try:
            from model import Kronos, KronosTokenizer
            from model import KronosPredictor as _KronosPredictor
        except ImportError:
            self._init_error = "Kronos 本地模块未安装 (需 git clone https://github.com/shiyu-coder/Kronos)"
            return False

        tok_name = model_info["tokenizer"]
        model_name = model_info["model"]
        max_ctx = model_info["max_context"]

        logger.info("加载 Kronos-%s: %s (params=%sM)", self.config.model_size, model_name, model_info["params_m"])

        tokenizer = KronosTokenizer.from_pretrained(tok_name)
        model = Kronos.from_pretrained(model_name)

        import torch

        if self.config.device == "auto":
            dev = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            dev = self.config.device
        model = model.to(dev)
        model.eval()

        self._model_wrapper = _KronosPredictor(model, tokenizer, max_context=max_ctx)
        return True

    # ============================================================
    # 预测接口
    # ============================================================
    def predict(self, df: pd.DataFrame, symbol: str) -> dict[str, float]:
        """对单只标的的历史数据进行预测, 返回因子信号字典.

        Args:
            df: 历史 K 线数据, 必须含 open/high/low/close/volume 列
            symbol: 标的代码 (如 "600519.SH")

        Returns:
            因子信号字典, 例如:
                {
                    "KRONOS_RET_5D": 0.03,      # 预测 5 日收益率
                    "KRONOS_RET_20D": 0.05,     # 预测 20 日收益率
                    "KRONOS_VOL_5D": 0.015,     # 预测 5 日波动率
                    "KRONOS_DIR": 1.0,          # 预测方向 (1=涨, -1=跌)
                    "KRONOS_MOM": 1.5,          # 预测动量
                    "KRONOS_CONF": 12.5,        # 预测置信度
                    "KRONOS_PREMIUM": 0.02,     # 预测溢价
                }
            flag 关闭或模型不可用时返回空字典 {}
        """
        if not is_enabled("USE_KRONOS_PREDICTOR"):
            return {}
        if not self.available:
            return {}

        required_cols = ["open", "high", "low", "close", "volume"]
        if not all(c in df.columns for c in required_cols):
            logger.warning("[%s] 数据缺少必要列: %s", symbol, list(df.columns))
            return {}

        if len(df) < 30:
            logger.warning("[%s] 历史数据不足: %d < 30", symbol, len(df))
            return {}

        t0 = time.perf_counter()
        try:
            pred_df = self._run_inference(df, symbol)
            if pred_df is None or pred_df.empty:
                self._error_count += 1
                return {}
            factors = self._extract_factors(pred_df, df)
            factors["symbol"] = symbol  # type: ignore[assignment]
            latency_ms = (time.perf_counter() - t0) * 1000
            factors["_latency_ms"] = latency_ms
            self._predict_count += 1

            if self.config.audit_enabled:
                self._write_audit(symbol, factors, latency_ms)
            return factors
        except Exception as e:  # noqa: BLE001  # 预测 fail-safe, 不阻断主流程
            self._error_count += 1
            logger.warning("[%s] Kronos 预测失败: %s", symbol, e)
            logger.debug(traceback.format_exc())
            return {}

    def predict_batch(self, symbols: list[str], df_map: dict[str, pd.DataFrame]) -> dict[str, dict[str, float]]:
        """批量预测多只标的.

        Args:
            symbols: 标的代码列表
            df_map: {symbol: DataFrame} 历史数据映射

        Returns:
            {symbol: factors_dict} 预测结果映射
        """
        results: dict[str, dict[str, float]] = {}
        for symbol in symbols:
            df = df_map.get(symbol)
            if df is None or df.empty:
                results[symbol] = {}
                continue
            results[symbol] = self.predict(df, symbol)
        return results

    def _run_inference(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame | None:
        """执行 Kronos 模型推理."""
        input_df = df[["open", "high", "low", "close", "volume"]].copy()
        if "amount" in df.columns:
            input_df["amount"] = df["amount"]
        else:
            input_df["amount"] = 0.0

        input_df = input_df.reset_index(drop=True)
        x_timestamp = pd.Series(pd.date_range(start="2020-01-01", periods=len(input_df), freq="D"))
        if isinstance(df.index, pd.DatetimeIndex):
            x_timestamp = pd.Series(df.index)

        last_date = x_timestamp.iloc[-1] if len(x_timestamp) > 0 else pd.Timestamp.now()
        y_timestamp = pd.Series(
            pd.date_range(
                start=last_date + pd.Timedelta(days=1),
                periods=self.config.pred_len,
                freq="D",
            )
        )

        pred_df = self._model_wrapper.predict(
            df=input_df,
            x_timestamp=x_timestamp,
            y_timestamp=y_timestamp,
            pred_len=self.config.pred_len,
            T=self.config.temperature,
            top_p=self.config.top_p,
            sample_count=1,
        )
        return pred_df

    def _extract_factors(self, pred_df: pd.DataFrame, hist_df: pd.DataFrame) -> dict[str, float]:
        """从预测结果提取因子信号 (不可变, 不修改输入)."""
        factors: dict[str, float] = {}
        last_close = float(hist_df["close"].iloc[-1]) if len(hist_df) > 0 else 1.0
        if last_close <= 0:
            last_close = 1.0

        for horizon in [1, 5, 10, 20]:
            if len(pred_df) >= horizon:
                pred_close = float(pred_df["close"].iloc[horizon - 1])
                ret = (pred_close - last_close) / last_close
                factors[f"KRONOS_RET_{horizon}D"] = round(ret, 6)
                if horizon >= 5:
                    pred_returns = pred_df["close"].iloc[:horizon].pct_change().dropna()
                    if len(pred_returns) > 0:
                        vol = float(pred_returns.std())
                        factors[f"KRONOS_VOL_{horizon}D"] = round(vol, 6)

        if len(pred_df) >= 5:
            ret_5d = factors.get("KRONOS_RET_5D", 0.0)
            hist_returns = hist_df["close"].pct_change().dropna()
            hist_vol = float(hist_returns.tail(20).std()) if len(hist_returns) >= 20 else 0.02
            factors["KRONOS_MOM"] = round(ret_5d / max(hist_vol, 1e-6), 6)

        factors["KRONOS_DIR"] = 1.0 if factors.get("KRONOS_RET_5D", 0.0) >= 0 else -1.0

        if len(pred_df) >= 5:
            pred_high = float(pred_df["high"].iloc[:5].max())
            pred_low = float(pred_df["low"].iloc[:5].min())
            pred_range = (pred_high - pred_low) / last_close
            factors["KRONOS_CONF"] = round(1.0 / max(pred_range, 1e-4), 6)
            pred_mean = float(pred_df["close"].iloc[:5].mean())
            factors["KRONOS_PREMIUM"] = round((pred_mean - last_close) / last_close, 6)

        return factors

    # ============================================================
    # 审计
    # ============================================================
    def _write_audit(self, symbol: str, factors: dict[str, float], latency_ms: float) -> None:
        """写预测审计日志 (JSONL 格式, 追加)."""
        try:
            safe_symbol = symbol.replace(".", "_").replace("/", "_")
            date_str = datetime.utcnow().strftime("%Y%m%d")
            audit_file = _PREDICTIONS_DIR / f"{safe_symbol}_{date_str}.jsonl"
            record = {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "symbol": symbol,
                "model_id": self.get_model_id(),
                "latency_ms": round(latency_ms, 2),
                "factors": {k: v for k, v in factors.items() if not k.startswith("_")},
            }
            with open(audit_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning("写审计日志失败 [%s]: %s", symbol, e)

    # ============================================================
    # 元数据 (供 ModelRegistry 注册用)
    # ============================================================
    def get_model_id(self) -> str:
        """返回模型唯一标识."""
        return f"kronos-{self.config.model_size}"

    def get_metadata(self) -> dict[str, Any]:
        """返回模型元数据 (供 AB 测试框架注册用)."""
        model_info = _MODEL_REGISTRY.get(self.config.model_size, {})
        return {
            "model_id": self.get_model_id(),
            "model_size": self.config.model_size,
            "params_m": model_info.get("params_m"),
            "lookback": self.config.lookback,
            "pred_len": self.config.pred_len,
            "device": self.config.device,
            "available": bool(self._available),
            "init_error": self._init_error,
            "init_latency_ms": round(self._init_latency_ms, 2),
            "predict_count": self._predict_count,
            "error_count": self._error_count,
            "flag_name": "USE_KRONOS_PREDICTOR",
        }

    def get_health(self) -> dict[str, Any]:
        """返回健康状态 (供监控用)."""
        return {
            "available": bool(self._available),
            "predict_count": self._predict_count,
            "error_count": self._error_count,
            "error_rate": (self._error_count / max(self._predict_count + self._error_count, 1)),
            "init_latency_ms": round(self._init_latency_ms, 2),
        }


# ============================================================
# 模块级快捷函数
# ============================================================
def predict(df: pd.DataFrame, symbol: str) -> dict[str, float]:
    """快捷函数: 使用单例预测单只标的."""
    return KronosPredictor.get_instance().predict(df, symbol)


def predict_batch(symbols: list[str], df_map: dict[str, pd.DataFrame]) -> dict[str, dict[str, float]]:
    """快捷函数: 批量预测."""
    return KronosPredictor.get_instance().predict_batch(symbols, df_map)


def is_available() -> bool:
    """快捷函数: 检查 Kronos 是否可用."""
    return KronosPredictor.get_instance().available


def get_health() -> dict[str, Any]:
    """快捷函数: 获取健康状态."""
    return KronosPredictor.get_instance().get_health()
