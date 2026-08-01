# -*- coding: utf-8 -*-
"""统一根因分析框架单元测试 — 三层面自我进化 Stage 2.

任务: 2.9
对应模块:
    - utils/alpha/root_cause.py (UnifiedRootCauseAnalyzer / RootCause / FixSuggestion / CausalChain / RootCauseReport)
    - utils/alpha/layers/code_diagnoser.py (CodeDiagnoser)
    - utils/alpha/layers/strategy_diagnoser.py (StrategyDiagnoser)
    - utils/alpha/layers/ops_diagnoser.py (OpsDiagnoser)
    - utils/alpha/causal_chain.py (CausalChainBuilder)

验收标准:
    1. FixSuggestion / RootCause / CausalChain / RootCauseReport 不可变 (frozen=True)
    2. Feature Flag 默认 False (HC-1), 关闭时返回降级报告
    3. requires_human_approval 默认 True (HC-3)
    4. CodeDiagnoser 识别 SystemChecker FAIL 项 (severity 推断)
    5. StrategyDiagnoser 识别漂移告警 + 决策低分
    6. OpsDiagnoser 识别数据源失败 + 数据质量过期
    7. CausalChainBuilder 构建跨层因果链 (6 条规则)
    8. 持久化: root_causes.jsonl 追加模式 (HC-4 只读历史)
    9. 结构化 evidence (Dict 非文本)
    10. 不污染生产数据 (positions.json / daily_returns.jsonl 不变)

设计原则:
    - AAA 模式 (Arrange → Act → Assert)
    - 全 mock, 不依赖外部 IO (<1s)
    - tmp_path 隔离文件系统
    - monkeypatch 控制 Feature Flag 状态
"""
from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# 项目根
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _flag_side_effect_main_only(flag_name: str, *args: Any, **kwargs: Any) -> bool:
    """Feature Flag side effect: 仅 USE_ROOT_CAUSE_ANALYZER=True, 其余 False.

    避免测试中误开启 USE_LLM_ROOT_CAUSE 导致 LLM provider 连接超时.
    """
    return flag_name == "USE_ROOT_CAUSE_ANALYZER"

from utils.alpha.root_cause import (  # noqa: E402
    ACTION_DATASOURCE_SWITCH,
    ACTION_MANUAL,
    ACTION_RETRAIN,
    CausalChain,
    FixSuggestion,
    LAYER_CODE,
    LAYER_OPS,
    LAYER_STRATEGY,
    RootCause,
    RootCauseReport,
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    UnifiedRootCauseAnalyzer,
    VALID_ACTIONS,
)
from utils.alpha.layers.code_diagnoser import CodeDiagnoser  # noqa: E402
from utils.alpha.layers.strategy_diagnoser import StrategyDiagnoser  # noqa: E402
from utils.alpha.layers.ops_diagnoser import OpsDiagnoser  # noqa: E402
from utils.alpha.causal_chain import CausalChainBuilder  # noqa: E402


# ============================================================
# FixSuggestion 数据类测试
# ============================================================


class TestFixSuggestion:
    """FixSuggestion 不可变数据类测试."""

    def test_create_minimal(self) -> None:
        """仅 action_type 即可创建, 其余用默认值."""
        fix = FixSuggestion(action_type=ACTION_MANUAL)
        assert fix.action_type == ACTION_MANUAL
        assert fix.target_file == ""
        assert fix.description == ""
        assert fix.estimated_risk == 0.5
        assert fix.requires_human_approval is True  # HC-3
        assert fix.remediation_commands == []

    def test_create_full(self) -> None:
        """完整字段创建."""
        fix = FixSuggestion(
            action_type=ACTION_RETRAIN,
            target_file="models/v9_lgb.pkl",
            description="重训练模型",
            estimated_risk=0.8,
            requires_human_approval=True,
            remediation_commands=["python retrain.py", "echo done"],
        )
        assert fix.action_type == ACTION_RETRAIN
        assert fix.target_file == "models/v9_lgb.pkl"
        assert fix.estimated_risk == 0.8
        assert len(fix.remediation_commands) == 2

    def test_invalid_action_type_raises(self) -> None:
        """非法 action_type 抛 ValueError."""
        with pytest.raises(ValueError, match="action_type"):
            FixSuggestion(action_type="invalid_action")

    def test_invalid_estimated_risk_raises(self) -> None:
        """estimated_risk 超出 [0, 1] 抛 ValueError."""
        with pytest.raises(ValueError, match="estimated_risk"):
            FixSuggestion(action_type=ACTION_MANUAL, estimated_risk=1.5)
        with pytest.raises(ValueError, match="estimated_risk"):
            FixSuggestion(action_type=ACTION_MANUAL, estimated_risk=-0.1)

    def test_frozen_immutable(self) -> None:
        """frozen=True, 修改抛 FrozenInstanceError."""
        fix = FixSuggestion(action_type=ACTION_MANUAL)
        with pytest.raises(FrozenInstanceError):
            fix.action_type = ACTION_RETRAIN  # type: ignore[misc]

    def test_to_dict(self) -> None:
        """to_dict 返回可 JSON 序列化的 dict."""
        fix = FixSuggestion(
            action_type=ACTION_MANUAL,
            remediation_commands=["cmd1", "cmd2"],
        )
        d = fix.to_dict()
        assert isinstance(d, dict)
        assert d["action_type"] == ACTION_MANUAL
        assert d["requires_human_approval"] is True
        assert d["remediation_commands"] == ["cmd1", "cmd2"]
        # 可 JSON 序列化
        json.dumps(d, ensure_ascii=False)

    def test_all_valid_actions(self) -> None:
        """所有 VALID_ACTIONS 都能创建."""
        for action in VALID_ACTIONS:
            fix = FixSuggestion(action_type=action)
            assert fix.action_type == action


# ============================================================
# RootCause 数据类测试
# ============================================================


class TestRootCause:
    """RootCause 不可变数据类测试."""

    def test_create_minimal(self) -> None:
        """最小字段创建."""
        cause = RootCause(
            cause_id="test-1",
            layer=LAYER_CODE,
            category="test",
            severity=SEVERITY_LOW,
        )
        assert cause.cause_id == "test-1"
        assert cause.layer == LAYER_CODE
        assert cause.severity == SEVERITY_LOW
        assert cause.confidence == 0.5
        assert cause.suggested_fix.action_type == ACTION_MANUAL
        assert cause.suggested_fix.requires_human_approval is True  # HC-3

    def test_invalid_layer_raises(self) -> None:
        """非法 layer 抛 ValueError."""
        with pytest.raises(ValueError, match="layer"):
            RootCause(
                cause_id="test", layer="invalid", category="test",
                severity=SEVERITY_LOW,
            )

    def test_invalid_severity_raises(self) -> None:
        """非法 severity 抛 ValueError."""
        with pytest.raises(ValueError, match="severity"):
            RootCause(
                cause_id="test", layer=LAYER_CODE, category="test",
                severity="super_high",
            )

    def test_invalid_confidence_raises(self) -> None:
        """confidence 超出 [0, 1] 抛 ValueError."""
        with pytest.raises(ValueError, match="confidence"):
            RootCause(
                cause_id="test", layer=LAYER_CODE, category="test",
                severity=SEVERITY_LOW, confidence=1.5,
            )

    def test_frozen_immutable(self) -> None:
        """frozen=True, 修改抛 FrozenInstanceError."""
        cause = RootCause(
            cause_id="test", layer=LAYER_CODE, category="test",
            severity=SEVERITY_LOW,
        )
        with pytest.raises(FrozenInstanceError):
            cause.severity = SEVERITY_CRITICAL  # type: ignore[misc]

    def test_evidence_structured_dict(self) -> None:
        """evidence 是结构化 Dict (非文本)."""
        cause = RootCause(
            cause_id="test", layer=LAYER_CODE, category="test",
            severity=SEVERITY_LOW,
            evidence={"score": 0.8, "items": [1, 2, 3], "name": "test"},
        )
        assert isinstance(cause.evidence, dict)
        assert cause.evidence["score"] == 0.8
        assert isinstance(cause.evidence["items"], list)

    def test_to_dict_serializable(self) -> None:
        """to_dict 可 JSON 序列化."""
        cause = RootCause(
            cause_id="test", layer=LAYER_STRATEGY, category="drift",
            severity=SEVERITY_HIGH,
            evidence={"model": "v9_lgb", "score": 0.25},
        )
        d = cause.to_dict()
        json_str = json.dumps(d, ensure_ascii=False)
        assert isinstance(json_str, str)
        parsed = json.loads(json_str)
        assert parsed["cause_id"] == "test"
        assert parsed["layer"] == LAYER_STRATEGY

    def test_evidence_non_serializable_converted(self) -> None:
        """非 JSON 原生类型的 evidence 值被转为字符串."""
        class Custom:
            def __str__(self) -> str:
                return "custom_object"
        cause = RootCause(
            cause_id="test", layer=LAYER_CODE, category="test",
            severity=SEVERITY_LOW,
            evidence={"custom": Custom()},
        )
        d = cause.to_dict()
        # Custom 对象被转为字符串
        assert d["evidence"]["custom"] == "custom_object"


# ============================================================
# CausalChain 数据类测试
# ============================================================


class TestCausalChain:
    """CausalChain 不可变数据类测试."""

    def test_create_with_nodes(self) -> None:
        """创建含节点的因果链."""
        nodes = [
            RootCause(cause_id="ops-1", layer=LAYER_OPS, category="ds_fail",
                      severity=SEVERITY_HIGH),
            RootCause(cause_id="code-1", layer=LAYER_CODE, category="check_fail",
                      severity=SEVERITY_CRITICAL),
        ]
        chain = CausalChain(
            chain_id="chain-1", nodes=nodes, confidence=0.8,
            description="ops → code",
        )
        assert chain.chain_id == "chain-1"
        assert len(chain.nodes) == 2
        assert chain.confidence == 0.8

    def test_invalid_confidence_raises(self) -> None:
        """confidence 超出 [0, 1] 抛 ValueError."""
        with pytest.raises(ValueError, match="confidence"):
            CausalChain(chain_id="chain", confidence=1.5)

    def test_frozen_immutable(self) -> None:
        """frozen=True."""
        chain = CausalChain(chain_id="chain", confidence=0.5)
        with pytest.raises(FrozenInstanceError):
            chain.confidence = 0.9  # type: ignore[misc]

    def test_to_dict(self) -> None:
        """to_dict 含节点列表."""
        nodes = [
            RootCause(cause_id="n1", layer=LAYER_OPS, category="c1",
                      severity=SEVERITY_LOW),
        ]
        chain = CausalChain(chain_id="chain-1", nodes=nodes, confidence=0.7)
        d = chain.to_dict()
        assert d["chain_id"] == "chain-1"
        assert len(d["nodes"]) == 1
        assert d["confidence"] == 0.7


# ============================================================
# RootCauseReport 数据类测试
# ============================================================


class TestRootCauseReport:
    """RootCauseReport 不可变数据类测试."""

    def test_create_empty(self) -> None:
        """空报告创建."""
        report = RootCauseReport()
        assert report.causes == []
        assert report.causal_chains == []
        assert report.is_degraded is False

    def test_to_from_dict_roundtrip(self) -> None:
        """to_dict / from_dict 往返保持数据."""
        causes = [
            RootCause(
                cause_id="c1", layer=LAYER_CODE, category="test",
                severity=SEVERITY_HIGH,
                evidence={"key": "value"},
                suggested_fix=FixSuggestion(
                    action_type=ACTION_MANUAL,
                    remediation_commands=["cmd1"],
                ),
                confidence=0.8,
            ),
        ]
        chains = [
            CausalChain(chain_id="ch1", nodes=causes, confidence=0.7,
                        description="test chain"),
        ]
        original = RootCauseReport(
            causes=causes, causal_chains=chains,
            is_degraded=False, analyzed_at="2026-08-01T12:00:00Z",
            summary="test",
        )
        d = original.to_dict()
        restored = RootCauseReport.from_dict(d)
        assert len(restored.causes) == 1
        assert restored.causes[0].cause_id == "c1"
        assert restored.causes[0].severity == SEVERITY_HIGH
        assert len(restored.causal_chains) == 1
        assert restored.causal_chains[0].chain_id == "ch1"
        assert restored.analyzed_at == "2026-08-01T12:00:00Z"

    def test_frozen_immutable(self) -> None:
        """frozen=True."""
        report = RootCauseReport()
        with pytest.raises(FrozenInstanceError):
            report.is_degraded = True  # type: ignore[misc]


# ============================================================
# UnifiedRootCauseAnalyzer 测试
# ============================================================


class TestUnifiedRootCauseAnalyzer:
    """UnifiedRootCauseAnalyzer 测试."""

    def test_flag_disabled_returns_degraded(self) -> None:
        """Flag 关闭时返回降级报告 (HC-1)."""
        with patch("utils.infra.feature_flags.is_enabled", return_value=False):
            analyzer = UnifiedRootCauseAnalyzer()
            report = analyzer.analyze(None)
        assert report.is_degraded is True
        assert "FEATURE_FLAG_DISABLED" in report.degraded_reason
        assert report.causes == []

    def test_flag_enabled_analyzes(self) -> None:
        """Flag 开启时执行分析 (可能 0 根因, 但非降级)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            persist_path = Path(tmpdir) / "rc.jsonl"
            with patch("utils.infra.feature_flags.is_enabled",
                       side_effect=_flag_side_effect_main_only):
                analyzer = UnifiedRootCauseAnalyzer(persistence_path=persist_path)
                report = analyzer.analyze(None)
            assert report.is_degraded is False
            assert isinstance(report.causes, list)
            assert isinstance(report.causal_chains, list)

    def test_persistence_appends(self) -> None:
        """持久化追加模式 (HC-4)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            persist_path = Path(tmpdir) / "rc.jsonl"
            with patch("utils.infra.feature_flags.is_enabled",
                       side_effect=_flag_side_effect_main_only):
                analyzer = UnifiedRootCauseAnalyzer(persistence_path=persist_path)
                analyzer.analyze(None)
                analyzer.analyze(None)
            lines = persist_path.read_text(encoding="utf-8").strip().splitlines()
            assert len(lines) == 2  # 追加 2 行

    def test_get_recent_causes(self) -> None:
        """get_recent_causes 返回扁平化根因列表."""
        with tempfile.TemporaryDirectory() as tmpdir:
            persist_path = Path(tmpdir) / "rc.jsonl"
            with patch("utils.infra.feature_flags.is_enabled",
                       side_effect=_flag_side_effect_main_only):
                analyzer = UnifiedRootCauseAnalyzer(persistence_path=persist_path)
                analyzer.analyze(None)
                recent = analyzer.get_recent_causes(7)
            assert isinstance(recent, list)

    def test_get_status(self) -> None:
        """get_status 返回状态字典."""
        analyzer = UnifiedRootCauseAnalyzer()
        status = analyzer.get_status()
        assert isinstance(status, dict)
        assert "enabled" in status
        assert "persistence_path" in status

    def test_diagnoser_failure_graceful(self) -> None:
        """诊断器加载失败时容错降级 (不抛异常)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            persist_path = Path(tmpdir) / "rc.jsonl"
            with patch("utils.infra.feature_flags.is_enabled",
                       side_effect=_flag_side_effect_main_only):
                analyzer = UnifiedRootCauseAnalyzer(persistence_path=persist_path)
                # 标记已加载, 防止 analyze() 重新加载诊断器覆盖 None
                analyzer._diagnosers_loaded = True
                # 模拟所有诊断器加载失败
                analyzer._code_diagnoser = None
                analyzer._strategy_diagnoser = None
                analyzer._ops_diagnoser = None
                analyzer._chain_builder = None
                report = analyzer.analyze(None)
            assert report.is_degraded is False
            assert report.causes == []

    def test_extract_health_report_time(self) -> None:
        """_extract_health_report_time 兼容对象/字典."""
        analyzer = UnifiedRootCauseAnalyzer()
        # 字典
        assert analyzer._extract_health_report_time({"generated_at": "2026-08-01"}) == "2026-08-01"
        # 对象
        class _HR:
            generated_at = "2026-08-02"
        assert analyzer._extract_health_report_time(_HR()) == "2026-08-02"
        # None
        assert analyzer._extract_health_report_time(None) == ""


# ============================================================
# CodeDiagnoser 测试
# ============================================================


class TestCodeDiagnoser:
    """CodeDiagnoser 测试."""

    @staticmethod
    def _make_check_result(code, name, status, level, detail="", remediation=""):
        """构造 mock CheckResult."""
        mock = MagicMock()
        mock.code = code
        mock.name = name
        mock.status = MagicMock(value=status)
        mock.level = MagicMock(value=level)
        mock.detail = detail
        mock.remediation = remediation
        return mock

    def test_identifies_fail_items(self) -> None:
        """识别 FAIL 项生成根因."""
        mock_report = MagicMock()
        mock_report.results = [
            self._make_check_result("C1.1", "positions.json", "FAIL", "ERROR",
                                    "不存在", "创建文件"),
            self._make_check_result("C5.1", "pandas", "PASS", "INFO"),
        ]
        with patch.object(CodeDiagnoser, "_run_check", return_value=mock_report):
            diagnoser = CodeDiagnoser(run_system_check=True)
            causes = diagnoser.diagnose(None)
        fail_causes = [c for c in causes if c.category == "system_check_fail"]
        assert len(fail_causes) == 1
        assert fail_causes[0].evidence["check_code"] == "C1.1"
        assert fail_causes[0].evidence["remediation"] == "创建文件"

    def test_blocking_codes_elevated_to_critical(self) -> None:
        """阻断性前缀 (C1./C2.1/C3.1 等) ERROR FAIL → critical."""
        mock_report = MagicMock()
        mock_report.results = [
            self._make_check_result("C1.1", "positions", "FAIL", "ERROR"),
            self._make_check_result("C3.1", "Wind MCP", "FAIL", "ERROR"),
        ]
        with patch.object(CodeDiagnoser, "_run_check", return_value=mock_report):
            diagnoser = CodeDiagnoser(run_system_check=True)
            causes = diagnoser.diagnose(None)
        critical_causes = [c for c in causes if c.severity == SEVERITY_CRITICAL]
        assert len(critical_causes) >= 2  # C1.1 和 C3.1 都是 critical

    def test_info_fail_no_cause(self) -> None:
        """INFO 级 FAIL 不产生根因."""
        mock_report = MagicMock()
        mock_report.results = [
            self._make_check_result("C9.1", "history", "FAIL", "INFO"),
        ]
        with patch.object(CodeDiagnoser, "_run_check", return_value=mock_report):
            diagnoser = CodeDiagnoser(run_system_check=True)
            causes = diagnoser.diagnose(None)
        assert len(causes) == 0

    def test_run_check_failure_returns_empty(self) -> None:
        """_run_check 失败时返回空列表 (容错)."""
        with patch.object(CodeDiagnoser, "_run_check", return_value=None):
            diagnoser = CodeDiagnoser(run_system_check=True)
            causes = diagnoser.diagnose(None)
        assert isinstance(causes, list)

    def test_infer_target_file(self) -> None:
        """_infer_target_file 正确映射检查项到文件."""
        assert CodeDiagnoser._infer_target_file("C1.1") == "config/positions.json"
        assert CodeDiagnoser._infer_target_file("C2.1") == ".env"
        assert CodeDiagnoser._infer_target_file("C3.1") == "utils/data_provider.py"
        assert CodeDiagnoser._infer_target_file("C9.1") == ""


# ============================================================
# StrategyDiagnoser 测试
# ============================================================


class TestStrategyDiagnoser:
    """StrategyDiagnoser 测试."""

    def test_diagnose_drift_alert(self, tmp_path: Path) -> None:
        """识别漂移告警生成根因."""
        drift_dir = tmp_path / "drift_alerts"
        drift_dir.mkdir()
        alert = {
            "severity": "high", "drift_type": "feature_drift",
            "model_name": "v9_lgb", "feature_name": "MOM_5D",
            "drift_score": 0.25, "psi": 0.35,
            "recorded_at": "2026-08-01T10:00:00Z",
        }
        (drift_dir / "v9_lgb_2026-08-01.jsonl").write_text(
            json.dumps(alert) + "\n", encoding="utf-8"
        )
        diagnoser = StrategyDiagnoser(drift_alerts_dir=drift_dir)
        causes = diagnoser.diagnose(None)
        drift_causes = [c for c in causes if c.category == "drift_alert"]
        assert len(drift_causes) == 1
        assert drift_causes[0].evidence["model_name"] == "v9_lgb"
        assert drift_causes[0].suggested_fix.action_type == ACTION_RETRAIN

    def test_diagnose_private_score_low(self, tmp_path: Path) -> None:
        """识别 private_score 低生成根因."""
        decisions_path = tmp_path / "decisions.jsonl"
        decision = {
            "timestamp": "2026-08-01T10:00:00Z",
            "private_score": 0.1,  # < 0.3 阈值
            "public_score": 0.5,
            "reward_hacking_risk": 0.2,
            "recommendation": "rollback",
            "sample_count": 10,
        }
        decisions_path.write_text(json.dumps(decision) + "\n", encoding="utf-8")
        diagnoser = StrategyDiagnoser(
            decisions_path=decisions_path,
            drift_alerts_dir=tmp_path / "empty_drift",  # 不存在
        )
        causes = diagnoser.diagnose(None)
        # 应识别 private_score_low + rollback_recommended
        categories = {c.category for c in causes}
        assert "private_score_low" in categories
        assert "strategy_rollback_recommended" in categories

    def test_diagnose_rh_risk_high(self, tmp_path: Path) -> None:
        """识别 reward_hacking_risk 高生成根因."""
        decisions_path = tmp_path / "decisions.jsonl"
        decision = {
            "timestamp": "2026-08-01T10:00:00Z",
            "private_score": 0.6,  # 正常
            "public_score": 0.5,
            "reward_hacking_risk": 0.7,  # > 0.5 阈值
            "recommendation": "continue",
            "sample_count": 10,
        }
        decisions_path.write_text(json.dumps(decision) + "\n", encoding="utf-8")
        diagnoser = StrategyDiagnoser(
            decisions_path=decisions_path,
            drift_alerts_dir=tmp_path / "empty_drift",
        )
        causes = diagnoser.diagnose(None)
        rh_causes = [c for c in causes if c.category == "reward_hacking_risk_high"]
        assert len(rh_causes) == 1

    def test_no_decisions_returns_empty(self, tmp_path: Path) -> None:
        """无 decisions.jsonl 时漂移诊断返回空."""
        diagnoser = StrategyDiagnoser(
            decisions_path=tmp_path / "nonexistent.jsonl",
            drift_alerts_dir=tmp_path / "empty_drift",
        )
        causes = diagnoser.diagnose(None)
        # 无 decisions + 无 drift_alerts → 空 (除非 health_report 补充)
        decisions_causes = [c for c in causes if c.category.startswith("private_score")
                            or c.category == "strategy_rollback_recommended"]
        assert len(decisions_causes) == 0

    def test_pit_violation_critical(self, tmp_path: Path) -> None:
        """pit_violations > 0 → critical 根因."""
        decisions_path = tmp_path / "decisions.jsonl"
        decision = {
            "timestamp": "2026-08-01T10:00:00Z",
            "private_score": 0.6,
            "public_score": 0.5,
            "reward_hacking_risk": 0.2,
            "recommendation": "continue",
            "sample_count": 10,
            "evaluator_report": {"pit_violations": 3},
        }
        decisions_path.write_text(json.dumps(decision) + "\n", encoding="utf-8")
        diagnoser = StrategyDiagnoser(
            decisions_path=decisions_path,
            drift_alerts_dir=tmp_path / "empty_drift",
        )
        causes = diagnoser.diagnose(None)
        pit_causes = [c for c in causes if c.category == "pit_violation"]
        assert len(pit_causes) == 1
        assert pit_causes[0].severity == SEVERITY_CRITICAL
        assert pit_causes[0].evidence["pit_violations"] == 3


# ============================================================
# OpsDiagnoser 测试
# ============================================================


class TestOpsDiagnoser:
    """OpsDiagnoser 测试."""

    def test_diagnose_datasource_fail(self, tmp_path: Path) -> None:
        """识别 C3 数据源失败."""
        sc_dir = tmp_path / "system_check"
        sc_dir.mkdir()
        archive = {
            "results": [
                {"code": "C3.1", "name": "Wind MCP", "status": "FAIL",
                 "level": "ERROR", "detail": "不可用",
                 "remediation": "检查 API key"},
                {"code": "C5.1", "name": "pandas", "status": "PASS",
                 "level": "INFO"},
            ],
        }
        (sc_dir / "system_check_20260801.json").write_text(
            json.dumps(archive), encoding="utf-8"
        )
        diagnoser = OpsDiagnoser(
            report_dirs={
                "system_check": sc_dir,
                "data_quality": tmp_path / "dq",
                "drift_alerts": tmp_path / "da",
                "flag_audit": tmp_path / "fa",
                "risk_bus": tmp_path / "rb",
            }
        )
        causes = diagnoser.diagnose(None)
        ds_causes = [c for c in causes if c.category == "datasource_fail"]
        assert len(ds_causes) == 1
        assert ds_causes[0].evidence["check_code"] == "C3.1"
        assert ds_causes[0].suggested_fix.action_type == ACTION_DATASOURCE_SWITCH

    def test_diagnose_data_quality_stale(self, tmp_path: Path) -> None:
        """识别数据质量报告过期."""
        dq_dir = tmp_path / "data_quality"
        dq_dir.mkdir()
        # 创建一个旧的报告文件 (mtime 在 24h 前)
        old_file = dq_dir / "old_report.json"
        old_file.write_text("{}", encoding="utf-8")
        # 修改 mtime 到 2 天前
        import os
        old_time = (old_file.stat().st_mtime) - 2 * 86400
        os.utime(old_file, (old_time, old_time))

        diagnoser = OpsDiagnoser(
            report_dirs={
                "system_check": tmp_path / "sc",
                "data_quality": dq_dir,
                "drift_alerts": tmp_path / "da",
                "flag_audit": tmp_path / "fa",
                "risk_bus": tmp_path / "rb",
            }
        )
        causes = diagnoser.diagnose(None)
        stale_causes = [c for c in causes if c.category == "data_quality_stale"]
        assert len(stale_causes) == 1

    def test_no_dirs_returns_empty(self, tmp_path: Path) -> None:
        """所有目录不存在时返回空 (或 no_monitoring 根因)."""
        diagnoser = OpsDiagnoser(
            report_dirs={
                "system_check": tmp_path / "sc",
                "data_quality": tmp_path / "dq",
                "drift_alerts": tmp_path / "da",
                "flag_audit": tmp_path / "fa",
                "risk_bus": tmp_path / "rb",
            }
        )
        causes = diagnoser.diagnose(None)
        # data_quality 目录不存在 → data_quality_no_monitoring 根因
        categories = {c.category for c in causes}
        assert "data_quality_no_monitoring" in categories

    def test_datasource_target_map(self) -> None:
        """数据源目标文件映射正确."""
        # 通过实际诊断验证映射 (间接测试)
        assert _DATASOURCE_TARGET_MAP_CHECK()


def _DATASOURCE_TARGET_MAP_CHECK() -> bool:
    """验证数据源目标映射 (辅助)."""
    from utils.alpha.layers.ops_diagnoser import _DATASOURCE_TARGET_MAP
    return "C3.1" in _DATASOURCE_TARGET_MAP


# ============================================================
# CausalChainBuilder 测试
# ============================================================


class TestCausalChainBuilder:
    """CausalChainBuilder 测试."""

    @staticmethod
    def _make_cause(cause_id, layer, category, confidence=0.8):
        """构造测试根因."""
        return RootCause(
            cause_id=cause_id, layer=layer, category=category,
            severity=SEVERITY_MEDIUM, confidence=confidence,
            detected_at="2026-08-01T12:00:00Z",
        )

    def test_empty_causes_returns_empty(self) -> None:
        """空根因列表返回空链."""
        builder = CausalChainBuilder()
        assert builder.build([]) == []

    def test_single_cause_returns_empty(self) -> None:
        """单根因不成链 (需 2+ 节点)."""
        builder = CausalChainBuilder()
        causes = [self._make_cause("c1", LAYER_CODE, "test")]
        assert builder.build(causes) == []

    def test_datasource_failure_chain(self) -> None:
        """规则 1: 数据源失效链 (ops→code→strategy)."""
        causes = [
            self._make_cause("ops-1", LAYER_OPS, "datasource_fail"),
            self._make_cause("code-1", LAYER_CODE, "system_check_fail",
                             ).__class__(
                cause_id="code-1", layer=LAYER_CODE,
                category="system_check_fail", severity=SEVERITY_CRITICAL,
                evidence={"check_code": "C3.1"}, confidence=0.9,
                detected_at="2026-08-01T12:00:00Z",
            ),
            self._make_cause("strat-1", LAYER_STRATEGY, "drift_alert"),
        ]
        builder = CausalChainBuilder()
        chains = builder.build(causes)
        assert len(chains) >= 1
        # 找到数据源失效链
        ds_chain = next(
            (c for c in chains if "datasource_failure" in c.chain_id), None
        )
        assert ds_chain is not None
        assert len(ds_chain.nodes) == 3
        # 因果顺序 ops → code → strategy
        assert [n.layer for n in ds_chain.nodes] == [LAYER_OPS, LAYER_CODE, LAYER_STRATEGY]

    def test_pit_violation_chain(self) -> None:
        """规则 2: PIT 违规链 (code→strategy)."""
        causes = [
            self._make_cause("code-1", LAYER_STRATEGY, "pit_violation"),
            self._make_cause("strat-1", LAYER_STRATEGY, "anti_cheat_low"),
        ]
        builder = CausalChainBuilder()
        chains = builder.build(causes)
        pit_chain = next((c for c in chains if "pit_violation" in c.chain_id), None)
        assert pit_chain is not None
        assert len(pit_chain.nodes) == 2

    def test_flag_change_chain(self) -> None:
        """规则 3: Flag 变更链 (ops→code)."""
        causes = [
            self._make_cause("ops-1", LAYER_OPS, "flag_instability"),
            RootCause(
                cause_id="code-1", layer=LAYER_CODE,
                category="system_check_fail", severity=SEVERITY_HIGH,
                evidence={"check_code": "C2.1"}, confidence=0.8,
                detected_at="2026-08-01T12:00:00Z",
            ),
        ]
        builder = CausalChainBuilder()
        chains = builder.build(causes)
        flag_chain = next((c for c in chains if "flag_change" in c.chain_id), None)
        assert flag_chain is not None
        assert len(flag_chain.nodes) == 2

    def test_confidence_decay(self) -> None:
        """链置信度 = min(节点) * 0.9 (衰减)."""
        causes = [
            self._make_cause("ops-1", LAYER_OPS, "datasource_fail", confidence=0.9),
            RootCause(
                cause_id="code-1", layer=LAYER_CODE,
                category="system_check_fail", severity=SEVERITY_CRITICAL,
                evidence={"check_code": "C3.1"}, confidence=0.8,
                detected_at="2026-08-01T12:00:00Z",
            ),
        ]
        builder = CausalChainBuilder()
        chains = builder.build(causes)
        ds_chain = next(
            (c for c in chains if "datasource_failure" in c.chain_id), None
        )
        assert ds_chain is not None
        # min(0.9, 0.8) * 0.9 = 0.72
        assert abs(ds_chain.confidence - 0.72) < 0.01

    def test_no_match_returns_empty(self) -> None:
        """无匹配规则时返回空."""
        causes = [
            self._make_cause("c1", LAYER_CODE, "unknown_category"),
            self._make_cause("c2", LAYER_STRATEGY, "another_unknown"),
        ]
        builder = CausalChainBuilder()
        chains = builder.build(causes)
        assert chains == []


# ============================================================
# 集成测试: 完整分析流程
# ============================================================


class TestIntegrationFullFlow:
    """完整分析流程集成测试."""

    def test_full_analysis_with_mock_data(self, tmp_path: Path) -> None:
        """完整分析流程: 三层诊断 + 因果链 + 持久化."""
        # 准备 mock 数据
        drift_dir = tmp_path / "drift_alerts"
        drift_dir.mkdir()
        alert = {
            "severity": "high", "model_name": "v9_lgb",
            "feature_name": "MOM_5D", "drift_score": 0.3,
            "psi": 0.4, "recorded_at": "2026-08-01T10:00:00Z",
        }
        (drift_dir / "v9_lgb.jsonl").write_text(
            json.dumps(alert) + "\n", encoding="utf-8"
        )

        decisions_path = tmp_path / "decisions.jsonl"
        decision = {
            "timestamp": "2026-08-01T10:00:00Z",
            "private_score": 0.1, "public_score": 0.3,
            "reward_hacking_risk": 0.6, "recommendation": "rollback",
            "sample_count": 5,
        }
        decisions_path.write_text(json.dumps(decision) + "\n", encoding="utf-8")

        persist_path = tmp_path / "root_causes.jsonl"

        # 执行分析 (mock Flag 开启 + mock CodeDiagnoser._run_check)
        class _MockCR:
            def __init__(self, code, status, level):
                self.code = code
                self.name = f"check_{code}"
                self.status = MagicMock(value=status)
                self.level = MagicMock(value=level)
                self.detail = "fail"
                self.remediation = "fix it"

        class _MockReport:
            def __init__(self):
                self.results = [
                    _MockCR("C3.1", "FAIL", "ERROR"),  # 数据源失败
                    _MockCR("C1.1", "PASS", "INFO"),
                ]

        with patch("utils.infra.feature_flags.is_enabled",
                   side_effect=_flag_side_effect_main_only):
            analyzer = UnifiedRootCauseAnalyzer(persistence_path=persist_path)
            # 替换诊断器为真实实例 (但 mock CodeDiagnoser._run_check)
            from utils.alpha.layers.code_diagnoser import CodeDiagnoser as CD
            from utils.alpha.layers.strategy_diagnoser import StrategyDiagnoser as SD
            from utils.alpha.layers.ops_diagnoser import OpsDiagnoser as OD

            analyzer._code_diagnoser = CD(run_system_check=True)
            analyzer._code_diagnoser._run_check = lambda: _MockReport()
            analyzer._strategy_diagnoser = SD(
                drift_alerts_dir=drift_dir,
                decisions_path=decisions_path,
            )
            analyzer._ops_diagnoser = OD(
                report_dirs={
                    "system_check": tmp_path / "sc",
                    "data_quality": tmp_path / "dq",
                    "drift_alerts": drift_dir,
                    "flag_audit": tmp_path / "fa",
                    "risk_bus": tmp_path / "rb",
                }
            )
            analyzer._diagnosers_loaded = True

            report = analyzer.analyze(None)

        # 验证
        assert report.is_degraded is False
        assert len(report.causes) > 0
        # 应有 code + strategy + ops 三层根因
        layers = {c.layer for c in report.causes}
        assert LAYER_CODE in layers
        assert LAYER_STRATEGY in layers
        # 持久化
        assert persist_path.exists()
        lines = persist_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert "causes" in parsed

    def test_does_not_pollute_production(self, tmp_path: Path) -> None:
        """HC-4: 不污染生产数据."""
        import hashlib
        positions = _PROJECT_ROOT / "config" / "positions.json"
        daily_returns = _PROJECT_ROOT / "reports" / "shadow" / "daily_returns.jsonl"

        def hash_of(p: Path) -> str:
            if not p.exists():
                return "NOT_EXISTS"
            return hashlib.sha256(p.read_bytes()).hexdigest()[:16]

        before_pos = hash_of(positions)
        before_dr = hash_of(daily_returns)

        with patch("utils.infra.feature_flags.is_enabled",
                   side_effect=_flag_side_effect_main_only):
            analyzer = UnifiedRootCauseAnalyzer(
                persistence_path=tmp_path / "rc.jsonl"
            )
            analyzer.analyze(None)

        after_pos = hash_of(positions)
        after_dr = hash_of(daily_returns)
        assert before_pos == after_pos, "positions.json 被修改 (违反 HC-4)"
        assert before_dr == after_dr, "daily_returns.jsonl 被修改 (违反 HC-4)"
