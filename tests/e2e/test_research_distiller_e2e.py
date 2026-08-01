"""
研究蒸馏信号 E2E 测试
=====================

端到端验证研究蒸馏信号从离线蒸馏 → 在线注入 → 融合输出的完整链路:
- 真实 ResearchDistiller + SignalFusionEngine 端到端
- 多源融合 (alpha + pipeline + research) 真实场景
- daily_workflow 注入逻辑模拟 (load_daily_snapshot → inject)

设计依据: .trae/documents/GitHub热门项目深度集成方案_2026-07-26.md 阶段6
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.research_distiller import DistilledSignal, ResearchDistiller
from utils.signal_fusion import SignalFusionEngine

# ============================================================
# E2E 测试组 1: 真实端到端链路
# ============================================================

class TestResearchDistillerE2E:
    """端到端: ResearchDistiller → SignalFusion 完整链路"""

    def test_e2e_full_pipeline_with_real_distiller(self, tmp_path):
        """E2E: 真实 ResearchDistiller 蒸馏 → save → load → inject → fuse"""
        # 1. 初始化真实组件
        distiller = ResearchDistiller(cache_dir=tmp_path, llm_enabled=False)
        engine = SignalFusionEngine()

        # 2. 构造蒸馏信号 (模拟离线 06:00 蒸馏产出)
        signals = [
            DistilledSignal(
                symbol='600276.SH',
                strength=0.65,
                confidence=0.8,
                source_type='news',
                source_id='report_hengrui_001',
                reasoning='恒瑞医药创新药管线进展',
                valid_until='2026-08-26',
                timestamp='2026-07-26T06:00:00',
                key_factors=['创新药'],
            ),
            DistilledSignal(
                symbol='600519.SH',
                strength=0.4,
                confidence=0.7,
                source_type='earnings_call',
                source_id='call_moutai_001',
                reasoning='茅台业绩会指引积极',
                valid_until='2026-08-26',
                timestamp='2026-07-26T06:00:00',
                key_factors=['业绩会'],
            ),
        ]

        # 3. 离线蒸馏: save_daily_snapshot
        snapshot_path = distiller.save_daily_snapshot(signals, '20260726')
        assert snapshot_path.exists()

        # 4. 在线加载 (模拟 daily_workflow 07:00 注入)
        snapshot = distiller.load_daily_snapshot('20260726')
        assert len(snapshot) == 2

        # 5. 注入 SignalFusion
        engine.inject_research_distilled_signals(snapshot)
        assert len(engine._research_distilled_signals) == 2

        # 6. 融合
        alpha = {
            '600276.SH': {'strength': 0.5, 'confidence': 0.8},
            '600519.SH': {'strength': 0.6, 'confidence': 0.9},
        }
        result = engine.fuse(alpha_signals=alpha)
        result_map = {r.symbol: r for r in result}

        # 7. 验证融合结果
        r_hengrui = result_map['600276.SH']
        assert r_hengrui.meta['research_distilled_applied'] is True
        assert r_hengrui.sources['research_distilled_strength'] == 0.65
        assert r_hengrui.meta['research_distilled_weight'] == 0.03

        r_moutai = result_map['600519.SH']
        assert r_moutai.meta['research_distilled_applied'] is True
        assert r_moutai.sources['research_distilled_strength'] == 0.4

    def test_e2e_daily_workflow_injection_pattern(self, tmp_path):
        """E2E: 模拟 daily_workflow.py 的注入模式 (load → inject → fuse)"""
        # 模拟 daily_workflow 中的注入块逻辑
        trade_date = '20260726'
        distiller = ResearchDistiller(cache_dir=tmp_path, llm_enabled=False)
        engine = SignalFusionEngine()

        # 预置快照
        distiller.save_daily_snapshot([
            DistilledSignal(
                symbol='000001.SZ', strength=-0.3, confidence=0.6,
                source_type='news', source_id='news_001',
                reasoning='银行利空', valid_until='2026-08-26',
                timestamp='2026-07-26T06:00:00', key_factors=['利空'],
            ),
        ], trade_date)

        # === 模拟 daily_workflow.py 注入块 ===
        research_signals = {}
        try:
            research_signals = distiller.load_daily_snapshot(trade_date)
            if research_signals and engine is not None:
                engine.inject_research_distilled_signals(research_signals)
                applied = True
            else:
                applied = False
        except Exception:
            applied = False

        assert applied is True
        assert len(engine._research_distilled_signals) == 1

        # 融合验证
        result = engine.fuse(alpha_signals={
            '000001.SZ': {'strength': 0.4, 'confidence': 0.7}
        })
        r = result[0]
        assert r.sources['research_distilled_strength'] == -0.3
        assert r.meta['research_distilled_applied'] is True


# ============================================================
# E2E 测试组 2: 降级与异常场景
# ============================================================

class TestResearchDistillerE2EDegradation:
    """E2E 降级场景"""

    def test_e2e_missing_snapshot_degrades_gracefully(self, tmp_path):
        """E2E: 缺失快照时优雅降级 (不影响主流程)"""
        distiller = ResearchDistiller(cache_dir=tmp_path, llm_enabled=False)
        engine = SignalFusionEngine()

        # 加载不存在的快照
        snapshot = distiller.load_daily_snapshot('20991231')
        assert snapshot == {}

        # 注入空快照
        engine.inject_research_distilled_signals(snapshot)
        assert engine._research_distilled_signals == {}

        # 融合仍正常工作
        result = engine.fuse(alpha_signals={
            '600276.SH': {'strength': 0.5, 'confidence': 0.8}
        })
        r = result[0]
        assert r.meta['research_distilled_applied'] is False
        assert r.strength != 0.0  # alpha 信号仍生效

    def test_e2e_distiller_failure_does_not_crash_fusion(self, tmp_path):
        """E2E: ResearchDistiller 失败不崩溃 SignalFusion"""
        engine = SignalFusionEngine()

        # 模拟 daily_workflow 的 try/except 降级
        research_signals = {}
        try:
            # 故意制造异常: 使用损坏的缓存目录
            broken_distiller = ResearchDistiller(cache_dir=Path('/nonexistent/path/xxx'))
            research_signals = broken_distiller.load_daily_snapshot('20260726')
        except Exception:
            research_signals = {}

        # 降级: research_signals 为空, 不影响融合
        engine.inject_research_distilled_signals(research_signals)

        result = engine.fuse(alpha_signals={
            '600276.SH': {'strength': 0.5, 'confidence': 0.8}
        })
        r = result[0]
        assert r.meta['research_distilled_applied'] is False
        assert math.isfinite(r.strength)


# ============================================================
# E2E 测试组 3: 多源融合真实场景
# ============================================================

class TestResearchDistillerE2EMultiSource:
    """E2E 多源融合真实场景"""

    def test_e2e_alpha_pipeline_research_three_sources(self, tmp_path):
        """E2E: alpha + pipeline + research 三源融合"""
        distiller = ResearchDistiller(cache_dir=tmp_path, llm_enabled=False)
        engine = SignalFusionEngine()

        # 研究蒸馏信号
        distiller.save_daily_snapshot([
            DistilledSignal(
                symbol='600276.SH', strength=0.7, confidence=0.8,
                source_type='news', source_id='r1', reasoning='利好',
                valid_until='2026-08-26', timestamp='2026-07-26T06:00:00',
                key_factors=['利好'],
            ),
        ], '20260726')
        research_map = distiller.load_daily_snapshot('20260726')
        engine.inject_research_distilled_signals(research_map)

        # Pipeline 因子信号
        engine.inject_pipeline_factor_signals({'600276.SH': 0.6})

        # Alpha 信号
        alpha = {'600276.SH': {'strength': 0.5, 'confidence': 0.8}}

        # 融合
        result = engine.fuse(alpha_signals=alpha)
        r = result[0]

        # 验证三源都记录在 sources
        assert r.sources['alpha_strength'] == 0.5
        assert r.sources['pipeline_factor_strength'] == 0.6
        assert r.sources['research_distilled_strength'] == 0.7

        # 验证三源都应用
        assert r.meta['pipeline_factor_applied'] is True
        assert r.meta['research_distilled_applied'] is True

        # strength 必须是有限数
        assert math.isfinite(r.strength)
        assert -1.0 <= r.strength <= 1.0


# 导入 math (放在文件末尾避免循环导入问题)
import math
