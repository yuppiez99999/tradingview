# -*- coding: utf-8 -*-
"""
信号审计器 — v5.7 Phase 2

对ML信号、AI Hedge Fund信号、GLM-5信号进行质量追踪和闭环评估。

核心功能：
1. record_signal() — 记录每次生成的信号
2. evaluate_accuracy() — 回顾N天前的信号，对比实际涨跌
3. get_optimal_threshold() — 基于历史动态确定最优阈值
4. 信号闭环：生成 → 验证 → 统计 → 反馈

使用方式:
    auditor = SignalAuditor()
    auditor.record_signal('600519', 'ml', 'BUY', 0.72)
    stats = auditor.evaluate_accuracy(lookback_days=30)
    optimal = auditor.get_optimal_threshold('600519', market_regime='volatile')
"""

import os
import sqlite3
import json
from datetime import datetime, timedelta, date
from typing import Dict, Any, Optional, List, Tuple

try:
    from .logging_manager import get_logger
    logger = get_logger('signal_audit')
except ImportError:
    import logging
    logger = logging.getLogger('signal_audit')


class SignalAuditor:
    """信号质量审计器 — 追踪信号准确率并动态调整阈值"""

    def __init__(self, db_path: str = None):
        if db_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            db_path = os.path.join(base_dir, 'data', 'signal_audit.db')
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        # 信号记录表
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signal_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                generated_at TEXT NOT NULL,
                code TEXT NOT NULL,
                source TEXT NOT NULL,
                action TEXT NOT NULL,
                probability REAL,
                confidence REAL,
                reason TEXT,
                market_regime TEXT DEFAULT 'unknown',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # 准确率验证表（5日后回看）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signal_accuracy (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                source TEXT NOT NULL,
                predicted_action TEXT NOT NULL,
                predicted_prob REAL,
                price_at_signal REAL,
                price_5d_later REAL,
                actual_return_5d REAL,
                was_correct INTEGER,
                evaluated_at TEXT NOT NULL,
                FOREIGN KEY (signal_id) REFERENCES signal_records(id)
            )
        """)
        # 动态阈值表
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dynamic_thresholds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                market_regime TEXT NOT NULL DEFAULT 'neutral',
                buy_threshold REAL,
                sell_threshold REAL,
                sample_count INTEGER DEFAULT 0,
                accuracy REAL,
                updated_at TEXT NOT NULL,
                UNIQUE(code, market_regime)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_signal_records_code
            ON signal_records(code, generated_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_signal_records_source
            ON signal_records(source, generated_at)
        """)
        conn.commit()
        conn.close()

    # ── 信号记录 ──

    def record_signal(self, code: str, source: str, action: str,
                      probability: float = 0.5, confidence: float = 0.5,
                      reason: str = "", market_regime: str = "neutral") -> int:
        """记录一条信号。返回信号ID（用于后续准确率评估）。"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute("""
                INSERT INTO signal_records
                (generated_at, code, source, action, probability, confidence, reason, market_regime)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (datetime.now().isoformat(), code, source, action,
                  probability, confidence, reason[:1000], market_regime))
            signal_id = cursor.lastrowid
            conn.commit()
            conn.close()
            return signal_id
        except Exception as e:
            logger.debug(f"记录信号失败: {e}")
            return -1

    def record_batch(self, signals: List[Dict[str, Any]]) -> List[int]:
        """批量记录信号。

        Args:
            signals: [{'code': '600519', 'source': 'ml', 'action': 'BUY',
                        'probability': 0.72, 'confidence': 0.68}, ...]
        Returns:
            信号ID列表
        """
        ids = []
        for s in signals:
            sid = self.record_signal(
                code=s.get('code', ''),
                source=s.get('source', 'unknown'),
                action=s.get('action', 'HOLD'),
                probability=s.get('probability', 0.5),
                confidence=s.get('confidence', 0.5),
                reason=s.get('reason', ''),
                market_regime=s.get('market_regime', 'neutral'),
            )
            if sid > 0:
                ids.append(sid)
        return ids

    # ── 准确率评估 ──

    def evaluate_signal(self, signal_id: int, price_at_signal: float,
                        price_5d_later: float) -> Optional[Dict]:
        """评估单条信号5日后的实际效果。

        Returns:
            {'was_correct': True/False, 'actual_return_5d': 0.05, ...}
        """
        try:
            conn = sqlite3.connect(self.db_path)
            record = conn.execute(
                "SELECT code, source, action, probability FROM signal_records WHERE id = ?",
                (signal_id,)
            ).fetchone()
            if not record:
                conn.close()
                return None

            code, source, action, prob = record
            actual_return = (price_5d_later - price_at_signal) / price_at_signal

            # 判断是否正确
            if action == 'BUY':
                was_correct = 1 if actual_return > 0 else 0
            elif action == 'SELL':
                was_correct = 1 if actual_return < 0 else 0
            else:
                was_correct = 1 if abs(actual_return) < 0.02 else 0  # HOLD: 波动<2%算正确

            conn.execute("""
                INSERT INTO signal_accuracy
                (signal_id, code, source, predicted_action, predicted_prob,
                 price_at_signal, price_5d_later, actual_return_5d, was_correct, evaluated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (signal_id, code, source, action, prob,
                  price_at_signal, price_5d_later, round(actual_return, 6),
                  was_correct, datetime.now().isoformat()))
            conn.commit()
            conn.close()

            return {
                'signal_id': signal_id,
                'code': code,
                'source': source,
                'predicted_action': action,
                'predicted_prob': prob,
                'price_at_signal': price_at_signal,
                'price_5d_later': price_5d_later,
                'actual_return_5d': round(actual_return * 100, 2),
                'was_correct': bool(was_correct),
            }
        except Exception as e:
            logger.debug(f"评估信号失败: {e}")
            return None

    def evaluate_accuracy(self, lookback_days: int = 30,
                          source: str = None) -> Dict[str, Any]:
        """回顾N天内的信号准确率。

        Returns:
            {
                'total_signals': 150,
                'evaluated_count': 120,
                'overall_accuracy': 0.58,
                'by_source': {
                    'ml': {'accuracy': 0.55, 'count': 80},
                    'glm5': {'accuracy': 0.62, 'count': 25},
                    'ai_hedge': {'accuracy': 0.60, 'count': 15},
                },
                'by_action': {
                    'BUY': {'accuracy': 0.56, 'count': 60},
                    'SELL': {'accuracy': 0.53, 'count': 40},
                    'HOLD': {'accuracy': 0.70, 'count': 20},
                },
            }
        """
        since = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%d')

        conn = sqlite3.connect(self.db_path)

        # 按来源统计
        source_query = """
            SELECT sr.source,
                   COUNT(*) as total,
                   SUM(sa.was_correct) as correct
            FROM signal_accuracy sa
            JOIN signal_records sr ON sa.signal_id = sr.id
            WHERE sr.generated_at >= ?
        """
        params = [since]
        if source:
            source_query += " AND sr.source = ?"
            params.append(source)
        source_query += " GROUP BY sr.source"
        source_rows = conn.execute(source_query, params).fetchall()

        # 按操作统计
        action_query = """
            SELECT sa.predicted_action,
                   COUNT(*) as total,
                   SUM(sa.was_correct) as correct
            FROM signal_accuracy sa
            JOIN signal_records sr ON sa.signal_id = sr.id
            WHERE sr.generated_at >= ?
        """
        a_params = [since]
        if source:
            action_query += " AND sr.source = ?"
            a_params.append(source)
        action_query += " GROUP BY sa.predicted_action"
        action_rows = conn.execute(action_query, a_params).fetchall()

        # 总计
        total_query = """
            SELECT COUNT(*) as total,
                   COALESCE(SUM(sa.was_correct), 0) as correct
            FROM signal_accuracy sa
            JOIN signal_records sr ON sa.signal_id = sr.id
            WHERE sr.generated_at >= ?
        """
        t_params = [since]
        if source:
            total_query += " AND sr.source = ?"
            t_params.append(source)
        total_row = conn.execute(total_query, t_params).fetchone()

        conn.close()

        by_source = {}
        for r in source_rows:
            s_name, total, correct = r
            by_source[s_name] = {
                'accuracy': round(correct / total, 3) if total > 0 else 0,
                'count': total,
            }

        by_action = {}
        for r in action_rows:
            a_name, total, correct = r
            by_action[a_name] = {
                'accuracy': round(correct / total, 3) if total > 0 else 0,
                'count': total,
            }

        total_count, total_correct = total_row if total_row else (0, 0)

        return {
            'total_signals': sum(v['count'] for v in by_source.values()),
            'evaluated_count': total_count,
            'overall_accuracy': round(total_correct / total_count, 3) if total_count > 0 else 0,
            'by_source': by_source,
            'by_action': by_action,
            'lookback_days': lookback_days,
        }

    # ── 动态阈值 ──

    def get_optimal_threshold(self, code: str = None,
                               market_regime: str = "neutral",
                               min_samples: int = 10) -> Dict[str, float]:
        """基于历史信号准确率，为指定市场状态计算最优买卖阈值。

        牛市 → 阈值上调（减少假买入）; 熊市 → 阈值上调（减少假买入）
        震荡市 → 阈值下调（捕捉波段）

        Returns:
            {'buy_threshold': 0.60, 'sell_threshold': 0.40}
        """
        threshold_map = {
            'bull': {'buy': 0.62, 'sell': 0.38},
            'bear': {'buy': 0.65, 'sell': 0.35},
            'neutral': {'buy': 0.58, 'sell': 0.42},
            'volatile': {'buy': 0.55, 'sell': 0.45},
        }
        defaults = threshold_map.get(market_regime, threshold_map['neutral'])

        # 尝试从数据库加载基于历史数据的优化阈值
        try:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute("""
                SELECT buy_threshold, sell_threshold, sample_count, accuracy
                FROM dynamic_thresholds
                WHERE market_regime = ?
                AND sample_count >= ?
            """, (market_regime, min_samples)).fetchall()
            conn.close()

            if rows:
                # 选择准确率最高的阈值
                best = max(rows, key=lambda r: r[3])
                if best[0] and best[1]:
                    return {'buy_threshold': best[0], 'sell_threshold': best[1]}
        except Exception:
            pass

        return defaults

    def update_threshold(self, code: str, market_regime: str,
                          buy_threshold: float, sell_threshold: float,
                          sample_count: int = 0, accuracy: float = 0):
        """更新动态阈值"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                INSERT INTO dynamic_thresholds
                (code, market_regime, buy_threshold, sell_threshold,
                 sample_count, accuracy, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code, market_regime) DO UPDATE SET
                    buy_threshold = excluded.buy_threshold,
                    sell_threshold = excluded.sell_threshold,
                    sample_count = excluded.sample_count,
                    accuracy = excluded.accuracy,
                    updated_at = excluded.updated_at
            """, (code, market_regime, buy_threshold, sell_threshold,
                  sample_count, accuracy, datetime.now().isoformat()))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug(f"更新阈值失败: {e}")

    # ── 批量评估：自动查找5天前的信号并尝试评估 ──

    def auto_evaluate_pending(self, price_getter=None,
                               max_signals: int = 50) -> int:
        """自动评估等待验证的信号（5天前生成、尚未评估的）。

        Args:
            price_getter: callable(code, date) -> price，用于获取5日后的价格
            max_signals: 最多评估多少条
        Returns:
            成功评估的信号数
        """
        five_days_ago = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')
        conn = sqlite3.connect(self.db_path)

        # 查找5天前生成但尚未评估的信号
        rows = conn.execute("""
            SELECT sr.id, sr.code, sr.source, sr.action, sr.probability, sr.generated_at
            FROM signal_records sr
            WHERE sr.generated_at BETWEEN ? AND ?
            AND sr.id NOT IN (SELECT signal_id FROM signal_accuracy)
            ORDER BY sr.generated_at DESC
            LIMIT ?
        """, (five_days_ago + " 00:00:00", five_days_ago + " 23:59:59", max_signals)
        ).fetchall()
        conn.close()

        evaluated = 0
        for r in rows:
            sig_id, code, source, action, prob, gen_at = r
            if not price_getter:
                continue
            try:
                signal_date = gen_at[:10]
                price_start = price_getter(code, signal_date)
                five_days_later = (datetime.strptime(signal_date, '%Y-%m-%d') + timedelta(days=5)).strftime('%Y-%m-%d')
                price_end = price_getter(code, five_days_later)
                if price_start and price_end:
                    result = self.evaluate_signal(sig_id, price_start, price_end)
                    if result:
                        evaluated += 1
            except Exception:
                continue

        return evaluated

    # ── 信号源权重推荐 ──

    def get_source_weights(self, lookback_days: int = 60) -> Dict[str, float]:
        """基于各信号源近期准确率推荐融合权重。

        Returns:
            {'ml': 0.35, 'glm5': 0.40, 'ai_hedge': 0.25}
        """
        accuracy = self.evaluate_accuracy(lookback_days=lookback_days)
        by_source = accuracy.get('by_source', {})

        if not by_source:
            return {'ml': 0.40, 'glm5': 0.35, 'ai_hedge': 0.25}

        # Softmax-like 归一化
        total_weight = 0
        weights = {}
        for src, info in by_source.items():
            # 准确率 * 样本数对数（惩罚样本太少的源）
            w = info['accuracy'] * (1 + 0.1 * (info['count'] ** 0.5 if info['count'] > 0 else 0))
            weights[src] = w
            total_weight += w

        if total_weight > 0:
            return {k: round(v / total_weight, 3) for k, v in weights.items()}

        return {'ml': 0.40, 'glm5': 0.35, 'ai_hedge': 0.25}


# ── 全局单例 ──

_auditor: Optional[SignalAuditor] = None


def get_signal_auditor() -> SignalAuditor:
    global _auditor
    if _auditor is None:
        _auditor = SignalAuditor()
    return _auditor
