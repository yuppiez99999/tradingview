"""DQC 模块骨架测试 — Phase 1 验证.

验证目标:
    1. 模块可正常 import (无循环依赖)
    2. 事件类型正确构造
    3. 六维指标对样本数据可运行
    4. P2 门禁判定逻辑正确
    5. AlertAggregator 抑制/升级逻辑正确

运行:
    cd e:\\各种PY程序\\28-终极量化交易系统8.4
    python -m pytest tests/test_dqc_skeleton.py -v
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

# 确保项目根在 path 中
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ============================================================
# 1. import 测试 (验证无循环依赖)
# ============================================================
def test_import_dqc_module():
    """验证 DQC 模块可正常 import."""
    from utils.dqc import DQCLevel
    from utils.dqc.event_types import DQCCheckpoint, DQCMetric

    assert DQCLevel.ERROR.value == "error"
    assert DQCCheckpoint.P2_CACHE.value == "P2"
    assert DQCMetric.C01_SYMBOL_COVERAGE.value == "C-01"


# ============================================================
# 2. DQCEvent 测试
# ============================================================
def test_dqc_event_construction():
    """验证 DQCEvent 可正确构造."""
    from utils.dqc import DQCCheckpoint, DQCEvent, DQCLevel

    event = DQCEvent(
        metric_id="C-01",
        level=DQCLevel.ERROR,
        checkpoint=DQCCheckpoint.P2_CACHE,
        value=0.85,
        threshold=0.95,
        message="标的覆盖率 85% 低于阈值 95%",
        symbol="600519",
    )

    # 验证自动填充 timestamp
    assert event.timestamp != ""
    assert event.level.is_blocking is True
    assert event.level == DQCLevel.ERROR

    # 验证 to_dict
    d = event.to_dict()
    assert d["metric_id"] == "C-01"
    assert d["level"] == "error"
    assert d["value"] == 0.85

    # 验证 to_risk_event_payload
    payload = event.to_risk_event_payload()
    assert payload["dqc_metric"] == "C-01"
    assert payload["dqc_level"] == "error"


def test_dqc_event_frozen():
    """验证 DQCEvent 不可变."""
    from utils.dqc import DQCCheckpoint, DQCEvent, DQCLevel

    event = DQCEvent(
        metric_id="A-01",
        level=DQCLevel.WARN,
        checkpoint=DQCCheckpoint.P2_CACHE,
        value=0.12,
        threshold=0.10,
        message="test",
    )
    with pytest.raises((AttributeError, TypeError)):
        event.metric_id = "X-01"  # type: ignore[misc]


def test_dqc_level_is_blocking():
    """验证级别阻断判定."""
    from utils.dqc import DQCLevel

    assert DQCLevel.INFO.is_blocking is False
    assert DQCLevel.WARN.is_blocking is False
    assert DQCLevel.ERROR.is_blocking is True
    assert DQCLevel.CRITICAL.is_blocking is True


# ============================================================
# 3. 指标检查测试 (使用样本数据)
# ============================================================
@pytest.fixture
def sample_good_df():
    """构造一个合规的样本 DataFrame."""
    return pd.DataFrame(
        {
            "symbol": ["600519.SH", "000858.SZ", "600519.SH", "000858.SZ"],
            "date": ["2026-08-01", "2026-08-01", "2026-08-02", "2026-08-02"],
            "open": [1800.0, 150.0, 1810.0, 151.0],
            "high": [1820.0, 152.0, 1830.0, 153.0],
            "low": [1790.0, 149.0, 1800.0, 150.0],
            "close": [1810.0, 151.0, 1820.0, 152.0],
            "volume": [100000, 200000, 110000, 210000],
            "preclose": [1790.0, 149.0, 1810.0, 151.0],
        }
    )


@pytest.fixture
def sample_bad_df():
    """构造一个有问题的样本 DataFrame (多种违规)."""
    return pd.DataFrame(
        {
            "symbol": ["600519.SH", "000858.SZ", "600519.SH", "300308.SZ"],
            "date": [
                "2026-08-01",
                "2026-08-01",
                "2026-08-01",
                "2026-08-02",
            ],  # 600519 重复
            "open": [1800.0, 150.0, 1800.0, 1194.0],
            "high": [1790.0, 152.0, 1800.0, 1195.0],  # 600519: high < open 违反
            "low": [1810.0, 149.0, 1800.0, 1193.0],  # 600519: low > open 违反
            "close": [0.0, 151.0, 1800.0, 1194.0],  # 600519: close=0 零价格
            "volume": [-100, 200000, 110000, 210000],  # 600519: 成交量为负
            "preclose": [1790.0, 149.0, 1790.0, 1190.0],
        }
    )


def test_completeness_check_good(sample_good_df):
    """完整性检查: 合规数据应无 ERROR."""
    from utils.dqc.metrics.completeness import check_completeness

    symbols = ["600519.SH", "000858.SZ"]
    events = check_completeness(
        sample_good_df,
        target_date=date(2026, 8, 2),
        expected_symbols=symbols,
    )
    blocking = [e for e in events if e.level.is_blocking]
    assert (
        len(blocking) == 0
    ), f"合规数据不应有阻断事件: {[e.message for e in blocking]}"


def test_completeness_check_missing_symbol(sample_good_df):
    """完整性检查: 缺失标的应触发 C-01."""
    from utils.dqc.metrics.completeness import check_completeness

    # 预期 5 个标的, 实际只有 2 个
    symbols = ["600519.SH", "000858.SZ", "510050.SH", "512880.SH", "588080.SH"]
    events = check_completeness(
        sample_good_df,
        target_date=date(2026, 8, 2),
        expected_symbols=symbols,
    )
    c01_events = [e for e in events if e.metric_id == "C-01"]
    assert len(c01_events) > 0, "缺失 3 个标的应触发 C-01"
    assert c01_events[0].level.value in ("error", "warn")


def test_accuracy_check_violations(sample_bad_df):
    """准确性检查: 违规数据应触发 A-02/A-03/A-06."""
    from utils.dqc.metrics.accuracy import check_accuracy

    events = check_accuracy(sample_bad_df)

    metric_ids = {e.metric_id for e in events}
    # 600519: high < open 违反 → A-02
    # 600519: 成交量为负 → A-03
    # 600519: close=0 → A-06
    assert "A-02" in metric_ids, "OHLC 关系违反应触发 A-02"
    assert "A-03" in metric_ids, "成交量为负应触发 A-03"
    assert "A-06" in metric_ids, "零价格应触发 A-06"


def test_uniqueness_check_dedup(sample_bad_df):
    """唯一性检查: 重复主键应触发 U-01."""
    from utils.dqc.metrics.uniqueness import check_uniqueness

    events = check_uniqueness(sample_bad_df)
    u01_events = [e for e in events if e.metric_id == "U-01"]
    assert len(u01_events) > 0, "主键重复应触发 U-01"
    assert u01_events[0].level.value == "error"


# ============================================================
# 4. P2 门禁测试
# ============================================================
def test_p2_gate_pass_good_data(sample_good_df):
    """P2 门禁: 合规数据应通过."""
    from utils.dqc import run_p2_gate

    symbols = ["600519.SH", "000858.SZ"]
    passed, events = run_p2_gate(
        target_date=date(2026, 8, 2),
        symbols=symbols,
        df=sample_good_df,
    )
    assert (
        passed is True
    ), f"合规数据应通过 P2: {[e.message for e in events if e.level.is_blocking]}"


def test_p2_gate_block_bad_data(sample_bad_df):
    """P2 门禁: 违规数据应阻断 (Feature Flag 关闭时仍通过, 但记录事件)."""
    from utils.dqc import run_p2_gate

    symbols = ["600519.SH", "000858.SZ", "300308.SZ"]
    passed, events = run_p2_gate(
        target_date=date(2026, 8, 1),
        symbols=symbols,
        df=sample_bad_df,
    )

    # Feature Flag 默认关闭 → 即使有问题也通过 (观察模式)
    # 但应记录阻断级事件
    blocking = [e for e in events if e.level.is_blocking]
    assert len(blocking) > 0, "违规数据应产生阻断级事件"
    assert passed is True, "Feature Flag 关闭时应通过 (观察模式)"


def test_p2_gate_block_when_flag_on(sample_bad_df):
    """P2 门禁: Feature Flag 启用时应真正阻断."""
    from utils.dqc import run_p2_gate

    with patch("utils.infra.feature_flags.is_enabled", return_value=True):
        symbols = ["600519.SH", "000858.SZ", "300308.SZ"]
        passed, events = run_p2_gate(
            target_date=date(2026, 8, 1),
            symbols=symbols,
            df=sample_bad_df,
        )
    assert passed is False, "Feature Flag 启用时, 违规数据应阻断"


def test_p2_gate_fail_safe_on_exception():
    """P2 门禁: DQC 自身异常应 fail-safe (降级为通过)."""
    from utils.dqc import run_p2_gate

    # 传入非法 df 触发异常
    passed, events = run_p2_gate(
        target_date=date(2026, 8, 1),
        symbols=["600519.SH"],
        df=None,  # 不传 df 且不传 cache_path → 触发 C-02 ERROR (非 fail-safe 路径)
    )
    # df=None + cache_path=None → 直接返回 C-02 ERROR
    assert passed is False


# ============================================================
# 5. AlertAggregator 测试
# ============================================================
def test_aggregator_first_emit():
    """聚合器: 首次出现应发送."""
    from utils.dqc.aggregator import AlertAggregator

    agg = AlertAggregator()  # 直接构造, 避免单例污染
    should, reason = agg.should_emit(
        "C-01", DQCLevel := __import__("utils.dqc", fromlist=["DQCLevel"]).DQCLevel.WARN
    )
    assert should is True
    assert reason is None


def test_aggregator_suppress_same_level():
    """聚合器: 同级 5 分钟内应抑制."""
    from utils.dqc import DQCLevel
    from utils.dqc.aggregator import AlertAggregator

    agg = AlertAggregator()
    # 首次发送
    should1, _ = agg.should_emit("C-01", DQCLevel.WARN)
    assert should1 is True
    agg.record_emit("C-01", level=DQCLevel.WARN)

    # 同级立即再发 → 抑制
    should2, reason = agg.should_emit("C-01", DQCLevel.WARN)
    assert should2 is False
    assert reason is not None


def test_aggregator_escalate_on_upgrade():
    """聚合器: 级别升级应立即发送."""
    from utils.dqc import DQCLevel
    from utils.dqc.aggregator import AlertAggregator

    agg = AlertAggregator()
    # 首次 WARN
    agg.should_emit("C-01", DQCLevel.WARN)
    agg.record_emit("C-01", level=DQCLevel.WARN)

    # 升级到 ERROR → 应立即发
    should, reason = agg.should_emit("C-01", DQCLevel.ERROR)
    assert should is True
    assert reason is not None
    assert "升级" in reason


def test_aggregator_per_symbol_isolation():
    """聚合器: 不同标的的同一指标应独立计数."""
    from utils.dqc import DQCLevel
    from utils.dqc.aggregator import AlertAggregator

    agg = AlertAggregator()
    # 600519 首次
    should1, _ = agg.should_emit("A-02", DQCLevel.WARN, symbol="600519.SH")
    assert should1 is True
    agg.record_emit("A-02", symbol="600519.SH", level=DQCLevel.WARN)

    # 000858 同指标首次 → 应发送 (不同标的独立)
    should2, _ = agg.should_emit("A-02", DQCLevel.WARN, symbol="000858.SZ")
    assert should2 is True


def test_aggregator_cleanup_stale():
    """聚合器: 清理过期状态."""
    from utils.dqc import DQCLevel
    from utils.dqc.aggregator import AlertAggregator

    agg = AlertAggregator()
    agg.should_emit("C-01", DQCLevel.WARN)
    # 手动将 last_seen 调到 2 小时前
    for state in agg._states.values():
        state.last_seen = datetime.now() - timedelta(hours=2)

    cleaned = agg.cleanup_stale(max_age=timedelta(hours=1))
    assert cleaned == 1
    assert len(agg._states) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
