# -*- coding: utf-8 -*-
"""统一健康度度量框架单元测试 — 三层面自我进化 Stage 1.

任务: 1.9
对应模块:
    - utils/alpha/health_metrics.py (UnifiedHealthMetrics / LayerScore / HealthReport)
    - utils/alpha/layers/code_health.py (CodeHealthLayer)
    - utils/alpha/layers/strategy_health.py (StrategyHealthLayer)
    - utils/alpha/layers/ops_health.py (OpsHealthLayer)

验收标准:
    1. LayerScore / HealthReport 不可变 (frozen=True)
    2. Feature Flag 默认 False (HC-1), 关闭时返回降级报告
    3. 权重总和校验 (容差 0.01)
    4. _compute_overall 排除降级层后重新归一化
    5. 持久化: health_trend.jsonl 追加模式 (HC-4 只读历史)
    6. 三层采集器 Flag 关闭时降级, 开启时返回合法 LayerScore
    7. 子指标计算逻辑正确 (anti_cheat / observation_progress / blocking_score 等)
    8. 不污染生产数据 (positions.json / daily_returns.jsonl 不变)

设计原则:
    - AAA 模式 (Arrange → Act → Assert)
    - 全 mock, 不依赖外部 IO (<1s)
    - tmp_path 隔离文件系统
    - monkeypatch 控制 Feature Flag 状态
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List
from unittest.mock import MagicMock

import pytest

# 项目根
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.alpha.health_metrics import (  # noqa: E402
    DEFAULT_WEIGHTS,
    UnifiedHealthMetrics,
    HealthReport,
    LayerScore,
)
from utils.alpha.layers.code_health import (  # noqa: E402
    CodeHealthLayer,
    MAX_BLOCKING_FAILURES,
    EXPECTED_VERIFY_SCRIPTS,
)
from utils.alpha.layers.strategy_health import (  # noqa: E402
    StrategyHealthLayer,
    DEFAULT_OBSERVATION_TOTAL,
)
from utils.alpha.layers.ops_health import (  # noqa: E402
    OpsHealthLayer,
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def disabled_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """让所有 Feature Flag 返回 False (默认状态, HC-1)."""
    monkeypatch.setattr(
        "utils.infra.feature_flags.is_enabled", lambda name: False
    )


@pytest.fixture
def enabled_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """让所有 Feature Flag 返回 True (测试开启路径)."""
    monkeypatch.setattr(
        "utils.infra.feature_flags.is_enabled", lambda name: True
    )


def _make_layer_score(
    layer: str = "code",
    score: float = 0.8,
    is_degraded: bool = False,
    degraded_reason: str = "",
) -> LayerScore:
    """构造测试用 LayerScore."""
    return LayerScore(
        layer=layer,
        score=score,
        sub_metrics={"m1": score},
        is_degraded=is_degraded,
        degraded_reason=degraded_reason,
        collected_at=datetime.now(timezone.utc).isoformat(),
    )


# ============================================================
# 1. LayerScore 不可变性
# ============================================================
class TestLayerScoreImmutability:
    """LayerScore 必须 frozen=True (HC 不可变性原则)."""

    def test_create_layer_score(self) -> None:
        """Arrange/Act: 创建 LayerScore; Assert: 字段正确."""
        ls = LayerScore(layer="code", score=0.75, sub_metrics={"a": 0.5})
        assert ls.layer == "code"
        assert ls.score == 0.75
        assert ls.sub_metrics == {"a": 0.5}
        assert ls.is_degraded is False

    def test_modify_frozen_field_raises(self) -> None:
        """修改 frozen 字段必须抛 FrozenInstanceError (AttributeError 子类)."""
        ls = LayerScore(layer="code", score=0.5)
        with pytest.raises(AttributeError):
            ls.score = 0.9  # type: ignore[misc]

    def test_modify_frozen_layer_raises(self) -> None:
        """修改 layer 字段也必须抛异常."""
        ls = LayerScore(layer="code", score=0.5)
        with pytest.raises(AttributeError):
            ls.layer = "strategy"  # type: ignore[misc]

    def test_to_dict_round_trip(self) -> None:
        """to_dict 输出可序列化, 分数四舍五入到 4 位."""
        ls = LayerScore(
            layer="ops", score=0.123456789,
            sub_metrics={"x": 0.987654321},
        )
        d = ls.to_dict()
        assert d["layer"] == "ops"
        assert d["score"] == 0.1235  # round(0.123456789, 4)
        assert d["sub_metrics"]["x"] == 0.9877  # round(0.987654321, 4)
        # 原对象未变
        assert ls.score == 0.123456789


# ============================================================
# 2. HealthReport 不可变性与序列化
# ============================================================
class TestHealthReportSerialization:
    """HealthReport frozen=True + from_dict/to_dict 往返."""

    def test_default_health_report(self) -> None:
        """默认 HealthReport 字段值."""
        hr = HealthReport(overall_score=0.5)
        assert hr.overall_score == 0.5
        assert hr.layer_scores == {}
        assert hr.is_degraded is False
        assert hr.degraded_layers == []
        assert hr.sample_count == 0

    def test_modify_frozen_report_raises(self) -> None:
        """HealthReport frozen, 修改 overall_score 抛异常."""
        hr = HealthReport(overall_score=0.5)
        with pytest.raises(AttributeError):
            hr.overall_score = 0.9  # type: ignore[misc]

    def test_to_dict_contains_all_fields(self) -> None:
        """to_dict 包含所有字段."""
        ls = _make_layer_score("code", 0.8)
        hr = HealthReport(
            overall_score=0.7,
            layer_scores={"code": ls},
            trend_vs_yesterday=0.05,
            is_degraded=False,
            sample_count=1,
        )
        d = hr.to_dict()
        assert "overall_score" in d
        assert "layer_scores" in d
        assert "trend_vs_yesterday" in d
        assert "is_degraded" in d
        assert "degraded_layers" in d
        assert "sample_count" in d
        assert d["layer_scores"]["code"]["layer"] == "code"

    def test_from_dict_round_trip(self) -> None:
        """from_dict → to_dict 往返保持数据一致."""
        ls = _make_layer_score("strategy", 0.65, is_degraded=True, degraded_reason="X")
        original = HealthReport(
            overall_score=0.42,
            layer_scores={"strategy": ls},
            trend_vs_yesterday=-0.1,
            trend_vs_last_week=0.02,
            is_degraded=True,
            degraded_layers=["strategy"],
            generated_at="2026-08-01T00:00:00+00:00",
            sample_count=2,
        )
        d = original.to_dict()
        restored = HealthReport.from_dict(d)

        assert restored.overall_score == original.overall_score
        assert restored.is_degraded == original.is_degraded
        assert restored.degraded_layers == original.degraded_layers
        assert "strategy" in restored.layer_scores
        assert restored.layer_scores["strategy"].score == 0.65
        assert restored.layer_scores["strategy"].is_degraded is True
        assert restored.sample_count == 2

    def test_from_dict_empty_dict(self) -> None:
        """from_dict 空字典返回默认值, 不抛异常."""
        hr = HealthReport.from_dict({})
        assert hr.overall_score == 0.0
        assert hr.layer_scores == {}
        assert hr.is_degraded is False


# ============================================================
# 3. 权重校验
# ============================================================
class TestWeightValidation:
    """权重总和必须 = 1.0 (容差 0.01)."""

    def test_default_weights_sum_to_one(self) -> None:
        """默认权重总和 = 1.0."""
        assert abs(sum(DEFAULT_WEIGHTS.values()) - 1.0) < 0.001

    def test_valid_weights_accepted(self, disabled_flags, tmp_path: Path) -> None:
        """合法权重不抛异常."""
        m = UnifiedHealthMetrics(
            weights={"code": 0.3, "strategy": 0.4, "ops": 0.3},
            history_path=tmp_path / "h.jsonl",
        )
        assert m.weights == {"code": 0.3, "strategy": 0.4, "ops": 0.3}

    def test_invalid_weights_raise(self, disabled_flags, tmp_path: Path) -> None:
        """权重总和 ≠ 1.0 抛 ValueError."""
        with pytest.raises(ValueError, match="权重总和"):
            UnifiedHealthMetrics(
                weights={"code": 0.5, "strategy": 0.5, "ops": 0.5},  # sum=1.5
                history_path=tmp_path / "h.jsonl",
            )

    def test_weights_within_tolerance_accepted(self, disabled_flags, tmp_path: Path) -> None:
        """容差 0.01 内的偏差接受."""
        m = UnifiedHealthMetrics(
            weights={"code": 0.255, "strategy": 0.49, "ops": 0.255},  # sum=1.0
            history_path=tmp_path / "h.jsonl",
        )
        assert m.weights["code"] == 0.255


# ============================================================
# 4. Feature Flag (HC-1)
# ============================================================
class TestFeatureFlag:
    """Feature Flag 默认 False, 关闭时返回降级报告."""

    def test_flag_disabled_by_default(self, disabled_flags, tmp_path: Path) -> None:
        """HC-1: Flag 默认 False."""
        m = UnifiedHealthMetrics(history_path=tmp_path / "h.jsonl")
        assert m.get_status()["enabled"] is False

    def test_disabled_returns_degraded_report(self, disabled_flags, tmp_path: Path) -> None:
        """Flag 关闭 → collect_all 返回降级报告."""
        m = UnifiedHealthMetrics(history_path=tmp_path / "h.jsonl")
        report = m.collect_all()

        assert report.is_degraded is True
        assert report.overall_score == 0.0
        assert report.sample_count == 0
        assert set(report.degraded_layers) == {"code", "strategy", "ops"}
        # 三层都降级
        for layer in ("code", "strategy", "ops"):
            assert report.layer_scores[layer].is_degraded is True
            assert report.layer_scores[layer].degraded_reason == "FEATURE_FLAG_DISABLED"

    def test_get_status_reflects_flag(self, disabled_flags, tmp_path: Path) -> None:
        """get_status 返回 enabled=False."""
        m = UnifiedHealthMetrics(history_path=tmp_path / "h.jsonl")
        status = m.get_status()
        assert status["enabled"] is False
        assert status["feature_flag"] == "USE_UNIFIED_HEALTH_METRICS"
        assert "weights" in status


# ============================================================
# 5. _compute_overall 归一化逻辑
# ============================================================
class TestComputeOverall:
    """_compute_overall 排除降级层后重新归一化."""

    def test_all_active_weighted_sum(self, disabled_flags, tmp_path: Path) -> None:
        """三层全活跃 → 标准加权求和."""
        m = UnifiedHealthMetrics(
            weights={"code": 0.25, "strategy": 0.50, "ops": 0.25},
            history_path=tmp_path / "h.jsonl",
        )
        scores = {
            "code": _make_layer_score("code", 1.0),
            "strategy": _make_layer_score("strategy", 0.8),
            "ops": _make_layer_score("ops", 0.6),
        }
        overall, degraded, dl = m._compute_overall(scores)
        expected = 0.25 * 1.0 + 0.50 * 0.8 + 0.25 * 0.6  # = 0.8
        assert overall == pytest.approx(expected, abs=1e-6)
        assert degraded is False
        assert dl == []

    def test_one_layer_degraded_renormalize(self, disabled_flags, tmp_path: Path) -> None:
        """一层降级 → 剩余层重新归一化权重."""
        m = UnifiedHealthMetrics(
            weights={"code": 0.25, "strategy": 0.50, "ops": 0.25},
            history_path=tmp_path / "h.jsonl",
        )
        scores = {
            "code": _make_layer_score("code", 1.0, is_degraded=True, degraded_reason="X"),
            "strategy": _make_layer_score("strategy", 0.8),
            "ops": _make_layer_score("ops", 0.6),
        }
        overall, degraded, dl = m._compute_overall(scores)
        # 活跃权重: strategy=0.50, ops=0.25, sum=0.75
        # 归一化: strategy=2/3, ops=1/3
        # overall = 2/3*0.8 + 1/3*0.6 = 0.5333... + 0.2 = 0.7333
        expected = (0.50 * 0.8 + 0.25 * 0.6) / 0.75
        assert overall == pytest.approx(expected, abs=1e-6)
        assert degraded is True
        assert dl == ["code"]

    def test_all_degraded_zero(self, disabled_flags, tmp_path: Path) -> None:
        """全部降级 → overall=0, is_degraded=True."""
        m = UnifiedHealthMetrics(
            weights={"code": 0.25, "strategy": 0.50, "ops": 0.25},
            history_path=tmp_path / "h.jsonl",
        )
        scores = {
            k: _make_layer_score(k, 0.9, is_degraded=True, degraded_reason="D")
            for k in ("code", "strategy", "ops")
        }
        overall, degraded, dl = m._compute_overall(scores)
        assert overall == 0.0
        assert degraded is True
        assert set(dl) == {"code", "strategy", "ops"}

    def test_overall_clamped_to_unit(self, disabled_flags, tmp_path: Path) -> None:
        """overall_score 限制在 [0, 1]."""
        m = UnifiedHealthMetrics(
            weights={"code": 1.0, "strategy": 0.0, "ops": 0.0},
            history_path=tmp_path / "h.jsonl",
        )
        scores = {
            "code": _make_layer_score("code", 1.5),  # 超出 1.0
            "strategy": _make_layer_score("strategy", 0.0),
            "ops": _make_layer_score("ops", 0.0),
        }
        overall, _, _ = m._compute_overall(scores)
        assert overall <= 1.0


# ============================================================
# 6. 趋势计算
# ============================================================
class TestTrendCalculation:
    """_calc_trend 趋势计算."""

    def test_insufficient_history_returns_zero(self, disabled_flags, tmp_path: Path) -> None:
        """历史不足返回 0.0."""
        m = UnifiedHealthMetrics(history_path=tmp_path / "h.jsonl")
        history: List[HealthReport] = []
        assert m._calc_trend(0.8, history, 1) == 0.0

    def test_trend_vs_yesterday(self, disabled_flags, tmp_path: Path) -> None:
        """vs 昨天: current - history[1]."""
        m = UnifiedHealthMetrics(history_path=tmp_path / "h.jsonl")
        h1 = HealthReport(overall_score=0.7)
        h0 = HealthReport(overall_score=0.6)
        history = [h0, h1]  # index 1 = 昨天
        trend = m._calc_trend(0.8, history, 1)
        assert trend == pytest.approx(0.1, abs=1e-6)

    def test_trend_negative_when_declining(self, disabled_flags, tmp_path: Path) -> None:
        """下降趋势为负值."""
        m = UnifiedHealthMetrics(history_path=tmp_path / "h.jsonl")
        history = [HealthReport(overall_score=0.9), HealthReport(overall_score=0.85)]
        trend = m._calc_trend(0.7, history, 1)
        assert trend < 0
        assert trend == pytest.approx(-0.15, abs=1e-6)


# ============================================================
# 7. 持久化 (HC-4: 只追加)
# ============================================================
class TestPersistence:
    """health_trend.jsonl 追加模式持久化."""

    def test_persist_appends_line(self, disabled_flags, tmp_path: Path) -> None:
        """_persist 追加一行 JSON 到历史文件."""
        hist_path = tmp_path / "h.jsonl"
        m = UnifiedHealthMetrics(history_path=hist_path)
        report = HealthReport(overall_score=0.5, generated_at="2026-08-01T00:00:00+00:00")
        m._persist(report)

        assert hist_path.exists()
        lines = hist_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["overall_score"] == 0.5

    def test_persist_multiple_appends(self, disabled_flags, tmp_path: Path) -> None:
        """多次 _persist 追加多行 (不覆盖)."""
        hist_path = tmp_path / "h.jsonl"
        m = UnifiedHealthMetrics(history_path=hist_path)
        m._persist(HealthReport(overall_score=0.1, generated_at="t1"))
        m._persist(HealthReport(overall_score=0.2, generated_at="t2"))
        m._persist(HealthReport(overall_score=0.3, generated_at="t3"))

        lines = hist_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3
        scores = [json.loads(l)["overall_score"] for l in lines]
        assert scores == [0.1, 0.2, 0.3]

    def test_read_history_returns_reports(self, disabled_flags, tmp_path: Path) -> None:
        """_read_history 解析历史为 HealthReport 列表."""
        hist_path = tmp_path / "h.jsonl"
        hist_path.write_text(
            json.dumps({"overall_score": 0.5, "generated_at": "t1"}) + "\n"
            + json.dumps({"overall_score": 0.6, "generated_at": "t2"}) + "\n",
            encoding="utf-8",
        )
        m = UnifiedHealthMetrics(history_path=hist_path)
        history = m._read_history(7)
        assert len(history) == 2
        # reversed: 最近在前
        assert history[0].overall_score == 0.6
        assert history[1].overall_score == 0.5

    def test_read_history_missing_file_returns_empty(self, disabled_flags, tmp_path: Path) -> None:
        """历史文件不存在返回空列表, 不抛异常."""
        m = UnifiedHealthMetrics(history_path=tmp_path / "nonexistent.jsonl")
        assert m._read_history(7) == []

    def test_read_history_skips_corrupt_lines(self, disabled_flags, tmp_path: Path) -> None:
        """损坏的行被跳过, 不影响有效行解析."""
        hist_path = tmp_path / "h.jsonl"
        hist_path.write_text(
            "{invalid json}\n"
            + json.dumps({"overall_score": 0.7, "generated_at": "t2"}) + "\n",
            encoding="utf-8",
        )
        m = UnifiedHealthMetrics(history_path=hist_path)
        history = m._read_history(7)
        assert len(history) == 1
        assert history[0].overall_score == 0.7

    def test_persist_failure_does_not_raise(self, disabled_flags, tmp_path: Path) -> None:
        """持久化失败不抛异常 (容错降级)."""
        # 用一个不可写路径触发异常 (父目录是文件)
        bad_path = tmp_path / "blocker"
        bad_path.write_text("x", encoding="utf-8")
        m = UnifiedHealthMetrics(history_path=bad_path / "h.jsonl")
        # 不应抛异常
        m._persist(HealthReport(overall_score=0.5))


# ============================================================
# 8. _safe_collect 容错
# ============================================================
class TestSafeCollect:
    """_safe_collect 采集器容错."""

    def test_valid_layer_score_returned(self, disabled_flags, tmp_path: Path) -> None:
        """采集器返回合法 LayerScore → 原样返回."""
        m = UnifiedHealthMetrics(history_path=tmp_path / "h.jsonl")
        mock_layer = MagicMock()
        expected = _make_layer_score("code", 0.9)
        mock_layer.collect.return_value = expected
        now = datetime.now(timezone.utc).isoformat()
        result = m._safe_collect(mock_layer, "code", now)
        assert result is expected

    def test_invalid_return_type_degrades(self, disabled_flags, tmp_path: Path) -> None:
        """采集器返回非 LayerScore → 降级."""
        m = UnifiedHealthMetrics(history_path=tmp_path / "h.jsonl")
        mock_layer = MagicMock()
        mock_layer.collect.return_value = {"not": "a LayerScore"}  # type: ignore
        now = datetime.now(timezone.utc).isoformat()
        result = m._safe_collect(mock_layer, "code", now)
        assert result.is_degraded is True
        assert "INVALID_RETURN_TYPE" in result.degraded_reason

    def test_exception_degrades(self, disabled_flags, tmp_path: Path) -> None:
        """采集器抛异常 → 降级, 不传播."""
        m = UnifiedHealthMetrics(history_path=tmp_path / "h.jsonl")
        mock_layer = MagicMock()
        mock_layer.collect.side_effect = RuntimeError("boom")
        now = datetime.now(timezone.utc).isoformat()
        result = m._safe_collect(mock_layer, "code", now)
        assert result.is_degraded is True
        assert "COLLECT_ERROR" in result.degraded_reason
        assert result.score == 0.0


# ============================================================
# 9. collect_all 端到端 (mock 三层)
# ============================================================
class TestCollectAllEndToEnd:
    """collect_all 完整流程 (注入 mock 三层)."""

    def test_collect_all_with_mocked_layers(
        self, enabled_flags, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Flag 开启 + mock 三层 → 返回合法 HealthReport."""
        hist_path = tmp_path / "h.jsonl"
        m = UnifiedHealthMetrics(
            weights={"code": 0.25, "strategy": 0.50, "ops": 0.25},
            history_path=hist_path,
        )
        assert m.get_status()["enabled"] is True

        # 注入 mock 层, 跳过懒加载
        code_mock = MagicMock()
        code_mock.collect.return_value = _make_layer_score("code", 1.0)
        strat_mock = MagicMock()
        strat_mock.collect.return_value = _make_layer_score("strategy", 0.8)
        ops_mock = MagicMock()
        ops_mock.collect.return_value = _make_layer_score("ops", 0.6)

        m._code_layer = code_mock
        m._strategy_layer = strat_mock
        m._ops_layer = ops_mock
        m._layers_loaded = True

        report = m.collect_all()

        assert report.is_degraded is False
        assert report.sample_count == 3
        expected = 0.25 * 1.0 + 0.50 * 0.8 + 0.25 * 0.6
        assert report.overall_score == pytest.approx(expected, abs=1e-6)
        # 持久化
        assert hist_path.exists()

    def test_collect_all_one_layer_fails(
        self, enabled_flags, tmp_path: Path
    ) -> None:
        """一层采集失败 → 该层降级, 其他层正常."""
        m = UnifiedHealthMetrics(
            weights={"code": 0.25, "strategy": 0.50, "ops": 0.25},
            history_path=tmp_path / "h.jsonl",
        )

        code_mock = MagicMock()
        code_mock.collect.return_value = _make_layer_score("code", 1.0)
        strat_mock = MagicMock()
        strat_mock.collect.side_effect = ValueError("strategy broken")
        ops_mock = MagicMock()
        ops_mock.collect.return_value = _make_layer_score("ops", 0.6)

        m._code_layer = code_mock
        m._strategy_layer = strat_mock
        m._ops_layer = ops_mock
        m._layers_loaded = True

        report = m.collect_all()

        assert report.is_degraded is True
        assert "strategy" in report.degraded_layers
        # 归一化: code=0.25, ops=0.25, sum=0.5
        # overall = (0.25*1.0 + 0.25*0.6) / 0.5 = 0.4/0.5 = 0.8
        assert report.overall_score == pytest.approx(0.8, abs=1e-6)
        assert report.sample_count == 2  # strategy 降级不计

    def test_collect_all_layer_unavailable(
        self, enabled_flags, tmp_path: Path
    ) -> None:
        """层加载失败 (None) → 该层标记 UNAVAILABLE."""
        m = UnifiedHealthMetrics(
            weights={"code": 0.34, "strategy": 0.33, "ops": 0.33},
            history_path=tmp_path / "h.jsonl",
        )
        # 全部 None
        m._code_layer = None
        m._strategy_layer = None
        m._ops_layer = None
        m._layers_loaded = True

        report = m.collect_all()

        assert report.is_degraded is True
        assert set(report.degraded_layers) == {"code", "strategy", "ops"}
        assert report.overall_score == 0.0
        for layer in ("code", "strategy", "ops"):
            assert "UNAVAILABLE" in report.layer_scores[layer].degraded_reason


# ============================================================
# 10. CodeHealthLayer 单元测试
# ============================================================
class TestCodeHealthLayer:
    """代码层健康度采集器."""

    def test_flag_disabled_returns_degraded(self, disabled_flags) -> None:
        """Flag 关闭 → 降级 LayerScore."""
        layer = CodeHealthLayer()
        assert layer._enabled is False
        result = layer.collect()
        assert result.is_degraded is True
        assert result.degraded_reason == "FEATURE_FLAG_DISABLED"
        assert result.score == 0.0

    def test_calc_p0_pass_rate_none_report(self) -> None:
        """_calc_p0_pass_rate(None) = 0.0."""
        assert CodeHealthLayer._calc_p0_pass_rate(None) == 0.0

    def test_calc_p0_pass_rate_all_passed(self) -> None:
        """全部 PASS → 1.0."""
        report = MagicMock()
        item = MagicMock()
        item.level = MagicMock()
        item.level.value = "ERROR"
        item.status = MagicMock()
        item.status.value = "PASS"
        report.results = [item, item, item]
        report.all_passed = True
        assert CodeHealthLayer._calc_p0_pass_rate(report) == 1.0

    def test_calc_p0_pass_rate_partial(self) -> None:
        """部分通过 → 通过比例."""
        report = MagicMock()
        items = []
        for ok in [True, True, False]:
            it = MagicMock()
            it.level = MagicMock()
            it.level.value = "ERROR"
            it.status = MagicMock()
            it.status.value = "PASS" if ok else "FAIL"
            items.append(it)
        report.results = items
        report.all_passed = False
        assert CodeHealthLayer._calc_p0_pass_rate(report) == pytest.approx(2 / 3, abs=1e-6)

    def test_calc_blocking_score_zero_failures(self) -> None:
        """0 失败 → 1.0."""
        report = MagicMock()
        report.blocking_failures = 0
        assert CodeHealthLayer._calc_blocking_score(report) == 1.0

    def test_calc_blocking_score_max_failures(self) -> None:
        """MAX_BLOCKING_FAILURES 失败 → 0.0."""
        report = MagicMock()
        report.blocking_failures = MAX_BLOCKING_FAILURES
        assert CodeHealthLayer._calc_blocking_score(report) == 0.0

    def test_calc_blocking_score_half(self) -> None:
        """半数失败 → 0.5."""
        report = MagicMock()
        report.blocking_failures = MAX_BLOCKING_FAILURES // 2
        assert CodeHealthLayer._calc_blocking_score(report) == pytest.approx(0.5, abs=1e-6)

    def test_static_analysis_score_both_configs(
        self, enabled_flags, tmp_path: Path
    ) -> None:
        """mypy.ini + .pylintrc 都存在 → 1.0."""
        layer = CodeHealthLayer()
        layer._project_root = tmp_path
        (tmp_path / "mypy.ini").write_text("[mypy]", encoding="utf-8")
        (tmp_path / ".pylintrc").write_text("[pylint]", encoding="utf-8")
        assert layer._calc_static_analysis_score() == 1.0

    def test_static_analysis_score_none(self, enabled_flags, tmp_path: Path) -> None:
        """配置都不存在 → 0.0."""
        layer = CodeHealthLayer()
        layer._project_root = tmp_path
        assert layer._calc_static_analysis_score() == 0.0

    def test_verify_scripts_score_scales(
        self, enabled_flags, tmp_path: Path
    ) -> None:
        """验证脚本数 / 20, 上限 1.0."""
        layer = CodeHealthLayer()
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        # 创建 10 个验证脚本
        for i in range(10):
            (scripts_dir / f"_verify_test_{i}.py").write_text("# test", encoding="utf-8")
        layer._project_root = tmp_path
        score = layer._calc_verify_scripts_score()
        assert score == pytest.approx(0.5, abs=1e-6)

    def test_verify_scripts_score_capped(self, enabled_flags, tmp_path: Path) -> None:
        """超过 20 个脚本 → 上限 1.0."""
        layer = CodeHealthLayer()
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        for i in range(EXPECTED_VERIFY_SCRIPTS + 5):
            (scripts_dir / f"_verify_test_{i}.py").write_text("# test", encoding="utf-8")
        layer._project_root = tmp_path
        assert layer._calc_verify_scripts_score() == 1.0

    def test_collect_enabled_returns_valid_score(
        self, enabled_flags, tmp_path: Path
    ) -> None:
        """Flag 开启 + mock SystemChecker → 合法 LayerScore."""
        layer = CodeHealthLayer()
        layer._project_root = tmp_path
        # mock _run_system_check 返回 None (容错路径)
        layer._run_system_check = MagicMock(return_value=None)  # type: ignore
        result = layer.collect()
        assert isinstance(result, LayerScore)
        assert result.layer == "code"
        assert 0.0 <= result.score <= 1.0
        assert "p0_pass_rate" in result.sub_metrics


# ============================================================
# 11. StrategyHealthLayer 单元测试
# ============================================================
class TestStrategyHealthLayer:
    """策略层健康度采集器 (只读 decisions.jsonl)."""

    def test_flag_disabled_returns_degraded(self, disabled_flags) -> None:
        """Flag 关闭 → 降级."""
        layer = StrategyHealthLayer()
        assert layer._enabled is False
        result = layer.collect()
        assert result.is_degraded is True
        assert result.degraded_reason == "FEATURE_FLAG_DISABLED"

    def test_no_decisions_history_degraded(
        self, enabled_flags, tmp_path: Path
    ) -> None:
        """无 decisions.jsonl → is_degraded=True, NO_DECISIONS_HISTORY."""
        layer = StrategyHealthLayer(decisions_path=tmp_path / "none.jsonl")
        result = layer.collect()
        assert result.is_degraded is True
        assert result.degraded_reason == "NO_DECISIONS_HISTORY"
        # 评分仍计算 (全 0)
        assert result.sub_metrics["private_score"] == 0.0
        assert result.sub_metrics["public_score"] == 0.0

    def test_with_decision_record(self, enabled_flags, tmp_path: Path) -> None:
        """有决策记录 → 子指标正确提取."""
        decisions_path = tmp_path / "decisions.jsonl"
        decision = {
            "private_score": 0.75,
            "public_score": 0.60,
            "reward_hacking_risk": 0.20,
            "timestamp": "2026-08-01T00:00:00Z",
            "action": "evaluate_only",
        }
        decisions_path.write_text(
            json.dumps(decision, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        layer = StrategyHealthLayer(decisions_path=decisions_path)

        # mock 观察期进度 (day=7, total=14)
        layer._get_observation_progress = MagicMock(return_value=(7, 14))  # type: ignore

        result = layer.collect()
        assert result.is_degraded is False
        assert result.sub_metrics["private_score"] == 0.75
        assert result.sub_metrics["public_score"] == 0.60
        # anti_cheat = 1 - 0.20 = 0.80
        assert result.sub_metrics["anti_cheat"] == pytest.approx(0.80, abs=1e-6)
        # observation_progress = 7/14 = 0.5
        assert result.sub_metrics["observation_progress"] == 0.5

    def test_anti_cheat_clamped(self, enabled_flags, tmp_path: Path) -> None:
        """reward_hacking_risk > 1 时 anti_cheat ≥ 0."""
        decisions_path = tmp_path / "decisions.jsonl"
        decisions_path.write_text(
            json.dumps({"private_score": 0.5, "reward_hacking_risk": 1.5}) + "\n",
            encoding="utf-8",
        )
        layer = StrategyHealthLayer(decisions_path=decisions_path)
        result = layer.collect()
        assert result.sub_metrics["anti_cheat"] >= 0.0

    def test_observation_progress_clamped(
        self, enabled_flags, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """observation_progress 限制在 [0, 1]."""
        layer = StrategyHealthLayer(decisions_path=tmp_path / "none.jsonl")
        # day=20 > total=14 → clamped to 1.0
        layer._get_observation_progress = MagicMock(return_value=(20, 14))  # type: ignore
        result = layer.collect()
        assert result.sub_metrics["observation_progress"] == 1.0

    def test_drift_health_stage1_stub(self, enabled_flags, tmp_path: Path) -> None:
        """Stage 1 drift_health 返回中性值 0.5."""
        layer = StrategyHealthLayer(decisions_path=tmp_path / "none.jsonl")
        assert layer._get_drift_health() == 0.5

    def test_read_latest_decision_skips_corrupt(
        self, enabled_flags, tmp_path: Path
    ) -> None:
        """_read_latest_decision 跳过损坏行, 返回最后有效记录."""
        decisions_path = tmp_path / "decisions.jsonl"
        decisions_path.write_text(
            "{corrupt}\n"
            + json.dumps({"private_score": 0.4}) + "\n"
            + "also corrupt\n"
            + json.dumps({"private_score": 0.9}) + "\n",
            encoding="utf-8",
        )
        layer = StrategyHealthLayer(decisions_path=decisions_path)
        latest = layer._read_latest_decision()
        assert latest is not None
        assert latest["private_score"] == 0.9

    def test_default_observation_total(self) -> None:
        """默认观察期 = 14 天 (HC-4)."""
        assert DEFAULT_OBSERVATION_TOTAL == 14


# ============================================================
# 12. OpsHealthLayer 单元测试
# ============================================================
class TestOpsHealthLayer:
    """运维层健康度采集器 (只读聚合 reports/)."""

    def test_flag_disabled_returns_degraded(self, disabled_flags) -> None:
        """Flag 关闭 → 降级."""
        layer = OpsHealthLayer()
        assert layer._enabled is False
        result = layer.collect()
        assert result.is_degraded is True
        assert result.degraded_reason == "FEATURE_FLAG_DISABLED"

    def test_datasource_redundancy_missing_dir(self, enabled_flags, tmp_path: Path) -> None:
        """system_check 目录不存在 → 低分 0.3."""
        layer = OpsHealthLayer()
        layer._project_root = tmp_path
        layer.report_dirs = {"system_check": tmp_path / "missing"}
        assert layer._calc_datasource_redundancy() == 0.3

    def test_datasource_redundancy_empty_dir(self, enabled_flags, tmp_path: Path) -> None:
        """目录存在但无归档 → 0.3."""
        layer = OpsHealthLayer()
        sc_dir = tmp_path / "system_check"
        sc_dir.mkdir()
        layer._project_root = tmp_path
        layer.report_dirs = {"system_check": sc_dir}
        assert layer._calc_datasource_redundancy() == 0.3

    def test_datasource_redundancy_with_archive(
        self, enabled_flags, tmp_path: Path
    ) -> None:
        """有归档但解析失败 → 中高分 0.8."""
        layer = OpsHealthLayer()
        sc_dir = tmp_path / "system_check"
        sc_dir.mkdir()
        (sc_dir / "system_check_20260801.json").write_text("{}", encoding="utf-8")
        layer._project_root = tmp_path
        layer.report_dirs = {"system_check": sc_dir}
        assert layer._calc_datasource_redundancy() == 0.8

    def test_data_quality_missing_dir(self, enabled_flags, tmp_path: Path) -> None:
        """data_quality 目录不存在 → 中性 0.5."""
        layer = OpsHealthLayer()
        layer._project_root = tmp_path
        layer.report_dirs = {"data_quality": tmp_path / "missing"}
        assert layer._calc_data_quality_score() == 0.5

    def test_data_quality_with_reports(self, enabled_flags, tmp_path: Path) -> None:
        """有报告 → 0.8."""
        layer = OpsHealthLayer()
        dq_dir = tmp_path / "data_quality"
        dq_dir.mkdir()
        (dq_dir / "report.json").write_text("{}", encoding="utf-8")
        layer._project_root = tmp_path
        layer.report_dirs = {"data_quality": dq_dir}
        assert layer._calc_data_quality_score() == 0.8

    def test_drift_alert_recency_no_alerts(self, enabled_flags, tmp_path: Path) -> None:
        """无告警 → 高分 0.8."""
        layer = OpsHealthLayer()
        layer._project_root = tmp_path
        layer.report_dirs = {"drift_alerts": tmp_path / "missing"}
        assert layer._calc_drift_alert_recency() == 0.8

    def test_drift_alert_recency_recent_alert(
        self, enabled_flags, tmp_path: Path
    ) -> None:
        """24h 内有告警 → 扣分 (< 0.8)."""
        layer = OpsHealthLayer()
        da_dir = tmp_path / "drift_alerts"
        da_dir.mkdir()
        # 刚刚创建的告警文件 (age ≈ 0)
        (da_dir / "alert.json").write_text("{}", encoding="utf-8")
        layer._project_root = tmp_path
        layer.report_dirs = {"drift_alerts": da_dir}
        score = layer._calc_drift_alert_recency()
        assert score < 0.8

    def test_flag_stability_no_changes(self, enabled_flags, tmp_path: Path) -> None:
        """无 Flag 变更 → 0.9."""
        layer = OpsHealthLayer()
        layer._project_root = tmp_path
        layer.report_dirs = {"flag_audit": tmp_path / "missing"}
        assert layer._calc_flag_stability() == 0.9

    def test_flag_stability_many_changes(self, enabled_flags, tmp_path: Path) -> None:
        """10+ 变更 → 低分."""
        layer = OpsHealthLayer()
        fa_dir = tmp_path / "flag_audit"
        fa_dir.mkdir()
        for i in range(12):
            (fa_dir / f"flag_{i}.jsonl").write_text("{}", encoding="utf-8")
        layer._project_root = tmp_path
        layer.report_dirs = {"flag_audit": fa_dir}
        assert layer._calc_flag_stability() == 0.0

    def test_risk_event_rate_no_events(self, enabled_flags, tmp_path: Path) -> None:
        """无风控事件 → 0.9."""
        layer = OpsHealthLayer()
        layer._project_root = tmp_path
        layer.report_dirs = {"risk_bus": tmp_path / "missing"}
        assert layer._calc_risk_event_rate() == 0.9

    def test_collect_enabled_returns_valid_score(
        self, enabled_flags, tmp_path: Path
    ) -> None:
        """Flag 开启 → 合法 LayerScore (0-1)."""
        layer = OpsHealthLayer()
        layer._project_root = tmp_path
        layer.report_dirs = {
            k: tmp_path / k for k in
            ("system_check", "data_quality", "drift_alerts", "flag_audit", "risk_bus")
        }
        result = layer.collect()
        assert isinstance(result, LayerScore)
        assert result.layer == "ops"
        assert 0.0 <= result.score <= 1.0
        assert "datasource_redundancy" in result.sub_metrics
        assert "risk_event_rate" in result.sub_metrics


# ============================================================
# 13. HC-4: 不污染生产数据
# ============================================================
class TestNoProductionPollution:
    """HC-4: 不修改 positions.json / daily_returns.jsonl."""

    def test_production_files_unchanged(
        self, enabled_flags, tmp_path: Path
    ) -> None:
        """collect_all 后生产数据文件 mtime 不变."""
        # 构造伪生产文件
        pos = tmp_path / "positions.json"
        pos.write_text("{}", encoding="utf-8")
        dr = tmp_path / "daily_returns.jsonl"
        dr.write_text("", encoding="utf-8")
        pos_mtime_before = pos.stat().st_mtime
        dr_mtime_before = dr.stat().st_mtime

        m = UnifiedHealthMetrics(history_path=tmp_path / "h.jsonl")
        m._code_layer = None
        m._strategy_layer = None
        m._ops_layer = None
        m._layers_loaded = True
        m.collect_all()

        assert pos.stat().st_mtime == pos_mtime_before
        assert dr.stat().st_mtime == dr_mtime_before


# ============================================================
# 14. compare_baseline 公共 API
# ============================================================
class TestCompareBaseline:
    """compare_baseline 与历史基线对比."""

    def test_baseline_not_found(self, disabled_flags, tmp_path: Path) -> None:
        """基线不存在 → 返回 error."""
        m = UnifiedHealthMetrics(history_path=tmp_path / "h.jsonl")
        current = HealthReport(overall_score=0.5)
        result = m.compare_baseline(current, "1999-01-01")
        assert "error" in result
        assert result["error"] == -1.0

    def test_baseline_found_returns_diff(self, disabled_flags, tmp_path: Path) -> None:
        """找到基线 → 返回差值."""
        hist_path = tmp_path / "h.jsonl"
        baseline = HealthReport(
            overall_score=0.6,
            layer_scores={
                "code": _make_layer_score("code", 0.7),
                "strategy": _make_layer_score("strategy", 0.5),
            },
            generated_at="2026-07-15T00:00:00+00:00",
        )
        hist_path.write_text(
            json.dumps(baseline.to_dict()) + "\n", encoding="utf-8"
        )
        m = UnifiedHealthMetrics(history_path=hist_path)
        current = HealthReport(
            overall_score=0.8,
            layer_scores={
                "code": _make_layer_score("code", 0.9),
                "strategy": _make_layer_score("strategy", 0.7),
            },
        )
        result = m.compare_baseline(current, "2026-07-15")
        assert result["overall_diff"] == pytest.approx(0.2, abs=1e-6)
        assert result["code_diff"] == pytest.approx(0.2, abs=1e-6)
        assert result["strategy_diff"] == pytest.approx(0.2, abs=1e-6)


# ============================================================
# 15. get_history 公共 API
# ============================================================
class TestGetHistory:
    """get_history 读取历史."""

    def test_get_history_default(self, disabled_flags, tmp_path: Path) -> None:
        """get_history() 返回 List[HealthReport]."""
        hist_path = tmp_path / "h.jsonl"
        hist_path.write_text(
            json.dumps({"overall_score": 0.5, "generated_at": "t1"}) + "\n",
            encoding="utf-8",
        )
        m = UnifiedHealthMetrics(history_path=hist_path)
        history = m.get_history(30)
        assert len(history) == 1
        assert isinstance(history[0], HealthReport)

    def test_get_history_empty(self, disabled_flags, tmp_path: Path) -> None:
        """空历史 → 空列表."""
        m = UnifiedHealthMetrics(history_path=tmp_path / "none.jsonl")
        assert m.get_history(30) == []
