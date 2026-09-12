"""test_research_distiller_unit.py — ResearchDistiller 单元测试

测试范围 (15 个测试, 覆盖任务要求 7 大类):
  - DistilledSignal dataclass: NaN 防御 / 边界裁剪 / 序列化 (4 个)
  - ResearchDistiller 初始化 / 模块加载 (1 个)
  - distill_news_batch(): 规则引擎兜底 / 关键词匹配 (3 个)
  - distill_earnings_call(): LLM 降级到规则引擎 (2 个)
  - 缺失文件 / 异常输入的安全降级 (1 个)
  - to_signal_map(): 多信号聚合 (按 confidence 加权平均) (1 个)
  - save/load_daily_snapshot(): 持久化读写一致性 (2 个)
  - 标的代码 NER (1 个)

设计原则:
  - 单模块测试, 全 Mock, <1s 完成 (本套件 ~0.5s)
  - 不依赖 LLM 调用 (ResearchDistiller(llm_enabled=False) 强制走规则引擎)
  - Python 3.8.9 兼容 (from __future__ import annotations)

集成日期: 2026-07-26
集成批次: GitHub 周榜热门项目深度集成 (第二批) - 阶段 4
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from utils.datetime_utils import now_bj
from utils.research_distiller import (
    DistilledSignal,
    ResearchDistiller,
    _safe_float,
)

# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def distiller_no_llm(tmp_path):
    """LLM 禁用的 ResearchDistiller 实例 (强制走规则引擎)

    使用 tmp_path 隔离 cache_dir, 避免污染真实 data/distilled_signals/
    """
    return ResearchDistiller(
        cache_dir=tmp_path / "distilled_signals",
        llm_enabled=False,
    )


@pytest.fixture
def sample_earnings_call_path():
    """sample_earnings_call_transcript.txt fixture 路径"""
    return (
        Path(__file__).resolve().parent.parent
        / "fixtures"
        / "sample_earnings_call_transcript.txt"
    )


# ============================================================
# DistilledSignal 数据结构测试 (4 个)
# ============================================================


class TestDistilledSignal:
    """DistilledSignal dataclass 防御性测试"""

    @pytest.mark.unit
    def test_distilled_signal_defaults_and_source_type_normalization(self):
        """默认值 + 非法 source_type 降级为 news"""
        s = DistilledSignal(symbol="600276.SH")
        assert s.symbol == "600276.SH"
        assert s.strength == 0.0
        assert s.confidence == 0.0
        assert s.source_type == "news"
        assert s.key_factors == []
        assert s.timestamp != ""
        assert s.valid_until == ""

        # 非法 source_type 降级
        bad = DistilledSignal(symbol="X", source_type="invalid")
        assert bad.source_type == "news"

        # 合法 type 不变
        for t in ("report", "earnings_call", "book", "news"):
            ok = DistilledSignal(symbol="X", source_type=t)
            assert ok.source_type == t

    @pytest.mark.unit
    def test_distilled_signal_nan_inf_defense(self):
        """NaN/Inf strength 与 confidence 归零 (P0 防御: 与 signal_fusion 一致)"""
        s = DistilledSignal(symbol="X", strength=float("nan"), confidence=float("inf"))
        assert s.strength == 0.0  # NaN 归零
        assert s.confidence == 0.0  # Inf 归零

        s2 = DistilledSignal(
            symbol="Y", strength=float("-inf"), confidence=float("nan")
        )
        assert s2.strength == 0.0
        assert s2.confidence == 0.0

    @pytest.mark.unit
    def test_distilled_signal_boundary_clipping(self):
        """strength / confidence 超出边界裁剪到 [-1,1] / [0,1]"""
        # strength 上界 + 下界
        assert DistilledSignal(symbol="X", strength=2.5).strength == 1.0
        assert DistilledSignal(symbol="X", strength=-1.8).strength == -1.0
        # confidence 上界 + 下界
        assert DistilledSignal(symbol="X", confidence=1.5).confidence == 1.0
        assert DistilledSignal(symbol="X", confidence=-0.3).confidence == 0.0

    @pytest.mark.unit
    def test_distilled_signal_serialization_roundtrip_and_tolerance(self):
        """to_dict / from_dict 往返一致 + from_dict 容错"""
        original = DistilledSignal(
            symbol="600276.SH",
            strength=0.65,
            confidence=0.82,
            source_type="report",
            source_id="r.pdf",
            reasoning="业绩超预期",
            valid_until="2026-08-02",
            timestamp="2026-07-26T10:00:00",
            key_factors=["ROE提升", "毛利率扩张"],
        )
        d = original.to_dict()
        assert d["symbol"] == "600276.SH"
        assert d["strength"] == 0.65
        assert d["key_factors"] == ["ROE提升", "毛利率扩张"]

        # 反序列化往返
        restored = DistilledSignal.from_dict(d)
        assert restored.symbol == original.symbol
        assert restored.strength == original.strength
        assert restored.source_type == original.source_type
        assert restored.key_factors == original.key_factors

        # from_dict 容错: 字段缺失 / 字符串数值 / 空 dict
        assert DistilledSignal.from_dict({"symbol": "X"}).strength == 0.0
        assert (
            DistilledSignal.from_dict({"symbol": "Y", "strength": "0.5"}).strength
            == 0.5
        )
        assert DistilledSignal.from_dict({}).symbol == ""


# ============================================================
# ResearchDistiller 初始化测试 (1 个, 合并 init + safe_float)
# ============================================================


class TestResearchDistillerInit:
    """ResearchDistiller 初始化 + _safe_float 工具函数"""

    @pytest.mark.unit
    def test_distiller_init_and_safe_float(self, tmp_path):
        """init: LLM 禁用 / 自定义 cache_dir / get_status + _safe_float 防御"""
        # _safe_float 防御
        assert _safe_float(0.5) == 0.5
        assert _safe_float(float("nan")) == 0.0
        assert _safe_float(float("inf")) == 0.0
        assert _safe_float(None, default=-1.0) == -1.0
        assert _safe_float("abc") == 0.0

        # init
        custom = tmp_path / "custom_cache"
        d = ResearchDistiller(cache_dir=custom, llm_enabled=False)
        assert d.llm_available is False
        assert custom.exists()
        assert str(d.cache_dir) == str(custom)

        # get_status 返回完整字段
        status = d.get_status()
        assert "llm_available" in status
        assert "cache_dir" in status
        assert "name_dict_size" in status
        assert "stats" in status


# ============================================================
# distill_news_batch 规则引擎测试 (3 个)
# ============================================================


class TestDistillNewsBatch:
    """新闻批量蒸馏 (规则引擎兜底)"""

    @pytest.mark.unit
    def test_distill_news_batch_strong_positive(self, distiller_no_llm):
        """强正面关键词触发看涨信号 (强度 > 0)"""
        signals = distiller_no_llm.distill_news_batch(
            [
                {
                    "title": "恒瑞医药业绩超预期",
                    "content": "净利润增长 30%, 强烈推荐, 目标价上调",
                    "symbol": "600276.SH",
                },
            ]
        )
        assert len(signals) == 1
        s = signals[0]
        assert s.symbol == "600276.SH"
        assert s.source_type == "news"
        assert s.strength > 0  # 看涨
        assert s.confidence > 0.4
        # key_factors 应包含强正面词
        assert any("强烈推荐" in f or "超预期" in f for f in s.key_factors)
        # news 类型 valid_until 为 1 天后
        valid_date = datetime.strptime(s.valid_until, "%Y-%m-%d").date()
        expected = (now_bj() + timedelta(days=1)).date()
        assert valid_date == expected

    @pytest.mark.unit
    def test_distill_news_batch_critical_negative(self, distiller_no_llm):
        """重大负面关键词触发强看跌信号 (强度 = -0.9, 类 veto)"""
        signals = distiller_no_llm.distill_news_batch(
            [
                {
                    "title": "某公司被立案调查",
                    "content": "证监会处罚, 涉嫌财务造假",
                    "symbol": "000001.SZ",
                },
            ]
        )
        assert len(signals) == 1
        s = signals[0]
        assert s.symbol == "000001.SZ"
        assert s.strength == -0.9  # 重大负面强制 -0.9
        assert s.confidence == 0.95
        assert any("立案调查" in f or "财务造假" in f for f in s.key_factors)

    @pytest.mark.unit
    def test_distill_news_batch_safety_and_ner(self, distiller_no_llm):
        """空输入返回空 + 未指定 symbol 时通过 NER 识别代码"""
        # 空输入 fail-closed
        assert distiller_no_llm.distill_news_batch([]) == []
        assert distiller_no_llm.distill_news_batch(None) == []

        # NER 识别 (无 symbol 字段, 通过文本中的完整代码识别)
        signals = distiller_no_llm.distill_news_batch(
            [
                {"title": "600276.SH 业绩点评", "content": "净利润增长, 买入评级"},
            ]
        )
        assert len(signals) >= 1
        assert any(s.symbol == "600276.SH" for s in signals)


# ============================================================
# distill_earnings_call LLM 降级测试 (2 个)
# ============================================================


class TestDistillEarningsCall:
    """业绩会纪要蒸馏 (LLM 降级到规则引擎)"""

    @pytest.mark.unit
    def test_distill_earnings_call_rule_engine_fallback(
        self,
        distiller_no_llm,
        sample_earnings_call_path,
    ):
        """LLM 禁用时, 业绩会纪要走规则引擎兜底 (forced_symbol 强制指定)"""
        transcript = sample_earnings_call_path.read_text(encoding="utf-8")
        signals = distiller_no_llm.distill_earnings_call(transcript, "600276")
        assert len(signals) == 1
        s = signals[0]
        assert s.symbol == "600276.SH"  # 规范化后带后缀
        assert s.source_type == "earnings_call"
        # 纪要包含 "业绩超预期", "强烈推荐" → 应为看涨
        assert s.strength > 0
        # earnings_call 时效 30 天
        valid_date = datetime.strptime(s.valid_until, "%Y-%m-%d").date()
        expected = (now_bj() + timedelta(days=30)).date()
        assert valid_date == expected

    @pytest.mark.unit
    def test_distill_earnings_call_invalid_input_safe(self, distiller_no_llm):
        """空纪要 + 非法 symbol 返回空列表 (fail-closed)"""
        assert distiller_no_llm.distill_earnings_call("", "600276.SH") == []
        assert distiller_no_llm.distill_earnings_call("   ", "600276.SH") == []
        assert distiller_no_llm.distill_earnings_call("业绩会内容", "INVALID") == []
        assert distiller_no_llm.distill_earnings_call("业绩会内容", "") == []


# ============================================================
# 缺失文件 / 异常输入的安全降级 (1 个)
# ============================================================


class TestDistillSafety:
    """研报/书籍蒸馏的安全降级 (fail-closed)"""

    @pytest.mark.unit
    def test_distill_report_and_book_safety(self, distiller_no_llm, tmp_path):
        """PDF 不存在 / 书籍不支持的格式 / 书籍文件缺失 均返回空列表"""
        # PDF 文件不存在
        missing_pdf = tmp_path / "nonexistent.pdf"
        assert distiller_no_llm.distill_report(missing_pdf) == []

        # 不支持的文件格式 (.docx)
        bad = tmp_path / "fake.docx"
        bad.write_text("dummy content", encoding="utf-8")
        assert distiller_no_llm.distill_book_chapter(bad) == []

        # 书籍文件不存在
        missing_md = tmp_path / "missing.md"
        assert distiller_no_llm.distill_book_chapter(missing_md) == []


# ============================================================
# to_signal_map 聚合测试 (1 个, 合并 4 个子场景)
# ============================================================


class TestToSignalMap:
    """信号聚合 (按 confidence 加权平均)"""

    @pytest.mark.unit
    def test_to_signal_map_aggregation_scenarios(self, distiller_no_llm):
        """单信号 / 多信号加权 / 全零 confidence / 边界裁剪"""
        # 场景 1: 单信号直接返回
        single = [DistilledSignal(symbol="A", strength=0.6, confidence=0.8)]
        assert distiller_no_llm.to_signal_map(single) == {"A": 0.6}

        # 场景 2: 同标的多信号按 confidence 加权平均
        # (0.8*0.5 + 0.4*0.5) / (0.5+0.5) = 0.6
        multi = [
            DistilledSignal(symbol="B", strength=0.8, confidence=0.5),
            DistilledSignal(symbol="B", strength=0.4, confidence=0.5),
        ]
        result = distiller_no_llm.to_signal_map(multi)
        assert abs(result["B"] - 0.6) < 1e-6

        # 场景 3: 全部 confidence=0 降级为简单平均
        zero_conf = [
            DistilledSignal(symbol="C", strength=0.4, confidence=0.0),
            DistilledSignal(symbol="C", strength=0.8, confidence=0.0),
        ]
        result_zc = distiller_no_llm.to_signal_map(zero_conf)
        assert abs(result_zc["C"] - 0.6) < 1e-6  # (0.4+0.8)/2

        # 场景 4: 聚合结果边界裁剪 (不超过 1.0)
        boundary = [
            DistilledSignal(symbol="D", strength=1.0, confidence=1.0),
            DistilledSignal(symbol="D", strength=1.0, confidence=1.0),
        ]
        assert distiller_no_llm.to_signal_map(boundary)["D"] == 1.0

        # 场景 5: 空输入
        assert distiller_no_llm.to_signal_map([]) == {}


# ============================================================
# save / load_daily_snapshot 持久化测试 (2 个)
# ============================================================


class TestSnapshotPersistence:
    """每日快照持久化读写"""

    @pytest.mark.unit
    def test_save_load_snapshot_consistency(self, distiller_no_llm):
        """保存后加载, signal_map 字典完全一致"""
        signals = [
            DistilledSignal(
                symbol="600276.SH",
                strength=0.6,
                confidence=0.8,
                source_type="report",
                source_id="r1.pdf",
                valid_until=(now_bj() + timedelta(days=7)).strftime("%Y-%m-%d"),
            ),
            DistilledSignal(
                symbol="000001.SZ",
                strength=-0.9,
                confidence=0.95,
                source_type="news",
                source_id="n1",
                valid_until=(now_bj() + timedelta(days=1)).strftime("%Y-%m-%d"),
            ),
        ]
        trade_date = "20260726"
        saved_path = distiller_no_llm.save_daily_snapshot(signals, trade_date)
        assert saved_path.exists()

        # 加载并比对
        loaded = distiller_no_llm.load_daily_snapshot(trade_date)
        expected = distiller_no_llm.to_signal_map(signals)
        assert loaded == expected
        assert "600276.SH" in loaded
        assert "000001.SZ" in loaded

        # 验证 JSON 文件结构
        with open(saved_path, encoding="utf-8") as f:
            payload = json.load(f)
        assert payload["trade_date"] == "20260726"
        assert payload["signal_count"] == 2
        assert isinstance(payload["signals"], list)
        assert isinstance(payload["signal_map"], dict)

    @pytest.mark.unit
    def test_snapshot_safety_and_date_normalization(self, distiller_no_llm):
        """缺失快照返回空 dict + 日期格式自动规范化"""
        # 缺失文件 fail-closed
        assert distiller_no_llm.load_daily_snapshot("20991231") == {}

        # YYYY-MM-DD 应规范化为 YYYYMMDD
        signals = [DistilledSignal(symbol="X", strength=0.5, confidence=0.7)]
        path1 = distiller_no_llm.save_daily_snapshot(signals, "2026-07-26")
        assert "20260726" in path1.name

        # YYYYMMDD 直接使用, 两种格式应指向同一文件
        path2 = distiller_no_llm.save_daily_snapshot(signals, "20260726")
        assert path1.name == path2.name == "distilled_signals_20260726.json"

        # 加载验证
        loaded = distiller_no_llm.load_daily_snapshot("2026-07-26")  # 带 - 格式
        assert "X" in loaded


# ============================================================
# 标的代码 NER 测试 (1 个, 合并 3 个子场景)
# ============================================================


class TestSymbolNER:
    """标的代码 NER 识别"""

    @pytest.mark.unit
    def test_symbol_ner_scenarios(self, distiller_no_llm):
        """代码规范化 + 文本提取 + 名称词典匹配"""
        # 1. 代码规范化
        norm = distiller_no_llm._normalize_symbol
        assert norm("600276") == "600276.SH"  # 沪市主板
        assert norm("000001") == "000001.SZ"  # 深市主板
        assert norm("300750") == "300750.SZ"  # 创业板
        assert norm("688981") == "688981.SH"  # 科创板
        assert norm("600276.sh") == "600276.SH"  # 已带后缀大写化
        assert norm("00700") == "00700.HK"  # 港股 5 位
        assert norm("") == ""  # 非法输入
        assert norm(None) == ""
        assert norm("INVALID") == ""
        assert norm("123") == ""  # 3 位数字不合法

        # 2. 文本提取 (完整代码优先, 裸代码次之)
        assert "600276.SH" in distiller_no_llm._extract_symbols(
            "恒瑞医药(600276.SH)业绩超预期"
        )
        assert "300750.SZ" in distiller_no_llm._extract_symbols(
            "宁德时代 300750 创新高"
        )
        multi = distiller_no_llm._extract_symbols("600276.SH 与 000001.SZ 同时被点名")
        assert "600276.SH" in multi
        assert "000001.SZ" in multi

        # 3. 名称词典匹配 (从 positions.json 加载)
        if not distiller_no_llm._name_to_symbol:
            pytest.skip("positions.json 未加载任何名称映射")
        sample_name = next(iter(distiller_no_llm._name_to_symbol))
        sample_code = distiller_no_llm._name_to_symbol[sample_name]
        syms = distiller_no_llm._extract_symbols(f"{sample_name}今日表现强劲")
        assert sample_code in syms
