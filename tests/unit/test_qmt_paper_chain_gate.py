"""G1 QMT paper 链路门控回归 (T001-T004, 2026-09-11).

覆盖 FR-001/002/003/006 与 AC-004/007/008:
    T001  终端不可达 → 抛 LiveBrokerUnavailableError, **不得**降级模拟继续
    T002  dry_run=true (TRADING_ENV=production) → 真实通道调用数 = 0, 影子留痕
    T003  enabled=false → 装配 SimulatedBroker (既有契约不回归)
    T004  验证入口在"前置未满足"时**明确失败 exit≠0**, 而非静默 pass

编写口径 (遵循项目铁律):
    - 门禁类变更须附"修复前会失败"证据: T004 在 `scripts/verify_qmt_paper_chain.py`
      交付前必红 (文件不存在 / 入口缺失), 交付后转绿.
    - "空集合 = 通过"是门禁假 PASS 头号来源: T004 专门断言入口**必须**给出非 0 退出码.
    - 放置于 tests/unit/ (禁 tests/e2e/, 祖先目录关键字会令用例天生被 skip).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest

from utils.execution.broker_factory import (
    LiveBrokerUnavailableError,
    get_broker,
    is_live_broker,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PAPER_SCRIPT = _PROJECT_ROOT / "scripts" / "verify_qmt_paper_chain.py"


@pytest.fixture
def clean_env(monkeypatch):
    """清空 broker 相关环境变量, 保证用例相互独立."""
    for key in (
        "TRADING_ENV",
        "QMT_RPC_URL",
        "QMT_RPC_TOKEN",
        "QMT_RPC_TIMEOUT",
        "QMT_ACCOUNT_ID",
        "QMT_PATH",
        "QMT_SESSION_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    yield


def _load_paper_module() -> ModuleType:
    """按文件路径加载验证入口 (不依赖包结构, 便于独立测试)."""
    if not _PAPER_SCRIPT.exists():
        pytest.fail(
            f"验证入口未交付: {_PAPER_SCRIPT} (T006/T007 的交付物)"
        )
    spec = importlib.util.spec_from_file_location(
        "verify_qmt_paper_chain", _PAPER_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ============================================================
# T001 / AC-008: 终端不可达 → fail-closed, 绝不降级模拟
# ============================================================
def test_terminal_unreachable_raises_and_never_degrades(clean_env, monkeypatch):
    """实盘就绪 + 终端不可达 → 抛 LiveBrokerUnavailableError, 不得返回模拟盘."""
    monkeypatch.setenv("TRADING_ENV", "production")
    monkeypatch.setenv("QMT_RPC_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("QMT_RPC_TOKEN", "any-token")

    with patch(
        "utils.execution.remote_qmt_broker.RemoteQmtBroker.connect",
        return_value=False,
    ):
        with pytest.raises(LiveBrokerUnavailableError) as excinfo:
            get_broker({"enabled": True, "dry_run": False})

    # 异常语义必须点明"拒绝降级", 便于运维定位
    assert "fail-closed" in str(excinfo.value)


def test_terminal_unreachable_local_direct_also_fails_closed(clean_env, monkeypatch):
    """未配 RPC_URL 走本地直连 (xtquant 缺失) → 同样 fail-closed."""
    monkeypatch.setenv("TRADING_ENV", "production")
    with pytest.raises(LiveBrokerUnavailableError):
        get_broker({"enabled": True, "dry_run": False})


# ============================================================
# T002 / AC-007: dry_run=true 时真实通道调用数必须为 0
# ============================================================
def test_dry_run_zero_real_channel_calls_with_shadow_trace(clean_env, monkeypatch):
    """dry_run=true + production → 走影子模拟, 真实通道一次都不许被触达."""
    monkeypatch.setenv("TRADING_ENV", "production")
    monkeypatch.setenv("QMT_RPC_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("QMT_RPC_TOKEN", "any-token")

    calls = {"remote_init": 0, "remote_connect": 0}
    alerts: list[tuple[str, str]] = []

    def _boom_remote_init(*args, **kwargs):
        calls["remote_init"] += 1
        raise AssertionError("dry_run=true 下不得构造真实通道 RemoteQmtBroker")

    def _boom_remote_connect(*args, **kwargs):
        calls["remote_connect"] += 1
        raise AssertionError("dry_run=true 下不得连接真实通道")

    def _spy_alert(message, level="WARNING"):
        alerts.append((level, message))

    with patch(
        "utils.execution.remote_qmt_broker.RemoteQmtBroker.__init__",
        _boom_remote_init,
    ), patch(
        "utils.execution.remote_qmt_broker.RemoteQmtBroker.connect",
        _boom_remote_connect,
    ), patch(
        "utils.execution.broker_factory._safe_send_alert", _spy_alert
    ):
        broker = get_broker({"enabled": True, "dry_run": True})

    assert calls == {"remote_init": 0, "remote_connect": 0}
    assert type(broker).__name__ == "SimulatedBroker"
    assert is_live_broker(broker) is False
    # 影子路径必须留痕, 不许静默
    assert any("dry_run" in msg for _lvl, msg in alerts), alerts


# ============================================================
# T003: enabled=false → 模拟盘 (既有契约不回归)
# ============================================================
def test_disabled_assembles_simulated(clean_env):
    """未启用 (默认) → SimulatedBroker, 且 production 也不改变结论."""
    broker = get_broker({"enabled": False})
    assert type(broker).__name__ == "SimulatedBroker"
    assert is_live_broker(broker) is False


def test_disabled_wins_over_production_env(clean_env, monkeypatch):
    """enabled=false 是最高优先级门控: 即使 TRADING_ENV=production 也不实盘."""
    monkeypatch.setenv("TRADING_ENV", "production")
    broker = get_broker({"enabled": False, "dry_run": False})
    assert type(broker).__name__ == "SimulatedBroker"


# ============================================================
# T004 / AC-004: 验证入口"前置未满足必须明确失败", 不许静默 pass
# ============================================================
def test_paper_chain_entry_delivered():
    """先红后绿的锚点: 入口文件必须存在 (T006/T007 交付物)."""
    assert _PAPER_SCRIPT.exists(), (
        "T006 未交付: scripts/verify_qmt_paper_chain.py 缺失 → "
        "paper 链验证入口不存在, T15 无法取证"
    )


def test_xtquant_check_rejects_namespace_only_install(monkeypatch, tmp_path):
    """AC-002 修正回归: "只有命名空间的 xtquant" 必须判为不可用.

    复现的正是 Py3.14 的真实形态 —— wheel 装上了、`import xtquant` 成功,
    但 `xtdata`/`xttrader` 子模块 ImportError。修复前 (find_spec 判据) 本用例**会失败**
    (假绿), 修复后 (能力级判据) 转绿。这是本项目"门禁假 PASS"防复发的核心断言。
    """
    fake_pkg = tmp_path / "xtquant"
    fake_pkg.mkdir()
    (fake_pkg / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    module = _load_paper_module()

    assert module._xtquant_available() is False, (
        "命名空间级 xtquant 被判为可用 ⇒ AC-002 会假 PASS "
        "(Py3.14 下 import xtquant 成功但 xtdata/xttrader 全废)"
    )


def test_preflight_reports_missing_prerequisites(monkeypatch):
    """前置缺失 → preflight 返回 (False, 原因列表), 原因必须可读."""
    module = _load_paper_module()
    monkeypatch.setattr(module, "_xtquant_available", lambda: False)
    monkeypatch.delenv("QMT_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("QMT_PATH", raising=False)

    ok, reasons = module.preflight()

    assert ok is False
    assert reasons, "前置未满足必须给出原因, 不许空集合静默通过"
    joined = " ".join(reasons)
    assert "xtquant" in joined


def test_preflight_only_exit_code_nonzero(clean_env, monkeypatch):
    """`--preflight-only` 前置未满足 → 退出码 ≠ 0 (是本次回归的核心判据)."""
    module = _load_paper_module()
    monkeypatch.setattr(module, "_xtquant_available", lambda: False)

    rc = module.main(["--preflight-only"])

    assert rc != 0, "前置未满足却返回 0 == 静默通过 (AC-004 禁止)"


def test_entry_subprocess_exit_code_nonzero_when_prereq_missing():
    """端到端口径: 真实子进程调用的退出码也必须 ≠ 0 (防"函数绿、CLI 哑")."""
    if not _PAPER_SCRIPT.exists():
        pytest.fail(f"验证入口未交付: {_PAPER_SCRIPT}")

    proc = subprocess.run(
        [sys.executable, str(_PAPER_SCRIPT), "--preflight-only"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(_PROJECT_ROOT),
        timeout=180,
        check=False,
    )

    assert proc.returncode != 0, (
        "验证入口前置未满足却以 0 退出 → 会被自动化门禁误判为通过\n"
        f"stdout={proc.stdout[-800:]}\nstderr={proc.stderr[-800:]}"
    )
    assert "前置" in (proc.stdout + proc.stderr), (
        "未给出明确的'前置未满足'提示, 运维无法定位\n"
        f"stdout={proc.stdout[-800:]}"
    )


# ============================================================
# T009 / AC-010: 文档漂移防复发
# `config/system_config.json` 已于 2026-09-07 合并至仓库根并删除
# ============================================================
_STALE_CONFIG_PATH = "config/system_config.json"


def test_sim_chain_script_has_no_stale_config_path():
    """仿真链指引不得再指向已删除的 config/system_config.json."""
    script = _PROJECT_ROOT / "scripts" / "verify_qmt_sim_chain.py"
    text = script.read_text(encoding="utf-8")
    assert _STALE_CONFIG_PATH not in text, (
        "verify_qmt_sim_chain.py 仍指向已删除的 config/system_config.json "
        "(唯一事实源 = 仓库根 system_config.json)"
    )


def test_quickstart_documents_root_config_as_authority():
    """quickstart.md 必须指明根 system_config.json 为唯一事实源 (AC-010 收口)."""
    quickstart = (
        _PROJECT_ROOT / "specs" / "G1-qmt-live-order-wiring" / "quickstart.md"
    )
    assert quickstart.exists(), "quickstart.md 是 AC-010 的收口载体"
    text = quickstart.read_text(encoding="utf-8")
    assert "唯一事实源" in text, "未声明唯一事实源"
    flat = text.replace("`", "").replace(" ", "")
    assert "根system_config.json" in flat, "未指向根 system_config.json"
