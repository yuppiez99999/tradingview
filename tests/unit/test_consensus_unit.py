"""alpha/llm/consensus.py 单元测试 — 多模型共识层.

目标模块: utils/alpha/llm/consensus.py (0% → 高覆盖)
覆盖: Finding/Verdict/ConsensusResult / _parse_json_response /
      MultiModelConsensus (discover/judge/run/to_dict) / run_consensus / findings_from_ocr
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from utils.alpha.llm.consensus import (
    ConsensusResult,
    Finding,
    MultiModelConsensus,
    Verdict,
    _parse_json_response,
    findings_from_ocr,
    run_consensus,
)


# ============================================================
# FindingDataclassTest — Finding 数据结构
# ============================================================

class FindingDataclassTest:

    def test_finding_defaults(self):
        f = Finding()
        assert f.id == ""
        assert f.lens == ""
        assert f.severity == "medium"
        assert f.title == ""
        assert f.description == ""
        assert f.evidence == ""
        assert f.location == ""
        assert f.model == ""

    def test_finding_with_fields(self):
        f = Finding(id="F1", lens="correctness", severity="high", title="t", model="glm")
        assert f.id == "F1"
        assert f.severity == "high"
        assert f.model == "glm"


# ============================================================
# VerdictDataclassTest — Verdict 数据结构
# ============================================================

class VerdictDataclassTest:

    def test_verdict_defaults(self):
        v = Verdict()
        assert v.finding_id == ""
        assert v.votes == []
        assert v.confirmed_count == 0
        assert v.total_judges == 0
        assert v.verdict == "dismissed"
        assert v.confidence == 0.0


# ============================================================
# ConsensusResultDataclassTest — ConsensusResult 数据结构
# ============================================================

class ConsensusResultDataclassTest:

    def test_defaults(self):
        r = ConsensusResult()
        assert r.artifact == ""
        assert r.discoveries == []
        assert r.verdicts == []
        assert r.confirmed_findings == []
        assert r.agreement is True
        assert r.success is False
        assert r.error is None
        assert r.raw_responses == {}


# ============================================================
# ParseJsonResponseTest — JSON 解析辅助函数
# ============================================================

class ParseJsonResponseTest:

    def test_none_input(self):
        assert _parse_json_response(None) is None

    def test_empty_string(self):
        assert _parse_json_response("") is None

    def test_plain_json(self):
        result = _parse_json_response('{"key": "value"}')
        assert result == {"key": "value"}

    def test_markdown_json_block(self):
        text = '```json\n{"key": "val"}\n```'
        assert _parse_json_response(text) == {"key": "val"}

    def test_markdown_block_without_lang(self):
        text = '```\n{"a": 1}\n```'
        assert _parse_json_response(text) == {"a": 1}

    def test_json_embedded_in_text(self):
        text = 'Some preamble\n{"findings": []}\ntrailing'
        result = _parse_json_response(text)
        assert result == {"findings": []}

    def test_invalid_json_returns_none(self):
        assert _parse_json_response("not json at all") is None

    def test_invalid_json_with_no_braces(self):
        assert _parse_json_response("just text no braces") is None

    def test_nested_json(self):
        text = '{"findings": [{"id": "F1", "severity": "high"}]}'
        result = _parse_json_response(text)
        assert result["findings"][0]["id"] == "F1"

    def test_whitespace_only(self):
        assert _parse_json_response("   ") is None


# ============================================================
# FindingsFromOcrTest — OCR 结果转 Finding
# ============================================================

class FindingsFromOcrTest:

    def test_empty_comments(self):
        result = findings_from_ocr({"comments": []})
        assert result == []

    def test_filters_by_severity(self):
        ocr = {"comments": [
            {"id": "C1", "severity": "low", "message": "low issue"},
            {"id": "C2", "severity": "high", "message": "high issue"},
            {"id": "C3", "severity": "critical", "message": "crit issue"},
        ]}
        result = findings_from_ocr(ocr)
        assert len(result) == 2
        assert result[0].id == "C2"
        assert result[1].id == "C3"

    def test_custom_severity_filter(self):
        ocr = {"comments": [
            {"id": "C1", "severity": "low", "message": "low"},
            {"id": "C2", "severity": "medium", "message": "med"},
        ]}
        result = findings_from_ocr(ocr, severity_filter=["low"])
        assert len(result) == 1
        assert result[0].id == "C1"

    def test_finding_fields_mapping(self):
        ocr = {"comments": [{
            "id": "C1", "severity": "high", "title": "Bug Here",
            "message": "desc", "evidence": "ev", "file": "f.py",
        }]}
        result = findings_from_ocr(ocr)
        f = result[0]
        assert f.id == "C1"
        assert f.severity == "high"
        assert f.title == "Bug Here"
        assert f.description == "desc"
        assert f.evidence == "ev"
        assert f.location == "f.py"
        assert f.lens == "correctness"
        assert f.model == "ocr-glm"

    def test_title_fallback_to_message(self):
        ocr = {"comments": [{"id": "C1", "severity": "high", "message": "msg only"}]}
        result = findings_from_ocr(ocr)
        assert result[0].title == "msg only"

    def test_list_input_raises_attribute_error(self):
        """源码对裸 list 输入调用 .get() → AttributeError (list 无 get 方法)."""
        ocr = [{"id": "C1", "severity": "critical", "message": "m"}]
        with pytest.raises(AttributeError):
            findings_from_ocr(ocr)

    def test_auto_id_when_missing(self):
        ocr = {"comments": [{"severity": "high", "message": "m"}]}
        result = findings_from_ocr(ocr)
        assert result[0].id == "OCR1"


# ============================================================
# MultiModelConsensusTest — 共识层核心
# ============================================================

class MultiModelConsensusTest:

    def test_init_defaults(self):
        c = MultiModelConsensus()
        assert c.models == ["deepseek", "glm"]
        assert c.judge_mode == "vote"
        assert c.temperature == 0.3
        assert c.max_tokens == 2000
        assert c.timeout == 20

    def test_init_custom(self):
        c = MultiModelConsensus(models=["a", "b", "c"], judge_mode="cross_validate", timeout=10)
        assert c.models == ["a", "b", "c"]
        assert c.judge_mode == "cross_validate"
        assert c.timeout == 10

    def test_call_model_unknown_returns_none(self):
        c = MultiModelConsensus(models=["unknown_model"])
        # _call_model 对未知模型返回 None (不经过 router)
        with patch.object(c, "_get_router"):
            result = c._call_model("unknown_model", "p", "s")
        assert result is None

    def test_discover_no_models_respond(self):
        c = MultiModelConsensus(models=["deepseek", "glm"])
        with patch.object(c, "_call_model", return_value=None):
            findings, raws = c.discover("artifact text", lens="correctness")
        assert findings == []
        assert raws == {"deepseek": None, "glm": None}

    def test_discover_parses_findings(self):
        c = MultiModelConsensus(models=["deepseek"])
        response = json.dumps({"findings": [
            {"id": "F1", "severity": "high", "title": "Bug", "description": "d",
             "evidence": "e", "location": "loc"},
        ]})
        with patch.object(c, "_call_model", return_value=response):
            findings, raws = c.discover("artifact", lens="testing")
        assert len(findings) == 1
        assert findings[0].id == "F1"
        assert findings[0].severity == "high"
        assert findings[0].lens == "testing"
        assert findings[0].model == "deepseek"

    def test_discover_invalid_json_skipped(self):
        c = MultiModelConsensus(models=["deepseek"])
        with patch.object(c, "_call_model", return_value="not json"):
            findings, raws = c.discover("artifact")
        assert findings == []

    def test_discover_auto_id(self):
        c = MultiModelConsensus(models=["deepseek"])
        response = json.dumps({"findings": [{"severity": "high", "title": "t"}]})
        with patch.object(c, "_call_model", return_value=response):
            findings, _ = c.discover("artifact")
        assert findings[0].id == "F1"

    def test_judge_vote_majority_confirmed(self):
        c = MultiModelConsensus(models=["m1", "m2", "m3"], judge_mode="vote")
        cand = Finding(id="F1", title="t", severity="high")
        responses = [
            json.dumps({"verdict": "confirmed", "confidence": 0.9}),
            json.dumps({"verdict": "confirmed", "confidence": 0.8}),
            json.dumps({"verdict": "dismissed", "confidence": 0.5}),
        ]
        with patch.object(c, "_call_model", side_effect=responses):
            verdicts = c.judge([cand])
        assert len(verdicts) == 1
        assert verdicts[0].verdict == "confirmed"
        assert verdicts[0].confirmed_count == 2
        assert verdicts[0].total_judges == 3

    def test_judge_vote_majority_dismissed(self):
        c = MultiModelConsensus(models=["m1", "m2", "m3"], judge_mode="vote")
        cand = Finding(id="F1", title="t", severity="low")
        responses = [
            json.dumps({"verdict": "dismissed", "confidence": 0.5}),
            json.dumps({"verdict": "dismissed", "confidence": 0.6}),
            json.dumps({"verdict": "confirmed", "confidence": 0.9}),
        ]
        with patch.object(c, "_call_model", side_effect=responses):
            verdicts = c.judge([cand])
        assert verdicts[0].verdict == "dismissed"
        assert verdicts[0].confirmed_count == 1

    def test_judge_cross_validate_both_confirmed(self):
        c = MultiModelConsensus(models=["m1", "m2"], judge_mode="cross_validate")
        cand = Finding(id="F1", title="t", severity="high")
        responses = [
            json.dumps({"verdict": "confirmed", "confidence": 0.9}),
            json.dumps({"verdict": "confirmed", "confidence": 0.8}),
        ]
        with patch.object(c, "_call_model", side_effect=responses):
            verdicts = c.judge([cand])
        assert verdicts[0].verdict == "confirmed"

    def test_judge_cross_validate_both_dismissed(self):
        c = MultiModelConsensus(models=["m1", "m2"], judge_mode="cross_validate")
        cand = Finding(id="F1", title="t", severity="low")
        responses = [
            json.dumps({"verdict": "dismissed", "confidence": 0.5}),
            json.dumps({"verdict": "dismissed", "confidence": 0.6}),
        ]
        with patch.object(c, "_call_model", side_effect=responses):
            verdicts = c.judge([cand])
        assert verdicts[0].verdict == "dismissed"

    def test_judge_cross_validate_split_high_conf_wins(self):
        c = MultiModelConsensus(models=["m1", "m2"], judge_mode="cross_validate")
        cand = Finding(id="F1", title="t", severity="medium")
        # m1 dismissed conf 0.5, m2 confirmed conf 0.9 → m2 wins
        responses = [
            json.dumps({"verdict": "dismissed", "confidence": 0.5}),
            json.dumps({"verdict": "confirmed", "confidence": 0.9}),
        ]
        with patch.object(c, "_call_model", side_effect=responses):
            verdicts = c.judge([cand])
        assert verdicts[0].verdict == "confirmed"

    def test_judge_no_votes_returns_dismissed(self):
        c = MultiModelConsensus(models=["m1"])
        cand = Finding(id="F1", title="t", severity="high")
        with patch.object(c, "_call_model", return_value=None):
            verdicts = c.judge([cand])
        assert verdicts[0].verdict == "dismissed"
        assert verdicts[0].total_judges == 0
        assert verdicts[0].confidence == 0.0

    def test_judge_empty_candidates(self):
        c = MultiModelConsensus(models=["m1"])
        verdicts = c.judge([])
        assert verdicts == []

    def test_run_no_findings(self):
        c = MultiModelConsensus(models=["deepseek", "glm"])
        with patch.object(c, "_call_model", return_value=None):
            result = c.run("artifact", lens="correctness")
        assert result.success is True
        assert result.mode == "no_findings"
        assert result.discoveries == []

    def test_run_with_findings_confirmed(self):
        c = MultiModelConsensus(models=["m1", "m2", "m3"], judge_mode="vote")
        discover_resp = json.dumps({"findings": [
            {"id": "F1", "severity": "high", "title": "Bug", "description": "d"},
        ]})
        judge_resps = [
            json.dumps({"verdict": "confirmed", "confidence": 0.9}),
            json.dumps({"verdict": "confirmed", "confidence": 0.8}),
            json.dumps({"verdict": "confirmed", "confidence": 0.7}),
        ]
        # discover: m1→findings, m2→None, m3→None; judge: 3 models × 1 finding
        with patch.object(c, "_call_model", side_effect=[discover_resp, None, None] + judge_resps):
            result = c.run("artifact", lens="correctness")
        assert result.success is True
        assert result.mode == "vote"
        assert len(result.confirmed_findings) == 1

    def test_run_with_candidates_override(self):
        """传入 candidates 跳过 discover."""
        c = MultiModelConsensus(models=["m1", "m2", "m3"], judge_mode="vote")
        cand = [Finding(id="F1", title="t", severity="high")]
        judge_resps = [
            json.dumps({"verdict": "dismissed", "confidence": 0.5}),
            json.dumps({"verdict": "dismissed", "confidence": 0.6}),
            json.dumps({"verdict": "dismissed", "confidence": 0.7}),
        ]
        with patch.object(c, "_call_model", side_effect=judge_resps):
            result = c.run("artifact", candidates=cand)
        assert result.success is True
        assert len(result.verdicts) == 1
        assert result.verdicts[0].verdict == "dismissed"

    def test_run_single_model_mode(self):
        c = MultiModelConsensus(models=["m1"])
        discover_resp = json.dumps({"findings": [
            {"id": "F1", "severity": "high", "title": "Bug"},
        ]})
        judge_resp = json.dumps({"verdict": "confirmed", "confidence": 0.9})
        with patch.object(c, "_call_model", side_effect=[discover_resp, judge_resp]):
            result = c.run("artifact")
        assert result.success is True
        assert result.mode == "single_m1"

    def test_run_divergence_detected(self):
        """部分 confirmed 部分 dismissed → agreement=False."""
        c = MultiModelConsensus(models=["m1", "m2", "m3"], judge_mode="vote")
        discover_resp = json.dumps({"findings": [
            {"id": "F1", "severity": "high", "title": "A"},
            {"id": "F2", "severity": "high", "title": "B"},
        ]})
        # F1: 2 confirmed → confirmed; F2: 0 confirmed → dismissed
        judge_resps = [
            json.dumps({"verdict": "confirmed", "confidence": 0.9}),
            json.dumps({"verdict": "confirmed", "confidence": 0.8}),
            json.dumps({"verdict": "dismissed", "confidence": 0.5}),
            json.dumps({"verdict": "dismissed", "confidence": 0.5}),
            json.dumps({"verdict": "dismissed", "confidence": 0.6}),
            json.dumps({"verdict": "dismissed", "confidence": 0.7}),
        ]
        # discover: m1→findings, m2→None, m3→None; judge: 3 models × 2 findings = 6
        with patch.object(c, "_call_model", side_effect=[discover_resp, None, None] + judge_resps):
            result = c.run("artifact")
        assert result.success is True
        assert result.agreement is False
        assert result.divergence_reason is not None

    def test_to_dict_structure(self):
        c = MultiModelConsensus(models=["m1", "m2", "m3"], judge_mode="vote")
        discover_resp = json.dumps({"findings": [
            {"id": "F1", "severity": "high", "title": "Bug", "description": "d"},
        ]})
        judge_resps = [json.dumps({"verdict": "confirmed", "confidence": 0.9})] * 3
        # discover: m1→findings, m2→None, m3→None; judge: 3 models × 1 finding
        with patch.object(c, "_call_model", side_effect=[discover_resp, None, None] + judge_resps):
            result = c.run("artifact", lens="testing")
        d = MultiModelConsensus.to_dict(result)
        assert d["lens"] == "testing"
        assert d["success"] is True
        assert d["mode"] == "vote"
        assert d["discoveries_count"] == 1
        assert d["confirmed_count"] == 1
        assert len(d["findings"]) == 1
        assert len(d["verdicts"]) == 1
        assert d["findings"][0]["id"] == "F1"


# ============================================================
# RunConsensusTest — 便捷函数
# ============================================================

class RunConsensusTest:

    def test_run_consensus_no_findings(self):
        with patch("utils.alpha.llm.consensus.MultiModelConsensus._call_model", return_value=None):
            result = run_consensus("artifact", lens="correctness")
        assert result.success is True
        assert result.mode == "no_findings"

    def test_run_consensus_with_output_path(self, tmp_path):
        out = tmp_path / "consensus_out.json"
        with patch("utils.alpha.llm.consensus.MultiModelConsensus._call_model", return_value=None):
            result = run_consensus("artifact", output_path=str(out))
        assert out.exists()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["success"] is True
        assert data["mode"] == "no_findings"

    def test_run_consensus_custom_models(self):
        with patch("utils.alpha.llm.consensus.MultiModelConsensus._call_model", return_value=None):
            result = run_consensus("artifact", models=["a", "b"], judge_mode="cross_validate")
        assert result.success is True