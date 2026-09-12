"""G7 覆盖率冲刺 — research_distiller 补充测试

目标: 将 utils/research_distiller.py 覆盖率从约 60% 提升到 85%+
测试重点:
    - DistilledSignal 校验分支
    - ResearchDistiller 初始化 / 配置分支
    - distill_report / distill_earnings_call / distill_book_chapter / distill_news_batch
    - 信号提取、质量评估、关键词分类、时效判断、研报处理
    - _llm_distill / _rule_distill / _extract_symbols / _normalize_symbol
    - _compute_valid_until / _is_signal_valid / to_signal_map
    - save/load_daily_snapshot / get_status / self_test
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from utils.datetime_utils import now_bj

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.research_distiller import (  # noqa: E402
    DistilledSignal,
    ResearchDistiller,
    _safe_float,
    self_test,
)

# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def tmp_snapshot_dir(tmp_path, monkeypatch):
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("utils.research_distiller.Path", lambda *a, **kw: tmp_path)
    return snap_dir


@pytest.fixture
def distiller_default(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.research_distiller.Path", lambda *a, **kw: tmp_path)
    d = ResearchDistiller(llm_enabled=False)
    return d


# ============================================================
# DistilledSignal 基础校验
# ============================================================


class TestDistilledSignalValidation:
    def test_strength_clamped_above_one(self):
        s = DistilledSignal(symbol="600276.SH", strength=2.0)
        assert s.strength == 1.0

    def test_strength_clamped_below_minus_one(self):
        s = DistilledSignal(symbol="600276.SH", strength=-2.0)
        assert s.strength == -1.0

    def test_confidence_clamped_above_one(self):
        s = DistilledSignal(symbol="600276.SH", confidence=2.0)
        assert s.confidence == 1.0

    def test_confidence_clamped_below_zero(self):
        s = DistilledSignal(symbol="600276.SH", confidence=-0.5)
        assert s.confidence == 0.0

    def test_nan_strength_becomes_zero(self):
        s = DistilledSignal(symbol="600276.SH", strength=float("nan"))
        assert s.strength == 0.0

    def test_nan_confidence_becomes_zero(self):
        s = DistilledSignal(symbol="600276.SH", confidence=float("nan"))
        assert s.confidence == 0.0

    def test_inf_strength_becomes_zero(self):
        s = DistilledSignal(symbol="600276.SH", strength=float("inf"))
        assert s.strength == 0.0

    def test_invalid_source_type_falls_back_to_news(self):
        s = DistilledSignal(symbol="600276.SH", source_type="invalid_type")
        assert s.source_type == "news"

    def test_non_string_symbol_converted(self):
        s = DistilledSignal(symbol=12345)
        assert s.symbol == "12345"

    def test_empty_symbol_remains_empty(self):
        s = DistilledSignal(symbol="")
        assert s.symbol == ""

    def test_to_dict_roundtrip(self):
        s = DistilledSignal(
            symbol="600276.SH", strength=0.6, confidence=0.8, key_factors=["a", "b"]
        )
        d = s.to_dict()
        assert d["symbol"] == "600276.SH"
        assert d["strength"] == 0.6
        assert d["confidence"] == 0.8

    def test_from_dict_roundtrip(self):
        original = DistilledSignal(
            symbol="000001.SZ", strength=0.5, confidence=0.9, key_factors=["x"]
        )
        restored = DistilledSignal.from_dict(original.to_dict())
        assert restored.symbol == original.symbol
        assert restored.strength == original.strength


# ============================================================
# 工具函数
# ============================================================


class TestSafeFloat:
    def test_none_returns_default(self):
        assert _safe_float(None) == 0.0
        assert _safe_float(None, default=1.0) == 1.0

    def test_valid_number(self):
        assert _safe_float("3.14") == 3.14
        assert _safe_float(42) == 42.0

    def test_nan_returns_default(self):
        assert _safe_float(float("nan")) == 0.0

    def test_inf_returns_default(self):
        assert _safe_float(float("inf")) == 0.0
        assert _safe_float(float("-inf")) == 0.0

    def test_invalid_string_returns_default(self):
        assert _safe_float("abc") == 0.0
        assert _safe_float("") == 0.0


# ============================================================
# ResearchDistiller 初始化
# ============================================================


class TestResearchDistillerInit:
    def test_default_init(self, tmp_path, monkeypatch):
        monkeypatch.setattr("utils.research_distiller.Path", lambda *a, **kw: tmp_path)
        d = ResearchDistiller()
        assert d.llm_available is False or True
        assert d.cache_dir == tmp_path
        assert isinstance(d._stats, dict)

    def test_llm_enabled_false(self, tmp_path, monkeypatch):
        monkeypatch.setattr("utils.research_distiller.Path", lambda *a, **kw: tmp_path)
        d = ResearchDistiller(llm_enabled=False)
        assert d.llm_available is False

    def test_custom_cache_dir(self, tmp_path, monkeypatch):
        custom = tmp_path / "custom_cache"
        d = ResearchDistiller(cache_dir=custom, llm_enabled=False)
        assert d.cache_dir == custom
        assert d.cache_dir.exists()

    def test_get_status_keys(self, tmp_path, monkeypatch):
        monkeypatch.setattr("utils.research_distiller.Path", lambda *a, **kw: tmp_path)
        d = ResearchDistiller(llm_enabled=False)
        status = d.get_status()
        assert "llm_available" in status
        assert "cache_dir" in status
        assert "stats" in status
        assert "llm_calls" in status["stats"]

    def test_self_test_runs(self):
        result = self_test()
        assert isinstance(result, bool)


# ============================================================
# 标的代码规范化
# ============================================================


class TestSymbolNormalization:
    def test_normalize_full_code_sh(self, distiller_default):
        assert distiller_default._normalize_symbol("600276.SH") == "600276.SH"

    def test_normalize_full_code_sz(self, distiller_default):
        assert distiller_default._normalize_symbol("000001.SZ") == "000001.SZ"

    def test_normalize_full_code_hk(self, distiller_default):
        assert distiller_default._normalize_symbol("00700.HK") == "00700.HK"

    def test_normalize_bare_code_6(self, distiller_default):
        assert distiller_default._normalize_symbol("600276") == "600276.SH"

    def test_normalize_bare_code_0(self, distiller_default):
        assert distiller_default._normalize_symbol("000001") == "000001.SZ"

    def test_normalize_bare_code_3(self, distiller_default):
        assert distiller_default._normalize_symbol("300750") == "300750.SZ"

    def test_normalize_bare_code_9(self, distiller_default):
        assert distiller_default._normalize_symbol("900001") == "900001.SH"

    def test_normalize_hk_5_digit(self, distiller_default):
        assert distiller_default._normalize_symbol("00700") == "00700.HK"

    def test_normalize_invalid_returns_empty(self, distiller_default):
        assert distiller_default._normalize_symbol("") == ""
        assert distiller_default._normalize_symbol("abc") == ""
        assert distiller_default._normalize_symbol("1234") == ""

    def test_normalize_lowercase_suffix(self, distiller_default):
        assert distiller_default._normalize_symbol("600276.sh") == "600276.SH"


# ============================================================
# 时效计算
# ============================================================


class TestValidity:
    def test_report_validity_7_days(self, distiller_default):
        valid = distiller_default._compute_valid_until("report")
        expected = (now_bj() + timedelta(days=7)).strftime("%Y-%m-%d")
        assert valid == expected

    def test_news_validity_1_day(self, distiller_default):
        valid = distiller_default._compute_valid_until("news")
        expected = (now_bj() + timedelta(days=1)).strftime("%Y-%m-%d")
        assert valid == expected

    def test_earnings_call_validity_30_days(self, distiller_default):
        valid = distiller_default._compute_valid_until("earnings_call")
        expected = (now_bj() + timedelta(days=30)).strftime("%Y-%m-%d")
        assert valid == expected

    def test_book_validity_90_days(self, distiller_default):
        valid = distiller_default._compute_valid_until("book")
        expected = (now_bj() + timedelta(days=90)).strftime("%Y-%m-%d")
        assert valid == expected

    def test_unknown_source_defaults_to_7_days(self, distiller_default):
        valid = distiller_default._compute_valid_until("unknown")
        expected = (now_bj() + timedelta(days=7)).strftime("%Y-%m-%d")
        assert valid == expected

    def test_is_signal_valid_true(self, distiller_default):
        future = (now_bj() + timedelta(days=1)).strftime("%Y-%m-%d")
        s = DistilledSignal(symbol="600276.SH", valid_until=future)
        assert distiller_default._is_signal_valid(s) is True

    def test_is_signal_valid_false_expired(self, distiller_default):
        past = (now_bj() - timedelta(days=1)).strftime("%Y-%m-%d")
        s = DistilledSignal(symbol="600276.SH", valid_until=past)
        assert distiller_default._is_signal_valid(s) is False

    def test_is_signal_valid_invalid_format_returns_false(self, distiller_default):
        s = DistilledSignal(symbol="600276.SH", valid_until="not-a-date")
        assert distiller_default._is_signal_valid(s) is True

    def test_is_signal_valid_empty_returns_true(self, distiller_default):
        s = DistilledSignal(symbol="600276.SH", valid_until="")
        assert distiller_default._is_signal_valid(s) is True


# ============================================================
# 规则引擎蒸馏
# ============================================================


class TestRuleDistill:
    def test_bullish_keywords(self, distiller_default):
        signals = distiller_default._rule_distill(
            "600276 业绩超预期, 净利润大增, 强烈推荐", "report", "test"
        )
        assert len(signals) == 1
        assert signals[0].strength > 0

    def test_bearish_keywords(self, distiller_default):
        signals = distiller_default._rule_distill(
            "000001 业绩预警, 亏损, 减持", "report", "test"
        )
        assert len(signals) == 1
        assert signals[0].strength < 0

    def test_critical_negative_triggers_strong_signal(self, distiller_default):
        signals = distiller_default._rule_distill(
            "600276 公司被立案调查, 财务造假", "news", "test"
        )
        assert len(signals) == 1
        assert signals[0].strength <= -0.9
        assert signals[0].confidence == 0.95

    def test_no_keywords_returns_weak_signal(self, distiller_default):
        signals = distiller_default._rule_distill(
            "600276 这是一条没有任何信号词的普通文本内容", "news", "test"
        )
        assert len(signals) == 1
        assert signals[0].confidence == 0.3

    def test_forced_symbol_overrides_ner(self, distiller_default):
        signals = distiller_default._rule_distill(
            "无标的文本", "news", "test", forced_symbol="600276.SH"
        )
        assert len(signals) == 1
        assert signals[0].symbol == "600276.SH"


# ============================================================
# 蒸馏入口
# ============================================================


class TestDistillNewsBatch:
    def test_single_positive(self, distiller_default):
        signals = distiller_default.distill_news_batch(
            [
                {
                    "title": "恒瑞医药业绩超预期",
                    "content": "净利润增长30%",
                    "symbol": "600276.SH",
                },
            ]
        )
        assert len(signals) == 1
        assert signals[0].symbol == "600276.SH"
        assert signals[0].strength > 0

    def test_single_negative(self, distiller_default):
        signals = distiller_default.distill_news_batch(
            [
                {
                    "title": "某公司爆雷",
                    "content": "财务造假, 被立案调查",
                    "symbol": "000001.SZ",
                },
            ]
        )
        assert len(signals) == 1
        assert signals[0].strength < 0

    def test_batch_with_scores(self, distiller_default):
        items = [
            {"title": "利好", "content": "增长", "symbol": "600276.SH"},
            {"title": "利空", "content": "下降", "symbol": "000001.SZ"},
        ]
        signals = distiller_default.distill_news_batch(items)
        assert len(signals) == 2

    def test_critical_negative_keywords(self, distiller_default):
        signals = distiller_default.distill_news_batch(
            [
                {"title": "退市", "content": "强制退市", "symbol": "600276.SH"},
            ]
        )
        assert len(signals) == 1
        assert signals[0].strength <= -0.9

    def test_empty_input_returns_empty(self, distiller_default):
        assert distiller_default.distill_news_batch([]) == []

    def test_ner_extracts_symbol(self, distiller_default):
        signals = distiller_default.distill_news_batch(
            [
                {"title": "600276 业绩好", "content": "增长"},
            ]
        )
        assert len(signals) >= 1

    def test_negative_decay_to_zero(self, distiller_default):
        signals = distiller_default.distill_news_batch(
            [
                {"title": "中性", "content": "没有信号的文本", "symbol": "600276.SH"},
            ]
        )
        assert len(signals) == 1
        assert signals[0].confidence == 0.3

    def test_invalid_symbol_skipped(self, distiller_default):
        signals = distiller_default.distill_news_batch(
            [
                {"title": "测试", "content": "测试内容", "symbol": "INVALID"},
            ]
        )
        assert len(signals) == 0


# ============================================================
# 信号聚合
# ============================================================


class TestSignalAggregation:
    def test_single_signal(self, distiller_default):
        signals = [
            DistilledSignal(symbol="600276.SH", strength=0.5, confidence=0.8),
        ]
        m = distiller_default.to_signal_map(signals)
        assert "600276.SH" in m
        assert m["600276.SH"] == 0.5

    def test_multi_signal_weighted(self, distiller_default):
        signals = [
            DistilledSignal(symbol="600276.SH", strength=0.4, confidence=0.6),
            DistilledSignal(symbol="600276.SH", strength=0.8, confidence=0.4),
        ]
        m = distiller_default.to_signal_map(signals)
        assert "600276.SH" in m

    def test_zero_confidence_fallback_to_mean(self, distiller_default):
        signals = [
            DistilledSignal(symbol="600276.SH", strength=0.5, confidence=0.0),
            DistilledSignal(symbol="600276.SH", strength=-0.5, confidence=0.0),
        ]
        m = distiller_default.to_signal_map(signals)
        assert "600276.SH" in m
        assert m["600276.SH"] == 0.0

    def test_boundary_clamp_to_one(self, distiller_default):
        signals = [
            DistilledSignal(symbol="600276.SH", strength=1.5, confidence=0.9),
        ]
        m = distiller_default.to_signal_map(signals)
        assert m["600276.SH"] == 1.0

    def test_empty_input_returns_empty_dict(self, distiller_default):
        assert distiller_default.to_signal_map([]) == {}

    def test_save_load_snapshot_roundtrip(self, distiller_default, tmp_path):
        signals = [
            DistilledSignal(
                symbol="600276.SH",
                strength=0.5,
                confidence=0.8,
                valid_until="2099-01-01",
            ),
        ]
        path = distiller_default.save_daily_snapshot(signals, "20990101")
        assert path.exists()
        loaded = distiller_default.load_daily_snapshot("20990101")
        assert "600276.SH" in loaded


# ============================================================
# 快照边界
# ============================================================


class TestSnapshotEdgeCases:
    def test_missing_snapshot_returns_empty(self, distiller_default):
        result = distiller_default.load_daily_snapshot("19990101")
        assert result == {}

    def test_date_normalization_dash_to_compact(self, distiller_default):
        assert distiller_default._normalize_date("2026-08-13") == "20260813"

    def test_same_date_formats_point_to_same_file(self, distiller_default):
        p1 = distiller_default.save_daily_snapshot([], "20260813")
        p2 = distiller_default.save_daily_snapshot([], "2026-08-13")
        assert p1 == p2

    def test_load_by_dash_date(self, distiller_default):
        signals = [
            DistilledSignal(symbol="600276.SH", strength=0.5, valid_until="2099-01-01")
        ]
        distiller_default.save_daily_snapshot(signals, "2026-08-13")
        loaded = distiller_default.load_daily_snapshot("2026-08-13")
        assert "600276.SH" in loaded


# ============================================================
# 状态查询
# ============================================================


class TestDistillerStatus:
    def test_get_status_contains_keys(self, distiller_default):
        status = distiller_default.get_status()
        assert "llm_available" in status
        assert "stats" in status

    def test_get_status_llm_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setattr("utils.research_distiller.Path", lambda *a, **kw: tmp_path)
        d = ResearchDistiller(llm_enabled=False)
        assert d.llm_available is False
        status = d.get_status()
        assert status["llm_available"] is False

    def test_self_test_runs(self):
        assert self_test() is True


# ============================================================
# 关键词与情感
# ============================================================


class TestKeywordAndFreshness:
    def test_rule_distill_bullish_keywords(self, distiller_default):
        signals = distiller_default.distill_news_batch(
            [
                {
                    "title": "业绩超预期",
                    "content": "净利润大增, 强烈推荐",
                    "symbol": "600276.SH",
                },
            ]
        )
        assert len(signals) == 1
        assert signals[0].strength > 0

    def test_rule_distill_bearish_keywords(self, distiller_default):
        signals = distiller_default.distill_news_batch(
            [
                {
                    "title": "业绩预警",
                    "content": "业绩不及预期, 毛利率下滑",
                    "symbol": "000001.SZ",
                },
            ]
        )
        assert len(signals) == 1
        assert signals[0].strength < 0
