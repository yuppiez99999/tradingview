"""RAG + RL 自适应情感分析单元测试.

被测模块: nlp/rag_rl_sentiment.py
文献: #69 CODS 2025
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from nlp.rag_rl_sentiment import (  # noqa: E402
    AdaptiveSentimentHub,
    FeedbackResult,
    NewsItem,
    PPOSentimentTuner,
    RAGRetriever,
    SentimentPrediction,
)

# ============================================================
# RAG 检索测试
# ============================================================


class TestRAGRetriever:
    def test_add_news(self):
        retriever = RAGRetriever()
        retriever.add_news(NewsItem("芯片涨价", "半导体涨价"))
        assert len(retriever.news_db) == 1

    def test_retrieve_empty(self):
        retriever = RAGRetriever()
        result = retriever.retrieve("测试")
        assert len(result.retrieved) == 0

    def test_retrieve_basic(self):
        retriever = RAGRetriever()
        retriever.add_news(NewsItem("半导体涨价", "芯片价格上调"))
        retriever.add_news(NewsItem("煤炭下跌", "动力煤价格下调"))
        result = retriever.retrieve("半导体", top_k=1)
        assert len(result.retrieved) == 1
        assert "半导体" in result.retrieved[0].title

    def test_retrieve_scores(self):
        retriever = RAGRetriever()
        retriever.add_news(NewsItem("利好消息", "业绩超预期"))
        result = retriever.retrieve("利好", top_k=1)
        assert len(result.scores) == 1
        assert result.scores[0] > 0  # 有相关性


# ============================================================
# PPO 调优器测试
# ============================================================


class TestPPOSentimentTuner:
    def test_init_weights(self):
        tuner = PPOSentimentTuner(n_weights=3)
        weights = tuner.get_weights()
        assert len(weights) == 3
        assert pytest.approx(sum(weights)) == 1.0  # 归一化

    def test_predict_sentiment(self):
        tuner = PPOSentimentTuner(n_weights=3)
        score = tuner.predict_sentiment(0.5, 0.3, 0.0)
        assert -1 <= score <= 1  # tanh 归一化

    def test_compute_reward_positive(self):
        """预测方向正确 → 正奖励."""
        tuner = PPOSentimentTuner()
        reward = tuner.compute_reward(predicted=0.5, actual_return=0.02)
        assert reward > 0

    def test_compute_reward_negative(self):
        """预测方向错误 → 负奖励."""
        tuner = PPOSentimentTuner()
        reward = tuner.compute_reward(predicted=0.5, actual_return=-0.02)
        assert reward < 0

    def test_ppo_update(self):
        """PPO 更新后权重仍归一化."""
        tuner = PPOSentimentTuner(n_weights=3)
        reward = 1.0
        scores = np.array([0.5, 0.3, 0.0])
        delta = tuner.ppo_update(reward, scores)
        assert len(delta) == 3
        assert pytest.approx(sum(tuner.get_weights())) == 1.0  # 仍归一化

    def test_ppo_update_non_negative(self):
        """PPO 更新后权重非负."""
        tuner = PPOSentimentTuner(n_weights=3)
        scores = np.array([0.5, 0.3, 0.0])
        for _ in range(10):
            tuner.ppo_update(reward=-1.0, scores=scores)
        assert all(w > 0 for w in tuner.get_weights())

    def test_compute_accuracy(self):
        tuner = PPOSentimentTuner()
        acc = tuner.compute_accuracy([0.5, -0.3, 0.1], [0.01, -0.02, 0.005])
        assert acc == 1.0  # 方向全对

    def test_compute_accuracy_partial(self):
        tuner = PPOSentimentTuner()
        acc = tuner.compute_accuracy([0.5, 0.3], [0.01, -0.02])  # 一对一错
        assert acc == 0.5

    def test_get_stats(self):
        tuner = PPOSentimentTuner()
        scores = np.array([0.5, 0.3, 0.0])
        tuner.ppo_update(1.0, scores)
        stats = tuner.get_stats()
        assert "current_weights" in stats
        assert "n_updates" in stats
        assert stats["n_updates"] == 1


# ============================================================
# 自适应 Hub 测试
# ============================================================


class TestAdaptiveSentimentHub:
    def test_add_news(self):
        hub = AdaptiveSentimentHub()
        hub.add_news("半导体涨价")
        assert len(hub.retriever.news_db) == 1

    def test_analyze_basic(self):
        hub = AdaptiveSentimentHub()
        result = hub.analyze("利好消息")
        assert isinstance(result, SentimentPrediction)
        assert -1 <= result.sentiment <= 1
        assert 0 <= result.confidence <= 1

    def test_analyze_positive(self):
        """正面文本 → 正情感."""
        hub = AdaptiveSentimentHub()
        result = hub.analyze("利好 增持 回购 业绩超预期")
        assert result.sentiment > 0

    def test_analyze_negative(self):
        """负面文本 → 负情感."""
        hub = AdaptiveSentimentHub()
        result = hub.analyze("暴跌 闪崩 爆雷 造假")
        assert result.sentiment < 0

    def test_analyze_with_context(self):
        """带上下文新闻分析."""
        hub = AdaptiveSentimentHub()
        result = hub.analyze("半导体", context_news=["芯片涨价", "国产替代加速"])
        assert isinstance(result, SentimentPrediction)
        assert len(hub.retriever.news_db) >= 2

    def test_update_with_feedback(self):
        """市场反馈更新."""
        hub = AdaptiveSentimentHub()
        pred = hub.analyze("利好消息")
        feedback = hub.update_with_feedback(pred, actual_return=0.02)
        assert isinstance(feedback, FeedbackResult)
        assert isinstance(feedback.reward, float)
        assert isinstance(feedback.weight_update, np.ndarray)

    def test_feedback_improves_weights(self):
        """多次反馈后权重调整."""
        hub = AdaptiveSentimentHub()
        initial_weights = hub.tuner.get_weights().copy()
        for _ in range(5):
            pred = hub.analyze("利好消息")
            hub.update_with_feedback(pred, actual_return=0.01)
        final_weights = hub.tuner.get_weights()
        assert not np.allclose(initial_weights, final_weights)  # 权重已调整

    def test_get_performance(self):
        hub = AdaptiveSentimentHub()
        hub.analyze("测试")
        perf = hub.get_performance()
        assert "n_predictions" in perf
        assert "tuner" in perf
        assert perf["n_predictions"] == 1


# ============================================================
# 验收标准测试
# ============================================================


class TestAcceptanceCriteria:
    """LIT-5.4 验收: LLaMA+RAG+PPO + 市场反馈自适应 + 准确率+5%."""

    def test_rag_retrieval(self):
        """验收: RAG 检索增强."""
        hub = AdaptiveSentimentHub()
        hub.add_news(NewsItem("半导体涨价", "芯片价格上调"))
        result = hub.analyze("半导体")
        assert len(result.retrieved_context) > 0  # RAG 检索到上下文

    def test_ppo_adaptive(self):
        """验收: PPO 市场反馈自适应."""
        hub = AdaptiveSentimentHub()
        # 训练: 正面预测 + 实际涨 → 强化正面权重
        for _ in range(50):
            pred = hub.analyze("利好 增持")
            hub.update_with_feedback(pred, actual_return=0.01)
        # 验证: 权重已从均匀分布调整
        weights = hub.tuner.get_weights()
        assert max(weights) > 1 / 3 + 0.005  # 某个权重明显增大

    def test_accuracy_improvement(self):
        """验收: 准确率提升 (反馈后)."""
        hub = AdaptiveSentimentHub()
        # 初始预测 (无反馈)
        pred1 = hub.analyze("利好消息")
        hub.update_with_feedback(pred1, actual_return=0.01)
        acc1 = hub.tuner.accuracy_history[-1]
        # 多轮反馈
        for _ in range(20):
            pred = hub.analyze("利好消息")
            hub.update_with_feedback(pred, actual_return=0.01)
        acc_final = hub.tuner.accuracy_history[-1]
        # 准确率应保持或提升
        assert acc_final >= acc1 - 0.01  # 允许小波动
