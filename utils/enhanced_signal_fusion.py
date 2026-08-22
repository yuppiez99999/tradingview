"""
增强信号融合引擎 - 动态权重升级版 v5.8

在原有SignalFusionEngine基础上增强动态权重算法，
增加更智能的权重调整机制，包括：

核心增强功能：
1. 自适应权重调整（基于性能和相关性）
2. 多维度权重优化（胜率、稳定性、时效性、多样性）
3. 实时权重监控与调整
4. 权重变化趋势分析
5. 异常检测与权重保护

主要改进：
- 多因子权重计算模型
- 实时性能追踪
- 动态权重调优
- 异常权重保护机制
- 权重变化趋势分析

"""

import sqlite3
import threading
import time
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

import numpy as np

warnings.filterwarnings('ignore')

try:
    from .logging_manager import get_logger
    logger = get_logger('enhanced_signal_fusion')
except ImportError:
    try:
        from logging_manager import get_logger
        logger = get_logger('enhanced_signal_fusion')
    except ImportError:
        import logging
        logger = logging.getLogger('enhanced_signal_fusion')

try:
    from .signal_fusion import FusedSignal, SignalFusionEngine, SignalResult
except ImportError:
    try:
        from signal_fusion import FusedSignal, SignalFusionEngine, SignalResult
    except ImportError:
        import logging as _logging
        _logging.getLogger('enhanced_signal_fusion').warning(
            "signal_fusion 不可用, EnhancedSignalFusionEngine 将使用基础占位类"
        )

        class SignalResult:
            def __init__(self, **kwargs: Any) -> None:
                for k, v in kwargs.items():
                    setattr(self, k, v)

        class FusedSignal:
            def __init__(self, **kwargs: Any) -> None:
                for k, v in kwargs.items():
                    setattr(self, k, v)

        class SignalFusionEngine:
            def __init__(self, db_path: Optional[str] = None) -> None:
                self.db_path = db_path or ":memory:"
                self._sources = {}
                self._source_weights = {}

            def register_source(
                self,
                name: str,
                getter: callable,
                initial_weight: Optional[float] = None,
            ) -> None:
                self._sources[name] = getter
                self._source_weights[name] = initial_weight or 1.0 / max(len(self._sources), 1)

            def _compute_dynamic_weights(self) -> dict[str, float]:
                if not self._source_weights:
                    return {}
                total = sum(self._source_weights.values())
                return {k: v / total for k, v in self._source_weights.items()} if total > 0 else {}

try:
    from .fast_signal_processor import FastSignal, generate_fast_signals
except ImportError:
    try:
        from fast_signal_processor import FastSignal, generate_fast_signals
    except ImportError:
        FastSignal = None
        generate_fast_signals = None

try:
    from .rule_engine import evaluate_trading_decision
except ImportError:
    try:
        from rule_engine import evaluate_trading_decision
    except ImportError:
        evaluate_trading_decision = None


@dataclass
class SourcePerformanceMetrics:
    """信号源性能指标"""
    source_name: str
    total_signals: int = 0
    correct_predictions: int = 0
    recent_accuracy: float = 0.0
    volatility: float = 0.0  # 预测结果的标准差
    response_time_avg: float = 0.0
    response_time_std: float = 0.0
    last_updated: str = ""
    performance_history: list[float] = field(default_factory=list)
    consecutive_losses: int = 0
    consecutive_wins: int = 0
    diversity_score: float = 0.0


@dataclass
class WeightAdjustmentConfig:
    """权重调整配置"""
    lookback_days: int = 30
    min_samples: int = 5
    max_weight_per_source: float = 0.5
    min_weight_per_source: float = 0.05
    adaptive_learning_rate: float = 0.1
    volatility_threshold: float = 0.3
    correlation_penalty_factor: float = 0.2
    performance_trend_window: int = 7
    emergency_rebalance_threshold: float = 0.8


class EnhancedSignalFusionEngine(SignalFusionEngine):
    """增强版信号融合引擎 - 动态权重升级"""

    def __init__(self, db_path: str = None, config: WeightAdjustmentConfig = None):
        super().__init__(db_path)
        self.config = config or WeightAdjustmentConfig()
        self._performance_metrics: dict[str, SourcePerformanceMetrics] = {}
        self._weight_history: dict[str, list[tuple[str, float]]] = defaultdict(list)
        self._correlation_matrix: dict[str, dict[str, float]] = {}
        self._last_weight_update: str = ""
        self._weight_lock = threading.RLock()
        self._initialized = False

        # 初始化数据库表
        self._init_enhanced_db()

    def _init_enhanced_db(self) -> None:
        """初始化增强版数据库表"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # 性能指标表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS source_performance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_name TEXT NOT NULL,
                    date TEXT NOT NULL,
                    total_signals INTEGER DEFAULT 0,
                    correct_predictions INTEGER DEFAULT 0,
                    accuracy REAL DEFAULT 0.0,
                    volatility REAL DEFAULT 0.0,
                    avg_response_time REAL DEFAULT 0.0,
                    consecutive_losses INTEGER DEFAULT 0,
                    consecutive_wins INTEGER DEFAULT 0,
                    diversity_score REAL DEFAULT 0.0,
                    UNIQUE(source_name, date)
                )
            """)

            # 权重历史表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS weight_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_name TEXT NOT NULL,
                    date TEXT NOT NULL,
                    weight REAL NOT NULL,
                    reason TEXT,
                    performance_score REAL DEFAULT 0.0,
                    UNIQUE(source_name, date)
                )
            """)

            # 相关性矩阵表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS source_correlations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source1 TEXT NOT NULL,
                    source2 TEXT NOT NULL,
                    correlation REAL NOT NULL,
                    date TEXT NOT NULL,
                    UNIQUE(source1, source2, date)
                )
            """)

            conn.commit()
            conn.close()

            self._initialized = True
            logger.info("增强版信号融合引擎数据库初始化完成")

        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.error(f"初始化增强版数据库失败: {e}")
            self._initialized = False

    def register_enhanced_source(
        self,
        name: str,
        getter: callable,
        initial_weight: Optional[float] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """注册增强版信号源"""
        super().register_source(name, getter, initial_weight)

        # 初始化性能指标
        if name not in self._performance_metrics:
            self._performance_metrics[name] = SourcePerformanceMetrics(
                source_name=name,
                last_updated=datetime.now().isoformat()
            )

        # 记录初始权重
        self._record_weight_change(name, self._source_weights[name], "initial_registration", 0.0)

        logger.info(f"注册增强版信号源: {name} (初始权重={self._source_weights.get(name, 'auto'):.3f})")

    def _record_weight_change(
        self,
        source_name: str,
        new_weight: float,
        reason: str,
        performance_score: float = 0.0,
    ) -> None:
        """记录权重变化历史"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO weight_history (source_name, date, weight, reason, performance_score)
                VALUES (?, ?, ?, ?, ?)
            """, (source_name, datetime.now().isoformat(), new_weight, reason, performance_score))

            conn.commit()
            conn.close()

            # 内存中记录
            self._weight_history[source_name].append((datetime.now().isoformat(), new_weight))

        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.warning(f"记录权重变化失败: {e}")

    def _compute_enhanced_dynamic_weights(self) -> dict[str, float]:
        """计算增强版动态权重"""
        if not self._initialized:
            return super()._compute_dynamic_weights()

        with self._weight_lock:
            # 获取所有可用信号源
            available_sources = list(self._sources.keys())

            if not available_sources:
                return {}

            # 1. 收集多维度性能指标
            performance_scores = self._compute_multi_dimensional_scores(available_sources)

            # 2. 计算相关性惩罚
            correlation_penalty = self._compute_correlation_penalty(available_sources)

            # 3. 应用权重约束和优化
            optimized_weights = self._apply_weight_constraints(
                performance_scores, correlation_penalty, available_sources
            )

            # 4. 记录权重变化
            self._update_weight_history(optimized_weights)

            return optimized_weights

    def _compute_multi_dimensional_scores(self, sources: list[str]) -> dict[str, float]:
        """计算多维度性能分数"""
        performance_scores = {}

        for source in sources:
            metrics = self._performance_metrics.get(source)
            if not metrics:
                performance_scores[source] = 0.5  # 默认分数
                continue

            # 1. 胜率分数 (40%)
            accuracy_score = metrics.recent_accuracy if metrics.recent_accuracy > 0 else 0.5

            # 2. 稳定性分数 (30%) - 波动性越低分数越高
            volatility_score = max(0, 1 - metrics.volatility) if metrics.volatility > 0 else 1.0

            # 3. 时效性分数 (20%) - 响应时间越快分数越高
            response_score = max(0, 1 - (metrics.response_time_avg / 1000))  # 假设1000ms为基准

            # 4. 多样性分数 (10%) - 避免过度依赖单一信号源
            diversity_score = metrics.diversity_score

            # 综合分数
            total_score = (
                accuracy_score * 0.4 +
                volatility_score * 0.3 +
                response_score * 0.2 +
                diversity_score * 0.1
            )

            performance_scores[source] = max(0.1, min(1.0, total_score))

        return performance_scores

    def _compute_correlation_penalty(self, sources: list[str]) -> dict[str, float]:
        """计算相关性惩罚"""
        correlation_penalty = {}

        # 获取最新相关性矩阵
        self._update_correlation_matrix()

        for source in sources:
            penalty_score = 0.0

            # 计算该源与其他源的平均相关性
            for other_source in sources:
                if source != other_source:
                    correlation = self._correlation_matrix.get(source, {}).get(other_source, 0)
                    if abs(correlation) > 0.7:  # 高相关性阈值
                        penalty_score += abs(correlation) * self.config.correlation_penalty_factor

            correlation_penalty[source] = min(0.5, penalty_score)  # 最大惩罚50%

        return correlation_penalty

    def _update_correlation_matrix(self) -> None:
        """更新信号源相关性矩阵"""
        sources = list(self._sources.keys())

        if len(sources) < 2:
            return

        try:
            # 从数据库获取各源的历史预测结果
            conn = sqlite3.connect(self.db_path)
            correlation_data = {}

            for source in sources:
                cursor = conn.execute("""
                    SELECT predicted_action, date
                    FROM signal_audit
                    WHERE source = ? AND date >= ?
                    ORDER BY date DESC
                    LIMIT 100
                """, (source, (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d')))

                results = cursor.fetchall()
                correlation_data[source] = [1 if r[0] == 'BUY' else -1 if r[0] == 'SELL' else 0 for r in results]

            conn.close()

            # 计算相关性矩阵
            for i, source1 in enumerate(sources):
                self._correlation_matrix[source1] = {}
                data1 = correlation_data[source1]

                if len(data1) < 10:  # 数据不足跳过
                    for source2 in sources:
                        self._correlation_matrix[source1][source2] = 0.0
                    continue

                for j, source2 in enumerate(sources):
                    if i == j:
                        self._correlation_matrix[source1][source2] = 1.0
                        continue

                    data2 = correlation_data[source2]
                    min_len = min(len(data1), len(data2))

                    if min_len >= 10:
                        correlation = np.corrcoef(data1[:min_len], data2[:min_len])[0, 1]
                        self._correlation_matrix[source1][source2] = correlation if not np.isnan(correlation) else 0.0
                    else:
                        self._correlation_matrix[source1][source2] = 0.0

            # 保存到数据库
            self._save_correlation_matrix()

        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.warning(f"更新相关性矩阵失败: {e}")
            # 如果失败，使用默认零相关性
            for source1 in sources:
                self._correlation_matrix[source1] = {source2: 0.0 for source2 in sources}

    def _save_correlation_matrix(self) -> None:
        """保存相关性矩阵到数据库"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # 清除旧数据
            cursor.execute("DELETE FROM source_correlations")

            # 保存新数据
            date_str = datetime.now().strftime('%Y-%m-%d')
            for source1 in self._correlation_matrix:
                for source2 in self._correlation_matrix[source1]:
                    cursor.execute("""
                        INSERT INTO source_correlations (source1, source2, correlation, date)
                        VALUES (?, ?, ?, ?)
                    """, (source1, source2, self._correlation_matrix[source1][source2], date_str))

            conn.commit()
            conn.close()

        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.warning(f"保存相关性矩阵失败: {e}")

    def _apply_weight_constraints(self, performance_scores: dict[str, float],
                                 correlation_penalty: dict[str, float],
                                 sources: list[str]) -> dict[str, float]:
        """应用权重约束和优化"""
        # 应用相关性惩罚
        adjusted_scores = {}
        for source in sources:
            adjusted_scores[source] = performance_scores[source] * (1 - correlation_penalty[source])

        # 计算原始权重（Softmax）
        exp_scores = np.exp([adjusted_scores[s] * 10 for s in sources])  # 乘以10增加差异度
        softmax_weights = exp_scores / np.sum(exp_scores)

        # 转换为字典
        weights = {sources[i]: softmax_weights[i] for i in range(len(sources))}

        # 应用权重约束
        for source in sources:
            # 最大权重限制
            weights[source] = min(weights[source], self.config.max_weight_per_source)

            # 最小权重保证
            weights[source] = max(weights[source], self.config.min_weight_per_source)

        # 归一化
        total_weight = sum(weights.values())
        if total_weight > 0:
            for source in sources:
                weights[source] /= total_weight

        return weights

    def _update_weight_history(self, new_weights: dict[str, float]) -> None:
        """更新权重历史并记录变化"""
        for source_name, new_weight in new_weights.items():
            old_weight = self._source_weights.get(source_name, 0.0)

            # 如果权重发生显著变化，记录变化
            if abs(new_weight - old_weight) > 0.05:  # 5%阈值
                # 计算该源当前性能分数
                performance_score = self._get_source_performance_score(source_name)

                # 记录权重变化
                self._record_weight_change(
                    source_name, new_weight,
                    "significant_change", performance_score
                )

                logger.info(f"信号源 {source_name} 权重调整: {old_weight:.3f} -> {new_weight:.3f}")

    def _get_source_performance_score(self, source_name: str) -> float:
        """获取信号源当前性能分数"""
        metrics = self._performance_metrics.get(source_name)
        return metrics.recent_accuracy if metrics else 0.5

    def update_performance_metrics(
        self,
        source_name: str,
        actual_outcome: str,
        predicted_action: str,
        response_time: Optional[float] = None,
    ) -> None:
        """更新信号源性能指标"""
        if source_name not in self._performance_metrics:
            return

        metrics = self._performance_metrics[source_name]

        # 更新基础统计
        metrics.total_signals += 1

        if actual_outcome == predicted_action:
            metrics.correct_predictions += 1
            metrics.consecutive_wins += 1
            metrics.consecutive_losses = 0
        else:
            metrics.consecutive_losses += 1
            metrics.consecutive_wins = 0

        # 更新胜率
        if metrics.total_signals > 0:
            metrics.recent_accuracy = metrics.correct_predictions / metrics.total_signals

        # 更新响应时间
        if response_time is not None:
            if metrics.response_time_avg == 0:
                metrics.response_time_avg = response_time
                metrics.response_time_std = 0
            else:
                # 移动平均
                old_avg = metrics.response_time_avg
                metrics.response_time_avg = old_avg * 0.9 + response_time * 0.1

                # 标准差
                metrics.response_time_std = np.sqrt(
                    metrics.response_time_std**2 * 0.9 +
                    (response_time - old_avg)**2 * 0.1
                )

        # 更新多样性分数（基于与其他源的相关性）
        diversity_score = self._compute_diversity_score(source_name)
        metrics.diversity_score = diversity_score

        # 更新时间戳
        metrics.last_updated = datetime.now().isoformat()

        # 保存到数据库
        self._save_performance_metrics(source_name, metrics)

        # 定期重新计算权重
        if metrics.total_signals % 10 == 0:  # 每10次信号重新计算权重
            self._adaptive_weight_adjustment()

    def _compute_diversity_score(self, source_name: str) -> float:
        """计算信号源多样性分数"""
        if source_name not in self._correlation_matrix:
            return 0.5

        correlations = self._correlation_matrix[source_name]
        abs_correlations = [abs(c) for c in correlations.values() if c != 1.0]  # 排除自己

        if not abs_correlations:
            return 0.5

        # 平均绝对相关性越低，多样性分数越高
        avg_abs_correlation = np.mean(abs_correlations)
        diversity_score = 1 - min(avg_abs_correlation, 1.0)

        return diversity_score

    def _save_performance_metrics(
        self,
        source_name: str,
        metrics: SourcePerformanceMetrics,
    ) -> None:
        """保存性能指标到数据库"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("""
                INSERT OR REPLACE INTO source_performance
                (source_name, date, total_signals, correct_predictions, accuracy,
                 volatility, avg_response_time, consecutive_losses, consecutive_wins, diversity_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                source_name, metrics.last_updated, metrics.total_signals,
                metrics.correct_predictions, metrics.recent_accuracy, metrics.volatility,
                metrics.response_time_avg, metrics.consecutive_losses,
                metrics.consecutive_wins, metrics.diversity_score
            ))

            conn.commit()
            conn.close()

        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.warning(f"保存性能指标失败: {e}")

    def _adaptive_weight_adjustment(self) -> None:
        """自适应权重调整"""
        if not self._initialized:
            return

        # 检查是否需要紧急重新平衡
        max_weight = max(self._source_weights.values())
        min_weight = min(self._source_weights.values())

        if max_weight - min_weight > self.config.emergency_rebalance_threshold:
            logger.warning("检测到权重分布不均，执行紧急重新平衡")
            self._emergency_rebalance()

        # 正常的权重调整
        new_weights = self._compute_enhanced_dynamic_weights()

        # 应用新权重
        for source_name, new_weight in new_weights.items():
            old_weight = self._source_weights.get(source_name, 0.0)
            if abs(new_weight - old_weight) > 0.01:  # 1%阈值
                self._source_weights[source_name] = new_weight

    def _emergency_rebalance(self) -> None:
        """紧急重新平衡权重"""
        sources = list(self._sources.keys())
        if len(sources) <= 1:
            return

        # 均匀分配权重
        uniform_weight = 1.0 / len(sources)

        for source in sources:
            self._source_weights[source] = uniform_weight

        logger.info(f"执行紧急重新平衡: {len(sources)}个信号源，均匀权重{uniform_weight:.3f}")

    def get_enhanced_stats(self) -> dict[str, Any]:
        """获取增强版统计信息"""
        stats = super().get_stats()

        # 添加性能指标
        performance_data = {}
        for source_name, metrics in self._performance_metrics.items():
            performance_data[source_name] = {
                "total_signals": metrics.total_signals,
                "recent_accuracy": metrics.recent_accuracy,
                "volatility": metrics.volatility,
                "avg_response_time": metrics.response_time_avg,
                "consecutive_wins": metrics.consecutive_wins,
                "consecutive_losses": metrics.consecutive_losses,
                "diversity_score": metrics.diversity_score,
                "last_updated": metrics.last_updated
            }

        stats["performance_metrics"] = performance_data
        stats["weight_history"] = dict(self._weight_history)
        stats["correlation_matrix"] = self._correlation_matrix
        stats["last_weight_update"] = self._last_weight_update
        stats["config"] = {
            "lookback_days": self.config.lookback_days,
            "max_weight_per_source": self.config.max_weight_per_source,
            "min_weight_per_source": self.config.min_weight_per_source,
            "adaptive_learning_rate": self.config.adaptive_learning_rate
        }

        return stats

    def get_weight_change_analysis(self, source_name: str, days: int = 30) -> dict[str, Any]:
        """获取特定信号源的权重变化分析"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
            cursor.execute("""
                SELECT date, weight, reason, performance_score
                FROM weight_history
                WHERE source_name = ? AND date >= ?
                ORDER BY date DESC
            """, (source_name, cutoff_date))

            records = cursor.fetchall()
            conn.close()

            if not records:
                return {"message": f"{days}天内无权重变化记录"}

            # 计算变化趋势
            weights = [r[1] for r in records]
            trend_direction = "increasing" if weights[-1] > weights[0] else "decreasing"
            total_change = weights[0] - weights[-1]

            # 计算波动性
            weight_volatility = np.std(weights) if len(weights) > 1 else 0

            return {
                "source_name": source_name,
                "period_days": days,
                "total_records": len(records),
                "trend_direction": trend_direction,
                "total_change": total_change,
                "weight_volatility": weight_volatility,
                "current_weight": weights[0],
                "weight_history": records
            }

        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.warning(f"获取权重变化分析失败: {e}")
            return {"error": str(e)}

    def optimize_weights_based_on_market_conditions(self, market_condition: str) -> None:
        """基于市场条件优化权重"""
        if not self._initialized:
            return

        # 根据市场条件调整权重偏好
        weight_adjustments = {
            "bullish": {
                "ml_model": 0.35,      # 增加ML模型权重
                "ai_hedge": 0.30,      # 保持AI对冲基金权重
                "glm5": 0.25,          # 略微减少GLM5权重
                "kondratiev": 0.10     # 减少康波周期权重
            },
            "bearish": {
                "ml_model": 0.25,      # 减少ML模型权重
                "ai_hedge": 0.35,      # 增加AI对冲基金权重（风险对冲）
                "glm5": 0.30,          # 增加GLM5权重
                "kondratiev": 0.10     # 保持康波周期权重
            },
            "volatile": {
                "ml_model": 0.30,      # 中等ML模型权重
                "ai_hedge": 0.25,      # 中等AI对冲基金权重
                "glm5": 0.35,          # 增加GLM5权重（稳定预期）
                "kondratiev": 0.10     # 保持康波周期权重
            },
            "trending": {
                "ml_model": 0.40,      # 高ML模型权重（趋势跟随）
                "ai_hedge": 0.20,      # 减少AI对冲基金权重
                "glm5": 0.30,          # 中等GLM5权重
                "kondratiev": 0.10     # 保持康波周期权重
            }
        }

        if market_condition in weight_adjustments:
            new_weights = weight_adjustments[market_condition]

            # 平滑过渡到新权重
            for source_name, target_weight in new_weights.items():
                if source_name in self._source_weights:
                    current_weight = self._source_weights[source_name]
                    # 使用学习率进行平滑调整
                    adjusted_weight = (
                        current_weight * (1 - self.config.adaptive_learning_rate) +
                        target_weight * self.config.adaptive_learning_rate
                    )
                    self._source_weights[source_name] = adjusted_weight

            logger.info(f"基于市场条件 {market_condition} 调整权重配置")
            self._last_weight_update = datetime.now().isoformat()


# ── 便捷函数和集成 ──

# 全局单例
_enhanced_fusion_engine: Optional[EnhancedSignalFusionEngine] = None


def get_enhanced_fusion_engine() -> EnhancedSignalFusionEngine:
    """获取全局增强融合引擎单例"""
    global _enhanced_fusion_engine
    if _enhanced_fusion_engine is None:
        _enhanced_fusion_engine = EnhancedSignalFusionEngine()
    return _enhanced_fusion_engine


def register_enhanced_fast_signal_source(initial_weight: float = 0.2) -> None:
    """注册增强版快速技术指标信号源"""
    try:
        engine = get_enhanced_fusion_engine()
        engine.register_enhanced_source(
            'fast_technical',
            _get_enhanced_fast_signal_source,
            initial_weight,
            {"type": "technical", "latency": "ultra_low"}
        )
        logger.info("增强版快速技术指标信号源已注册")
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
        logger.error(f"注册增强版快速技术指标信号源失败: {e}")


def _get_enhanced_fast_signal_source(code: str) -> SignalResult:
    """增强版快速技术指标信号源"""
    try:
        from .hybrid_fusion import get_hybrid_fusion_engine

        start_time = time.time()
        engine = get_hybrid_fusion_engine()
        hybrid_signal = engine.get_hybrid_signal(code, "", force_hybrid=False)

        response_time = (time.time() - start_time) * 1000  # 转换为毫秒

        if hybrid_signal.source == 'fast' and hybrid_signal.fast_signal:
            fast_signal = hybrid_signal.fast_signal
            signal_result = SignalResult(
                code=code,
                source='fast_technical',
                score=fast_signal.confidence,
                action=fast_signal.action,
                confidence=fast_signal.confidence,
                reason=f"增强版快速技术指标信号: {fast_signal.action} (RSI={fast_signal.rsi:.2f}, MACD={fast_signal.macd_signal:.4f})",
                timestamp=datetime.now().isoformat()
            )

            # 更新性能指标
            if _enhanced_fusion_engine:
                _enhanced_fusion_engine.update_performance_metrics(
                    'fast_technical', 'UNKNOWN', fast_signal.action, response_time
                )

            return signal_result
        else:
            return None

    except (AttributeError, TypeError, ValueError, OSError) as e:
        logger.warning(f"获取增强版快速技术指标信号失败: {e}")
        return None


# 自动注册增强版快速信号源
try:
    enhanced_engine = get_enhanced_fusion_engine()
    if 'fast_technical' not in enhanced_engine._sources:
        register_enhanced_fast_signal_source()
        logger.info("自动注册增强版快速技术指标信号源成功")
except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
    logger.warning(f"自动注册增强版快速信号源失败: {e}")
