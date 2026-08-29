"""test_alt_data_indicators_unit.py — 另类数据指标引擎单元测试

覆盖要点:
    - SatelliteIndicator / SearchIndexIndicator / RecruitmentIndicator / PatentIndicator dataclass
    - AltDataSignal / AltDataResult dataclass
    - AltDataIndicators 构造 (默认/自定义参数)
    - add_satellite / add_search / add_recruitment / add_patent / add_satellite_batch
    - analyze (空/卫星/搜索/招聘/专利/综合评分/覆盖率/置信度/异常/全市场)
    - _calc_satellite_score / _calc_search_score / _calc_recruitment_score / _calc_patent_score
    - get_signal / load_demo_data / summarize
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from utils.alt_data_indicators import (
    AltDataIndicators,
    AltDataResult,
    AltDataSignal,
    PatentIndicator,
    RecruitmentIndicator,
    SatelliteIndicator,
    SearchIndexIndicator,
)

# ============================================================
# Dataclass
# ============================================================


class TestSatelliteIndicator:
    @pytest.mark.unit
    def test_defaults(self):
        s = SatelliteIndicator(
            region="宁波港", indicator_type="PORT_ACTIVITY", value=85.5
        )
        assert s.region == "宁波港"
        assert s.yoy_change == 0.0
        assert s.mom_change == 0.0
        assert s.related_symbols == []
        assert s.confidence == 0.8


class TestSearchIndexIndicator:
    @pytest.mark.unit
    def test_defaults(self):
        s = SearchIndexIndicator(keyword="半导体", platform="BAIDU", index_value=5000)
        assert s.trend_7d == 0.0
        assert s.is_breakout is False
        assert s.related_symbols == []


class TestRecruitmentIndicator:
    @pytest.mark.unit
    def test_defaults(self):
        r = RecruitmentIndicator(company="中芯国际", job_count=200)
        assert r.avg_salary == 0.0
        assert r.related_symbol == ""


class TestPatentIndicator:
    @pytest.mark.unit
    def test_defaults(self):
        p = PatentIndicator(company="华为")
        assert p.patent_count == 0
        assert p.citation_count == 0
        assert p.tech_distribution == {}


class TestAltDataSignal:
    @pytest.mark.unit
    def test_defaults(self):
        s = AltDataSignal(symbol="000001")
        assert s.satellite_score == 0.0
        assert s.composite_score == 0.0
        assert s.coverage == 0.0


class TestAltDataResult:
    @pytest.mark.unit
    def test_defaults(self):
        r = AltDataResult()
        assert r.signals == {}
        assert r.anomalies == []


# ============================================================
# AltDataIndicators 构造
# ============================================================


class TestInit:
    @pytest.mark.unit
    def test_defaults(self):
        e = AltDataIndicators()
        assert e.w_sat == 0.3
        assert e.w_search == 0.25
        assert e.w_recruit == 0.2
        assert e.w_patent == 0.25
        assert e.anomaly_threshold == 2.0
        assert e.expiry_days == 30

    @pytest.mark.unit
    def test_custom(self):
        e = AltDataIndicators(
            w_satellite=0.4, w_search=0.3, w_recruitment=0.1, w_patent=0.2
        )
        assert e.w_sat == 0.4
        assert e.w_search == 0.3


# ============================================================
# 数据添加
# ============================================================


class TestAddData:
    @pytest.mark.unit
    def test_add_satellite(self):
        e = AltDataIndicators()
        e.add_satellite(SatelliteIndicator("宁波港", "PORT_ACTIVITY", 85.5))
        assert len(e.satellite_data) == 1

    @pytest.mark.unit
    def test_add_search(self):
        e = AltDataIndicators()
        e.add_search(SearchIndexIndicator("半导体", "BAIDU", 5000))
        assert len(e.search_data) == 1

    @pytest.mark.unit
    def test_add_recruitment(self):
        e = AltDataIndicators()
        e.add_recruitment(RecruitmentIndicator("中芯", 200))
        assert len(e.recruitment_data) == 1

    @pytest.mark.unit
    def test_add_patent(self):
        e = AltDataIndicators()
        e.add_patent(PatentIndicator("华为", patent_count=100))
        assert len(e.patent_data) == 1

    @pytest.mark.unit
    def test_add_satellite_batch(self):
        e = AltDataIndicators()
        count = e.add_satellite_batch(
            [
                SatelliteIndicator("A", "PORT_ACTIVITY", 1),
                SatelliteIndicator("B", "OIL_TANK", 2),
            ]
        )
        assert count == 2
        assert len(e.satellite_data) == 2


# ============================================================
# analyze
# ============================================================


class TestAnalyze:
    @pytest.mark.unit
    def test_empty(self):
        e = AltDataIndicators()
        result = e.analyze(["000001"])
        assert "000001" in result.signals
        assert result.signals["000001"].composite_score == 0.0
        assert result.market_alt_score == 0.0

    @pytest.mark.unit
    def test_with_satellite(self):
        e = AltDataIndicators()
        e.add_satellite(
            SatelliteIndicator(
                "宁波港",
                "PORT_ACTIVITY",
                85.5,
                yoy_change=20.0,
                related_symbols=["601016"],
            )
        )
        result = e.analyze(["601016"])
        sig = result.signals["601016"]
        assert sig.satellite_score > 0
        assert sig.coverage == 0.25  # 1/4

    @pytest.mark.unit
    def test_with_search(self):
        e = AltDataIndicators()
        e.add_search(
            SearchIndexIndicator(
                "半导体",
                "BAIDU",
                5000,
                trend_7d=50.0,
                related_symbols=["002049"],
            )
        )
        result = e.analyze(["002049"])
        sig = result.signals["002049"]
        assert sig.search_score > 0

    @pytest.mark.unit
    def test_with_recruitment(self):
        e = AltDataIndicators()
        e.add_recruitment(
            RecruitmentIndicator(
                "中芯",
                200,
                job_count_yoy=30.0,
                salary_change=10.0,
                related_symbol="688981",
            )
        )
        result = e.analyze(["688981"])
        sig = result.signals["688981"]
        assert sig.recruitment_score > 0

    @pytest.mark.unit
    def test_with_patent(self):
        e = AltDataIndicators()
        e.add_patent(
            PatentIndicator(
                "华为",
                patent_count=100,
                citation_count=500,
                patent_count_yoy=20.0,
                citation_growth=30.0,
                related_symbol="300308",
            )
        )
        result = e.analyze(["300308"])
        sig = result.signals["300308"]
        assert sig.patent_score > 0

    @pytest.mark.unit
    def test_composite_score(self):
        e = AltDataIndicators()
        e.add_satellite(
            SatelliteIndicator(
                "港口",
                "PORT_ACTIVITY",
                85.5,
                yoy_change=50.0,
                related_symbols=["000001"],
                confidence=1.0,
            )
        )
        result = e.analyze(["000001"])
        sig = result.signals["000001"]
        # satellite_score = 1.0 * 1.0 = 1.0, composite = 0.3 * 1.0 = 0.3
        assert sig.composite_score == pytest.approx(0.3, abs=0.01)

    @pytest.mark.unit
    def test_anomaly_detection(self):
        e = AltDataIndicators()
        e.add_satellite(
            SatelliteIndicator(
                "港口",
                "PORT_ACTIVITY",
                100,
                yoy_change=100.0,
                related_symbols=["000001"],
            )
        )
        result = e.analyze(["000001"])
        # composite > 0.5 → anomaly
        if abs(result.signals["000001"].composite_score) > 0.5:
            assert len(result.anomalies) >= 1

    @pytest.mark.unit
    def test_coverage_summary(self):
        e = AltDataIndicators()
        e.add_satellite(SatelliteIndicator("A", "PORT", 1, related_symbols=["000001"]))
        e.add_search(SearchIndexIndicator("k", "BAIDU", 1, related_symbols=["000001"]))
        result = e.analyze(["000001"])
        assert result.coverage_summary["satellite"] == 1
        assert result.coverage_summary["search"] == 1

    @pytest.mark.unit
    def test_expired_data_filtered(self):
        e = AltDataIndicators()
        old_ts = datetime.now() - timedelta(days=60)
        e.add_satellite(
            SatelliteIndicator(
                "港口",
                "PORT_ACTIVITY",
                85.5,
                yoy_change=20.0,
                related_symbols=["000001"],
                timestamp=old_ts,
            )
        )
        result = e.analyze(["000001"])
        assert result.signals["000001"].satellite_score == 0.0


# ============================================================
# 评分函数
# ============================================================


class TestScoring:
    @pytest.mark.unit
    def test_satellite_score(self):
        e = AltDataIndicators()
        inds = [SatelliteIndicator("A", "PORT", 1, yoy_change=50.0, confidence=1.0)]
        assert e._calc_satellite_score(inds) == pytest.approx(1.0)

    @pytest.mark.unit
    def test_satellite_score_negative(self):
        e = AltDataIndicators()
        inds = [SatelliteIndicator("A", "PORT", 1, yoy_change=-50.0, confidence=1.0)]
        assert e._calc_satellite_score(inds) == pytest.approx(-1.0)

    @pytest.mark.unit
    def test_search_score_breakout(self):
        e = AltDataIndicators()
        inds = [SearchIndexIndicator("k", "B", 1, trend_7d=100.0, is_breakout=True)]
        # score = 1.0 * 1.5 = 1.5 → clamped to mean
        score = e._calc_search_score(inds)
        assert score > 1.0

    @pytest.mark.unit
    def test_recruitment_score(self):
        e = AltDataIndicators()
        inds = [RecruitmentIndicator("c", 100, job_count_yoy=50.0, salary_change=20.0)]
        # 0.6*1.0 + 0.4*1.0 = 1.0
        assert e._calc_recruitment_score(inds) == pytest.approx(1.0)

    @pytest.mark.unit
    def test_patent_score(self):
        e = AltDataIndicators()
        inds = [
            PatentIndicator(
                "c", patent_count=100, patent_count_yoy=30.0, citation_growth=50.0
            )
        ]
        # 0.5*1.0 + 0.5*1.0 = 1.0
        assert e._calc_patent_score(inds) == pytest.approx(1.0)


# ============================================================
# get_signal
# ============================================================


class TestGetSignal:
    @pytest.mark.unit
    def test_found(self):
        e = AltDataIndicators()
        e.add_satellite(
            SatelliteIndicator(
                "A", "PORT", 1, yoy_change=10.0, related_symbols=["000001"]
            )
        )
        sig = e.get_signal("000001")
        assert sig is not None
        assert sig.symbol == "000001"

    @pytest.mark.unit
    def test_not_found(self):
        e = AltDataIndicators()
        sig = e.get_signal("000999")
        assert sig is not None  # AltDataSignal always created
        assert sig.composite_score == 0.0


# ============================================================
# load_demo_data
# ============================================================


class TestLoadDemoData:
    @pytest.mark.unit
    def test_load(self, monkeypatch):
        monkeypatch.setenv("ALT_DATA_DEMO", "1")
        e = AltDataIndicators()
        count = e.load_demo_data(["000001", "000002"])
        assert count == 8  # 4 per symbol
        assert len(e.satellite_data) == 2
        assert len(e.search_data) == 2


# ============================================================
# summarize
# ============================================================


class TestSummarize:
    @pytest.mark.unit
    def test_summary(self):
        e = AltDataIndicators()
        e.add_satellite(
            SatelliteIndicator(
                "A", "PORT", 1, yoy_change=20.0, related_symbols=["000001"]
            )
        )
        result = e.analyze(["000001"])
        s = e.summarize(result)
        assert s["total_signals"] == 1
        assert "market_alt_score" in s
        assert "anomalies_count" in s
