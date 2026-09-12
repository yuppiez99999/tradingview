"""test_factor_criteria_switch_unit.py — 口径切换执行器单元测试 (Issue #13)

覆盖 fail-closed 判据 (任一不满足即拒绝执行):
    1. 名单文件缺失 / 不可解析
    2. shadow.available=false (空名单最危险, 不得被读作"无需淘汰")
    3. A/B 结构缺字段 / 计数不自洽
    4. --apply 必须带 --confirmed-by
    5. 切换幂等 (已是 false 不重复改)

以及: 干跑不改文件 / --apply 正确翻转 shadow_legacy / 记录含名单规模与确认人。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.factor_criteria_switch import (  # noqa: E402
    _apply_switch,
    load_and_validate,
    main,
)


def _valid_shadow() -> dict:
    return {
        "available": True,
        "source_json": "reports/operations/factor_shadow/x.json",
        "legacy_effective_new_ineffective": [
            {"factor_name": "A1", "failed_on": "|IC|"},
            {"factor_name": "A2", "failed_on": "|IR|"},
        ],
        "new_undecidable_but_legacy_decidable": [{"factor_name": "B1", "n_samples": 30}],
        "undecidable_in_both": ["C1"],
        "total_candidates": 3,
    }


def _write(tmp_path: Path, payload: dict, name: str = "shadow.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


class TestFailClosed:
    @pytest.mark.unit
    def test_missing_file_rejected(self, tmp_path):
        shadow, problems = load_and_validate(tmp_path / "nope.json")
        assert shadow is None
        assert "不存在" in problems[0]

    @pytest.mark.unit
    def test_unparsable_file_rejected(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        shadow, problems = load_and_validate(p)
        assert shadow is None
        assert "不可解析" in problems[0]

    @pytest.mark.unit
    def test_unavailable_list_rejected_not_read_as_empty(self, tmp_path):
        """核心: available=false 必须拒绝, 绝不当成"空名单 = 无需淘汰"。"""
        payload = {"shadow": {"available": False, "reason": "缺 validation_shadow 字段"}}
        shadow, problems = load_and_validate(_write(tmp_path, payload))
        assert shadow is None
        assert "available=false" in problems[0]

    @pytest.mark.unit
    def test_missing_ab_keys_rejected(self, tmp_path):
        payload = {"shadow": {"available": True, "legacy_effective_new_ineffective": []}}
        shadow, problems = load_and_validate(_write(tmp_path, payload))
        assert shadow is None
        assert "名单结构缺" in problems[0]

    @pytest.mark.unit
    def test_inconsistent_total_rejected(self, tmp_path):
        payload = {"shadow": {**_valid_shadow(), "total_candidates": 99}}
        shadow, problems = load_and_validate(_write(tmp_path, payload))
        assert shadow is None
        assert "total_candidates" in problems[0]

    @pytest.mark.unit
    def test_valid_list_accepted(self, tmp_path):
        shadow, problems = load_and_validate(_write(tmp_path, {"shadow": _valid_shadow()}))
        assert problems == []
        assert shadow is not None
        assert shadow["total_candidates"] == 3

    @pytest.mark.unit
    def test_apply_requires_confirmed_by(self, tmp_path):
        p = _write(tmp_path, {"shadow": _valid_shadow()})
        argv = ["prog", "--list", str(p), "--apply"]
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sys, "argv", argv)
            assert main() == 1


class TestApplySwitch:
    @pytest.mark.unit
    def test_flips_shadow_legacy(self):
        text = "factor_validation:\n  min_samples: 60\n  shadow_legacy: true\n"
        updated, changes = _apply_switch(text)
        assert "shadow_legacy: false" in updated
        assert "true → false" in changes[0]

    @pytest.mark.unit
    def test_idempotent_when_already_false(self):
        text = "factor_validation:\n  shadow_legacy: false\n"
        updated, changes = _apply_switch(text)
        assert updated == text
        assert "幂等" in changes[0]

    @pytest.mark.unit
    def test_only_touches_factor_section(self):
        text = (
            "stop_loss:\n  shadow_legacy: true\n"
            "factor_validation:\n  shadow_legacy: true\n"
            "other:\n  x: 1\n"
        )
        updated, _ = _apply_switch(text)
        lines = updated.splitlines()
        assert lines[1].strip() == "shadow_legacy: true"  # stop_loss 段不动
        assert lines[3].strip() == "shadow_legacy: false"

    @pytest.mark.unit
    def test_missing_line_reports_no_change(self):
        text = "factor_validation:\n  min_samples: 60\n"
        updated, changes = _apply_switch(text)
        assert updated == text
        assert "未在文件中找到" in changes[0]

    @pytest.mark.unit
    def test_dry_run_does_not_modify_config(self, tmp_path):
        """干跑必须零改动, 且写出记录含名单规模。"""
        p = _write(tmp_path, {"shadow": _valid_shadow()})
        record_dir = tmp_path / "records"
        before = (_PROJECT_ROOT / "config" / "risk_thresholds.yaml").read_text(encoding="utf-8")
        argv = ["prog", "--list", str(p), "--record-dir", str(record_dir)]
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sys, "argv", argv)
            assert main() == 0
        after = (_PROJECT_ROOT / "config" / "risk_thresholds.yaml").read_text(encoding="utf-8")
        assert before == after
        records = list(record_dir.glob("factor_criteria_switch_record_*.md"))
        assert len(records) == 1
        body = records[0].read_text(encoding="utf-8")
        assert "A 类" in body and "B 类" in body
        assert "干跑" in body

    @pytest.mark.unit
    def test_fail_closed_returns_nonzero_and_writes_nothing(self, tmp_path):
        p = _write(tmp_path, {"shadow": {"available": False, "reason": "x"}})
        record_dir = tmp_path / "records"
        argv = ["prog", "--list", str(p), "--record-dir", str(record_dir)]
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sys, "argv", argv)
            assert main() == 1
        assert not record_dir.exists() or not list(record_dir.glob("*.md"))
