"""排序损失函数系统评估单元测试.

被测模块: tests/eval/ranking_loss_eval.py
文献: #59 CIKM 2025
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.eval.ranking_loss_eval import (  # noqa: E402
    ComparisonReport,
    EvaluationResult,
    ListwiseLoss,
    PairwiseLoss,
    PointwiseLoss,
    RankingLossEvaluator,
    average_precision,
    dcg_at_k,
    mean_reciprocal_rank,
    ndcg_at_k,
)

# ============================================================
# 排序指标测试
# ============================================================

class TestRankingMetrics:
    def test_dcg_at_k(self):
        rels = np.array([3, 2, 1, 0])
        dcg = dcg_at_k(rels, k=4)
        assert dcg > 0

    def test_ndcg_perfect(self):
        """完美排序 NDCG=1."""
        rels = np.array([3, 2, 1, 0])
        assert ndcg_at_k(rels, rels, k=4) == pytest.approx(1.0)

    def test_ndcg_worst(self):
        """逆序 NDCG<1."""
        ideal = np.array([3, 2, 1, 0])
        predicted = np.array([0, 1, 2, 3])
        assert ndcg_at_k(predicted, ideal, k=4) < 1.0

    def test_mrr(self):
        """MRR: 第一个相关项的倒数."""
        rels = np.array([0, 1, 0])  # 第2位相关
        assert mean_reciprocal_rank(rels) == pytest.approx(0.5)

    def test_mrr_first(self):
        rels = np.array([1, 0, 0])  # 第1位相关
        assert mean_reciprocal_rank(rels) == pytest.approx(1.0)

    def test_average_precision(self):
        rels = np.array([1, 0, 1])  # 2个相关
        ap = average_precision(rels)
        assert 0 < ap <= 1


# ============================================================
# Pointwise 损失测试
# ============================================================

class TestPointwiseLoss:
    def test_compute(self):
        scores = np.array([0.5, 0.3, 0.8])
        labels = np.array([0.4, 0.2, 0.9])
        result = PointwiseLoss.compute(scores, labels)
        assert result.loss_name == "pointwise"
        assert result.loss >= 0
        assert result.gradient is not None

    def test_zero_loss(self):
        """完美预测损失=0."""
        scores = np.array([0.5, 0.3])
        labels = np.array([0.5, 0.3])
        result = PointwiseLoss.compute(scores, labels)
        assert result.loss == pytest.approx(0.0, abs=1e-6)

    def test_predict(self):
        features = np.array([[1, 0], [0, 1]])
        weights = np.array([0.5, 0.3])
        scores = PointwiseLoss.predict(features, weights)
        assert scores[0] == pytest.approx(0.5)
        assert scores[1] == pytest.approx(0.3)


# ============================================================
# Pairwise 损失测试
# ============================================================

class TestPairwiseLoss:
    def test_compute(self):
        scores = np.array([0.5, 0.3, 0.8])
        labels = np.array([0.4, 0.2, 0.9])
        result = PairwiseLoss.compute(scores, labels)
        assert result.loss_name == "pairwise"
        assert result.loss >= 0

    def test_correct_order_lower_loss(self):
        """正确排序损失 < 错误排序."""
        labels = np.array([0.9, 0.1])  # 0 应排在 1 前面
        correct = PairwiseLoss.compute(np.array([0.8, 0.2]), labels)
        wrong = PairwiseLoss.compute(np.array([0.2, 0.8]), labels)
        assert correct.loss < wrong.loss


# ============================================================
# Listwise 损失测试
# ============================================================

class TestListwiseLoss:
    def test_compute(self):
        scores = np.array([0.5, 0.3, 0.8])
        labels = np.array([0.4, 0.2, 0.9])
        result = ListwiseLoss.compute(scores, labels)
        assert result.loss_name == "listwise"
        assert result.loss >= 0

    def test_zero_labels(self):
        """全零标签损失=0."""
        scores = np.array([0.5, 0.3])
        labels = np.array([0.0, 0.0])
        result = ListwiseLoss.compute(scores, labels)
        assert result.loss == pytest.approx(0.0)


# ============================================================
# 评估器测试
# ============================================================

class TestRankingLossEvaluator:
    def test_generate_data(self):
        evaluator = RankingLossEvaluator()
        features, labels = evaluator.generate_ranking_data(n_items=10, n_features=5)
        assert features.shape == (10, 5)
        assert len(labels) == 10
        assert labels.min() >= 0
        assert labels.max() <= 1  # 归一化

    def test_train_and_evaluate(self):
        evaluator = RankingLossEvaluator(n_iterations=10)
        features, labels = evaluator.generate_ranking_data(n_items=10, n_features=5)
        result = evaluator.train_and_evaluate(PointwiseLoss, features, labels)
        assert isinstance(result, EvaluationResult)
        assert 0 <= result.ndcg_10 <= 1
        assert 0 <= result.map_score <= 1
        assert 0 <= result.mrr <= 1

    def test_run_comparison(self):
        evaluator = RankingLossEvaluator(n_iterations=50)
        report = evaluator.run_comparison(n_items=20, n_features=10)
        assert isinstance(report, ComparisonReport)
        assert len(report.results) == 3  # 三种损失
        assert report.best_loss in ["pointwise", "pairwise", "listwise"]

    def test_select_best(self):
        evaluator = RankingLossEvaluator(n_iterations=50)
        report = evaluator.run_comparison(n_items=20, n_features=10)
        best = evaluator.select_best(report)
        assert best.ndcg_10 == max(r.ndcg_10 for r in report.results)


# ============================================================
# 验收标准测试
# ============================================================

class TestAcceptanceCriteria:
    """LIT-5.5 验收: pointwise/pairwise/listwise 对比 + 选最优."""

    def test_three_losses_implemented(self):
        """验收: 三种损失函数实现."""
        evaluator = RankingLossEvaluator(n_iterations=10)
        report = evaluator.run_comparison(n_items=10, n_features=5)
        names = [r.loss_name for r in report.results]
        assert "pointwise" in names
        assert "pairwise" in names
        assert "listwise" in names

    def test_best_selected(self):
        """验收: 选出最优损失函数."""
        evaluator = RankingLossEvaluator(n_iterations=100)
        report = evaluator.run_comparison(n_items=30, n_features=10)
        best = evaluator.select_best(report)
        assert best.ndcg_10 > 0  # NDCG > 0 (有排序能力)

    def test_financial_ranking(self):
        """验收: 金融排序任务 (选股排名)."""
        evaluator = RankingLossEvaluator(n_iterations=100)
        # 模拟选股: 50只标的, 15个因子
        report = evaluator.run_comparison(n_items=50, n_features=15)
        # 至少一种损失 NDCG > 0.5 (有意义的排序)
        assert max(r.ndcg_10 for r in report.results) > 0.5
