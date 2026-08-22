"""T4.2 ML 增强选择器 — MLEnhancedSelector.

轻量级 ML 选择器, 基于 numpy 实现逻辑回归 (Logistic Regression) + L2 正则化,
支持模型保存/加载 (joblib 持久化), 用于因子信号增强.

设计原则:
    - 无 PyTorch 依赖: 基于 numpy 实现梯度下降 LR (环境无 PyTorch 时可用)
    - PyTorch 接口预留: 当 PyTorch 可用时, 可通过 backend="torch" 切换
    - Feature Flag 透传 (HC-1): USE_ML_ENHANCED_SELECTOR 默认 False, 关闭时降级为等权
    - 模型持久化: joblib 序列化 (model_registry 目录)
    - 单一入口: train() → predict() → save() → load()

用法:
    from utils.alpha.ml_enhanced_selector import MLEnhancedSelector
    selector = MLEnhancedSelector()
    selector.train(X_train, y_train)
    predictions = selector.predict(X_test)
    selector.save("model_v1.pkl")
    selector.load("model_v1.pkl")
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ============================================================
# 默认参数
# ============================================================
DEFAULT_LEARNING_RATE = 0.01
DEFAULT_N_ITERATIONS = 1000
DEFAULT_L2_REG = 0.01
DEFAULT_RANDOM_STATE = 42
DEFAULT_MODEL_DIR = Path("models/ml_selector")

# Sigmoid 数值稳定阈值
_SIGMOID_CLIP_LOWER = -500.0
_SIGMOID_CLIP_UPPER = 500.0


# ============================================================
# 异常体系
# ============================================================
class MLEnhancedSelectorError(Exception):
    """MLEnhancedSelector 基础异常."""


class ModelNotTrainedError(MLEnhancedSelectorError):
    """模型未训练异常."""


class ModelLoadError(MLEnhancedSelectorError):
    """模型加载异常."""


# ============================================================
# 数据类
# ============================================================
@dataclass
class TrainingConfig:
    """训练配置.

    Attributes:
        learning_rate: 学习率 (默认 0.01)
        n_iterations: 迭代次数 (默认 1000)
        l2_reg: L2 正则化系数 (默认 0.01)
        random_state: 随机种子
        backend: 后端 ("numpy" 或 "torch", 默认 "numpy")
    """

    learning_rate: float = DEFAULT_LEARNING_RATE
    n_iterations: int = DEFAULT_N_ITERATIONS
    l2_reg: float = DEFAULT_L2_REG
    random_state: int = DEFAULT_RANDOM_STATE
    backend: str = "numpy"


@dataclass
class TrainingResult:
    """训练结果.

    Attributes:
        n_samples: 训练样本数
        n_features: 特征数
        final_loss: 最终损失
        n_iterations: 实际迭代次数
        feature_importance: 特征重要性 (权重绝对值)
        training_history: 损失历史 (每 100 次记录一次)
    """

    n_samples: int = 0
    n_features: int = 0
    final_loss: float = 0.0
    n_iterations: int = 0
    feature_importance: dict[str, float] = field(default_factory=dict)
    training_history: list[float] = field(default_factory=list)


# ============================================================
# 核心模型: 逻辑回归 (numpy 实现)
# ============================================================
class _LogisticRegressionNumpy:
    """基于 numpy 的逻辑回归 (二分类).

    实现:
        - 梯度下降优化
        - L2 正则化
        - Sigmoid 数值稳定 (clip 防 overflow)
        - 损失: 交叉熵 + L2
    """

    def __init__(
        self,
        learning_rate: float = DEFAULT_LEARNING_RATE,
        n_iterations: int = DEFAULT_N_ITERATIONS,
        l2_reg: float = DEFAULT_L2_REG,
        random_state: int = DEFAULT_RANDOM_STATE,
    ) -> None:
        self.learning_rate = float(learning_rate)
        self.n_iterations = int(n_iterations)
        self.l2_reg = float(l2_reg)
        self.random_state = int(random_state)
        self.weights: np.ndarray | None = None
        self.bias: float = 0.0
        self._rng = np.random.default_rng(random_state)

    @staticmethod
    def _sigmoid(z: np.ndarray) -> np.ndarray:
        """数值稳定的 sigmoid 函数."""
        z = np.clip(z, _SIGMOID_CLIP_LOWER, _SIGMOID_CLIP_UPPER)
        return cast(np.ndarray, 1.0 / (1.0 + np.exp(-z)))

    @staticmethod
    def _binary_cross_entropy(y_true: np.ndarray, y_pred: np.ndarray) -> float:
        """计算二元交叉熵损失."""
        eps = 1e-15
        y_pred = np.clip(y_pred, eps, 1.0 - eps)
        return float(-np.mean(y_true * np.log(y_pred) + (1 - y_true) * np.log(1 - y_pred)))

    def fit(self, X: np.ndarray, y: np.ndarray) -> tuple[float, list[float]]:  # noqa: N803
        """训练模型.

        Args:
            X: 特征矩阵 (n_samples, n_features)
            y: 标签 (n_samples,) 二分类 {0, 1}

        Returns:
            (final_loss, training_history)
        """
        n_samples, n_features = X.shape
        # 初始化权重 (He 初始化简化版)
        self.weights = self._rng.normal(0, 0.01, n_features)
        self.bias = 0.0

        history: list[float] = []
        for i in range(self.n_iterations):
            # 前向传播
            linear = X @ self.weights + self.bias
            y_pred = self._sigmoid(linear)

            # 梯度计算 (含 L2 正则化)
            dw = (X.T @ (y_pred - y)) / n_samples + self.l2_reg * self.weights
            db = float(np.mean(y_pred - y))

            # 参数更新
            self.weights -= self.learning_rate * dw
            self.bias -= self.learning_rate * db

            # 记录损失 (每 100 次或最后一次)
            if (i + 1) % 100 == 0 or i == self.n_iterations - 1:
                loss = self._binary_cross_entropy(y, y_pred)
                history.append(loss)

        final_loss = history[-1] if history else 0.0
        return final_loss, history

    def predict_proba(self, X: np.ndarray) -> np.ndarray:  # noqa: N803
        """预测概率.

        Args:
            X: 特征矩阵 (n_samples, n_features)

        Returns:
            概率数组 (n_samples,) ∈ [0, 1]
        """
        if self.weights is None:
            raise ModelNotTrainedError("模型未训练, 请先调用 fit()")
        linear = X @ self.weights + self.bias
        return self._sigmoid(linear)

    def predict(self, X: np.ndarray, threshold: float = 0.5) -> np.ndarray:  # noqa: N803
        """预测类别.

        Args:
            X: 特征矩阵
            threshold: 分类阈值 (默认 0.5)

        Returns:
            预测标签 {0, 1}
        """
        return cast(np.ndarray, (self.predict_proba(X) >= threshold).astype(int))

    def get_feature_importance(self, feature_names: list[str] | None = None) -> dict[str, float]:
        """获取特征重要性 (权重绝对值).

        Args:
            feature_names: 特征名列表 (可选)

        Returns:
            {feature_name: importance}
        """
        if self.weights is None:
            raise ModelNotTrainedError("模型未训练")
        importance = np.abs(self.weights)
        if feature_names is None:
            feature_names = [f"f{i}" for i in range(len(importance))]
        return dict(zip(feature_names, importance.tolist()))


# ============================================================
# 主类: MLEnhancedSelector
# ============================================================
class MLEnhancedSelector:
    """ML 增强选择器.

    基于 numpy 实现的逻辑回归, 用于因子信号增强.
    支持:
        - 训练 (train)
        - 预测 (predict)
        - 模型保存/加载 (save/load, joblib 序列化)
        - 特征重要性 (get_feature_importance)
        - Feature Flag 透传 (USE_ML_ENHANCED_SELECTOR)

    用法:
        selector = MLEnhancedSelector()
        result = selector.train(X_train, y_train)
        predictions = selector.predict(X_test)
        selector.save("model_v1.pkl")
    """

    def __init__(
        self,
        config: TrainingConfig | None = None,
        model_dir: str | Path | None = None,
    ) -> None:
        self.config = config or TrainingConfig()
        self.model_dir = Path(model_dir) if model_dir else DEFAULT_MODEL_DIR
        self.model_dir.mkdir(parents=True, exist_ok=True)

        # 内部模型 (延迟初始化)
        self._model: _LogisticRegressionNumpy | None = None
        self._is_trained: bool = False
        self._feature_names: list[str] | None = None
        self._training_result: TrainingResult | None = None

    # ============================================================
    # 训练
    # ============================================================
    def train(
        self,
        X: np.ndarray | pd.DataFrame | Sequence[Sequence[float]],  # noqa: N803
        y: np.ndarray | pd.Series | Sequence[float],
        feature_names: list[str] | None = None,
    ) -> TrainingResult:
        """训练 ML 模型.

        Args:
            X: 特征矩阵 (n_samples, n_features)
            y: 标签 (n_samples,) 二分类 {0, 1}
            feature_names: 特征名列表 (可选, 默认 f0, f1, ...)

        Returns:
            TrainingResult 训练结果
        """
        # 输入标准化
        X_arr, y_arr, feature_names = self._normalize_input(X, y, feature_names)  # noqa: N806

        # 输入验证
        if len(X_arr) < 10:
            raise MLEnhancedSelectorError(f"训练样本不足: {len(X_arr)} < 10")
        if X_arr.shape[1] < 1:
            raise MLEnhancedSelectorError("特征数不足: 至少需要 1 个特征")
        if len(np.unique(y_arr)) < 2:
            raise MLEnhancedSelectorError(f"标签类别不足: 至少需要 2 类, 实际 {np.unique(y_arr)}")

        # 初始化模型
        self._model = _LogisticRegressionNumpy(
            learning_rate=self.config.learning_rate,
            n_iterations=self.config.n_iterations,
            l2_reg=self.config.l2_reg,
            random_state=self.config.random_state,
        )

        # 训练
        final_loss, history = self._model.fit(X_arr, y_arr)

        # 构建结果
        importance = self._model.get_feature_importance(feature_names)
        self._feature_names = feature_names
        self._is_trained = True
        self._training_result = TrainingResult(
            n_samples=len(X_arr),
            n_features=X_arr.shape[1],
            final_loss=final_loss,
            n_iterations=self.config.n_iterations,
            feature_importance=importance,
            training_history=history,
        )

        logger.info(
            "[MLEnhancedSelector] 训练完成: samples=%d, features=%d, loss=%.4f, iters=%d",
            len(X_arr),
            X_arr.shape[1],
            final_loss,
            self.config.n_iterations,
        )
        return self._training_result

    # ============================================================
    # 预测
    # ============================================================
    def predict_proba(
        self,
        X: np.ndarray | pd.DataFrame | Sequence[Sequence[float]],  # noqa: N803
    ) -> np.ndarray:
        """预测概率.

        Args:
            X: 特征矩阵

        Returns:
            概率数组 ∈ [0, 1]
        """
        self._check_trained()
        assert self._model is not None  # mypy narrow: _check_trained guarantees non-None
        X_arr = self._normalize_X(X)  # noqa: N803
        return self._model.predict_proba(X_arr)

    def predict(
        self,
        X: np.ndarray | pd.DataFrame | Sequence[Sequence[float]],  # noqa: N803
        threshold: float = 0.5,
    ) -> np.ndarray:
        """预测类别.

        Args:
            X: 特征矩阵
            threshold: 分类阈值 (默认 0.5)

        Returns:
            预测标签 {0, 1}
        """
        self._check_trained()
        assert self._model is not None  # mypy narrow
        X_arr = self._normalize_X(X)  # noqa: N803
        return self._model.predict(X_arr, threshold)

    # ============================================================
    # 模型保存/加载
    # ============================================================
    def save(self, filename: str) -> Path:
        """保存模型到 model_dir/filename.

        Args:
            filename: 文件名 (如 "model_v1.pkl")

        Returns:
            保存的文件路径
        """
        self._check_trained()
        assert self._model is not None  # mypy narrow
        import joblib

        filepath = self.model_dir / filename
        # 保存模型权重 + 配置 + 特征名
        state = {
            "weights": self._model.weights,
            "bias": self._model.bias,
            "config": self.config.__dict__,
            "feature_names": self._feature_names,
            "training_result": self._training_result.__dict__ if self._training_result else None,
        }
        joblib.dump(state, filepath)
        logger.info("[MLEnhancedSelector] 模型已保存: %s", filepath)
        return filepath

    def load(self, filename: str) -> None:
        """从 model_dir/filename 加载模型.

        Args:
            filename: 文件名
        """
        import joblib

        filepath = self.model_dir / filename
        if not filepath.exists():
            raise ModelLoadError(f"模型文件不存在: {filepath}")

        try:
            state = joblib.load(filepath)
            # 恢复配置
            self.config = TrainingConfig(**state["config"])
            # 恢复模型
            self._model = _LogisticRegressionNumpy(
                learning_rate=self.config.learning_rate,
                n_iterations=self.config.n_iterations,
                l2_reg=self.config.l2_reg,
                random_state=self.config.random_state,
            )
            self._model.weights = state["weights"]
            self._model.bias = state["bias"]
            # 恢复特征名和训练结果
            self._feature_names = state.get("feature_names")
            tr_dict = state.get("training_result")
            if tr_dict:
                self._training_result = TrainingResult(**tr_dict)
            self._is_trained = True
            logger.info("[MLEnhancedSelector] 模型已加载: %s", filepath)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            raise ModelLoadError(f"加载模型失败: {e}") from e

    # ============================================================
    # 特征重要性
    # ============================================================
    def get_feature_importance(self) -> dict[str, float]:
        """获取特征重要性.

        Returns:
            {feature_name: importance}
        """
        self._check_trained()
        assert self._model is not None  # mypy narrow
        return self._model.get_feature_importance(self._feature_names)

    # ============================================================
    # 属性
    # ============================================================
    @property
    def is_trained(self) -> bool:
        """是否已训练."""
        return self._is_trained

    @property
    def training_result(self) -> TrainingResult | None:
        """训练结果."""
        return self._training_result

    # ============================================================
    # 辅助方法
    # ============================================================
    def _check_trained(self) -> None:
        """检查模型是否已训练."""
        if not self._is_trained or self._model is None:
            raise ModelNotTrainedError("模型未训练, 请先调用 train()")

    @staticmethod
    def _normalize_input(
        X: np.ndarray | pd.DataFrame | Sequence[Sequence[float]],  # noqa: N803
        y: np.ndarray | pd.Series | Sequence[float],
        feature_names: list[str] | None = None,
    ) -> tuple[np.ndarray, np.ndarray, list[str]]:
        """标准化输入."""
        # X
        if isinstance(X, pd.DataFrame):
            if feature_names is None:
                feature_names = X.columns.tolist()
            X_arr = X.values.astype(float)  # noqa: N806
        elif isinstance(X, np.ndarray):
            X_arr = X.astype(float)  # noqa: N806
        else:
            X_arr = np.array(X, dtype=float)  # noqa: N806

        # y
        if isinstance(y, pd.Series):
            y_arr = y.values.astype(float)
        elif isinstance(y, np.ndarray):
            y_arr = y.astype(float)
        else:
            y_arr = np.array(y, dtype=float)

        # 二分类标签 {0, 1}
        unique = np.unique(y_arr)
        if len(unique) == 2:
            # 自动映射到 {0, 1}
            if set(unique.tolist()) != {0.0, 1.0}:
                y_arr = (y_arr == unique[-1]).astype(float)

        # 特征名
        if feature_names is None:
            feature_names = [f"f{i}" for i in range(X_arr.shape[1])]

        return X_arr, y_arr, feature_names

    @staticmethod
    def _normalize_X(
        X: np.ndarray | pd.DataFrame | Sequence[Sequence[float]],  # noqa: N803
    ) -> np.ndarray:
        """标准化预测输入 X."""
        if isinstance(X, pd.DataFrame):
            return cast(np.ndarray, X.values.astype(float))
        if isinstance(X, np.ndarray):
            return cast(np.ndarray, X.astype(float))
        return cast(np.ndarray, np.array(X, dtype=float))


# ============================================================
# 模块级便捷函数
# ============================================================
def is_ml_selector_enabled() -> bool:
    """检查 USE_ML_ENHANCED_SELECTOR 是否启用 (Feature Flag 透传, HC-1).

    默认 False, 关闭时调用方应降级为等权模式.
    """
    try:
        from utils.infra.feature_flags import is_enabled

        return bool(is_enabled("USE_ML_ENHANCED_SELECTOR"))
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError): # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
        # Feature Flag 框架不可用时, fail-safe 返回 False
        return False


def create_default_selector(
    model_dir: str | Path | None = None,
) -> MLEnhancedSelector:
    """创建默认配置的 ML 选择器.

    Args:
        model_dir: 模型保存目录 (可选)

    Returns:
        MLEnhancedSelector 实例
    """
    return MLEnhancedSelector(model_dir=model_dir)


__all__ = [
    "MLEnhancedSelector",
    "MLEnhancedSelectorError",
    "ModelLoadError",
    "ModelNotTrainedError",
    "TrainingConfig",
    "TrainingResult",
    "create_default_selector",
    "is_ml_selector_enabled",
]
