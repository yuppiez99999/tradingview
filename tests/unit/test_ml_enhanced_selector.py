# -*- coding: utf-8 -*-
"""T4.2 ML 增强选择器单元测试.

验证:
    1. MLEnhancedSelector 基础接口 (train/predict/save/load)
    2. 训练结果 (TrainingResult 字段)
    3. 预测概率与类别
    4. 模型保存/加载 (joblib 持久化)
    5. 特征重要性
    6. 异常处理 (未训练/样本不足/标签类别不足)
    7. Feature Flag 透传 (HC-1)
    8. 便捷函数
    9. 输入类型兼容 (numpy/DataFrame/list)
    10. 数值稳定性 (sigmoid clip)
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from utils.alpha.ml_enhanced_selector import (
    MLEnhancedSelector,
    MLEnhancedSelectorError,
    ModelLoadError,
    ModelNotTrainedError,
    TrainingConfig,
    TrainingResult,
    create_default_selector,
    is_ml_selector_enabled,
)


# ============================================================
# 测试数据生成器
# ============================================================
def make_classification_data(
    n_samples: int = 200,
    n_features: int = 5,
    noise: float = 0.1,
    seed: int = 42,
) -> tuple:
    """生成二分类测试数据.

    线性可分 + 噪声, 用于测试逻辑回归.
    """
    rng = np.random.default_rng(seed)
    X = rng.normal(0, 1, (n_samples, n_features))
    # 真实权重: 前 3 个特征有信号, 后 2 个噪声
    true_weights = np.array([1.5, -1.0, 0.8, 0.0, 0.0][:n_features])
    logits = X @ true_weights + noise * rng.normal(0, 1, n_samples)
    probs = 1.0 / (1.0 + np.exp(-logits))
    y = (probs >= 0.5).astype(float)
    return X, y


# ============================================================
# 1. 基础接口测试
# ============================================================
class TestBasicInterface:
    """MLEnhancedSelector 基础接口测试."""

    def test_init_default_config(self):
        """默认配置初始化."""
        selector = MLEnhancedSelector()
        assert selector.config.learning_rate == 0.01
        assert selector.config.n_iterations == 1000
        assert selector.config.l2_reg == 0.01
        assert selector.config.backend == "numpy"

    def test_init_custom_config(self):
        """自定义配置初始化."""
        config = TrainingConfig(learning_rate=0.05, n_iterations=500, l2_reg=0.1)
        selector = MLEnhancedSelector(config=config)
        assert selector.config.learning_rate == 0.05
        assert selector.config.n_iterations == 500
        assert selector.config.l2_reg == 0.1

    def test_init_model_dir_created(self, tmp_path):
        """model_dir 自动创建."""
        model_dir = tmp_path / "test_models"
        MLEnhancedSelector(model_dir=model_dir)
        assert model_dir.exists()

    def test_is_trained_default_false(self):
        """未训练时 is_trained=False."""
        selector = MLEnhancedSelector()
        assert selector.is_trained is False

    def test_train_returns_training_result(self):
        """train() 返回 TrainingResult."""
        selector = MLEnhancedSelector(
            config=TrainingConfig(n_iterations=100),
            model_dir=Path("models/test_ml_selector"),
        )
        X, y = make_classification_data()
        result = selector.train(X, y)
        assert isinstance(result, TrainingResult)

    def test_train_sets_is_trained(self):
        """训练后 is_trained=True."""
        selector = MLEnhancedSelector(
            config=TrainingConfig(n_iterations=100),
            model_dir=Path("models/test_ml_selector"),
        )
        X, y = make_classification_data()
        selector.train(X, y)
        assert selector.is_trained is True


# ============================================================
# 2. 训练结果测试
# ============================================================
class TestTrainingResult:
    """训练结果测试."""

    def test_training_result_fields(self):
        """TrainingResult 含全部字段."""
        selector = MLEnhancedSelector(
            config=TrainingConfig(n_iterations=100),
            model_dir=Path("models/test_ml_selector"),
        )
        X, y = make_classification_data(n_samples=100, n_features=3)
        result = selector.train(X, y, feature_names=["f1", "f2", "f3"])
        assert result.n_samples == 100
        assert result.n_features == 3
        assert result.final_loss > 0.0
        assert result.n_iterations == 100
        assert "f1" in result.feature_importance
        assert "f2" in result.feature_importance
        assert "f3" in result.feature_importance
        assert len(result.training_history) > 0

    def test_training_loss_decreases(self):
        """训练损失应该下降."""
        selector = MLEnhancedSelector(
            config=TrainingConfig(n_iterations=500),
            model_dir=Path("models/test_ml_selector"),
        )
        X, y = make_classification_data()
        result = selector.train(X, y)
        # 最终损失应小于初始损失
        assert len(result.training_history) >= 2
        assert result.training_history[-1] <= result.training_history[0]

    def test_training_result_property(self):
        """training_result 属性访问."""
        selector = MLEnhancedSelector(
            config=TrainingConfig(n_iterations=100),
            model_dir=Path("models/test_ml_selector"),
        )
        X, y = make_classification_data()
        selector.train(X, y)
        assert selector.training_result is not None
        assert selector.training_result.n_samples > 0

    def test_feature_importance_non_negative(self):
        """特征重要性非负."""
        selector = MLEnhancedSelector(
            config=TrainingConfig(n_iterations=100),
            model_dir=Path("models/test_ml_selector"),
        )
        X, y = make_classification_data(n_features=4)
        result = selector.train(X, y)
        for _name, imp in result.feature_importance.items():
            assert imp >= 0.0

    def test_feature_importance_signal_vs_noise(self):
        """信号特征重要性 > 噪声特征."""
        selector = MLEnhancedSelector(
            config=TrainingConfig(n_iterations=500, l2_reg=0.001),
            model_dir=Path("models/test_ml_selector"),
        )
        # 前 3 个特征有信号, 后 2 个噪声
        X, y = make_classification_data(n_samples=500, n_features=5, seed=42)
        result = selector.train(X, y, feature_names=["sig1", "sig2", "sig3", "noise1", "noise2"])
        # 信号特征重要性应 > 噪声特征
        sig_imp = sum(result.feature_importance[f] for f in ["sig1", "sig2", "sig3"])
        noise_imp = sum(result.feature_importance[f] for f in ["noise1", "noise2"])
        assert sig_imp > noise_imp


# ============================================================
# 3. 预测测试
# ============================================================
class TestPrediction:
    """预测测试."""

    def test_predict_proba_range(self):
        """predict_proba 返回 [0, 1] 之间的概率."""
        selector = MLEnhancedSelector(
            config=TrainingConfig(n_iterations=200),
            model_dir=Path("models/test_ml_selector"),
        )
        X, y = make_classification_data()
        selector.train(X, y)
        probs = selector.predict_proba(X)
        assert probs.shape == (len(X),)
        assert np.all(probs >= 0.0)
        assert np.all(probs <= 1.0)

    def test_predict_binary_labels(self):
        """predict 返回 {0, 1} 二分类标签."""
        selector = MLEnhancedSelector(
            config=TrainingConfig(n_iterations=200),
            model_dir=Path("models/test_ml_selector"),
        )
        X, y = make_classification_data()
        selector.train(X, y)
        preds = selector.predict(X)
        unique = set(np.unique(preds).tolist())
        assert unique.issubset({0, 1})

    def test_predict_accuracy_on_separable_data(self):
        """线性可分数据上预测准确率应 > 0.7."""
        selector = MLEnhancedSelector(
            config=TrainingConfig(n_iterations=1000, learning_rate=0.1),
            model_dir=Path("models/test_ml_selector"),
        )
        X, y = make_classification_data(n_samples=500, noise=0.05, seed=42)
        selector.train(X, y)
        preds = selector.predict(X)
        accuracy = float(np.mean(preds == y))
        assert accuracy > 0.7

    def test_predict_custom_threshold(self):
        """自定义阈值预测."""
        selector = MLEnhancedSelector(
            config=TrainingConfig(n_iterations=200),
            model_dir=Path("models/test_ml_selector"),
        )
        X, y = make_classification_data()
        selector.train(X, y)
        # 高阈值 → 更少预测为 1
        # 注: 使用 np.count_nonzero 替代 .sum() 规避 pytest-cov + numpy 2.x 的 C 级追踪冲突
        preds_high = selector.predict(X, threshold=0.9)
        preds_low = selector.predict(X, threshold=0.1)
        n_high = int(np.count_nonzero(preds_high))
        n_low = int(np.count_nonzero(preds_low))
        assert n_high <= n_low

    def test_predict_not_trained_raises(self):
        """未训练时 predict 抛 ModelNotTrainedError."""
        selector = MLEnhancedSelector()
        X = np.array([[1.0, 2.0]])
        with pytest.raises(ModelNotTrainedError):
            selector.predict(X)

    def test_predict_proba_not_trained_raises(self):
        """未训练时 predict_proba 抛异常."""
        selector = MLEnhancedSelector()
        X = np.array([[1.0, 2.0]])
        with pytest.raises(ModelNotTrainedError):
            selector.predict_proba(X)


# ============================================================
# 4. 模型保存/加载测试
# ============================================================
class TestModelPersistence:
    """模型保存/加载测试."""

    def test_save_returns_path(self, tmp_path):
        """save() 返回文件路径."""
        selector = MLEnhancedSelector(
            config=TrainingConfig(n_iterations=50),
            model_dir=tmp_path,
        )
        X, y = make_classification_data()
        selector.train(X, y)
        path = selector.save("test_model.pkl")
        assert path.exists()
        assert path.name == "test_model.pkl"

    def test_load_restores_model(self, tmp_path):
        """load() 恢复模型."""
        selector1 = MLEnhancedSelector(
            config=TrainingConfig(n_iterations=200, learning_rate=0.05),
            model_dir=tmp_path,
        )
        X, y = make_classification_data()
        selector1.train(X, y)
        preds1 = selector1.predict(X)

        # 保存
        selector1.save("model.pkl")

        # 新实例加载
        selector2 = MLEnhancedSelector(model_dir=tmp_path)
        selector2.load("model.pkl")

        # 预测结果应一致
        preds2 = selector2.predict(X)
        assert np.array_equal(preds1, preds2)

    def test_load_config_restored(self, tmp_path):
        """load() 恢复配置."""
        selector1 = MLEnhancedSelector(
            config=TrainingConfig(n_iterations=100, learning_rate=0.05, l2_reg=0.1),
            model_dir=tmp_path,
        )
        X, y = make_classification_data()
        selector1.train(X, y)
        selector1.save("model.pkl")

        selector2 = MLEnhancedSelector(model_dir=tmp_path)
        selector2.load("model.pkl")
        assert selector2.config.learning_rate == 0.05
        assert selector2.config.l2_reg == 0.1

    def test_load_not_exists_raises(self, tmp_path):
        """加载不存在的模型抛 ModelLoadError."""
        selector = MLEnhancedSelector(model_dir=tmp_path)
        with pytest.raises(ModelLoadError):
            selector.load("nonexistent.pkl")

    def test_load_corrupted_raises(self, tmp_path):
        """加载损坏的文件抛 ModelLoadError."""
        selector = MLEnhancedSelector(model_dir=tmp_path)
        # 写入非 pkl 文件
        bad_file = tmp_path / "bad.pkl"
        bad_file.write_text("not a valid pkl file")
        with pytest.raises(ModelLoadError):
            selector.load("bad.pkl")

    def test_save_not_trained_raises(self, tmp_path):
        """未训练时 save 抛异常."""
        selector = MLEnhancedSelector(model_dir=tmp_path)
        with pytest.raises(ModelNotTrainedError):
            selector.save("model.pkl")


# ============================================================
# 5. 特征重要性测试
# ============================================================
class TestFeatureImportance:
    """特征重要性测试."""

    def test_get_feature_importance_dict(self):
        """get_feature_importance 返回字典."""
        selector = MLEnhancedSelector(
            config=TrainingConfig(n_iterations=100),
            model_dir=Path("models/test_ml_selector"),
        )
        X, y = make_classification_data(n_features=3)
        selector.train(X, y, feature_names=["a", "b", "c"])
        importance = selector.get_feature_importance()
        assert isinstance(importance, dict)
        assert set(importance.keys()) == {"a", "b", "c"}

    def test_get_feature_importance_not_trained_raises(self):
        """未训练时 get_feature_importance 抛异常."""
        selector = MLEnhancedSelector()
        with pytest.raises(ModelNotTrainedError):
            selector.get_feature_importance()

    def test_default_feature_names(self):
        """默认特征名 f0, f1, ..."""
        selector = MLEnhancedSelector(
            config=TrainingConfig(n_iterations=100),
            model_dir=Path("models/test_ml_selector"),
        )
        X, y = make_classification_data(n_features=3)
        result = selector.train(X, y)  # 不提供 feature_names
        assert "f0" in result.feature_importance
        assert "f1" in result.feature_importance
        assert "f2" in result.feature_importance


# ============================================================
# 6. 异常处理测试
# ============================================================
class TestExceptionHandling:
    """异常处理测试."""

    def test_insufficient_samples_raises(self):
        """训练样本不足 (<10) 抛异常."""
        selector = MLEnhancedSelector(
            config=TrainingConfig(n_iterations=50),
            model_dir=Path("models/test_ml_selector"),
        )
        X = np.array([[1.0, 2.0]] * 5)  # 只有 5 个样本
        y = np.array([0, 1, 0, 1, 0])
        with pytest.raises(MLEnhancedSelectorError) as exc_info:
            selector.train(X, y)
        assert "样本不足" in str(exc_info.value)

    def test_single_class_raises(self):
        """标签只有 1 类抛异常."""
        selector = MLEnhancedSelector(
            config=TrainingConfig(n_iterations=50),
            model_dir=Path("models/test_ml_selector"),
        )
        X = np.random.default_rng(42).normal(0, 1, (50, 3))
        y = np.zeros(50)  # 全 0
        with pytest.raises(MLEnhancedSelectorError) as exc_info:
            selector.train(X, y)
        assert "标签类别" in str(exc_info.value)

    def test_zero_features_raises(self):
        """特征数为 0 抛异常."""
        selector = MLEnhancedSelector(
            config=TrainingConfig(n_iterations=50),
            model_dir=Path("models/test_ml_selector"),
        )
        X = np.array([]).reshape(50, 0)  # 0 特征
        y = np.array([0, 1] * 25)
        with pytest.raises(MLEnhancedSelectorError):
            selector.train(X, y)

    def test_model_not_trained_error_inheritance(self):
        """ModelNotTrainedError 继承 MLEnhancedSelectorError."""
        assert issubclass(ModelNotTrainedError, MLEnhancedSelectorError)

    def test_model_load_error_inheritance(self):
        """ModelLoadError 继承 MLEnhancedSelectorError."""
        assert issubclass(ModelLoadError, MLEnhancedSelectorError)


# ============================================================
# 7. Feature Flag 透传测试 (HC-1)
# ============================================================
class TestFeatureFlag:
    """Feature Flag 透传测试."""

    def test_is_ml_selector_enabled_default_false(self):
        """默认 USE_ML_ENHANCED_SELECTOR=False."""
        # Feature Flag 框架未启用时, fail-safe 返回 False
        result = is_ml_selector_enabled()
        assert isinstance(result, bool)
        # 默认应为 False (HC-1 透传)
        assert result is False

    def test_is_ml_selector_enabled_no_framework(self):
        """Feature Flag 框架不可用时返回 False."""
        # mock 导入失败
        import sys
        original_path = sys.path[:]
        sys.path.clear()
        try:
            result = is_ml_selector_enabled()
            assert result is False
        finally:
            sys.path.extend(original_path)


# ============================================================
# 8. 便捷函数测试
# ============================================================
class TestConvenienceFunctions:
    """便捷函数测试."""

    def test_create_default_selector(self):
        """create_default_selector 返回 MLEnhancedSelector."""
        selector = create_default_selector(model_dir=Path("models/test_ml_selector"))
        assert isinstance(selector, MLEnhancedSelector)
        assert selector.is_trained is False

    def test_create_default_selector_custom_dir(self, tmp_path):
        """create_default_selector 支持自定义目录."""
        selector = create_default_selector(model_dir=tmp_path)
        assert selector.model_dir == tmp_path


# ============================================================
# 9. 输入类型兼容测试
# ============================================================
class TestInputTypes:
    """输入类型兼容测试."""

    def test_numpy_array_input(self):
        """numpy 数组输入."""
        selector = MLEnhancedSelector(
            config=TrainingConfig(n_iterations=100),
            model_dir=Path("models/test_ml_selector"),
        )
        X, y = make_classification_data()
        result = selector.train(X, y)
        assert result.n_samples > 0

    def test_pandas_dataframe_input(self):
        """pandas DataFrame 输入."""
        selector = MLEnhancedSelector(
            config=TrainingConfig(n_iterations=100),
            model_dir=Path("models/test_ml_selector"),
        )
        X, y = make_classification_data(n_features=3)
        df = pd.DataFrame(X, columns=["feat_a", "feat_b", "feat_c"])
        series = pd.Series(y)
        result = selector.train(df, series)
        assert result.n_samples > 0
        assert "feat_a" in result.feature_importance

    def test_list_input(self):
        """list 输入."""
        selector = MLEnhancedSelector(
            config=TrainingConfig(n_iterations=100),
            model_dir=Path("models/test_ml_selector"),
        )
        X, y = make_classification_data(n_features=3, n_samples=50)
        X_list = X.tolist()
        y_list = y.tolist()
        result = selector.train(X_list, y_list)
        assert result.n_samples == 50

    def test_predict_with_dataframe(self):
        """predict 支持 DataFrame."""
        selector = MLEnhancedSelector(
            config=TrainingConfig(n_iterations=100),
            model_dir=Path("models/test_ml_selector"),
        )
        X, y = make_classification_data(n_features=3)
        selector.train(X, y)
        df = pd.DataFrame(X, columns=["a", "b", "c"])
        preds = selector.predict(df)
        assert len(preds) == len(df)

    def test_auto_label_mapping(self):
        """自动标签映射 (非 {0,1} 标签)."""
        selector = MLEnhancedSelector(
            config=TrainingConfig(n_iterations=100),
            model_dir=Path("models/test_ml_selector"),
        )
        X, y = make_classification_data(n_samples=50)
        # 将 {0, 1} 映射为 {-1, 1}
        y_alt = np.where(y == 1, 1, -1).astype(float)
        result = selector.train(X, y_alt)
        assert result.n_samples == 50


# ============================================================
# 10. 数值稳定性测试
# ============================================================
class TestNumericalStability:
    """数值稳定性测试."""

    def test_sigmoid_clip(self):
        """sigmoid 大输入不溢出."""
        from utils.alpha.ml_enhanced_selector import _LogisticRegressionNumpy
        # 极大/极小值
        z = np.array([1e10, -1e10, 0.0, 100.0, -100.0])
        probs = _LogisticRegressionNumpy._sigmoid(z)
        assert np.all(np.isfinite(probs))
        assert probs[0] >= 1.0 - 1e-15  # 极大值 → 接近 1
        assert probs[1] < 1e-200  # 极小值 → 接近 0 (sigmoid 极小值返回非零但极小浮点)
        assert 0.49 < probs[2] < 0.51  # 0 → 0.5

    def test_training_with_outliers(self):
        """含异常值的训练数据."""
        selector = MLEnhancedSelector(
            config=TrainingConfig(n_iterations=100),
            model_dir=Path("models/test_ml_selector"),
        )
        X, y = make_classification_data(n_samples=100)
        # 加入异常值
        X[0] = [1e5] * X.shape[1]
        # 不应抛异常
        result = selector.train(X, y)
        assert result.n_samples == 100

    def test_training_with_constant_feature(self):
        """恒定特征训练."""
        selector = MLEnhancedSelector(
            config=TrainingConfig(n_iterations=100),
            model_dir=Path("models/test_ml_selector"),
        )
        X, y = make_classification_data(n_samples=100, n_features=3)
        # 第 2 列设为恒定值
        X[:, 1] = 5.0
        result = selector.train(X, y, feature_names=["f0", "const", "f2"])
        # 恒定特征的重要性应接近 0
        assert result.feature_importance["const"] < 1.0

    def test_reproducibility(self):
        """训练可重现 (固定 random_state)."""
        config = TrainingConfig(n_iterations=100, random_state=42)
        selector1 = MLEnhancedSelector(config=config, model_dir=Path("models/test_ml_selector"))
        selector2 = MLEnhancedSelector(config=config, model_dir=Path("models/test_ml_selector"))
        X, y = make_classification_data(seed=42)
        selector1.train(X, y)
        selector2.train(X, y)
        # 权重应一致
        np.testing.assert_array_almost_equal(
            selector1._model.weights,  # type: ignore[union-attr]
            selector2._model.weights,  # type: ignore[union-attr]
        )


# ============================================================
# 11. 模块级常量测试
# ============================================================
class TestModuleConstants:
    """模块级常量测试."""

    def test_all_exported(self):
        """__all__ 完整."""
        from utils.alpha import ml_enhanced_selector
        expected = {
            "MLEnhancedSelector",
            "TrainingConfig",
            "TrainingResult",
            "MLEnhancedSelectorError",
            "ModelNotTrainedError",
            "ModelLoadError",
            "is_ml_selector_enabled",
            "create_default_selector",
        }
        assert expected.issubset(set(ml_enhanced_selector.__all__))


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
