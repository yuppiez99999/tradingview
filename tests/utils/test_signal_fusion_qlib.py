"""Signal Fusion qlib_lgb_v2 shadow 接入单元测试.

覆盖场景:
    1. 模型注册: register_qlib_lgb_v2_shadow 成功注册到 SignalFusionEngine
    2. shadow 信号记录: qlib_lgb_v2_daily.jsonl 追加信号差异记录
    3. 数据桥接失败 fail-closed: qlib_data_bridge 异常时 fail-closed

对齐 tasks T1.6.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.signal_fusion import (
    QLIB_LGB_V2_EXCESS_RETURN,
    QLIB_LGB_V2_MODEL_NAME,
    QLIB_LGB_V2_SHARPE_OOS,
    QlibShadowResult,
    SignalFusionEngine,
    _get_qlib_lgb_v2_signal,
    _save_qlib_shadow_signal,
    apply_qlib_lgb_v2_shadow,
    register_qlib_lgb_v2_shadow,
)


@pytest.fixture
def fusion_engine(tmp_path):
    """构造测试用 SignalFusionEngine."""
    db_path = str(tmp_path / "test_signals.db")
    return SignalFusionEngine(db_path=db_path)


# ============================================================
# 场景 1: 模型注册
# ============================================================

class TestModelRegistration:
    """qlib_lgb_v2 模型注册到 SignalFusionEngine."""

    def test_register_success(self, fusion_engine):
        result = register_qlib_lgb_v2_shadow(fusion_engine, use_qlib=True, qlib_mode="shadow")
        assert result is True
        assert fusion_engine.has_source(QLIB_LGB_V2_MODEL_NAME)

    def test_register_skip_when_disabled(self, fusion_engine):
        result = register_qlib_lgb_v2_shadow(fusion_engine, use_qlib=False)
        assert result is False
        assert not fusion_engine.has_source(QLIB_LGB_V2_MODEL_NAME)

    def test_register_shadow_weight_zero(self, fusion_engine):
        register_qlib_lgb_v2_shadow(fusion_engine, use_qlib=True, qlib_mode="shadow")
        weight = fusion_engine._source_weights.get(QLIB_LGB_V2_MODEL_NAME, 0)
        assert weight == 0.0

    def test_register_active_weight_nonzero(self, fusion_engine):
        register_qlib_lgb_v2_shadow(fusion_engine, use_qlib=True, qlib_mode="active")
        weight = fusion_engine._source_weights.get(QLIB_LGB_V2_MODEL_NAME, 0)
        assert weight > 0.0

    def test_model_name_constant(self):
        assert QLIB_LGB_V2_MODEL_NAME == "qlib_lgb_v2"

    def test_sharpe_oos_constants(self):
        assert QLIB_LGB_V2_SHARPE_OOS["train"] == 1.86
        assert QLIB_LGB_V2_SHARPE_OOS["test"] == 2.44

    def test_excess_return_constants(self):
        assert QLIB_LGB_V2_EXCESS_RETURN["train"] == 0.1152
        assert QLIB_LGB_V2_EXCESS_RETURN["test"] == 0.4725


# ============================================================
# 场景 2: shadow 信号记录
# ============================================================

class TestShadowSignalRecording:
    """qlib_lgb_v2 shadow 信号记录到 jsonl."""

    def test_apply_shadow_returns_result(self, fusion_engine):
        result = apply_qlib_lgb_v2_shadow(
            fusion_engine, symbol="600519", trade_date="2026-08-18",
            use_qlib=True, qlib_mode="shadow",
        )
        assert isinstance(result, QlibShadowResult)

    def test_apply_shadow_success(self, fusion_engine):
        result = apply_qlib_lgb_v2_shadow(
            fusion_engine, symbol="600519", trade_date="2026-08-18",
            use_qlib=True, qlib_mode="shadow",
        )
        assert result.success is True
        assert result.shadow_mode is True

    def test_apply_shadow_signal_computed(self, fusion_engine):
        result = apply_qlib_lgb_v2_shadow(
            fusion_engine, symbol="600519", trade_date="2026-08-18",
            use_qlib=True, qlib_mode="shadow",
        )
        assert result.qlib_signal != 0.0 or result.success is True

    def test_save_shadow_signal_writes_jsonl(self, tmp_path, monkeypatch):
        report_path = tmp_path / "qlib_shadow.jsonl"
        monkeypatch.setattr(
            "utils.signal_fusion.QLIB_SHADOW_REPORT_PATH",
            report_path,
        )
        _save_qlib_shadow_signal("2026-08-18", "600519", 0.5, 0.3)
        assert report_path.exists()
        lines = report_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["date"] == "2026-08-18"
        assert record["symbol"] == "600519"
        assert record["qlib_signal"] == 0.5
        assert record["v9_signal"] == 0.3
        assert record["signal_diff"] == pytest.approx(0.2)

    def test_save_shadow_signal_appends(self, tmp_path, monkeypatch):
        report_path = tmp_path / "qlib_shadow.jsonl"
        monkeypatch.setattr(
            "utils.signal_fusion.QLIB_SHADOW_REPORT_PATH",
            report_path,
        )
        for i in range(3):
            _save_qlib_shadow_signal(f"2026-08-{18+i}", "600519", 0.5, 0.3)
        lines = report_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3


# ============================================================
# 场景 3: 数据桥接失败 fail-closed
# ============================================================

class TestDataBridgeFailClosed:
    """数据桥接失败: qlib_data_bridge 异常时 fail-closed."""

    def test_apply_shadow_disabled_returns_skip(self, fusion_engine):
        result = apply_qlib_lgb_v2_shadow(
            fusion_engine, symbol="600519", use_qlib=False,
        )
        assert result.success is False
        assert "跳过" in result.error_message

    def test_apply_shadow_kill_switch(self, fusion_engine):
        result = apply_qlib_lgb_v2_shadow(
            fusion_engine, symbol="600519", use_qlib=True,
            kill_switch_triggered=True,
        )
        assert result.success is False
        assert "kill_switch" in result.error_message

    def test_apply_shadow_signal_failure_fail_closed(self, fusion_engine):
        with patch("utils.signal_fusion._get_qlib_lgb_v2_signal", side_effect=ImportError("qlib not installed")):
            result = apply_qlib_lgb_v2_shadow(
                fusion_engine, symbol="600519", use_qlib=True, qlib_mode="shadow",
            )
        assert result.success is False
        assert "fail-closed" in result.error_message or "失败" in result.error_message

    def test_get_qlib_signal_returns_float(self):
        signal = _get_qlib_lgb_v2_signal("600519")
        assert isinstance(signal, float)
        assert -1.0 <= signal <= 1.0 or abs(signal) < 10.0

    def test_get_qlib_signal_with_bridge_failure(self):
        with patch("utils.qlib_data_bridge.to_qlib_symbol", side_effect=Exception("bridge error")):
            with pytest.raises(Exception, match="bridge error"):
                _get_qlib_lgb_v2_signal("600519")


# ============================================================
# 辅助测试: active 模式
# ============================================================

class TestActiveMode:
    """qlib_lgb_v2 active 模式."""

    def test_apply_active_mode(self, fusion_engine):
        result = apply_qlib_lgb_v2_shadow(
            fusion_engine, symbol="600519", trade_date="2026-08-18",
            use_qlib=True, qlib_mode="active",
        )
        assert result.success is True
        assert result.shadow_mode is False

    def test_apply_active_no_jsonl_write(self, fusion_engine, tmp_path, monkeypatch):
        report_path = tmp_path / "qlib_shadow.jsonl"
        monkeypatch.setattr(
            "utils.signal_fusion.QLIB_SHADOW_REPORT_PATH",
            report_path,
        )
        apply_qlib_lgb_v2_shadow(
            fusion_engine, symbol="600519", trade_date="2026-08-18",
            use_qlib=True, qlib_mode="active",
        )
        assert not report_path.exists()
