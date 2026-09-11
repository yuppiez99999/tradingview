"""test_factor_criteria_shadow_report_unit.py — 口径影子报告脚本单元测试

覆盖:
    - 口径快照确实来自 config/risk_thresholds.yaml 唯一事实源
    - JSON 产物缺 validation_shadow 字段时**不静默出空名单**, 而是显式报不可用
    - Markdown 渲染必须同时呈现 A 类与 B 类两个维度
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.factor_criteria_shadow_report import (  # noqa: E402
    _build_from_json,
    _criteria_snapshot,
    _render_markdown,
)


class TestCriteriaSnapshot:
    @pytest.mark.unit
    def test_reads_from_single_source_of_truth(self):
        """口径必须来自 config/risk_thresholds.yaml, 而非散落常量。"""
        from utils.risk_thresholds import resolve_config

        snap = _criteria_snapshot()
        cfg, _ = resolve_config("factor_validation")
        assert snap["new"]["min_samples"] == cfg["min_samples"]
        assert snap["new"]["score_mode"] == cfg["score_mode"]
        assert "risk_thresholds.yaml" in snap["config_source"]

    @pytest.mark.unit
    def test_legacy_criteria_are_shadow_only(self):
        """旧口径常量仍保留且与影子字段一致 (不参与判定)。"""
        snap = _criteria_snapshot()
        assert snap["legacy"]["min_samples"] == 5
        assert snap["legacy"]["ic_effective_threshold"] == 0.02
        assert snap["legacy"]["ir_effective_threshold"] == 0.2


class TestBuildFromJson:
    @pytest.mark.unit
    def test_missing_shadow_block_is_explicit_not_empty_list(self, tmp_path):
        """旧产物不含影子块时必须显式标记不可用 —— 否则会误当成"名单为空"。"""
        p = tmp_path / "old.json"
        p.write_text(json.dumps({"effective_factors": []}), encoding="utf-8")

        payload = _build_from_json(p)
        assert payload["available"] is False
        assert "validation_shadow" in payload["reason"]
        assert "重跑" in payload["reason"]

    @pytest.mark.unit
    def test_shadow_block_passthrough(self, tmp_path):
        p = tmp_path / "new.json"
        p.write_text(
            json.dumps(
                {
                    "validation_shadow": {
                        "total_candidates": 0,
                        "legacy_effective_new_ineffective": [],
                        "new_undecidable_but_legacy_decidable": [],
                        "undecidable_in_both": [],
                    }
                }
            ),
            encoding="utf-8",
        )
        payload = _build_from_json(p)
        assert payload["available"] is True
        assert payload["total_candidates"] == 0


class TestRenderMarkdown:
    @staticmethod
    def _criteria():
        return _criteria_snapshot()

    @pytest.mark.unit
    def test_unavailable_payload_renders_blocker(self):
        md = _render_markdown(
            {"available": False, "reason": "该 JSON 不含 validation_shadow"},
            self._criteria(),
            "2026-09-11",
        )
        assert "无法生成名单" in md
        assert "该 JSON 不含 validation_shadow" in md

    @pytest.mark.unit
    def test_both_dimensions_present(self):
        """A 类与 B 类必须都渲染, 且 B 类要说明"只按 A 类会漏人"。"""
        payload = {
            "available": True,
            "total_candidates": 2,
            "legacy_effective_new_ineffective": [
                {
                    "factor_name": "OLD_OK_NEW_NO",
                    "category": "MOM",
                    "ic_mean": 0.05,
                    "ic_ir": 0.27,
                    "n_samples": 120,
                    "new_score": 15.1,
                    "legacy_score": 15.1,
                }
            ],
            "new_undecidable_but_legacy_decidable": [
                {
                    "factor_name": "SHORT_20D",
                    "category": "SHORT",
                    "n_samples": 20,
                    "sample_gap": 40,
                }
            ],
            "undecidable_in_both": [],
        }
        md = _render_markdown(payload, self._criteria(), "2026-09-11")

        assert "A 类 · 旧有效 / 新无效" in md
        assert "B 类 · 新口径不可判" in md
        assert "OLD_OK_NEW_NO" in md
        assert "SHORT_20D" in md
        assert "需补 40 日" in md
        assert "会把它们整体漏掉" in md
        # 总览计数必须含两类
        assert "口径切换影响因子总数: 2" in md
        # 只报告不判定
        assert "不改变任何判定" in md
