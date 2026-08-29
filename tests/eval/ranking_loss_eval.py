"""排序损失函数系统评估
==========================

文献依据: #59 CIKM 2025 — Ranking Loss for Financial Prediction
任务: LIT-5.5 排序损失函数系统评估
依赖: LIT-5.1 制度门控 Transformer

核心设计
--------
三种排序损失函数对比:
1. Pointwise: 逐点损失 (MSE), 将排序视为回归
2. Pairwise: 成对损失 (BPR/RankNet), 比较标的对
3. Listwise: 列表损失 (ListNet/Plackett-Luce), 直接优化排序

评估指标: NDCG@k, MAP, MRR

验收标准
--------
- pointwise/pairwise/listwise 三种损失实现
- 金融排序任务对比 (选股排名)
- 选出最优损失函数

使用示例
--------
    from tests.eval.ranking_loss_eval import RankingLossEvaluator

    evaluator = RankingLossEvaluator()
    result = evaluator.evaluate(predictions, ground_truth)
    best = evaluator.select_best(result)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger("ranking_loss_eval")


# ============================================================
# 排序指标
# ============================================================


def dcg_at_k(rels: np.ndarray, k: int) -> float:
    """DCG@k: 折损累计增益."""
    if len(rels) == 0:
        return 0.0
    k = min(k, len(rels))
    discounts = 1.0 / np.log2(np.arange(2, k + 2))
    return float(np.sum(rels[:k] * discounts))


def ndcg_at_k(predicted_rank: np.ndarray, ideal_rank: np.ndarray, k: int = 10) -> float:
    """NDCG@k: 归一化折损累计增益."""
    dcg = dcg_at_k(predicted_rank, k)
    idcg = dcg_at_k(ideal_rank, k)
    if idcg == 0:
        return 0.0
    return dcg / idcg


def mean_reciprocal_rank(predicted_rank: np.ndarray) -> float:
    """MRR: 平均倒数排名."""
    # predicted_rank: 按预测排序的相关性分数
    # 找到第一个相关项 (rel > 0) 的位置
    for i, rel in enumerate(predicted_rank):
        if rel > 0:
            return 1.0 / (i + 1)
    return 0.0


def average_precision(predicted_rank: np.ndarray) -> float:
    """AP: 平均精度."""
    relevant = 0
    total_precision = 0.0
    for i, rel in enumerate(predicted_rank):
        if rel > 0:
            relevant += 1
            total_precision += relevant / (i + 1)
    if relevant == 0:
        return 0.0
    return total_precision / relevant


# ============================================================
# 排序损失函数
# ============================================================


@dataclass
class LossResult:
    """损失计算结果."""

    loss: float
    loss_name: str
    gradient: np.ndarray | None = None


class PointwiseLoss:
    """Pointwise 损失 (MSE).

    L = Σ (s_i - y_i)² / n

    将排序视为回归问题, 预测分数逼近真实相关性。
    """

    name = "pointwise"

    @staticmethod
    def compute(scores: np.ndarray, labels: np.ndarray) -> LossResult:
        """计算 MSE 损失."""
        diff = scores - labels
        loss = float(np.mean(diff**2))
        grad = 2 * diff / len(scores)
        return LossResult(loss=loss, loss_name="pointwise", gradient=grad)

    @staticmethod
    def predict(features: np.ndarray, weights: np.ndarray) -> np.ndarray:
        """预测分数 (线性模型)."""
        return features @ weights


class PairwiseLoss:
    """Pairwise 损失 (BPR - Bayesian Personalized Ranking).

    L = -Σ_{i,j: y_i > y_j} log(σ(s_i - s_j))

    比较正样本对 (i优于j), 鼓励 s_i > s_j。
    """

    name = "pairwise"

    @staticmethod
    def compute(scores: np.ndarray, labels: np.ndarray) -> LossResult:
        """计算 BPR 损失."""
        n = len(scores)
        loss = 0.0
        grad = np.zeros(n)
        count = 0

        for i in range(n):
            for j in range(n):
                if labels[i] > labels[j]:
                    diff = scores[i] - scores[j]
                    # sigmoid
                    sig = 1.0 / (1.0 + math.exp(-diff))
                    loss -= math.log(sig + 1e-10)
                    # 梯度
                    grad[i] += 1 - sig
                    grad[j] -= 1 - sig
                    count += 1

        if count > 0:
            loss /= count
            grad /= count

        return LossResult(loss=loss, loss_name="pairwise", gradient=grad)

    @staticmethod
    def predict(features: np.ndarray, weights: np.ndarray) -> np.ndarray:
        """预测分数."""
        return features @ weights


class ListwiseLoss:
    """Listwise 损失 (ListNet).

    L = -Σ y_i × log(p_i), p_i = exp(s_i) / Σ exp(s_j)

    基于 Plackett-Luce 模型, 直接优化整个列表的排序概率。
    """

    name = "listwise"

    @staticmethod
    def compute(scores: np.ndarray, labels: np.ndarray) -> LossResult:
        """计算 ListNet 损失 (交叉熵)."""
        # 归一化分数和标签为概率分布
        scores_max = np.max(scores)
        scores_exp = np.exp(scores - scores_max)  # 数值稳定
        p_scores = scores_exp / np.sum(scores_exp)

        # 标签归一化 (Top-1 概率)
        labels_pos = np.maximum(labels, 0)
        label_sum = np.sum(labels_pos)
        if label_sum == 0:
            return LossResult(
                loss=0.0, loss_name="listwise", gradient=np.zeros(len(scores))
            )
        p_labels = labels_pos / label_sum

        # 交叉熵
        loss = -float(np.sum(p_labels * np.log(p_scores + 1e-10)))

        # 梯度: p_scores - p_labels
        grad = p_scores - p_labels

        return LossResult(loss=loss, loss_name="listwise", gradient=grad)

    @staticmethod
    def predict(features: np.ndarray, weights: np.ndarray) -> np.ndarray:
        """预测分数."""
        return features @ weights


# ============================================================
# 排序损失评估器
# ============================================================


@dataclass
class EvaluationResult:
    """单损失评估结果."""

    loss_name: str
    final_loss: float
    ndcg_10: float
    map_score: float
    mrr: float
    n_iterations: int


@dataclass
class ComparisonReport:
    """对比报告."""

    results: list[EvaluationResult]
    best_loss: str
    best_ndcg: float
    summary: dict[str, Any] = field(default_factory=dict)


class RankingLossEvaluator:
    """排序损失函数系统评估.

    对比 pointwise/pairwise/listwise 三种损失在金融排序任务上的表现。

    用法:
        evaluator = RankingLossEvaluator()
        report = evaluator.run_comparison(n_items=20, n_features=10)
    """

    LOSS_FUNCTIONS = [PointwiseLoss, PairwiseLoss, ListwiseLoss]

    def __init__(
        self,
        lr: float = 0.01,
        n_iterations: int = 100,
    ) -> None:
        self.lr = lr
        self.n_iterations = n_iterations

    def generate_ranking_data(
        self,
        n_items: int = 20,
        n_features: int = 10,
        seed: int = 42,
    ) -> tuple[np.ndarray, np.ndarray]:
        """生成排序数据 (特征 + 真实标签).

        Returns:
            (features [n_items, n_features], labels [n_items])
        """
        rng = np.random.default_rng(seed)
        # 真实权重
        true_weights = rng.normal(0, 1, n_features)
        # 特征
        features = rng.normal(0, 1, (n_items, n_features))
        # 真实分数 + 噪声
        true_scores = features @ true_weights
        noise = rng.normal(0, 0.1, n_items)
        labels = true_scores + noise
        # 归一化标签为 [0, 1] 相关性
        labels = (labels - labels.min()) / (labels.max() - labels.min() + 1e-8)
        return features, labels

    def train_and_evaluate(
        self,
        loss_fn: type[PointwiseLoss | PairwiseLoss | ListwiseLoss],
        features: np.ndarray,
        labels: np.ndarray,
    ) -> EvaluationResult:
        """训练并评估单个损失函数.

        Args:
            loss_fn: 损失函数类
            features: 特征矩阵
            labels: 真实标签

        Returns:
            EvaluationResult
        """
        n_items, n_features = features.shape
        rng = np.random.default_rng(42)

        # 初始化权重
        weights = rng.normal(0, 0.1, n_features)
        losses: list[float] = []

        for _ in range(self.n_iterations):
            scores = loss_fn.predict(features, weights)
            result = loss_fn.compute(scores, labels)
            losses.append(result.loss)

            # 梯度下降 (通过链式法则)
            if result.gradient is not None:
                # dL/dw = dL/ds × ds/dw = grad × features
                weight_grad = features.T @ result.gradient
                weights -= self.lr * weight_grad

        # 最终预测排序
        final_scores = loss_fn.predict(features, weights)
        predicted_order = np.argsort(-final_scores)  # 降序
        ideal_order = np.argsort(-labels)

        # 按预测排序后的标签
        predicted_rels = labels[predicted_order]
        ideal_rels = labels[ideal_order]

        # 评估指标
        ndcg = ndcg_at_k(predicted_rels, ideal_rels, k=10)
        map_score = average_precision(predicted_rels)
        mrr = mean_reciprocal_rank(predicted_rels)

        return EvaluationResult(
            loss_name=loss_fn.name,
            final_loss=losses[-1] if losses else 0.0,
            ndcg_10=ndcg,
            map_score=map_score,
            mrr=mrr,
            n_iterations=self.n_iterations,
        )

    def run_comparison(
        self,
        n_items: int = 20,
        n_features: int = 10,
        seed: int = 42,
    ) -> ComparisonReport:
        """运行三种损失函数对比.

        Args:
            n_items: 标的数
            n_features: 特征数
            seed: 随机种子

        Returns:
            ComparisonReport
        """
        features, labels = self.generate_ranking_data(n_items, n_features, seed)

        results: list[EvaluationResult] = []
        for loss_fn in self.LOSS_FUNCTIONS:
            result = self.train_and_evaluate(loss_fn, features, labels)
            results.append(result)

        # 选最优 (NDCG@10 最高)
        best = max(results, key=lambda r: r.ndcg_10)

        summary = {
            "n_items": n_items,
            "n_features": n_features,
            "n_iterations": self.n_iterations,
            "loss_functions": [r.loss_name for r in results],
            "ndcg_scores": {r.loss_name: r.ndcg_10 for r in results},
            "map_scores": {r.loss_name: r.map_score for r in results},
            "mrr_scores": {r.loss_name: r.mrr for r in results},
        }

        return ComparisonReport(
            results=results,
            best_loss=best.loss_name,
            best_ndcg=best.ndcg_10,
            summary=summary,
        )

    def select_best(self, report: ComparisonReport) -> EvaluationResult:
        """选择最优损失函数."""
        return max(report.results, key=lambda r: r.ndcg_10)


# ============================================================
# CLI 入口
# ============================================================


def main() -> None:
    """CLI 入口: 排序损失函数系统评估."""
    print("=" * 60)
    print("排序损失函数系统评估")
    print("文献: #59 CIKM 2025")
    print("=" * 60)

    evaluator = RankingLossEvaluator(n_iterations=200)

    # === 1. 生成数据 ===
    print("\n--- 1. 生成排序数据 ---")
    features, labels = evaluator.generate_ranking_data(n_items=50, n_features=15)
    print(f"  标的数: {len(labels)}, 特征数: {features.shape[1]}")
    print(f"  标签范围: [{labels.min():.3f}, {labels.max():.3f}]")

    # === 2. 三种损失对比 ===
    print("\n--- 2. 三种损失对比 ---")
    report = evaluator.run_comparison(n_items=50, n_features=15)
    print(f"{'损失':<12} {'NDCG@10':<10} {'MAP':<10} {'MRR':<10} {'最终损失':<10}")
    print("-" * 52)
    for r in report.results:
        print(
            f"{r.loss_name:<12} {r.ndcg_10:<10.4f} {r.map_score:<10.4f} {r.mrr:<10.4f} {r.final_loss:<10.4f}"
        )

    # === 3. 最优选择 ===
    print("\n--- 3. 最优损失函数 ---")
    best = evaluator.select_best(report)
    print(f"  最优: {best.loss_name}")
    print(f"  NDCG@10: {best.ndcg_10:.4f}")
    print(f"  MAP: {best.map_score:.4f}")
    print(f"  MRR: {best.mrr:.4f}")

    # === 4. 多次实验 ===
    print("\n--- 4. 多次实验稳定性 ---")
    n_trials = 5
    all_ndcgs: dict[str, list[float]] = {
        "pointwise": [],
        "pairwise": [],
        "listwise": [],
    }
    for seed in range(n_trials):
        r = evaluator.run_comparison(n_items=30, n_features=10, seed=seed + 100)
        for res in r.results:
            all_ndcgs[res.loss_name].append(res.ndcg_10)

    print(f"{'损失':<12} {'平均NDCG':<10} {'标准差':<10} {'最小':<10} {'最大':<10}")
    print("-" * 52)
    for name, ndcgs in all_ndcgs.items():
        arr = np.array(ndcgs)
        print(
            f"{name:<12} {arr.mean():<10.4f} {arr.std():<10.4f} {arr.min():<10.4f} {arr.max():<10.4f}"
        )


if __name__ == "__main__":
    main()
