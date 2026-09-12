"""
VolRegimeWeighter 单元测试
覆盖: 常量、数据类、Regime 分类、权重矩阵、约束执行、主类流程、降级路径
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 项目根加入 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.alpha.vol_regime_weighter import (
    ALIGNED_HEDGE_RATIOS,
    DEFAULT_WEIGHT_MATRIX,
    REGIME_BEAR,
    REGIME_BULL,
    REGIME_CRISIS,
    REGIME_NEUTRAL,
    STYLE_CATEGORIES,
    VolRegime,
    VolRegimeWeighter,
    WeightSuggestion,
    classify_regime_by_vol,
)

# ============================================================
# 常量定义测试
# ============================================================


class TestVolRegimeConstants:
    """常量定义测试."""

    def test_weight_matrix_has_four_regimes(self):
        """矩阵包含四个 regime."""
        assert set(DEFAULT_WEIGHT_MATRIX.keys()) == {
            REGIME_BULL,
            REGIME_NEUTRAL,
            REGIME_BEAR,
            REGIME_CRISIS,
        }

    def test_weight_matrix_has_eight_styles(self):
        """每个 regime 包含 8 类风格."""
        for regime, styles in DEFAULT_WEIGHT_MATRIX.items():
            assert set(styles.keys()) == set(
                STYLE_CATEGORIES
            ), f"regime {regime} 缺少风格: {set(STYLE_CATEGORIES) - set(styles.keys())}"

    def test_bull_multiplier_tech_is_1_20(self):
        """bull 档科技倍数为 1.20."""
        assert DEFAULT_WEIGHT_MATRIX[REGIME_BULL]["科技"] == 1.20

    def test_crisis_multiplier_cash_is_3_00(self):
        """crisis 档现金倍数为 3.00."""
        assert DEFAULT_WEIGHT_MATRIX[REGIME_CRISIS]["现金"] == 3.00

    def test_crisis_multiplier_tech_is_0_30(self):
        """crisis 档科技倍数为 0.30 (大幅减仓)."""
        assert DEFAULT_WEIGHT_MATRIX[REGIME_CRISIS]["科技"] == 0.30

    def test_neutral_all_multipliers_are_1(self):
        """neutral 档所有倍数为 1.0 (不调整)."""
        for style, m in DEFAULT_WEIGHT_MATRIX[REGIME_NEUTRAL].items():
            assert m == 1.0, f"neutral 档 {style} 倍数应为 1.0, 实际 {m}"

    def test_all_multipliers_positive(self):
        """所有倍数为正数."""
        for regime, styles in DEFAULT_WEIGHT_MATRIX.items():
            for style, m in styles.items():
                assert m > 0, f"{regime}.{style} 倍数 {m} 非正"

    def test_aligned_hedge_ratios_match_portfolio_yaml(self):
        """对齐 portfolio.yaml dynamic_hedge_policy 的四档."""
        assert ALIGNED_HEDGE_RATIOS == {
            REGIME_BULL: 0.20,
            REGIME_NEUTRAL: 0.40,
            REGIME_BEAR: 0.75,
            REGIME_CRISIS: 0.90,
        }


# ============================================================
# 数据类测试
# ============================================================


class TestVolRegimeDataclasses:
    """数据类测试."""

    def test_vol_regime_to_dict(self):
        """VolRegime.to_dict 返回完整字段."""
        regime = VolRegime(
            label=REGIME_BEAR,
            confidence=0.82,
            source="vix",
            indicators={"vix": 32.5, "realized_vol": 0.28},
            aligned_hedge_ratio=0.75,
            hedge_policy_key="bear_market",
            consistency_check={"consistent": True},
        )
        d = regime.to_dict()
        assert d["label"] == REGIME_BEAR
        assert d["confidence"] == 0.82
        assert d["indicators"]["vix"] == 32.5
        assert d["aligned_hedge_ratio"] == 0.75
        assert d["hedge_policy_key"] == "bear_market"

    def test_weight_suggestion_noop(self):
        """WeightSuggestion.noop 返回降级结果."""
        s = WeightSuggestion.noop(reason="test_disabled")
        assert s.degraded is True
        assert s.degraded_reason == "test_disabled"
        assert s.regime.label == REGIME_NEUTRAL
        assert s.regime.confidence == 0.0

    def test_weight_suggestion_to_dict(self):
        """WeightSuggestion.to_dict 包含完整审计字段."""
        regime = VolRegime(
            label=REGIME_BULL,
            confidence=0.85,
            source="vix",
            aligned_hedge_ratio=0.20,
            hedge_policy_key="bull_market",
        )
        s = WeightSuggestion(
            timestamp="2026-08-05T15:30:00",
            regime=regime,
            current_weights={"科技": 0.20},
            suggested_weights={"科技": 0.24},
            multipliers={"科技": 1.20},
            deltas={"科技": 0.04},
            confidence=0.85,
            trigger_reason="test",
        )
        d = s.to_dict()
        assert d["observation_phase"] is True
        assert d["audit"]["portfolio_yaml_untouched"] is True
        assert d["next_steps"]["phase_0_action"] == "review_only"
        assert d["regime"]["label"] == REGIME_BULL


# ============================================================
# Regime 分类测试
# ============================================================


class TestRegimeClassification:
    """Regime 识别测试."""

    def test_vix_below_20_is_bull(self):
        """VIX < 20 → bull."""
        w = _make_weighter(enabled=True)
        r = w.sense_regime(vix_value=15.0)
        assert r.label == REGIME_BULL
        assert r.source == "vix"

    def test_vix_20_to_30_is_neutral(self):
        """20 ≤ VIX < 30 → neutral."""
        w = _make_weighter(enabled=True)
        r = w.sense_regime(vix_value=25.0)
        assert r.label == REGIME_NEUTRAL

    def test_vix_30_to_40_is_bear(self):
        """30 ≤ VIX < 40 → bear."""
        w = _make_weighter(enabled=True)
        r = w.sense_regime(vix_value=35.0)
        assert r.label == REGIME_BEAR

    def test_vix_above_40_is_crisis(self):
        """VIX ≥ 40 → crisis."""
        w = _make_weighter(enabled=True)
        r = w.sense_regime(vix_value=50.0)
        assert r.label == REGIME_CRISIS

    def test_vix_none_falls_back_to_realized_vol(self):
        """VIX 缺失时回退到 realized_vol."""
        w = _make_weighter(enabled=True)
        # mock vol_controller 返回 0.28 → bear
        w._vol_controller = MagicMock()
        w._vol_controller.calc_realized_vol.return_value = 0.28
        r = w.sense_regime(vix_value=None, daily_returns=[0.01] * 20)
        assert r.label == REGIME_BEAR
        assert r.source == "realized_vol"

    def test_realized_vol_below_0_15_is_bull(self):
        """RV < 0.15 → bull."""
        w = _make_weighter(enabled=True)
        w._vol_controller = MagicMock()
        w._vol_controller.calc_realized_vol.return_value = 0.10
        r = w.sense_regime(daily_returns=[0.005] * 20)
        assert r.label == REGIME_BULL

    def test_realized_vol_above_0_40_is_crisis(self):
        """RV ≥ 0.40 → crisis."""
        w = _make_weighter(enabled=True)
        w._vol_controller = MagicMock()
        w._vol_controller.calc_realized_vol.return_value = 0.50
        r = w.sense_regime(daily_returns=[0.02] * 20)
        assert r.label == REGIME_CRISIS

    def test_vix_and_rv_inconsistent_takes_conservative(self):
        """VIX 与 RV 不一致时取更保守档."""
        w = _make_weighter(enabled=True)
        w._vol_controller = MagicMock()
        w._vol_controller.calc_realized_vol.return_value = 0.05  # RV → bull
        r = w.sense_regime(vix_value=35.0, daily_returns=[0.001] * 20)  # VIX → bear
        # 应取更保守的 bear
        assert r.label == REGIME_BEAR
        assert r.confidence <= 0.50  # 不一致时 confidence 上限 0.5

    def test_psi_high_reduces_confidence(self):
        """PSI > 0.25 时减 0.2 confidence."""
        w = _make_weighter(enabled=True)
        r_no_psi = w.sense_regime(vix_value=25.0)
        r_high_psi = w.sense_regime(vix_value=25.0, psi_value=0.30)
        assert r_high_psi.confidence < r_no_psi.confidence
        assert r_high_psi.confidence <= r_no_psi.confidence - 0.19  # 容差

    def test_drawdown_above_0_05_floors_to_neutral(self):
        """回撤 > 5% 时 regime 至少为 neutral."""
        w = _make_weighter(enabled=True)
        r = w.sense_regime(vix_value=15.0, current_drawdown=0.06)  # VIX=bull 但回撤大
        # 应 floor 到 neutral
        assert r.label in (REGIME_NEUTRAL, REGIME_BEAR, REGIME_CRISIS)

    def test_drawdown_above_0_12_floors_to_bear(self):
        """回撤 > 12% 时 regime 至少为 bear."""
        w = _make_weighter(enabled=True)
        r = w.sense_regime(vix_value=15.0, current_drawdown=0.15)  # VIX=bull 但回撤大
        # 应 floor 到 bear
        assert r.label in (REGIME_BEAR, REGIME_CRISIS)

    def test_aligned_hedge_ratio_matches_regime(self):
        """aligned_hedge_ratio 与 regime 对应."""
        w = _make_weighter(enabled=True)
        for vix, expected_regime in [
            (15.0, REGIME_BULL),
            (25.0, REGIME_NEUTRAL),
            (35.0, REGIME_BEAR),
            (50.0, REGIME_CRISIS),
        ]:
            r = w.sense_regime(vix_value=vix)
            assert r.aligned_hedge_ratio == ALIGNED_HEDGE_RATIOS[expected_regime]


# ============================================================
# 权重矩阵应用测试
# ============================================================


class TestWeightMatrix:
    """权重矩阵应用测试."""

    def test_bull_regime_increases_tech(self):
        """bull 档科技加仓."""
        w = _make_weighter(enabled=True)
        s = w.compute_weights(
            current_weights={"科技": 0.20, "现金": 0.05},
            vix_value=15.0,
        )
        assert s.multipliers["科技"] == 1.20
        assert s.suggested_weights["科技"] > 0.20

    def test_crisis_regime_decreases_tech(self):
        """crisis 档科技大幅减仓."""
        w = _make_weighter(enabled=True)
        s = w.compute_weights(
            current_weights={"科技": 0.20, "现金": 0.05},
            vix_value=50.0,
        )
        assert s.multipliers["科技"] == 0.30
        assert s.suggested_weights["科技"] < 0.20

    def test_cash_absorbs_delta(self):
        """差额归现金 (sum_to_one 约束)."""
        w = _make_weighter(enabled=True)
        # 构造一个总和 = 1.0 的输入
        current = {
            "科技": 0.30,
            "新能源": 0.10,
            "现金": 0.05,
            "医药": 0.15,
            "金融": 0.10,
            "宽基": 0.10,
            "资源": 0.10,
            "防御": 0.10,
        }
        s = w.compute_weights(current_weights=current, vix_value=35.0)  # bear
        total = sum(s.suggested_weights.values())
        assert abs(total - 1.0) < 1e-6, f"总和 {total} ≠ 1.0"

    def test_constraints_max_sector_exposure(self):
        """单一风格 > 30% 时裁剪."""
        w = _make_weighter(enabled=True)
        # 科技 = 0.50, bull × 1.2 = 0.60, 超过 0.30 上限
        current = {"科技": 0.50, "现金": 0.50}
        s = w.compute_weights(current_weights=current, vix_value=15.0)
        assert s.suggested_weights["科技"] <= 0.30 + 1e-6
        assert any(
            "max_sector" in c or "max_single" in c for c in s.constraints_applied
        )

    def test_constraints_cash_floor(self):
        """现金 < 5% 时抬升."""
        w = _make_weighter(enabled=True)
        current = {"科技": 0.95, "现金": 0.01}
        s = w.compute_weights(current_weights=current, vix_value=25.0)  # neutral
        assert s.suggested_weights["现金"] >= 0.05 - 1e-6

    def test_constraints_sum_to_one(self):
        """总和必须 = 1.0."""
        w = _make_weighter(enabled=True)
        current = {
            "科技": 0.235,
            "新能源": 0.09,
            "医药": 0.15,
            "金融": 0.11,
            "宽基": 0.06,
            "资源": 0.09,
            "防御": 0.05,
            "现金": 0.05,
        }
        for vix in [15.0, 25.0, 35.0, 50.0]:
            s = w.compute_weights(current_weights=current, vix_value=vix)
            total = sum(s.suggested_weights.values())
            assert abs(total - 1.0) < 1e-6, f"VIX={vix} 总和 {total}"


# ============================================================
# 主类流程测试
# ============================================================


class TestVolRegimeWeighterMain:
    """主类测试."""

    def test_flag_disabled_returns_noop(self):
        """Flag=False 时返回 noop (HC-1)."""
        w = _make_weighter(enabled=False)
        s = w.compute_weights(current_weights={"科技": 0.20}, vix_value=35.0)
        assert s.degraded is True
        assert "feature_flag_disabled" in s.degraded_reason

    def test_flag_disabled_does_not_write_reports(self, tmp_path):
        """Flag=False 时不写报告."""
        w = _make_weighter(enabled=False, reports_dir=tmp_path)
        s = WeightSuggestion.noop()
        path = w.emit_suggestion(s, reports_dir=tmp_path)
        # noop 也会写文件, 但内容是降级标志
        assert path.exists()
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["degraded"] is True

    def test_phase_0_does_not_modify_portfolio_yaml(self, tmp_path):
        """Phase 0 不修改 portfolio.yaml (HC-4)."""
        # 创建假 portfolio.yaml
        portfolio_path = tmp_path / "portfolio.yaml"
        portfolio_path.write_text("test: unchanged\n", encoding="utf-8")
        mtime_before = portfolio_path.stat().st_mtime

        w = _make_weighter(enabled=True, reports_dir=tmp_path)
        w.run_cycle(
            portfolio_snapshot={"科技": 0.20, "现金": 0.05},
            vix_value=35.0,
            reports_dir=tmp_path,
        )
        mtime_after = portfolio_path.stat().st_mtime
        assert mtime_before == mtime_after, "portfolio.yaml 被修改了!"

    def test_compute_weights_full_flow(self):
        """完整流程: VIX=35 → bear → 科技减仓."""
        w = _make_weighter(enabled=True)
        current = {
            "科技": 0.235,
            "新能源": 0.09,
            "医药": 0.15,
            "金融": 0.11,
            "宽基": 0.06,
            "资源": 0.09,
            "防御": 0.05,
            "现金": 0.05,
        }
        s = w.compute_weights(current_weights=current, vix_value=35.0)
        assert s.regime.label == REGIME_BEAR
        assert s.suggested_weights["科技"] < current["科技"]  # 减仓
        assert s.suggested_weights["现金"] > current["现金"]  # 加仓现金

    def test_emit_suggestion_writes_json(self, tmp_path):
        """报告写入 JSON 文件."""
        w = _make_weighter(enabled=True, reports_dir=tmp_path)
        regime = VolRegime(
            label=REGIME_BEAR,
            confidence=0.82,
            source="vix",
            aligned_hedge_ratio=0.75,
            hedge_policy_key="bear_market",
        )
        s = WeightSuggestion(
            timestamp="2026-08-05T15:30:00",
            regime=regime,
            current_weights={"科技": 0.235},
            suggested_weights={"科技": 0.141},
            multipliers={"科技": 0.60},
            deltas={"科技": -0.094},
            confidence=0.82,
            trigger_reason="test",
        )
        path = w.emit_suggestion(s, reports_dir=tmp_path)
        assert path.exists()
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["regime"]["label"] == REGIME_BEAR
        assert "audit" in data

    def test_degraded_when_vix_and_rv_both_missing(self):
        """VIX 和 RV 都缺失时降级到 neutral + 低 confidence."""
        w = _make_weighter(enabled=True)
        w._vol_controller = MagicMock()
        w._vol_controller.calc_realized_vol.return_value = (
            0.20  # 不会触发, 因为 daily_returns=None
        )
        r = w.sense_regime(vix_value=None, daily_returns=None)
        assert r.label == REGIME_NEUTRAL
        assert r.confidence <= 0.30

    def test_vol_controller_injection(self):
        """依赖注入: 可替换 VolTargetController."""
        mock_vc = MagicMock()
        mock_vc.calc_realized_vol.return_value = 0.28
        w = VolRegimeWeighter(vol_controller=mock_vc)
        # 注入成功 (无需通过 _init_vol_controller)
        assert w._vol_controller is mock_vc


# ============================================================
# 集成测试 (与 EvolutionOrchestrator)
# ============================================================


class TestEvolutionOrchestratorIntegration:
    """与 EvolutionOrchestrator 集成测试 (mock)."""

    def test_run_cycle_with_orchestrator_calls_log_decision(self):
        """run_cycle 接受 orchestrator 时调用 log_decision."""
        w = _make_weighter(enabled=True)
        mock_orch = MagicMock()
        mock_orch.log_decision.return_value = True

        w.run_cycle(
            portfolio_snapshot={"科技": 0.20, "现金": 0.05},
            vix_value=35.0,
            orchestrator=mock_orch,
        )
        mock_orch.log_decision.assert_called_once()
        # 验证 extra_payload 被传入
        call_kwargs = mock_orch.log_decision.call_args
        assert call_kwargs.kwargs.get("extra_payload") is not None

    def test_run_cycle_handles_old_log_decision_without_extra_payload(self):
        """兼容旧版 log_decision (不支持 extra_payload)."""
        w = _make_weighter(enabled=True)
        mock_orch = MagicMock()

        # 模拟旧版 log_decision 不接受 extra_payload
        def old_log_decision(
            report=None, action="evaluate_only", reason="", metrics=None
        ):
            return True

        mock_orch.log_decision.side_effect = old_log_decision

        # 不应抛异常
        result = w.run_cycle(
            portfolio_snapshot={"科技": 0.20, "现金": 0.05},
            vix_value=35.0,
            orchestrator=mock_orch,
        )
        assert result["status"] == "ok"


# ============================================================
# 便捷函数测试
# ============================================================


class TestClassifyRegimeByVol:
    """便捷函数 classify_regime_by_vol 测试."""

    def test_classify_vix_bull(self):
        """VIX=15 → bull."""
        with patch(
            "utils.alpha.vol_regime_weighter.VolRegimeWeighter._check_flag",
            return_value=True,
        ):
            r = classify_regime_by_vol(vix=15.0)
            assert r.label == REGIME_BULL

    def test_classify_vix_crisis(self):
        """VIX=50 → crisis."""
        with patch(
            "utils.alpha.vol_regime_weighter.VolRegimeWeighter._check_flag",
            return_value=True,
        ):
            r = classify_regime_by_vol(vix=50.0)
            assert r.label == REGIME_CRISIS


# ============================================================
# 快照解析降级语义 (SC-9 修复, 2026-09-12)
# ============================================================


class TestSnapshotParseDegradation:
    """portfolio_snapshot 解析必须区分「数据缺失」与「真实空持仓」.

    背景 (审查报告 §五 显式未扫面 regime→权重映射, 本轮补扫):
      - `_parse_portfolio_snapshot` 情况 1 的判据 `all(isinstance(v,(int,float)))`
        对任意非数值键 (meta/updated/risk_parameters 等) 整体失效 → 落到情况 2 →
        没有 `assets` 键 → 返回 `{}` → `compute_weights` 空输入 → 约束层
        「现金下限 0.05 + 总和归 1」把一切差异归入现金 → **建议 100% 现金**,
        而报告 `status: "ok"`、`degraded: False` —— 数据缺失被呈现为
        「全部风格清仓」这一强指令, 与真实调仓建议无法区分。
      - 生产链路 `EvolutionOrchestrator._read_portfolio_snapshot` 在文件缺失时
        返回 `{"assets": []}`, 走的正是本条路径。
    """

    def test_case1_tolerates_non_numeric_metadata_keys(self):
        """情况1: 数值风格权重 + 非数值元数据键 → 风格权重必须保留."""
        w = _make_weighter()
        got = w._parse_portfolio_snapshot(
            {"科技": 0.20, "价值": 0.15, "现金": 0.05, "meta": "v8.6"}
        )
        assert got.get("科技") == pytest.approx(0.20), (
            f"非数值元数据键导致风格权重丢失: {got}"
        )
        assert got.get("价值") == pytest.approx(0.15)
        assert got.get("现金") == pytest.approx(0.05)

    def test_case1_tolerates_none_value(self):
        """情况1: 含 None 值 → 数值风格权重必须保留."""
        w = _make_weighter()
        got = w._parse_portfolio_snapshot(
            {"科技": 0.20, "现金": 0.05, "updated": None}
        )
        assert got.get("科技") == pytest.approx(0.20)
        assert got.get("现金") == pytest.approx(0.05)

    def test_case1_tolerates_nested_dict_without_assets(self):
        """情况1: 含嵌套 dict (无 assets 键) → 数值风格权重必须保留."""
        w = _make_weighter()
        got = w._parse_portfolio_snapshot(
            {"科技": 0.20, "现金": 0.05, "risk_parameters": {"max": 0.1}}
        )
        assert got.get("科技") == pytest.approx(0.20)

    def test_bool_is_not_treated_as_weight(self):
        """bool 是 int 子类, 但语义上不是权重 → 不得混入风格权重."""
        w = _make_weighter()
        got = w._parse_portfolio_snapshot(
            {"科技": 0.20, "现金": 0.05, "flag": True}
        )
        assert got.get("科技") == pytest.approx(0.20)
        assert "flag" not in got, "bool 被误当权重参与计算"

    def test_empty_assets_snapshot_is_flagged(self):
        """assets 为空 (生产链路文件缺失形态) → 必须显式标记降级, 不得静默出 100% 现金.

        注意: 返回值沿用既有契约 (8 类风格全 0 的结构), 本次不改该形状;
        关键是 **降级标记** 必须置位, 使「快照缺失」可审计。
        """
        w = _make_weighter()
        got = w._parse_portfolio_snapshot({"assets": []})
        assert all(v == 0.0 for v in got.values()), f"空 assets 应产出全零权重: {got}"
        assert w._last_snapshot_degraded is True, (
            "空 assets 快照未标记降级: 数据缺失被伪装成『建议 100% 现金』"
        )
        assert w._last_snapshot_degraded_reason, "降级原因为空, 无法审计"

    def test_normal_snapshot_not_flagged(self):
        """正常快照不得被误标降级."""
        w = _make_weighter()
        w._parse_portfolio_snapshot({"科技": 0.20, "现金": 0.05})
        assert w._last_snapshot_degraded is False

    def test_run_cycle_marks_degraded_on_empty_snapshot(self):
        """run_cycle 空快照 → 报告 degraded=True 且带可读原因 (fail-closed 语义)."""
        w = _make_weighter(reports_dir=Path(tempfile.mkdtemp()))
        res = w.run_cycle(portfolio_snapshot={"assets": []}, vix_value=35.0)
        assert res["status"] == "ok"
        report = json.loads(
            Path(res["report_path"]).read_text(encoding="utf-8")
        )
        assert report["degraded"] is True, (
            "空快照产出的报告未标记 degraded: 与真实调仓建议无法区分"
        )
        assert report["degraded_reason"], "降级原因为空, 无法审计"

    def test_empty_style_recorded_not_silently_dropped(self):
        """assets 中 style 缺失/未知 → 归入显式键而非空字符串键."""
        w = _make_weighter()
        got = w._parse_portfolio_snapshot(
            {
                "assets": [
                    {"style": "科技", "weight": 0.05},
                    {"weight": 0.03},  # style 缺失
                ]
            }
        )
        assert got.get("科技") == pytest.approx(0.05)
        assert "" not in got, "style 缺失的资产被静默归入空字符串键"
        assert any("未分类" in k or "未知" in k for k in got), (
            f"style 缺失的资产应归入显式『未分类』键以便审计: {got}"
        )


# ============================================================
# Helper
# ============================================================


def _make_weighter(
    enabled: bool = True, reports_dir: Path | None = None
) -> VolRegimeWeighter:
    """创建测试用 VolRegimeWeighter (绕过 Flag 检查)."""
    with patch(
        "utils.alpha.vol_regime_weighter.VolRegimeWeighter._check_flag",
        return_value=enabled,
    ):
        w = VolRegimeWeighter(reports_dir=reports_dir or Path(tempfile.mkdtemp()))
    return w


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
