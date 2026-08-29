"""RAG + RL 自适应情感分析
=============================

文献依据: #69 CODS 2025 — RAG + RL for Adaptive Sentiment
任务: LIT-5.4 RAG + RL 自适应情感分析
增强目标: nlp/sentiment_hub.py (规则引擎 → RAG + RL 自适应)

核心设计
--------
1. RAG (检索增强生成):
   - 从新闻库检索相关新闻 (关键词 + 语义相似度)
   - 增强情感分析上下文

2. RL (强化学习 PPO):
   - 基于市场反馈 (实际涨跌) 调整情感权重
   - 策略梯度 + 裁剪 (PPO 核心)
   - 在线自适应更新

3. 自适应:
   - 情感阈值动态调整
   - 市场反馈驱动权重更新
   - 准确率监控 +5% 目标

验收标准
--------
- LLaMA + RAG + PPO 架构 (轻量模拟)
- 市场反馈自适应
- 准确率 +5%

使用示例
--------
    from nlp.rag_rl_sentiment import AdaptiveSentimentHub

    hub = AdaptiveSentimentHub()
    result = hub.analyze("半导体板块利好", context_news=["芯片涨价", "国产替代"])
    hub.update_with_feedback(result, actual_return=0.02)  # 市场反馈
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger("rag_rl_sentiment")


# ============================================================
# RAG 检索增强
# ============================================================


@dataclass
class NewsItem:
    """新闻条目."""

    title: str
    content: str
    source: str = ""
    timestamp: str = ""


@dataclass
class RetrievalResult:
    """检索结果."""

    query: str
    retrieved: list[NewsItem]
    scores: list[float]  # 相关性分数


class RAGRetriever:
    """RAG 检索器 — 基于关键词 + TF-IDF 相似度.

    用法:
        retriever = RAGRetriever()
        retriever.add_news(NewsItem("芯片涨价", "半导体涨价"))
        result = retriever.retrieve("半导体", top_k=3)
    """

    def __init__(self) -> None:
        self.news_db: list[NewsItem] = []
        self.vocab: dict[str, int] = {}  # 词汇表

    def add_news(self, news: NewsItem) -> None:
        """添加新闻到知识库."""
        self.news_db.append(news)
        self._update_vocab(news)

    def add_news_batch(self, news_list: list[NewsItem]) -> None:
        """批量添加新闻."""
        for news in news_list:
            self.add_news(news)

    def _update_vocab(self, news: NewsItem) -> None:
        """更新词汇表 (简单分词)."""
        text = news.title + news.content
        for word in self._tokenize(text):
            if word not in self.vocab:
                self.vocab[word] = len(self.vocab)

    def _tokenize(self, text: str) -> list[str]:
        """简单中文分词 (按字符 + 关键词)."""
        # 按字符分词 (适用于中文)
        return [c for c in text if c.strip()]

    def _tfidf_vector(self, text: str) -> np.ndarray:
        """计算 TF-IDF 向量."""
        tokens = self._tokenize(text)
        if not tokens or not self.vocab:
            return np.zeros(1)
        vec = np.zeros(len(self.vocab))
        for token in tokens:
            if token in self.vocab:
                vec[self.vocab[token]] += 1
        # TF 归一化
        total = vec.sum()
        if total > 0:
            vec = vec / total
        return vec

    def retrieve(
        self,
        query: str,
        top_k: int = 3,
    ) -> RetrievalResult:
        """检索相关新闻.

        Args:
            query: 查询文本
            top_k: 返回前 K 条

        Returns:
            RetrievalResult
        """
        if not self.news_db:
            return RetrievalResult(query=query, retrieved=[], scores=[])

        query_vec = self._tfidf_vector(query)
        scores: list[float] = []
        for news in self.news_db:
            news_vec = self._tfidf_vector(news.title + news.content)
            # 余弦相似度
            if np.linalg.norm(query_vec) > 0 and np.linalg.norm(news_vec) > 0:
                sim = float(
                    np.dot(query_vec, news_vec)
                    / (np.linalg.norm(query_vec) * np.linalg.norm(news_vec))
                )
            else:
                sim = 0.0
            # 关键词命中加成
            keyword_bonus = sum(0.1 for c in query if c in news.title + news.content)
            scores.append(sim + keyword_bonus)

        # 排序取 top_k
        ranked = sorted(zip(scores, self.news_db), key=lambda x: x[0], reverse=True)
        top_scores = [s for s, _ in ranked[:top_k]]
        top_news = [n for _, n in ranked[:top_k]]

        return RetrievalResult(query=query, retrieved=top_news, scores=top_scores)


# ============================================================
# PPO 强化学习调优
# ============================================================


@dataclass
class SentimentPrediction:
    """情感预测结果."""

    text: str
    sentiment: float  # [-1, 1]
    confidence: float  # [0, 1]
    weights: np.ndarray  # 当前权重
    scores: np.ndarray  # 各路分数 [rule, rag, history]
    retrieved_context: list[NewsItem] = field(default_factory=list)


@dataclass
class FeedbackResult:
    """反馈结果."""

    prediction: SentimentPrediction
    actual_return: float  # 实际涨跌
    reward: float  # 奖励
    weight_update: np.ndarray  # 权重更新量
    accuracy_before: float
    accuracy_after: float


class PPOSentimentTuner:
    """PPO 情感权重调优器.

    策略: 情感分数 = w1 × 规则分数 + w2 × RAG 分数 + w3 × 历史分数
    PPO: 基于市场反馈 (实际涨跌) 调整权重

    用法:
        tuner = PPOSentimentTuner(n_weights=3)
        weights = tuner.get_weights()
        update = tuner.ppo_update(old_weights, reward, advantage)
    """

    def __init__(
        self,
        n_weights: int = 3,
        lr: float = 0.01,
        clip_ratio: float = 0.2,
    ) -> None:
        self.n_weights = n_weights
        self.lr = lr
        self.clip_ratio = clip_ratio
        # 初始化均匀权重
        self.weights = np.ones(n_weights) / n_weights
        self.weight_history: list[np.ndarray] = [self.weights.copy()]
        self.reward_history: list[float] = []
        self.accuracy_history: list[float] = []

    def get_weights(self) -> np.ndarray:
        """获取当前权重."""
        return self.weights.copy()

    def predict_sentiment(
        self,
        rule_score: float,
        rag_score: float,
        history_score: float = 0.0,
    ) -> float:
        """预测情感分数.

        Args:
            rule_score: 规则引擎分数 [-1, 1]
            rag_score: RAG 增强分数 [-1, 1]
            history_score: 历史分数 [-1, 1]

        Returns:
            情感分数 [-1, 1]
        """
        scores = np.array([rule_score, rag_score, history_score])
        raw = float(np.dot(self.weights, scores))
        # tanh 归一化到 [-1, 1]
        return math.tanh(raw)

    def compute_reward(
        self,
        predicted: float,
        actual_return: float,
    ) -> float:
        """计算奖励.

        奖励 = 预测方向与实际方向一致 → 正; 不一致 → 负.
        """
        # 实际方向 (涨为正, 跌为负)
        actual_direction = (
            1.0 if actual_return > 0 else (-1.0 if actual_return < 0 else 0.0)
        )
        # 预测方向
        pred_direction = 1.0 if predicted > 0 else (-1.0 if predicted < 0 else 0.0)
        # 方向一致奖励
        direction_reward = pred_direction * actual_direction
        # 幅度奖励 (预测强度 × 实际幅度, 权重低避免抵消方向惩罚)
        magnitude_reward = abs(predicted) * abs(actual_return) * 0.1
        return direction_reward + magnitude_reward

    def ppo_update(
        self,
        reward: float,
        scores: np.ndarray,
        old_weights: np.ndarray | None = None,
    ) -> np.ndarray:
        """PPO 策略更新.

        梯度: grad_i = advantage × scores_i (各路分数差异化)
        裁剪: PPO clip(ratio, 1-ε, 1+ε)

        Args:
            reward: 当前奖励
            scores: 各路分数 [rule, rag, history]
            old_weights: 旧权重 (None 则用当前)

        Returns:
            权重更新量
        """
        if old_weights is None:
            old_weights = self.weights.copy()

        # 优势估计 (减去历史均值)
        mean_reward = (
            float(np.mean(self.reward_history[-100:])) if self.reward_history else 0.0
        )
        advantage = reward - mean_reward

        # 梯度: advantage × scores (各路分数差异化)
        grad = advantage * scores

        # 概率比 (简化: 权重比)
        ratio = self.weights / (old_weights + 1e-8)

        # PPO 裁剪
        clipped_ratio = np.clip(ratio, 1 - self.clip_ratio, 1 + self.clip_ratio)

        # 更新权重 (梯度上升 × 裁剪比)
        update = self.lr * grad * clipped_ratio
        new_weights = self.weights + update

        # 归一化 (保持权重和为 1)
        new_weights = np.maximum(new_weights, 1e-4)  # 防止负权重
        new_weights = new_weights / new_weights.sum()

        weight_delta = new_weights - self.weights
        self.weights = new_weights
        self.weight_history.append(self.weights.copy())
        self.reward_history.append(reward)

        return weight_delta

    def compute_accuracy(
        self,
        predictions: list[float],
        actuals: list[float],
    ) -> float:
        """计算方向准确率."""
        if not predictions:
            return 0.0
        correct = 0
        for pred, actual in zip(predictions, actuals):
            pred_dir = 1 if pred > 0 else (-1 if pred < 0 else 0)
            actual_dir = 1 if actual > 0 else (-1 if actual < 0 else 0)
            if pred_dir == actual_dir and pred_dir != 0:
                correct += 1
        return correct / len(predictions)

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息."""
        return {
            "n_weights": self.n_weights,
            "current_weights": self.weights.tolist(),
            "n_updates": len(self.reward_history),
            "mean_reward": (
                float(np.mean(self.reward_history)) if self.reward_history else 0.0
            ),
            "recent_accuracy": (
                self.accuracy_history[-1] if self.accuracy_history else 0.0
            ),
        }


# ============================================================
# 自适应情感分析 Hub
# ============================================================


class AdaptiveSentimentHub:
    """RAG + RL 自适应情感分析 Hub.

    整合:
    - RAG 检索增强 (RAGRetriever)
    - PPO 强化学习 (PPOSentimentTuner)
    - 规则引擎 (复用 sentiment_hub 关键词)

    用法:
        hub = AdaptiveSentimentHub()
        result = hub.analyze("半导体利好", context_news=[...])
        hub.update_with_feedback(result, actual_return=0.02)
    """

    # 规则引擎关键词 (复用 sentiment_hub)
    POSITIVE_KEYWORDS = [
        "利好",
        "增持",
        "回购",
        "突破",
        "涨停",
        "业绩超预期",
        "中标",
        "补贴",
        "政策支持",
        "战略合作",
        "收购",
        "合并",
    ]
    NEGATIVE_KEYWORDS = [
        "暴跌",
        "闪崩",
        "退市",
        "立案",
        "处罚",
        "违约",
        "爆雷",
        "造假",
        "下调",
        "减持",
        "质押",
        "诉讼",
        "亏损",
        "停牌",
        "风险",
    ]

    def __init__(self) -> None:
        self.retriever = RAGRetriever()
        self.tuner = PPOSentimentTuner(n_weights=3)
        self.prediction_history: list[SentimentPrediction] = []
        self.actual_history: list[float] = []

    def add_news(self, news: NewsItem | str) -> None:
        """添加新闻到 RAG 知识库."""
        if isinstance(news, str):
            news = NewsItem(title=news, content=news)
        self.retriever.add_news(news)

    def _rule_score(self, text: str) -> float:
        """规则引擎分数."""
        pos_count = sum(1 for kw in self.POSITIVE_KEYWORDS if kw in text)
        neg_count = sum(1 for kw in self.NEGATIVE_KEYWORDS if kw in text)
        total = pos_count + neg_count
        if total == 0:
            return 0.0
        return (pos_count - neg_count) / total  # [-1, 1]

    def _rag_score(self, text: str, retrieved: list[NewsItem]) -> float:
        """RAG 增强分数 (基于检索新闻的情感)."""
        if not retrieved:
            return 0.0
        scores = [self._rule_score(n.title + n.content) for n in retrieved]
        return float(np.mean(scores))

    def _history_score(self) -> float:
        """历史分数 (最近预测的指数加权)."""
        if not self.prediction_history:
            return 0.0
        recent = self.prediction_history[-10:]
        weights = np.exp(-np.arange(len(recent))[::-1] * 0.1)  # 指数衰减
        weights = weights / weights.sum()
        return float(np.sum(weights * [p.sentiment for p in recent]))

    def analyze(
        self,
        text: str,
        context_news: list[NewsItem | str] | None = None,
        top_k: int = 3,
    ) -> SentimentPrediction:
        """分析文本情感.

        Args:
            text: 待分析文本
            context_news: 上下文新闻 (可选)
            top_k: RAG 检索数

        Returns:
            SentimentPrediction
        """
        # 添加上下文新闻
        if context_news:
            for news in context_news:
                if isinstance(news, str):
                    news = NewsItem(title=news, content=news)
                self.retriever.add_news(news)

        # RAG 检索
        retrieval = self.retriever.retrieve(text, top_k=top_k)

        # 三路评分
        rule = self._rule_score(text)
        rag = self._rag_score(text, retrieval.retrieved)
        history = self._history_score()

        # PPO 加权预测
        sentiment = self.tuner.predict_sentiment(rule, rag, history)
        confidence = min(1.0, abs(rule) + abs(rag) + 0.1)

        prediction = SentimentPrediction(
            text=text,
            sentiment=sentiment,
            confidence=confidence,
            weights=self.tuner.get_weights(),
            scores=np.array([rule, rag, history]),
            retrieved_context=retrieval.retrieved,
        )
        self.prediction_history.append(prediction)
        return prediction

    def update_with_feedback(
        self,
        prediction: SentimentPrediction,
        actual_return: float,
    ) -> FeedbackResult:
        """基于市场反馈更新模型.

        Args:
            prediction: 之前的预测
            actual_return: 实际涨跌

        Returns:
            FeedbackResult
        """
        self.actual_history.append(actual_return)

        # 计算奖励
        reward = self.tuner.compute_reward(prediction.sentiment, actual_return)

        # PPO 更新 (传入各路分数用于差异化梯度)
        old_weights = prediction.weights.copy()
        weight_update = self.tuner.ppo_update(reward, prediction.scores, old_weights)

        # 准确率
        recent_preds = [p.sentiment for p in self.prediction_history[-20:]]
        recent_actuals = self.actual_history[-20:]
        acc_before = (
            self.tuner.accuracy_history[-1] if self.tuner.accuracy_history else 0.0
        )
        acc_after = self.tuner.compute_accuracy(recent_preds, recent_actuals)
        self.tuner.accuracy_history.append(acc_after)

        return FeedbackResult(
            prediction=prediction,
            actual_return=actual_return,
            reward=reward,
            weight_update=weight_update,
            accuracy_before=acc_before,
            accuracy_after=acc_after,
        )

    def get_performance(self) -> dict[str, Any]:
        """获取性能报告."""
        tuner_stats = self.tuner.get_stats()
        return {
            "n_predictions": len(self.prediction_history),
            "n_feedback": len(self.actual_history),
            "tuner": tuner_stats,
            "news_db_size": len(self.retriever.news_db),
        }


# ============================================================
# CLI 入口
# ============================================================


def main() -> None:
    """CLI 入口: 演示 RAG + RL 自适应情感分析."""
    print("=" * 60)
    print("RAG + RL 自适应情感分析")
    print("文献: #69 CODS 2025")
    print("=" * 60)

    hub = AdaptiveSentimentHub()

    # === 1. 添加新闻知识库 ===
    print("\n--- 1. 添加新闻知识库 ---")
    news_batch = [
        NewsItem("半导体涨价", "芯片价格上调, 国产替代加速", "财经网"),
        NewsItem("某公司暴跌", "业绩爆雷, 股价闪崩", "新浪财经"),
        NewsItem("政策利好", "国家支持半导体产业发展", "人民日报"),
        NewsItem("减持公告", "大股东减持, 市场担忧", "Wind"),
    ]
    hub.retriever.add_news_batch(news_batch)
    print(f"  新闻库: {len(hub.retriever.news_db)} 条")

    # === 2. 情感分析 ===
    print("\n--- 2. 情感分析 ---")
    texts = [
        "半导体板块利好, 芯片涨价",
        "某公司暴跌, 业绩爆雷",
        "政策支持半导体产业发展",
    ]
    for text in texts:
        result = hub.analyze(text)
        print(
            f"  '{text}': 情感={result.sentiment:.3f}, "
            f"置信={result.confidence:.3f}, "
            f"权重={result.weights.round(3).tolist()}"
        )

    # === 3. 市场反馈 + PPO 更新 ===
    print("\n--- 3. 市场反馈 + PPO 更新 ---")
    actuals = [0.02, -0.03, 0.01]  # 实际涨跌
    for pred, actual in zip(hub.prediction_history, actuals):
        feedback = hub.update_with_feedback(pred, actual)
        print(
            f"  预测={feedback.prediction.sentiment:.3f}, "
            f"实际={feedback.actual_return:+.3f}, "
            f"奖励={feedback.reward:.3f}, "
            f"准确率={feedback.accuracy_after:.1%}"
        )

    # === 4. 性能报告 ===
    print("\n--- 4. 性能报告 ---")
    perf = hub.get_performance()
    print(f"  预测数: {perf['n_predictions']}")
    print(f"  反馈数: {perf['n_feedback']}")
    print(f"  PPO 更新数: {perf['tuner']['n_updates']}")
    print(f"  平均奖励: {perf['tuner']['mean_reward']:.3f}")
    print(f"  当前权重: {perf['tuner']['current_weights']}")
    print(f"  最近准确率: {perf['tuner']['recent_accuracy']:.1%}")


if __name__ == "__main__":
    main()
