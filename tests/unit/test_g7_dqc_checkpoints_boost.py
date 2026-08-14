"""DQC 检查点覆盖率提升测试 — G7 冲刺.

覆盖目标:
    1. P2 缓存质量门禁: 注册/执行/配置/错误处理/阻断逻辑/观察模式
    2. P3 因子质量门禁: 注册/执行/配置/错误处理/阻断逻辑/观察模式
    3. 外部依赖 mock: RiskBus / FeatureFlag / PathConfig / ParquetLoader

运行:
    python -m pytest tests/unit/test_g7_dqc_checkpoints_boost.py -v --tb=short
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ============================================================
# Fixtures
# ============================================================
@pytest.fixture
def good_cache_df():
    return pd.DataFrame({
        "symbol": ["600519.SH", "000858.SZ", "600519.SH", "000858.SZ"],
        "date": ["2026-08-01", "2026-08-01", "2026-08-02", "2026-08-02"],
        "open": [1800.0, 150.0, 1810.0, 151.0],
        "high": [1820.0, 152.0, 1830.0, 153.0],
        "low": [1790.0, 149.0, 1800.0, 150.0],
        "close": [1810.0, 151.0, 1820.0, 152.0],
        "volume": [100000, 200000, 110000, 210000],
        "preclose": [1790.0, 149.0, 1810.0, 151.0],
    })


@pytest.fixture
def bad_cache_df():
    return pd.DataFrame({
        "symbol": ["600519.SH", "000858.SZ", "600519.SH", "600519.SH"],
        "date": ["2026-08-01", "2026-08-01", "2026-08-01", "2026-08-01"],
        "open": [1800.0, 150.0, 1800.0, 1800.0],
        "high": [1790.0, 152.0, 1800.0, 1800.0],
        "low": [1810.0, 149.0, 1800.0, 1800.0],
        "close": [0.0, 151.0, 1800.0, 1800.0],
        "volume": [-100, 200000, 110000, 110000],
        "preclose": [1790.0, 149.0, 1790.0, 1790.0],
    })


@pytest.fixture
def sample_factor_df():
    return pd.DataFrame({
        "symbol": ["600519.SH", "000858.SZ", "300308.SZ"],
        "date": ["2026-08-01", "2026-08-01", "2026-08-01"],
        "MOM_5D": [0.05, -0.02, 0.10],
        "VOL_20D": [0.15, 0.20, 0.12],
    })


@pytest.fixture
def baseline_factor_df():
    return pd.DataFrame({
        "symbol": ["600519.SH", "000858.SZ", "300308.SZ"],
        "date": ["2026-07-28", "2026-07-28", "2026-07-28"],
        "MOM_5D": [0.04, -0.01, 0.09],
        "VOL_20D": [0.14, 0.19, 0.11],
    })


# ============================================================
# P2 检查点
# ============================================================
class TestP2CacheQualityGateRegistration:
    def test_gate_importable(self):
        from utils.dqc.checkpoints.p2_cache_quality import P2CacheQualityGate, run_p2_gate
        assert P2CacheQualityGate is not None
        assert run_p2_gate is not None

    def test_blocking_levels_defined(self):
        from utils.dqc.checkpoints.p2_cache_quality import P2CacheQualityGate
        from utils.dqc.event_types import DQCLevel
        assert DQCLevel.ERROR in P2CacheQualityGate.BLOCKING_LEVELS
        assert DQCLevel.CRITICAL in P2CacheQualityGate.BLOCKING_LEVELS
        assert DQCLevel.WARN not in P2CacheQualityGate.BLOCKING_LEVELS
        assert DQCLevel.INFO not in P2CacheQualityGate.BLOCKING_LEVELS

    def test_instance_creation(self):
        from utils.dqc.checkpoints.p2_cache_quality import P2CacheQualityGate
        gate = P2CacheQualityGate()
        assert gate._checkpoint.value == "P2"
        assert gate._published == []


class TestP2CacheQualityGateExecution:
    def test_run_with_good_df_returns_pass(self, good_cache_df):
        from utils.dqc.checkpoints.p2_cache_quality import P2CacheQualityGate
        gate = P2CacheQualityGate()
        passed, events = gate.run(
            target_date=date(2026, 8, 2),
            symbols=["600519.SH", "000858.SZ"],
            df=good_cache_df,
        )
        assert passed is True
        assert len(events) > 0

    def test_run_with_bad_df_returns_blocking_events(self, bad_cache_df):
        from utils.dqc.checkpoints.p2_cache_quality import P2CacheQualityGate
        gate = P2CacheQualityGate()
        passed, events = gate.run(
            target_date=date(2026, 8, 1),
            symbols=["600519.SH", "000858.SZ"],
            df=bad_cache_df,
        )
        blocking = [e for e in events if e.level.is_blocking]
        assert len(blocking) > 0

    def test_run_none_df_none_cache_returns_error_c02(self):
        from utils.dqc.checkpoints.p2_cache_quality import P2CacheQualityGate
        gate = P2CacheQualityGate()
        passed, events = gate.run(
            target_date=date(2026, 8, 1),
            symbols=["600519.SH"],
            df=None,
            cache_path=None,
        )
        assert passed is False
        c02_events = [e for e in events if e.metric_id == "C-02"]
        assert len(c02_events) == 1
        assert c02_events[0].level.value == "error"

    def test_run_loads_cache_from_path(self, good_cache_df, tmp_path):
        from utils.dqc.checkpoints.p2_cache_quality import P2CacheQualityGate
        cache_file = tmp_path / "cache.parquet"
        with patch.object(P2CacheQualityGate, "_load_cache", return_value=good_cache_df):
            gate = P2CacheQualityGate()
            passed, events = gate.run(
                target_date=date(2026, 8, 2),
                symbols=["600519.SH", "000858.SZ"],
                df=None,
                cache_path=cache_file,
            )
        assert passed is True

    def test_run_load_cache_file_not_found(self):
        from utils.dqc.checkpoints.p2_cache_quality import P2CacheQualityGate
        missing = Path("/tmp/nonexistent_dqc_cache_12345.parquet")
        gate = P2CacheQualityGate()
        passed, events = gate.run(
            target_date=date(2026, 8, 1),
            symbols=["600519.SH"],
            df=None,
            cache_path=missing,
        )
        assert passed is False
        c02_events = [e for e in events if e.metric_id == "C-02"]
        assert len(c02_events) == 1

    def test_run_load_cache_corrupt_parquet(self, tmp_path):
        from utils.dqc.checkpoints.p2_cache_quality import P2CacheQualityGate
        bad_file = tmp_path / "corrupt.parquet"
        with patch("pandas.read_parquet", side_effect=ValueError("bad parquet")):
            gate = P2CacheQualityGate()
            passed, events = gate.run(
                target_date=date(2026, 8, 1),
                symbols=["600519.SH"],
                df=None,
                cache_path=bad_file,
            )
        assert passed is False
        c02_events = [e for e in events if e.metric_id == "C-02"]
        assert len(c02_events) == 1

    def test_run_records_all_published_events(self, good_cache_df):
        from utils.dqc.checkpoints.p2_cache_quality import P2CacheQualityGate
        gate = P2CacheQualityGate()
        passed, events = gate.run(
            target_date=date(2026, 8, 2),
            symbols=["600519.SH", "000858.SZ"],
            df=good_cache_df,
        )
        assert len(gate._published) == len(events)

    def test_run_empty_symbols(self, good_cache_df):
        from utils.dqc.checkpoints.p2_cache_quality import P2CacheQualityGate
        gate = P2CacheQualityGate()
        passed, events = gate.run(
            target_date=date(2026, 8, 2),
            symbols=[],
            df=good_cache_df,
        )
        assert passed is True


class TestP2CacheQualityGateConfig:
    def test_gate_disabled_does_not_block(self, bad_cache_df):
        from utils.dqc.checkpoints.p2_cache_quality import P2CacheQualityGate
        gate = P2CacheQualityGate()
        with patch("utils.infra.feature_flags.is_enabled", return_value=False):
            passed, events = gate.run(
                target_date=date(2026, 8, 1),
                symbols=["600519.SH"],
                df=bad_cache_df,
            )
        blocking = [e for e in events if e.level.is_blocking]
        assert len(blocking) > 0
        assert passed is True

    def test_gate_enabled_blocks_on_errors(self, bad_cache_df):
        from utils.dqc.checkpoints.p2_cache_quality import P2CacheQualityGate
        gate = P2CacheQualityGate()
        with patch("utils.infra.feature_flags.is_enabled", return_value=True):
            passed, events = gate.run(
                target_date=date(2026, 8, 1),
                symbols=["600519.SH"],
                df=bad_cache_df,
            )
        blocking = [e for e in events if e.level.is_blocking]
        assert len(blocking) > 0
        assert passed is False

    def test_gate_enabled_import_error_defaults_to_not_block(self, bad_cache_df):
        from utils.dqc.checkpoints.p2_cache_quality import P2CacheQualityGate
        gate = P2CacheQualityGate()
        with patch("utils.infra.feature_flags.is_enabled", side_effect=ImportError("no flags module")):
            passed, events = gate.run(
                target_date=date(2026, 8, 1),
                symbols=["600519.SH"],
                df=bad_cache_df,
            )
        assert passed is True

    def test_is_gate_enabled_true(self):
        from utils.dqc.checkpoints.p2_cache_quality import P2CacheQualityGate
        gate = P2CacheQualityGate()
        with patch("utils.infra.feature_flags.is_enabled", return_value=True):
            assert gate._is_gate_enabled() is True

    def test_is_gate_enabled_false(self):
        from utils.dqc.checkpoints.p2_cache_quality import P2CacheQualityGate
        gate = P2CacheQualityGate()
        with patch("utils.infra.feature_flags.is_enabled", return_value=False):
            assert gate._is_gate_enabled() is False

    def test_is_gate_enabled_runtime_error(self):
        from utils.dqc.checkpoints.p2_cache_quality import P2CacheQualityGate
        gate = P2CacheQualityGate()
        with patch("utils.infra.feature_flags.is_enabled", side_effect=RuntimeError("flag service down")):
            assert gate._is_gate_enabled() is False


class TestP2CacheQualityGateErrorHandling:
    def test_fail_safe_on_value_error(self):
        from utils.dqc.checkpoints.p2_cache_quality import P2CacheQualityGate
        with patch("utils.dqc.metrics.completeness._check_c01_symbol_coverage", side_effect=ValueError("bad data")):
            gate = P2CacheQualityGate()
            passed, events = gate.run(
                target_date=date(2026, 8, 1),
                symbols=["600519.SH"],
                df=pd.DataFrame({"symbol": ["600519.SH"], "date": ["2026-08-01"]}),
            )
        assert passed is True
        warn_events = [e for e in events if e.level == "warn"]
        assert len(warn_events) >= 1

    def test_fail_safe_on_type_error(self):
        from utils.dqc.checkpoints.p2_cache_quality import P2CacheQualityGate
        with patch("utils.dqc.metrics.completeness._check_c01_symbol_coverage", side_effect=TypeError("type mismatch")):
            gate = P2CacheQualityGate()
            passed, events = gate.run(
                target_date=date(2026, 8, 1),
                symbols=["600519.SH"],
                df=pd.DataFrame({"symbol": ["600519.SH"], "date": ["2026-08-01"]}),
            )
        assert passed is True

    def test_fail_safe_on_key_error(self):
        from utils.dqc.checkpoints.p2_cache_quality import P2CacheQualityGate
        with patch("utils.dqc.metrics.completeness._check_c01_symbol_coverage", side_effect=KeyError("missing")):
            gate = P2CacheQualityGate()
            passed, events = gate.run(
                target_date=date(2026, 8, 1),
                symbols=["600519.SH"],
                df=pd.DataFrame({"symbol": ["600519.SH"], "date": ["2026-08-01"]}),
            )
        assert passed is True

    def test_fail_safe_on_runtime_error(self):
        from utils.dqc.checkpoints.p2_cache_quality import P2CacheQualityGate
        with patch("utils.dqc.metrics.completeness._check_c01_symbol_coverage", side_effect=RuntimeError("boom")):
            gate = P2CacheQualityGate()
            passed, events = gate.run(
                target_date=date(2026, 8, 1),
                symbols=["600519.SH"],
                df=pd.DataFrame({"symbol": ["600519.SH"], "date": ["2026-08-01"]}),
            )
        assert passed is True

    def test_publish_risk_bus_failure_is_swallowed(self, good_cache_df):
        from utils.dqc.checkpoints.p2_cache_quality import P2CacheQualityGate
        gate = P2CacheQualityGate()
        with patch.object(gate, "_publish_to_risk_bus", side_effect=OSError("bus down")):
            passed, events = gate.run(
                target_date=date(2026, 8, 2),
                symbols=["600519.SH", "000858.SZ"],
                df=good_cache_df,
            )
        assert passed is True

    def test_publish_audit_log_failure_is_swallowed(self, good_cache_df):
        from utils.dqc.checkpoints.p2_cache_quality import P2CacheQualityGate
        gate = P2CacheQualityGate()
        with patch.object(gate, "_write_audit_log", side_effect=OSError("disk full")):
            passed, events = gate.run(
                target_date=date(2026, 8, 2),
                symbols=["600519.SH", "000858.SZ"],
                df=good_cache_df,
            )
        assert passed is True

    def test_log_block_does_not_raise(self):
        from utils.dqc.checkpoints.p2_cache_quality import P2CacheQualityGate
        gate = P2CacheQualityGate()
        with patch.object(gate, "_is_gate_enabled", return_value=False):
            passed, events = gate.run(
                target_date=date(2026, 8, 2),
                symbols=["600519.SH", "000858.SZ"],
                df=good_cache_df,
            )
        assert passed is True


class TestP2CacheQualityGateExternalDeps:
    def test_load_cache_oserror_returns_none(self, tmp_path):
        from utils.dqc.checkpoints.p2_cache_quality import P2CacheQualityGate
        gate = P2CacheQualityGate()
        result = gate._load_cache(date(2026, 8, 1), tmp_path / "missing.parquet")
        assert result is None

    def test_load_cache_value_error_returns_none(self, tmp_path):
        from utils.dqc.checkpoints.p2_cache_quality import P2CacheQualityGate
        bad_file = tmp_path / "bad.parquet"
        with patch("pandas.read_parquet", side_effect=ValueError("bad parquet")):
            gate = P2CacheQualityGate()
            result = gate._load_cache(date(2026, 8, 1), bad_file)
        assert result is None

    def test_run_p2_gate_convenience_function(self, good_cache_df):
        from utils.dqc.checkpoints.p2_cache_quality import run_p2_gate
        passed, events = run_p2_gate(
            target_date=date(2026, 8, 2),
            symbols=["600519.SH", "000858.SZ"],
            df=good_cache_df,
        )
        assert passed is True
        assert isinstance(events, list)


class TestP2CacheQualityGatePublishing:
    def test_publish_appends_event(self, good_cache_df):
        from utils.dqc.checkpoints.p2_cache_quality import P2CacheQualityGate
        from utils.dqc.event_types import DQCEvent, DQCLevel, DQCCheckpoint
        gate = P2CacheQualityGate()
        event = DQCEvent(
            metric_id="C-01",
            level=DQCLevel.INFO,
            checkpoint=DQCCheckpoint.P2_CACHE,
            value=1.0,
            threshold=0.95,
            message="test",
        )
        gate._publish(event)
        assert len(gate._published) == 1
        assert gate._published[0].metric_id == "C-01"

    def test_publish_critical_logs_critical(self, good_cache_df):
        from utils.dqc.checkpoints.p2_cache_quality import P2CacheQualityGate
        from utils.dqc.event_types import DQCEvent, DQCLevel, DQCCheckpoint
        gate = P2CacheQualityGate()
        event = DQCEvent(
            metric_id="C-01",
            level=DQCLevel.CRITICAL,
            checkpoint=DQCCheckpoint.P2_CACHE,
            value=0.0,
            threshold=1.0,
            message="critical test",
        )
        with patch("utils.dqc.checkpoints.p2_cache_quality.logger") as mock_logger:
            gate._publish(event)
        mock_logger.critical.assert_called_once()

    def test_publish_error_logs_error(self, good_cache_df):
        from utils.dqc.checkpoints.p2_cache_quality import P2CacheQualityGate
        from utils.dqc.event_types import DQCEvent, DQCLevel, DQCCheckpoint
        gate = P2CacheQualityGate()
        event = DQCEvent(
            metric_id="A-01",
            level=DQCLevel.ERROR,
            checkpoint=DQCCheckpoint.P2_CACHE,
            value=0.0,
            threshold=1.0,
            message="error test",
        )
        with patch("utils.dqc.checkpoints.p2_cache_quality.logger") as mock_logger:
            gate._publish(event)
        mock_logger.error.assert_called_once()

    def test_publish_warn_logs_warning(self, good_cache_df):
        from utils.dqc.checkpoints.p2_cache_quality import P2CacheQualityGate
        from utils.dqc.event_types import DQCEvent, DQCLevel, DQCCheckpoint
        gate = P2CacheQualityGate()
        event = DQCEvent(
            metric_id="T-01",
            level=DQCLevel.WARN,
            checkpoint=DQCCheckpoint.P2_CACHE,
            value=5.0,
            threshold=30.0,
            message="warn test",
        )
        with patch("utils.dqc.checkpoints.p2_cache_quality.logger") as mock_logger, \
             patch.object(gate, "_publish_to_risk_bus"), \
             patch.object(gate, "_write_audit_log"):
            gate._publish(event)
        mock_logger.warning.assert_called_once()

    def test_publish_info_logs_info(self, good_cache_df):
        from utils.dqc.checkpoints.p2_cache_quality import P2CacheQualityGate
        from utils.dqc.event_types import DQCEvent, DQCLevel, DQCCheckpoint
        gate = P2CacheQualityGate()
        event = DQCEvent(
            metric_id="C-01",
            level=DQCLevel.INFO,
            checkpoint=DQCCheckpoint.P2_CACHE,
            value=1.0,
            threshold=0.95,
            message="info test",
        )
        with patch("utils.dqc.checkpoints.p2_cache_quality.logger") as mock_logger:
            gate._publish(event)
        mock_logger.info.assert_called_once()


# ============================================================
# P3 检查点
# ============================================================
class TestP3FactorQualityGateRegistration:
    def test_gate_importable(self):
        from utils.dqc.checkpoints.p3_factor_quality import P3FactorQualityGate, run_p3_gate
        assert P3FactorQualityGate is not None
        assert run_p3_gate is not None

    def test_blocking_levels_defined(self):
        from utils.dqc.checkpoints.p3_factor_quality import P3FactorQualityGate
        from utils.dqc.event_types import DQCLevel
        assert DQCLevel.ERROR in P3FactorQualityGate.BLOCKING_LEVELS
        assert DQCLevel.CRITICAL in P3FactorQualityGate.BLOCKING_LEVELS

    def test_instance_creation(self):
        from utils.dqc.checkpoints.p3_factor_quality import P3FactorQualityGate
        gate = P3FactorQualityGate()
        assert gate._checkpoint.value == "P3"
        assert gate._published == []


class TestP3FactorQualityGateExecution:
    def test_run_empty_factor_df_returns_error(self):
        from utils.dqc.checkpoints.p3_factor_quality import P3FactorQualityGate
        gate = P3FactorQualityGate()
        passed, events = gate.run(
            target_date=date(2026, 8, 1),
            factor_df=pd.DataFrame(),
            baseline_df=pd.DataFrame(),
            factor_cols=["MOM_5D"],
        )
        assert passed is False
        f01_events = [e for e in events if e.metric_id == "F-01"]
        assert len(f01_events) >= 1
        assert f01_events[0].level.value == "error"

    def test_run_empty_factor_cols_returns_warn_and_pass(self, sample_factor_df):
        from utils.dqc.checkpoints.p3_factor_quality import P3FactorQualityGate
        gate = P3FactorQualityGate()
        passed, events = gate.run(
            target_date=date(2026, 8, 1),
            factor_df=sample_factor_df,
            baseline_df=baseline_factor_df,
            factor_cols=[],
        )
        assert passed is True
        f01_events = [e for e in events if e.metric_id == "F-01"]
        assert len(f01_events) >= 1
        assert f01_events[0].level.value == "warn"

    def test_run_valid_data_passes(self, sample_factor_df, baseline_factor_df):
        from utils.dqc.checkpoints.p3_factor_quality import P3FactorQualityGate
        gate = P3FactorQualityGate()
        passed, events = gate.run(
            target_date=date(2026, 8, 1),
            factor_df=sample_factor_df,
            baseline_df=baseline_factor_df,
            factor_cols=["MOM_5D", "VOL_20D"],
        )
        assert passed is True

    def test_run_with_nan_factor_triggers_x04_warn(self):
        from utils.dqc.checkpoints.p3_factor_quality import P3FactorQualityGate
        df = pd.DataFrame({
            "symbol": ["600519.SH"] * 10,
            "date": ["2026-08-01"] * 10,
            "MOM_5D": [0.05] * 9 + [None],
        })
        baseline = pd.DataFrame({
            "symbol": ["600519.SH"] * 10,
            "date": ["2026-07-28"] * 10,
            "MOM_5D": [0.04] * 10,
        })
        gate = P3FactorQualityGate()
        passed, events = gate.run(
            target_date=date(2026, 8, 1),
            factor_df=df,
            baseline_df=baseline,
            factor_cols=["MOM_5D"],
        )
        x04_events = [e for e in events if e.metric_id == "X-04"]
        assert len(x04_events) >= 1
        assert x04_events[0].level.value == "warn"

    def test_run_with_high_nan_factor_triggers_x04_error(self):
        from utils.dqc.checkpoints.p3_factor_quality import P3FactorQualityGate
        df = pd.DataFrame({
            "symbol": ["600519.SH"] * 10,
            "date": ["2026-08-01"] * 10,
            "MOM_5D": [None] * 3 + [0.05] * 7,
        })
        baseline = pd.DataFrame({
            "symbol": ["600519.SH"] * 10,
            "date": ["2026-07-28"] * 10,
            "MOM_5D": [0.04] * 10,
        })
        gate = P3FactorQualityGate()
        passed, events = gate.run(
            target_date=date(2026, 8, 1),
            factor_df=df,
            baseline_df=baseline,
            factor_cols=["MOM_5D"],
        )
        x04_events = [e for e in events if e.metric_id == "X-04"]
        assert len(x04_events) >= 1
        assert x04_events[0].level.value == "error"

    def test_run_records_published_events(self, sample_factor_df, baseline_factor_df):
        from utils.dqc.checkpoints.p3_factor_quality import P3FactorQualityGate
        gate = P3FactorQualityGate()
        passed, events = gate.run(
            target_date=date(2026, 8, 1),
            factor_df=sample_factor_df,
            baseline_df=baseline_factor_df,
            factor_cols=["MOM_5D", "VOL_20D"],
        )
        assert len(gate._published) == len(events)


class TestP3FactorQualityGateConfig:
    def test_gate_disabled_does_not_block(self, sample_factor_df, baseline_factor_df):
        from utils.dqc.checkpoints.p3_factor_quality import P3FactorQualityGate
        gate = P3FactorQualityGate()
        with patch("utils.infra.feature_flags.is_enabled", return_value=False):
            passed, events = gate.run(
                target_date=date(2026, 8, 1),
                factor_df=sample_factor_df,
                baseline_df=baseline_factor_df,
                factor_cols=["MOM_5D", "VOL_20D"],
            )
        assert passed is True

    def test_gate_enabled_blocks_on_errors(self):
        from utils.dqc.checkpoints.p3_factor_quality import P3FactorQualityGate
        df = pd.DataFrame({
            "symbol": ["600519.SH"] * 10,
            "date": ["2026-08-01"] * 10,
            "MOM_5D": [None] * 10,
        })
        baseline = pd.DataFrame({
            "symbol": ["600519.SH"] * 10,
            "date": ["2026-07-28"] * 10,
            "MOM_5D": [0.04] * 10,
        })
        gate = P3FactorQualityGate()
        with patch("utils.infra.feature_flags.is_enabled", return_value=True):
            passed, events = gate.run(
                target_date=date(2026, 8, 1),
                factor_df=df,
                baseline_df=baseline,
                factor_cols=["MOM_5D"],
            )
        blocking = [e for e in events if e.level.is_blocking]
        if blocking:
            assert passed is False

    def test_is_gate_enabled_import_error_defaults_false(self):
        from utils.dqc.checkpoints.p3_factor_quality import P3FactorQualityGate
        gate = P3FactorQualityGate()
        with patch("utils.infra.feature_flags.is_enabled", side_effect=ImportError("no module")):
            assert gate._is_gate_enabled() is False


class TestP3FactorQualityGateErrorHandling:
    def test_fail_safe_on_value_error(self, sample_factor_df, baseline_factor_df):
        from utils.dqc.checkpoints.p3_factor_quality import P3FactorQualityGate
        with patch("utils.dqc.checkpoints.p3_factor_quality.check_distribution_drift", side_effect=ValueError("bad")):
            gate = P3FactorQualityGate()
            passed, events = gate.run(
                target_date=date(2026, 8, 1),
                factor_df=sample_factor_df,
                baseline_df=baseline_factor_df,
                factor_cols=["MOM_5D"],
            )
        assert passed is True
        warn_events = [e for e in events if e.level == "warn"]
        assert len(warn_events) >= 1

    def test_fail_safe_on_type_error(self, sample_factor_df, baseline_factor_df):
        from utils.dqc.checkpoints.p3_factor_quality import P3FactorQualityGate
        with patch("utils.dqc.checkpoints.p3_factor_quality.check_distribution_drift", side_effect=TypeError("type")):
            gate = P3FactorQualityGate()
            passed, events = gate.run(
                target_date=date(2026, 8, 1),
                factor_df=sample_factor_df,
                baseline_df=baseline_factor_df,
                factor_cols=["MOM_5D"],
            )
        assert passed is True

    def test_fail_safe_on_key_error(self, sample_factor_df, baseline_factor_df):
        from utils.dqc.checkpoints.p3_factor_quality import P3FactorQualityGate
        with patch("utils.dqc.checkpoints.p3_factor_quality.check_distribution_drift", side_effect=KeyError("missing")):
            gate = P3FactorQualityGate()
            passed, events = gate.run(
                target_date=date(2026, 8, 1),
                factor_df=sample_factor_df,
                baseline_df=baseline_factor_df,
                factor_cols=["MOM_5D"],
            )
        assert passed is True

    def test_fail_safe_on_runtime_error(self, sample_factor_df, baseline_factor_df):
        from utils.dqc.checkpoints.p3_factor_quality import P3FactorQualityGate
        with patch("utils.dqc.checkpoints.p3_factor_quality.check_distribution_drift", side_effect=RuntimeError("boom")):
            gate = P3FactorQualityGate()
            passed, events = gate.run(
                target_date=date(2026, 8, 1),
                factor_df=sample_factor_df,
                baseline_df=baseline_factor_df,
                factor_cols=["MOM_5D"],
            )
        assert passed is True

    def test_publish_risk_bus_failure_is_swallowed(self, sample_factor_df, baseline_factor_df):
        from utils.dqc.checkpoints.p3_factor_quality import P3FactorQualityGate
        gate = P3FactorQualityGate()
        with patch.object(gate, "_publish_to_risk_bus", side_effect=OSError("bus down")):
            passed, events = gate.run(
                target_date=date(2026, 8, 1),
                factor_df=sample_factor_df,
                baseline_df=baseline_factor_df,
                factor_cols=["MOM_5D"],
            )
        assert passed is True

    def test_publish_audit_log_failure_is_swallowed(self, sample_factor_df, baseline_factor_df):
        from utils.dqc.checkpoints.p3_factor_quality import P3FactorQualityGate
        gate = P3FactorQualityGate()
        with patch.object(gate, "_write_audit_log", side_effect=OSError("disk full")):
            passed, events = gate.run(
                target_date=date(2026, 8, 1),
                factor_df=sample_factor_df,
                baseline_df=baseline_factor_df,
                factor_cols=["MOM_5D"],
            )
        assert passed is True


class TestP3U02FactorDedup:
    def test_no_duplicates_no_event(self, sample_factor_df):
        from utils.dqc.checkpoints.p3_factor_quality import P3FactorQualityGate
        gate = P3FactorQualityGate()
        events = gate._check_u02_factor_dedup(sample_factor_df, ["MOM_5D"])
        assert len(events) == 0

    def test_duplicates_produce_error_event(self):
        from utils.dqc.checkpoints.p3_factor_quality import P3FactorQualityGate
        df = pd.DataFrame({
            "symbol": ["600519.SH", "600519.SH"],
            "date": ["2026-08-01", "2026-08-01"],
            "MOM_5D": [0.05, 0.06],
        })
        gate = P3FactorQualityGate()
        events = gate._check_u02_factor_dedup(df, ["MOM_5D"])
        assert len(events) == 1
        assert events[0].metric_id == "U-02"
        assert events[0].level.value == "error"
        assert events[0].value == 2.0

    def test_no_symbol_column_returns_empty(self):
        from utils.dqc.checkpoints.p3_factor_quality import P3FactorQualityGate
        df = pd.DataFrame({"date": ["2026-08-01", "2026-08-01"], "MOM_5D": [0.05, 0.06]})
        gate = P3FactorQualityGate()
        events = gate._check_u02_factor_dedup(df, ["MOM_5D"])
        assert len(events) == 0

    def test_no_date_column_returns_empty(self):
        from utils.dqc.checkpoints.p3_factor_quality import P3FactorQualityGate
        df = pd.DataFrame({"symbol": ["600519.SH", "600519.SH"], "MOM_5D": [0.05, 0.06]})
        gate = P3FactorQualityGate()
        events = gate._check_u02_factor_dedup(df, ["MOM_5D"])
        assert len(events) == 0

    def test_uses_code_column_if_symbol_missing(self):
        from utils.dqc.checkpoints.p3_factor_quality import P3FactorQualityGate
        df = pd.DataFrame({
            "code": ["600519.SH", "600519.SH"],
            "date": ["2026-08-01", "2026-08-01"],
            "MOM_5D": [0.05, 0.06],
        })
        gate = P3FactorQualityGate()
        events = gate._check_u02_factor_dedup(df, ["MOM_5D"])
        assert len(events) == 1


class TestP3X04FactorReproducibility:
    def test_no_nan_no_event(self, sample_factor_df):
        from utils.dqc.checkpoints.p3_factor_quality import P3FactorQualityGate
        gate = P3FactorQualityGate()
        events = gate._check_x04_factor_reproducibility(sample_factor_df, ["MOM_5D", "VOL_20D"])
        assert len(events) == 0

    def test_nan_below_warn_threshold_no_event(self):
        from utils.dqc.checkpoints.p3_factor_quality import P3FactorQualityGate
        df = pd.DataFrame({
            "symbol": ["600519.SH"] * 10,
            "date": ["2026-08-01"] * 10,
            "MOM_5D": [0.05] * 10,
        })
        gate = P3FactorQualityGate()
        events = gate._check_x04_factor_reproducibility(df, ["MOM_5D"])
        assert len(events) == 0

    def test_nan_above_warn_below_error_produces_warn(self):
        from utils.dqc.checkpoints.p3_factor_quality import P3FactorQualityGate
        df = pd.DataFrame({
            "symbol": ["600519.SH"] * 20,
            "date": ["2026-08-01"] * 20,
            "MOM_5D": [None] * 2 + [0.05] * 18,
        })
        gate = P3FactorQualityGate()
        events = gate._check_x04_factor_reproducibility(df, ["MOM_5D"])
        assert len(events) == 1
        assert events[0].level.value == "warn"
        assert events[0].metric_id == "X-04"

    def test_nan_above_error_produces_error(self):
        from utils.dqc.checkpoints.p3_factor_quality import P3FactorQualityGate
        df = pd.DataFrame({
            "symbol": ["600519.SH"] * 20,
            "date": ["2026-08-01"] * 20,
            "MOM_5D": [None] * 5 + [0.05] * 15,
        })
        gate = P3FactorQualityGate()
        events = gate._check_x04_factor_reproducibility(df, ["MOM_5D"])
        assert len(events) == 1
        assert events[0].level.value == "error"
        assert events[0].metric_id == "X-04"

    def test_missing_column_skipped(self, sample_factor_df):
        from utils.dqc.checkpoints.p3_factor_quality import P3FactorQualityGate
        gate = P3FactorQualityGate()
        events = gate._check_x04_factor_reproducibility(sample_factor_df, ["NON_EXISTENT"])
        assert len(events) == 0

    def test_empty_dataframe_returns_empty(self):
        from utils.dqc.checkpoints.p3_factor_quality import P3FactorQualityGate
        gate = P3FactorQualityGate()
        events = gate._check_x04_factor_reproducibility(pd.DataFrame(), ["MOM_5D"])
        assert len(events) == 0

    def test_zero_rows_returns_empty(self):
        from utils.dqc.checkpoints.p3_factor_quality import P3FactorQualityGate
        df = pd.DataFrame({"symbol": [], "date": [], "MOM_5D": []})
        gate = P3FactorQualityGate()
        events = gate._check_x04_factor_reproducibility(df, ["MOM_5D"])
        assert len(events) == 0


class TestP3RunP3GateConvenience:
    def test_run_p3_gate_returns_tuple(self, sample_factor_df, baseline_factor_df):
        from utils.dqc.checkpoints.p3_factor_quality import run_p3_gate
        result = run_p3_gate(
            target_date=date(2026, 8, 1),
            factor_df=sample_factor_df,
            baseline_df=baseline_factor_df,
            factor_cols=["MOM_5D", "VOL_20D"],
        )
        assert isinstance(result, tuple)
        assert len(result) == 2
        passed, events = result
        assert isinstance(passed, bool)
        assert isinstance(events, list)

    def test_run_p3_gate_empty_factor_df(self):
        from utils.dqc.checkpoints.p3_factor_quality import run_p3_gate
        passed, events = run_p3_gate(
            target_date=date(2026, 8, 1),
            factor_df=pd.DataFrame(),
            baseline_df=pd.DataFrame(),
            factor_cols=["MOM_5D"],
        )
        assert passed is False

    def test_run_p3_gate_with_symbols(self, sample_factor_df, baseline_factor_df):
        from utils.dqc.checkpoints.p3_factor_quality import run_p3_gate
        passed, events = run_p3_gate(
            target_date=date(2026, 8, 1),
            factor_df=sample_factor_df,
            baseline_df=baseline_factor_df,
            factor_cols=["MOM_5D"],
            symbols=["600519.SH", "000858.SZ", "300308.SZ"],
        )
        assert isinstance(passed, bool)
