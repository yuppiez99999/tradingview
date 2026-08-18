"""
单元测试: utils/event_tracker.py
覆盖 EventTracker / get_event_tracker / track_event / track_operation
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from utils import event_tracker as et
from utils.event_tracker import EventTracker, get_event_tracker, track_event, track_operation


@pytest.fixture(autouse=True)
def _reset_singleton():
    et._event_tracker = None
    yield
    et._event_tracker = None


class TestEventTrackerInit:
    def test_default_logger_name(self):
        tracker = EventTracker()
        assert tracker._logger is not None
        assert tracker._active_sessions == {}

    def test_custom_logger_name(self):
        tracker = EventTracker(logger_name="custom")
        assert tracker._logger is not None


class TestStartSession:
    def test_basic_session(self):
        tracker = EventTracker()
        sid = tracker.start_session("sess1")
        assert sid == "sess1"
        assert "sess1" in tracker._active_sessions

    def test_session_with_meta(self):
        tracker = EventTracker()
        tracker.start_session("sess1", meta={"key": "value"})
        assert tracker._active_sessions["sess1"]["meta"] == {"key": "value"}

    def test_session_has_events_list(self):
        tracker = EventTracker()
        tracker.start_session("sess1")
        assert tracker._active_sessions["sess1"]["events"] == []


class TestLogOperationStart:
    def test_basic_start(self):
        tracker = EventTracker()
        start = tracker.log_operation_start("fetch_data")
        assert isinstance(start, float)
        assert start > 0

    def test_with_session(self):
        tracker = EventTracker()
        tracker.start_session("s1")
        tracker.log_operation_start("op1", session_id="s1", target="stock")
        assert len(tracker._active_sessions["s1"]["events"]) == 1
        assert tracker._active_sessions["s1"]["events"][0]["operation"] == "op1"

    def test_with_unknown_session(self):
        tracker = EventTracker()
        tracker.log_operation_start("op1", session_id="unknown")
        assert "unknown" not in tracker._active_sessions


class TestLogOperationComplete:
    def test_basic_complete(self):
        tracker = EventTracker()
        start = time.time() - 0.1
        duration = tracker.log_operation_complete("op1", start_time=start)
        assert duration > 0

    def test_without_start_time(self):
        tracker = EventTracker()
        duration = tracker.log_operation_complete("op1")
        assert duration == 0

    def test_with_result_summary(self):
        tracker = EventTracker()
        tracker.log_operation_complete("op1", result_summary="100 rows")

    def test_failure(self):
        tracker = EventTracker()
        tracker.log_operation_complete("op1", success=False)


class TestLogOperationError:
    def test_basic_error(self):
        tracker = EventTracker()
        tracker.log_operation_error("op1", "connection failed")

    def test_with_start_time(self):
        tracker = EventTracker()
        start = time.time() - 0.5
        tracker.log_operation_error("op1", "timeout", start_time=start)


class TestLogTokenUsage:
    def test_basic(self):
        tracker = EventTracker()
        tracker.log_token_usage("deepseek", "chat", 100, 50, 0.001)

    def test_with_session(self):
        tracker = EventTracker()
        tracker.log_token_usage("glm", "chat", 200, 100, 0.002, session_id="s1")


class TestLogPriceCheck:
    def test_valid_price(self):
        tracker = EventTracker()
        tracker.log_price_check("000001.SZ", 10.5, "akshare", valid=True)

    def test_invalid_price(self):
        tracker = EventTracker()
        tracker.log_price_check("000001.SZ", 0.0, "akshare", valid=False, reason="zero price")


class TestFinishSession:
    def test_existing_session(self):
        tracker = EventTracker()
        tracker.start_session("s1")
        tracker.log_operation_start("op1", session_id="s1")
        summary = tracker.finish_session("s1")
        assert summary["session_id"] == "s1"
        assert summary["event_count"] == 1
        assert "s1" not in tracker._active_sessions

    def test_nonexistent_session(self):
        tracker = EventTracker()
        summary = tracker.finish_session("unknown")
        assert summary == {}


class TestTrack:
    def test_basic_track(self):
        tracker = EventTracker()
        tracker.track("custom_event")

    def test_track_with_data(self):
        tracker = EventTracker()
        tracker.track("custom_event", data={"key": "value"})


class TestGetEventTracker:
    def test_singleton(self):
        t1 = get_event_tracker()
        t2 = get_event_tracker()
        assert t1 is t2


class TestTrackEventDecorator:
    def test_successful_call(self):
        @track_event("my_op")
        def my_func(x):
            return x * 2
        result = my_func(5)
        assert result == 10

    def test_default_operation_name(self):
        @track_event()
        def my_func(x):
            return x + 1
        assert my_func(3) == 4

    def test_exception_propagates(self):
        @track_event("failing_op")
        def my_func():
            raise ValueError("test error")
        with pytest.raises(ValueError):
            my_func()


class TestTrackOperation:
    def test_successful_context(self):
        with track_operation("ctx_op") as tracker:
            assert tracker is not None

    def test_exception_in_context(self):
        with pytest.raises(RuntimeError):
            with track_operation("ctx_op"):
                raise RuntimeError("ctx error")