"""制度门控 Transformer (Adaptive Financial Transformer)
=====================================================

文献依据: #58 Adaptive Financial Transformer (Regime-Gated) (2026.06)
任务: LIT-5.1 制度门控 Transformer 集成
开源参考: Dishan18/FinancialTransformer

核心设计
--------
1. FeatureSemanticMapper: 95 特征 → 11 语义类映射 (降维)
   - 趋势类/动量类/反转类/波动率类/流动性类/价值类/成长类/质量类/情绪类/宏观类/技术形态类
2. RegimeDetector: 市场制度检测 (低波/高波/趋势/反转)
3. RegimeGatedTransformer: 制度门控 Transformer
   - 不同制度下使用不同的注意力权重
   - gate(regime) × attention(features)
4. AdaptiveFinancialTransformer: 集成接口

验收标准
--------
- 95 特征 → 11 语义类 (复杂度降 ≥ 10%)
- 制度门控: 不同制度不同参数

使用示例
--------
    from utils.regime_gated_transformer import AdaptiveFinancialTransformer

    model = AdaptiveFinancialTransformer()
    result = model.forward(features_95, regime_hint=0.5)
    print(result.predictions, result.regime, result.complexity_reduction)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any

import numpy as np

logger = logging.getLogger("regime_gated_transformer")


# ============================================================
# 枚举
# ============================================================

class SemanticClass(str, Enum):
    """11 个语义类."""
    TREND = "trend"  # 趋势类
    MOMENTUM = "momentum"  # 动量类
    REVERSAL = "reversal"  # 反转类
    VOLATILITY = "volatility"  # 波动率类
    LIQUIDITY = "liquidity"  # 流动性类
    VALUE = "value"  # 价值类
    GROWTH = "growth"  # 成长类
    QUALITY = "quality"  # 质量类
    SENTIMENT = "sentiment"  # 情绪类
    MACRO = "macro"  # 宏观类
    TECHNICAL = "technical"  # 技术形态类


class MarketRegime(str, Enum):
    """市场制度."""
    LOW_VOL = "low_vol"  # 低波动
    HIGH_VOL = "high_vol"  # 高波动
    TRENDING = "trending"  # 趋势
    REVERSING = "reversing"  # 反转


# ============================================================
# 特征语义映射 (95 → 11)
# ============================================================

# 95 特征 → 11 语义类的映射 (每类约 8-9 个特征)
DEFAULT_FEATURE_MAPPING: dict[SemanticClass, list[int]] = {
    SemanticClass.TREND: list(range(0, 9)),  # 0-8: MA/EMA/趋势线
    SemanticClass.MOMENTUM: list(range(9, 18)),  # 9-17: RSI/MACD/动量
    SemanticClass.REVERSAL: list(range(18, 27)),  # 18-26: KDJ/威廉/反转
    SemanticClass.VOLATILITY: list(range(27, 36)),  # 27-35: ATR/布林/波动率
    SemanticClass.LIQUIDITY: list(range(36, 45)),  # 36-44: 换手率/成交量/流动性
    SemanticClass.VALUE: list(range(45, 54)),  # 45-53: PE/PB/股息率
    SemanticClass.GROWTH: list(range(54, 63)),  # 54-62: 营收增长/利润增长
    SemanticClass.QUALITY: list(range(63, 72)),  # 63-71: ROE/ROA/资产负债率
    SemanticClass.SENTIMENT: list(range(72, 81)),  # 72-80: 情绪指标/新闻
    SemanticClass.MACRO: list(range(81, 90)),  # 81-89: 利率/CPI/PMI
    SemanticClass.TECHNICAL: list(range(90, 95)),  # 90-94: 技术形态
}


@dataclass
class SemanticMappingResult:
    """语义映射结果."""
    semantic_features: np.ndarray  # (11,) 语义特征向量
    class_contributions: dict[str, float]  # 各类贡献
    n_original_features: int  # 原始特征数
    n_semantic_classes: int  # 语义类数


class FeatureSemanticMapper:
    """95 特征 → 11 语义类映射.

    通过加权聚合将 95 个特征映射到 11 个语义类,
    实现降维和可解释性。
    """

    def __init__(
        self,
        feature_mapping: dict[SemanticClass, list[int]] | None = None,
        n_features: int = 95,
    ) -> None:
        self.mapping = feature_mapping or DEFAULT_FEATURE_MAPPING
        self.n_features = n_features
        self.n_classes = len(self.mapping)
        # 各类的聚合权重 (均匀初始化)
        self._weights: dict[SemanticClass, np.ndarray] = {}
        for cls, indices in self.mapping.items():
            self._weights[cls] = np.ones(len(indices)) / len(indices)

    def map_features(self, features: np.ndarray) -> SemanticMappingResult:
        """将 95 维特征映射到 11 维语义特征.

        Args:
            features: (95,) 或 (N, 95) 特征向量

        Returns:
            SemanticMappingResult
        """
        if features.ndim == 1:
            features = features.reshape(1, -1)
        n_samples = features.shape[0]

        semantic_features = np.zeros((n_samples, self.n_classes))
        contributions: dict[str, float] = {}

        for i, (cls, indices) in enumerate(self.mapping.items()):
            cls_features = features[:, indices]  # (N, n_cls)
            weights = self._weights[cls]
            # 加权聚合: 均值 + 标准差 (捕捉分布)
            weighted_mean = np.mean(cls_features * weights, axis=1)
            semantic_features[:, i] = weighted_mean
            contributions[cls.value] = float(np.mean(np.abs(weighted_mean)))

        return SemanticMappingResult(
            semantic_features=semantic_features.squeeze(),
            class_contributions=contributions,
            n_original_features=self.n_features,
            n_semantic_classes=self.n_classes,
        )

    def complexity_reduction(self) -> float:
        """复杂度降低比例."""
        return 1.0 - self.n_classes / self.n_features


# ============================================================
# 制度检测
# ============================================================

@dataclass
class RegimeDetectionResult:
    """制度检测结果."""
    regime: MarketRegime
    confidence: float
    regime_probs: dict[str, float]  # 各制度概率


class RegimeDetector:
    """市场制度检测.

    基于语义特征检测当前市场制度:
    - 低波动: 波动率类特征低
    - 高波动: 波动率类特征高
    - 趋势: 趋势类特征强
    - 反转: 反转类特征强
    """

    def __init__(self, vol_threshold: float = 0.5, trend_threshold: float = 0.3) -> None:
        self.vol_threshold = vol_threshold
        self.trend_threshold = trend_threshold

    def detect(
        self,
        semantic_features: np.ndarray,
        regime_hint: float | None = None,
    ) -> RegimeDetectionResult:
        """检测市场制度.

        Args:
            semantic_features: (11,) 语义特征
            regime_hint: 外部制度提示 (0=低波, 1=高波, None=自动检测)

        Returns:
            RegimeDetectionResult
        """
        # 语义类索引: 0=趋势, 1=动量, 2=反转, 3=波动率
        trend_score = abs(semantic_features[0]) if len(semantic_features) > 0 else 0
        momentum_score = abs(semantic_features[1]) if len(semantic_features) > 1 else 0
        reversal_score = abs(semantic_features[2]) if len(semantic_features) > 2 else 0
        vol_score = abs(semantic_features[3]) if len(semantic_features) > 3 else 0

        # 制度概率 (softmax)
        scores = {
            MarketRegime.LOW_VOL: -vol_score + 1.0,
            MarketRegime.HIGH_VOL: vol_score + 1.0,
            MarketRegime.TRENDING: trend_score + momentum_score,
            MarketRegime.REVERSING: reversal_score,
        }
        # 外部提示加权
        if regime_hint is not None:
            if regime_hint < 0.3:
                scores[MarketRegime.LOW_VOL] += 2.0
            elif regime_hint > 0.7:
                scores[MarketRegime.HIGH_VOL] += 2.0

        # Softmax 归一化
        score_values = np.array(list(scores.values()))
        exp_scores = np.exp(score_values - np.max(score_values))
        probs = exp_scores / exp_scores.sum()

        regime_probs = {r.value: float(p) for r, p in zip(scores.keys(), probs)}
        best_idx = int(np.argmax(probs))
        regime = list(scores.keys())[best_idx]
        confidence = float(probs[best_idx])

        return RegimeDetectionResult(
            regime=regime,
            confidence=confidence,
            regime_probs=regime_probs,
        )


# ============================================================
# 制度门控 Transformer
# ============================================================

@dataclass
class TransformerConfig:
    """Transformer 配置."""
    n_heads: int = 4  # 注意力头数
    d_model: int = 11  # 模型维度 (= 语义类数)
    d_ff: int = 32  # 前馈网络维度
    n_layers: int = 2  # 层数
    dropout: float = 0.1  # dropout


class RegimeGatedTransformer:
    """制度门控 Transformer.

    核心创新: 不同市场制度下使用不同的注意力权重
    gate(regime) × attention(features)

    用 numpy 实现 (无 PyTorch 依赖):
    - 多头自注意力
    - 前馈网络
    - 制度门控
    """

    def __init__(
        self,
        config: TransformerConfig | None = None,
        seed: int | None = 42,
    ) -> None:
        self.config = config or TransformerConfig()
        self._rng = np.random.default_rng(seed)
        self._init_weights()

    def _init_weights(self) -> None:
        """初始化权重."""
        c = self.config
        # 每个制度一组注意力权重
        self._regime_weights: dict[MarketRegime, dict[str, np.ndarray]] = {}
        for regime in MarketRegime:
            self._regime_weights[regime] = {
                "W_q": self._rng.standard_normal((c.d_model, c.d_model)) * 0.1,
                "W_k": self._rng.standard_normal((c.d_model, c.d_model)) * 0.1,
                "W_v": self._rng.standard_normal((c.d_model, c.d_model)) * 0.1,
                "W_ff1": self._rng.standard_normal((c.d_model, c.d_ff)) * 0.1,
                "W_ff2": self._rng.standard_normal((c.d_ff, c.d_model)) * 0.1,
            }
        # 制度门控权重
        self._gate_weights = self._rng.standard_normal((len(MarketRegime),)) * 0.1

    def _attention(
        self,
        x: np.ndarray,
        weights: dict[str, np.ndarray],
    ) -> np.ndarray:
        """单头自注意力 (简化)."""
        Q = x @ weights["W_q"]
        K = x @ weights["W_k"]
        V = x @ weights["W_v"]
        d_k = max(Q.shape[-1], 1)
        scores = Q @ K.T / math.sqrt(d_k)
        # Softmax
        scores = scores - np.max(scores, axis=-1, keepdims=True)
        attn = np.exp(scores)
        attn = attn / np.sum(attn, axis=-1, keepdims=True)
        return attn @ V

    def _feedforward(
        self,
        x: np.ndarray,
        weights: dict[str, np.ndarray],
    ) -> np.ndarray:
        """前馈网络 (ReLU)."""
        hidden = x @ weights["W_ff1"]
        hidden = np.maximum(hidden, 0)  # ReLU
        return hidden @ weights["W_ff2"]

    def forward(
        self,
        semantic_features: np.ndarray,
        regime: MarketRegime,
        regime_probs: dict[str, float],
    ) -> np.ndarray:
        """前向传播.

        Args:
            semantic_features: (11,) 语义特征
            regime: 当前制度
            regime_probs: 各制度概率

        Returns:
            (11,) Transformer 输出
        """
        x = semantic_features.copy()
        weights = self._regime_weights[regime]

        # 制度门控: gate = sigmoid(Σ prob_r × gate_weight_r)
        gate = 0.0
        for i, r in enumerate(MarketRegime):
            gate += regime_probs.get(r.value, 0.0) * self._gate_weights[i]
        gate = 1.0 / (1.0 + math.exp(-gate))  # sigmoid

        # Transformer 层
        for _ in range(self.config.n_layers):
            # 自注意力 + 残差
            attn_out = self._attention(x.reshape(1, -1), weights).squeeze()
            x = x + gate * attn_out  # 制度门控
            # 前馈 + 残差
            ff_out = self._feedforward(x, weights)
            x = x + gate * ff_out

        return x


# ============================================================
# 自适应金融 Transformer (集成接口)
# ============================================================

@dataclass
class TransformerOutput:
    """Transformer 输出."""
    predictions: np.ndarray  # 预测值
    regime: MarketRegime  # 检测制度
    regime_confidence: float  # 制度置信度
    semantic_features: np.ndarray  # 语义特征
    class_contributions: dict[str, float]  # 各类贡献
    complexity_reduction: float  # 复杂度降低
    gate_value: float  # 门控值


class AdaptiveFinancialTransformer:
    """自适应金融 Transformer.

    集成: 特征语义映射 + 制度检测 + 制度门控 Transformer

    用法:
        model = AdaptiveFinancialTransformer()
        result = model.forward(features_95, regime_hint=0.5)
    """

    def __init__(
        self,
        transformer_config: TransformerConfig | None = None,
        n_features: int = 95,
        seed: int | None = 42,
    ) -> None:
        self.feature_mapper = FeatureSemanticMapper(n_features=n_features)
        self.regime_detector = RegimeDetector()
        self.transformer = RegimeGatedTransformer(transformer_config, seed=seed)
        self.n_features = n_features

    def forward(
        self,
        features: np.ndarray,
        regime_hint: float | None = None,
    ) -> TransformerOutput:
        """前向传播.

        Args:
            features: (95,) 特征向量
            regime_hint: 外部制度提示

        Returns:
            TransformerOutput
        """
        # 1. 特征语义映射 (95 → 11)
        mapping_result = self.feature_mapper.map_features(features)
        semantic_features = mapping_result.semantic_features

        # 2. 制度检测
        regime_result = self.regime_detector.detect(semantic_features, regime_hint)

        # 3. 制度门控 Transformer
        output = self.transformer.forward(
            semantic_features, regime_result.regime, regime_result.regime_probs,
        )

        # 4. 预测 (简单线性投影到标量)
        predictions = np.tanh(np.sum(output))  # [-1, 1]

        # 5. 门控值
        gate = 0.0
        for i, r in enumerate(MarketRegime):
            gate += regime_result.regime_probs.get(r.value, 0.0) * self.transformer._gate_weights[i]
        gate_value = 1.0 / (1.0 + math.exp(-gate))

        return TransformerOutput(
            predictions=predictions,
            regime=regime_result.regime,
            regime_confidence=regime_result.confidence,
            semantic_features=semantic_features,
            class_contributions=mapping_result.class_contributions,
            complexity_reduction=self.feature_mapper.complexity_reduction(),
            gate_value=gate_value,
        )

    def batch_forward(
        self,
        features_batch: np.ndarray,
        regime_hint: float | None = None,
    ) -> list[TransformerOutput]:
        """批量前向传播."""
        results: list[TransformerOutput] = []
        for i in range(features_batch.shape[0]):
            result = self.forward(features_batch[i], regime_hint)
            results.append(result)
        return results

    def complexity_report(self) -> dict[str, Any]:
        """复杂度报告."""
        return {
            "n_original_features": self.n_features,
            "n_semantic_classes": self.feature_mapper.n_classes,
            "complexity_reduction_pct": self.feature_mapper.complexity_reduction() * 100,
            "n_regimes": len(MarketRegime),
            "transformer_config": {
                "n_heads": self.transformer.config.n_heads,
                "d_model": self.transformer.config.d_model,
                "d_ff": self.transformer.config.d_ff,
                "n_layers": self.transformer.config.n_layers,
            },
        }


# ============================================================
# CLI 入口
# ============================================================

def main() -> None:
    """CLI 入口: 演示制度门控 Transformer."""
    print("=" * 60)
    print("制度门控 Transformer (Adaptive Financial Transformer)")
    print("文献: #58 Adaptive Financial Transformer (2026.06)")
    print("=" * 60)

    model = AdaptiveFinancialTransformer()

    # === 1. 复杂度报告 ===
    print("\n--- 1. 复杂度报告 ---")
    report = model.complexity_report()
    print(f"  原始特征数: {report['n_original_features']}")
    print(f"  语义类数: {report['n_semantic_classes']}")
    print(f"  复杂度降低: {report['complexity_reduction_pct']:.1f}%")
    print(f"  制度数: {report['n_regimes']}")

    # === 2. 单样本前向传播 ===
    print("\n--- 2. 单样本前向传播 ---")
    features = np.random.default_rng(42).standard_normal(95)
    result = model.forward(features)
    print(f"  预测值: {result.predictions:.4f}")
    print(f"  检测制度: {result.regime.value}")
    print(f"  制度置信度: {result.regime_confidence:.2%}")
    print(f"  门控值: {result.gate_value:.4f}")

    # === 3. 语义类贡献 ===
    print("\n--- 3. 语义类贡献 ---")
    for cls, contrib in sorted(result.class_contributions.items(), key=lambda x: -abs(x[1])):
        print(f"  {cls:>12s}: {contrib:+.4f}")

    # === 4. 不同制度提示对比 ===
    print("\n--- 4. 不同制度提示对比 ---")
    for hint in [0.0, 0.3, 0.5, 0.7, 1.0]:
        r = model.forward(features, regime_hint=hint)
        print(f"  hint={hint:.1f}: regime={r.regime.value:>10s}, conf={r.regime_confidence:.2%}, pred={r.predictions:+.4f}")

    # === 5. 批量前向传播 ===
    print("\n--- 5. 批量前向传播 (10 样本) ---")
    batch = np.random.default_rng(123).standard_normal((10, 95))
    results = model.batch_forward(batch)
    regimes = [r.regime.value for r in results]
    print(f"  样本数: {len(results)}")
    print(f"  制度分布: {dict((r, regimes.count(r)) for r in set(regimes))}")
    print(f"  预测均值: {np.mean([r.predictions for r in results]):+.4f}")


if __name__ == "__main__":
    main()
