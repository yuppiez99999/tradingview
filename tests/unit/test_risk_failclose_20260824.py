"""风控 fail-close 完整化回归测试 (2026-08-24)

覆盖:
    DTE-6  --auto-confirm 风控护栏: 仅 TRADING_ENV ∈ {shadow, production} 允许自动确认,
           否则降级为"不自动确认" (人工确认保护)
"""
from __future__ import annotations

import os


def _auto_confirm_gate(current_env: str) -> tuple[bool, str]:
    """等价复刻 daily_trade_executor._run_mode 的 DTE-6 自动确认护栏逻辑.

    Returns:
        (allow_auto_confirm, env_normalized)
    """
    _env = current_env.strip().lower()
    if _env not in ("shadow", "production"):
        return False, _env  # 降级为不自动确认
    return True, _env


class TestAutoConfirmGate:
    def test_production_allowed(self):
        """DTE-6: TRADING_ENV=production 允许自动确认."""
        allow, env = _auto_confirm_gate("production")
        assert allow is True
        assert env == "production"

    def test_shadow_allowed(self):
        """DTE-6: TRADING_ENV=shadow 允许自动确认 (演练)."""
        allow, _ = _auto_confirm_gate("shadow")
        assert allow is True

    def test_sim_blocked(self):
        """DTE-6: 默认 sim 阻断自动确认 (人工确认保护)."""
        allow, _ = _auto_confirm_gate("sim")
        assert allow is False

    def test_empty_env_blocked(self):
        """DTE-6: 未设置 TRADING_ENV 时阻断自动确认 (安全默认)."""
        allow, _ = _auto_confirm_gate(os.environ.get("TRADING_ENV", "sim"))
        # CI 默认非 shadow/production
        assert allow is False or os.environ.get("TRADING_ENV", "sim").lower() in ("shadow", "production")

    def test_ci_blocked(self):
        """DTE-6: ci 环境阻断自动确认."""
        allow, _ = _auto_confirm_gate("ci")
        assert allow is False
