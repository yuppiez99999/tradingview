"""生产运营中心数据加载层单测 (Production Edition T5, 2026-09-02)."""
from __future__ import annotations

import json
from pathlib import Path

from ui.components.health_center import (
    derive_status_color,
    load_anomaly_timeline,
    load_health_history,
    load_latest_health,
    load_shadow_progress,
)


def _write_health(root: Path, date: str, total: float, status: str) -> None:
    d = root / "reports" / "health_score"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"health_score_{date}.json").write_text(
        json.dumps({
            "date": date,
            "generated_at": f"{date}T17:05:00",
            "total_score": total,
            "status": status,
            "dimensions": {
                "model": {"score": 90.0, "weight": 0.25, "degraded": False, "detail": {}},
                "data": {"score": 80.0, "weight": 0.2, "degraded": False, "detail": {}},
                "trading": {"score": 70.0, "weight": 0.15, "degraded": False, "detail": {}},
                "risk": {"score": 100.0, "weight": 0.2, "degraded": False, "detail": {}},
                "capital": {"score": 100.0, "weight": 0.2, "degraded": False, "detail": {}},
            },
            "degraded_dimensions": [],
        }, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_shadow_state(root: Path, start_date: str, days: int) -> None:
    d = root / "output" / "shadow_account"
    d.mkdir(parents=True, exist_ok=True)
    (d / "s12_shadow_state.json").write_text(
        json.dumps({
            "account_id": "S12_SHADOW_P3",
            "strategy_id": "S12_DEFENSIVE_RP",
            "initial_capital": 2000000.0,
            "start_date": start_date,
            "trading_day_count": days,
        }, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_degradation(root: Path, lines: list[dict]) -> None:
    d = root / "reports"
    d.mkdir(parents=True, exist_ok=True)
    with (d / "degradation_log.jsonl").open("w", encoding="utf-8") as f:
        for rec in lines:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


class TestLoadHealthHistory:
    def test_sorted_ascending_by_date(self, tmp_path):
        _write_health(tmp_path, "2026-09-01", 73.5, "YELLOW")
        _write_health(tmp_path, "2026-08-31", 90.0, "GREEN")
        _write_health(tmp_path, "2026-09-02", 68.0, "RED")
        hist = load_health_history(tmp_path)
        assert [h["date"] for h in hist] == ["2026-08-31", "2026-09-01", "2026-09-02"]

    def test_days_limit(self, tmp_path):
        for i in range(1, 41):
            _write_health(tmp_path, f"2026-07-{i:02d}" if i <= 31 else f"2026-08-{i - 31:02d}", 80.0, "GREEN")
        hist = load_health_history(tmp_path, days=30)
        assert len(hist) == 30
        assert hist[-1]["date"] == "2026-08-09"

    def test_empty_dir(self, tmp_path):
        assert load_health_history(tmp_path) == []
        assert load_latest_health(tmp_path) is None

    def test_corrupt_json_skipped(self, tmp_path):
        _write_health(tmp_path, "2026-09-01", 90.0, "GREEN")
        d = tmp_path / "reports" / "health_score"
        (d / "health_score_2026-09-02.json").write_text("{not json", encoding="utf-8")
        hist = load_health_history(tmp_path)
        assert [h["date"] for h in hist] == ["2026-09-01"]

    def test_load_latest(self, tmp_path):
        _write_health(tmp_path, "2026-09-01", 73.5, "YELLOW")
        _write_health(tmp_path, "2026-09-02", 96.0, "GREEN")
        latest = load_latest_health(tmp_path)
        assert latest is not None
        assert latest["date"] == "2026-09-02"
        assert latest["total_score"] == 96.0


class TestAnomalyTimeline:
    def test_recent_entries_only(self, tmp_path):
        _write_degradation(tmp_path, [
            {"ts": "2026-09-02T16:31:00", "scope": "daily_trade_executor", "key": "k1"},
            {"ts": "2026-08-01T09:00:00", "scope": "old_scope", "key": "k0"},
            {"ts": "2026-09-02T17:00:00", "scope": "pretrade_guard", "key": "k2"},
        ])
        tl = load_anomaly_timeline(tmp_path, days=7, today="2026-09-02")
        assert len(tl) == 2
        assert all(e["ts"].startswith("2026-09-02") for e in tl)

    def test_malformed_lines_skipped(self, tmp_path):
        d = tmp_path / "reports"
        d.mkdir(parents=True)
        (d / "degradation_log.jsonl").write_text(
            '{"ts": "2026-09-02T10:00:00", "scope": "a"}\nnot-json\n\n', encoding="utf-8"
        )
        tl = load_anomaly_timeline(tmp_path, days=7, today="2026-09-02")
        assert len(tl) == 1
        assert tl[0]["scope"] == "a"

    def test_missing_file(self, tmp_path):
        assert load_anomaly_timeline(tmp_path, days=7, today="2026-09-02") == []


class TestShadowProgress:
    def test_phase3_shadow(self, tmp_path):
        _write_shadow_state(tmp_path, "2026-09-02", 0)
        p = load_shadow_progress(tmp_path)
        assert p["current_stage"] == "Phase 3 影子验证 (200 万虚拟)"
        assert p["stages"][2]["status"] == "进行中"
        assert p["stages"][0]["status"] == "已完成"
        assert p["trading_day_count"] == 0

    def test_missing_state_all_pending(self, tmp_path):
        p = load_shadow_progress(tmp_path)
        assert p["current_stage"] == "未启动"
        assert all(s["status"] == "未开始" for s in p["stages"])
        assert p["trading_day_count"] == 0


class TestStatusColor:
    def test_mapping(self):
        assert derive_status_color("GREEN") == "green"
        assert derive_status_color("YELLOW") == "orange"
        assert derive_status_color("RED") == "red"
        assert derive_status_color("unknown") == "gray"
