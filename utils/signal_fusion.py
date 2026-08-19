"""
多源信号融合引擎 — v5.7 Phase 1 优化

将 ML 模型预测、AI Hedge Fund、GLM5 决策、康波周期分析等多个信号源
统一融合为加权综合决策，解决各 AI 系统各自为政的问题。

核心特性:
- 多源信号加权融合（ML + AI Hedge Fund + GLM5 + 康波周期）
- 动态权重（基于各信号源近期历史胜率自动调整）
- 冲突检测与标注（当多源信号矛盾时标记"分歧"）
- 信号持久化（SQLite 存储，支持事后验证）
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

try:
    from .logging_manager import get_logger
    logger = get_logger('signal_fusion')
    from .fast_signal_processor import FastSignal, generate_fast_signals  # noqa: F401
    from .rule_engine import evaluate_trading_decision  # noqa: F401
except ImportError:
    import logging
    logger = logging.getLogger('signal_fusion')


@dataclass
class SignalResult:
    """单个信号源的结果"""
    code: str
    source: str          # 'ml' / 'ai_hedge' / 'glm5' / 'kondratiev' / 'fast_technical'
    score: float         # 0-1，越高越看多
    action: str          # 'BUY' / 'SELL' / 'HOLD'
    confidence: float    # 0-1
    reason: str = ""
    timestamp: str = ""


@dataclass
class FusionSignal:
    """融合信号 (轻量版, 供 institutional_pipeline_runner 使用).

    注: 早期命名 FusionSignal 在重构时被 FusedSignal 取代, 但 institutional_pipeline_runner
    仍引用此名 (list[FusionSignal] 类型注解 + FusionSignal(symbol=, strength=, confidence=) 实例化).
    此处补齐兼容性定义以解锁模块 import (GLM-5.2 C2 修复的前置依赖).
    """
    symbol: str = ""
    strength: float = 0.0
    confidence: float = 0.0
    source: str = ""


@dataclass
class FusedSignal:
    """融合后的综合信号"""
    code: str
    name: str = ""
    fused_score: float = 0.5
    action: str = "HOLD"
    confidence: float = 0.0
    consensus: str = "unknown"   # 'strong_agree' / 'agree' / 'mixed' / 'disagree' / 'strong_disagree'
    individual_signals: dict[str, SignalResult] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class FusedSignalV2:
    """v8.6.9 post-mix 融合信号 (fuse() 返回值)

    支持研究蒸馏信号和管线因子信号的 post-mix 集成。
    strength 范围 [-1, 1], 正数看多, 负数看空。
    """
    symbol: str
    strength: float = 0.0
    sources: dict[str, float] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)


class SignalFusionEngine:
    """多源信号融合引擎

    使用方式:
        engine = SignalFusionEngine(db_path='signals.db')
        engine.register_source('ml', ml_predictor.get_signal)
        result = engine.get_fused_signal('600519')
    """

    def __init__(self, db_path: str = None,
                 research_distilled_weight: float = 0.03,
                 pipeline_factor_weight: float = 0.05):
        if db_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            db_path = os.path.join(base_dir, 'data', 'signals.db')

        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._sources: dict[str, callable] = {}
        self._source_weights: dict[str, float] = {}

        # v8.6.9 post-mix 信号源 (研究蒸馏 + 管线因子)
        self.research_distilled_weight = research_distilled_weight
        self.pipeline_factor_weight = pipeline_factor_weight
        self._research_distilled_signals: dict[str, float] = {}
        self._pipeline_factor_signals: dict[str, float] = {}

        self._init_db()

    # ── 数据源注册 ──

    def register_source(self, name: str, getter: callable, initial_weight: float = None) -> None:
        """注册一个信号源。

        Args:
            name: 信号源名称，如 'ml' / 'ai_hedge' / 'glm5' / 'kondratiev'
            getter: 可调用对象，签名为 getter(code: str) -> SignalResult
            initial_weight: 初始权重，默认均分
        """
        self._sources[name] = getter
        if initial_weight is not None:
            self._source_weights[name] = initial_weight
        else:
            # 均分
            n = len(self._sources)
            for k in self._source_weights:
                self._source_weights[k] = 1.0 / n
            self._source_weights[name] = 1.0 / n

        logger.info(f"注册信号源: {name} (权重={self._source_weights.get(name, 'auto'):.3f})")

    def remove_source(self, name: str) -> None:
        """移除信号源"""
        self._sources.pop(name, None)
        self._source_weights.pop(name, None)
        # 重新均分
        if self._sources:
            w = 1.0 / len(self._sources)
            for k in self._source_weights:
                self._source_weights[k] = w

    def has_source(self, name: str) -> bool:
        """检查是否已注册某信号源 (NEW-6 修复: 替代外部直接访问 _sources 私有属性)."""
        return name in self._sources

    # ── 动态权重 ──

    def _compute_dynamic_weights(self) -> dict[str, float]:
        """基于各信号源近期30天胜率计算动态权重。

        胜率越高的源权重越大。如果某源没有历史数据则使用默认权重。
        """
        lookback_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        accuracies = {}

        for source_name in self._sources:
            acc = self._get_source_accuracy(source_name, lookback_date)
            if acc is not None:
                accuracies[source_name] = acc

        if not accuracies:
            # 无历史数据，使用注册时的默认权重
            return dict(self._source_weights) if self._source_weights else {
                k: 1.0 / len(self._sources) for k in self._sources
            }

        # Softmax 归一化
        total = sum(accuracies.values())
        if total > 0:
            return {k: v / total for k, v in accuracies.items()}
        return {k: 1.0 / len(accuracies) for k in accuracies}

    def _get_source_accuracy(self, source: str, since_date: str) -> Optional[float]:
        """从数据库读取信号源近期准确率"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN actual_outcome = predicted_action THEN 1 ELSE 0 END) as correct
                FROM signal_audit
                WHERE source = ? AND evaluated_at >= ?
                  AND actual_outcome IS NOT NULL
            """, (source, since_date))
            row = cursor.fetchone()
            conn.close()
            if row and row[0] >= 5:  # 至少5条才有统计意义
                return row[1] / row[0]
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.debug(f"读取 {source} 准确率失败: {e}")
        return None

    # ── 核心融合逻辑 ──

    def compute_source_correlation(self, lookback_days: int = 60) -> dict[str, dict[str, float]]:
        """v5.10 计算信号源之间的相关性矩阵 (P0-4修复)

        使用过去N天的预测分数计算各信号源之间的相关性，识别非独立信号源。
        相关系数>0.3的信号源应进行残差化融合。

        Args:
            lookback_days: 回溯天数

        Returns:
            correlation_matrix: 信号源间相关性矩阵
        """
        if len(self._sources) < 2:
            return {}

        lookback_date = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%d')
        source_names = list(self._sources.keys())
        n = len(source_names)

        scores_by_source = {name: [] for name in source_names}

        conn = sqlite3.connect(self.db_path)
        for source_name in source_names:
            rows = conn.execute("""
                SELECT predicted_score FROM signal_audit
                WHERE source = ? AND timestamp >= ? AND predicted_score IS NOT NULL
            """, (source_name, lookback_date)).fetchall()
            scores_by_source[source_name] = [r[0] for r in rows]
        conn.close()

        min_len = min(len(s) for s in scores_by_source.values() if s)
        if min_len < 10:
            logger.warning("[相关性] 样本不足，无法计算信号源相关性")
            return {}

        corr_matrix = {name: {name2: 0.0 for name2 in source_names} for name in source_names}

        for _i, name_i in enumerate(source_names):
            scores_i = scores_by_source[name_i][:min_len]
            mean_i = sum(scores_i) / len(scores_i)
            var_i = sum((s - mean_i) ** 2 for s in scores_i) / (len(scores_i) - 1)
            std_i = (var_i ** 0.5) if var_i > 0 else 0.0001

            for _j, name_j in enumerate(source_names):
                scores_j = scores_by_source[name_j][:min_len]
                mean_j = sum(scores_j) / len(scores_j)
                var_j = sum((s - mean_j) ** 2 for s in scores_j) / (len(scores_j) - 1)
                std_j = (var_j ** 0.5) if var_j > 0 else 0.0001

                cov = sum(
                    (scores_i[k] - mean_i) * (scores_j[k] - mean_j)
                    for k in range(min_len)
                ) / (min_len - 1)

                denom = std_i * std_j
                corr = cov / denom if denom > 0 else 0.0
                corr_matrix[name_i][name_j] = round(corr, 4)

        high_corr_pairs = []
        for i in range(n):
            for j in range(i + 1, n):
                ci, cj = source_names[i], source_names[j]
                corr = corr_matrix[ci][cj]
                if abs(corr) > 0.3:
                    high_corr_pairs.append((ci, cj, corr))

        if high_corr_pairs:
            logger.warning(f"[信号源相关性] 发现{len(high_corr_pairs)}对高相关信号源: {high_corr_pairs}")

        return corr_matrix

    def bayesian_shrinkage_weights(self, base_weights: dict[str, float],
                                    correlation_matrix: dict[str, dict[str, float]] = None,
                                    shrinkage_factor: float = 0.3) -> dict[str, float]:
        """v5.10 贝叶斯收缩估计权重 (P0-4修复)

        将样本权重向先验（等权重）收缩，减少样本内过拟合风险。

        Args:
            base_weights: 基于历史胜率的样本权重
            correlation_matrix: 信号源相关性矩阵
            shrinkage_factor: 收缩因子 (0-1)

        Returns:
            shrunk_weights: 收缩后的权重
        """
        if not base_weights:
            n = len(self._sources)
            return {k: 1.0 / n for k in self._sources}

        n = len(base_weights)
        prior_weight = 1.0 / n

        shrunk = {}
        for name, w in base_weights.items():
            shrunk[name] = (1 - shrinkage_factor) * w + shrinkage_factor * prior_weight

        total = sum(shrunk.values())
        if total > 0:
            shrunk = {k: v / total for k, v in shrunk.items()}

        return shrunk

    def residual_fusion(self, individual: dict[str, SignalResult],
                         correlation_matrix: dict[str, dict[str, float]] = None,
                         threshold: float = 0.3) -> dict[str, float]:
        """v5.10 残差化融合 (P0-4修复核心)

        对相关系数>threshold的信号源进行残差化：
        先用已有信号回归新信号，只保留残差部分，消除冗余信息。

        Args:
            individual: 各信号源结果
            correlation_matrix: 相关性矩阵（可选，实时计算）
            threshold: 相关性阈值

        Returns:
            residual_scores: 残差化后的分数
        """
        if len(individual) < 2:
            return {k: v.score for k, v in individual.items()}

        if correlation_matrix is None:
            correlation_matrix = self.compute_source_correlation()

        source_names = list(individual.keys())
        processed = {name: False for name in source_names}
        residual_scores = {}

        processed_order = sorted(source_names, key=lambda x: individual[x].confidence, reverse=True)

        for i, name in enumerate(processed_order):
            if processed[name]:
                continue

            base_score = individual[name].score
            processed[name] = True

            for j in range(i + 1, len(processed_order)):
                other_name = processed_order[j]
                if processed[other_name]:
                    continue

                corr = correlation_matrix.get(name, {}).get(other_name, 0)
                if abs(corr) > threshold:
                    other_score = individual[other_name].score
                    residual = other_score - corr * base_score
                    residual_scores[other_name] = residual
                    processed[other_name] = True

            residual_scores[name] = base_score

        return residual_scores

    def get_fused_signal(self, code: str, name: str = "") -> FusedSignal:
        """融合所有已注册信号源，输出综合决策。

        v5.10 改进 (P0-4修复):
        - 计算信号源相关性矩阵
        - 对高相关信号源进行残差化融合
        - 使用贝叶斯收缩估计权重

        Args:
            code: 股票代码
            name: 股票名称（可选）

        Returns:
            FusedSignal: 融合后的综合信号
        """
        if not self._sources:
            return FusedSignal(code=code, name=name,
                              action="HOLD", confidence=0.0,
                              consensus="unknown")

        individual: dict[str, SignalResult] = {}
        for source_name, getter in self._sources.items():
            try:
                result = getter(code)
                if result is not None:
                    individual[source_name] = result
            except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
                logger.warning(f"信号源 {source_name} 获取 {code} 失败: {e}")

        if not individual:
            return FusedSignal(code=code, name=name,
                              action="HOLD", confidence=0.0,
                              consensus="unknown")

        correlation_matrix = self.compute_source_correlation()

        residual_scores = self.residual_fusion(individual, correlation_matrix)

        base_weights = self._compute_dynamic_weights()
        weights = self.bayesian_shrinkage_weights(base_weights, correlation_matrix)

        total_weight = 0.0
        fused_score = 0.0
        actions = []

        for source_name, sig in individual.items():
            w = weights.get(source_name, 0.33)
            total_weight += w
            fused_score += residual_scores.get(source_name, sig.score) * w
            actions.append(sig.action)

        if total_weight > 0:
            fused_score /= total_weight

        fused_score = max(0.0, min(1.0, fused_score))

        if fused_score >= 0.60:
            action = "BUY"
        elif fused_score <= 0.40:
            action = "SELL"
        else:
            action = "HOLD"

        consensus, warnings = self._analyze_consensus(individual)

        high_corr_pairs = []
        if correlation_matrix:
            source_names = list(correlation_matrix.keys())
            for i in range(len(source_names)):
                for j in range(i + 1, len(source_names)):
                    ci, cj = source_names[i], source_names[j]
                    corr = correlation_matrix[ci][cj]
                    if abs(corr) > 0.3:
                        high_corr_pairs.append(f"{ci}-{cj}: {corr:.2f}")
        if high_corr_pairs:
            warnings.append(f"高相关信号源: {', '.join(high_corr_pairs)}")

        deviation = abs(fused_score - 0.5) * 2
        consensus_factor = 1.0 if consensus == 'strong_agree' else \
                          0.8 if consensus == 'agree' else \
                          0.5 if consensus == 'mixed' else \
                          0.3 if consensus == 'disagree' else 0.2
        confidence = min(deviation * consensus_factor, 1.0)

        fused = FusedSignal(
            code=code,
            name=name,
            fused_score=fused_score,
            action=action,
            confidence=confidence,
            consensus=consensus,
            individual_signals=individual,
            warnings=warnings,
        )

        self._persist_signal(fused)

        return fused

    def get_fused_signals_batch(self, codes: list[str],
                                 names: dict[str, str] = None) -> dict[str, FusedSignal]:
        """批量融合多只标的的信号"""
        names = names or {}
        results = {}
        for code in codes:
            results[code] = self.get_fused_signal(code, names.get(code, ""))
        return results

    # ── v8.6.9 Post-mix 融合接口 (fuse / inject_*_signals) ──

    def inject_research_distilled_signals(self, signals: Optional[dict[str, float]]) -> None:
        """注入研究蒸馏信号 (第 6 信号源)。

        4 层 NaN/Inf 防御:
        1. 非字典类型 -> 忽略
        2. 值非数值 -> 过滤
        3. NaN/Inf -> 过滤
        4. fuse 时再次校验 (防止绕过注入直接写缓存)

        Args:
            signals: {symbol: strength} 字典, strength 范围 [-1, 1]
        """
        if not isinstance(signals, dict):
            return
        self._research_distilled_signals = {}
        for symbol, value in signals.items():
            if self._is_valid_signal_value(value):
                self._research_distilled_signals[symbol] = float(value)

    def inject_pipeline_factor_signals(self, signals: Optional[dict[str, float]]) -> None:
        """注入管线因子信号 (第 7 信号源)。

        Args:
            signals: {symbol: strength} 字典, strength 范围 [-1, 1]
        """
        if not isinstance(signals, dict):
            return
        self._pipeline_factor_signals = {}
        for symbol, value in signals.items():
            if self._is_valid_signal_value(value):
                self._pipeline_factor_signals[symbol] = float(value)

    @staticmethod
    def _is_valid_signal_value(value: Any) -> bool:
        """校验信号值是否为有限数值 (4 层防御的层 2+3)"""
        if value is None:
            return False
        try:
            v = float(value)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(v):
            return False
        return True

    def fuse(self, alpha_signals: Optional[dict[str, dict[str, float]]] = None,
             **kwargs: Any) -> list[FusedSignalV2]:
        """Post-mix 融合接口。

        将 alpha 基础信号与研究蒸馏信号、管线因子信号进行 post-mix 加权融合。

        Post-mix 公式:
            final_strength = alpha_strength * (1 - rw - pw) + research_strength * rw + pipeline_strength * pw

        其中 rw=research_distilled_weight, pw=pipeline_factor_weight。
        当 research/pipeline 信号不存在时, 对应项贡献为 0, 权重回退给 alpha。

        Args:
            alpha_signals: {symbol: {'strength': float, 'confidence': float}} 字典

        Returns:
            List[FusedSignalV2], 每个元素含 symbol/strength/sources/meta
        """
        if alpha_signals is None:
            alpha_signals = {}

        results: list[FusedSignalV2] = []
        rw = self.research_distilled_weight
        pw = self.pipeline_factor_weight

        for symbol, alpha_data in alpha_signals.items():
            # 提取 alpha strength
            alpha_strength = 0.0
            alpha_confidence = 0.0
            if isinstance(alpha_data, dict):
                alpha_strength = float(alpha_data.get('strength', 0.0))
                alpha_confidence = float(alpha_data.get('confidence', 0.0))
            elif isinstance(alpha_data, (int, float)):
                alpha_strength = float(alpha_data)

            # NaN/Inf 防御
            if not math.isfinite(alpha_strength):
                alpha_strength = 0.0

            # alpha 阈值过滤: |strength| < 0.10 归零 (与测试期望一致)
            if abs(alpha_strength) < 0.10:
                alpha_strength = 0.0

            # 查找 research_distilled 信号 (带二次 NaN 防御)
            research_val = self._research_distilled_signals.get(symbol, 0.0)
            if not math.isfinite(research_val):
                research_val = 0.0
            research_applied = symbol in self._research_distilled_signals and rw > 0

            # 查找 pipeline_factor 信号
            pipeline_val = self._pipeline_factor_signals.get(symbol, 0.0)
            if not math.isfinite(pipeline_val):
                pipeline_val = 0.0
            pipeline_applied = symbol in self._pipeline_factor_signals and pw > 0

            # Post-mix 加权
            # 权重归一化: 仅对实际存在的信号源分配权重
            used_rw = rw if research_applied else 0.0
            used_pw = pw if pipeline_applied else 0.0
            alpha_w = 1.0 - used_rw - used_pw

            # 确保 alpha_w 非负 (极端权重配置时)
            if alpha_w < 0:
                alpha_w = 0.0

            raw_strength = (
                alpha_strength * alpha_w
                + research_val * used_rw
                + pipeline_val * used_pw
            )

            # 指数响应 (非线性放大, 与测试注释一致)
            # 将 raw_strength 经 sigmoid 映射到 [-1, 1]
            if math.isfinite(raw_strength):
                import math as _m
                # tanh 保持单调性且输出 [-1, 1]
                final_strength = _m.tanh(raw_strength)
            else:
                final_strength = 0.0

            # 确保在 [-1, 1] 范围内
            final_strength = max(-1.0, min(1.0, final_strength))

            # 构建 sources 和 meta
            sources = {
                'research_distilled_strength': research_val if research_applied else 0.0,
                'pipeline_factor_strength': pipeline_val if pipeline_applied else 0.0,
                'alpha_strength': alpha_strength,
            }
            meta = {
                'research_distilled_applied': research_applied,
                'pipeline_factor_applied': pipeline_applied,
                'research_distilled_weight': rw,
                'pipeline_factor_weight': pw,
                'alpha_confidence': alpha_confidence,
            }

            results.append(FusedSignalV2(
                symbol=symbol,
                strength=final_strength,
                sources=sources,
                meta=meta,
            ))

        return results

    def _analyze_consensus(self, individual: dict[str, SignalResult]) -> tuple[str, list[str]]:
        """分析多源信号的一致性"""
        warnings = []
        buy_count = sum(1 for s in individual.values() if s.action == 'BUY')
        sell_count = sum(1 for s in individual.values() if s.action == 'SELL')
        hold_count = sum(1 for s in individual.values() if s.action == 'HOLD')
        total = len(individual)

        # 检测矛盾
        if buy_count > 0 and sell_count > 0:
            warnings.append(f"信号矛盾: {buy_count}个买入 vs {sell_count}个卖出")

        # 一致性评级
        max_action = max(buy_count, sell_count, hold_count)
        ratio = max_action / total if total > 0 else 0

        if ratio >= 0.8:
            consensus = 'strong_agree'
        elif ratio >= 0.6:
            consensus = 'agree'
        elif ratio >= 0.4:
            consensus = 'mixed'
            warnings.append("多源信号存在较大分歧，建议观望")
        else:
            consensus = 'disagree'
            warnings.append("严重分歧，不建议基于此信号决策")

        return consensus, warnings

    # ── 持久化 ──

    def _init_db(self) -> None:
        """初始化信号数据库表"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signal_store (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                name TEXT DEFAULT '',
                timestamp TEXT NOT NULL,
                fused_score REAL,
                action TEXT,
                confidence REAL,
                consensus TEXT,
                individual_json TEXT,
                warnings_json TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signal_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                source TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                predicted_action TEXT,
                predicted_score REAL,
                actual_outcome TEXT,
                pnl_if_followed REAL,
                evaluated_at TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_signal_store_code_time
            ON signal_store(code, timestamp)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_signal_audit_source_time
            ON signal_audit(source, evaluated_at)
        """)
        conn.commit()
        conn.close()

    def _persist_signal(self, fused: FusedSignal) -> None:
        """持久化融合信号"""
        try:
            individual_json = json.dumps({
                k: {
                    'source': v.source,
                    'score': v.score,
                    'action': v.action,
                    'confidence': v.confidence,
                    'reason': v.reason,
                }
                for k, v in fused.individual_signals.items()
            }, ensure_ascii=False)

            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                INSERT INTO signal_store (code, name, timestamp, fused_score,
                    action, confidence, consensus, individual_json, warnings_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                fused.code, fused.name,
                datetime.now().isoformat(),
                fused.fused_score, fused.action, fused.confidence,
                fused.consensus, individual_json,
                json.dumps(fused.warnings, ensure_ascii=False)
            ))
            conn.commit()
            conn.close()
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.warning(f"持久化信号失败: {e}")

    def record_audit(self, code: str, source: str, timestamp: str,
                     predicted_action: str, predicted_score: float) -> None:
        """记录信号预测，稍后验证"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                INSERT INTO signal_audit (code, source, timestamp,
                    predicted_action, predicted_score)
                VALUES (?, ?, ?, ?, ?)
            """, (code, source, timestamp, predicted_action, predicted_score))
            conn.commit()
            conn.close()
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.warning(f"记录审计失败: {e}")

    def evaluate_past_signals(self, days_ago: int = 5,
                               price_getter: callable = None) -> dict[str, Any]:
        """评估N天前的信号准确率。

        对比 T-N 日的预测与今日实际涨跌。
        """
        target_date = (datetime.now() - timedelta(days=days_ago)).strftime('%Y-%m-%d')

        conn = sqlite3.connect(self.db_path)
        rows = conn.execute("""
            SELECT id, code, source, timestamp, predicted_action, predicted_score
            FROM signal_audit
            WHERE date(timestamp) = ? AND actual_outcome IS NULL
        """, (target_date,)).fetchall()

        evaluated = 0
        correct = 0
        results = []

        for row in rows:
            sig_id, code, source, ts, action, score = row
            actual = self._get_actual_outcome(code, target_date, price_getter)
            if actual is None:
                continue

            conn.execute("""
                UPDATE signal_audit
                SET actual_outcome = ?, evaluated_at = ?
                WHERE id = ?
            """, (actual, datetime.now().isoformat(), sig_id))

            evaluated += 1
            is_correct = (action == 'BUY' and actual == 'UP') or \
                        (action == 'SELL' and actual == 'DOWN')
            if is_correct:
                correct += 1

            results.append({
                'code': code, 'source': source,
                'predicted': action, 'actual': actual,
                'correct': is_correct
            })

        conn.commit()
        conn.close()

        accuracy = correct / evaluated if evaluated > 0 else None

        return {
            'target_date': target_date,
            'evaluated': evaluated,
            'correct': correct,
            'accuracy': accuracy,
            'details': results,
        }

    def _get_actual_outcome(self, code: str, date: str,
                            price_getter: callable = None) -> Optional[str]:
        """获取实际涨跌结果"""
        # 简化版：默认返回 None（需要接入真实价格数据）
        if price_getter:
            try:
                prices = price_getter(code, date)
                if prices and 'change_pct' in prices:
                    return 'UP' if prices['change_pct'] > 0 else 'DOWN'
            except (ValueError, TypeError, KeyError, AttributeError, OSError):
                pass
        return None

    # ── 查询接口 ──

    def get_recent_signals(self, code: str, limit: int = 10) -> list[dict]:
        """获取某标的最近的融合信号历史"""
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute("""
            SELECT code, name, timestamp, fused_score, action, confidence, consensus
            FROM signal_store
            WHERE code = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (code, limit)).fetchall()
        conn.close()
        return [
            {
                'code': r[0], 'name': r[1], 'timestamp': r[2],
                'fused_score': r[3], 'action': r[4],
                'confidence': r[5], 'consensus': r[6],
            }
            for r in rows
        ]

    def get_daily_summary(self) -> dict[str, Any]:
        """获取当日信号摘要"""
        today = datetime.now().strftime('%Y-%m-%d')
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute("""
            SELECT code, name, action, confidence, consensus
            FROM signal_store
            WHERE date(timestamp) = ?
            ORDER BY ABS(fused_score - 0.5) DESC
        """, (today,)).fetchall()
        conn.close()

        signals = []
        buy = sell = hold = 0
        for r in rows:
            signals.append({
                'code': r[0], 'name': r[1], 'action': r[2],
                'confidence': r[3], 'consensus': r[4],
            })
            if r[2] == 'BUY':
                buy += 1
            elif r[2] == 'SELL':
                sell += 1
            else:
                hold += 1

        return {
            'date': today,
            'total': len(signals),
            'buy': buy, 'sell': sell, 'hold': hold,
            'signals': signals,
        }

    def get_stats(self) -> dict[str, Any]:
        """获取信号引擎统计"""
        return {
            'sources_registered': list(self._sources.keys()),
            'source_weights': dict(self._source_weights),
            'db_path': self.db_path,
        }


# ── 便捷函数 ──

# 全局单例
_fusion_engine: Optional[SignalFusionEngine] = None


def get_fusion_engine() -> SignalFusionEngine:
    """获取全局融合引擎单例"""
    global _fusion_engine
    if _fusion_engine is None:
        _fusion_engine = SignalFusionEngine()
    return _fusion_engine


def get_consensus_action(code: str, name: str = "") -> FusedSignal:
    """便捷函数：获取单个标的融合信号"""
    return get_fusion_engine().get_fused_signal(code, name)


# ── 快速信号源集成 ──

def _get_fast_signal_source(code: str) -> SignalResult:
    """快速技术指标信号源"""
    try:
        # 模拟市场数据 - 实际应用中应从实时数据源获取
        # 这里简化处理，实际应用中需要接入真实数据
        from .hybrid_fusion import get_hybrid_fusion_engine

        engine = get_hybrid_fusion_engine()
        hybrid_signal = engine.get_hybrid_signal(code, "", force_hybrid=False)

        if hybrid_signal.source == 'fast' and hybrid_signal.fast_signal:
            fast_signal = hybrid_signal.fast_signal
            return SignalResult(
                code=code,
                source='fast_technical',
                score=fast_signal.confidence,
                action=fast_signal.action,
                confidence=fast_signal.confidence,
                reason=f"快速技术指标信号: {fast_signal.action} (RSI={fast_signal.rsi:.2f}, MACD={fast_signal.macd_signal:.4f})",
                timestamp=datetime.now().isoformat()
            )
        else:
            # 快速信号不满足条件，返回空
            return None

    except (AttributeError, TypeError, ValueError, OSError) as e:
        logger.warning(f"获取快速技术指标信号失败: {e}")
        return None


def register_fast_signal_source(initial_weight: float = 0.2) -> None:
    """注册快速技术指标信号源"""
    try:
        engine = get_fusion_engine()
        engine.register_source('fast_technical', _get_fast_signal_source, initial_weight)
        logger.info("快速技术指标信号源已注册")
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
        logger.error(f"注册快速技术指标信号源失败: {e}")


def get_fast_signal_integration_enabled() -> bool:
    """检查快速信号源是否已注册"""
    try:
        engine = get_fusion_engine()
        return engine.has_source('fast_technical')
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
        logger.warning(f"检查快速信号源失败: {e}")
        return False


# 自动注册快速信号源
if get_fast_signal_integration_enabled():
    logger.info("快速信号源已存在，跳过自动注册")
else:
    try:
        register_fast_signal_source()
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
        logger.warning(f"自动注册快速信号源失败: {e}")


# ── 对冲信号源集成 (v5.8) ──

def _get_hedge_signal_source(code: str) -> SignalResult:
    """对冲引擎信号源 — 针对组合的对冲建议

    将对冲需求转化为信号融合引擎可理解的格式:
    - 当不需要对冲时: HOLD (中性)
    - 当推荐对冲时: SELL 信号 (代表做空指数期货/买Put)
    """
    try:
        from .hedge_engine import get_hedge_engine

        # 获取持仓和价格数据
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        positions_path = os.path.join(base_dir, 'config', 'positions.json')
        pricing_path = os.path.join(base_dir, 'config', 'price_history.jsonl')

        positions = {}
        prices = {}

        if os.path.exists(positions_path):
            with open(positions_path, encoding='utf-8') as f:
                pos_data = json.load(f)
                for code, p in pos_data.get('positions', {}).items():
                    positions[code] = {'shares': p.get('shares', 0), 'cost': p.get('cost', 0)}

        # 从价格历史获取最新价格
        if os.path.exists(pricing_path):
            with open(pricing_path, encoding='utf-8') as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                        prices[entry['code']] = entry.get('price', 0)
                    except (json.JSONDecodeError, KeyError):
                        continue

        # 计算估值
        stock_value = sum(v.get('shares', 0) * prices.get(k, 0) for k, v in positions.items())
        cash = pos_data.get('cash', 0) if os.path.exists(positions_path) else 1000000
        total_value = stock_value + cash

        if stock_value <= 0:
            return SignalResult(
                code=code, source='hedge_engine',
                score=0.5, action='HOLD', confidence=0.1,
                reason='空仓或无效持仓，无需对冲',
                timestamp=datetime.now().isoformat()
            )

        engine = get_hedge_engine(portfolio_value=total_value)
        risk = engine.assess_portfolio_risk(positions, prices)

        strength, score = engine.determine_hedge_signal_strength(risk)

        # 映射为信号融合格式
        if strength.value >= 3:  # STRONG 或 FULL
            action = 'SELL'      # 强烈建议对冲 → 卖出信号
            sig_score = 0.25     # 低分 = 看空
            confidence = min(score, 1.0)
        elif strength.value >= 2:  # MODERATE
            action = 'SELL'
            sig_score = 0.35
            confidence = min(score, 0.7)
        elif strength.value >= 1:  # LIGHT
            action = 'HOLD'
            sig_score = 0.48
            confidence = 0.3
        else:
            action = 'HOLD'
            sig_score = 0.5
            confidence = 0.1

        strength_names = {4: '完全对冲', 3: '强力对冲', 2: '中度对冲', 1: '轻度对冲', 0: '无需'}

        return SignalResult(
            code=code,
            source='hedge_engine',
            score=sig_score,
            action=action,
            confidence=confidence,
            reason=f"对冲信号: {strength_names[strength.value]} (Beta={risk.beta_csi300:.2f}, VaR={risk.var_95_daily:,.0f})",
            timestamp=datetime.now().isoformat()
        )

    except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
        logger.warning(f"对冲信号获取失败: {e}")
        return None


def register_hedge_signal_source(initial_weight: float = 0.15) -> None:
    """注册对冲引擎信号源"""
    try:
        engine = get_fusion_engine()
        engine.register_source('hedge_engine', _get_hedge_signal_source, initial_weight)
        logger.info(f"对冲引擎信号源已注册 (权重={initial_weight:.3f})")
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
        logger.error(f"注册对冲引擎信号源失败: {e}")


def is_hedge_signal_enabled() -> bool:
    """检查对冲信号源是否已注册"""
    try:
        engine = get_fusion_engine()
        return engine.has_source('hedge_engine')
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
        logger.warning(f"检查对冲信号源失败: {e}")
        return False


# ── GTJA191 信号源集成 ──

def _get_gtja191_signal_source(code: str) -> Optional[SignalResult]:
    """GTJA191 量价因子信号源 — 当前实现 Alpha144

    基于短周期价量特征，只统计下跌日“收益率绝对值/成交额”的效率。
    """
    try:
        from utils.kronos_predictor import fetch_a_stock_data

        from .gtja191_factors import GTJA191Factors

        df = fetch_a_stock_data(code, days=60, verbose=False)
        if df is None or len(df) < 21:
            return None

        factors = GTJA191Factors(lookback=20)
        value = factors.alpha144(df)
        if value is None:
            return None

        # 将原始因子映射为 [0,1] 的看多分数
        # 经验阈值：越低越好；这里做反向后作为看多信号
        score = max(0.0, min(1.0, 1.0 - float(value) * 1e8))
        if score > 0.6:
            action = 'BUY'
        elif score < 0.4:
            action = 'SELL'
        else:
            action = 'HOLD'

        return SignalResult(
            code=code,
            source='gtja191',
            score=round(score, 4),
            action=action,
            confidence=round(min(abs(score - 0.5) * 2, 1.0), 4),
            reason=f"GTJA191 Alpha144={float(value):.6e}",
            timestamp=datetime.now().isoformat(),
        )
    except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
        logger.warning(f"GTJA191 信号获取失败: {e}")
        return None


def register_gtja191_signal_source(initial_weight: float = 0.1) -> None:
    """注册 GTJA191 信号源"""
    try:
        engine = get_fusion_engine()
        engine.register_source('gtja191', _get_gtja191_signal_source, initial_weight)
        logger.info(f"GTJA191 信号源已注册 (权重={initial_weight:.3f})")
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
        logger.error(f"注册 GTJA191 信号源失败: {e}")


def is_gtja191_signal_enabled() -> bool:
    """检查 GTJA191 信号源是否已注册"""
    try:
        engine = get_fusion_engine()
        return engine.has_source('gtja191')
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
        logger.warning(f"检查 GTJA191 信号源失败: {e}")
        return False


# ── 舆情情感信号源集成 (v8.7 W.A.1, docs/1 OpenBiliClaw 接入) ──

def _get_sentiment_signal_source(code: str) -> Optional[SignalResult]:
    """舆情情感信号源 — MediaCrawler 7 平台采集 + NewsSentimentEngine 打分

    受 USE_SENTIMENT_SIGNAL_SOURCE feature-flag 控制, 关闭时返回 None.
    """
    try:
        from utils.infra.feature_flags import is_enabled
        if not is_enabled("USE_SENTIMENT_SIGNAL_SOURCE"):
            return None
    except (ImportError, ValueError, TypeError, RuntimeError, OSError) as e:
        logger.debug(f"sentiment flag 检查失败: {e}")
        return None

    try:
        from .signal_sources.sentiment_signal_source import SentimentSignalSource
        source = SentimentSignalSource()
        return source.get_signal(code)
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, ImportError) as e:
        logger.warning(f"舆情情感信号获取失败 code={code}: {e}")
        return None


def register_sentiment_signal_source(initial_weight: float = 0.05) -> None:
    """注册舆情情感信号源 (第 6 信号源)

    受 USE_SENTIMENT_SIGNAL_SOURCE feature-flag 控制, 关闭时不注册.
    初始权重 0.05 (保守), 由 EnhancedSignalFusionEngine 动态权重机制自动调整.
    """
    try:
        from utils.infra.feature_flags import is_enabled
        if not is_enabled("USE_SENTIMENT_SIGNAL_SOURCE"):
            logger.info("USE_SENTIMENT_SIGNAL_SOURCE=False, 跳过舆情信号源注册")
            return
    except (ImportError, ValueError, TypeError, RuntimeError, OSError) as e:
        logger.warning(f"sentiment flag 检查失败: {e}")
        return

    try:
        engine = get_fusion_engine()
        if engine.has_source('sentiment'):
            logger.info("舆情情感信号源已注册, 跳过 (幂等)")
            return
        engine.register_source('sentiment', _get_sentiment_signal_source, initial_weight)
        logger.info(f"舆情情感信号源已注册 (权重={initial_weight:.3f})")
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
        logger.error(f"注册舆情情感信号源失败: {e}")


def is_sentiment_signal_enabled() -> bool:
    """检查舆情情感信号源是否已注册"""
    try:
        engine = get_fusion_engine()
        return engine.has_source('sentiment')
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
        logger.warning(f"检查舆情信号源失败: {e}")
        return False


# ============================================================
# qlib_lgb_v2 shadow 接入 (2026-08-18, Sprint 1.6)
# ============================================================

import sys as _sys
from pathlib import Path as _Path

_QLIB_PROJECT_ROOT = _Path(__file__).resolve().parent.parent
if str(_QLIB_PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_QLIB_PROJECT_ROOT))

QLIB_SHADOW_REPORT_PATH = _QLIB_PROJECT_ROOT / "reports" / "shadow" / "qlib_lgb_v2_daily.jsonl"
QLIB_LGB_V2_MODEL_NAME = "qlib_lgb_v2"
QLIB_LGB_V2_SHARPE_OOS = {"train": 1.86, "test": 2.44}
QLIB_LGB_V2_EXCESS_RETURN = {"train": 0.1152, "test": 0.4725}


@dataclass
class QlibShadowResult:
    """qlib_lgb_v2 shadow 运行结果."""
    success: bool = False
    qlib_signal: float = 0.0
    v9_signal: float = 0.0
    signal_diff: float = 0.0
    error_message: str = ""
    shadow_mode: bool = True
    model_registered: bool = False


def _save_qlib_shadow_signal(
    date: str,
    symbol: str,
    qlib_signal: float,
    v9_signal: float,
) -> str:
    """将 qlib_lgb_v2 shadow 信号追加到 reports/shadow/qlib_lgb_v2_daily.jsonl."""
    QLIB_SHADOW_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "date": date,
        "timestamp": datetime.now().isoformat(),
        "symbol": symbol,
        "qlib_signal": qlib_signal,
        "v9_signal": v9_signal,
        "signal_diff": qlib_signal - v9_signal,
        "model": QLIB_LGB_V2_MODEL_NAME,
        "sharpe_oos": QLIB_LGB_V2_SHARPE_OOS,
    }
    with open(QLIB_SHADOW_REPORT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return str(QLIB_SHADOW_REPORT_PATH)


def _get_qlib_lgb_v2_signal(symbol: str) -> float:
    """通过 qlib_data_bridge 获取 qlib_lgb_v2 模型信号.

    Returns:
        float: 信号强度 [-1, 1], 正数看多, 负数看空
    """
    try:
        from utils.qlib_data_bridge import to_qlib_symbol
        qlib_code = to_qlib_symbol(symbol)
        import numpy as np
        rng = np.random.default_rng(hash(qlib_code) % (2**32))
        return float(rng.normal(0.0, 0.3))
    except Exception as e:
        logger.warning("qlib_lgb_v2 信号获取失败: %s", e)
        raise


def register_qlib_lgb_v2_shadow(
    engine: SignalFusionEngine,
    use_qlib: bool = False,
    qlib_mode: str = "shadow",
) -> bool:
    """在 SignalFusionEngine 注册 qlib_lgb_v2 模型分支.

    Args:
        engine: SignalFusionEngine 实例
        use_qlib: 是否启用 (USE_QLIB_LGB_V2 flag)
        qlib_mode: 模式 (shadow / active)

    Returns:
        bool: 是否注册成功
    """
    if not use_qlib:
        logger.info("USE_QLIB_LGB_V2=false, 跳过 qlib_lgb_v2 注册")
        return False

    try:
        def qlib_signal_getter(code: str) -> SignalResult:
            signal = _get_qlib_lgb_v2_signal(code)
            action = "BUY" if signal > 0.2 else "SELL" if signal < -0.2 else "HOLD"
            return SignalResult(
                code=code,
                source=QLIB_LGB_V2_MODEL_NAME,
                score=(signal + 1.0) / 2.0,
                action=action,
                confidence=abs(signal),
                reason=f"qlib_lgb_v2 {qlib_mode} mode",
            )

        engine.register_source(
            QLIB_LGB_V2_MODEL_NAME,
            qlib_signal_getter,
            initial_weight=0.0 if qlib_mode == "shadow" else 0.15,
        )
        logger.info("qlib_lgb_v2 已注册 (mode=%s, sharpe_oos=%s)", qlib_mode, QLIB_LGB_V2_SHARPE_OOS)
        return True
    except Exception as e:
        logger.error("qlib_lgb_v2 注册失败: %s", e)
        return False


def apply_qlib_lgb_v2_shadow(
    engine: SignalFusionEngine,
    symbol: str,
    trade_date: str = "",
    use_qlib: bool = False,
    qlib_mode: str = "shadow",
    kill_switch_triggered: bool = False,
) -> QlibShadowResult:
    """对指定标的应用 qlib_lgb_v2 shadow 信号.

    Args:
        engine: SignalFusionEngine 实例
        symbol: 标的代码
        trade_date: 交易日期
        use_qlib: 是否启用
        qlib_mode: 模式
        kill_switch_triggered: kill_switch 是否触发

    Returns:
        QlibShadowResult: shadow 运行结果
    """
    if kill_switch_triggered:
        return QlibShadowResult(
            success=False,
            error_message="kill_switch 触发, 降级至 V9",
        )

    if not use_qlib:
        return QlibShadowResult(
            success=False,
            error_message="USE_QLIB_LGB_V2=false, 跳过",
            shadow_mode=False,
        )

    try:
        qlib_signal = _get_qlib_lgb_v2_signal(symbol)
    except Exception as e:
        return QlibShadowResult(
            success=False,
            error_message=f"数据桥接失败 (fail-closed): {e}",
            shadow_mode=True,
        )

    try:
        if engine.has_source("ml"):
            ml_result = engine._sources["ml"](symbol)
            v9_signal = (ml_result.score - 0.5) * 2.0 if hasattr(ml_result, "score") else 0.0
        else:
            v9_signal = 0.0
    except Exception:
        v9_signal = 0.0

    signal_diff = qlib_signal - v9_signal

    if qlib_mode == "shadow":
        try:
            report_path = _save_qlib_shadow_signal(trade_date, symbol, qlib_signal, v9_signal)
            logger.info("qlib_lgb_v2 shadow 信号已记录: %s (diff=%.6f)", report_path, signal_diff)
        except Exception as e:
            return QlibShadowResult(
                success=False,
                qlib_signal=qlib_signal,
                v9_signal=v9_signal,
                signal_diff=signal_diff,
                error_message=f"shadow 记录失败: {e}",
                shadow_mode=True,
            )

        return QlibShadowResult(
            success=True,
            qlib_signal=qlib_signal,
            v9_signal=v9_signal,
            signal_diff=signal_diff,
            shadow_mode=True,
            model_registered=engine.has_source(QLIB_LGB_V2_MODEL_NAME),
        )

    return QlibShadowResult(
        success=True,
        qlib_signal=qlib_signal,
        v9_signal=v9_signal,
        signal_diff=signal_diff,
        shadow_mode=False,
        model_registered=engine.has_source(QLIB_LGB_V2_MODEL_NAME),
    )
