"""报告项 15 (批次三) 回归: broker.enable 切换门禁 fail-closed.

背景 (`代码质量审计报告_20260909.md` 第 15 项 / §7bis):
    修复前: 真实下单就绪 (broker.enabled=true + dry_run=false + TRADING_ENV=production)
            但真实 broker 装配失败时, `get_broker()` 静默降级 SimulatedBroker;
            且 `AutomatedExecutionSystem.__init__` 捕获异常后以 `OrderRouter(broker=None)`
            继续初始化 → 主链路照常跑。
    风险:   订单被"模拟成交"而真实账户无仓位 → "以为在下单、实则空转"。
    修复后: 实盘就绪路径 fail-closed —— 抛 `LiveBrokerUnavailableError`, 拒绝启动;
            非实盘路径仍 fail-open 降级 (不阻断日常链路)。

本文件按"修复前会失败"原则编写: 标注 ✗修复前 的用例在旧代码下必红。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from utils.execution.broker_factory import (
    LiveBrokerUnavailableError,
    get_broker,
    is_live_broker,
    is_live_intent,
)

_AES = "utils.execution.automated_execution_system"


@pytest.fixture
def clean_env(monkeypatch):
    """清空 broker 相关环境变量, 保证用例相互独立."""
    for key in ("TRADING_ENV", "QMT_RPC_URL", "QMT_RPC_TOKEN", "QMT_RPC_TIMEOUT"):
        monkeypatch.delenv(key, raising=False)
    yield


# ============================================================
# 1. is_live_intent 三重条件矩阵
# ============================================================
@pytest.mark.parametrize(
    "enabled,dry_run,env,expected",
    [
        (True, False, "production", True),
        (True, False, "PRODUCTION", True),
        (True, False, " production ", True),
        (True, False, "sim", False),
        (True, False, "shadow", False),
        (None, False, "production", False),
        (True, True, "production", False),
        (False, False, "production", False),
    ],
)
def test_is_live_intent_matrix(clean_env, monkeypatch, enabled, dry_run, env, expected):
    """①②③ 三重条件同时满足才判为"真实下单就绪"."""
    monkeypatch.setenv("TRADING_ENV", env)
    cfg = {"enabled": enabled, "dry_run": dry_run}
    assert is_live_intent(cfg) is expected


def test_is_live_intent_swallows_config_error():
    """配置读取异常 → False (安全默认, 不误判为实盘)."""
    with patch(
        "utils.execution.broker_factory._load_broker_config",
        side_effect=OSError("config unreadable"),
    ):
        assert is_live_intent() is False


# ============================================================
# 2. get_broker: 实盘就绪 fail-closed / 非实盘 fail-open
# ============================================================
def test_get_broker_live_intent_xtquant_missing_raises(clean_env, monkeypatch):
    """✗修复前: 旧代码返回 SimulatedBroker (降级); 新契约必须抛异常."""
    monkeypatch.setenv("TRADING_ENV", "production")
    with pytest.raises(LiveBrokerUnavailableError):
        get_broker({"enabled": True, "dry_run": False})


def test_get_broker_live_intent_rpc_token_missing_raises(clean_env, monkeypatch):
    """✗修复前: 配了 RPC_URL 但缺 token → 旧代码降级模拟; 新契约 fail-closed."""
    monkeypatch.setenv("TRADING_ENV", "production")
    monkeypatch.setenv("QMT_RPC_URL", "http://127.0.0.1:18080")
    with pytest.raises(LiveBrokerUnavailableError):
        get_broker({"enabled": True, "dry_run": False})


def test_get_broker_non_live_intent_still_degrades(clean_env, monkeypatch):
    """无回归: TRADING_ENV≠production 时仍 fail-open 降级模拟 (防裸实盘)."""
    monkeypatch.setenv("TRADING_ENV", "shadow")
    broker = get_broker({"enabled": True, "dry_run": False})
    assert type(broker).__name__ == "SimulatedBroker"
    assert is_live_broker(broker) is False


def test_get_broker_dry_run_degrades(clean_env, monkeypatch):
    """无回归: dry_run=true → 影子模拟, 不因 production 而实盘."""
    monkeypatch.setenv("TRADING_ENV", "production")
    broker = get_broker({"enabled": True, "dry_run": True})
    assert type(broker).__name__ == "SimulatedBroker"


def test_get_broker_disabled_degrades(clean_env):
    """无回归: 未启用 → 模拟盘."""
    broker = get_broker({"enabled": False})
    assert type(broker).__name__ == "SimulatedBroker"


def test_build_simulated_rejects_live_flag(clean_env):
    """防御性: 即使被误传 live=True, 也拒绝构造模拟盘 (fail-closed 兜底)."""
    from utils.execution import broker_factory as bf

    with pytest.raises(LiveBrokerUnavailableError):
        bf._build_simulated({"enabled": True, "dry_run": False}, live=True)


# ============================================================
# 3. is_live_broker 类型判定
# ============================================================
def test_is_live_broker_classification():
    class SimulatedBroker:  # noqa: N801  # 与真实类名对齐的白名单判定
        pass

    class QmtBrokerAPI:  # noqa: N801
        pass

    assert is_live_broker(SimulatedBroker()) is False
    assert is_live_broker(QmtBrokerAPI()) is True
    assert is_live_broker(None) is False


# ============================================================
# 4. AutomatedExecutionSystem 主链路 fail-closed 接线
# ============================================================
def test_aes_init_live_broker_unavailable_fail_closed(clean_env, monkeypatch):
    """✗修复前: 旧代码捕获后继续初始化 (broker=None); 新契约必须拒绝启动."""
    monkeypatch.setattr(f"{_AES}._GET_BROKER_AVAILABLE", True)
    with patch(
        f"{_AES}.get_broker",
        side_effect=LiveBrokerUnavailableError("xtquant missing"),
    ):
        from utils.execution.automated_execution_system import AutomatedExecutionSystem

        with pytest.raises(LiveBrokerUnavailableError):
            AutomatedExecutionSystem()


def test_aes_init_non_live_failure_still_degrades(clean_env, monkeypatch):
    """无回归: 非实盘就绪时装配失败仍降级 (broker=None), 不阻断初始化."""
    monkeypatch.setattr(f"{_AES}._GET_BROKER_AVAILABLE", True)
    with patch(f"{_AES}.get_broker", side_effect=OSError("connect fail")):
        from utils.execution.automated_execution_system import AutomatedExecutionSystem

        sys_aes = AutomatedExecutionSystem()
    assert sys_aes.order_router.broker is None


def test_aes_init_fail_closed_when_factory_missing_and_live_intent(
    clean_env, monkeypatch
):
    """broker_factory 整体不可用 + 实盘就绪 → 拒绝启动 (不能在无校验下裸跑)."""
    monkeypatch.setattr(f"{_AES}._GET_BROKER_AVAILABLE", False)
    monkeypatch.setattr(f"{_AES}._live_intent_fallback", lambda: True)
    from utils.execution.automated_execution_system import AutomatedExecutionSystem

    with pytest.raises(RuntimeError):
        AutomatedExecutionSystem()


def test_aes_init_ok_when_factory_missing_and_not_live_intent(
    clean_env, monkeypatch
):
    """无回归: broker_factory 不可用但非实盘就绪 → 正常初始化 (模拟)."""
    monkeypatch.setattr(f"{_AES}._GET_BROKER_AVAILABLE", False)
    monkeypatch.setattr(f"{_AES}._live_intent_fallback", lambda: False)
    from utils.execution.automated_execution_system import AutomatedExecutionSystem

    sys_aes = AutomatedExecutionSystem()
    assert sys_aes.order_router.broker is None


# ============================================================
# 5. _live_intent_fallback 兜底判定 (broker_factory 不可用时)
# ============================================================
def test_live_intent_fallback_reads_system_config(clean_env, monkeypatch, tmp_path):
    import json

    from utils.execution import automated_execution_system as aes_mod

    (tmp_path / "system_config.json").write_text(
        json.dumps({"broker": {"enabled": True, "dry_run": False}}), encoding="utf-8"
    )
    monkeypatch.setattr(aes_mod, "_PROJECT_ROOT", str(tmp_path))

    monkeypatch.setenv("TRADING_ENV", "production")
    assert aes_mod._live_intent_fallback() is True

    monkeypatch.setenv("TRADING_ENV", "sim")
    assert aes_mod._live_intent_fallback() is False

    # 配置缺失 → 安全默认为未启用
    monkeypatch.setenv("TRADING_ENV", "production")
    monkeypatch.setattr(aes_mod, "_PROJECT_ROOT", str(tmp_path / "nope"))
    assert aes_mod._live_intent_fallback() is False
