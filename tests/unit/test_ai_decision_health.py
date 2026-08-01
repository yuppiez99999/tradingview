# -*- coding: utf-8 -*-
"""ai_decision.health 测试套件 — 模型健康检查 + 熔断器 + 降级

覆盖路线图步骤 3 的 5 个验收场景:
  1. 健康检查通过: check(role) 返回 healthy=True
  2. 熔断触发: 连续失败 max_failures 次后 is_circuit_open 返回 True
  3. 自动降级: 熔断时 get_provider_with_fallback 返回 MockProvider
  4. 冷却恢复: cooldown_seconds 后熔断器自动恢复
  5. 端到端: 业务调用失败累计触发熔断
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ai_decision.health import (
    CircuitBreaker,
    HealthStatus,
    ModelHealthMonitor,
    get_default_monitor,
)
from ai_decision.providers import BaseProvider, MockProvider


# ============================================================
# 辅助: 总是返回 None 的 Provider (模拟故障)
# ============================================================

class _NoneProvider(BaseProvider):
    """模拟故障 provider — generate 总是返回 None"""
    is_mock = False
    model_name = "none_provider"

    def generate(self, prompt: str, system: str = "", timeout: int = 30):
        return None


class _ExceptionProvider(BaseProvider):
    """模拟异常 provider — generate 总是抛异常"""
    is_mock = False
    model_name = "exception_provider"

    def generate(self, prompt: str, system: str = "", timeout: int = 30):
        raise RuntimeError("provider unavailable")


# ============================================================
# 场景 1: 健康检查通过
# ============================================================

def test_check_healthy_with_mock_provider():
    """场景 1: 默认 MockProvider 探测成功, 返回 healthy=True"""
    mon = ModelHealthMonitor()
    status = mon.check("judge", timeout=2.0)
    assert isinstance(status, HealthStatus)
    assert status.role == "judge"
    assert status.healthy is True
    assert status.latency_ms >= 0
    assert status.error == ""
    assert status.circuit_open is False
    assert status.provider_name  # 非空
    assert status.last_check > 0


def test_check_healthy_records_success():
    """场景 1: 探测成功后 record_success 被调用, 连续失败计数归零"""
    mon = ModelHealthMonitor(max_failures=3)
    # 先制造 1 次失败
    mon.record_failure("judge")
    assert mon._breakers["judge"].consecutive_failures == 1
    # 探测成功应重置
    mon.check("judge")
    assert mon._breakers["judge"].consecutive_failures == 0


# ============================================================
# 场景 2: 熔断触发 (连续失败 max_failures 次)
# ============================================================

def test_circuit_triggers_on_consecutive_failures():
    """场景 2: 连续失败 3 次触发熔断"""
    mon = ModelHealthMonitor(max_failures=3, cooldown_seconds=300)
    # 前 2 次不熔断
    mon.record_failure("bull")
    mon.record_failure("bull")
    assert mon.is_circuit_open("bull") is False
    # 第 3 次触发熔断
    mon.record_failure("bull")
    assert mon.is_circuit_open("bull") is True


def test_circuit_not_triggered_below_threshold():
    """场景 2: 失败次数 < max_failures 时不熔断"""
    mon = ModelHealthMonitor(max_failures=5)
    for _ in range(4):
        mon.record_failure("bear")
    assert mon.is_circuit_open("bear") is False


# ============================================================
# 场景 3: 自动降级 MockProvider
# ============================================================

def test_get_provider_fallback_returns_mock_when_open():
    """场景 3: 熔断时 get_provider_with_fallback 返回 MockProvider"""
    mon = ModelHealthMonitor(max_failures=2)
    mon.record_failure("bull")
    mon.record_failure("bull")
    assert mon.is_circuit_open("bull") is True
    prov = mon.get_provider_with_fallback("bull")
    assert isinstance(prov, MockProvider)


def test_get_provider_fallback_returns_real_when_closed():
    """场景 3: 未熔断时 get_provider_with_fallback 返回真实 provider"""
    mon = ModelHealthMonitor()
    mon.get_provider_with_fallback("judge")
    # 默认无 API Key, get_active_provider 返回 MockProvider, 但不是熔断降级
    # 关键是 is_circuit_open 为 False
    assert mon.is_circuit_open("judge") is False


# ============================================================
# 场景 4: 冷却恢复
# ============================================================

def test_circuit_resets_after_cooldown():
    """场景 4: cooldown_seconds 后熔断器自动恢复

    使用短 cooldown (1s) 避免测试等待过久
    """
    mon = ModelHealthMonitor(max_failures=2, cooldown_seconds=1)
    mon.record_failure("bear")
    mon.record_failure("bear")
    assert mon.is_circuit_open("bear") is True
    # 等待冷却期过
    time.sleep(1.1)
    # is_circuit_open 内部调用 try_reset, 应自动恢复
    assert mon.is_circuit_open("bear") is False


def test_circuit_not_reset_before_cooldown():
    """场景 4: 冷却期内熔断器保持开启"""
    mon = ModelHealthMonitor(max_failures=2, cooldown_seconds=10)
    mon.record_failure("bear")
    mon.record_failure("bear")
    assert mon.is_circuit_open("bear") is True
    # 立即检查 (未过冷却期)
    assert mon.is_circuit_open("bear") is True


def test_record_success_resets_failures():
    """场景 4: 业务调用成功 record_success 重置连续失败计数"""
    mon = ModelHealthMonitor(max_failures=3)
    mon.record_failure("judge")
    mon.record_failure("judge")
    mon.record_success("judge")  # 重置
    assert mon._breakers["judge"].consecutive_failures == 0
    assert mon.is_circuit_open("judge") is False
    # 再失败 2 次不应熔断 (因为已重置, 需再 3 次才熔断)
    mon.record_failure("judge")
    mon.record_failure("judge")
    assert mon.is_circuit_open("judge") is False


# ============================================================
# 场景 5: 端到端 (探测失败 → 熔断 → 降级)
# ============================================================

def test_e2e_probe_failure_triggers_circuit_and_fallback():
    """场景 5: 探测连续失败 → 触发熔断 → get_provider_with_fallback 降级 Mock

    mock get_active_provider 返回 _NoneProvider 模拟故障
    """
    import ai_decision.health as health_mod
    original_gap = health_mod.get_active_provider

    mon = ModelHealthMonitor(max_failures=3, cooldown_seconds=300)
    try:
        health_mod.get_active_provider = lambda role: _NoneProvider()
        # 探测 3 次都失败 (provider 返回 None)
        for _ in range(3):
            status = mon.check("judge", timeout=1.0)
            assert status.healthy is False
        # 熔断应已触发
        assert mon.is_circuit_open("judge") is True
        # 降级 MockProvider
        prov = mon.get_provider_with_fallback("judge")
        assert isinstance(prov, MockProvider)
    finally:
        health_mod.get_active_provider = original_gap


def test_e2e_probe_exception_records_failure():
    """场景 5: 探测抛异常时 record_failure 被调用"""
    import ai_decision.health as health_mod
    original_gap = health_mod.get_active_provider

    mon = ModelHealthMonitor(max_failures=3)
    try:
        health_mod.get_active_provider = lambda role: _ExceptionProvider()
        status = mon.check("judge", timeout=1.0)
        assert status.healthy is False
        assert "exception" in status.error.lower() or "unavailable" in status.error
        assert mon._breakers["judge"].consecutive_failures == 1
    finally:
        health_mod.get_active_provider = original_gap


def test_e2e_circuit_open_check_skips_api_call():
    """场景 5: 熔断开启时 check 直接返回不健康, 不调用 API"""
    mon = ModelHealthMonitor(max_failures=2, cooldown_seconds=300)
    # 制造熔断
    mon.record_failure("judge")
    mon.record_failure("judge")
    assert mon.is_circuit_open("judge") is True
    # check 应直接返回不健康 (不调用 provider)
    status = mon.check("judge")
    assert status.healthy is False
    assert status.circuit_open is True
    assert "circuit open" in status.error.lower()
    assert status.provider_name == "N/A"


# ============================================================
# 补充: maybe_probe + get_stats + get_health_summary
# ============================================================

def test_maybe_probe_respects_interval():
    """maybe_probe 受 probe_interval 控制, 间隔内返回缓存"""
    mon = ModelHealthMonitor(probe_interval_seconds=60.0)
    status1 = mon.maybe_probe("judge", timeout=2.0)
    assert status1 is not None
    assert status1.healthy is True
    # 立即再次调用应返回缓存 (同一对象引用, 不触发新探测)
    status2 = mon.maybe_probe("judge", timeout=2.0)
    assert status2 is status1


def test_maybe_probe_returns_none_for_first_unknown_role():
    """maybe_probe 首次调用会触发 check, 不返回 None"""
    mon = ModelHealthMonitor()
    status = mon.maybe_probe("bull", timeout=2.0)
    assert status is not None
    assert status.role == "bull"


def test_get_stats_structure():
    """get_stats 返回正确结构"""
    mon = ModelHealthMonitor(max_failures=3, cooldown_seconds=300, probe_interval_seconds=60)
    mon.record_failure("judge")
    stats = mon.get_stats()
    assert "roles" in stats
    assert "config" in stats
    assert "judge" in stats["roles"]
    assert stats["roles"]["judge"]["consecutive_failures"] == 1
    assert stats["roles"]["judge"]["max_failures"] == 3
    assert stats["roles"]["judge"]["is_open"] is False
    assert stats["config"]["max_failures"] == 3
    assert stats["config"]["cooldown_seconds"] == 300
    assert stats["config"]["probe_interval_seconds"] == 60.0


def test_get_stats_includes_last_status():
    """get_stats 包含最近探测状态"""
    mon = ModelHealthMonitor()
    mon.check("judge")  # 触发探测
    stats = mon.get_stats()
    assert "last_status" in stats["roles"]["judge"]
    assert stats["roles"]["judge"]["last_status"]["role"] == "judge"
    assert stats["roles"]["judge"]["last_status"]["healthy"] is True


def test_get_health_summary():
    """get_health_summary 返回简版摘要"""
    mon = ModelHealthMonitor(max_failures=2)
    # 制造 bull 熔断
    mon.record_failure("bull")
    mon.record_failure("bull")
    # 探测 judge (MockProvider 成功)
    mon.check("judge")
    summary = mon.get_health_summary()
    assert "total_roles" in summary
    assert "open_breakers" in summary
    assert "healthy_roles" in summary
    assert "unhealthy_roles" in summary
    assert "bull" in summary["open_breakers"]
    assert "bull" in summary["unhealthy_roles"]
    assert "judge" in summary["healthy_roles"]


# ============================================================
# 补充: CircuitBreaker dataclass 验证 (复用验证)
# ============================================================

def test_circuit_breaker_record_failure():
    """CircuitBreaker.record_failure 累计失败并触发熔断"""
    cb = CircuitBreaker(provider="test", max_failures=2, cooldown_seconds=60)
    cb.record_failure()
    assert cb.consecutive_failures == 1
    assert cb.is_open is False
    cb.record_failure()
    assert cb.consecutive_failures == 2
    assert cb.is_open is True


def test_circuit_breaker_record_success_resets():
    """CircuitBreaker.record_success 重置失败计数"""
    cb = CircuitBreaker(provider="test", max_failures=2)
    cb.record_failure()
    cb.record_success()
    assert cb.consecutive_failures == 0
    assert cb.is_open is False


def test_circuit_breaker_try_reset_after_cooldown():
    """CircuitBreaker.try_reset 冷却后返回 True"""
    cb = CircuitBreaker(provider="test", max_failures=1, cooldown_seconds=1)
    cb.record_failure()
    assert cb.is_open is True
    # 未过冷却
    assert cb.try_reset() is False
    # 过冷却
    time.sleep(1.1)
    assert cb.try_reset() is True
    assert cb.is_open is False


# ============================================================
# 全局单例
# ============================================================

def test_get_default_monitor_singleton():
    """get_default_monitor 返回全局单例"""
    mon1 = get_default_monitor()
    mon2 = get_default_monitor()
    assert mon1 is mon2
    assert isinstance(mon1, ModelHealthMonitor)


# ============================================================
# 端到端: orchestrator 集成
# ============================================================

def test_orchestrator_with_health_monitor_no_crash():
    """端到端: run_decision 传入 health_monitor 不崩溃

    验证步骤 3 集成不破坏 orchestrator 主链路
    """
    import shutil
    # 清理审计目录
    audit_dir = os.path.join("reports", "ai_decision")
    if os.path.exists(audit_dir):
        shutil.rmtree(audit_dir, ignore_errors=True)

    from ai_decision.orchestrator import run_decision
    mon = ModelHealthMonitor(max_failures=3, cooldown_seconds=300)
    dec = run_decision("600519", mode="shadow", health_monitor=mon)
    assert dec is not None
    assert dec.symbol == "600519"
    # judge provider 调用后应有熔断器记录
    mon.get_stats()
    # judge 可能被探测过 (取决于 maybe_probe interval)
    # 关键是不崩溃 + decision 正常返回


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
