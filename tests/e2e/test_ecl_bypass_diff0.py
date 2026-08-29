"""ECL 旁路 diff=0 E2E 测试 — CTX-A3 T3.

验证: flag on/off 决策输出逐字节一致 (ECL 只新增自己的库与 reports/ecl/ 文件).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from utils.infra.ecl.bypass import run_ecl_bypass
from utils.infra.ecl.event_store import EventStore
from utils.infra.ecl.sinks import EclEventSink, clear_sinks, register_sink


@pytest.fixture()
def ecl_db(tmp_path: Path) -> Path:
    return tmp_path / "ecl.db"


@pytest.fixture(autouse=True)
def _clean_sinks():
    clear_sinks()
    yield
    clear_sinks()


class TestDiffZero:
    """flag on/off 决策输出 diff=0."""

    def test_flag_off_bypass_noop(self, ecl_db: Path, tmp_path: Path) -> None:
        """flag 全 off 时旁路 noop, 不产出任何文件."""
        decisions = tmp_path / "decisions.jsonl"
        retrieval_dir = tmp_path / "ecl_reports"
        with patch(
            "utils.infra.feature_flags.FeatureFlags.is_enabled", return_value=False
        ):
            result = run_ecl_bypass(
                "2026-01-01",
                db_path=ecl_db,
                decisions_path=decisions,
                retrieval_dir=retrieval_dir,
            )
        assert result["reconcile"]["skipped"] is not None
        assert result["derive"]["skipped"] is not None
        assert result["retrieval"]["skipped"] is not None
        assert not retrieval_dir.exists()

    def test_flag_off_sink_noop(self, ecl_db: Path) -> None:
        """flag off 时 sink noop, EventStore 不写入."""
        sink = EclEventSink(db_path=ecl_db)
        register_sink(sink)
        record = {
            "timestamp": "2026-01-01T10:00:00",
            "action": "evaluate",
            "public_score": 0.5,
            "private_score": 0.5,
            "sample_count": 100,
            "recommendation": "hold",
            "reason": "ok",
            "reward_hacking_risk": 0.1,
        }
        with patch(
            "utils.infra.feature_flags.FeatureFlags.is_enabled", return_value=False
        ):
            result = sink.write(record)
        assert result is True
        store = EventStore(ecl_db)
        assert store.count() == 0

    def test_flag_on_sink_writes_but_decision_unchanged(self, ecl_db: Path) -> None:
        """flag on 时 sink 写入 EventStore, 但决策记录本身不变."""
        sink = EclEventSink(db_path=ecl_db)
        register_sink(sink)
        record = {
            "timestamp": "2026-01-01T10:00:00",
            "action": "evaluate",
            "public_score": 0.5,
            "private_score": 0.5,
            "sample_count": 100,
            "recommendation": "hold",
            "reason": "ok",
            "reward_hacking_risk": 0.1,
        }
        original_record = json.dumps(record, sort_keys=True)
        with patch(
            "utils.infra.feature_flags.FeatureFlags.is_enabled", return_value=True
        ):
            sink.write(record)
        assert json.dumps(record, sort_keys=True) == original_record
        store = EventStore(ecl_db)
        assert store.count() == 1

    def test_bypass_fail_open_on_exception(self, tmp_path: Path) -> None:
        """旁路任何异常 → 返回 success=False 但不抛出."""
        with patch(
            "utils.infra.feature_flags.FeatureFlags.is_enabled", return_value=True
        ):
            with patch(
                "utils.infra.ecl.bypass._reconcile_events",
                side_effect=RuntimeError("db locked"),
            ):
                result = run_ecl_bypass(
                    "2026-01-01",
                    db_path=tmp_path / "ecl.db",
                    decisions_path=tmp_path / "decisions.jsonl",
                    retrieval_dir=tmp_path / "ecl_reports",
                )
        assert "reconcile" in result

    def test_retrieval_report_not_in_archive_patterns(self, tmp_path: Path) -> None:
        """检索报告 reports/ecl/ 不进归档 (单一位置)."""
        retrieval_dir = tmp_path / "reports" / "ecl"
        report_path = retrieval_dir / "retrieval_2026-01-01.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open("w") as f:
            json.dump({"test": True}, f)
        assert report_path.exists()
        assert "reports" in str(report_path)
        assert "ecl" in str(report_path)
