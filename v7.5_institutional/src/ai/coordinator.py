# -*- coding: utf-8 -*-
"""
AI 协调器 — v5.7 Phase 1 优化

统一管理多AI系统的任务路由、结果协调和成本管控。
三个AI系统（AI Hedge Fund、GLM-5、豆包Speed）各自独立调用LLM，
可能产生矛盾建议。协调器职责：

1. 任务路由：根据任务类型自动选择最合适的AI模型
2. 结果协调：汇总多个AI系统的结论，检测矛盾并标注
3. 成本管控：每日Token预算、用量追踪、超预算自动切换便宜模型
4. 冲突解决：基于各AI系统历史胜率加权决策
"""

import os
import json
import time
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Tuple
from enum import Enum

try:
    from .logging_manager import get_logger
    logger = get_logger('ai_coordinator')
except ImportError:
    import logging
    logger = logging.getLogger('ai_coordinator')


class TaskType(Enum):
    """AI任务类型"""
    DAILY_REPORT = "daily_report"           # 日报告生成（质量要求中等，量大）
    INTRADAY_DECISION = "intraday_decision" # 盘中实时决策（速度要求高）
    DEEP_RESEARCH = "deep_research"         # 深度研究（质量要求极高，量少）
    SENTIMENT = "sentiment"                 # 情绪分析（量中等）
    MACRO_ANALYSIS = "macro_analysis"       # 宏观分析（质量要求高）


class Priority(Enum):
    """任务优先级"""
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class AICoordinator:
    """AI 协调器 — 统一管理多AI系统

    使用方式:
        coordinator = AICoordinator(daily_token_budget=500000)
        model = coordinator.route(TaskType.INTRADAY_DECISION, Priority.HIGH)
    """

    # 模型配置（价格按 2024-2025 市场行情估算，单位：元/1K tokens）
    MODEL_CONFIG = {
        'doubao_speed': {
            'cost_per_1k_input': 0.0008,
            'cost_per_1k_output': 0.002,
            'max_tokens': 32000,
            'roles': [TaskType.INTRADAY_DECISION, TaskType.DAILY_REPORT, TaskType.SENTIMENT],
        },
        'glm5': {
            'cost_per_1k_input': 0.015,
            'cost_per_1k_output': 0.015,
            'max_tokens': 128000,
            'roles': [TaskType.MACRO_ANALYSIS],
        },
        'deepseek': {
            'cost_per_1k_input': 0.002,
            'cost_per_1k_output': 0.008,
            'max_tokens': 64000,
            'roles': [TaskType.DEEP_RESEARCH],
        },
    }

    def __init__(self, daily_token_budget: int = 500000,
                 db_path: str = None):
        if db_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            db_path = os.path.join(base_dir, 'data', 'ai_coordinator.db')

        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self.daily_token_budget = daily_token_budget
        self._token_used_today = 0
        self._today = datetime.now().strftime('%Y-%m-%d')
        self._init_db()

    def _init_db(self):
        """初始化数据库"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS token_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                model TEXT NOT NULL,
                task_type TEXT NOT NULL,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                cost_estimate REAL DEFAULT 0.0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                source TEXT NOT NULL,
                ticker TEXT NOT NULL,
                action TEXT NOT NULL,
                confidence REAL,
                reasoning TEXT,
                model_used TEXT,
                tokens_consumed INTEGER,
                task_type TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_token_usage_date
            ON token_usage(date)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_decision_accuracy (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_id INTEGER NOT NULL,
                predicted_action TEXT NOT NULL,
                actual_outcome TEXT,
                pnl_if_followed REAL,
                evaluated_at TEXT NOT NULL,
                FOREIGN KEY (decision_id) REFERENCES ai_decisions(id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ai_decisions_source_ticker
            ON ai_decisions(source, ticker)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ai_decision_accuracy_id
            ON ai_decision_accuracy(decision_id)
        """)
        conn.commit()
        conn.close()

    # ── 任务路由 ──

    def route(self, task_type: TaskType, priority: Priority = Priority.MEDIUM) -> str:
        """根据任务类型和优先级选择最合适的模型。

        Returns:
            str: 模型名称 ('doubao_speed' / 'glm5' / 'deepseek')
        """
        # 检查每日预算
        self._refresh_daily_budget()
        budget_ratio = self._token_used_today / self.daily_token_budget if self.daily_token_budget > 0 else 0

        # 预算快用完 (>80%) → 强制切换便宜模型
        if budget_ratio > 0.8 and priority != Priority.CRITICAL:
            logger.warning(f"Token预算已使用 {budget_ratio:.0%}，强制切换到 doubao_speed")
            return 'doubao_speed'

        # 按任务类型匹配
        if task_type == TaskType.INTRADAY_DECISION:
            return 'doubao_speed'  # 盘中决策：豆包 Speed 速度快成本低

        if task_type == TaskType.DEEP_RESEARCH:
            return 'deepseek' if budget_ratio < 0.5 else 'doubao_speed'

        if task_type == TaskType.MACRO_ANALYSIS:
            return 'glm5' if budget_ratio < 0.6 else 'doubao_speed'

        # 默认：日报/情绪分析用便宜模型
        return 'doubao_speed'

    def _refresh_daily_budget(self):
        """刷新每日预算（跨天重置）"""
        today = datetime.now().strftime('%Y-%m-%d')
        if today != self._today:
            self._token_used_today = 0
            self._today = today

    def can_proceed(self, estimated_tokens: int = 1000) -> Tuple[bool, str]:
        """检查是否可以继续调用AI（预算内）"""
        self._refresh_daily_budget()
        after = self._token_used_today + estimated_tokens
        ratio = after / self.daily_token_budget if self.daily_token_budget > 0 else 0

        if ratio > 1.0:
            return False, f"超出每日预算 ({self.daily_token_budget:,} tokens)"
        if ratio > 0.9:
            return True, f"预算仅剩 {100 - ratio * 100:.0f}%，请谨慎使用"

        return True, ""

    # ── Token 用量追踪 ──

    def record_usage(self, model: str, task_type: str,
                     input_tokens: int, output_tokens: int):
        """记录Token用量"""
        config = self.MODEL_CONFIG.get(model, {})
        cost = (input_tokens * config.get('cost_per_1k_input', 0) +
                output_tokens * config.get('cost_per_1k_output', 0)) / 1000

        self._token_used_today += (input_tokens + output_tokens)

        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                INSERT INTO token_usage (date, model, task_type,
                    input_tokens, output_tokens, cost_estimate)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (datetime.now().strftime('%Y-%m-%d'), model, task_type,
                  input_tokens, output_tokens, cost))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug(f"记录Token用量失败: {e}")

    # ── AI决策持久化 ──

    def record_decision(self, source: str, ticker: str, action: str,
                        confidence: float = 0.0, reasoning: str = "",
                        model_used: str = "", tokens: int = 0,
                        task_type: str = ""):
        """记录AI决策"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                INSERT INTO ai_decisions (timestamp, source, ticker, action,
                    confidence, reasoning, model_used, tokens_consumed, task_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (datetime.now().isoformat(), source, ticker, action,
                  confidence, reasoning[:2000], model_used, tokens, task_type))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug(f"记录AI决策失败: {e}")

    # ── 冲突检测 ──

    def resolve_conflicts(self, decisions_by_source: Dict[str, Dict[str, str]]
                          ) -> Dict[str, Dict[str, Any]]:
        """检测并解决多AI系统对同一标的的矛盾信号。

        Args:
            decisions_by_source: {
                'ai_hedge': {'600519': 'BUY', '000001': 'HOLD'},
                'glm5': {'600519': 'SELL', '000001': 'HOLD'},
                'ml': {'600519': 'BUY', '000001': 'SELL'},
            }

        Returns:
            {
                '600519': {
                    'actions': {'ai_hedge': 'BUY', 'glm5': 'SELL', 'ml': 'BUY'},
                    'conflict': True,
                    'resolved_action': 'BUY',
                    'confidence': 0.67,
                },
            }
        """
        # 收集所有标的
        all_tickers = set()
        for decisions in decisions_by_source.values():
            all_tickers.update(decisions.keys())

        resolved = {}
        for ticker in all_tickers:
            actions = {}
            for source, decisions in decisions_by_source.items():
                if ticker in decisions:
                    actions[source] = decisions[ticker]

            buy_count = sum(1 for a in actions.values() if a == 'BUY')
            sell_count = sum(1 for a in actions.values() if a == 'SELL')
            hold_count = sum(1 for a in actions.values() if a == 'HOLD')

            # 是否有冲突（同时存在买入和卖出）
            has_conflict = buy_count > 0 and sell_count > 0

            # 多数投票
            if buy_count > sell_count and buy_count > hold_count:
                resolved_action = 'BUY'
                confidence = buy_count / len(actions)
            elif sell_count > buy_count and sell_count > hold_count:
                resolved_action = 'SELL'
                confidence = sell_count / len(actions)
            else:
                resolved_action = 'HOLD'
                confidence = max(buy_count, sell_count, hold_count) / len(actions)

            resolved[ticker] = {
                'actions': actions,
                'conflict': has_conflict,
                'resolved_action': resolved_action,
                'confidence': round(confidence, 2),
                'buy_votes': buy_count,
                'sell_votes': sell_count,
                'hold_votes': hold_count,
            }

        return resolved

    # ── 统计与查询 ──

    def get_daily_usage(self, date: str = None) -> Dict[str, Any]:
        """获取指定日期的Token使用统计"""
        date = date or datetime.now().strftime('%Y-%m-%d')
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute("""
            SELECT model, task_type,
                   SUM(input_tokens) as total_input,
                   SUM(output_tokens) as total_output,
                   SUM(cost_estimate) as total_cost
            FROM token_usage
            WHERE date = ?
            GROUP BY model, task_type
        """, (date,)).fetchall()
        conn.close()

        breakdown = []
        total_input = total_output = total_cost = 0
        for r in rows:
            total_input += r[2]
            total_output += r[3]
            total_cost += r[4]
            breakdown.append({
                'model': r[0], 'task_type': r[1],
                'input_tokens': r[2], 'output_tokens': r[3],
                'cost': round(r[4], 4),
            })

        return {
            'date': date,
            'total_input_tokens': total_input,
            'total_output_tokens': total_output,
            'total_tokens': total_input + total_output,
            'total_cost': round(total_cost, 4),
            'budget': self.daily_token_budget,
            'budget_used_pct': round(
                (total_input + total_output) / self.daily_token_budget * 100, 1
            ) if self.daily_token_budget > 0 else 0,
            'breakdown': breakdown,
        }

    def get_decision_history(self, ticker: str = None, source: str = None,
                              days: int = 7) -> List[Dict[str, Any]]:
        """查询AI决策历史"""
        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        conn = sqlite3.connect(self.db_path)

        query = """
            SELECT timestamp, source, ticker, action, confidence,
                   reasoning, model_used, tokens_consumed, task_type
            FROM ai_decisions
            WHERE date(timestamp) >= ?
        """
        params = [since]
        if ticker:
            query += " AND ticker = ?"
            params.append(ticker)
        if source:
            query += " AND source = ?"
            params.append(source)
        query += " ORDER BY timestamp DESC LIMIT 200"

        rows = conn.execute(query, params).fetchall()
        conn.close()

        return [
            {
                'timestamp': r[0], 'source': r[1], 'ticker': r[2],
                'action': r[3], 'confidence': r[4],
                'reasoning': (r[5] or "")[:200],
                'model_used': r[6], 'tokens': r[7], 'task_type': r[8],
            }
            for r in rows
        ]

    def get_stats(self) -> Dict[str, Any]:
        """获取协调器统计"""
        self._refresh_daily_budget()
        daily = self.get_daily_usage()
        return {
            'daily_token_used': self._token_used_today,
            'daily_budget': self.daily_token_budget,
            'daily_cost': daily['total_cost'],
            'models_available': list(self.MODEL_CONFIG.keys()),
        }

    # ── v5.7 Phase 2: AI决策准确率评估 ──

    def record_accuracy(self, decision_id: int, predicted_action: str,
                        actual_outcome: str = "", pnl_if_followed: float = 0.0):
        """记录AI决策的5日验证结果。"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                INSERT INTO ai_decision_accuracy
                (decision_id, predicted_action, actual_outcome, pnl_if_followed, evaluated_at)
                VALUES (?, ?, ?, ?, ?)
            """, (decision_id, predicted_action, actual_outcome,
                  pnl_if_followed, datetime.now().isoformat()))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug(f"记录AI决策准确率失败: {e}")

    def get_source_accuracy(self, days: int = 30) -> Dict[str, Any]:
        """统计各AI信号源的近期准确率（有验证数据的）。

        Returns:
            {'glm5': {'accuracy': 0.62, 'count': 25, 'avg_pnl': 0.012}, ...}
        """
        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        conn = sqlite3.connect(self.db_path)

        rows = conn.execute("""
            SELECT d.source,
                   COUNT(*) as total,
                   SUM(CASE
                       WHEN a.actual_outcome = 'WIN' THEN 1
                       WHEN d.action = 'BUY' AND a.pnl_if_followed > 0 THEN 1
                       WHEN d.action = 'SELL' AND a.pnl_if_followed > 0 THEN 1
                       ELSE 0
                   END) as wins,
                   AVG(a.pnl_if_followed) as avg_pnl
            FROM ai_decisions d
            JOIN ai_decision_accuracy a ON d.id = a.decision_id
            WHERE d.timestamp >= ?
            GROUP BY d.source
        """, (since,)).fetchall()
        conn.close()

        result = {}
        for r in rows:
            source, total, wins, avg_pnl = r
            result[source] = {
                'accuracy': round(wins / total, 3) if total > 0 else 0,
                'count': total,
                'avg_pnl': round(avg_pnl or 0, 4),
            }
        return result


# ── 全局单例 ──

_coordinator: Optional[AICoordinator] = None


def get_ai_coordinator() -> AICoordinator:
    """获取全局AI协调器单例"""
    global _coordinator
    if _coordinator is None:
        budget = int(os.environ.get('AI_TOKEN_BUDGET', '500000'))
        _coordinator = AICoordinator(daily_token_budget=budget)
    return _coordinator
