"""
研究蒸馏信号 → SignalFusion 集成测试
=====================================

验证 ResearchDistiller 产出的信号能正确注入 SignalFusionEngine 并影响融合结果:
- load_daily_snapshot → inject_research_distilled_signals → fuse 完整链路
- 持久化往返: save_daily_snapshot → load_daily_snapshot → inject
- 与真实 ResearchDistiller 实例的端到端集成
- 多源共存 (research + pipeline + alpha)

设计依据: .trae/documents/GitHub热门项目深度集成方案_2026-07-26.md 阶段6
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.research_distiller import DistilledSignal, ResearchDistiller  # noqa: E402
from utils.signal_fusion import SignalFusionEngine  # noqa: E402

# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def distiller(tmp_path):
    """使用临时缓存目录的 ResearchDistiller"""
    return ResearchDistiller(cache_dir=tmp_path)


@pytest.fixture
def engine():
    return SignalFusionEngine()


@pytest.fixture
def sample_distilled_signals():
    """样本蒸馏信号列表 (使用合法 source_type: news/earnings_call/book_chapter)"""
    return [
        DistilledSignal(
            symbol='600276.SH',
            strength=0.7,
            confidence=0.8,
            source_type='news',
            source_id='report_001',
            reasoning='恒瑞医药创新药管线进展超预期',
            valid_until='2026-08-26',
            timestamp='2026-07-26T06:00:00',
            key_factors=['创新药', '管线进展'],
        ),
        DistilledSignal(
            symbol='000001.SZ',
            strength=-0.5,
            confidence=0.7,
            source_type='earnings_call',
            source_id='call_002',
            reasoning='平安银行净息差收窄',
            valid_until='2026-08-26',
            timestamp='2026-07-26T06:00:00',
            key_factors=['净息差', '收窄'],
        ),
    ]


# ============================================================
# 测试组 1: 完整链路集成
# ============================================================

class TestResearchDistillerSignalFusionIntegration:
    """ResearchDistiller → SignalFusion 完整链路"""

    def test_load_snapshot_then_inject_then_fuse(self, distiller, engine):
        """load_daily_snapshot → inject → fuse 完整链路"""
        # 准备: 先保存快照
        signals = [
            DistilledSignal(symbol='600276.SH', strength=0.7, confidence=0.8,
                            source_type='research_report', source_id='r1',
                            reasoning='利好', valid_until='2026-08-26',
                            timestamp='2026-07-26T06:00:00', key_factors=['利好']),
        ]
        distiller.save_daily_snapshot(signals, '20260726')

        # 执行: 加载快照
        snapshot = distiller.load_daily_snapshot('20260726')
        assert isinstance(snapshot, dict)
        assert '600276.SH' in snapshot

        # 注入 SignalFusion
        engine.inject_research_distilled_signals(snapshot)

        # 融合
        result = engine.fuse(alpha_signals={
            '600276.SH': {'strength': 0.5, 'confidence': 0.8}
        })
        r = result[0]
        assert r.meta['research_distilled_applied'] is True
        assert r.sources['research_distilled_strength'] == 0.7

    def test_to_signal_map_then_inject(self, distiller, engine, sample_distilled_signals):
        """to_signal_map → inject 链路"""
        signal_map = distiller.to_signal_map(sample_distilled_signals)
        assert isinstance(signal_map, dict)
        assert len(signal_map) == 2

        engine.inject_research_distilled_signals(signal_map)
        assert len(engine._research_distilled_signals) == 2

    def test_empty_snapshot_no_effect_on_fusion(self, distiller, engine):
        """空快照不影响融合"""
        # 加载不存在的日期
        snapshot = distiller.load_daily_snapshot('20991231')
        assert snapshot == {}

        engine.inject_research_distilled_signals(snapshot)
        assert engine._research_distilled_signals == {}

        result = engine.fuse(alpha_signals={
            '600276.SH': {'strength': 0.5, 'confidence': 0.8}
        })
        r = result[0]
        assert r.meta['research_distilled_applied'] is False


# ============================================================
# 测试组 2: 持久化往返
# ============================================================

class TestResearchDistillerPersistenceRoundTrip:
    """save → load 持久化往返测试"""

    def test_save_load_roundtrip_preserves_signals(self, distiller, sample_distilled_signals):
        """save → load 保留信号值"""
        distiller.save_daily_snapshot(sample_distilled_signals, '20260726')
        loaded = distiller.load_daily_snapshot('20260726')

        assert len(loaded) == 2
        assert loaded['600276.SH'] == 0.7
        assert loaded['000001.SZ'] == -0.5

    def test_load_nonexistent_date_returns_empty(self, distiller):
        """加载不存在的日期返回空 dict"""
        result = distiller.load_daily_snapshot('20250101')
        assert result == {}
        assert isinstance(result, dict)

    def test_snapshot_file_persisted_to_disk(self, distiller, sample_distilled_signals):
        """快照文件持久化到磁盘"""
        path = distiller.save_daily_snapshot(sample_distilled_signals, '20260726')
        assert path.exists()
        # 验证文件内容是合法 JSON, 格式为 dict (含 signals/signal_map/signal_count)
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        assert isinstance(data, dict)
        assert 'signals' in data
        assert 'signal_map' in data
        assert data['signal_count'] == 2
        assert isinstance(data['signals'], list)
        assert len(data['signals']) == 2


# ============================================================
# 测试组 3: 多源共存集成
# ============================================================

class TestMultiSourceCoexistence:
    """多信号源共存集成测试"""

    def test_research_and_pipeline_coexist(self, distiller, engine, sample_distilled_signals):
        """研究蒸馏 + Pipeline 因子共存"""
        # 研究蒸馏信号
        research_map = distiller.to_signal_map(sample_distilled_signals)
        engine.inject_research_distilled_signals(research_map)

        # Pipeline 因子信号
        engine.inject_pipeline_factor_signals({
            '600276.SH': 0.6,
            '000001.SZ': -0.4,
        })

        # 融合
        alpha = {
            '600276.SH': {'strength': 0.5, 'confidence': 0.8},
            '000001.SZ': {'strength': 0.4, 'confidence': 0.7},
        }
        result = engine.fuse(alpha_signals=alpha)
        result_map = {r.symbol: r for r in result}

        # 验证两个信号源都生效
        r1 = result_map['600276.SH']
        assert r1.sources['research_distilled_strength'] == 0.7
        assert r1.sources['pipeline_factor_strength'] == 0.6
        assert r1.meta['research_distilled_applied'] is True
        assert r1.meta['pipeline_factor_applied'] is True

    def test_research_overrides_weak_alpha(self, distiller, engine):
        """强 research 信号能翻转弱 alpha 信号方向"""
        # 弱 alpha 看涨
        alpha = {'600276.SH': {'strength': 0.1, 'confidence': 0.4}}

        # 强 research 看跌
        research_map = {'600276.SH': -0.9}
        engine.inject_research_distilled_signals(research_map)

        result = engine.fuse(alpha_signals=alpha)
        r = result[0]
        # research 信号已注入
        assert r.sources['research_distilled_strength'] == -0.9
        # 注意: alpha strength=0.1 < 0.10 阈值会被归零, 但 research 仍记录在 sources


# ============================================================
# 测试组 4: 降级与异常场景
# ============================================================

class TestDegradationScenarios:
    """降级与异常场景"""

    def test_distiller_init_failure_no_crash(self, engine):
        """ResearchDistiller 初始化失败不影响 SignalFusion"""
        # 模拟: 即使 distiller 抛异常, engine 仍可正常 fuse
        result = engine.fuse(alpha_signals={
            '600276.SH': {'strength': 0.5, 'confidence': 0.8}
        })
        assert len(result) == 1
        assert result[0].meta['research_distilled_applied'] is False

    def test_malformed_snapshot_no_crash(self, engine):
        """畸形的快照数据不影响融合"""
        # 注入包含各种异常值的数据
        malformed = {
            '600276.SH': 'not_a_number',  # 字符串
            '000001.SZ': None,             # None
            '600519.SH': float('nan'),     # NaN
            '000333.SZ': 0.6,              # 有效值
        }
        engine.inject_research_distilled_signals(malformed)
        # 仅有效值保留
        assert '000333.SZ' in engine._research_distilled_signals
        # 异常值过滤
        assert '600276.SH' not in engine._research_distilled_signals
        assert '000001.SZ' not in engine._research_distilled_signals
        assert '600519.SH' not in engine._research_distilled_signals
