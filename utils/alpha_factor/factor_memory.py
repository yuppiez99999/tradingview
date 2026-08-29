"""因子记忆层 — OpenViking 风格脚手架 (Wave 9 / W9-B)

灵感来源: https://github.com/volcengine/OpenViking (29,362 stars, 2026-08-19 GitHub Trending)
+ https://github.com/akitaonrails/ai-memory (2,716 stars, Rust, Agent 长期记忆)

为 28 系统提供因子实验结论的跨会话保留, 避免重复回测:
  - 因子定义 + IC 历史 + 实验结论 (有效/无效/过拟合/前视偏差)
  - 策略迭代记忆 (回测参数 + 结果 + 决策依据)
  - PR review pipeline 上下文 (review 结论 + 影响半径)

设计原则:
  - 边缘安全: 不动 utils/alpha_factor/library.py 主链路, 仅提供记忆 API
  - 后端 sqlite (零依赖, 与 ai_memory/team_memory_hub.py 风格一致)
  - 09-05 前仅脚手架 + POC, 不接入 evolution/auto_factor_factory.py
  - 09-06 起进入实质对接 (W9-B Sprint)

接入点:
  - data/feature_registry.json (因子注册表, 09-06 后对接)
  - utils/evolution/auto_factor_factory.py (因子自动发现, 09-06 后对接)
  - utils/ai_memory/team_memory_hub.py (团队记忆, 互补不重复)

关联文档:
  - docs/高价值项目集成排期计划_20260811.md §8.3 (W9-B Sprint)
  - cairn/github-trending-wave9-20260819.md (待创建)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    from utils.logging_manager import get_logger

    logger = get_logger("factor_memory")
except ImportError:
    logger = logging.getLogger("factor_memory")


# ============================================================
# 数据结构
# ============================================================


@dataclass
class FactorExperiment:
    """因子实验记录

    Attributes:
        experiment_id: 实验 ID (自动生成)
        factor_name: 因子名 (如 EP/BP/ROE_TTM/GTJA_001)
        factor_category: 因子类别 (Value/Growth/Quality/Momentum/...)
        params: 实验参数 (回测区间/调仓频率/分组数/...)
        metrics: 评估指标 (IC/ICIR/Sharpe/MaxDD/turnover/...)
        conclusion: 结论 (effective/ineffective/overfit/lookahead/...)
        evidence: 证据 (日志指针/图表路径/单元测试结果)
        created_at: 创建时间戳
        tags: 标签 (如 "wave5_gnn", "sprint1", "shadow")
    """

    experiment_id: str
    factor_name: str
    factor_category: str
    params: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    conclusion: str = ""
    evidence: dict[str, str] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    tags: list[str] = field(default_factory=list)


@dataclass
class StrategyIteration:
    """策略迭代记录"""

    iteration_id: str
    strategy_name: str
    change_summary: str  # 本次改动摘要
    backtest_summary: dict[str, float] = field(default_factory=dict)
    decision: str = ""  # adopt/reject/iterate/shadow
    rationale: str = ""
    created_at: float = field(default_factory=time.time)


# ============================================================
# 因子记忆库
# ============================================================


class FactorMemory:
    """因子记忆库 (OpenViking + ai-memory 风格)

    Usage:
        mem = FactorMemory()
        mem.record_experiment(FactorExperiment(
            experiment_id="exp_001",
            factor_name="EP",
            factor_category="Value",
            metrics={"IC_20d": 0.05, "ICIR": 1.2},
            conclusion="effective",
        ))
        history = mem.query_factor("EP")
        print(history[0].conclusion)
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        if db_path is None:
            base = Path(__file__).resolve().parent.parent.parent
            db_path = str(base / "data" / "factor_memory.db")
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS factor_experiments (
                    experiment_id TEXT PRIMARY KEY,
                    factor_name TEXT,
                    factor_category TEXT,
                    params TEXT,
                    metrics TEXT,
                    conclusion TEXT,
                    evidence TEXT,
                    created_at REAL,
                    tags TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_factor_name ON factor_experiments(factor_name);
                CREATE INDEX IF NOT EXISTS idx_conclusion ON factor_experiments(conclusion);

                CREATE TABLE IF NOT EXISTS strategy_iterations (
                    iteration_id TEXT PRIMARY KEY,
                    strategy_name TEXT,
                    change_summary TEXT,
                    backtest_summary TEXT,
                    decision TEXT,
                    rationale TEXT,
                    created_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_strategy ON strategy_iterations(strategy_name);
            """)

    def record_experiment(self, exp: FactorExperiment) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO factor_experiments VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    exp.experiment_id,
                    exp.factor_name,
                    exp.factor_category,
                    json.dumps(exp.params, ensure_ascii=False),
                    json.dumps(exp.metrics, ensure_ascii=False),
                    exp.conclusion,
                    json.dumps(exp.evidence, ensure_ascii=False),
                    exp.created_at,
                    json.dumps(exp.tags, ensure_ascii=False),
                ),
            )
        logger.info(
            "record exp: %s (%s) -> %s",
            exp.experiment_id,
            exp.factor_name,
            exp.conclusion,
        )

    def record_iteration(self, it: StrategyIteration) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO strategy_iterations VALUES (?,?,?,?,?,?,?)",
                (
                    it.iteration_id,
                    it.strategy_name,
                    it.change_summary,
                    json.dumps(it.backtest_summary, ensure_ascii=False),
                    it.decision,
                    it.rationale,
                    it.created_at,
                ),
            )
        logger.info(
            "record iter: %s (%s) -> %s", it.iteration_id, it.strategy_name, it.decision
        )

    def query_factor(
        self,
        factor_name: str,
        conclusion: Optional[str] = None,
        limit: int = 50,
    ) -> list[FactorExperiment]:
        """查询因子历史实验"""
        sql = "SELECT * FROM factor_experiments WHERE factor_name = ?"
        args: list[Any] = [factor_name]
        if conclusion:
            sql += " AND conclusion = ?"
            args.append(conclusion)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(sql, args).fetchall()
        return [self._row_to_exp(r) for r in rows]

    def query_strategy(
        self, strategy_name: str, limit: int = 50
    ) -> list[StrategyIteration]:
        sql = "SELECT * FROM strategy_iterations WHERE strategy_name = ? ORDER BY created_at DESC LIMIT ?"
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(sql, [strategy_name, limit]).fetchall()
        return [self._row_to_iter(r) for r in rows]

    def has_conclusion(
        self,
        factor_name: str,
        conclusion: str,
        params: Optional[dict[str, Any]] = None,
    ) -> bool:
        """是否已有相同结论的实验 (避免重复回测)"""
        existing = self.query_factor(factor_name, conclusion=conclusion)
        if not existing:
            return False
        if params is None:
            return True
        return any(e.params == params for e in existing)

    def _row_to_exp(self, row: tuple) -> FactorExperiment:
        return FactorExperiment(
            experiment_id=row[0],
            factor_name=row[1],
            factor_category=row[2],
            params=json.loads(row[3]) if row[3] else {},
            metrics=json.loads(row[4]) if row[4] else {},
            conclusion=row[5],
            evidence=json.loads(row[6]) if row[6] else {},
            created_at=row[7],
            tags=json.loads(row[8]) if row[8] else [],
        )

    def _row_to_iter(self, row: tuple) -> StrategyIteration:
        return StrategyIteration(
            iteration_id=row[0],
            strategy_name=row[1],
            change_summary=row[2],
            backtest_summary=json.loads(row[3]) if row[3] else {},
            decision=row[4],
            rationale=row[5],
            created_at=row[6],
        )
