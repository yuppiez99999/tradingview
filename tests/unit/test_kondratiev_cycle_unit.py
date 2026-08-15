"""kondratiev_cycle 单元测试.

被测模块: utils/kondratiev_cycle.py
覆盖目标: >=90%

测试康波周期阶段判定、行业轮动、大宗商品信号、十五五交叠、报告生成。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.kondratiev_cycle import (  # noqa: E402
    FIFTEEN_FIVE_KONDRATIEV_OVERLAY,
    KONDRATIEV_WAVE_6,
    KondratievCycleAnalyzer,
    KondratievPhase,
)


class KondratievCycleTest:
    """kondratiev_cycle 单元测试."""

    # ------ KondratievPhase 常量 ------
    def test_phase_constants(self):
        assert KondratievPhase.RECESSION == "衰退期"
        assert KondratievPhase.RECOVERY == "复苏期"
        assert KondratievPhase.PROSPERITY == "繁荣期"
        assert KondratievPhase.STAGFLATION == "滞胀期"

    # ------ KONDRATIEV_WAVE_6 结构 ------
    def test_wave6_structure(self):
        assert KONDRATIEV_WAVE_6["start_year"] == 2023
        assert KONDRATIEV_WAVE_6["expected_peak"] == 2038
        assert KONDRATIEV_WAVE_6["expected_end"] == 2058
        assert len(KONDRATIEV_WAVE_6["core_drivers"]) == 6
        est = KONDRATIEV_WAVE_6["current_phase_estimate"]
        assert est["phase"] == KondratievPhase.RECOVERY
        assert 0 <= est["progress_pct"] <= 100

    def test_wave6_phase_allocation_has_all_phases(self):
        alloc = KONDRATIEV_WAVE_6["phase_allocation"]
        for phase in ["复苏期", "繁荣期", "滞胀期", "衰退期"]:
            assert phase in alloc
            assert "sectors" in alloc[phase]
            assert "commodities" in alloc[phase]
            assert "style" in alloc[phase]
            assert "risk_level" in alloc[phase]

    # ------ FIFTEEN_FIVE_KONDRATIEV_OVERLAY 结构 ------
    def test_overlay_structure(self):
        assert FIFTEEN_FIVE_KONDRATIEV_OVERLAY["period"] == "2026-2030"
        assert FIFTEEN_FIVE_KONDRATIEV_OVERLAY["total_fifteen_weight"] == 1.0
        assert len(FIFTEEN_FIVE_KONDRATIEV_OVERLAY["synergy_sectors"]) == 6
        for s in FIFTEEN_FIVE_KONDRATIEV_OVERLAY["synergy_sectors"]:
            assert "sector" in s
            assert "fifteen_weight" in s
            assert "kondratiev_score" in s
            assert "rationale" in s

    # ------ __init__ ------
    def test_init_defaults(self):
        a = KondratievCycleAnalyzer()
        assert a.data_source is None
        assert a.wave_config is KONDRATIEV_WAVE_6
        assert a.overlay is FIFTEEN_FIVE_KONDRATIEV_OVERLAY

    def test_init_with_data_source(self):
        ds = object()
        a = KondratievCycleAnalyzer(data_source=ds)
        assert a.data_source is ds

    # ------ get_current_phase ------
    def test_get_current_phase_structure(self):
        a = KondratievCycleAnalyzer()
        phase = a.get_current_phase()
        assert phase["phase"] == KondratievPhase.RECOVERY
        assert phase["phase_name_cn"] == KondratievPhase.RECOVERY
        assert phase["wave"] == KONDRATIEV_WAVE_6["wave_label"]
        assert "progress_pct" in phase
        assert "confidence" in phase
        assert "next_phase" in phase
        assert "estimated_transition" in phase
        assert isinstance(phase["recommended_sectors"], list)
        assert isinstance(phase["recommended_commodities"], list)
        assert isinstance(phase["basis"], list)

    def test_get_current_phase_recovery_sectors(self):
        a = KondratievCycleAnalyzer()
        phase = a.get_current_phase()
        # 复苏期推荐板块
        assert "AI算力" in phase["recommended_sectors"]
        assert "半导体" in phase["recommended_sectors"]
        assert "铜" in phase["recommended_commodities"]

    # ------ _get_next_phase ------
    def test_get_next_phase_recovery_to_prosperity(self):
        a = KondratievCycleAnalyzer()
        assert a._get_next_phase(KondratievPhase.RECOVERY) == KondratievPhase.PROSPERITY

    def test_get_next_phase_prosperity_to_stagflation(self):
        a = KondratievCycleAnalyzer()
        assert a._get_next_phase(KondratievPhase.PROSPERITY) == KondratievPhase.STAGFLATION

    def test_get_next_phase_stagflation_to_recession(self):
        a = KondratievCycleAnalyzer()
        assert a._get_next_phase(KondratievPhase.STAGFLATION) == KondratievPhase.RECESSION

    def test_get_next_phase_recession_to_recovery(self):
        a = KondratievCycleAnalyzer()
        assert a._get_next_phase(KondratievPhase.RECESSION) == KondratievPhase.RECOVERY

    def test_get_next_phase_unknown_fallback(self):
        a = KondratievCycleAnalyzer()
        # 未知阶段回退到繁荣期
        assert a._get_next_phase("未知阶段") == KondratievPhase.PROSPERITY

    # ------ _estimate_transition_date ------
    def test_estimate_transition_date_format(self):
        a = KondratievCycleAnalyzer()
        result = a._estimate_transition_date(75)
        assert result.endswith("年前后")

    def test_estimate_transition_date_zero_progress(self):
        a = KondratievCycleAnalyzer()
        # progress=0 → remaining=100 → years_left=4
        result = a._estimate_transition_date(0)
        assert "年前后" in result

    def test_estimate_transition_date_full_progress(self):
        a = KondratievCycleAnalyzer()
        # progress=100 → remaining=0 → years_left=max(1,0)=1
        result = a._estimate_transition_date(100)
        assert "年前后" in result

    def test_estimate_transition_date_high_progress(self):
        a = KondratievCycleAnalyzer()
        # progress=99 → remaining=1 → years_left=max(1, 0.04)=1
        result = a._estimate_transition_date(99)
        assert "年前后" in result

    # ------ get_sector_allocation ------
    def test_get_sector_allocation_returns_list(self):
        a = KondratievCycleAnalyzer()
        result = a.get_sector_allocation()
        assert isinstance(result, list)
        assert len(result) > 0

    def test_get_sector_allocation_item_structure(self):
        a = KondratievCycleAnalyzer()
        result = a.get_sector_allocation()
        for item in result:
            assert "sector" in item
            assert "kondratiev_phase" in item
            assert "kondratiev_favorability" in item
            assert "fifteen_five_weight" in item
            assert "combined_score" in item
            assert "recommendation" in item
            assert item["recommendation"] in ("超配", "标配", "低配")

    def test_get_sector_allocation_sorted_by_combined_score(self):
        a = KondratievCycleAnalyzer()
        result = a.get_sector_allocation()
        scores = [item["combined_score"] for item in result]
        assert scores == sorted(scores, reverse=True)

    def test_get_sector_allocation_recovery_phase(self):
        a = KondratievCycleAnalyzer()
        result = a.get_sector_allocation()
        # 复苏期 sectors
        expected_sectors = KONDRATIEV_WAVE_6["phase_allocation"]["复苏期"]["sectors"]
        assert {item["sector"] for item in result} == set(expected_sectors)
        for item in result:
            assert item["kondratiev_phase"] == KondratievPhase.RECOVERY

    # ------ get_commodity_signal ------
    def test_get_commodity_signal_returns_list(self):
        a = KondratievCycleAnalyzer()
        result = a.get_commodity_signals()
        assert isinstance(result, list)
        assert len(result) == 6  # 6 个商品

    def test_get_commodity_signal_item_structure(self):
        a = KondratievCycleAnalyzer()
        result = a.get_commodity_signals()
        for item in result:
            assert "name" in item
            assert "driver" in item
            assert "phase_sensitivity" in item
            assert "kondratiev_recommendation" in item
            assert "current_signal" in item
            assert item["kondratiev_recommendation"] in ("推荐", "观望")

    def test_get_commodity_signal_recovery_recommended(self):
        a = KondratievCycleAnalyzer()
        result = a.get_commodity_signals()
        # 复苏期推荐 铜/锡/白银
        recommended = {item["name"] for item in result if item["kondratiev_recommendation"] == "推荐"}
        assert "铜" in recommended
        assert "锡" in recommended
        assert "白银" in recommended

    def test_get_commodity_signal_non_recommended(self):
        a = KondratievCycleAnalyzer()
        result = a.get_commodity_signals()
        # 黄金/原油/铝 不在复苏期推荐列表
        watch = {item["name"] for item in result if item["kondratiev_recommendation"] == "观望"}
        assert "黄金" in watch
        assert "原油" in watch
        assert "铝" in watch

    def test_get_commodity_signal_names(self):
        a = KondratievCycleAnalyzer()
        result = a.get_commodity_signals()
        names = {item["name"] for item in result}
        assert names == {"铜", "锡", "铝", "黄金", "白银", "原油"}

    # ------ get_fifteen_five_overlay ------
    def test_get_fifteen_five_overlay_structure(self):
        a = KondratievCycleAnalyzer()
        overlay = a.get_fifteen_five_overlay()
        assert overlay["period"] == "2026-2030"
        assert overlay["current_kondratiev_phase"] == KondratievPhase.RECOVERY
        assert "kondratiev_progress" in overlay
        assert "synergy_conclusion" in overlay
        assert "investment_implication" in overlay
        assert isinstance(overlay["synergy_sectors"], list)

    def test_get_fifteen_five_overlay_contains_base_keys(self):
        a = KondratievCycleAnalyzer()
        overlay = a.get_fifteen_five_overlay()
        # 应包含原始 overlay 的所有键
        for key in FIFTEEN_FIVE_KONDRATIEV_OVERLAY:
            assert key in overlay

    # ------ generate_report ------
    def test_generate_report_returns_string(self):
        a = KondratievCycleAnalyzer()
        report = a.generate_report()
        assert isinstance(report, str)
        assert "康波周期" in report
        assert "十五五" in report

    def test_generate_report_contains_sections(self):
        a = KondratievCycleAnalyzer()
        report = a.generate_report()
        assert "一、康波周期当前阶段" in report
        assert "二、行业配置建议" in report
        assert "三、大宗商品" in report
        assert "四、十五五规划" in report

    def test_generate_report_contains_phase_info(self):
        a = KondratievCycleAnalyzer()
        report = a.generate_report()
        assert KondratievPhase.RECOVERY in report
        assert "复苏期" in report

    def test_generate_report_save_to_file(self, tmp_path):
        a = KondratievCycleAnalyzer()
        report = a.generate_report(save_dir=str(tmp_path))
        # 文件已写入
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].suffix == ".md"
        # 返回内容与文件内容一致
        assert files[0].read_text(encoding="utf-8") == report

    def test_generate_report_save_creates_dir(self, tmp_path):
        a = KondratievCycleAnalyzer()
        nested = tmp_path / "nested" / "dir"
        a.generate_report(save_dir=str(nested))
        assert nested.exists()
        files = list(nested.iterdir())
        assert len(files) == 1

    # ------ 端到端: 全流程 ------
    def test_full_workflow(self):
        a = KondratievCycleAnalyzer()
        phase = a.get_current_phase()
        sectors = a.get_sector_allocation()
        commodities = a.get_commodity_signals()
        overlay = a.get_fifteen_five_overlay()
        report = a.generate_report()
        # 各环节产出一致: 当前阶段贯穿
        assert phase["phase"] == KondratievPhase.RECOVERY
        assert all(s["kondratiev_phase"] == phase["phase"] for s in sectors)
        assert overlay["current_kondratiev_phase"] == phase["phase"]
        assert phase["phase"] in report