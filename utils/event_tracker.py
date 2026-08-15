"""
事件追踪器 — 借鉴 TradingAgents-CN 结构化事件日志模式
提供操作开始/完成/错误的统一追踪，支持性能计时和Token用量记录
"""

import time
from datetime import datetime
from functools import wraps
from typing import Any, Dict, Optional

from .logging_manager import get_logger


class EventTracker:
    """事件追踪器 — 借鉴 TradingAgents-CN log_analysis_start/complete/error 模式"""

    def __init__(self, logger_name: str = 'quant'):
        self._logger = get_logger(logger_name)
        self._active_sessions: Dict[str, Dict[str, Any]] = {}

    def start_session(self, session_id: str, meta: Optional[Dict] = None) -> str:
        """开始一个追踪会话"""
        self._active_sessions[session_id] = {
            'start_time': time.time(),
            'meta': meta or {},
            'events': [],
            'subtasks': [],
        }
        self._logger.info(
            f"🚀 [会话开始] {session_id}",
            extra={'session_id': session_id, 'event_type': 'session_start',
                   'timestamp': datetime.now().isoformat(), **(meta or {})})
        return session_id

    def log_operation_start(self, operation: str, session_id: str = None,
                            target: str = None, **extra):
        """记录操作开始 — 借鉴 log_module_start"""
        start_time = time.time()
        event = {
            'operation': operation,
            'start_time': start_time,
            'target': target,
            **extra,
        }
        if session_id and session_id in self._active_sessions:
            self._active_sessions[session_id]['events'].append(event)

        log_extra = {
            'operation': operation,
            'event_type': 'operation_start',
            'timestamp': datetime.now().isoformat(),
        }
        if session_id:
            log_extra['session_id'] = session_id
        if target:
            log_extra['target'] = target
        log_extra.update(extra)

        self._logger.info(f"📊 [开始] {operation}" +
                         (f" - {target}" if target else ""),
                         extra=log_extra)
        return start_time

    def log_operation_complete(self, operation: str, start_time: float = None,
                               session_id: str = None, target: str = None,
                               success: bool = True, result_summary: str = None,
                               **extra):
        """记录操作完成 — 借鉴 log_module_complete"""
        duration_ms = (time.time() - start_time) * 1000 if start_time else 0
        status = "✅" if success else "❌"

        log_extra = {
            'operation': operation,
            'event_type': 'operation_complete',
            'duration_ms': duration_ms,
            'success': success,
            'timestamp': datetime.now().isoformat(),
        }
        if session_id:
            log_extra['session_id'] = session_id
        if target:
            log_extra['target'] = target
        log_extra.update(extra)

        msg = f"{status} [完成] {operation}" + (f" - {target}" if target else "")
        msg += f" | 耗时: {duration_ms:.0f}ms"
        if result_summary:
            msg += f" | {result_summary}"

        self._logger.info(msg, extra=log_extra)
        return duration_ms

    def log_operation_error(self, operation: str, error: str, start_time: float = None,
                            session_id: str = None, target: str = None, **extra):
        """记录操作错误 — 借鉴 log_module_error"""
        duration_ms = (time.time() - start_time) * 1000 if start_time else 0

        log_extra = {
            'operation': operation,
            'event_type': 'operation_error',
            'duration_ms': duration_ms,
            'error': error,
            'timestamp': datetime.now().isoformat(),
        }
        if session_id:
            log_extra['session_id'] = session_id
        if target:
            log_extra['target'] = target
        log_extra.update(extra)

        self._logger.error(
            f"❌ [错误] {operation}" + (f" - {target}" if target else "") +
            f" | 耗时: {duration_ms:.0f}ms | 错误: {error}",
            extra=log_extra, exc_info=True)

    def log_token_usage(self, provider: str, model: str, input_tokens: int,
                        output_tokens: int, cost: float, session_id: str = None):
        """记录Token用量 — 借鉴 log_token_usage"""
        self._logger.info(
            f"📊 Token: {provider}/{model} | 输入={input_tokens} 输出={output_tokens} | 成本=¥{cost:.6f}",
            extra={
                'provider': provider, 'model': model,
                'tokens': {'input': input_tokens, 'output': output_tokens},
                'cost': cost, 'session_id': session_id,
                'event_type': 'token_usage',
            })

    def log_price_check(self, code: str, price: float, source: str,
                        valid: bool, reason: str = None):
        """记录价格校验"""
        level = 'info' if valid else 'warning'
        msg = f"{'✅' if valid else '⚠️'} [价格校验] {code}: ¥{price:.2f} (来源:{source})"
        if reason:
            msg += f" - {reason}"
        getattr(self._logger, level)(msg, extra={
            'stock_code': code, 'price': price, 'source': source,
            'valid': valid, 'event_type': 'price_check',
        })

    def finish_session(self, session_id: str) -> Dict[str, Any]:
        """结束追踪会话并返回统计"""
        if session_id not in self._active_sessions:
            return {}
        session = self._active_sessions.pop(session_id)
        total_ms = (time.time() - session['start_time']) * 1000
        summary = {
            'session_id': session_id,
            'total_duration_ms': total_ms,
            'event_count': len(session['events']),
            'meta': session['meta'],
        }
        self._logger.info(
            f"🏁 [会话结束] {session_id} | 总耗时: {total_ms:.0f}ms | 事件数: {summary['event_count']}",
            extra={'session_id': session_id, 'event_type': 'session_end',
                   'duration_ms': total_ms, **summary})
        return summary

    def track(self, event_name: str, data: Dict[str, Any] = None):
        """通用事件追踪接口 — 兼容外部调用"""
        self._logger.info(
            f"📊 [追踪] {event_name}",
            extra={'event_type': 'track', 'event_name': event_name,
                   'data': data or {}, 'timestamp': datetime.now().isoformat()})


# 全局单例
_event_tracker: Optional[EventTracker] = None


def get_event_tracker() -> EventTracker:
    global _event_tracker
    if _event_tracker is None:
        _event_tracker = EventTracker()
    return _event_tracker


# ============================================================
# 装饰器 — 便捷使用
# ============================================================

def track_event(operation: str = None, target_param: str = None):
    """事件追踪装饰器 — 自动记录操作开始/完成/错误"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            op_name = operation or func.__name__
            tracker = get_event_tracker()
            start = tracker.log_operation_start(op_name)
            try:
                result = func(*args, **kwargs)
                tracker.log_operation_complete(op_name, start_time=start, success=True)
                return result
            except Exception as e:
                tracker.log_operation_error(op_name, str(e), start_time=start)
                raise
        return wrapper
    return decorator


def track_operation(operation_name: str):
    """上下文管理器式事件追踪"""
    from contextlib import contextmanager

    @contextmanager
    def _track():
        tracker = get_event_tracker()
        start = tracker.log_operation_start(operation_name)
        try:
            yield tracker
        except Exception as e:
            tracker.log_operation_error(operation_name, str(e), start_time=start)
            raise
        else:
            tracker.log_operation_complete(operation_name, start_time=start, success=True)
    return _track()
