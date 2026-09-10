"""
团队级共享记忆中枢 — 把各 AI 分析师的决策教训聚合为跨会话共享记忆

借鉴 TencentDB-Agent-Memory 项目思路, 在现有 MemoryReflection (单 agent 反思)
之上新增团队级聚合/共享/检索层, 让分析师们"记得"上次为什么止损。

接入链路:
    MemoryReflection.evaluate_past_decisions (T+N 回访, 产出 reflection 文本)
        → TeamMemoryHub.share_lesson (写入共享池)
        → TeamMemoryHub.query_relevant_lessons (按 ticker/上下文检索)
        → orchestrator.run_ai_hedge_fund 构建 state 时注入 state["data"]["historical_lessons"]
        → 各分析师从 state 读取注入 prompt (零侵入分析师文件)

存储: SQLite team_lessons 表 (复用 ai_coordinator.db 或独立 db)
检索: SQLite LIKE + 时序衰减排序 (不引入 TF-IDF/sklearn, 保持零新依赖)

设计依据: docs/1设计计划集成到系统内并能完整运行_20260817.md W.B.2
上游: docs/1 (TencentDB-Agent-Memory 接入建议) + memory_reflection.py
下游: orchestrator.py run_ai_hedge_fund (state 注入) + memory_reflection.py (share_lesson)
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from utils.datetime_utils import now_bj

try:
    from ..logging_manager import get_logger

    logger = get_logger("team_memory_hub")
except ImportError:
    logger = logging.getLogger("team_memory_hub")

try:
    from ..infra.feature_flags import is_enabled
except ImportError:

    def is_enabled(name: str) -> bool:
        return False


FLAG_NAME = "USE_TEAM_MEMORY_HUB"


# ============================================================
# 数据类
# ============================================================


@dataclass
class Lesson:
    """一条团队教训"""

    lesson_id: str
    agent_name: str
    ticker: str
    lesson_text: str
    context: str = ""
    decision: str = ""
    outcome: str | None = None  # correct / incorrect1d / correct5d / unknown
    confidence: float = 0.0
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "lesson_id": self.lesson_id,
            "agent_name": self.agent_name,
            "ticker": self.ticker,
            "lesson_text": self.lesson_text,
            "context": self.context,
            "decision": self.decision,
            "outcome": self.outcome,
            "confidence": self.confidence,
            "created_at": self.created_at,
        }

    def to_prompt_text(self) -> str:
        """转为可注入 prompt 的简短文本"""
        outcome_tag = f"[{self.outcome}]" if self.outcome else ""
        return f"- {self.ticker} {outcome_tag} {self.lesson_text[:200]}"


@dataclass
class AgentProfile:
    """分析师画像 (历史聚合)"""

    agent_name: str
    total_lessons: int = 0
    correct_count: int = 0
    wrong_count: int = 0
    recent_lessons: list[Lesson] = field(default_factory=list)
    tickers_covered: list[str] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        if self.correct_count + self.wrong_count == 0:
            return 0.0
        return self.correct_count / (self.correct_count + self.wrong_count)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "total_lessons": self.total_lessons,
            "correct_count": self.correct_count,
            "wrong_count": self.wrong_count,
            "accuracy": round(self.accuracy, 4),
            "tickers_covered": self.tickers_covered,
        }


# ============================================================
# TeamMemoryHub 主类
# ============================================================


class TeamMemoryHub:
    """团队级共享记忆中枢

    使用方式:
        hub = TeamMemoryHub()
        hub.share_lesson("warren_buffett", "600519", "看多判断失误, 低估空头证据")
        lessons = hub.query_relevant_lessons("600519", ticker="600519", top_k=5)

    Feature Flag:
        USE_TEAM_MEMORY_HUB=True 启用, False 时所有操作降级为 no-op
    """

    def __init__(self, db_path: str | None = None) -> None:
        if db_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            db_path = os.path.join(base_dir, "data", "team_memory.db")
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    # ------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self) -> None:
        try:
            conn = self._get_conn()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS team_lessons (
                    lesson_id TEXT PRIMARY KEY,
                    agent_name TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    lesson_text TEXT NOT NULL,
                    context TEXT DEFAULT '',
                    decision TEXT DEFAULT '',
                    outcome TEXT,
                    confidence REAL DEFAULT 0.0,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_lessons_ticker ON team_lessons(ticker)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_lessons_agent ON team_lessons(agent_name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_lessons_created ON team_lessons(created_at)"
            )
            conn.commit()
        except (ValueError, TypeError, sqlite3.Error, OSError) as e:
            logger.warning("team_lessons 表初始化失败: %s", e)

    # ------------------------------------------------------------
    # 写入教训
    # ------------------------------------------------------------

    def share_lesson(
        self,
        agent_name: str,
        ticker: str,
        lesson_text: str,
        context: str = "",
        decision: str = "",
        outcome: str | None = None,
        confidence: float = 0.0,
    ) -> str | None:
        """分析师把教训写入共享池

        Args:
            agent_name: 分析师名 (如 "warren_buffett")
            ticker: 标的代码
            lesson_text: 教训文本
            context: 决策上下文 (如 "bullish conf=75")
            decision: 决策动作 (如 "BUY" / "bullish")
            outcome: 结果 (correct / wrong1d / correct5d / unknown)
            confidence: 决策置信度 [0,1]

        Returns:
            lesson_id 或 None (flag 关闭 / 失败)
        """
        if not is_enabled(FLAG_NAME):
            return None
        if not agent_name or not ticker or not lesson_text:
            return None

        lesson_id = f"{agent_name}_{ticker}_{now_bj().strftime('%Y%m%d%H%M%S%f')}"
        created_at = now_bj().isoformat()

        try:
            conn = self._get_conn()
            conn.execute(
                """INSERT INTO team_lessons
                   (lesson_id, agent_name, ticker, lesson_text, context, decision, outcome, confidence, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    lesson_id,
                    agent_name,
                    ticker,
                    lesson_text[:2000],
                    context[:500],
                    decision[:50],
                    outcome,
                    float(confidence),
                    created_at,
                ),
            )
            conn.commit()
            logger.debug("share_lesson: %s @ %s by %s", ticker, lesson_id, agent_name)
            return lesson_id
        except (ValueError, TypeError, sqlite3.Error, OSError) as e:
            logger.warning("share_lesson 失败: %s", e)
            return None

    # ------------------------------------------------------------
    # 检索教训
    # ------------------------------------------------------------

    def query_relevant_lessons(
        self,
        context: str = "",
        ticker: str = "",
        agent_name: str = "",
        top_k: int = 5,
        days_back: int = 90,
    ) -> list[Lesson]:
        """检索相关历史教训

        Args:
            context: 当前上下文 (用于关键词匹配, 空则不匹配)
            ticker: 标的过滤 (空则全部)
            agent_name: 分析师过滤 (空则全部)
            top_k: 最多返回数
            days_back: 回溯天数

        Returns:
            List[Lesson], 按时序倒序 (最新在前)
        """
        if not is_enabled(FLAG_NAME):
            return []

        try:
            conn = self._get_conn()
            sql = "SELECT lesson_id, agent_name, ticker, lesson_text, context, decision, outcome, confidence, created_at FROM team_lessons WHERE created_at >= ?"  # noqa: E501
            params: list[Any] = [
                (now_bj() - timedelta(days=days_back)).isoformat()
            ]

            if ticker:
                sql += " AND ticker = ?"
                params.append(ticker)
            if agent_name:
                sql += " AND agent_name = ?"
                params.append(agent_name)
            if context:
                sql += " AND (lesson_text LIKE ? OR context LIKE ?)"
                kw = f"%{context[:100]}%"
                params.extend([kw, kw])

            sql += " ORDER BY created_at DESC LIMIT ?"
            params.append(top_k)

            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_lesson(r) for r in rows]
        except (ValueError, TypeError, sqlite3.Error, OSError) as e:
            logger.debug("query_relevant_lessons 失败: %s", e)
            return []

    def get_lessons_for_tickers(
        self,
        tickers: list[str],
        top_k_per_ticker: int = 3,
        days_back: int = 90,
    ) -> dict[str, list[Lesson]]:
        """批量获取多个标的的教训 (供 orchestrator 注入 state)

        Returns:
            {ticker: [Lesson, ...]}
        """
        if not is_enabled(FLAG_NAME):
            return {}
        result: dict[str, list[Lesson]] = {}
        for tk in tickers:
            if not tk:
                continue
            result[tk] = self.query_relevant_lessons(
                ticker=tk, top_k=top_k_per_ticker, days_back=days_back
            )
        return result

    def build_lessons_prompt_block(
        self,
        tickers: list[str],
        top_k_per_ticker: int = 3,
        days_back: int = 90,
    ) -> str:
        """构建可注入 prompt 的教训文本块 (供 orchestrator state 注入)

        Returns:
            格式化文本, 如:
            [历史教训]
            600519:
              - [correct] 看多判断准确, 业绩超预期
              - [wrong1d] 低估空头证据
            000001:
              (无历史教训)
        """
        if not is_enabled(FLAG_NAME):
            return ""
        lessons_map = self.get_lessons_for_tickers(tickers, top_k_per_ticker, days_back)
        if not any(lessons_map.values()):
            return ""

        lines = ["[历史教训]"]
        for tk in tickers:
            lessons = lessons_map.get(tk, [])
            if not lessons:
                lines.append(f"{tk}: (无历史教训)")
                continue
            lines.append(f"{tk}:")
            for ls in lessons:
                lines.append(f"  {ls.to_prompt_text()}")
        return "\n".join(lines)

    # ------------------------------------------------------------
    # 分析师画像
    # ------------------------------------------------------------

    def get_agent_profile(self, agent_name: str, recent_n: int = 10) -> AgentProfile:
        """聚合某分析师的历史画像"""
        profile = AgentProfile(agent_name=agent_name)
        if not is_enabled(FLAG_NAME) or not agent_name:
            return profile

        try:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT COUNT(*) as total, SUM(CASE WHEN outcome LIKE 'correct%' THEN 1 ELSE 0 END) as correct, SUM(CASE WHEN outcome LIKE 'wrong%' THEN 1 ELSE 0 END) as wrong FROM team_lessons WHERE agent_name = ?",  # noqa: E501
                (agent_name,),
            ).fetchone()
            profile.total_lessons = row["total"] if row else 0
            profile.correct_count = (row["correct"] if row else 0) or 0
            profile.wrong_count = (row["wrong"] if row else 0) or 0

            recent_rows = conn.execute(
                "SELECT lesson_id, agent_name, ticker, lesson_text, context, decision, outcome, confidence, created_at FROM team_lessons WHERE agent_name = ? ORDER BY created_at DESC LIMIT ?",  # noqa: E501
                (agent_name, recent_n),
            ).fetchall()
            profile.recent_lessons = [self._row_to_lesson(r) for r in recent_rows]

            ticker_rows = conn.execute(
                "SELECT DISTINCT ticker FROM team_lessons WHERE agent_name = ?",
                (agent_name,),
            ).fetchall()
            profile.tickers_covered = [r["ticker"] for r in ticker_rows]
        except (ValueError, TypeError, sqlite3.Error, OSError) as e:
            logger.debug("get_agent_profile 失败: %s", e)

        return profile

    # ------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """记忆中枢统计"""
        try:
            conn = self._get_conn()
            total = conn.execute("SELECT COUNT(*) FROM team_lessons").fetchone()[0]
            by_agent = {
                r[0]: r[1]
                for r in conn.execute(
                    "SELECT agent_name, COUNT(*) FROM team_lessons GROUP BY agent_name"
                )
            }
            by_outcome = {
                r[0] or "unknown": r[1]
                for r in conn.execute(
                    "SELECT outcome, COUNT(*) FROM team_lessons GROUP BY outcome"
                )
            }
            return {
                "db_path": self.db_path,
                "flag_enabled": is_enabled(FLAG_NAME),
                "total_lessons": total,
                "by_agent": by_agent,
                "by_outcome": by_outcome,
            }
        except (ValueError, TypeError, sqlite3.Error, OSError) as e:
            return {"db_path": self.db_path, "error": str(e)}

    # ------------------------------------------------------------
    # 资源管理
    # ------------------------------------------------------------

    def _row_to_lesson(self, row: sqlite3.Row) -> Lesson:
        return Lesson(
            lesson_id=row["lesson_id"],
            agent_name=row["agent_name"],
            ticker=row["ticker"],
            lesson_text=row["lesson_text"],
            context=row["context"],
            decision=row["decision"],
            outcome=row["outcome"],
            confidence=row["confidence"],
            created_at=row["created_at"],
        )

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except (ValueError, TypeError, RuntimeError, OSError):
                pass
            self._conn = None

    def __enter__(self) -> TeamMemoryHub:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()


# ============================================================
# 便捷函数
# ============================================================

_hub_instance: TeamMemoryHub | None = None


def get_team_memory_hub() -> TeamMemoryHub:
    """获取全局 TeamMemoryHub 单例"""
    global _hub_instance
    if _hub_instance is None:
        _hub_instance = TeamMemoryHub()
    return _hub_instance
