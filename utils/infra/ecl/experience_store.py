"""ExperienceStore — 经验库 + 嵌入 + 相似检索 (CTX-A2).

L2 层: 场景指纹归一化 + 经验条目沉淀 + outcome 延迟回填 + Top-K 相似检索.

设计 (spec_CTX-A2.md):
    - R1: scenario_fp = regime_label|vix_bucket|rv_bucket|dd_bucket
    - R2: 仅从 regime_shift_suggestion + strategy_eval 生成经验
    - R3: outcome 双 horizon (T+5/T+20), 延迟回填, 不可回填保持 NULL
    - R5: query_similar <500ms
    - R6: 检索结果仅写 reports/ecl/retrieval_{date}.json, 不进决策链
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from utils.infra.ecl.embeddings import cosine_sim, detect_embedding_backend, embed
from utils.infra.ecl.event_store import EventStore

logger = logging.getLogger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ecl_experiences (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id         INTEGER NOT NULL UNIQUE REFERENCES ecl_events(id),
    scenario_fp      TEXT,
    decision_summary TEXT NOT NULL,
    lesson_tags      TEXT,
    outcome_5d       REAL,
    outcome_20d      REAL,
    embedding        BLOB,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ecl_exp_fp ON ecl_experiences(scenario_fp);
"""

VIX_BUCKETS = [(20.0, "low"), (30.0, "normal")]
RV_BUCKETS = [(0.15, "low"), (0.30, "normal")]
DD_BUCKETS = [(0.05, "shallow"), (0.10, "mid")]


def _bucket(
    value: float | None, buckets: list[tuple[float, str]], high_label: str
) -> str | None:
    if value is None:
        return None
    for threshold, label in buckets:
        if value < threshold:
            return label
    return high_label


def compute_scenario_fp(
    regime_label: str | None,
    vix: float | None,
    realized_vol: float | None,
    current_drawdown: float | None,
) -> str | None:
    """场景指纹 = regime_label|vix_bucket|rv_bucket|dd_bucket."""
    vb = _bucket(vix, VIX_BUCKETS, "high")
    rvb = _bucket(realized_vol, RV_BUCKETS, "high")
    ddb = _bucket(current_drawdown, DD_BUCKETS, "deep")
    if regime_label is None or vb is None or rvb is None or ddb is None:
        return None
    return f"{regime_label}|vix_{vb}|rv_{rvb}|dd_{ddb}"


def _build_decision_summary(event_type: str, payload: dict[str, Any]) -> str:
    if event_type == "regime_shift_suggestion":
        return (
            f"suggested_weights={payload.get('suggested_weights')}, "
            f"multipliers={payload.get('multipliers')}, "
            f"constraints={payload.get('constraints_applied')}"
        )
    return (
        f"recommendation={payload.get('recommendation')}, "
        f"public={payload.get('public_score')}, private={payload.get('private_score')}, "
        f"reason={payload.get('reason')}"
    )


def _compute_lesson_tags(event_type: str, payload: dict[str, Any]) -> list[str]:
    if event_type == "regime_shift_suggestion":
        return []
    tags: list[str] = []
    score = payload.get("public_score")
    if score is not None and score < 0:
        tags.append("score_negative")
    rh_risk = payload.get("reward_hacking_risk")
    if rh_risk is not None and rh_risk > 0.5:
        tags.append("reward_hacking_risk")
    return tags


def _extract_indicators(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if event_type == "regime_shift_suggestion":
        return payload.get("indicators", {}) or {}
    market_ctx = payload.get("market_context", {}) or {}
    return {
        "vix": market_ctx.get("vix"),
        "realized_vol": market_ctx.get("realized_vol"),
        "current_drawdown": market_ctx.get("current_drawdown"),
    }


def _regime_label(event_type: str, payload: dict[str, Any]) -> str | None:
    if event_type == "regime_shift_suggestion":
        return (payload.get("regime", {}) or {}).get("label")
    return (payload.get("market_context", {}) or {}).get("regime_label")


class ExperienceStore:
    """L2 经验库.

    Args:
        db_path: SQLite 数据库路径 (与 EventStore 共享或独立).
        event_store: 上游 EventStore (L1).
    """

    def __init__(self, db_path: Path | str | None, event_store: EventStore) -> None:
        if db_path is None:
            db_path = event_store.db_path
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.event_store = event_store
        self._init_schema()

    def _init_schema(self) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.executescript(_SCHEMA_SQL)
            conn.commit()

    def _now_iso(self) -> str:
        return datetime.now().isoformat(timespec="seconds")

    def derive_from_events(self, as_of: str) -> int:
        """当日(as_of)事件 → 经验条目.

        幂等: event_id 唯一索引, 可重跑.
        仅从 regime_shift_suggestion + strategy_eval 生成.

        Returns:
            新增条数.
        """
        events = self.event_store.replay()
        date_prefix = as_of[:10]
        eligible = [
            e
            for e in events
            if e.event_type in ("regime_shift_suggestion", "strategy_eval")
            and e.ts.startswith(date_prefix)
        ]
        if not eligible:
            return 0

        backend = detect_embedding_backend()
        inserted = 0
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            for ev in eligible:
                existing = conn.execute(
                    "SELECT 1 FROM ecl_experiences WHERE event_id=?", (ev.id,)
                ).fetchone()
                if existing:
                    continue

                indicators = _extract_indicators(ev.event_type, ev.payload)
                regime_label = _regime_label(ev.event_type, ev.payload)
                scenario_fp = compute_scenario_fp(
                    regime_label,
                    indicators.get("vix"),
                    indicators.get("realized_vol"),
                    indicators.get("current_drawdown"),
                )
                summary = _build_decision_summary(ev.event_type, ev.payload)
                tags = _compute_lesson_tags(ev.event_type, ev.payload)

                embedding_blob: bytes | None = None
                if backend != "fts5":
                    text = (
                        f"{regime_label or 'unknown'} regime, "
                        f"VIX {indicators.get('vix')}, "
                        f"realized vol {indicators.get('realized_vol')}, "
                        f"drawdown {indicators.get('current_drawdown')}. {summary}"
                    )
                    vec = embed(text)
                    if vec is not None:
                        embedding_blob = np.asarray(vec, dtype=np.float32).tobytes()

                conn.execute(
                    """
                    INSERT INTO ecl_experiences
                        (event_id, scenario_fp, decision_summary, lesson_tags, embedding, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ev.id,
                        scenario_fp,
                        summary,
                        json.dumps(tags, ensure_ascii=False),
                        embedding_blob,
                        self._now_iso(),
                    ),
                )
                inserted += 1
            conn.commit()
        return inserted

    def backfill_outcomes(
        self,
        returns_jsonl: Path | str,
        horizons: tuple[int, ...] = (5, 20),
    ) -> int:
        """延迟回填 outcome (T+5 / T+20 组合累计收益).

        仅回填 ts + N 交易日 <= 今日 且 outcome 为 NULL 的条目.
        不可回填的保持 NULL, 绝不前视.

        Returns:
            回填条数.
        """
        returns_path = Path(returns_jsonl)
        if not returns_path.exists():
            logger.warning("daily_returns.jsonl 不存在: %s", returns_path)
            return 0

        trade_dates: list[str] = []
        daily_returns: dict[str, float] = {}
        with returns_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                date = rec.get("date", "")
                ret = rec.get("daily_return")
                if date and ret is not None:
                    trade_dates.append(date)
                    daily_returns[date] = float(ret)

        if not trade_dates:
            return 0
        trade_dates_sorted = sorted(trade_dates)
        today = trade_dates_sorted[-1]

        filled = 0
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT e.id, e.event_id, ev.ts
                FROM ecl_experiences e
                JOIN ecl_events ev ON e.event_id = ev.id
                WHERE e.outcome_5d IS NULL OR e.outcome_20d IS NULL
                """).fetchall()

            for row in rows:
                event_ts = row["ts"]
                event_date = event_ts[:10]
                try:
                    idx = trade_dates_sorted.index(event_date)
                except ValueError:
                    continue

                updates: dict[str, float] = {}
                for horizon in horizons:
                    target_idx = idx + horizon
                    if target_idx >= len(trade_dates_sorted):
                        continue
                    if trade_dates_sorted[target_idx] > today:
                        continue
                    cum_ret = 1.0
                    for i in range(idx + 1, target_idx + 1):
                        d = trade_dates_sorted[i]
                        cum_ret *= 1.0 + daily_returns.get(d, 0.0)
                    col = f"outcome_{horizon}d"
                    updates[col] = cum_ret - 1.0

                if updates:
                    set_clause = ", ".join(f"{k}=?" for k in updates)
                    params = list(updates.values()) + [row["id"]]
                    conn.execute(
                        # 列名由内部 horizon(int) 生成, 值全参数化
                        f"UPDATE ecl_experiences SET {set_clause} WHERE id=?",  # nosec B608
                        params,
                    )
                    filled += 1
            conn.commit()
        return filled

    def query_similar(
        self, context: dict[str, Any], k: int = 5
    ) -> list[dict[str, Any]]:
        """Top-K 相似场景检索.

        一级过滤: scenario_fp 完全匹配 (SQL).
        二级排序: 嵌入余弦 (st/hash) 或 FTS5 rank.

        Args:
            context: {vix, realized_vol, current_drawdown, regime_label}.
            k: Top-K.

        Returns:
            [{scenario_fp, ts, decision_summary, outcome_5d, outcome_20d, lesson_tags}].
        """
        scenario_fp = compute_scenario_fp(
            context.get("regime_label"),
            context.get("vix"),
            context.get("realized_vol"),
            context.get("current_drawdown"),
        )
        if scenario_fp is None:
            return []

        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            rows = conn.execute(
                """
                SELECT e.*, ev.ts FROM ecl_experiences e
                JOIN ecl_events ev ON e.event_id = ev.id
                WHERE e.scenario_fp = ?
                ORDER BY ev.ts DESC
                """,
                (scenario_fp,),
            ).fetchall()

        if not rows:
            return []

        results: list[dict[str, Any]] = []
        query_vec = None
        backend = detect_embedding_backend()
        if backend != "fts5":
            query_text = (
                f"{context.get('regime_label', 'unknown')} regime, "
                f"VIX {context.get('vix')}, "
                f"realized vol {context.get('realized_vol')}, "
                f"drawdown {context.get('current_drawdown')}."
            )
            query_vec = embed(query_text)

        for row in rows:
            entry: dict[str, Any] = {
                "scenario_fp": row["scenario_fp"],
                "ts": row["ts"],
                "decision_summary": row["decision_summary"],
                "outcome_5d": row["outcome_5d"],
                "outcome_20d": row["outcome_20d"],
                "lesson_tags": json.loads(row["lesson_tags"] or "[]"),
            }
            if query_vec is not None and row["embedding"] is not None:
                cand_vec = np.frombuffer(row["embedding"], dtype=np.float32).tolist()
                entry["_sim"] = cosine_sim(query_vec, cand_vec)
            else:
                entry["_sim"] = 0.0
            results.append(entry)

        results.sort(key=lambda x: x.pop("_sim", 0.0), reverse=True)
        return results[:k]

    def stats(self) -> dict[str, Any]:
        """统计: 条目数/outcome 回填率/embedding 档位/scenario_fp 分布."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            total = conn.execute("SELECT COUNT(*) FROM ecl_experiences").fetchone()[0]
            filled_5d = conn.execute(
                "SELECT COUNT(*) FROM ecl_experiences WHERE outcome_5d IS NOT NULL"
            ).fetchone()[0]
            filled_20d = conn.execute(
                "SELECT COUNT(*) FROM ecl_experiences WHERE outcome_20d IS NOT NULL"
            ).fetchone()[0]
            fp_dist = dict(
                conn.execute(
                    "SELECT scenario_fp, COUNT(*) FROM ecl_experiences GROUP BY scenario_fp"
                ).fetchall()
            )
        return {
            "total": int(total),
            "outcome_5d_filled": int(filled_5d),
            "outcome_20d_filled": int(filled_20d),
            "embedding_backend": detect_embedding_backend(),
            "scenario_fp_distribution": {str(k): int(v) for k, v in fp_dist.items()},
        }

    def write_retrieval_report(
        self, context: dict[str, Any], results: list[dict], output_path: Path | str
    ) -> None:
        """检索报告写到 reports/ecl/retrieval_{date}.json (不进归档)."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "query_context": context,
            "result_count": len(results),
            "results": results,
            "generated_at": self._now_iso(),
        }
        with path.open("w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2, default=str)
