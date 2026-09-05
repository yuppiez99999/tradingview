"""统一评估周合并器 单元测试 (C4, 2026-09-05).

覆盖场景:
    1. 五源收集函数各自 verdict (PASS/FAIL/PENDING/MANUAL)
    2. qlib 源固定 FAIL (缺口分析口径, 不随数据积累翻转)
    3. S6 骨架比例暴露 (qlib 同型风险)
    4. MVSK 窗口满 READY / 未满 PENDING / fail_fast
    5. p33 产物存在与缺失两条路径
    6. 报告构建 (汇总行 + 不改变 RELEASE GATE 声明)
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))


def _load_mod():
    spec = importlib.util.spec_from_file_location(
        "generate_unified_evaluation",
        _PROJECT_ROOT / "scripts" / "generate_unified_evaluation.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["generate_unified_evaluation"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ev():
    return _load_mod()


class TestCollectP33:
    def test_latest_json_pass(self, ev, tmp_path, monkeypatch):
        p33 = tmp_path / "p33_shadow_evaluation_x.json"
        p33.write_text(
            json.dumps(
                {
                    "trading_days": 30,
                    "forced": False,
                    "all_pass": True,
                    "checks": {"return_positive": {"pass": True}},
                    "consistency_checks": {"nav_reconciliation": {"pass": True}},
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(ev, "P33_DIR", tmp_path)
        out = ev.collect_p33()
        assert out["verdict"] == "PASS"
        assert "p33_shadow_evaluation_x.json" in out["source"]

    def test_latest_json_fail(self, ev, tmp_path, monkeypatch):
        p33 = tmp_path / "p33_shadow_evaluation_x.json"
        p33.write_text(
            json.dumps(
                {"trading_days": 30, "forced": False, "all_pass": False, "checks": {}, "consistency_checks": {}}
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(ev, "P33_DIR", tmp_path)
        out = ev.collect_p33()
        assert out["verdict"] == "FAIL"

    def test_no_artifact_pending(self, ev, tmp_path, monkeypatch):
        """无产物 → PENDING 不臆造 PASS."""
        monkeypatch.setattr(ev, "P33_DIR", tmp_path)
        out = ev.collect_p33()
        assert out["verdict"] == "PENDING"

    def test_corrupted_json_falls_to_state(self, ev, tmp_path, monkeypatch):
        p33 = tmp_path / "p33_shadow_evaluation_bad.json"
        p33.write_text("{broken", encoding="utf-8")
        monkeypatch.setattr(ev, "P33_DIR", tmp_path)
        out = ev.collect_p33()
        assert out["verdict"] == "PENDING"  # fail-open 降级, 不崩

    def test_forced_preview_not_fail(self, ev, tmp_path, monkeypatch):
        """forced 预演产物 (样本不足) → PENDING 不构成 FAIL.

        实跑发现: 09-02 forced 预演 (0 交易日) 曾被判 FAIL 误导评估周.
        """
        p33 = tmp_path / "p33_shadow_evaluation_20260902.json"
        p33.write_text(
            json.dumps({"trading_days": 0, "forced": True, "all_pass": False, "checks": {}, "consistency_checks": {}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(ev, "P33_DIR", tmp_path)
        out = ev.collect_p33()
        assert out["verdict"] == "PENDING"
        assert "forced 预演" in out["detail"]


class TestCollectMvsk:
    def test_window_full_ready(self, ev, tmp_path, monkeypatch):
        (tmp_path / "shadow_30day_status.json").write_text(
            json.dumps({"days_elapsed": 30, "start_date": "2026-09-13", "fail_fast_triggered": False}),
            encoding="utf-8",
        )
        monkeypatch.setattr(ev, "SHADOW_DIR", tmp_path)
        out = ev.collect_mvsk()
        assert out["verdict"] == "READY"

    def test_window_partial_pending(self, ev, tmp_path, monkeypatch):
        (tmp_path / "shadow_30day_status.json").write_text(
            json.dumps({"days_elapsed": 5, "start_date": "", "fail_fast_triggered": False}),
            encoding="utf-8",
        )
        monkeypatch.setattr(ev, "SHADOW_DIR", tmp_path)
        out = ev.collect_mvsk()
        assert out["verdict"] == "PENDING"
        assert "未启动" in out["detail"]

    def test_fail_fast_stays_pending(self, ev, tmp_path, monkeypatch):
        (tmp_path / "shadow_30day_status.json").write_text(
            json.dumps({"days_elapsed": 30, "start_date": "2026-09-13", "fail_fast_triggered": True}),
            encoding="utf-8",
        )
        monkeypatch.setattr(ev, "SHADOW_DIR", tmp_path)
        out = ev.collect_mvsk()
        assert out["verdict"] == "PENDING"


class TestCollectQlib:
    def test_always_fail_by_design(self, ev, tmp_path, monkeypatch):
        """缺口分析口径: FAIL 不随数据积累翻转 (接线错配+信号静态)."""
        (tmp_path / "qlib_lgb_v2_daily.jsonl").write_text(
            json.dumps({"date": "2026-09-04"}) + "\n" * 7, encoding="utf-8"
        )
        monkeypatch.setattr(ev, "SHADOW_DIR", tmp_path)
        out = ev.collect_qlib()
        assert out["verdict"] == "FAIL"
        assert "qlib_w729_gap_analysis" in out["source"]


class TestCollectS6:
    def test_all_skeleton_risk_exposed(self, ev, tmp_path, monkeypatch):
        """全骨架 → PENDING + 风险注记 (qlib 同型风险)."""
        s6 = tmp_path / "s6_paper_trading.jsonl"
        s6.write_text(
            json.dumps({"date": "2026-09-04", "status": "skeleton"}) + "\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(ev, "S6_JSONL", s6)
        monkeypatch.setattr(ev, "PROJECT_ROOT", tmp_path)
        out = ev.collect_s6()
        assert out["verdict"] == "PENDING"
        assert "骨架" in out["detail"]

    def test_real_records_counted(self, ev, tmp_path, monkeypatch):
        s6 = tmp_path / "s6_paper_trading.jsonl"
        s6.write_text(
            json.dumps({"date": "2026-09-14", "status": "skeleton"})
            + "\n"
            + json.dumps({"date": "2026-09-15", "status": "real"})
            + "\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(ev, "S6_JSONL", s6)
        monkeypatch.setattr(ev, "PROJECT_ROOT", tmp_path)
        out = ev.collect_s6()
        assert "真实 1 / 骨架 1" in out["detail"]

    def test_missing_file_pending(self, ev, tmp_path, monkeypatch):
        monkeypatch.setattr(ev, "S6_JSONL", tmp_path / "nonexistent.jsonl")
        out = ev.collect_s6()
        assert out["verdict"] == "PENDING"


class TestCollectSprint2:
    def test_manual_verdict(self, ev):
        out = ev.collect_sprint2()
        assert out["verdict"] == "MANUAL"


class TestBuildReport:
    def test_summary_lines(self, ev):
        sources = {
            "A": {"verdict": "PASS", "detail": "x", "source": ""},
            "B": {"verdict": "FAIL", "detail": "y", "source": ""},
            "C": {"verdict": "PENDING", "detail": "z", "source": ""},
        }
        report = ev.build_report(sources, "2026-10-13")
        assert "PASS 1 / FAIL 1" in report
        assert "不改变任何 RELEASE GATE" in report
        assert "2026-10-13" in report
