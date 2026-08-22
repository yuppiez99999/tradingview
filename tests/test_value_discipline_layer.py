"""价值纪律层单测 — 覆盖 info_grade / quality_screen / mirror_test / 主模块

不依赖真实 LLM, 全部 mock。
"""

import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.value_discipline.info_grade import grade_info, grade_label, grade_strategy_adjustment
from utils.value_discipline.mirror_test import mirror_test
from utils.value_discipline.quality_screen import screen_quality


@dataclass
class FakeSignal:
    action: str = "BUY"
    code: str = "600519"
    name: str = "贵州茅台"
    current_weight: float = 0.05
    target_weight: float = 0.08
    weight_change: float = 0.03
    quantity: int = 100
    price: float = 1500.0
    confidence: float = 0.8
    reason: str = "高端白酒龙头, 品牌护城河深, ROE 持续 >30%"
    urgency: str = "MEDIUM"
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


DEFAULT_THRESHOLDS = {
    "roe_10y_min": 0.08,
    "fcf_5y_min": 0.0,
    "interest_coverage_min": 2.0,
    "gross_margin_min": 0.15,
    "ocf_to_netincome_min": 0.7,
    "net_margin_min": 0.05,
    "share_dilution_5y_max": 0.20,
}


class TestInfoGrade:
    def test_grade_a(self):
        assert grade_info({"上市年数": 15, "券商覆盖数": 30}) == "A"

    def test_grade_b(self):
        assert grade_info({"上市年数": 5, "券商覆盖数": 10}) == "B"

    def test_grade_c(self):
        assert grade_info({"上市年数": 1, "券商覆盖数": 2}) == "C"

    def test_grade_default_b(self):
        assert grade_info({}) == "C"

    def test_strategy_adjustment(self):
        assert "共识陷阱" in grade_strategy_adjustment("A")
        assert "置信度" in grade_strategy_adjustment("B")
        assert "第一性原理" in grade_strategy_adjustment("C")

    def test_label(self):
        assert grade_label("A") == "信息充裕"
        assert grade_label("C") == "信息稀缺"


class TestQualityScreen:
    def test_all_pass(self):
        fin = {
            "roe_10y_avg": 0.30, "fcf_5y_cumulative": 1e9,
            "interest_coverage": 10.0, "gross_margin_avg": 0.90,
            "ocf_to_netincome_5y": 1.0, "net_margin_avg": 0.50,
            "share_dilution_5y": 0.0,
        }
        res = screen_quality(fin, DEFAULT_THRESHOLDS)
        assert not res.triggered
        assert not res.hard_fail

    def test_roe_fail(self):
        fin = {"roe_10y_avg": 0.05, "fcf_5y_cumulative": 1e9,
               "interest_coverage": 10.0, "gross_margin_avg": 0.90,
               "ocf_to_netincome_5y": 1.0, "net_margin_avg": 0.50,
               "share_dilution_5y": 0.0, "上市年数": 15}
        res = screen_quality(fin, DEFAULT_THRESHOLDS)
        assert "1" in res.triggered
        assert res.hard_fail

    def test_exemption_a_strategic_investment(self):
        fin = {"roe_10y_avg": 0.05, "fcf_5y_cumulative": 1e9,
               "interest_coverage": 10.0, "gross_margin_avg": 0.35,
               "ocf_to_netincome_5y": 1.0, "net_margin_avg": 0.10,
               "share_dilution_5y": 0.0, "上市年数": 5,
               "roe_recent_2y_positive_ocf": True}
        res = screen_quality(fin, DEFAULT_THRESHOLDS)
        assert "1" in res.triggered
        assert "A" in res.exemptions
        assert not res.hard_fail

    def test_exemption_c_high_turnover(self):
        fin = {"roe_10y_avg": 0.25, "fcf_5y_cumulative": 1e9,
               "interest_coverage": 10.0, "gross_margin_avg": 0.10,
               "ocf_to_netincome_5y": 1.2, "net_margin_avg": 0.03,
               "share_dilution_5y": 0.0, "is_high_turnover_thin_margin": True}
        res = screen_quality(fin, DEFAULT_THRESHOLDS)
        assert "4" in res.triggered or "6" in res.triggered
        assert "C" in res.exemptions
        assert not res.hard_fail

    def test_bank_insurance_skip_interest(self):
        fin = {"roe_10y_avg": 0.30, "fcf_5y_cumulative": 1e9,
               "interest_coverage": 0.5, "gross_margin_avg": 0.90,
               "ocf_to_netincome_5y": 1.0, "net_margin_avg": 0.50,
               "share_dilution_5y": 0.0}
        res = screen_quality(fin, DEFAULT_THRESHOLDS, is_bank_insurance=True)
        assert "3" not in res.triggered

    def test_multiple_fail(self):
        fin = {"roe_10y_avg": 0.05, "fcf_5y_cumulative": -1e8,
               "interest_coverage": 0.5, "gross_margin_avg": 0.10,
               "ocf_to_netincome_5y": 0.3, "net_margin_avg": 0.02,
               "share_dilution_5y": 0.30, "上市年数": 15}
        res = screen_quality(fin, DEFAULT_THRESHOLDS)
        assert len(res.triggered) >= 5
        assert res.hard_fail


class TestMirrorTest:
    def test_short_thesis_pass(self):
        assert mirror_test("好生意") is True

    def test_no_llm_fallback_true(self):
        assert mirror_test("这是一个比较长的投资论点需要判断", llm_caller=None, fallback=True) is True

    def test_llm_compressible(self):
        def caller(prompt):
            return '{"compressible": true}'
        assert mirror_test("较长论点" * 20, llm_caller=caller) is True

    def test_llm_not_compressible(self):
        def caller(prompt):
            return '{"compressible": false}'
        assert mirror_test("较长论点" * 20, llm_caller=caller) is False

    def test_llm_exception_fallback(self):
        def caller(prompt):
            raise RuntimeError("boom")
        assert mirror_test("较长论点" * 20, llm_caller=caller, fallback=True) is True

    def test_llm_bad_json_fallback(self):
        def caller(prompt):
            return "not json"
        assert mirror_test("较长论点" * 20, llm_caller=caller, fallback=False) is False


class TestValueDisciplineLayer:
    def _make_layer(self, enabled=True, llm_responses=None):
        from utils.value_discipline_layer import ValueDisciplineLayer

        layer = ValueDisciplineLayer.__new__(ValueDisciplineLayer)
        layer.config = {
            "enabled": enabled,
            "scenes": ["rebalancing_analysis"],
            "masters": {
                "buffett": {"weight": 0.25}, "munger": {"weight": 0.25},
                "dyp": {"weight": 0.30}, "lixu": {"weight": 0.20},
            },
            "quality_screen": {
                "enabled": True, "hard_fail_action": "REDUCE",
                "indicators": DEFAULT_THRESHOLDS,
            },
            "mirror_test": {"enabled": True, "max_sentences": 5, "llm_fallback": True},
            "llm": {"parallel": False, "timeout_per_master": 8},
        }
        layer.enabled = enabled

        responses = llm_responses or {"score": 4, "reason": "好生意"}

        def mock_llm(prompt):
            import json as _json
            if "compressible" in prompt:
                return '{"compressible": true}'
            return _json.dumps(responses, ensure_ascii=False)

        layer._llm_caller = mock_llm
        return layer

    def test_disabled_bypass(self):
        layer = self._make_layer(enabled=False)
        sig = FakeSignal()
        out = layer.apply(sig, {}, {})
        assert out.signal is sig
        assert out.verdict == "grey"

    def test_apply_pass(self):
        layer = self._make_layer(llm_responses={"score": 5, "reason": "卓越"})
        fin = {"roe_10y_avg": 0.30, "fcf_5y_cumulative": 1e9,
               "interest_coverage": 10.0, "gross_margin_avg": 0.90,
               "ocf_to_netincome_5y": 1.0, "net_margin_avg": 0.50,
               "share_dilution_5y": 0.0}
        meta = {"上市年数": 15, "券商覆盖数": 30}
        out = layer.apply(FakeSignal(), fin, meta)
        assert out.verdict == "pass"
        assert out.consensus > 0
        assert out.mirror_pass is True
        assert out.info_grade == "A"
        assert out.signal.confidence <= 0.8

    def test_apply_hard_fail_reduce(self):
        layer = self._make_layer()
        fin = {"roe_10y_avg": 0.05, "fcf_5y_cumulative": -1e8,
               "interest_coverage": 0.5, "gross_margin_avg": 0.10,
               "ocf_to_netincome_5y": 0.3, "net_margin_avg": 0.02,
               "share_dilution_5y": 0.30, "上市年数": 15}
        out = layer.apply(FakeSignal(), fin, {"上市年数": 15, "券商覆盖数": 30})
        assert out.verdict == "fail"
        assert out.signal.action == "REDUCE"
        assert any(getattr(a, "alert_type", a.get("alert_type")) == "QUALITY_FAIL" for a in out.extra_alerts)

    def test_apply_mirror_fail_hold(self):
        layer = self._make_layer()

        def mock_llm(prompt):
            import json as _json
            if "compressible" in prompt:
                return '{"compressible": false}'
            return _json.dumps({"score": 4, "reason": "ok"}, ensure_ascii=False)

        layer._llm_caller = mock_llm
        fin = {"roe_10y_avg": 0.30, "fcf_5y_cumulative": 1e9,
               "interest_coverage": 10.0, "gross_margin_avg": 0.90,
               "ocf_to_netincome_5y": 1.0, "net_margin_avg": 0.50,
               "share_dilution_5y": 0.0}
        out = layer.apply(FakeSignal(), fin, {"上市年数": 15, "券商覆盖数": 30})
        assert out.mirror_pass is False
        assert out.signal.action == "HOLD"

    def test_apply_batch(self):
        layer = self._make_layer()
        sigs = [FakeSignal(code="600519"), FakeSignal(code="000858", name="五粮液")]
        fin_map = {s.code: {"roe_10y_avg": 0.30, "fcf_5y_cumulative": 1e9,
                            "interest_coverage": 10.0, "gross_margin_avg": 0.90,
                            "ocf_to_netincome_5y": 1.0, "net_margin_avg": 0.50,
                            "share_dilution_5y": 0.0} for s in sigs}
        meta_map = {s.code: {"上市年数": 15, "券商覆盖数": 30} for s in sigs}
        outs = layer.apply_batch(sigs, fin_map, meta_map)
        assert len(outs) == 2
        assert all(o.verdict == "pass" for o in outs)

    def test_immutable_signal(self):
        layer = self._make_layer()
        sig = FakeSignal()
        orig_conf = sig.confidence
        fin = {"roe_10y_avg": 0.30, "fcf_5y_cumulative": 1e9,
               "interest_coverage": 10.0, "gross_margin_avg": 0.90,
               "ocf_to_netincome_5y": 1.0, "net_margin_avg": 0.50,
               "share_dilution_5y": 0.0}
        out = layer.apply(sig, fin, {"上市年数": 15, "券商覆盖数": 30})
        assert sig.confidence == orig_conf
        assert out.signal.confidence != orig_conf or out.signal is not sig

    def test_llm_unavailable_master(self):
        layer = self._make_layer()
        layer._llm_caller = None
        fin = {"roe_10y_avg": 0.30, "fcf_5y_cumulative": 1e9,
               "interest_coverage": 10.0, "gross_margin_avg": 0.90,
               "ocf_to_netincome_5y": 1.0, "net_margin_avg": 0.50,
               "share_dilution_5y": 0.0}
        out = layer.apply(FakeSignal(), fin, {"上市年数": 15, "券商覆盖数": 30})
        assert all(not v.available for v in out.masters.values())
        assert out.consensus == 0.0

    def test_render_summary(self):
        layer = self._make_layer()
        from utils.value_discipline_layer import DisciplinedSignal

        d = DisciplinedSignal(signal=FakeSignal(), verdict="pass", consensus=0.8,
                              mirror_pass=True, info_grade="A")
        summary = layer.render_summary([d])
        assert "价值纪律层摘要" in summary
        assert "verdict=pass" in summary
